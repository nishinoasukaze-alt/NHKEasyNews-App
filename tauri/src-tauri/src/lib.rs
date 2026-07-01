// NHK Easy News — Tauri v2 壳层。
//
// 设计：UI 用 WebView（dist/ 下原生 HTML/CSS/JS），所有业务能力通过调用
// Python 后端（PyInstaller 爬虫 exe 或源码态 python app.py）的 `backend`
// JSON 子命令完成。后端 stdout 输出单行 JSON，这里解析后回传前端。

use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{json, Value};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, State,
};

/// 后端调用方式：可执行文件 + 前置参数（如源码态的 ["app.py", "backend"]）。
struct Backend {
    program: String,
    base_args: Vec<String>,
    /// 开发态（program 为 python + 源码 app.py）：需设 cwd/PYTHONPATH 指向项目根。
    /// 打包态（program 为 frozen sidecar exe）：自带模块，不设 cwd（编译期路径在目标机不存在）。
    dev_mode: bool,
    /// 打包态显式数据根（app\data）：经 NHK_DATA_ROOT 传给 sidecar，
    /// 使壳与爬虫共用软件根目录下的 data，而非藏在 engine 子目录。None 则用爬虫默认。
    data_root: Option<PathBuf>,
}

impl Backend {
    /// 解析后端调用方式：
    /// 1) 环境变量 NHK_BACKEND_CMD（空格分隔，首项为程序，其余为前置参数）；
    /// 2) 打包态：可执行文件旁的 sidecar `nhk-crawl/NHKEasyNews.exe`；
    /// 3) 开发态：项目内 .venv 的 pythonw + widget/app.py。
    fn resolve(exe_dir: &Path) -> Backend {
        // 1) 环境变量覆盖（开发联调最灵活）
        if let Ok(cmd) = std::env::var("NHK_BACKEND_CMD") {
            let parts: Vec<String> = cmd.split_whitespace().map(|s| s.to_string()).collect();
            if !parts.is_empty() {
                let program = parts[0].clone();
                let mut base_args: Vec<String> = parts[1..].to_vec();
                base_args.push("backend".to_string());
                return Backend { program, base_args, dev_mode: true, data_root: None };
            }
        }

        // 2) 打包态 sidecar：软件根目录下 engine\nhk-crawler.exe（frozen，自带模块）。
        //    数据统一放软件根目录的 data\（经 NHK_DATA_ROOT 传给 sidecar），更直观。
        let sidecar = exe_dir.join("engine").join("nhk-crawler.exe");
        if sidecar.exists() {
            return Backend {
                program: sidecar.to_string_lossy().to_string(),
                base_args: vec!["backend".to_string()],
                dev_mode: false,
                data_root: Some(exe_dir.join("data")),
            };
        }

        // 3) 开发态回退：项目根（tauri/src-tauri 上溯两级）下 .venv + widget/app.py
        //    exe_dir 在 dev 下是 target/debug，故用 CARGO_MANIFEST_DIR 更可靠
        let project_root = dev_project_root();
        let py = project_root.join(".venv").join("Scripts").join("python.exe");
        let app_py = project_root.join("widget").join("app.py");
        let program = if py.exists() {
            py.to_string_lossy().to_string()
        } else {
            "python".to_string()
        };
        Backend {
            program,
            base_args: vec![
                app_py.to_string_lossy().to_string(),
                "backend".to_string(),
            ],
            dev_mode: true,
            data_root: None,
        }
    }

    /// 运行一个 backend 子命令，返回 stdout 解析出的 JSON。
    fn call(&self, args: &[&str]) -> Result<Value, String> {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.base_args);
        for a in args {
            cmd.arg(a);
        }
        // 开发态才设 cwd/PYTHONPATH 指向项目根（源码 app.py 需要找到 nhk_tool/widget）。
        // 打包态 sidecar 是 frozen exe，自带模块，且编译期项目路径在目标机不存在，绝不能设。
        if self.dev_mode {
            let root = dev_project_root();
            cmd.current_dir(&root);
            cmd.env("PYTHONPATH", root.join("src"));
        }
        // 打包态：把数据根显式指向软件根目录 data\（爬虫读 NHK_DATA_ROOT）
        if let Some(dr) = &self.data_root {
            cmd.env("NHK_DATA_ROOT", dr);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let output = cmd
            .output()
            .map_err(|e| format!("启动后端失败：{e}"))?;
        let stdout = String::from_utf8_lossy(&output.stdout);
        let line = stdout.lines().last().unwrap_or("").trim();
        if line.is_empty() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!(
                "后端无 JSON 输出（exit={:?}）：{}",
                output.status.code(),
                stderr.lines().last().unwrap_or("")
            ));
        }
        serde_json::from_str::<Value>(line)
            .map_err(|e| format!("后端 JSON 解析失败：{e}；原文：{line}"))
    }
}

