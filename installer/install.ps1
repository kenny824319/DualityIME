# 兩儀輸入法安裝的「整台電腦」部分，由安裝檔（DualityIME-X.Y.Z-setup.exe）以系統管理員權限執行：
#   powershell -File install.ps1 -Payload <解開的安裝內容> [-Update]
# 這個使用者自己的部分（資料、HKCU、鍵盤清單、啟動引擎）由安裝檔與 user.ps1 在沒有提權的程序裡做。
#
# 安裝絕不重新開機：正被程式使用的舊檔先改名移開（Windows 允許），新檔立刻放上去，
# 之後打開的程式就用新版；改名的舊檔排定在下次開機時刪除，什麼時候重新開機由使用者決定。
#
# Steps: stop the engines (2.x SmartZhuyinEngine and 3.0 DualityIMEEngine, so nothing writes the
# user's data while setup.exe moves it afterwards), lookup.exe and settings.exe of either install
# folder; -Update keeps settings.exe of 3.0 running (one per user: it started this update and waits
# for the result; its file is replaced by renaming like the DLLs). Back up %ProgramFiles%\DualityIME,
# put the files, register them, HKLM entries; only then remove the 2.x program (its files, Run value,
# uninstall entry, shortcut). Nothing is ever unregistered: 2.x and 3.0 share the CLSID and profile
# GUIDs, and unregistering (UnregisterProfile) could drop the input method from users' keyboard lists;
# registering 3.0 overwrites where the CLSID points (also a developer's cpp\build\stage\).
# A failure puts back what was there: the backed-up 3.0, else the 2.x DLLs registered again (2.x is
# untouched until 3.0 is registered).
# Writes what it did to <Payload>\install.log too (setup.exe shows it: this window is a separate one).
param([Parameter(Mandatory = $true)][string]$Payload, [switch]$Update, [switch]$From2x)
$ErrorActionPreference = "Stop"
$Dst = Join-Path $env:ProgramFiles "DualityIME"
$OldDst = Join-Path $env:ProgramFiles "SmartZhuyin"   # 2.x (智慧輸入)
$Backup = "$Dst.backup"
$RunKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
$UninstallRoot = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
$LogFile = Join-Path $Payload "install.log"

function Log($text, $color) {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
    try { Add-Content -Path $LogFile -Value $text -Encoding UTF8 } catch { }
}
function Ok($text) { Log "      OK  $text" Green }
function Info($text) { Log "      ... $text" }
function Warn($text) { Log "      ... $text" Yellow }

Add-Type -Namespace DualityIMEInstall -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool MoveFileEx(string existing, string newName, int flags);
'@

$script:pendingDelete = $false

function Remove-OrSchedule($path) {
    # delete now, or (still loaded by some app) let Windows delete it while starting up next time
    try { Remove-Item -Force -Recurse $path -ErrorAction Stop; return } catch { }   # (-Recurse: a folder never asks)
    [void][DualityIMEInstall.Native]::MoveFileEx($path, [NullString]::Value, 4)   # MOVEFILE_DELAY_UNTIL_REBOOT
    $script:pendingDelete = $true
}

function Remove-Tree($path) {
    # a folder: what open apps still use (a DLL) goes when Windows next starts, the folders after it
    if (-not (Test-Path $path)) { return }
    try { Remove-Item -Recurse -Force -Confirm:$false $path -ErrorAction Stop; return } catch { }
    Get-ChildItem $path -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object { Remove-OrSchedule $_.FullName }
    Get-ChildItem $path -Recurse -Force -Directory -ErrorAction SilentlyContinue | Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-OrSchedule $_.FullName }
    Remove-OrSchedule $path
}

function Put-File($src, $dst) {
    # a file in use (the input method DLL in every open app, settings.exe during an update) can't be
    # overwritten, but it can be renamed: move it aside, then the new one goes in its place right away
    New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
    try { Copy-Item $src $dst -Force -ErrorAction Stop; return } catch { }
    $old = "$dst.old-$([DateTime]::Now.Ticks)"
    Move-Item $dst $old -Force
    Remove-OrSchedule $old
    Copy-Item $src $dst -Force
}

