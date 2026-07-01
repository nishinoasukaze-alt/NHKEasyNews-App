"""网络抓取层：Playwright 浏览器驱动 + 礼貌限速。

现行 NHK Easy News 是 Next.js SPA，且首访有「海外アクセス確認」门禁，
必须用真实（headful）浏览器点击「確認しました」写入同意 cookie 后，
列表接口（top-list.json，否则 401）与详情页（SSR）才放行。

因此本模块用一个浏览器会话统一：
  1. 打开主页 → 关闭同意门禁（写入 cookie）；
  2. 在该已授权上下文内获取 top-list.json 与详情页 HTML（自动带 cookie）；
  3. 资源（图片）下载同样在浏览器上下文内 fetch，避免 401；
  4. 音频为 AES-128 加密 HLS 流（media.vd.st.nhk，URL 带 Akamai hdntl token）：
     打开详情页点击音频按钮触发播放器，捕获带 token 的子流 m3u8，
     下载分片 + serve.key，AES-128-CBC 解密后拼接为本地音频文件。
     注：用新版 headless（--headless=new）启动 Chromium，播放器 iframe 才会正常发流
     （旧版 headless 下播放器不起，抓不到音频）。

对外保留 fetch_news_list / fetch_detail / download_file 三个函数，
并新增 download_audio（HLS 音频），均依赖一个已开启的会话
（用 open_session() 上下文管理器包裹整个流程）。
"""
from __future__ import annotations

import base64
import json
import re
import time
from contextlib import contextmanager
from urllib.parse import urljoin
from pathlib import Path
from typing import Any, Iterator

from . import config
from .logger import NetworkError, DownloadError, get_logger

logger = get_logger()

# 当前活动会话（由 open_session 设置）。模块级，便于兼容原有函数式调用。
_session: "BrowserSession | None" = None


