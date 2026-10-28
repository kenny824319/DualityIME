# 兩儀輸入法安裝的「這個使用者」部分，由安裝檔在沒有提權的程序裡執行（install.ps1 之後；
# 舊版 %APPDATA%\SmartZhuyin 的資料已由安裝檔搬好）：
#   powershell -File user.ps1 -Payload <解開的安裝內容> [-First] [-HadTips "0404:{...}{...};..."] [-HadDefault "0404:{...}{...}"]
#   -First       沒有裝著 2.x 或 3.0 的程式（第一次安裝，或移除後再裝，只剩學習紀錄）：加進鍵盤清單，但不設為預設輸入法
#   -HadTips     安裝前這個使用者鍵盤清單裡的兩儀輸入法項目；-HadDefault 安裝前的預設輸入法（是兩儀輸入法時）
# Not first: the keyboard list and the default input method stay as the user had them. A safety net:
# an entry of ours or a default that was there before the install and is missing now (whatever took
# it: the install never unregisters anything) is put back; nothing else is added.
#
# 解除安裝的「這個使用者」部分，由 uninstall.exe 在沒有提權的程序裡執行（從 %TEMP% 裡的一份複本）：
#   powershell -File user.ps1 -Uninstall     整台電腦的部分之前：預設輸入法改回原本的、鍵盤清單拿掉兩儀輸入法
#                                            （中文(台灣)留在清單裡）、為它加的中文(香港)拿掉；失敗時結束代碼 1，
#                                            uninstall.exe 就不移除程式
#   powershell -File user.ps1 -Cleanup [-RemoveUserData]
#                                            整台電腦的部分之後：還原舊版改過的快捷鍵，刪除 HKCU\Software\DualityIME、
#                                            %LOCALAPPDATA%\DualityIME，加 -RemoveUserData 也刪 %APPDATA%\DualityIME
param([string]$Payload = $PSScriptRoot, [switch]$First, [string]$HadTips = "", [string]$HadDefault = "",
      [switch]$Uninstall, [switch]$Cleanup, [switch]$RemoveUserData)
$ErrorActionPreference = "Stop"
$OurKey = "HKCU:\Software\DualityIME"
$LegacyKey = "HKCU:\Software\SmartZhuyin"   # 2.x (智慧輸入)
$Clsid = "{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}"
$OldGuid = "{9B7D10BE-5868-4C53-BD98-1F713F72A7A8}"   # the PIME version's profile
$PimeTip = "0404:{35F67E9D-A54D-4177-9697-8B0AB71A9E04}$OldGuid"

function Ok($text) { Write-Host "      OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "      ... $text" -ForegroundColor Yellow }

$code = 0
function Try-Step($name, [scriptblock]$block) {
    try { & $block } catch { Warn "$name 失敗：$($_.Exception.Message)"; $script:code = 1 }
}

Add-Type -Namespace DualityIMEUser -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint action, uint param, IntPtr p, uint flags);
[DllImport("input.dll", CharSet = CharSet.Unicode)] public static extern bool InstallLayoutOrTip(string tip, uint flags);
'@

function Restore-LegacyHotkeys {
    # an old version (before 2026-10-25) changed the keyboard-switch hotkeys: they go back to how they were
    $backup = "$LegacyKey\ToggleBackup"
    if (-not (Test-Path $backup)) { return }
    $toggle = "HKCU:\Keyboard Layout\Toggle"
    $saved = Get-ItemProperty $backup
    foreach ($n in @("Hotkey", "Language Hotkey", "Layout Hotkey")) {
        if ($saved.$n) { Set-ItemProperty $toggle -Name $n -Value $saved.$n }
        else { Remove-ItemProperty $toggle -Name $n -ErrorAction SilentlyContinue }
    }
    [void][DualityIMEUser.Native]::SystemParametersInfo(0x5B, 0, [IntPtr]::Zero, 2)   # SPI_SETLANGTOGGLE
    Ok "切換輸入法的快捷鍵已還原成安裝舊版之前的設定"
}

