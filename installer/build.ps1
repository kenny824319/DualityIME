# 用 Windows 內建的 .NET Framework 編譯器建置，不需要另外安裝任何東西。
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Only lookup.exe]
#       開發用：bin\settings.exe（設定工具）與 bin\lookup.exe（框選查詢）
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Release
#       安裝檔：out\DualityIME-<VERSION>-setup.exe（一個檔案帶著所有要安裝的東西）與 out\payload\（內容）。
#       先執行 cpp\publish.cmd（把 DLL 與引擎放進 bin\）。
param([string[]]$Only, [switch]$Release)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $PSScriptRoot "src"
$csc = Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:SystemRoot "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
$fw = Split-Path $csc -Parent
$speech = Join-Path $env:SystemRoot "Microsoft.NET\assembly\GAC_MSIL\System.Speech\v4.0_4.0.0.0__31bf3856ad364e35\System.Speech.dll"
$icon = Join-Path $root "installer\app.ico"
$wv = Join-Path $root "third_party\webview2"
$webview = @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")
$setupRefs = @("/reference:$fw\System.Web.Extensions.dll", "/reference:$fw\System.IO.Compression.dll",
               "/reference:$fw\System.IO.Compression.FileSystem.dll")

function Build($out, $files, $csArgs) {
    & $csc /nologo /optimize+ /codepage:65001 "/win32icon:$icon" "/out:$out" @($csArgs) @($files | ForEach-Object { Join-Path $src $_ })
    if ($LASTEXITCODE -ne 0) { throw "編譯 $($files -join ", ") 失敗" }
    Write-Host "built $out"
}

$settingsArgs = @("/target:winexe", "/platform:x64", "/reference:$wv\Microsoft.Web.WebView2.Core.dll",
    "/reference:$wv\Microsoft.Web.WebView2.WinForms.dll", "/reference:$fw\System.Web.Extensions.dll",
    "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll")
# 框選查詢 (started by the IME): UI Automation reads the selection, System.Speech reads it aloud
$lookupArgs = @("/target:winexe", "/reference:$fw\WPF\UIAutomationClient.dll", "/reference:$fw\WPF\UIAutomationTypes.dll",
    "/reference:$fw\WPF\WindowsBase.dll", "/reference:$fw\System.Web.Extensions.dll", "/reference:$speech")

if (-not $Release) {
    # the settings app (a WebView2 window showing settings\ui) and lookup.exe, run from the project
    $bin = Join-Path $root "bin"
    New-Item -ItemType Directory -Force $bin | Out-Null
    if (-not $Only -or "settings.exe" -in $Only) {
        Build (Join-Path $bin "settings.exe") @("Settings.cs", "Migrate.cs", "Updater.cs") $settingsArgs
        foreach ($dll in $webview) { Copy-Item (Join-Path $wv $dll) $bin -Force }
    }
    if (-not $Only -or "lookup.exe" -in $Only) { Build (Join-Path $bin "lookup.exe") @("Lookup.cs") $lookupArgs }
    exit 0
}

# ---- the installer ------------------------------------------------------------------------------------
$version = (Get-Content (Join-Path $root "VERSION") | Select-Object -First 1).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION 不是 X.Y.Z：$version" }
$out = Join-Path $root "out"
$pay = Join-Path $out "payload"
New-Item -ItemType Directory -Force $pay | Out-Null

Build (Join-Path $pay "settings.exe") @("Settings.cs", "Migrate.cs", "Updater.cs") $settingsArgs
Build (Join-Path $pay "lookup.exe") @("Lookup.cs") $lookupArgs
# uninstall.exe: the installer's own code without a payload ("Settings > Apps" runs it with --uninstall)
Build (Join-Path $pay "uninstall.exe") @("Setup.cs", "Migrate.cs") (@("/target:exe", "/win32manifest:$src\setup.manifest") + $setupRefs)

# payload path -> source; only these go into the zip (nothing stale from an earlier build)
$files = [ordered]@{
    "DualityIME64.dll" = "bin\DualityIME64.dll"; "DualityIME32.dll" = "bin\DualityIME32.dll"
    "DualityIMEEngine.exe" = "bin\DualityIMEEngine.exe"
    "lookup.exe" = "out\payload\lookup.exe"; "settings.exe" = "out\payload\settings.exe"; "uninstall.exe" = "out\payload\uninstall.exe"
    "icon.ico" = "installer\app.ico"
    "settings-system.ps1" = "installer\settings-system.ps1"; "language.ps1" = "installer\language.ps1"
    "install.ps1" = "installer\install.ps1"; "user.ps1" = "installer\user.ps1"; "uninstall.ps1" = "installer\uninstall.ps1"
    "remove-pime.ps1" = "installer\remove-pime.ps1"
    "VERSION" = "VERSION"
}
foreach ($dll in $webview) { $files[$dll] = "third_party\webview2\$dll" }
# the engine's data: only what it reads (not the corpus the bigram is built from)
foreach ($f in @("lexicon.txt", "bigram.bin", "lm.bin", "english.txt", "english_tech.txt", "LICENSE-McBopomofo.txt")) { $files["data\$f"] = "data\$f" }
foreach ($f in Get-ChildItem (Join-Path $root "settings\ui") -File) { $files["ui\$($f.Name)"] = "settings\ui\$($f.Name)" }

foreach ($k in $files.Keys) {
    $from = Join-Path $root $files[$k]
    if (-not (Test-Path $from)) { throw "缺少 $($files[$k])$(if ($files[$k] -like 'bin\*') { '（先執行 cpp\publish.cmd）' })" }
    $to = Join-Path $pay $k
    if ([IO.Path]::GetFullPath($from) -ne [IO.Path]::GetFullPath($to)) {
        New-Item -ItemType Directory -Force (Split-Path $to -Parent) | Out-Null
        Copy-Item $from $to -Force
    }
}

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zipPath = Join-Path $out "payload.zip"
$stream = [IO.File]::Open($zipPath, [IO.FileMode]::Create)
try {
    $zip = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($k in $files.Keys) {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $pay $k), ($k -replace "\\", "/"),
                [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $zip.Dispose() }
} finally { $stream.Dispose() }

$setup = Join-Path $out "DualityIME-$version-setup.exe"
Build $setup @("Setup.cs", "Migrate.cs") (@("/target:exe", "/win32manifest:$src\setup.manifest", "/resource:$zipPath,payload.zip") + $setupRefs)

# the check: the installer carries every file above
$listed = @(& $setup --list-payload)
if ($LASTEXITCODE -ne 0) { throw "$setup --list-payload 失敗" }
$missing = @($files.Keys | Where-Object { $listed -notcontains $_ })
if ($missing.Count -or $listed.Count -ne $files.Count) { throw "安裝檔的內容不對：缺少 $($missing -join ', ')；共 $($listed.Count) 個檔案" }
Write-Host "$setup ($([int]((Get-Item $setup).Length / 1KB)) KB, $($listed.Count) files, version $version)"
