# nhk-web-easy-tool

每天定时从 [NHK Easy News](https://news.web.nhk/news/easy/) 主页爬取展示的四条新闻
（图片、标题、正文、音频），按固定格式保存到本地，并在桌面通过一个可定制的
**Tauri**（Rust + WebView）挂件快速展示。

## 架构

UI 与爬虫分层：Tauri 壳负责界面，Python 引擎负责爬取，壳通过命令行 JSON 桥接调用引擎。

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Tauri 壳 (Rust + WebView)  │        │  Python 引擎 (PyInstaller)   │
│  · 无边框/置顶/可缩放挂件   │  调用  │  · Playwright 驱动 Chromium  │
│  · 列表/详情/设置三视图     │ ─────▶ │  · 过同意门禁 + 抓四条新闻   │
│  · 系统托盘                 │ backend│  · 图片 / SSR 正文 / HLS 音频 │
│  · 手动爬取 / 日期浏览      │  JSON  │  · Windows 任务计划管理      │
└─────────────────────────────┘        └──────────────────────────────┘
              │                                       │
              └──────────────┬────────────────────────┘
                             ▼
                    data/ (manifest.json + 图片/音频/正文)
```

- 壳以 `engine\nhk-crawler.exe backend <cmd>` 调用引擎（stdout 单行 JSON）；爬取以
  `--crawl` 调用。数据经 `NHK_DATA_ROOT` 统一落在软件根目录 `data\`。
- 定时由 **Windows 任务计划程序**负责（`WakeToRun` 可从睡眠唤醒执行），在设置界面一键启用。

> **站点说明（2026-06 实测）**
> `news.web.nhk` 为 Next.js SPA：列表走 `top-list.json`（需同意门禁 cookie，否则 401）；
> 首访有「海外アクセス確認」弹窗，新版 headless（`--headless=new`）可自动点过、cookie 过期自动续期；
> 音频为 `media.vd.st.nhk` 的 AES-128 加密 HLS 流，工具捕获 token、下载分片并解密合并为 AAC。

## 目录结构

```
src/nhk_tool/      爬取与处理（Python 引擎核心）
  config.py        集中配置（站点URL/保存目录/抓取参数；支持 NHK_DATA_ROOT）
  fetcher.py       网络抓取：Playwright 会话/门禁/限速/重试
  parser.py        解析层（站点结构假设集中于此）
  storage.py       保存固定目录 + manifest.json 索引
  run_crawl.py     爬取入口，编排全流程（日志标注触发来源）
widget/            Python 引擎的桌面/命令行入口与工具
  app.py           入口：--crawl 爬取 / backend 命令行桥接（Tauri 壳调用）
  backend_cli.py   JSON 桥接：status/read-manifest/list-days/crawl/task-*/prefs-*
  task_scheduler.py 注册/管理 Windows 任务计划
  prefs.py         用户偏好读写（widget_prefs.json）
  （app_gui/ui/settings_dialog 等为旧 PySide6 UI，Tauri 版下不再使用，保留供参考/测试）
tauri/             Tauri 桌面壳
  dist/            前端（原生 HTML/CSS/JS：index.html/style.css/app.js）
  src-tauri/       Rust（lib.rs 命令 + 托盘 + 窗口；tauri.conf.json 配置）
packaging/
  build.bat        两段式一键打包（引擎 + 壳 → 便携 app\）
  nhk_news.spec    PyInstaller 打包规格（引擎 exe，含 Chromium）
  app.ico          应用图标（壳/窗口/托盘/引擎统一用它）
  make_icon.py     生成 app.ico 的脚本
tests/             离线单测（fixtures 不依赖网络）
```

## 本地数据格式

```
data/news/2026-07-01/
  manifest.json                 # 挂件唯一数据契约（有序四条索引）
  ne2026063012475/
    image.jpg  audio.aac  body.txt  meta.json
  ...
data/logs/crawl-2026-07-01.log  # 每次爬取日志，开头标注触发来源
data/widget_prefs.json          # 偏好（关闭行为/开机自启/爬取时间/任务开关/日期标记）
```

## 使用（普通用户：下载即用）

从 **GitHub Releases** 下载打包好的 `NHKEasyNews-app.zip`，解压后：

1. 双击 `NHKEasyNews.exe` 启动挂件（无需安装 Python/Rust）。
2. 点右上角**爬取图标**手动抓取当日新闻（会二次确认，爬取期间引擎短暂运行）。
3. 点 **⚙ 设置**可配置：开机自启、关闭行为、每日爬取时间、启用 Windows 定时任务
   （启用/改时间需管理员授权，会弹 UAC）。
4. 顶部**日期选择器**可回看本地已存档的历史日期；历史日期无法补爬（NHK 仅提供当前最新新闻）。

```
NHKEasyNews-App/app/
  NHKEasyNews.exe       ← 双击这个
  data/                 ← 新闻/日志/配置（便携：拷走整个 app\ 即带着数据）
  engine/nhk-crawler.exe ← 爬虫引擎（含 Chromium，无需直接运行）
```

## 从源码构建（开发者）

前置：Windows + Python 3.14 + Rust（MSVC 工具链）+ Node（Tauri CLI 依赖）。

```powershell
# 1) Python 引擎依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

# 2) 首次过一次同意门禁（生成 data\auth_state.json；之后自动续期）
$env:PYTHONPATH = "$pwd\src"
python -m nhk_tool.setup_consent

# 3) Rust + Tauri CLI（若未装）
#    rustup 装 stable-x86_64-pc-windows-msvc；cargo install tauri-cli --version "^2"

# 4) 开发模式（热重载；后端走源码）
cd tauri\src-tauri
$env:NHK_BACKEND_CMD = "$(Resolve-Path ..\..\.venv\Scripts\python.exe) $(Resolve-Path ..\..\widget\app.py)"
cargo tauri dev
```

### 一键打包为便携应用

```powershell
.\packaging\build.bat
```
- 两段式：先 PyInstaller 打引擎（含 Chromium），再 `cargo build --release` 打壳，组装到
  项目外 `..\..\NHKEasyNews-App\app\`。
- 打包产物**不进 Git 仓库**（含几百 MB Chromium）；发布时把 `app\` 打成 zip 传到 GitHub Releases。

## 定时（Windows 任务计划）

- 在「⚙ 设置 → 启用 Windows 定时任务」勾选启用（弹一次 UAC），注册系统级每日任务。
- 任务以 `nhk-crawler.exe --crawl --from-task` 运行：不开窗、跑完退出，挂件无需常驻。
- **`WakeToRun`**：能把电脑从睡眠中唤醒执行（这是用任务计划而非进程内定时的根本原因）。
- 前提：唤醒依赖系统「允许唤醒计时器」开启；完全关机/休眠不保证唤醒。
- 日志会标注触发来源（`Windows 定时任务` / `挂件手动爬取` / `命令行`）。

## 测试

```powershell
$env:PYTHONPATH = "$pwd\src"
pytest -q
```
解析/存储/桥接单测使用离线 fixtures，不访问网络。

## 注意

- 解析结构集中在 `parser.py`（列表字段 + 正文容器），站点改版时优先改它与 `config.py`。
- 音频若在你的网络不可达 `media.vd.st.nhk`，可在 `config.py` 设 `FETCH_AUDIO=False` 跳过。
- 抓取已内置请求间隔与重试退避，请勿调低 `REQUEST_INTERVAL` 高频抓取。
- 新闻日文内容按原文保留，工具不做翻译。
