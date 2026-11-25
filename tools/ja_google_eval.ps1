# #36: types Japanese sentences through Google Japanese Input (Google nihongo nyuuryoku) and records what comes out,
# to compare our Japanese mode with it later. Nothing is installed: the Japanese keyboard must already be in the
# Windows language list (the user's computer: it is). It types into sz_tsftest's own test window: keep off the
# keyboard while it runs.
#   powershell -ExecutionPolicy Bypass -File tools\ja_google_eval.ps1 [-Sentences tools\ja_sentences_sample.tsv]
#       [-Out ja_google.json]
# Sentences: a UTF-8 file of lines "romaji<TAB>expected" (# a comment). Each is typed as romaji, one Space, one Enter
# (what an everyday user does: a sentence split into several segments is recorded as it comes, not fixed up).
# Out (UTF-8 JSON): {"ime": "<profile description>", "date": ..., "results": [{"romaji", "expected", "got", "same"}]}.
# The input method is turned on first (sz_tsftest --open) and each line starts from an empty box.
param(
    [string]$Sentences = "",
    [string]$Out = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Sentences) { $Sentences = Join-Path $root "tools\ja_sentences_sample.tsv" }
if (-not $Out) { $Out = Join-Path $env:TEMP "ja_google.json" }
$tester = Join-Path $root "cpp\build\x64-Release\sz_tsftest.exe"
if (-not (Test-Path $tester)) { "STOPPED: build first (cpp\build.cmd): $tester"; exit 1 }

# Google's TSF profile under Japanese (0x0411): the TIP whose description names Google
$tip = $null
foreach ($k in Get-ChildItem "HKLM:\SOFTWARE\Microsoft\CTF\TIP" -ErrorAction SilentlyContinue) {
    $lp = Join-Path $k.PSPath "LanguageProfile\0x00000411"
    if (-not (Test-Path $lp)) { continue }
    foreach ($p in Get-ChildItem $lp -ErrorAction SilentlyContinue) {
        $desc = (Get-ItemProperty $p.PSPath -ErrorAction SilentlyContinue).Description
        if ($desc -and $desc -match "Google") { $tip = @{ clsid = $k.PSChildName; profile = $p.PSChildName; desc = $desc } }
    }
}
if (-not $tip) { "STOPPED: Google Japanese Input's profile under 0x0411 was not found"; exit 1 }

$rows = @()
foreach ($line in Get-Content $Sentences -Encoding UTF8) {
    if (-not $line -or $line.StartsWith("#")) { continue }
    $parts = $line.Split("`t")
    if ($parts.Count -ge 2) { $rows += ,@($parts[0], $parts[1]) }
}
$dir = Join-Path $env:TEMP ("ja_google_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $dir | Out-Null
$script = Join-Path $dir "script.txt"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($script, [string[]]($rows | ForEach-Object { "$($_[0]){SPACE}{ENTER} => $($_[1])" }), $utf8)
$outTxt = Join-Path $dir "out.txt"
$tsfArgs = @("`"$script`"", "--tsf", "--tip", $tip.clsid, $tip.profile, "--lang", "0411", "--open")
$p = Start-Process $tester -ArgumentList $tsfArgs -PassThru -RedirectStandardOutput $outTxt
$null = $p.Handle
if (-not $p.WaitForExit([Math]::Max(120000, $rows.Count * 5000))) { "HUNG"; Stop-Process $p -Force }
$lines = @(Get-Content $outTxt -Encoding UTF8)
$results = @()
$i = 0
foreach ($l in $lines) {
    if ($l -match "^(ok  |FAIL)  (.*?) => (.*?)(   \(expected .*\))?$" -and $i -lt $rows.Count) {
        $results += [ordered]@{ romaji = $rows[$i][0]; expected = $rows[$i][1]; got = $Matches[3]; same = ($Matches[3] -eq $rows[$i][1]) }
        $i++
    }
}
$report = [ordered]@{ ime = $tip.desc; date = (Get-Date -Format "yyyy-MM-dd HH:mm"); results = $results }
[System.IO.File]::WriteAllText($Out, ($report | ConvertTo-Json -Depth 4), $utf8)
$same = @($results | Where-Object { $_.same }).Count
"$same / $($rows.Count) same as expected ($($results.Count) typed); written to $Out"
$lines | Where-Object { $_ -notmatch "^(ok  |FAIL)  " }