/// 开发态项目根：编译期 CARGO_MANIFEST_DIR = tauri/src-tauri，上溯两级。
fn dev_project_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or(manifest)
}

/// 全局应用状态：解析好的后端调用方式。
struct AppState {
    backend: Backend,
    /// 数据根目录（启动时经一次 backend status 解析并缓存），用于直接读偏好文件，
    /// 避免每次关闭窗口都起 Python 子进程造成卡顿。
    data_root: std::sync::Mutex<Option<PathBuf>>,
}

// ---------------------------------------------------------------------------
// Tauri 命令
// ---------------------------------------------------------------------------

/// 读取 manifest（可指定历史日期），并为每条新闻补 body_preview（读 body 前 120 字）。
/// day 传 None/"" 读最新一日；传 "YYYY-MM-DD" 读该历史日期。
#[tauri::command]
fn read_manifest(state: State<AppState>, day: Option<String>) -> Result<Value, String> {
    let mut args: Vec<&str> = vec!["read-manifest"];
    let day_val = day.unwrap_or_default();
    if !day_val.is_empty() {
        args.push("--day");
        args.push(&day_val);
    }
    let mut res = state.backend.call(&args)?;
    let day_dir = res
        .get("day_dir")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    if let (Some(day_dir), Some(items)) = (
        day_dir.as_ref(),
        res.get_mut("manifest")
            .and_then(|m| m.get_mut("items"))
            .and_then(|i| i.as_array_mut()),
    ) {
        for it in items.iter_mut() {
            let body_rel = it.get("body").and_then(|v| v.as_str()).map(String::from);
            if let Some(rel) = body_rel {
                let preview = read_preview(Path::new(day_dir), &rel, 120);
                if let Some(obj) = it.as_object_mut() {
                    obj.insert("body_preview".into(), json!(preview));
                }
            }
        }
    }
    Ok(res)
}

/// 列出本地已存档日期 + today（供日期选择器判断哪些可选、哪天是今天）。
#[tauri::command]
fn list_days(state: State<AppState>) -> Result<Value, String> {
    state.backend.call(&["list-days"])
}

/// 读取某文件全文（详情页正文）。
#[tauri::command]
fn read_text(day_dir: String, rel: String) -> Result<String, String> {
    let p = safe_join(Path::new(&day_dir), &rel)?;
    std::fs::read_to_string(&p).map_err(|e| format!("读取失败：{e}"))
}

/// 把相对资源路径解析为 WebView 可加载的 asset URL（前端也可直接用
/// convertFileSrc，这里提供 Rust 侧版本以统一）。
#[tauri::command]
fn resolve_asset(
    app: tauri::AppHandle,
    day_dir: String,
    rel: String,
) -> Result<String, String> {
    let p = safe_join(Path::new(&day_dir), &rel)?;
    // Tauri v2：用 asset 协议。前端 convertFileSrc 等价，这里直接返回绝对路径，
    // 由前端 convertFileSrc 包装（见 app.js）。
    let _ = app;
    Ok(p.to_string_lossy().to_string())
}

/// 触发一次爬取（同步等后端返回）。
#[tauri::command]
fn crawl_now(state: State<AppState>) -> Result<Value, String> {
    state.backend.call(&["crawl"])
}

#[tauri::command]
fn get_task_status(state: State<AppState>) -> Result<Value, String> {
    state.backend.call(&["task-status"])
}

#[tauri::command]
fn set_task(
    state: State<AppState>,
    enabled: bool,
    times: Vec<String>,
) -> Result<Value, String> {
    if enabled {
        let mut args: Vec<&str> = vec!["task-register", "--times"];
        for t in &times {
            args.push(t.as_str());
        }
        state.backend.call(&args)
    } else {
        state.backend.call(&["task-unregister"])
    }
}

#[tauri::command]
fn get_prefs(state: State<AppState>) -> Result<Value, String> {
    // 优先直接读 data/widget_prefs.json（免起 Python 子进程，消除设置界面卡顿）
    if let Some(data_root) = state.data_root.lock().ok().and_then(|g| g.clone()) {
        let p = data_root.join("widget_prefs.json");
        if let Ok(text) = std::fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                return Ok(json!({ "ok": true, "prefs": v }));
            }
        } else {
            // 文件不存在（首次运行）→ 空偏好，仍免子进程
            return Ok(json!({ "ok": true, "prefs": {} }));
        }
    }
    // 兜底：data_root 未知时走后端
    state.backend.call(&["prefs-get"])
}