function Test-Ours($tip) { return "$tip" -like "*$Clsid*" -or "$tip" -like "*$OldGuid*" }

# ---- uninstall, before the machine part: this user's keyboard list -----------------------------------
if ($Uninstall) {
    . (Join-Path $Payload "language.ps1")
    # the default input method first (while ours is still in the list): the previous default comes back
    Try-Step "改回原本的預設輸入法" {
        if (Test-Ours ([string](Get-WinDefaultInputMethodOverride).InputMethodTip)) {
            $prev = (Get-ItemProperty $OurKey -ErrorAction SilentlyContinue).PreviousDefaultIme
            if ($prev) { Set-SzDefault $prev }
            else { Remove-ItemProperty "HKCU:\Control Panel\International\User Profile" -Name InputMethodOverride -ErrorAction SilentlyContinue }
            Ok "已改回原本的預設輸入法"
        }
    }
    try {
        # 中文(台灣) must not be left with nothing: Windows would take the language out of the list, and
        # with it the Windows display language (the next language's after signing out) and the Store's.
        # If ours is the only one there, Windows' own 微軟注音 goes in first (the plain keyboard if not
        # even that is installed): the list is as on a Windows with no other input method installed.
        $tips = @((Get-WinUserLanguageList).InputMethodTips)
        $oursTw = @($tips | Where-Object { $_ -like "0404:*" -and (Test-Ours $_) })
        $othersTw = @($tips | Where-Object { $_ -like "0404:*" -and -not (Test-Ours $_) })
        if ($oursTw.Count -and -not $othersTw.Count) {
            $bopomofo = "{B115690A-EA02-48D5-A231-E3578D2FDF80}{B2F9C502-1742-11D4-9790-0080C882687E}"
            $installed = Test-Path "HKLM:\SOFTWARE\Microsoft\CTF\TIP\{B115690A-EA02-48D5-A231-E3578D2FDF80}\LanguageProfile\0x00000404\{B2F9C502-1742-11D4-9790-0080C882687E}"
            $keep = if ($installed) { "0404:$bopomofo" } else { "0404:00000404" }
            if (-not [DualityIMEUser.Native]::InstallLayoutOrTip($keep, 0)) { throw "無法把$(if ($installed) { '微軟注音' } else { '中文(台灣)鍵盤' })加進鍵盤清單" }
            Ok "已加入$(if ($installed) { '微軟注音' } else { '中文(台灣)鍵盤' })（中文(台灣)不能沒有輸入法，否則 Windows 會把這個語言移出清單）"
        }
        # one input method at a time (ILOT_UNINSTALL): rewriting the whole list could drop other languages
        foreach ($tip in $tips) {
            if (Test-Ours $tip) { [void][DualityIMEUser.Native]::InstallLayoutOrTip($tip, 1) }
        }
        $left = @((Get-WinUserLanguageList).InputMethodTips | Where-Object { Test-Ours $_ })
        if ($left.Count) { Warn "鍵盤清單裡還有兩儀輸入法（$($left -join ', ')），取消登錄後 Windows 會拿掉它" }
        else { Ok "已從這個使用者的鍵盤清單移除" }
    } catch {
        Warn "整理鍵盤清單失敗：$($_.Exception.Message)"
        exit 1
    }
    # 中文(香港), if it came into the list only for us (the setting for Ctrl+Shift)
    Try-Step "移除為兩儀輸入法加入的中文(香港)" { Remove-SzAddedLanguage }
    exit 0   # (a failure above is reported; the program can still be removed)
}