function Register-Dlls($dir, $prefix = "DualityIME") {
    # (registering only, never unregistering: see the top)
    foreach ($job in @(@("${prefix}64.dll", "System32"), @("${prefix}32.dll", "SysWOW64"))) {   # (32-bit apps)
        $exe = "$env:SystemRoot\$($job[1])\regsvr32.exe"
        if (-not (Test-Path $exe)) { continue }
        $p = Start-Process $exe "/s `"$(Join-Path $dir $job[0])`"" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "註冊 $($job[0]) 失敗（regsvr32 結束代碼 $($p.ExitCode)）" }
    }
}

function Under($proc, $dirs) {
    foreach ($d in $dirs) {
        if ($proc.Path -and $proc.Path.StartsWith("$d\", [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Stop-Ours([bool]$tools) {
    # the engines, wherever they run from (an open app's input method may start one again while this
    # runs: called again before the 2.x folder goes and at the end, before setup.exe moves the data)
    $procs = @(Get-Process SmartZhuyinEngine, DualityIMEEngine -ErrorAction SilentlyContinue)
    if ($tools) {
        $procs += @(Get-Process lookup -ErrorAction SilentlyContinue | Where-Object { Under $_ @($Dst, $OldDst) })
        # settings.exe: with -Update the 3.0 one stays (one per user: it started this update and waits for the result)
        $settingsDirs = if ($Update) { @($OldDst) } else { @($Dst, $OldDst) }
        $procs += @(Get-Process settings -ErrorAction SilentlyContinue | Where-Object { Under $_ $settingsDirs })
    }
    foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { } }
    foreach ($p in $procs) { try { $p.WaitForExit(5000) | Out-Null } catch { } }
}

$code = 0
$backedUp = $false
$hadOld = $null    # had 3.0 before: $true / $false, $null = not known yet (then nothing destructive is undone)
$changed = $false  # set right before the first change: a failure before it undoes nothing
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "需要系統管理員權限（請執行安裝檔 DualityIME-X.Y.Z-setup.exe）。" }
    $Version = (Get-Content (Join-Path $Payload "VERSION") -ErrorAction Stop | Select-Object -First 1).Trim()
    # what goes into the install folder (payload path = installed path)
    $files = @("DualityIME64.dll", "DualityIME32.dll", "DualityIMEEngine.exe", "lookup.exe", "settings.exe",
               "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll",
               "uninstall.exe", "icon.ico", "settings-system.ps1", "language.ps1", "uninstall.ps1", "user.ps1", "remove-pime.ps1",
               "data\lexicon.txt", "data\bigram.bin", "data\lm.bin", "data\english.txt", "data\english_tech.txt", "data\LICENSE-McBopomofo.txt")
    $files += @(Get-ChildItem (Join-Path $Payload "ui") -File | ForEach-Object { "ui\$($_.Name)" })
    foreach ($f in $files + @("ui\index.html")) {
        if (-not (Test-Path (Join-Path $Payload $f))) { throw "安裝內容不完整，缺少 $f，請重新下載安裝檔。" }
    }

    # ---- 1. stop what runs from either install folder ----------------------------------------------
    $hadOld = [bool](Test-Path $Dst)
    $changed = $true
    Info "停止執行中的輸入法引擎與工具"
    Stop-Ours $true

    # ---- 2. back up the installed 3.0, put the new files ---------------------------------------------
    if (Test-Path $Backup) { Remove-Tree $Backup }   # (what a failed earlier run left; what can't go now goes at restart)
    if ($hadOld) {
        New-Item -ItemType Directory -Force $Backup | Out-Null
        Copy-Item -Path "$Dst\*" -Destination $Backup -Recurse -Force
        $backedUp = $true
    }
    New-Item -ItemType Directory -Force $Dst | Out-Null
    # what earlier updates moved aside
    Get-ChildItem $Dst -Recurse -Filter "*.old-*" -ErrorAction SilentlyContinue | ForEach-Object { Remove-OrSchedule $_.FullName }
    foreach ($f in $files) { Put-File (Join-Path $Payload $f) (Join-Path $Dst $f) }
    # version.txt: the version only (the updater reads its first line)
    Set-Content -Path (Join-Path $Dst "version.txt") -Value $Version -Encoding ASCII
    Ok "程式檔已放到 $Dst（$Version）"

    # ---- 3. tell Windows about the input method (the same GUIDs as 2.x: now pointing here) ---------------
    Register-Dlls $Dst
    # the engine starts at sign-in for every user (with that user's rights); user.ps1 starts it now
    $engine = Join-Path $Dst "DualityIMEEngine.exe"
    New-ItemProperty -Path $RunKey -Name DualityIMEEngine -Value "`"$engine`"" -PropertyType String -Force | Out-Null
    Ok "已向 Windows 登錄輸入法"

    # the settings app in the Start menu (for everyone on this computer)
    try {
        $lnk = Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "兩儀輸入法設定.lnk"
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $Dst "settings.exe"
        $sc.WorkingDirectory = $Dst
        $sc.IconLocation = (Join-Path $Dst "icon.ico") + ",0"
        $sc.Description = "兩儀輸入法的設定、使用說明與更新"
        $sc.Save()
    } catch { Warn "無法建立開始功能表的捷徑：$($_.Exception.Message)" }

    # Settings > Apps
    $key = "$UninstallRoot\DualityIME"
    New-Item -Force $key | Out-Null
    $sizeKb = [int]((Get-ChildItem $Dst -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    $values = @{
        DisplayName = "兩儀輸入法"; DisplayVersion = $Version; Publisher = "兩儀輸入法"
        DisplayIcon = (Join-Path $Dst "icon.ico"); InstallLocation = $Dst
        UninstallString = "`"$(Join-Path $Dst 'uninstall.exe')`" --uninstall"
    }
    foreach ($k in $values.Keys) { New-ItemProperty -Path $key -Name $k -Value $values[$k] -PropertyType String -Force | Out-Null }
    foreach ($k in @("NoModify", "NoRepair")) { New-ItemProperty -Path $key -Name $k -Value 1 -PropertyType DWord -Force | Out-Null }
    New-ItemProperty -Path $key -Name EstimatedSize -Value $sizeKb -PropertyType DWord -Force | Out-Null
    Ok "開始功能表與「設定 > 應用程式」：兩儀輸入法"

    # ---- 4. the 2.x program (智慧輸入), once 3.0 is registered: never unregistered (see the top) ----------
    try {
        $old2x = (Test-Path $OldDst) -or (Test-Path "$UninstallRoot\SmartZhuyin")
        Stop-Ours $false
        Remove-Tree $OldDst
        Remove-ItemProperty $RunKey -Name SmartZhuyinEngine -ErrorAction SilentlyContinue
        Remove-Item "$UninstallRoot\SmartZhuyin" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "智慧輸入設定.lnk") -Force -ErrorAction SilentlyContinue
        if ($old2x) { Ok "已移除舊版「智慧輸入」的程式（記憶、詞庫與設定會搬到兩儀輸入法）" }
    } catch { Warn "移除舊版「智慧輸入」的程式沒有完成：$($_.Exception.Message)（兩儀輸入法已安裝好）" }
} catch {
    Log ""
    Log "安裝失敗：$($_.Exception.Message)" Red
    $code = 1
    if (-not $changed) {
        Warn "電腦上什麼都沒有改變"
    } elseif ($backedUp) {
        # the version from before: its files back (renamed aside when in use), registered again
        try {
            foreach ($f in @(Get-ChildItem $Backup -Recurse -File)) {
                $rel = $f.FullName.Substring($Backup.Length + 1)
                if ($rel -like "*.old-*") { continue }
                Put-File $f.FullName (Join-Path $Dst $rel)
            }
            Register-Dlls $Dst
            Warn "已恢復成安裝前的版本"
        } catch {
            Log "恢復安裝前的版本失敗：$($_.Exception.Message)（備份在 $Backup）" Red
            $backedUp = $false   # (kept)
        }
    } elseif ($hadOld -eq $false) {
        # no 3.0 before: 2.x (untouched so far) is the registered one again, nothing of 3.0 stays.
        # Not unregistered (see the top); a first install without 2.x leaves the registration, never listed.
        if (Test-Path (Join-Path $OldDst "SmartZhuyin64.dll")) {
            try { Register-Dlls $OldDst "SmartZhuyin"; Warn "已恢復成安裝前的「智慧輸入」" }
            catch { Log "恢復安裝前的「智慧輸入」失敗：$($_.Exception.Message)" Red }
        }
        Remove-ItemProperty $RunKey -Name DualityIMEEngine -ErrorAction SilentlyContinue
        Remove-Item "$UninstallRoot\DualityIME" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "兩儀輸入法設定.lnk") -Force -ErrorAction SilentlyContinue
        Remove-Tree $Dst
    }
}
if ($backedUp) { Remove-Tree $Backup }

if ($code -eq 0) {
    # PIME (versions before 2.0) is handled after the files, as before; a failure here is not the install's
    try {
        $pime = (Get-ItemProperty "HKLM:\SOFTWARE\PIME" -ErrorAction SilentlyContinue)."(default)"
        if (-not $pime) { $pime = Join-Path ${env:ProgramFiles(x86)} "PIME" }
        $oldDir = Join-Path $pime "python\input_methods\smart_zhuyin"
        if (Test-Path $oldDir) {
            # only our files in PIME; PIME itself and its other input methods stay. PIME's own installer
            # or uninstaller is never run here (in silent mode it may restart the computer by itself).
            Get-Process PIMELauncher, python, pythonw, hotkey, lookup -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -and $_.Path.StartsWith($pime, [StringComparison]::OrdinalIgnoreCase) } |
                Stop-Process -Force
            Start-Sleep -Milliseconds 500
            Remove-Tree $oldDir
            # PIME lists its input methods when registered: again, without ours
            foreach ($job in @(@("x64\PIMETextService.dll", "System32"), @("arm64\PIMETextService.dll", "System32"),
                               @("x86\PIMETextService.dll", "SysWOW64"))) {
                $dll = Join-Path $pime $job[0]
                $exe = "$env:SystemRoot\$($job[1])\regsvr32.exe"
                if ((Test-Path $dll) -and (Test-Path $exe)) { Start-Process $exe "/s `"$dll`"" -Wait }
            }
            Start-Process "$env:SystemRoot\explorer.exe" "`"$(Join-Path $pime 'PIMELauncher.exe')`""
            Ok "已移除 PIME 版的智慧輸入"
        }
        # PIME itself (and the Python that came with it) is not needed any more: ask (not in an update), then remove it
        . (Join-Path $Payload "remove-pime.ps1")
        $pimeDir = Find-Pime
        if ($pimeDir -and -not $Update) {
            $others = @(Get-PimeOthers $pimeDir)
            Write-Host ""
            Write-Host "      這台電腦還裝著舊版用的 PIME 輸入法框架（含它下載的 Python 執行環境），新版已經不需要。" -ForegroundColor Yellow
            if ($others.Count) {
                Write-Host "      注意：PIME 裡還有其他輸入法：$($others -join '、')。移除 PIME 後它們也會一起移除。" -ForegroundColor Yellow
                $answer = Read-Host "      要移除 PIME 嗎？(y/N)"
                $yes = $answer -match '^(y|yes|是)$'
            } else {
                $answer = Read-Host "      要移除 PIME 嗎？(Y/n)"
                $yes = $answer -notmatch '^(n|no|否)$'
            }
            if ($yes) {
                if (Remove-Pime $pimeDir) { $script:pendingDelete = $true }
                Ok "已移除 PIME 與它的 Python 環境、設定與記錄"
            } else {
                Info "保留 PIME（之後可以在「設定 > 應用程式」移除）"
            }
        } elseif (-not $pimeDir -and (Test-Path (Join-Path ${env:ProgramFiles(x86)} "PIME"))) {
            # PIME removed before, but some of its files are still there
            if (Remove-PimeLeftovers) { $script:pendingDelete = $true }
        }
    } catch { Warn "清理 PIME 失敗：$($_.Exception.Message)" }
    if ($script:pendingDelete -and -not $From2x) {   # from 2.x setup.exe asks to sign out instead
        Info "已經開著的程式會繼續用舊版，關掉再開就是新版；舊檔會在下次開機時自動清掉（不用特地重新開機）"
    }
}
# setup.exe moves %APPDATA%\SmartZhuyin next: no engine may write it meanwhile (user.ps1 starts 3.0's again)
if ($changed) { Stop-Ours $false }
exit $code