#[tauri::command]
fn set_prefs(state: State<AppState>, patch: Value) -> Result<Value, String> {
    let s = patch.to_string();
    state.backend.call(&["prefs-set", &s])
}

/// 关闭行为：读偏好 close_action，tray→隐藏，quit→退出，其余默认隐藏。
/// 直接读 data/widget_prefs.json（不起 Python 子进程），避免关闭卡顿。
#[tauri::command]
fn win_close(app: tauri::AppHandle, state: State<AppState>) -> Result<(), String> {
    let action = read_close_action(&state).unwrap_or_default();
    if action == "quit" {
        app.exit(0);
    } else if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
    Ok(())
}

/// 从缓存的 data_root 直接读 widget_prefs.json 的 close_action。
fn read_close_action(state: &State<AppState>) -> Option<String> {
    let data_root = state.data_root.lock().ok()?.clone()?;
    let p = data_root.join("widget_prefs.json");
    let text = std::fs::read_to_string(&p).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    v.get("close_action")
        .and_then(|c| c.as_str())
        .map(String::from)
}

// ---------------------------------------------------------------------------
// 辅助
// ---------------------------------------------------------------------------

/// 安全拼接：拒绝越界（.. 逃逸）的相对路径。
fn safe_join(base: &Path, rel: &str) -> Result<PathBuf, String> {
    if rel.contains("..") {
        return Err("非法路径".into());
    }
    Ok(base.join(rel))
}

/// 读文件前 n 字符作预览，换行转空格。
fn read_preview(day_dir: &Path, rel: &str, limit: usize) -> String {
    let Ok(p) = safe_join(day_dir, rel) else {
        return String::new();
    };
    let text = std::fs::read_to_string(&p).unwrap_or_default();
    let flat: String = text.replace(['\n', '\r'], " ").trim().to_string();
    let chars: Vec<char> = flat.chars().collect();
    if chars.len() > limit {
        let head: String = chars[..limit].iter().collect();
        format!("{head}…")
    } else {
        flat
    }
}

// ---------------------------------------------------------------------------
// 入口
// ---------------------------------------------------------------------------
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // 解析后端调用方式（基于当前 exe 所在目录）
            let exe_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .unwrap_or_else(|| PathBuf::from("."));
            let backend = Backend::resolve(&exe_dir);
            // 启动时经一次 backend status 解析 data_root 并缓存（后续关闭窗口直接读偏好文件）
            let data_root = backend
                .call(&["status"])
                .ok()
                .and_then(|v| {
                    v.get("data_root")
                        .and_then(|d| d.as_str())
                        .map(PathBuf::from)
                });
            app.manage(AppState {
                backend,
                data_root: std::sync::Mutex::new(data_root),
            });

            // 系统托盘：显示/隐藏、刷新、设置、退出
            build_tray(app.handle())?;

            // 初始定位到主屏右上角
            if let Some(win) = app.get_webview_window("main") {
                if let Ok(Some(monitor)) = win.current_monitor() {
                    let size = monitor.size();
                    let scale = win.scale_factor().unwrap_or(1.0);
                    let w = (380.0 * scale) as i32;
                    let x = size.width as i32 - w - 24;
                    let _ = win.set_position(tauri::PhysicalPosition { x: x.max(0), y: 24 });
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            read_manifest,
            list_days,
            read_text,
            resolve_asset,
            crawl_now,
            get_task_status,
            set_task,
            get_prefs,
            set_prefs,
            win_close
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// 构建系统托盘图标与菜单。
fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "表示 / 隐藏", true, None::<&str>)?;
    let refresh = MenuItem::with_id(app, "refresh", "更新 / 刷新", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "设置", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "終了 / 退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &refresh, &settings, &quit])?;

    // 托盘图标：优先从打包资源里的 PNG 读取（RGBA 干净），失败再回退窗口图标。
    let icon = app
        .path()
        .resource_dir()
        .ok()
        .map(|d| d.join("icons").join("32x32.png"))
        .filter(|p| p.exists())
        .and_then(|p| tauri::image::Image::from_path(&p).ok())
        .or_else(|| app.default_window_icon().cloned());

    let mut builder = TrayIconBuilder::with_id("main-tray")
        .tooltip("NHK Easy News")
        .menu(&menu);
    if let Some(ic) = icon {
        builder = builder.icon(ic);
    }
    let _tray = builder
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => toggle_main(app),
            "refresh" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.emit("tray-refresh", ());
                }
            }
            "settings" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.emit("tray-settings", ());
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            // 只在左键「释放」时触发一次；否则 Down+Up 两个阶段会各触发一次，
            // 导致窗口显示→隐藏来回闪烁、需反复点击。
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn toggle_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if w.is_visible().unwrap_or(false) {
            let _ = w.hide();
        } else {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}
