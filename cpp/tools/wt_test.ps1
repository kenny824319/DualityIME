# Types into a new Windows Terminal window (its own TSF text store, unlike the rich edit box of
# sz_tsftest) and checks the line that comes out. Like e2e.ps1: a fresh engine whose learned
# words go to a temporary folder; the installed engine is started again afterwards.
#   powershell -ExecutionPolicy Bypass -File cpp\tools\wt_test.ps1 <keys> <expected>
# keys: characters as typed, and {LEFT} {RIGHT} {UP} {DOWN} {ENTER} {ESC} {BACK} {TAB} {HOME} {END},
# {WAIT} (half a second), {SHOT} (a screenshot of the window to %TEMP%\sz-wt-shot<n>.png).
# Two Enters are added at the end (the text goes out, the line ends).
param([Parameter(Mandatory)] $Keys, [Parameter(Mandatory)] $Expected)
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$installed = Join-Path $env:ProgramFiles "DualityIME\DualityIMEEngine.exe"
$built = Join-Path $PSScriptRoot "..\build\x64-Release\DualityIMEEngine.exe"
$engine = if (Test-Path $built) { $built } else { $installed }

Add-Type -Namespace SzWt -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll")] public static extern short VkKeyScan(char c);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
[DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
public struct RECT { public int Left, Top, Right, Bottom; }
'@
[void][SzWt.Native]::SetProcessDPIAware()
function Title-Of($h) { $sb = New-Object System.Text.StringBuilder 256; [void][SzWt.Native]::GetWindowText($h, $sb, 256); $sb.ToString() }
$tag = "SZWT" + (Get-Random)
function Key($vk, $up) {
    # never type into another window
    if ((Title-Of ([SzWt.Native]::GetForegroundWindow())) -ne $tag) { throw "stopped: the test window lost the focus" }
    [SzWt.Native]::keybd_event([byte]$vk, [byte][SzWt.Native]::MapVirtualKey($vk, 0), $(if ($up) { 2 } else { 0 }), [IntPtr]::Zero)
}
function Tap($vk, $shift, $extended = $false) {
    if ($shift) { Key 0xA0 $false }
    $ext = if ($extended) { 1 } else { 0 }
    if ((Title-Of ([SzWt.Native]::GetForegroundWindow())) -ne $tag) { throw "stopped: the test window lost the focus" }
    [SzWt.Native]::keybd_event([byte]$vk, [byte][SzWt.Native]::MapVirtualKey($vk, 0), $ext, [IntPtr]::Zero)
    [SzWt.Native]::keybd_event([byte]$vk, [byte][SzWt.Native]::MapVirtualKey($vk, 0), 2 + $ext, [IntPtr]::Zero)
    if ($shift) { Key 0xA0 $true }
    Start-Sleep -Milliseconds 60
}
$named = @{ LEFT = @(0x25, $true); UP = @(0x26, $true); RIGHT = @(0x27, $true); DOWN = @(0x28, $true); HOME = @(0x24, $true)
            END = @(0x23, $true); ENTER = @(0x0D, $false); ESC = @(0x1B, $false); BACK = @(0x08, $false); TAB = @(0x09, $false) }
$script:shots = 0
function Shot($h) {
    Add-Type -AssemblyName System.Drawing
    $r = New-Object SzWt.Native+RECT
    [void][SzWt.Native]::GetWindowRect($h, [ref]$r)
    $bmp = New-Object Drawing.Bitmap ($r.Right - $r.Left), ($r.Bottom - $r.Top)
    [Drawing.Graphics]::FromImage($bmp).CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
    $script:shots++
    $bmp.Save((Join-Path $env:TEMP "sz-wt-shot$($script:shots).png"))
}

$caps = [Console]::CapsLock
if ($caps) { [SzWt.Native]::keybd_event(0x14, 0x3A, 0, [IntPtr]::Zero); [SzWt.Native]::keybd_event(0x14, 0x3A, 2, [IntPtr]::Zero) }
Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300
$dir = Join-Path $env:TEMP "sz-wt"
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir | Out-Null
$out = Join-Path $dir "line.txt"
$code = 1
try {
    # (--keep-elevated: it stays this elevated process's child, for the elevated test window and $ours)
    $ours = Start-Process $engine -ArgumentList "--user-dir", "`"$dir`"", "--data-dir", "`"$(Join-Path $root 'data')`"", "--keep-elevated" -PassThru
    Start-Sleep -Milliseconds 1200
    if ($ours.HasExited) { "STOPPED: the test engine did not start"; exit 1 }
    # the line read goes to a file (UTF-8), then the window closes
    # (a script file: wt.exe takes ; as its own command separator)
    $reader = Join-Path $dir "read.ps1"
    [IO.File]::WriteAllText($reader, "`$l = Read-Host`r`n[IO.File]::WriteAllText('$out', `$l)`r`n")
    Start-Process wt.exe -ArgumentList "-w", "new", "--title", $tag, "--suppressApplicationTitle", "powershell", "-NoProfile", "-NoLogo", "-ExecutionPolicy", "Bypass", "-File", "`"$reader`""
    $h = [IntPtr]::Zero
    for ($i = 0; $i -lt 150 -and $h -eq [IntPtr]::Zero; $i++) {
        Start-Sleep -Milliseconds 200
        $h = [SzWt.Native]::FindWindow([NullString]::Value, $tag)
    }
    if ($h -eq [IntPtr]::Zero) { "STOPPED: no test window"; exit 1 }
    Start-Sleep -Milliseconds 1500
    [void][SzWt.Native]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 500
    $i = 0
    while ($i -lt $Keys.Length) {
        if ($Keys[$i] -eq '{') {
            $close = $Keys.IndexOf('}', $i)
            $name = $Keys.Substring($i + 1, $close - $i - 1)
            $i = $close + 1
            if ($name -eq "WAIT") { Start-Sleep -Milliseconds 500 }
            elseif ($name -eq "SHOT") { Start-Sleep -Milliseconds 400; Shot $h }
            else { Tap $named[$name][0] $false $named[$name][1] }
            continue
        }
        $vks = [SzWt.Native]::VkKeyScan($Keys[$i])
        Tap ($vks -band 0xFF) (($vks -shr 8) -band 1)
        $i++
    }
    Start-Sleep -Milliseconds 300
    Tap 0x0D $false   # Enter: the text goes out
    Start-Sleep -Milliseconds 300
    Tap 0x0D $false   # Enter: the line ends
    for ($i = 0; $i -lt 25 -and -not (Test-Path $out); $i++) { Start-Sleep -Milliseconds 200 }
    $got = if (Test-Path $out) { [IO.File]::ReadAllText($out) } else { "(nothing)" }
    $others = @(Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $ours.Id })
    if ($ours.HasExited -or $others.Count) { "WARNING: another engine ran during the test: results are not valid" }
    elseif ($got -eq $Expected) { "ok    $got"; $code = 0 }
    else { "FAIL  $got`n   (expected $Expected)" }
    [IO.File]::WriteAllText((Join-Path $env:TEMP "sz-wt-result.txt"), "$got")
} catch {
    "$_"
} finally {
    $left = [SzWt.Native]::FindWindow([NullString]::Value, $tag)
    if ($left -ne [IntPtr]::Zero) { [void][SzWt.Native]::PostMessage($left, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) }  # WM_CLOSE
    Get-Process DualityIMEEngine -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 300
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    # through explorer.exe like e2e.ps1: an engine before 3.0.6 started from here stays elevated
    if (Test-Path $installed) { Start-Process "$env:SystemRoot\explorer.exe" "`"$installed`"" }
    if ($caps -and -not [Console]::CapsLock) { [SzWt.Native]::keybd_event(0x14, 0x3A, 0, [IntPtr]::Zero); [SzWt.Native]::keybd_event(0x14, 0x3A, 2, [IntPtr]::Zero) }
}
exit $code
