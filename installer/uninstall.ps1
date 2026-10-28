# 兩儀輸入法解除安裝的「整台電腦」部分，由 uninstall.exe（或 setup.exe --uninstall）以系統管理員權限執行，
# 從系統管理員自己複製到 %ProgramFiles%\DualityIME.uninstall-<ticks> 的一份執行（只有系統管理員能寫入；
# 程式資料夾會被刪掉）。絕不重新開機。
#   powershell -File uninstall.ps1 [-RemovePime] [-WaitFor <檔案>]
#   -RemovePime  也移除舊版用過的 PIME 與它的 Python 環境（見 remove-pime.ps1）
#   -WaitFor     先等這個檔案出現：內容是 go 才移除，其他內容（或等了 10 分鐘還沒有）就什麼都不做、結束代碼 21
# 這個使用者自己的部分（鍵盤清單、預設輸入法、HKCU、學習紀錄）由 uninstall.exe 以 user.ps1 在沒有提權的程序裡做：
# 鍵盤清單在這之前（uninstall.exe 先取得系統管理員權限，再整理鍵盤清單，然後才寫 go：權限被拒時什麼都沒改），
# 其他的在這之後。
# Steps: stop the engines and lookup.exe (not settings.exe: it started the uninstall and closes itself),
# the Run value, regsvr32 /u (the one place unregistering is right), PIME when asked, the program folder
# (files still in use are moved out of it, renamed, and deleted when Windows next starts: the folder goes
# now, and a reinstall before that restart is not touched), the Settings > Apps entry, the shortcut.
# Writes what it did to <this folder>\uninstall.log too (uninstall.exe reads it afterwards: this window is
# hidden; only administrators can write to the folder, which goes when Windows next starts). The -WaitFor
# file is in the user's %TEMP% folder and only read.
param([switch]$RemovePime, [string]$WaitFor = "")
$ErrorActionPreference = "Stop"
$Dst = Join-Path $env:ProgramFiles "DualityIME"
$UninstallKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DualityIME"
$RunKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
$LogFile = Join-Path $PSScriptRoot "uninstall.log"

function Log($text, $color) {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
    try { Add-Content -Path $LogFile -Value $text -Encoding UTF8 } catch { }
}
function Ok($text) { Log "      OK  $text" Green }
function Info($text) { Log "      ... $text" }
function Warn($text) { Log "      ... $text" Yellow }

# ---- wait for uninstall.exe: it sets up this user's keyboard list first --------------------------------
if ($WaitFor) {
    $until = [DateTime]::Now.AddMinutes(10)
    while (-not (Test-Path $WaitFor) -and [DateTime]::Now -lt $until) { Start-Sleep -Milliseconds 200 }
    $signal = if (Test-Path $WaitFor) { "$(Get-Content $WaitFor -Raw)".Trim() } else { "" }
    if ($signal -ne "go") { exit 21 }
}

Add-Type -Namespace DualityIMEUn -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool MoveFileEx(string existing, string newName, int flags);
'@

$script:later = $false

function Schedule($path) {
    [void][DualityIMEUn.Native]::MoveFileEx($path, [NullString]::Value, 4)   # MOVEFILE_DELAY_UNTIL_REBOOT
    $script:later = $true
}

function Remove-Tree($path) {
    if (-not (Test-Path $path)) { return }
    try { Remove-Item -Recurse -Force -Confirm:$false $path -ErrorAction Stop; return } catch { }
    # what open apps still use (the input method DLL, the running uninstall.exe) can't be deleted, but it
    # can be renamed: it moves to <folder>.removed-<ticks> and goes when Windows next starts. A delete
    # scheduled at its old path would take the file a reinstall puts there before the restart.
    $aside = "$path.removed-$([DateTime]::Now.Ticks)"
    # (inside a catch block $_ is the error: the file is $f)
    foreach ($f in @(Get-ChildItem $path -Recurse -Force -File -ErrorAction SilentlyContinue)) {
        try { Remove-Item -Force $f.FullName -ErrorAction Stop }
        catch {
            try {
                New-Item -ItemType Directory -Force $aside | Out-Null
                $to = Join-Path $aside "$([Guid]::NewGuid().ToString('N'))-$($f.Name)"
                [IO.File]::Move($f.FullName, $to)
                Schedule $to
            } catch { Schedule $f.FullName }
        }
    }
    foreach ($d in @(Get-ChildItem $path -Recurse -Force -Directory -ErrorAction SilentlyContinue | Sort-Object { $_.FullName.Length } -Descending)) {
        try { Remove-Item -Recurse -Force -Confirm:$false $d.FullName -ErrorAction Stop } catch { Schedule $d.FullName }
    }
    try { Remove-Item -Recurse -Force -Confirm:$false $path -ErrorAction Stop } catch { Schedule $path }
    if (Test-Path $aside) { Schedule $aside }   # (after its files)
}

$code = 0
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "需要系統管理員權限（請從「設定 > 應用程式」或兩儀輸入法設定移除）。" }

    # ---- 1. the engines (every user's: nothing may run from the folder) and lookup.exe -----------------
    Remove-ItemProperty $RunKey -Name DualityIMEEngine -ErrorAction SilentlyContinue
    $procs = @(Get-Process DualityIMEEngine -ErrorAction SilentlyContinue)
    $procs += @(Get-Process lookup -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith("$Dst\", [StringComparison]::OrdinalIgnoreCase) })
    foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { } }
    foreach ($p in $procs) { try { $p.WaitForExit(5000) | Out-Null } catch { } }
    Ok "已停止輸入法引擎"

    # ---- 2. Windows forgets the input method -----------------------------------------------------------
    $unregistered = $true
    foreach ($job in @(@("DualityIME64.dll", "System32"), @("DualityIME32.dll", "SysWOW64"))) {
        $dll = Join-Path $Dst $job[0]
        $exe = "$env:SystemRoot\$($job[1])\regsvr32.exe"
        if ((Test-Path $dll) -and (Test-Path $exe)) {
            $p = Start-Process $exe "/u /s `"$dll`"" -Wait -PassThru
            if ($p.ExitCode -ne 0) { Warn "取消登錄 $($job[0]) 失敗（regsvr32 結束代碼 $($p.ExitCode)）"; $unregistered = $false }
        }
    }
    if ($unregistered) { Ok "已向 Windows 取消登錄輸入法" }

    if ($RemovePime) {
        . (Join-Path $PSScriptRoot "remove-pime.ps1")
        $pimeDir = Find-Pime
        if ($pimeDir) {
            if (Remove-Pime $pimeDir) { $script:later = $true }
            Ok "已移除 PIME 與它的 Python 環境"
        }
    }

    # ---- 3. the files, Settings > Apps, the Start menu --------------------------------------------------
    Remove-Tree $Dst
    if (Test-Path $Dst) { Warn "$Dst 還有東西正在使用中，會在下次開機時刪除" }
    else { Ok "已刪除程式檔（$Dst）" }
    if (Test-Path $UninstallKey) { Remove-Item $UninstallKey -Recurse -Force }
    Remove-Item (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "兩儀輸入法設定.lnk") -Force -ErrorAction SilentlyContinue
    Ok "已從開始功能表與「設定 > 應用程式」移除"
    if ($script:later) { Info "有些檔案正被開著的程式使用，會在下次開機時自動刪除（不用特地重新開機）" }
} catch {
    Log ""
    Log "移除失敗：$($_.Exception.Message)" Red
    $code = 1
}
exit $code