# ---- uninstall, after the machine part: what this user has of ours ------------------------------------
if ($Cleanup) {
    Try-Step "還原切換輸入法的快捷鍵" { Restore-LegacyHotkeys }
    Try-Step "刪除兩儀輸入法的設定值" {
        Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name SmartZhuyinHotkey -ErrorAction SilentlyContinue
        if (Test-Path $OurKey) { Remove-Item $OurKey -Recurse -Force }
    }
    # the settings window's browser data (WebView2): nothing of the user's in it
    Try-Step "刪除設定視窗的瀏覽器資料" {
        $web = Join-Path $env:LOCALAPPDATA "DualityIME"
        if (Test-Path $web) { Remove-Item $web -Recurse -Force }
    }
    if ($RemoveUserData) {
        Try-Step "刪除學習紀錄與設定" {
            $data = Join-Path $env:APPDATA "DualityIME"
            if (Test-Path $data) { Remove-Item $data -Recurse -Force }
            Ok "已刪除學習紀錄與設定（$data）"
        }
    }
    exit $code
}

# ---- 1. what 2.x kept in HKCU ----------------------------------------------------------------------
Try-Step "搬移舊版的設定值" {
    if (-not (Test-Path $OurKey)) { New-Item $OurKey | Out-Null }   # (not -Force: keeps what is stored there)
    # the 2.x git updater's values (3.0 updates from GitHub Releases)
    foreach ($n in @("SourceDir", "UpdateBackup")) { Remove-ItemProperty $OurKey -Name $n -ErrorAction SilentlyContinue }
    if (-not (Test-Path $LegacyKey)) { return }
    $old = Get-ItemProperty $LegacyKey
    $ours = Get-ItemProperty $OurKey
    foreach ($n in @("PreviousDefaultIme", "Language", "UpdateBranch")) {
        if ($null -ne $old.$n -and "$($old.$n)" -ne "" -and $null -eq $ours.$n) {
            New-ItemProperty -Path $OurKey -Name $n -Value "$($old.$n)" -PropertyType String -Force | Out-Null
        }
    }
    Restore-LegacyHotkeys
    Remove-Item $LegacyKey -Recurse -Force
}
Try-Step "清除舊版的殘留" {
    Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name SmartZhuyinHotkey -ErrorAction SilentlyContinue
    # the 2.x settings window's browser data (WebView2): nothing of the user's in it
    $web = Join-Path $env:LOCALAPPDATA "SmartZhuyin"
    if (Test-Path $web) { Remove-Item $web -Recurse -Force }
}

# ---- 2. this user's keyboard list --------------------------------------------------------------------
. (Join-Path $Payload "language.ps1")
Try-Step "設定鍵盤清單" {
    $inList = @((Get-WinUserLanguageList).InputMethodTips | ForEach-Object { "$_".ToUpperInvariant() })
    # the PIME version's entry of ours goes (it is gone from this computer)
    if ($inList -contains $PimeTip.ToUpperInvariant()) { [void][DualityIMEUser.Native]::InstallLayoutOrTip($PimeTip, 1) }   # ILOT_UNINSTALL
    if ($First) {
        # under 中文(香港) from Windows 11 24H2 on, else 中文(台灣) (see language.ps1); not the default input method
        $hk = [Environment]::OSVersion.Version.Build -ge 26100
        Set-SzLanguage $hk
        Ok "已加入「$(if ($hk) { '中文(香港)' } else { '中文(台灣)' })」的鍵盤清單"
        return
    }
    # safety net: what of ours was there before the install and is missing now goes back
    foreach ($tip in @($HadTips -split ";" | Where-Object { $_ })) {
        if ($inList -notcontains $tip.ToUpperInvariant()) {
            if (-not [DualityIMEUser.Native]::InstallLayoutOrTip($tip, 0)) { throw "無法把兩儀輸入法放回鍵盤清單" }
        }
    }
    if ($HadDefault -and [string](Get-WinDefaultInputMethodOverride).InputMethodTip -ne $HadDefault) {
        Set-SzDefault $HadDefault
    }
}

# ---- 3. the engine, for this user without administrator rights (through explorer) -------------------------
Try-Step "啟動輸入法引擎" {
    $engine = Join-Path $env:ProgramFiles "DualityIME\DualityIMEEngine.exe"
    if (Test-Path $engine) { Start-Process "$env:SystemRoot\explorer.exe" "`"$engine`"" }
}
exit $code
