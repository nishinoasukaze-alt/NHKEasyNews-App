"""探针 v3：查清“海外アクセス確認”弹窗的存储机制，正确放行后导出真实新闻结构。

策略：
1. 打开页面，dump 初始 localStorage / cookies。
2. 用多种方式点击「確認しました」按钮（含 force / 父级 button）。
3. dump 点击后 localStorage 变化，找出“已同意”写入的键。
4. reload，dump 主区域 DOM 结构、所有 a[href] 含 /news/easy/、所有 api.web.nhk 调用。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

HOME = "https://news.web.nhk/news/easy/"
OUT = Path(__file__).resolve().parents[1] / "data" / "probe3"
OUT.mkdir(parents=True, exist_ok=True)

calls: list[dict] = []


def dump_storage(page):
    return page.evaluate(
        "() => ({local: {...localStorage}, session: {...sessionStorage}})"
    )


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = ctx.new_page()

        def on_response(resp):
            u = resp.url
            if "api.web.nhk" in u or "news/easy" in u:
                rec = {"url": u, "status": resp.status,
                       "ct": resp.headers.get("content-type", "")}
                if "json" in rec["ct"]:
                    try:
                        rec["body_preview"] = resp.text()[:2500]
                    except Exception:  # noqa: BLE001
                        pass
                calls.append(rec)

        page.on("response", on_response)

        page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        before = dump_storage(page)

        # 点击确认按钮：先试可见文本，再试 force
        clicked = False
        for sel in ["button:has-text('確認しました')",
                    "button:has-text('I understand')",
                    "text=確認しました"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=4000, force=True)
                    clicked = True
                    print(f"点击成功：{sel}")
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"点击失败 {sel}: {str(exc)[:80]}")
        page.wait_for_timeout(3000)
        after = dump_storage(page)

        # 找出点击后新增/变化的 storage 键
        diff = {k: after["local"][k] for k in after["local"]
                if before["local"].get(k) != after["local"][k]}
        print(f"点击：{clicked}；localStorage 变化键：{list(diff.keys())}")

        # reload，让 SPA 依据已同意状态加载内容
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass

        page.screenshot(path=str(OUT / "final.png"), full_page=True)
        (OUT / "final.html").write_text(page.content(), encoding="utf-8")
        (OUT / "storage.json").write_text(
            json.dumps({"before": before, "after": after, "diff": diff},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)")
        easy_links = sorted({l for l in links if "/news/easy/" in l and l.rstrip("/") != HOME.rstrip("/")})
        (OUT / "easy_links.json").write_text(
            json.dumps(easy_links, ensure_ascii=False, indent=2), encoding="utf-8")

        # main 区域文本预览
        main_text = page.evaluate(
            "() => { const m=document.querySelector('main'); return m? m.innerText.slice(0,600):'NO MAIN'; }")
        print("\n=== main innerText 预览 ===")
        print(main_text)

        browser.close()

    (OUT / "calls.json").write_text(
        json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\napi/easy 调用 {len(calls)} 条；easy 链接 {len(easy_links)} 条")
    for c in calls:
        print(f"  [{c['status']}] {c['url']}")
    print(f"产物目录：{OUT}")


if __name__ == "__main__":
    main()
