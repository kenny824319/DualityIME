# Runs sz_tsftest against a fresh engine whose learned words go to a temporary
# folder (yours in %APPDATA% are not touched); everything is deleted afterwards and
# the engine next to the DLL Windows loads is started again.
# When what goes into the DLL changed since origin/develop (the last release), the DLL
# Windows loads must be the one just built (cpp\deploy.cmd, or a setup of this tree).
#   powershell -ExecutionPolicy Bypass -File cpp\tools\e2e.ps1 [script] [-Mode:--tsf]
# (-Mode:--tsf: the box talks TSF itself, like Word and Chrome, instead of through the IMM32 layer; a bare --tsf is
# swallowed by PowerShell and never arrives)
# The engine just built is used if there is one (the protocol is the same), else the
# installed one. If that engine is not running when the keys go out, the test stops:
# the input method would start the everyday engine instead, with your learned words.
# -SaveTo results.txt: also save the results there (UTF-8, as sz_tsftest wrote them)
# -Config autocorrect_config.json: the test engine's config.json (else every setting as it comes)
# -User learned_singles_user.json: the test engine's user.json to start from (else nothing learned)
# -Dictionary custom_prediction_dictionary.json: the test engine's dictionary.json (個人詞庫; else none)
# -QuickFill quick_fill_sample.json: the test engine's quick_fill.json (快速填入; else none)
# -Images folder: its files become the test engine's appearance folder (自訂外觀's photos; else none)
# -Symbols symbols_custom.json: the test engine's symbols.json (符號鍵盤自訂, #69; else the built-in pages)
param($Script = "$PSScriptRoot\tsf_test.txt", $Mode = "", $SaveTo = "", $Config = "", $User = "", $Dictionary = "", $QuickFill = "",
      $Images = "", $Symbols = "", [switch]$Game)
# -Game: the test window counts as a game for the run (遊戲中 Shift 交給遊戲, #31): the test program is put in Windows'
#   list of games (HKCU\System\GameConfigStore\Children\DualityIME-e2e) and taken out of it again afterwards
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$build = Join-Path $PSScriptRoot "..\build"
$installed = Join-Path $env:ProgramFiles "DualityIME\DualityIMEEngine.exe"
$built = Join-Path $build "x64-Release\DualityIMEEngine.exe"
$engine = if (Test-Path $built) { $built } else { $installed }
# The DLL Windows loads: the path registered for the CLSID (the installed one, or cpp\build\stage after
# register.cmd). A change to what goes into it (cpp\tip, cpp\common, the engine files it includes) is
# tested only with the DLL just built: 2026-11-03 a change to cpp\tip was tested with the old installed DLL.
# (An engine-only change runs with any DLL; an installed version older than origin/develop is caught by
# tools\e2e_all.ps1 before a release, which compares the DLL with bin\'s.)
$clsid = "HKLM:\SOFTWARE\Classes\CLSID\{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}\InprocServer32"
$loaded = (Get-ItemProperty -LiteralPath $clsid -ErrorAction SilentlyContinue).'(default)'
if (-not $loaded -or -not (Test-Path -LiteralPath $loaded)) {
    "STOPPED: the input method is not registered: install it (or cpp\register.cmd) first"; exit 1
}
$dllSources = @("cpp/tip", "cpp/common", "cpp/engine/json.cpp", "cpp/engine/json.h", "cpp/engine/symbols.cpp",
                "cpp/engine/symbols.h", "cpp/engine/util.cpp", "cpp/engine/util.h")
