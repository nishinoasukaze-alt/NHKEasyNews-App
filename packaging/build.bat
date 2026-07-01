@echo off
REM Two-stage packaging for NHK Easy News (Tauri shell + Python crawler engine).
REM Portable output at ..\..\NHKEasyNews-App\app:
REM   app\NHKEasyNews.exe        Tauri shell (UI, double-click entry)
REM   app\engine\nhk-crawler.exe Python crawler engine (with Chromium)
REM   app\data\                 runtime (news / logs / config)
setlocal
cd /d "%~dp0.."

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] venv python not found: %VENV_PY%
    exit /b 1
)

REM Rust/Tauri toolchain (MSI install + cargo bin)
set "PATH=C:\Program Files\Rust stable MSVC 1.96\bin;%USERPROFILE%\.cargo\bin;%PATH%"

REM Output outside the repo (resolve to ABSOLUTE path to avoid cmd's flaky
REM relative "..\" handling in rmdir/mkdir on some setups).
for %%I in ("..\NHKEasyNews-App") do set "OUT_ROOT=%%~fI"
set "PY_DIST=%OUT_ROOT%\dist"
set "PY_BUILD=%OUT_ROOT%\build"
set "APP_DIR=%OUT_ROOT%\app"

echo [1/4] Cleaning old outputs...
if exist "%PY_DIST%\NHKEasyNews" rmdir /s /q "%PY_DIST%\NHKEasyNews"
if exist "%PY_BUILD%" rmdir /s /q "%PY_BUILD%"
if exist "%APP_DIR%" rmdir /s /q "%APP_DIR%"

echo [2/4] Building crawler engine (PyInstaller onedir)...
"%VENV_PY%" -m PyInstaller --noconfirm ^
    --distpath "%PY_DIST%" ^
    --workpath "%PY_BUILD%" ^
    packaging\nhk_news.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed
    exit /b 1
)

echo [3/4] Building Tauri shell (release exe)...
REM Force tauri-build to re-embed the exe icon: clear this crate's build-script
REM output, otherwise incremental build keeps the cached old icon.
if exist "tauri\src-tauri\target\release\build" (
    for /d %%D in ("tauri\src-tauri\target\release\build\nhk-easy-news-*") do rmdir /s /q "%%D"
)
pushd tauri\src-tauri
cargo build --release
if errorlevel 1 (
    echo [ERROR] cargo build failed
    popd
    exit /b 1
)
popd

echo [4/4] Assembling portable app folder -> %APP_DIR%
if exist "%APP_DIR%" rmdir /s /q "%APP_DIR%"
mkdir "%APP_DIR%"
if not exist "%APP_DIR%" (
    echo [ERROR] cannot create app dir: %APP_DIR%
    exit /b 1
)
REM Shell exe (double-click entry; icon embedded at compile time from app.ico)
copy /y "tauri\src-tauri\target\release\nhk-easy-news.exe" "%APP_DIR%\NHKEasyNews.exe" >nul
REM Crawler engine into engine\, renamed nhk-crawler.exe to distinguish from shell
xcopy /e /i /y "%PY_DIST%\NHKEasyNews" "%APP_DIR%\engine" >nul
if exist "%APP_DIR%\engine\NHKEasyNews.exe" ren "%APP_DIR%\engine\NHKEasyNews.exe" "nhk-crawler.exe"
if not exist "%APP_DIR%\engine\nhk-crawler.exe" (
    echo [ERROR] crawler engine missing after assembly
    exit /b 1
)

echo.
echo Done. Portable app: %APP_DIR%\NHKEasyNews.exe
echo   - UI shell (double-click this): NHKEasyNews.exe
echo   - Crawler engine (internal): engine\nhk-crawler.exe (with Chromium)
echo   - Runtime data: data\ (created next to NHKEasyNews.exe on first run)
echo.
echo Tip: zip the whole app\ folder for GitHub Releases (portable, no install).
endlocal
