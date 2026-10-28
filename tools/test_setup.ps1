# Tests the installer's logic (installer\src\Setup.cs, tools\setup_test.cs) in a temporary folder.
# Nothing is installed: the setup.exe it builds carries a fake payload and is only run with
# --list-payload and a --expect-version that does not match (it stops before touching anything).
#   powershell -ExecutionPolicy Bypass -File tools\test_setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root "installer\src"
$csc = Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$fw = Split-Path $csc -Parent
$dir = Join-Path $env:TEMP "dualityime-setup-test"
New-Item -ItemType Directory -Force $dir | Out-Null
$refs = @("/reference:$fw\System.Web.Extensions.dll", "/reference:$fw\System.IO.Compression.dll",
          "/reference:$fw\System.IO.Compression.FileSystem.dll")
$code = @((Join-Path $src "Setup.cs"), (Join-Path $src "Migrate.cs"))

# a fake payload: VERSION 1.2.3 and one data file
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zipPath = Join-Path $dir "payload.zip"
$stream = [IO.File]::Open($zipPath, [IO.FileMode]::Create)
try {
    $zip = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($e in @(@("VERSION", "1.2.3`r`n"), @("data/a.txt", "a"))) {
            $w = New-Object IO.StreamWriter($zip.CreateEntry($e[0]).Open())
            $w.Write($e[1]); $w.Dispose()
        }
    } finally { $zip.Dispose() }
} finally { $stream.Dispose() }

$fake = Join-Path $dir "setup-fake.exe"
$bare = Join-Path $dir "uninstall-fake.exe"
$test = Join-Path $dir "setup_test.exe"
& $csc /nologo /codepage:65001 /target:exe "/out:$fake" "/win32manifest:$src\setup.manifest" "/resource:$zipPath,payload.zip" @refs @code
if ($LASTEXITCODE -ne 0) { Write-Host "setup.exe build failed"; exit 1 }
& $csc /nologo /codepage:65001 /target:exe "/out:$bare" "/win32manifest:$src\setup.manifest" @refs @code
if ($LASTEXITCODE -ne 0) { Write-Host "uninstall.exe build failed"; exit 1 }
& $csc /nologo /codepage:65001 /target:exe "/out:$test" /main:SetupTest @refs @code (Join-Path $PSScriptRoot "setup_test.cs")
if ($LASTEXITCODE -ne 0) { Write-Host "setup_test.exe build failed"; exit 1 }
[Console]::OutputEncoding = [Text.Encoding]::UTF8
& $test $fake $bare
$result = $LASTEXITCODE

# the installer's scripts parse (they only run on a real install, uninstall or update)
foreach ($f in Get-ChildItem (Join-Path $root "installer") -Filter *.ps1) {
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errors)
    if ($errors.Count) { Write-Host "FAIL  $($f.Name) does not parse: $($errors[0].Message)"; $result = 1 }
    else { Write-Host "ok    $($f.Name) parses" }
}

# language.ps1's Preload renumbering, on a stand-in key (never the real Keyboard Layout\Preload)
. (Join-Path $root "installer\language.ps1")
$testKey = "HKCU:\Software\DualityIMETest"
$fakePreload = "$testKey\Preload"
function Preload-Is($values) {
    $p = Get-ItemProperty $fakePreload
    $got = @(); $n = 1
    while ($null -ne $p."$n") { $got += $p."$n"; $n++ }
    $count = @($p.PSObject.Properties | Where-Object { $_.Name -match '^\d+$' }).Count
    return @((($got -join ",") -eq ($values -join ",") -and $count -eq $got.Count), ($got -join ","))
}
$cases = @(
    @("c04 after the user's", @("00000404", "00000409", "00000411", "00000c04"), @("00000404", "00000409", "00000411")),
    @("c04 in the middle", @("00000404", "00000c04", "00000409"), @("00000404", "00000409")),
    @("other layouts with 0c04 inside stay", @("00000404", "0c040409"), @("00000404", "0c040409")),
    @("no c04: unchanged", @("00000404", "00000409"), @("00000404", "00000409")),
    @("only c04: unchanged", @("00000c04"), @("00000c04")))
try {
    foreach ($c in $cases) {
        if (Test-Path $testKey) { Remove-Item $testKey -Recurse -Force }
        New-Item $fakePreload -Force | Out-Null
        for ($i = 0; $i -lt $c[1].Count; $i++) { New-ItemProperty $fakePreload -Name "$($i + 1)" -Value $c[1][$i] | Out-Null }
        Remove-SzPreloadHK $fakePreload
        $r = Preload-Is $c[2]
        if ($r[0]) { Write-Host "ok    Preload: $($c[0])" } else { Write-Host "FAIL  Preload: $($c[0]) ($($r[1]))"; $result = 1 }
    }
} finally {
    if (Test-Path $testKey) { Remove-Item $testKey -Recurse -Force }
}
exit $result
