# Every end-to-end case file in cpp\tools, each through cpp\tools\e2e.ps1: the release gate
# (tools\release.ps1 -Phase publish refuses without the record this writes).
#   powershell -ExecutionPolicy Bypass -File tools\e2e_all.ps1 [-List]
# A case file is a cpp\tools\*.txt with case lines ("keys => text"); the options it needs for e2e.ps1
# are on a line "# e2e-args: -Config cpp\tools\x.json" (none: as it comes). A new case file runs
# without being listed anywhere; the probes for other input methods (no "=>") are left out.
# Before anything is typed, what types must be what is released (bin\, which the installer packs):
# the DualityIME64.dll Windows loads (the path registered for the CLSID: register.cmd points it at
# cpp\build\stage) and the engine e2e.ps1 starts (cpp\build\x64-Release, built next to bin\'s copy).
# 2026-11-03 the old installed DLL was tested.
# Every file passes: out\e2e-record.json {"head": <commit>, "dll": <SHA-256>, "engine": <SHA-256>}.
# It types into a window and stops the engine you type with: keep off the keyboard while it runs.
# -List: print the case files and their options, run nothing.
param([switch]$List)

# The case files under $Dir, by name: [{File, Args}]
function Get-E2eCaseFiles {
    param([string]$Dir)
    $out = @()
    foreach ($f in Get-ChildItem -LiteralPath $Dir -Filter *.txt | Sort-Object Name) {
        $lines = @(Get-Content -LiteralPath $f.FullName -Encoding UTF8)
        $cases = @($lines | Where-Object { -not $_.StartsWith("#") -and $_.Contains(" => ") })
        if ($cases.Count -eq 0) { continue }
        $opts = @()
        foreach ($l in $lines) {
            if ($l -match '^#\s*e2e-args:\s*(.*)$') { $opts = @($Matches[1].Trim() -split '\s+' | Where-Object { $_ }); break }
        }
        $out += [pscustomobject]@{ File = $f.FullName; Args = $opts }
    }
    return $out
}

# ---- everything below runs only when the script is run, not when a test dot-sources it ----
if ($MyInvocation.InvocationName -eq ".") { return }

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$cases = @(Get-E2eCaseFiles (Join-Path $root "cpp\tools"))
if ($List) {
    foreach ($c in $cases) { "$(Split-Path $c.File -Leaf) $($c.Args -join ' ')" }
    exit 0
}
if ($cases.Count -eq 0) { "STOP: no case files in cpp\tools"; exit 1 }
if (@(& git status --porcelain).Count -gt 0) { "STOP: the working tree is not clean: commit first (the record is of a commit)"; exit 1 }
$built = Join-Path $root "bin\DualityIME64.dll"
$builtEngine = Join-Path $root "bin\DualityIMEEngine.exe"
$testEngine = Join-Path $root "cpp\build\x64-Release\DualityIMEEngine.exe"
foreach ($f in $built, $builtEngine, $testEngine) {
    if (-not (Test-Path -LiteralPath $f)) { "STOP: $f is missing: run tools\release.ps1 -Phase prepare first"; exit 1 }
}
$clsid = "HKLM:\SOFTWARE\Classes\CLSID\{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}\InprocServer32"
$loaded = (Get-ItemProperty -LiteralPath $clsid -ErrorAction SilentlyContinue).'(default)'
if (-not $loaded -or -not (Test-Path -LiteralPath $loaded)) { "STOP: the input method is not registered: install out\DualityIME-<VERSION>-setup.exe first"; exit 1 }
$dll = (Get-FileHash -LiteralPath $built -Algorithm SHA256).Hash
if ((Get-FileHash -LiteralPath $loaded -Algorithm SHA256).Hash -ne $dll) {
    "STOP: the input method Windows loads ($loaded) is not the one built in bin\ (the test would type with another one):"
    "      install out\DualityIME-<VERSION>-setup.exe (it packs bin\), then run this again"
    exit 1
}
$engine = (Get-FileHash -LiteralPath $builtEngine -Algorithm SHA256).Hash
if ((Get-FileHash -LiteralPath $testEngine -Algorithm SHA256).Hash -ne $engine) {
    "STOP: cpp\build\x64-Release\DualityIMEEngine.exe (the engine the test starts) is not bin\'s: run cpp\publish.cmd, then this again"
    exit 1
}
$head = (& git rev-parse HEAD).Trim()
$record = Join-Path $root "out\e2e-record.json"
# (a record of an earlier run would pass publish even if this one fails)
if (Test-Path -LiteralPath $record) { [IO.File]::Delete($record) }

$e2e = Join-Path $root "cpp\tools\e2e.ps1"
$failed = @()
foreach ($c in $cases) {
    $name = Split-Path $c.File -Leaf
    "== $name $($c.Args -join ' ')"
    $lines = @(& powershell -NoProfile -ExecutionPolicy Bypass -File $e2e $c.File @($c.Args))
    $code = $LASTEXITCODE
    # (the passing lines are many: the rest, and the summary)
    $lines | Where-Object { $_ -notmatch '^ok\s' }
    # passed only with the summary of a run to the end and nothing failed: a run stopped half way (the
    # test window lost the focus) printed no summary, and e2e.ps1 had said 0 for it (2026-11-06)
    $summary = @($lines | Where-Object { $_ -match '^\d+ passed, \d+ failed$' })
    if ($code -ne 0 -or $summary.Count -ne 1 -or $summary[0] -notmatch '^[1-9]\d* passed, 0 failed$') { $failed += $name }
}
""
if ($failed.Count) { "FAILED: $($failed -join ', ') (no record written)"; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $root "out") | Out-Null
$json = '{"head": "' + $head + '", "dll": "' + $dll + '", "engine": "' + $engine + '"}'
[IO.File]::WriteAllText($record, $json + "`n", (New-Object Text.UTF8Encoding($false)))
"ALL PASSED: $($cases.Count) case files; record out\e2e-record.json ($($head.Substring(0, 7)))"
exit 0
