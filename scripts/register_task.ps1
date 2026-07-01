# register_task.ps1
# 【已退役】此脚本的逻辑已内置到 exe 设置界面（widget/task_scheduler.py），
# 现在请在挂件「设置 → 定时任务」中勾选启用即可，无需手动跑本脚本。
# 保留本文件仅作参考/应急（手动注册）。注意：本脚本 Action 指向旧的
# scripts/run_crawl.bat，与新版 exe --crawl 机制不同。
#
# 注册 Windows 任务计划程序，每天定时运行 NHK Easy News 爬取脚本。
# 用法（管理员 PowerShell）：
#   .\scripts\register_task.ps1                              # 默认每天 09:30 与 21:30
#   .\scripts\register_task.ps1 -Times @("08:00","20:00")    # 自定义多个时间
#   .\scripts\register_task.ps1 -Unregister                  # 注销任务

param(
    [string[]]$Times = @("09:30", "21:30"),
    [string]$TaskName = "NHK-Easy-News-Crawl",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

# 规范化 $Times：兼容 "09:30,21:30" 这类逗号串（命令行数组传参不会自动拆分），
# 拆成多个时间。空白项剔除。
$Times = @($Times | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# 注册 S4U 任务需要管理员权限：若非管理员，则通过 UAC 自提权重启本脚本。
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "需要管理员权限，正在通过 UAC 提权重启 ..."
    # 用 -Command 包装，确保提权窗口在结束前暂留并可见结果（便于排错）
    $inner = "& '$PSCommandPath' -Times '$($Times -join ',')' -TaskName '$TaskName'"
    if ($Unregister) { $inner += " -Unregister" }
    $inner += "; Write-Host ''; Read-Host '完成，按回车关闭'"
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $inner)
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argList
    return
}

# 项目根目录（本脚本上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
$batPath = Join-Path $PSScriptRoot "run_crawl.bat"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已注销任务：$TaskName"
    } else {
        Write-Host "任务不存在：$TaskName"
    }
    return
}

if (-not (Test-Path $batPath)) {
    throw "找不到批处理脚本：$batPath"
}

# 动作：运行批处理；触发器：每天多个指定时间（New-ScheduledTaskTrigger 数组）
$action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $projectRoot
$triggers = $Times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }

# 设置：开机错过即补跑、唤醒执行、电池下也跑、限时防卡死
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# Principal：S4U（无论用户是否登录都运行，无需存密码）。
# 配合 headless 爬取——任务在后台无窗口运行，锁屏/注销也能执行。
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Limited

# 已存在则先注销，保证幂等
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "每天定时爬取 NHK Easy News 四条新闻（headless，$($Times -join ' / ')）" | Out-Null

Write-Host "已注册任务：$TaskName，每天运行时间：$($Times -join ' / ')"
Write-Host "运行方式：S4U（未登录/锁屏也运行）+ headless（无窗口）"
Write-Host "手动测试：Start-ScheduledTask -TaskName $TaskName"
Write-Host "查看信息：Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "注意：音频需可见浏览器（headful），定时任务只抓文字+图片。"
Write-Host "      需要音频时在有桌面时运行：python -m nhk_tool.fetch_audio"
