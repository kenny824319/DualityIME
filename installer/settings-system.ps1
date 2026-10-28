# 設定工具（settings.exe）要動到 Windows 設定的地方：預設輸入法、放在哪個語言。
# 每次呼叫做一件事，最後一行輸出 JSON 給設定工具讀。每個使用者自己的設定，不需要系統管理員權限。
#   settings-system.ps1 -Action state
#   settings-system.ps1 -Action default_ime -On 1|0
#   settings-system.ps1 -Action language_hk -On 1|0
param([ValidateSet("state", "default_ime", "language_hk")] [string]$Action, [int]$On = 0)
$ErrorActionPreference = "Stop"
$OurKey = "HKCU:\Software\DualityIME"   # remembers the previous default for turning it off / uninstalling

# language.ps1: next to this file once installed, in installer\ in the project
$langScript = Join-Path $PSScriptRoot "language.ps1"
. $langScript

function Get-DefaultTip { try { return [string](Get-WinDefaultInputMethodOverride).InputMethodTip } catch { return "" } }
function Our-Tip { return Get-SzTip (Get-SzLanguage) }

function Set-DefaultIme([bool]$on) {
    $ours = Our-Tip
    $cur = Get-DefaultTip
    if ($on) {
        if ($cur -and $cur -ne $ours) {
            if (-not (Test-Path $OurKey)) { New-Item $OurKey | Out-Null }
            New-ItemProperty -Path $OurKey -Name PreviousDefaultIme -Value $cur -PropertyType String -Force | Out-Null
        }
        Set-SzDefault $ours
        return
    }
    $prev = (Get-ItemProperty $OurKey -ErrorAction SilentlyContinue).PreviousDefaultIme
    if ($cur -eq $ours) {
        if ($prev) { Set-SzDefault $prev }
        else { Remove-ItemProperty "HKCU:\Control Panel\International\User Profile" -Name InputMethodOverride -ErrorAction SilentlyContinue }
    }
}

try {
    switch ($Action) {
        "default_ime" { Set-DefaultIme ([bool]$On) }
        "language_hk" { Set-SzLanguage ([bool]$On) }
    }
    $state = @{ ok = $true; default_ime = ((Get-DefaultTip) -eq (Our-Tip)); language_hk = ((Get-SzLanguage) -eq "0C04") }
} catch {
    $state = @{ ok = $false; default_ime = ((Get-DefaultTip) -eq (Our-Tip)); language_hk = ((Get-SzLanguage) -eq "0C04") }
}
$state | ConvertTo-Json -Compress