class BrowserSession:
    """封装一个 Playwright 浏览器上下文。

    默认 headless：加载已保存的同意 cookie（storage_state）即可访问，无需点击。
    headless 参数可显式覆盖（setup_consent 用 headful 过同意并保存 cookie）。
    """

    def __init__(self, headless: bool | None = None) -> None:
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._consented = False
        self._headless = config.BROWSER_HEADLESS if headless is None else headless

    # -- 生命周期 ----------------------------------------------------------
    def start(self) -> None:
        # 延迟导入，未安装 Playwright 时给出清晰提示
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise NetworkError(
                "未安装 playwright，请先 pip install playwright 并 "
                "python -m playwright install chromium"
            ) from exc

        self._pw = sync_playwright().start()
        try:
            # 新版 headless（--headless=new）行为接近真实浏览器：音频播放器 iframe
            # 会正常加载并发流（旧版 headless 下播放器不起，抓不到音频）。
            # 故 headless 时改用 launch(headless=False, args=["--headless=new", ...])。
            launch_args = list(config.BROWSER_LAUNCH_ARGS)
            if self._headless:
                launch_args.insert(0, "--headless=new")
                self._browser = self._pw.chromium.launch(
                    headless=False, args=launch_args
                )
            else:
                self._browser = self._pw.chromium.launch(
                    headless=False, args=launch_args
                )
        except Exception as exc:  # pragma: no cover
            raise NetworkError(
                f"启动 Chromium 失败：{exc}；"
                "请确认已执行 python -m playwright install chromium"
            ) from exc

        # 若已有保存的同意 cookie，则加载复用（headless 关键路径）
        ctx_kwargs = dict(
            user_agent=config.USER_AGENT,
            locale=config.BROWSER_LOCALE,
            timezone_id=config.BROWSER_TIMEZONE,
        )
        if config.AUTH_STATE_PATH.exists():
            ctx_kwargs["storage_state"] = str(config.AUTH_STATE_PATH)
            logger.info("加载已保存的同意 cookie：%s", config.AUTH_STATE_PATH)
        self._ctx = self._browser.new_context(**ctx_kwargs)
        self._ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        self._page = self._ctx.new_page()

    def save_auth_state(self) -> None:
        """保存当前上下文 cookie（storage_state）到 AUTH_STATE_PATH。"""
        config.AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._ctx.storage_state(path=str(config.AUTH_STATE_PATH))
        logger.info("已保存同意 cookie：%s", config.AUTH_STATE_PATH)

    def close(self) -> None:
        for closer in (
            lambda: self._ctx and self._ctx.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001  # 关闭阶段错误不致命
                pass

    # -- 门禁 --------------------------------------------------------------
    def _check_authorized(self) -> bool:
        """探测 top-list.json 是否放行（200 即已授权）。

        只需让页面进入 news.web.nhk 同源上下文即可 fetch（带 cookie）。
        该 SPA 站点 goto 会等大量持续加载，故用短超时 + commit，超时也无妨：
        只要导航已 commit 到目标源，后续同源 fetch 即可工作。
        """
        try:
            self._page.goto(
                config.BASE_URL, wait_until="commit", timeout=config.NAV_COMMIT_TIMEOUT_MS
            )
        except Exception as exc:  # noqa: BLE001  # 超时但可能已 commit 到同源
            logger.info("主页导航未在限时内完成（继续探测）：%s", str(exc)[:60])
        try:
            status, _ = self._fetch_text_in_page(config.NEWS_LIST_URL)
            return status == 200
        except Exception:  # noqa: BLE001
            return False

    def ensure_consent(self) -> None:
        """确保已授权访问，并在 cookie 失效时自动续期。

        两段式：先复用已保存的 cookie 直接验证 top-list.json；200 即跳过点击
        （headless 关键路径）。未授权时点击「海外アクセス確認」同意弹窗——新版
        headless（--headless=new）可自动点过门禁，无需 headful、无需手动
        setup_consent。点击后重新验证一次并保存新 cookie 供后续复用。
        """
        if self._consented:
            return

        # 1) 复用 cookie 直接验证
        if self._check_authorized():
            logger.info("已授权（复用同意 cookie），跳过门禁点击")
            self._consented = True
            return

        # 2) 未授权：点击同意弹窗续期（headless 下亦可自动点过）
        logger.info("未授权，尝试处理同意门禁：%s", config.BASE_URL)
        for sel in config.CONSENT_BUTTON_SELECTORS:
            try:
                self._page.wait_for_selector(
                    sel, timeout=config.CONSENT_WAIT_TIMEOUT_MS, state="visible"
                )
            except Exception:  # noqa: BLE001
                continue
            try:
                self._page.locator(sel).first.click(timeout=5000)
                logger.info("已点击同意按钮：%s", sel)
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("点击同意按钮失败 %s：%s", sel, exc)

        self._page.wait_for_timeout(config.PAGE_WAIT_AFTER_CONSENT_MS)

        # 3) 重新验证 cookie 是否真的拿到（不因点过按钮就假定成功）
        if self._check_authorized():
            logger.info("同意门禁通过，cookie 已续期")
            try:
                self.save_auth_state()  # 新 cookie 供后续 headless / 任务计划复用
            except Exception as exc:  # noqa: BLE001
                logger.warning("保存同意 cookie 失败：%s", exc)
            self._consented = True
            return

        # 续期后仍未授权：多为 geo 限制或网络问题（非 headless 局限）。
        raise NetworkError(
            "同意门禁处理后仍未获授权（top-list.json 非 200）。"
            "可能为海外 geo 限制或网络异常；请确认网络可访问 NHK，必要时"
            "运行 setup_consent（headful）手动确认：python -m nhk_tool.setup_consent"
        )

    # -- 取数 --------------------------------------------------------------
    def fetch_json(self, url: str) -> Any:
        """在已授权上下文内 fetch JSON（带 cookie）。"""
        self.ensure_consent()
        logger.info("获取 JSON：%s", url)
        result = self._page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                return {status: r.status, text: await r.text()};
            }""",
            url,
        )
        time.sleep(config.REQUEST_INTERVAL)
        if result["status"] != 200:
            raise NetworkError(f"获取 JSON 失败 HTTP {result['status']}：{url}")
        try:
            return json.loads(result["text"])
        except ValueError as exc:
            raise NetworkError(f"响应非合法 JSON：{url}：{exc}") from exc

    def fetch_html(self, url: str) -> str:
        """获取详情页 HTML。

        详情页为 SSR：正文在初始响应即完整存在，故用页面内 fetch（带 cookie）
        直接取 HTML 源码，跳过浏览器 goto 的渲染/资源等待——在该 SPA 站点上
        goto 会等大量持续加载，单页常达 20s+，fetch 仅需 1~2s。
        """
        self.ensure_consent()
        logger.info("获取详情页：%s", url)
        status, html = self._fetch_text_in_page(url)
        time.sleep(config.REQUEST_INTERVAL)
        if status >= 400:
            raise NetworkError(f"详情页 HTTP {status}：{url}")
        return html

    def download(self, url: str, dest: Path) -> Path:
        """在浏览器上下文内下载二进制资源（带 cookie），写入 dest。"""
        self.ensure_consent()
        logger.info("下载资源：%s -> %s", url, dest)
        status, data = self._fetch_bytes(url)
        time.sleep(config.REQUEST_INTERVAL)
        if status != 200 or data is None:
            raise DownloadError(f"下载失败 HTTP {status}：{url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def _fetch_bytes(self, url: str) -> tuple[int, bytes | None]:
        """在浏览器上下文内 fetch 二进制，返回 (status, bytes|None)。

        网络层瞬时异常（如 TypeError: Failed to fetch）会重试 config.RETRY 次并
        指数退避；重试耗尽仍失败才转为 DownloadError，交由调用方按"单条非致命"
        处理。这样最后一条新闻赶上网络抖动时不会直接丢图丢音频。
        """
        last_exc: Exception | None = None
        for attempt in range(1, config.RETRY + 1):
            try:
                result = self._page.evaluate(
                    """async (url) => {
                        const r = await fetch(url, {credentials: 'include'});
                        if (!r.ok) return {status: r.status, b64: null};
                        const buf = await r.arrayBuffer();
                        let binary = '';
                        const bytes = new Uint8Array(buf);
                        const chunk = 0x8000;
                        for (let i = 0; i < bytes.length; i += chunk) {
                            binary += String.fromCharCode.apply(
                                null, bytes.subarray(i, i + chunk));
                        }
                        return {status: r.status, b64: btoa(binary)};
                    }""",
                    url,
                )
                data = base64.b64decode(result["b64"]) if result["b64"] else None
                return result["status"], data
            except Exception as exc:  # noqa: BLE001  # Playwright Error / fetch 失败
                last_exc = exc
                if attempt < config.RETRY:
                    backoff = config.RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "浏览器内 fetch 失败（第 %d/%d 次，%.1fs 后重试）：%s（%s）",
                        attempt, config.RETRY, backoff, url, str(exc)[:80],
                    )
                    time.sleep(backoff)
        raise DownloadError(
            f"浏览器内 fetch 失败（已重试 {config.RETRY} 次）：{url}"
            f"（{str(last_exc)[:80]}）"
        )

    def _fetch_text_in_page(self, url: str) -> tuple[int, str]:
        """在浏览器上下文内 fetch 文本，返回 (status, text)。"""
        result = self._page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                return {status: r.status, text: await r.text()};
            }""",
            url,
        )
        return result["status"], result["text"]

    # -- 音频（加密 HLS） --------------------------------------------------
    def _capture_audio_substreams(self, detail_url: str) -> list[str]:
        """进详情页、触发播放器，捕获带 token 的音频子流 m3u8 URL 列表。

        单次尝试，失败（捕获不到）返回空列表，由调用方决定是否重试。
        """
        sub_urls: list[str] = []

        def on_resp(r):
            if config.AUDIO_HLS_MEDIA_PLAYLIST in r.url and r.status == 200:
                sub_urls.append(r.url)

        self._page.on("response", on_resp)
        try:
            self._page.goto(detail_url, wait_until="domcontentloaded")
            self._page.wait_for_timeout(config.PAGE_WAIT_AFTER_LOAD_MS)
            # 打开音频面板
            try:
                self._page.locator(
                    ".js-open-audio, .article-buttons__audio"
                ).first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            self._page.wait_for_timeout(1500)
            # 在各 frame 内点击播放按钮触发取流
            for fr in self._page.frames:
                try:
                    if fr.locator("button").count() > 0:
                        fr.locator("button").first.click(timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
            # 等待子流出现
            for _ in range(int(config.AUDIO_HLS_WAIT_MS / 500)):
                if sub_urls:
                    break
                self._page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001  # 页面导航/交互抖动，交由上层重试
            logger.warning("音频子流捕获过程异常：%s（%s）", detail_url, str(exc)[:80])
        finally:
            self._page.remove_listener("response", on_resp)
        return sub_urls

    def download_hls_audio(self, detail_url: str, dest: Path) -> Path:
        """下载某条新闻的音频（AES-128 加密 HLS），解密合并写入 dest。

        流程：进详情页 → 点音频按钮触发播放器 → 捕获带 token 的子流 m3u8
        → 下载分片与 serve.key → AES-128-CBC 解密 → 拼接为本地音频文件。
        任一步失败抛 DownloadError（音频在 run_crawl 中为非致命项）。
        """
        self.ensure_consent()
        logger.info("抓取音频(HLS)：%s -> %s", detail_url, dest)

        # 捕获子流可能因页面/播放器加载抖动而失败，重试 config.RETRY 次。
        sub_urls: list[str] = []
        for attempt in range(1, config.RETRY + 1):
            sub_urls = self._capture_audio_substreams(detail_url)
            if sub_urls:
                break
            if attempt < config.RETRY:
                backoff = config.RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "未捕获到音频流（第 %d/%d 次，%.1fs 后重试）：%s",
                    attempt, config.RETRY, backoff, detail_url,
                )
                time.sleep(backoff)

        if not sub_urls:
            raise DownloadError(
                f"未捕获到音频流（已重试 {config.RETRY} 次），"
                f"可能无音频或站点变化：{detail_url}"
            )

        sub = sub_urls[0]
        status, m3u8 = self._fetch_text_in_page(sub)
        if status != 200:
            raise DownloadError(f"音频子流 m3u8 HTTP {status}：{sub}")

        key_url, segments = _parse_media_playlist(m3u8)
        if not segments:
            raise DownloadError(f"音频 m3u8 无分片：{sub}")

        key = None
        if key_url:
            ks, kb = self._fetch_bytes(urljoin(sub, key_url))
            if ks != 200 or not kb:
                raise DownloadError(f"获取 AES 密钥失败 HTTP {ks}")
            key = kb

        data = _download_and_decrypt(self, sub, segments, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        time.sleep(config.REQUEST_INTERVAL)
        logger.info("音频完成：%s（%d 分片，%d 字节）", dest.name, len(segments), len(data))
        return dest


def _parse_media_playlist(m3u8: str) -> tuple[str | None, list[str]]:
    """解析媒体级 m3u8，返回 (AES 密钥 URI 或 None, 分片相对/绝对 URL 列表)。"""
    key_url: str | None = None
    segments: list[str] = []
    for line in m3u8.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                key_url = m.group(1)
        elif line and not line.startswith("#"):
            segments.append(line)
    return key_url, segments


def _download_and_decrypt(
    sess: "BrowserSession",
    base_url: str,
    segments: list[str],
    key: bytes | None,
) -> bytes:
    """下载所有分片，按需 AES-128-CBC 解密（IV=媒体序号），拼接为字节流。

    HLS 规范：未显式声明 IV 时，用分片的 media sequence number 作为 IV
    （本站分片从序号 1 起）。解密后去除 PKCS7 填充。
    """
    if key is not None:
        from Crypto.Cipher import AES  # 延迟导入，未装时仅影响音频

    out = bytearray()
    total = len(segments)
    for i, seg in enumerate(segments, start=1):
        seg_url = urljoin(base_url, seg)
        status, enc = sess._fetch_bytes(seg_url)
        if status != 200 or enc is None:
            raise DownloadError(f"音频分片下载失败 HTTP {status}：分片 {i}/{total}")
        if key is not None:
            iv = i.to_bytes(16, "big")
            dec = AES.new(key, AES.MODE_CBC, iv).decrypt(enc)
            pad = dec[-1] if dec else 0
            if 1 <= pad <= 16:
                dec = dec[:-pad]
            out += dec
        else:
            out += enc
        time.sleep(config.AUDIO_SEGMENT_INTERVAL)
    return bytes(out)


@contextmanager
def open_session(headless: bool | None = None) -> Iterator[BrowserSession]:
    """开启浏览器会话并设为模块级活动会话，供下列函数式接口使用。

    headless=None 用 config 默认（无头）；setup_consent 传 False 强制 headful。
    """
    global _session
    sess = BrowserSession(headless=headless)
    sess.start()
    _session = sess
    try:
        yield sess
    finally:
        _session = None
        sess.close()


def _require_session() -> BrowserSession:
    if _session is None:
        raise NetworkError(
            "无活动浏览器会话，请用 with fetcher.open_session(): 包裹抓取流程"
        )
    return _session


def session_is_headless() -> bool:
    """当前活动会话是否为 headless（无活动会话时按 config 默认）。"""
    if _session is None:
        return config.BROWSER_HEADLESS
    return _session._headless


# ---------------------------------------------------------------------------
# 兼容原有调用方的函数式接口（依赖活动会话）
# ---------------------------------------------------------------------------
def fetch_news_list() -> Any:
    """获取 top-list.json，返回已解析的 JSON 对象。"""
    return _require_session().fetch_json(config.NEWS_LIST_URL)


def fetch_detail(detail_url: str) -> str:
    """获取详情页 HTML 文本。"""
    return _require_session().fetch_html(detail_url)


def download_file(url: str, dest: Path) -> Path:
    """下载资源到 dest，失败抛 DownloadError。"""
    return _require_session().download(url, dest)


def download_audio(detail_url: str, dest: Path) -> Path:
    """下载某条新闻音频（加密 HLS），解密合并到 dest，失败抛 DownloadError。"""
    return _require_session().download_hls_audio(detail_url, dest)
