# Tests the settings app (bin\settings.exe): what it writes (tools\settings_test.cs, in a temporary
# folder: the real settings are not touched) and that its pages render without errors.
#   powershell -ExecutionPolicy Bypass -File tools\test_settings.ps1 [-Source]
#   -Source: test installer\src instead of bin\settings.exe (compiled into %TEMP%\dualityime-settings-test);
#            the page check is skipped (check.py runs it this way)
param([switch]$Source)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$csc = Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$fw = Split-Path $csc -Parent
$bin = Join-Path $root "bin"
if ($Source) {
    # settings.exe from the source, with what it needs next to it (the WebView2 DLLs, data\lexicon.txt)
    $bin = Join-Path $env:TEMP "dualityime-settings-test"
    New-Item -ItemType Directory -Force (Join-Path $bin "data") | Out-Null
    $wv = Join-Path $root "third_party\webview2"
    $src = @("Settings.cs", "Migrate.cs", "Updater.cs") | ForEach-Object { Join-Path $root "installer\src\$_" } | Where-Object { Test-Path $_ }
    & $csc /nologo /codepage:65001 /target:winexe /platform:x64 "/out:$bin\settings.exe" `
        "/reference:$wv\Microsoft.Web.WebView2.Core.dll" "/reference:$wv\Microsoft.Web.WebView2.WinForms.dll" `
        "/reference:$fw\System.Web.Extensions.dll" "/reference:System.Windows.Forms.dll" "/reference:System.Drawing.dll" @($src)
    if ($LASTEXITCODE -ne 0) { Write-Host "settings.exe build failed"; exit 1 }
    foreach ($dll in @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")) {
        Copy-Item (Join-Path $wv $dll) $bin -Force
    }
    Copy-Item (Join-Path $root "data\lexicon.txt") (Join-Path $bin "data\lexicon.txt") -Force
}
# (built next to settings.exe: it finds data\lexicon.txt from there, like the real one)
$test = Join-Path $bin "settings_test.exe"
try {
    & $csc /nologo /codepage:65001 /platform:x64 "/out:$test" "/reference:$bin\settings.exe" `
        "/reference:$fw\System.Web.Extensions.dll" (Join-Path $PSScriptRoot "settings_test.cs")
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    & $test
    $code = $LASTEXITCODE

    # the pages, in a headless Edge with the page's stand-in for settings.exe: each draws its title
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($Source) { Write-Host "skip  pages (-Source)" }
    elseif (-not $edge) { Write-Host "skip  pages (no Edge)" }
    else {
        $url = "file:///" + ((Join-Path $root "settings\ui\index.html") -replace "\\", "/")
        $pages = [ordered]@{ general = "一般"; typing = "打字"; complete = "補字與記憶"; dict = "個人詞庫"; look = "外觀"; help = "使用說明"; faq = "常見問題"; about = "關於" }
        foreach ($p in $pages.Keys) {
            $dom = & $edge --headless=new --disable-gpu --virtual-time-budget=3000 --dump-dom "$url#$p" 2>$null | Out-String
            $ok = $dom -match "<h1>$($pages[$p])</h1>"
            Write-Host "$(if ($ok) { 'ok  ' } else { 'FAIL' })  page $p"
            if (-not $ok) { $code++ }
        }
    }
} finally {
    Remove-Item $test -ErrorAction SilentlyContinue
}
exit $code
