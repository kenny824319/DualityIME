# Runs sz_tsftest against a fresh engine whose learned words go to a temporary
# folder (yours in %APPDATA% are not touched); everything is deleted afterwards and
# the installed engine is started again.
#   powershell -ExecutionPolicy Bypass -File cpp\tools\e2e.ps1 [script] [--tsf]
# The engine just built is used if there is one (the protocol is the same), else the
# installed one. If that engine is not running when the keys go out, the test stops:
# the input method would start the everyday engine instead, with your learned words.
# -SaveTo results.txt: also save the results there (UTF-8, as sz_tsftest wrote them)
# -Config autocorrect_config.json: the test engine's config.json (else every setting as it comes)
param($Script = "$PSScriptRoot\tsf_test.txt", $Mode = "", $SaveTo = "", $Config = "")
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$build = Join-Path $PSScriptRoot "..\build"
$installed = Join-Path $env:ProgramFiles "DualityIME\DualityIMEEngine.exe"
$built = Join-Path $build "x64-Release\DualityIMEEngine.exe"
$engine = if (Test-Path $built) { $built } else { $installed }
Get-Process DualityIMEEngine, sz_tsftest -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300
$dir = Join-Path $env:TEMP "sz-e2e"
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir | Out-Null
if ($Config) { Copy-Item $Config (Join-Path $dir "config.json") }
$code = 1
# the cases are typed with Caps Lock off: turn it off for the test, back on afterwards
Add-Type -Namespace SzE2e -Name Keys -MemberDefinition '[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);'
function Press-CapsLock { [SzE2e.Keys]::keybd_event(0x14, 0x3A, 0, [IntPtr]::Zero); [SzE2e.Keys]::keybd_event(0x14, 0x3A, 2, [IntPtr]::Zero); Start-Sleep -Milliseconds 100 }
$caps = [Console]::CapsLock
if ($caps) { Press-CapsLock }
try {
    $ours = Start-Process $engine -ArgumentList "--user-dir", "`"$dir`"", "--data-dir", "`"$(Join-Path $root 'data')`"" -PassThru
    Start-Sleep -Milliseconds 1200
    if ($ours.HasExited) { "STOPPED: the test engine did not start"; exit 1 }
    $out = Join-Path $dir "out.txt"
    # the language it is set up under for this user (中文(香港) is a setting)
    $lang = if (@((Get-WinUserLanguageList).InputMethodTips) -like "0C04:{7F9854C9*") { @("--lang", "0c04") } else { @() }
    $p = Start-Process "$build\x64-Release\sz_tsftest.exe" -ArgumentList (@("`"$Script`"") + @($Mode | Where-Object { $_ }) + $lang) `
        -PassThru -RedirectStandardOutput $out
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
