# Removes PIME, the input method framework that versions before 2.0 needed, with everything that
# came with it: its folder (its own Python, 新酷音), its registration with Windows, start-up entry,
# Settings > Apps entry, Start menu shortcuts, its settings (%APPDATA%\PIME, %LOCALAPPDATA%\PIME)
# and a PIME installer left in %TEMP% by the old setup.
#
# PIME's own uninstaller is NOT used: in silent mode it may restart the computer without asking.
# Files still loaded by open apps are deleted when Windows next starts; no restart is needed now.
# Dot-source it, then call Remove-Pime (administrator rights needed). Returns $true if some files
# are left for the next start.

function Find-Pime {
    $dir = (Get-ItemProperty "HKLM:\SOFTWARE\PIME" -ErrorAction SilentlyContinue)."(default)"
    if (-not $dir) { $dir = Join-Path ${env:ProgramFiles(x86)} "PIME" }
    if (Test-Path (Join-Path $dir "PIMELauncher.exe")) { return $dir }
    return $null
}

function Get-PimeOthers($pime) {
    # input methods in PIME besides ours and 新酷音 (which came with it): someone may use them
    $dir = Join-Path $pime "python\input_methods"
    @(Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin @("smart_zhuyin", "chewing", "__pycache__") -and (Test-Path (Join-Path $_.FullName "ime.json")) } |
        ForEach-Object {
            try { ((Get-Content (Join-Path $_.FullName "ime.json") -Raw -Encoding UTF8) | ConvertFrom-Json).name } catch { $_.Name }
        })
}

function Add-PimeNative {
    Add-Type -Namespace DualityIMEPime -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool MoveFileEx(string existing, string newName, int flags);
[DllImport("input.dll", CharSet = CharSet.Unicode)] public static extern bool InstallLayoutOrTip(string tip, uint flags);
'@ -ErrorAction SilentlyContinue
}

# deletes a folder; files still loaded by open apps go when Windows next starts ($script:pimeLater)
function Remove-PimeFolder($path) {
    if (-not (Test-Path $path)) { return }
    try { Remove-Item -Recurse -Force $path -ErrorAction Stop; return } catch { }
    Get-ChildItem $path -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object {
        try { Remove-Item -Force $_.FullName -ErrorAction Stop }
        catch { [void][DualityIMEPime.Native]::MoveFileEx($_.FullName, [NullString]::Value, 4); $script:pimeLater = $true }
    }
    Get-ChildItem $path -Recurse -Force -Directory -ErrorAction SilentlyContinue | Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { try { Remove-Item -Force -Recurse $_.FullName -ErrorAction Stop } catch { [void][DualityIMEPime.Native]::MoveFileEx($_.FullName, [NullString]::Value, 4) } }
    try { Remove-Item -Force -Recurse $path -ErrorAction Stop } catch { [void][DualityIMEPime.Native]::MoveFileEx($path, [NullString]::Value, 4); $script:pimeLater = $true }
}

# what an earlier removal could not delete and Windows did not either (PIME itself already gone:
# no launcher, no registration). Returns $true if some files are left for the next start.
function Remove-PimeLeftovers {
    $dir = Join-Path ${env:ProgramFiles(x86)} "PIME"
    if (-not (Test-Path $dir) -or (Find-Pime)) { return $false }
    if (Test-Path "HKLM:\SOFTWARE\PIME") { return $false }
    if (Test-Path "Registry::HKEY_CLASSES_ROOT\CLSID\{35F67E9D-A54D-4177-9697-8B0AB71A9E04}") { return $false }
    Add-PimeNative
    $script:pimeLater = $false
    Remove-PimeFolder $dir
    return $script:pimeLater
}

function Remove-Pime($pime) {
    Add-PimeNative
    $script:pimeLater = $false
    $PimeClsid = "{35F67E9D-A54D-4177-9697-8B0AB71A9E04}"

    # its input methods leave this user's keyboard list, one at a time (other languages stay)
    foreach ($tip in @((Get-WinUserLanguageList).InputMethodTips)) {
        if ($tip -like "*$PimeClsid*") { [void][DualityIMEPime.Native]::InstallLayoutOrTip($tip, 1) }
    }
    Get-Process PIMELauncher, python, pythonw, hotkey, lookup -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($pime, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force
    Start-Sleep -Milliseconds 500
    foreach ($job in @(@("x64\PIMETextService.dll", "System32"), @("arm64\PIMETextService.dll", "System32"),
                       @("x86\PIMETextService.dll", "SysWOW64"))) {
        $dll = Join-Path $pime $job[0]
        $exe = "$env:SystemRoot\$($job[1])\regsvr32.exe"
        if ((Test-Path $dll) -and (Test-Path $exe)) { Start-Process $exe "/u /s `"$dll`"" -Wait }
    }
    foreach ($view in @("HKLM:\SOFTWARE", "HKLM:\SOFTWARE\WOW6432Node")) {
        Remove-ItemProperty "$view\Microsoft\Windows\CurrentVersion\Run" -Name PIMELauncher -ErrorAction SilentlyContinue
        Remove-Item "$view\Microsoft\Windows\CurrentVersion\Uninstall\PIME" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item "$view\PIME" -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Start menu folder: the one holding a shortcut to PIME's uninstaller
    $shell = New-Object -ComObject WScript.Shell
    foreach ($menu in @([Environment]::GetFolderPath("CommonPrograms"), [Environment]::GetFolderPath("Programs"))) {
        Get-ChildItem $menu -Directory -ErrorAction SilentlyContinue | Where-Object {
            @(Get-ChildItem $_.FullName -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object {
                $shell.CreateShortcut($_.FullName).TargetPath -like "$pime*" }).Count -gt 0
        } | ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Remove-PimeFolder $pime
    Remove-PimeFolder (Join-Path $env:APPDATA "PIME")
    Remove-PimeFolder (Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "PIME")
    Get-ChildItem $env:TEMP -Filter "PIME-*-setup.exe" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    return $script:pimeLater
}
