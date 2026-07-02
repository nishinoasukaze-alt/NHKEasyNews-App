"""集中配置：站点地址、保存目录、抓取参数、展示选项。

站点改版或个性化定制时，优先只改本文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 站点地址
# ---------------------------------------------------------------------------
# NHK Easy News 现行站点（旧 www3.nhk.or.jp 已 301 跳转至此）。
# 注意：站点已改版为 Next.js SPA，且首访有「海外アクセス確認」门禁弹窗，
# 必须经浏览器点击「確認しました」写入同意 cookie 后，接口/详情页才放行
# （否则 top-list.json 返回 401）。详见 fetcher.py。
BASE_URL = "https://news.web.nhk/news/easy/"

# 新闻列表 JSON（现行接口）。需携带同意后的 cookie 才能访问。
# 结构：JSON 数组，每项含 news_id/title/title_with_ruby/outline_with_ruby/
#       news_prearranged_time/top_priority_number/top_display_flag/
#       news_web_image_uri/news_easy_image_uri/news_easy_voice_uri/has_* 等。
NEWS_LIST_URL = "https://news.web.nhk/news/easy/top-list.json"

# 详情页 HTML 模板，{news_id} 为占位符（SSR 页面，需同意 cookie）
DETAIL_URL_TEMPLATE = "https://news.web.nhk/news/easy/{news_id}/{news_id}.html"

# 资源（图片/音频）所在目录模板
ASSET_BASE_TEMPLATE = "https://news.web.nhk/news/easy/{news_id}/"

# 音频播放器页模板（带 voiceId）。注意：现行站点音频经此播放器内专有 JS
# 动态加载，真实音频文件路径未公开暴露；且海外网络受 geo 限制无法播放/下载。
# 本模板仅用于在 meta 中记录音频入口，供将来在日本网络环境扩展真实下载。
AUDIO_PLAYER_URL_TEMPLATE = (
    "https://news.web.nhk/news/easy/player/audio-v6.html?voiceId={voice_uri}"
)

# ---------------------------------------------------------------------------
# 抓取参数
# ---------------------------------------------------------------------------
NEWS_COUNT = 4          # 主页展示并抓取的新闻条数
TIMEOUT = 15            # 单次请求超时（秒）
RETRY = 3               # 最大重试次数
RETRY_BACKOFF = 2.0     # 重试退避基数（秒）：sleep = backoff * 2**(attempt-1)
REQUEST_INTERVAL = 1.5  # 相邻请求间隔（秒），礼貌抓取
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 nhk-web-easy-tool/0.1"
)

# ---------------------------------------------------------------------------
# Playwright（浏览器驱动）
# ---------------------------------------------------------------------------
# 无头化：列表/正文/图片在 headless 下复用已保存的同意 cookie 即可（实测可行），
# 无需弹窗、无需点击，因而可在锁屏/未登录下由任务计划稳定运行。
# 首次使用及 cookie 过期时，用 setup_consent（headful）过一次同意并保存 cookie。
BROWSER_HEADLESS = True
# cookie 失效（401）时，是否允许自动起一次 headful 刷新（无交互桌面时会失败）。
BROWSER_HEADFUL_FALLBACK = True
# Chromium 启动参数。autoplay 放开让音频播放器自动起流（headless 下抓音频关键）。
BROWSER_LAUNCH_ARGS = (
    "--autoplay-policy=no-user-gesture-required",
    "--use-fake-device-for-media-stream",
    "--mute-audio",
)
# 浏览器上下文区域设置（贴近日本用户环境）
BROWSER_LOCALE = "ja-JP"
BROWSER_TIMEZONE = "Asia/Tokyo"
# 「海外からのアクセス確認」弹窗的同意按钮（Playwright 选择器，按序尝试）
CONSENT_BUTTON_SELECTORS = (
    "button:has-text('確認しました')",
    "button:has-text('I understand')",
)
# 页面加载/水合等待（毫秒）
PAGE_WAIT_AFTER_LOAD_MS = 2500
PAGE_WAIT_AFTER_CONSENT_MS = 6000
NAV_TIMEOUT_MS = 60000
# 仅需导航 commit 到同源即可 fetch 时用的短超时（该 SPA 完整加载很慢）。
NAV_COMMIT_TIMEOUT_MS = 15000
# 等待同意弹窗按钮出现的超时（毫秒）。弹窗为前端水合后渲染，需显式等待。
CONSENT_WAIT_TIMEOUT_MS = 15000

# 列表中“当日展示”的判定与排序字段
TOP_DISPLAY_FLAG_FIELD = "top_display_flag"
TOP_PRIORITY_FIELD = "top_priority_number"

# ---------------------------------------------------------------------------
# 音频（加密 HLS）
# ---------------------------------------------------------------------------
# 音频经 media.vd.st.nhk 的 AES-128 加密 HLS 流分发，URL 带 Akamai hdntl token。
# 抓取方式：详情页点音频按钮触发播放器，捕获带 token 的“音频子流”m3u8。
# 该子流 URL 含此标记（区别于带视频的 k10... 流）：
AUDIO_HLS_SUB_MARKER = "easy_audio/"
# 子流文件名特征（媒体级播放列表，含分片与 AES key）
AUDIO_HLS_MEDIA_PLAYLIST = "index_64k.m3u8"
# 等待播放器发起取流的最长时间（毫秒）
AUDIO_HLS_WAIT_MS = 12000
# 相邻分片下载间隔（秒），礼貌抓取
AUDIO_SEGMENT_INTERVAL = 0.3
# 是否抓取音频（默认开启；若环境无法访问流媒体 CDN 可关闭以加速）
FETCH_AUDIO = True

# ---------------------------------------------------------------------------
# 本地保存
# ---------------------------------------------------------------------------
# 项目根目录：src/nhk_tool/config.py -> 上溯三级
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 数据目录：
#   - 环境变量 NHK_DATA_ROOT 显式指定时优先用它（Tauri 壳启动 sidecar 时传 app\data，
#     使壳与爬虫共用同一 data 根，且 data 落在软件根目录更直观）；
#   - 否则打包态（PyInstaller，sys.frozen=True）：exe 在 engine\ 子目录时用上级
#     app\data（与壳/定时任务共用同一根），否则用 exe 旁 data\；
#   - 源码运行用项目内 data/（不影响开发与测试）。
def _resolve_data_root(env_root: str, frozen: bool, exe_path: Path,
                       project_root: Path) -> Path:
    """纯函数：按运行形态解析数据根目录（便于离线单测，无副作用）。

    - env_root 非空 → 直接采用（壳启动 sidecar 时传 app\\data）。
    - frozen 且 exe 位于名为 engine 的目录 → 上级目录的 data（打包布局
      app\\engine\\nhk-crawler.exe，数据落 app\\data）。
    - frozen 其他情况 → exe 旁 data。
    - 非 frozen（源码）→ 项目内 data。
    """
    if env_root:
        return Path(env_root)
    if frozen:
        exe_dir = exe_path.resolve().parent
        if exe_dir.name.lower() == "engine":
            return exe_dir.parent / "data"
        return exe_dir / "data"
    return project_root / "data"


DATA_ROOT = _resolve_data_root(
    os.environ.get("NHK_DATA_ROOT", "").strip(),
    bool(getattr(sys, "frozen", False)),
    Path(sys.executable),
    PROJECT_ROOT,
)

SAVE_ROOT = DATA_ROOT / "news"       # 每日新闻：news/YYYY-MM-DD/
LOG_ROOT = DATA_ROOT / "logs"        # 日志：logs/crawl-YYYY-MM-DD.log
# 已保存的同意 cookie（Playwright storage_state）。存在则 headless 复用。
AUTH_STATE_PATH = DATA_ROOT / "auth_state.json"

# 单条新闻目录内的固定文件名
IMAGE_FILENAME = "image.jpg"
AUDIO_FILENAME = "audio.aac"   # 现行站点语音为 AES-128 HLS，解密合并为 AAC
BODY_FILENAME = "body.txt"
META_FILENAME = "meta.json"

# 每日目录下的索引文件，作为挂件唯一数据契约
MANIFEST_FILENAME = "manifest.json"

# 抓取状态文件（记录上次成功抓取所见 news_id 集合，用于“当日新增”判定）。
# 与日期目录平级存于 SAVE_ROOT 下，独立于“天”。
STATE_FILENAME = "state.json"

# 旧新闻保留天数：超过此天数的日期目录会在每次成功爬取后自动清理。
RETENTION_DAYS = 30

# 默认爬取时间段（HH:MM，本地时间）。用户可在设置界面自定义，存入 widget_prefs.json。
DEFAULT_CRAWL_TIMES = ("09:00", "21:00")

# 单次爬取任务的最长时限（秒）。超时视为卡死，记日志并放弃本次，避免占锁导致
# 后续定时被跳过。默认 600 秒（4 条新闻含音频正常约 1~2 分钟，留足余量）。
CRAWL_MAX_SECONDS = 600

# ---------------------------------------------------------------------------
# 挂件展示（可定制）
# ---------------------------------------------------------------------------
WIDGET_TITLE = "NHK Easy News"
WIDGET_WIDTH = 380               # 初始宽度
WIDGET_BODY_PREVIEW_CHARS = 120  # 正文预览截断字数

# 桌面小挂件：自由缩放与托盘
WIDGET_MIN_WIDTH = 300           # 最小宽度
WIDGET_MIN_HEIGHT = 240          # 最小高度
WIDGET_INIT_HEIGHT = 600         # 初始高度
RESIZE_MARGIN = 6                # 边缘缩放命中阈值（像素）
WIDGET_ENABLE_TRAY = True        # 最小化到系统托盘，方便随时启用

# 无更新时挂件顶部提示文案
WIDGET_NO_UPDATE_NOTICE = "📭 本日のニュースはまだ更新されていません（最新の4件を表示）"
