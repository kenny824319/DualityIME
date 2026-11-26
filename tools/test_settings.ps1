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

    # 框選查詢's Japanese (#36 plan 7): Lookup.cs from the source, with the Japanese data it reads beside it
    $lk = Join-Path $env:TEMP "dualityime-lookup-test"
    New-Item -ItemType Directory -Force (Join-Path $lk "data") | Out-Null
    foreach ($f in @("lexicon.txt", "ja_dict.bin", "ja_conn.bin", "ja_romaji.tsv")) { Copy-Item (Join-Path $root "data\$f") (Join-Path $lk "data\$f") -Force }
    $speech = Join-Path $env:SystemRoot "Microsoft.NET\assembly\GAC_MSIL\System.Speech\v4.0_4.0.0.0__31bf3856ad364e35\System.Speech.dll"  # (as installer\build.ps1)
    & $csc /nologo /codepage:65001 /target:winexe "/out:$lk\lookup.exe" "/reference:$fw\System.Web.Extensions.dll" "/reference:$speech" `
        (Join-Path $root "installer\src\Lookup.cs")
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  lookup.exe build"; $code++ }
    else {
        & $csc /nologo /codepage:65001 "/out:$lk\lookup_test.exe" "/reference:$lk\lookup.exe" (Join-Path $PSScriptRoot "lookup_test.cs")
        if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  lookup_test build"; $code++ }
        else { & (Join-Path $lk "lookup_test.exe"); $code += $LASTEXITCODE }
    }

    # the pages, in a headless Edge with the page's stand-in for settings.exe: each draws its title.
    # Some machines' Edge prints nothing for --dump-dom, not even a one-line page (2026-11-15, 2026-11-26): every
    # page then "failed", and session after session chased a page
    # that was fine (the user 2026-11-26, /collab:correct). A one-line page is asked first: nothing back is the machine,
    # said so, and each page is drawn to a screenshot instead - a session looks at the ones its change touched (the
    # user: "你還是要用截圖去驗證改動"). Its own profile folder: an Edge already open would take the request otherwise.
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($Source) { Write-Host "skip  pages (-Source)" }
    elseif (-not $edge) { Write-Host "skip  pages (no Edge)" }
    else {
        $url = "file:///" + ((Join-Path $root "settings\ui\index.html") -replace "\\", "/")
        $pages = [ordered]@{ general = "一般"; typing = "打字"; complete = "補字與記憶"; dict = "個人詞庫"; japanese = "日文"; look = "外觀"; help = "使用說明"; faq = "常見問題"; about = "關於" }
        $work = Join-Path $env:TEMP "dualityime-pages"
        New-Item -ItemType Directory -Force $work | Out-Null
        $profile = Join-Path $work "edge-profile"
        $probe = Join-Path $work "probe.html"
        Set-Content -Path $probe -Value "<!doctype html><html><body><h1>probe</h1></body></html>" -Encoding ASCII
        $probeDom = & $edge --headless=new --disable-gpu "--user-data-dir=$profile" --dump-dom ("file:///" + ($probe -replace "\\", "/")) 2>$null | Out-String
        if ($probeDom -match "<h1>probe</h1>") {
            foreach ($p in $pages.Keys) {
                $dom = & $edge --headless=new --disable-gpu "--user-data-dir=$profile" --virtual-time-budget=3000 --dump-dom "$url#$p" 2>$null | Out-String
                $ok = $dom -match "<h1>$($pages[$p])</h1>"
                Write-Host "$(if ($ok) { 'ok  ' } else { 'FAIL' })  page $p"
                if (-not $ok) { $code++ }
            }
        } else {
            Write-Host "skip  pages: this Edge's --dump-dom prints nothing, even for a one-line page - the machine, not the pages"
            $shots = Join-Path $work "shots"
            New-Item -ItemType Directory -Force $shots | Out-Null
            foreach ($p in $pages.Keys) {
                $shot = Join-Path $shots "$p.png"
                Remove-Item $shot -ErrorAction SilentlyContinue
                Start-Process -FilePath $edge -Wait -WindowStyle Hidden -ArgumentList @("--headless=new", "--disable-gpu",
                    "--user-data-dir=$shots\edge-profile", "--virtual-time-budget=3000", "--window-size=1100,900", "--screenshot=$shot", "$url#$p")
                # (a profile of its own: the probe's Edge may still hold the other one, and the first shot came out empty)
                $ok = (Test-Path $shot) -and (Get-Item $shot).Length -gt 0
                Write-Host "$(if ($ok) { 'shot' } else { 'FAIL' })  page $p$(if ($ok) { ": $shot" } else { ' (no screenshot either)' })"
                if (-not $ok) { $code++ }
            }
            Write-Host "look  the screenshots above: open the pages a change touched and check them before saying they work"
        }
    }
} finally {
    Remove-Item $test -ErrorAction SilentlyContinue
}
exit $code
