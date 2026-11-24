# Draws the icons the input method shows on the taskbar (cpp\tip\*.ico), the way Windows 11 shows
# its own input methods there: the bare character, white on a dark taskbar and black on a light one.
#   icon_profile.ico, icon_profile_light.ico   the input method's own: U+5100 (yi)
#   icon.ico, icon_light.ico                   the mode button in mixed mode: U+5169 (liang)
#   icon_en.ico, icon_en_light.ico             the mode button in English mode: U+82F1 (ying)
# (the _light ones are black: for a light taskbar)
# The drawing is in tools\tray_icons.cs. Needs the Microsoft JhengHei font (part of Windows).
#   powershell -ExecutionPolicy Bypass -File tools\make_tray_icons.ps1
# (installer\app.ico and settings\ui\logo.svg still come from tools\make_icons.py.)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$tip = Join-Path $root "cpp\tip"
Add-Type -AssemblyName System.Drawing
Add-Type -Path (Join-Path $PSScriptRoot "tray_icons.cs") -ReferencedAssemblies System.Drawing

$sizes = 16, 20, 24, 32, 40, 48, 64, 256
# (code points, not the characters: Windows PowerShell reads a script without a BOM as ANSI)
$icons = [ordered]@{
    "icon_profile" = [string][char]0x5100
    "icon"         = [string][char]0x5169
    "icon_en"      = [string][char]0x82F1
}
$all = -join $icons.Values   # one size and one line for all three: they sit level on the taskbar
foreach ($name in $icons.Keys) {
    foreach ($black in $false, $true) {
        $frames = New-Object 'System.Collections.Generic.List[System.Drawing.Bitmap]'
        foreach ($size in $sizes) { $frames.Add([IconDraw]::Draw($icons[$name], $all, $size, $black)) }
        $file = if ($black) { "${name}_light.ico" } else { "$name.ico" }
        [IconDraw]::SaveIco((Join-Path $tip $file), $frames)
        Write-Host "wrote cpp\tip\$file"
    }
}
