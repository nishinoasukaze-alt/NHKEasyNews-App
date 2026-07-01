"""验证：捕获带 token 的 HLS 子流，下载分片+key，AES-128 解密合并为音频文件。

链路全部在浏览器上下文内进行（自动带 hdntl token，避免 403）。
产出 data/probe_audio/out.aac，验证可播放后再固化进 fetcher。
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urljoin

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Crypto.Cipher import AES  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HOME = "https://news.web.nhk/news/easy/"
NID = "ne2026062513003"
DETAIL = f"{HOME}{NID}/{NID}.html"
OUT = Path("data/probe_audio")
OUT.mkdir(parents=True, exist_ok=True)


def fetch_text(page, url: str) -> str:
    return page.evaluate(
        """async (u) => {
            const r = await fetch(u, {credentials: 'include'});
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return await r.text();
        }""",
        url,
    )


def fetch_b64(page, url: str) -> bytes:
    import base64
    b64 = page.evaluate(
        """async (u) => {
            const r = await fetch(u, {credentials: 'include'});
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const buf = await r.arrayBuffer();
            let s = ''; const bytes = new Uint8Array(buf);
            for (let i = 0; i < bytes.length; i += 0x8000)
                s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            return btoa(s);
        }""",
        url,
    )
    return base64.b64decode(b64)


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo")
        page = ctx.new_page()

        sub_urls: list[str] = []
        page.on(
            "response",
            lambda r: sub_urls.append(r.url)
            if ("index_64k.m3u8" in r.url and r.status == 200) else None,
        )

        page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_selector("button:has-text('確認しました')", timeout=15000)
            page.locator("button:has-text('確認しました')").first.click()
        except Exception as exc:  # noqa: BLE001
            print("consent:", exc)
        page.wait_for_timeout(5000)

        page.goto(DETAIL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        try:
            page.locator(".js-open-audio, .article-buttons__audio").first.click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(2000)
        for fr in page.frames:
            try:
                if fr.locator("button").count() > 0:
                    fr.locator("button").first.click(timeout=2000)
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(7000)

        if not sub_urls:
            print("未捕获到音频子流 m3u8，失败")
            b.close()
            return
        sub = sub_urls[0]
        print("子流 m3u8:", sub[:120])

        m3u8 = fetch_text(page, sub)
        # 解析 key URL 与分片
        key_url = None
        seg_lines: list[str] = []
        for line in m3u8.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-KEY"):
                # URI="..."
                import re
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    key_url = m.group(1)
            elif line and not line.startswith("#"):
                seg_lines.append(line)
        print(f"分片数: {len(seg_lines)}, 有密钥: {key_url is not None}")

        key = fetch_b64(page, key_url) if key_url else None
        if key:
            print("AES key 长度:", len(key))

        data = bytearray()
        for i, seg in enumerate(seg_lines, start=1):
            seg_url = urljoin(sub, seg)
            enc = fetch_b64(page, seg_url)
            if key:
                # HLS AES-128-CBC，IV 未显式给出 → 用 media sequence number（从 1 起）
                iv = i.to_bytes(16, "big")
                dec = AES.new(key, AES.MODE_CBC, iv).decrypt(enc)
                # 去 PKCS7 padding
                pad = dec[-1]
                if 1 <= pad <= 16:
                    dec = dec[:-pad]
                data += dec
            else:
                data += enc
            print(f"  分片 {i}/{len(seg_lines)} ok ({len(enc)} bytes)")

        out = OUT / "out.aac"
        out.write_bytes(bytes(data))
        print(f"\n合并完成: {out} ({len(data)} bytes)")
        # 校验 ADTS 头（0xFFF sync）
        sig = data[:2]
        print("前两字节:", sig.hex(), "（ADTS 应为 fff*）")
        b.close()


if __name__ == "__main__":
    main()
