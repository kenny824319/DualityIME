# Every end-to-end case file in cpp\tools, each through cpp\tools\e2e.ps1: the release gate
# (tools\release.ps1 -Phase publish refuses without the record this writes).
#   powershell -ExecutionPolicy Bypass -File tools\e2e_all.ps1 [-List]
# A case file is a cpp\tools\*.txt with case lines ("keys => text"); the options it needs for e2e.ps1
# are on a line "# e2e-args: -Config cpp\tools\x.json" (none: as it comes). A new case file runs
# without being listed anywhere; the probes for other input methods (no "=>") are left out.
# Before anything is typed, the installed DualityIME64.dll must be the one built into bin\ (what the
# installer packs): else the input method tested is not the one released (2026-11-03 the old DLL was).
# Every file passes: out\e2e-record.json {"head": <commit>, "dll": <SHA-256 of that DLL>}.
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
$installed = Join-Path $env:ProgramFiles "DualityIME\DualityIME64.dll"
if (-not (Test-Path -LiteralPath $built)) { "STOP: bin\DualityIME64.dll is missing: run tools\release.ps1 -Phase prepare first"; exit 1 }
if (-not (Test-Path -LiteralPath $installed)) { "STOP: the input method is not installed: install out\DualityIME-<VERSION>-setup.exe first"; exit 1 }
$dll = (Get-FileHash -LiteralPath $built -Algorithm SHA256).Hash
if ((Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash -ne $dll) {
    "STOP: the installed input method is not the one built in bin\ (the test would type with another one):"
    "      install out\DualityIME-<VERSION>-setup.exe (it packs bin\), then run this again"
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
    if ($code -ne 0) { $failed += $name }
}
""
if ($failed.Count) { "FAILED: $($failed -join ', ') (no record written)"; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $root "out") | Out-Null
$json = '{"head": "' + $head + '", "dll": "' + $dll + '"}'
[IO.File]::WriteAllText($record, $json + "`n", (New-Object Text.UTF8Encoding($false)))
"ALL PASSED: $($cases.Count) case files; record out\e2e-record.json ($($head.Substring(0, 7)))"
exit 0
