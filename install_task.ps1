# 注册基金雷达的计划任务：
#   1) FundRadar_1430：每个工作日 14:30 抓取行情并推送
#   2) FundRadar_2030：每个工作日 20:30 静默刷新净值与收益（不推送）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "D:\python\pythonw.exe"
$taskName = "FundRadar_1430"

if (-not (Test-Path $python)) {
    throw "找不到 Python：$python。请修改本脚本里的 `$python` 路径。"
}
if (-not (Test-Path (Join-Path $root "config.json"))) {
    throw "缺少 config.json，请先运行 python configure.py"
}

$action = New-ScheduledTaskAction -Execute $python -Argument "run.py --scheduled" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "14:30"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "基金雷达：大盘研判 + 持仓基金买卖建议（14:30）" -Force | Out-Null
    Write-Host "已注册计划任务：$taskName（工作日 14:30）"
} catch {
    Write-Warning "Register-ScheduledTask 失败，改用 schtasks：$($_.Exception.Message)"
    $tr = "`"$python`" `"$root\run.py`" --scheduled"
    schtasks /Create /TN $taskName /SC WEEKLY /D MON,TUE,WED,THU,FRI /TR $tr /ST 14:30 /F
}

# 20:30 静默刷新一次，把当日实际净值写进报告和收益（不推送）
$eveningName = "FundRadar_2030"
$eveningAction = New-ScheduledTaskAction -Execute $python -Argument "run.py --scheduled --force --no-push" -WorkingDirectory $root
$eveningTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "20:30"
try {
    Register-ScheduledTask -TaskName $eveningName -Action $eveningAction -Trigger $eveningTrigger `
        -Settings $settings -Description "基金雷达：晚间静默刷新净值与收益（20:30）" -Force | Out-Null
    Write-Host "已注册计划任务：$eveningName（工作日 20:30，不推送）"
} catch {
    Write-Warning "晚间刷新任务注册失败：$($_.Exception.Message)"
}

Write-Host "查看：schtasks /Query /TN $taskName /V /FO LIST"
Write-Host "手动测试：$python `"$root\run.py`" --scheduled"
Write-Host "管理面板：双击 start_dashboard.bat，或运行 $python dashboard.py --port 8765"
Write-Host "面板地址：http://127.0.0.1:8765"