& git -C $root diff --quiet origin/develop -- @dllSources
if ($LASTEXITCODE -ne 0) {  # (1: changed; anything else: git could not tell, so checked as changed)
    $builtDll = Join-Path $build "x64-Release\DualityIME64.dll"
    $same = (Test-Path -LiteralPath $builtDll) -and
            ((Get-FileHash -LiteralPath $loaded).Hash -eq (Get-FileHash -LiteralPath $builtDll).Hash)
    if (-not $same) {
        "STOPPED: what goes into the input method's DLL changed since origin/develop, but Windows loads another DLL:"
        "         $loaded"
        "         put the one just built in place first: cpp\deploy.cmd (cpp\register.cmd once, as administrator),"
        "         or install a setup built from this tree (cpp\publish.cmd, then installer\build.ps1 -Release)"
        exit 1
    }
}
$loadedDir = Split-Path $loaded -Parent
# No other engine may hold the pipe when the test engine starts (it would leave at once, "already
# running"): the everyday one a run before started through explorer.exe may show up late, and an
# input method that gets the focus back starts one. Stopped until none is left half a second on.
function Stop-OtherEngines {
    for ($i = 0; $i -lt 10; $i++) {
        $running = @(Get-Process DualityIMEEngine -ErrorAction SilentlyContinue)
        if ($running.Count -eq 0 -and $i -gt 0) { return }
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }
}
Get-Process sz_tsftest -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-OtherEngines
$dir = Join-Path $env:TEMP "sz-e2e"
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir | Out-Null
if ($Config) { Copy-Item $Config (Join-Path $dir "config.json") }
if ($User) { Copy-Item $User (Join-Path $dir "user.json") }
if ($Dictionary) { Copy-Item $Dictionary (Join-Path $dir "dictionary.json") }
if ($QuickFill) { Copy-Item $QuickFill (Join-Path $dir "quick_fill.json") }
if ($Symbols) { Copy-Item $Symbols (Join-Path $dir "symbols.json") }
if ($Images) {
    New-Item -ItemType Directory (Join-Path $dir "appearance") | Out-Null
    Copy-Item (Join-Path $Images "*") (Join-Path $dir "appearance")
}
$code = 1
$gameKey = "HKCU:\System\GameConfigStore\Children\DualityIME-e2e"
if ($Game) {
    $tester = (Resolve-Path (Join-Path $build "x64-Release\sz_tsftest.exe")).Path
    New-Item -Path $gameKey -Force | Out-Null
    New-ItemProperty -Path $gameKey -Name MatchedExeFullPath -Value $tester -PropertyType String -Force | Out-Null
}
# the cases are typed with Caps Lock off: turn it off for the test, back on afterwards
Add-Type -Namespace SzE2e -Name Keys -MemberDefinition '[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);'
function Press-CapsLock { [SzE2e.Keys]::keybd_event(0x14, 0x3A, 0, [IntPtr]::Zero); [SzE2e.Keys]::keybd_event(0x14, 0x3A, 2, [IntPtr]::Zero); Start-Sleep -Milliseconds 100 }
$caps = [Console]::CapsLock
if ($caps) { Press-CapsLock }
try {
    # (--keep-elevated: it stays this elevated process's child, for the elevated test window and $ours)
    for ($try = 1; $try -le 3; $try++) {
        $ours = Start-Process $engine -ArgumentList "--user-dir", "`"$dir`"", "--data-dir", "`"$(Join-Path $root 'data')`"", "--keep-elevated" -PassThru
        Start-Sleep -Milliseconds 1200
        if (-not $ours.HasExited) { break }
        Stop-OtherEngines  # (one came up in between and took the pipe: once more)
    }
    if ($ours.HasExited) { "STOPPED: the test engine did not start"; exit 1 }
    $out = Join-Path $dir "out.txt"
    # the language it is set up under for this user (中文(香港) is a setting)
    $lang = if (@((Get-WinUserLanguageList).InputMethodTips) -like "0C04:{7F9854C9*") { @("--lang", "0c04") } else { @() }
    $p = Start-Process "$build\x64-Release\sz_tsftest.exe" -ArgumentList (@("`"$Script`"") + @($Mode | Where-Object { $_ }) + $lang) `
        -PassThru -RedirectStandardOutput $out
    # (the handle read now, or ExitCode is empty once it has exited and the script exits 0 whatever
    # failed: 2026-11-06 a failing case and a stopped run both came back 0)
    $null = $p.Handle
    # (about 3 s a line at most: long scripts, e.g. from tools/asus_diff.py, get the time they need)
    $lines = @(Get-Content $Script | Where-Object { $_ -and -not $_.StartsWith("#") }).Count
    if (-not $p.WaitForExit([Math]::Max(120000, $lines * 3000))) { "HUNG"; Stop-Process $p -Force }
    # (a key a case left down, {+INS}, let go: a test stopped as HUNG could not do it - else Windows keeps it held, #73)
    foreach ($vk in 0x2D, 0x7C, 0x87) { [SzE2e.Keys]::keybd_event($vk, 0, $(if ($vk -eq 0x2D) { 3 } else { 2 }), [IntPtr]::Zero) }
    Get-Content $out -Encoding UTF8
    if ($SaveTo) { Copy-Item $out $SaveTo -Force }
    $others = @(Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $ours.Id })
    if ($ours.HasExited -or $others.Count) { "WARNING: another engine ran during the test: results are not valid" }
    else { $code = $p.ExitCode }
} finally {
    if ($Game) { Remove-Item -Path $gameKey -Recurse -Force -ErrorAction SilentlyContinue }  # (out of the list again)
    Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 300
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    # the one for everyday typing again: the engine next to the DLL Windows loads, the one that DLL starts
    # (2026-11-13 the installed engine was started next to a staged DLL: old and new mixed). Through
    # explorer.exe like the installer: started from an administrator window it would run as administrator
    # too, and apps could not reach it. Without data\ next to it (a stage from before deploy.cmd linked
    # it): started with the repository's, and it starts itself again without administrator rights.
    $everyday = Join-Path $loadedDir "DualityIMEEngine.exe"
    if (Test-Path -LiteralPath $everyday) {
        # (judged by the dictionary in it: a link to a folder that is gone still "exists")
        if (Test-Path -LiteralPath (Join-Path $loadedDir "data\lexicon.txt")) { Start-Process "$env:SystemRoot\explorer.exe" "`"$everyday`"" }
        else { Start-Process $everyday -ArgumentList "--data-dir", "`"$(Join-Path $root 'data')`"" }
    }
    if ($caps -and -not [Console]::CapsLock) { Press-CapsLock }
}
exit $code
