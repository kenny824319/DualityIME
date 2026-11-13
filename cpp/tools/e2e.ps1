# Runs sz_tsftest against a fresh engine whose learned words go to a temporary
# folder (yours in %APPDATA% are not touched); everything is deleted afterwards and
# the installed engine is started again.
#   powershell -ExecutionPolicy Bypass -File cpp\tools\e2e.ps1 [script] [--tsf]
# The engine just built is used if there is one (the protocol is the same), else the
# installed one. If that engine is not running when the keys go out, the test stops:
# the input method would start the everyday engine instead, with your learned words.
# -SaveTo results.txt: also save the results there (UTF-8, as sz_tsftest wrote them)
# -Config autocorrect_config.json: the test engine's config.json (else every setting as it comes)
# -User learned_singles_user.json: the test engine's user.json to start from (else nothing learned)
# -Dictionary custom_prediction_dictionary.json: the test engine's dictionary.json (個人詞庫; else none)
# -QuickFill quick_fill_sample.json: the test engine's quick_fill.json (快速填入; else none)
param($Script = "$PSScriptRoot\tsf_test.txt", $Mode = "", $SaveTo = "", $Config = "", $User = "", $Dictionary = "", $QuickFill = "")
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$build = Join-Path $PSScriptRoot "..\build"
$installed = Join-Path $env:ProgramFiles "DualityIME\DualityIMEEngine.exe"
$built = Join-Path $build "x64-Release\DualityIMEEngine.exe"
$engine = if (Test-Path $built) { $built } else { $installed }
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
$code = 1
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
    Get-Content $out -Encoding UTF8
    if ($SaveTo) { Copy-Item $out $SaveTo -Force }
    $others = @(Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $ours.Id })
    if ($ours.HasExited -or $others.Count) { "WARNING: another engine ran during the test: results are not valid" }
    else { $code = $p.ExitCode }
} finally {
    Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 300
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    # the one for everyday typing again, through explorer.exe like the installer: started from an
    # administrator window it would run as administrator too, and apps could not reach it
    if (Test-Path $installed) { Start-Process "$env:SystemRoot\explorer.exe" "`"$installed`"" }
    if ($caps -and -not [Console]::CapsLock) { Press-CapsLock }
}
exit $code
