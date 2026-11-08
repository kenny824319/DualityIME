# Tests the pure functions of tools\release.ps1 (version bump, "did anything packaged change") and the
# command lines its -WhatIf mode prints. Touches no repo, no network, no GitHub.
#   powershell -ExecutionPolicy Bypass -File tools\test_release.ps1
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "release.ps1"
if (-not (Test-Path $script)) { Write-Host "FAIL: tools\release.ps1 is missing"; exit 1 }
. $script

$fail = 0
function Check($name, $actual, $expected) {
    if ($actual -ceq $expected) { Write-Host "ok   $name" }
    else { Write-Host "FAIL $name : got '$actual', expected '$expected'"; $script:fail++ }
}

Check "next develop 3.0.0" (Get-NextVersion -Current "3.0.0" -Channel develop) "3.0.1"
Check "next main 3.1.12" (Get-NextVersion -Current "3.1.12" -Channel main) "3.2.0"
Check "next develop 3.1.9" (Get-NextVersion -Current "3.1.9" -Channel develop) "3.1.10"
Check "next main 3.0.5" (Get-NextVersion -Current "3.0.5" -Channel main) "3.1.0"
Check "docs only" (Test-PackagedChange -Files @("docs/a.md", "README.md")) $false
Check "cpp" (Test-PackagedChange -Files @("cpp/x.cpp")) $true
Check "VERSION" (Test-PackagedChange -Files @("VERSION")) $true
Check "engine py" (Test-PackagedChange -Files @("engine/x.py")) $false
Check "installer" (Test-PackagedChange -Files @("docs/a.md", "installer/src/Setup.cs")) $true
Check "settings" (Test-PackagedChange -Files @("settings/ui/app.js")) $true
Check "data" (Test-PackagedChange -Files @("data/lexicon.bin")) $true
Check "prefix only" (Test-PackagedChange -Files @("cpptools/x", "VERSIONS")) $false
Check "empty" (Test-PackagedChange -Files @()) $false

# the publish command line (WhatIf prints it, runs nothing)
$cmd = Get-ReleaseArguments -Version "3.0.1" -Repo "kenny824319/DualityIME" -Sha "abc123" -NotesFile "n.txt" -Channel develop
Check "gh args develop" ($cmd -join " ") 'release create v3.0.1 out\DualityIME-3.0.1-setup.exe --repo kenny824319/DualityIME --target abc123 --title 兩儀輸入法 3.0.1 --notes-file n.txt --prerelease'
$cmd = Get-ReleaseArguments -Version "3.1.0" -Repo "kenny824319/DualityIME" -Sha "abc123" -NotesFile "n.txt" -Channel main
Check "gh args main" ($cmd -join " ") 'release create v3.1.0 out\DualityIME-3.1.0-setup.exe --repo kenny824319/DualityIME --target abc123 --title 兩儀輸入法 3.1.0 --notes-file n.txt'

# prepare version: an unreleased bump commit at HEAD is not bumped again
$bump = " * 版本 3.0.1"
Check "prepare fresh" (Get-PrepareVersion -Current "3.0.1" -Channel develop -TagExists $true -HeadSubject $bump) "3.0.2"
Check "prepare resume" (Get-PrepareVersion -Current "3.0.1" -Channel develop -TagExists $false -HeadSubject $bump) "3.0.1"
Check "prepare resume main" (Get-PrepareVersion -Current "3.1.0" -Channel main -TagExists $false -HeadSubject " * 版本 3.1.0") "3.1.0"
Check "prepare other head" (Get-PrepareVersion -Current "3.0.1" -Channel develop -TagExists $false -HeadSubject " * foo") "3.0.2"
Check "prepare older bump" (Get-PrepareVersion -Current "3.0.1" -Channel develop -TagExists $false -HeadSubject " * 版本 3.0.0") "3.0.2"

# prepare steps: the check, then the release binaries, then the installer that packs them
Check "prepare steps" ((Get-PrepareSteps) -join " | ") 'py -3 .claude/scripts/check.py --release | cmd /c cpp\publish.cmd | powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Release'

# check.py refuses to run on its own for a change that goes into the installer (prepare runs it with
# --release): its rule must be this script's Test-PackagedChange
$paths = @("docs/a.md", "README.md", "cpp/x.cpp", "VERSION", "engine/x.py", "installer/src/Setup.cs",
           "settings/ui/app.js", "data/lexicon.bin", "cpptools/x", "VERSIONS", "tools/release.ps1",
           ".claude/scripts/check.py", "cpp\engine\a.cpp")
$scripts = Join-Path (Split-Path $PSScriptRoot -Parent) ".claude\scripts"
$py = & py -3 -c "import sys; sys.path.insert(0, sys.argv[1]); import check; print('\n'.join(check.packaged(sys.argv[2:])))" $scripts @paths
$ps = @($paths | Where-Object { Test-PackagedChange -Files @($_) })
Check "check.py packaged = release.ps1" (@($py | Where-Object { $_ }) -join ",") ($ps -join ",")

# develop's prepare stops while origin/main has commits develop lacks (main's X.(Y+1).0 bump)
Check "develop: no origin/main" (Get-DevelopBehindMain -MainExists $false -MainIsAncestor $false) $null
Check "develop: main merged" (Get-DevelopBehindMain -MainExists $true -MainIsAncestor $true) $null
Check "develop: main ahead" ((Get-DevelopBehindMain -MainExists $true -MainIsAncestor $false) -like "main 有 develop 沒有的提交：先把 main 合回 develop*") $true

# after a main release: how develop catches up (printed, never run)
Check "sync develop fast-forward" ((Get-SyncDevelopCommands -DevelopIsAncestor $true) -join " | ") "git push origin main:develop"
Check "sync develop merge" ((Get-SyncDevelopCommands -DevelopIsAncestor $false) -join " | ") "git checkout develop | git merge main | git push origin develop | git checkout main"

# where the release notes start: main from the last stable release, not the nearest (pre-release) tag
Check "notes base develop" (Get-NotesBase -Channel develop -Nearest "v3.0.6" -LatestStable "v3.0.0") "v3.0.6"
Check "notes base main" (Get-NotesBase -Channel main -Nearest "v3.0.6" -LatestStable "v3.0.0") "v3.0.0"
Check "notes base main, no stable" (Get-NotesBase -Channel main -Nearest "v3.0.6" -LatestStable "") "v3.0.6"
Check "notes base none" (Get-NotesBase -Channel main -Nearest "" -LatestStable "") ""

# release notes
# (whole commit messages, newest first: one line per item, trailers and the version bump left out)
Check "notes no tag" (Get-ReleaseNotes -Version "3.0.0" -Messages @()) "兩儀輸入法 3.0.0"
Check "notes items" (Get-ReleaseNotes -Version "3.0.1" -Messages @(" * a`n + b`n`nCo-Authored-By: x <y>`nClaude-Session: z", " * c")) " * a`n + b`n * c"
Check "notes crlf" (Get-ReleaseNotes -Version "3.0.1" -Messages @(" * a`r`n * b`r`n")) " * a`n * b"
Check "notes no bump" (Get-ReleaseNotes -Version "3.0.2" -Messages @(" * 版本 3.0.2`n`nCo-Authored-By: x <y>", " * a")) " * a"
Check "notes only bump" (Get-ReleaseNotes -Version "3.0.2" -Messages @(" * 版本 3.0.2")) "兩儀輸入法 3.0.2"
Check "notes other format" (Get-ReleaseNotes -Version "3.0.1" -Messages @("fix the thing`n`nsome body text", "")) "fix the thing"
# the version commit: trailers as one "line|line" string (-File passes no arrays)
Check "version msg no trailers" (Get-VersionMessage -Version "3.1.2" -Trailers "") " * 版本 3.1.2`n"
Check "version msg trailers" (Get-VersionMessage -Version "3.1.2" -Trailers "Co-Authored-By: A <a@b> | Claude-Session: https://x/y") " * 版本 3.1.2`n`nCo-Authored-By: A <a@b>`nClaude-Session: https://x/y`n"

# only what users notice: items starting with tests, tools, the manual, review notes, the to-do list,
# handoffs, the push rules or the release notes are left out; 問題修正 and the rest stay
$internal = @(" + 新增實機測試：a", " * 開發工具：b", " + 新增開發工具：c", " * 使用說明改寫", " * 審查備忘：d",
    " * 待辦加上 e", " + 新增交接文件：f", " * 推送檢查：g", " * 更新README", " * 發正式版時，發版說明 h")
Check "notes internal only" (Get-ReleaseNotes -Version "3.1.2" -Messages @($internal -join "`n")) "兩儀輸入法 3.1.2"
$kept = @(" * 問題修正。使用說明寫錯了", " * 問題修正。實機測試才看得到的錯", " + 新增安裝檔帶入模型；使用說明與資料來源",
    " * 選字學習：x", " + 新增設定：y")
Check "notes user items" (Get-ReleaseNotes -Version "3.1.2" -Messages @((@($internal[0]) + $kept + @($internal[1])) -join "`n")) ($kept -join "`n")

# the program to run: on PATH, else the usual install path if it is there, else "" (not installed:
# publish stops; 3.1.3 was reported published with no gh on the computer)
Check "command on path" (Resolve-Command -OnPath "C:\x\gh.exe" -Usual "C:\nowhere\gh.exe") "C:\x\gh.exe"
Check "command usual path" (Resolve-Command -OnPath "" -Usual $script) $script
Check "command missing" (Resolve-Command -OnPath "" -Usual "C:\nowhere\gh.exe") ""
Check "command missing, no usual" (Resolve-Command -OnPath "" -Usual "") ""

# prepare's gate (commits newest first): the newest commit that changed the engine must be followed,
# itself included, by a commit that adds case lines or the user-approved line. 3.1.5: an earlier fix
# had added its cases, the 自由 / 現象 fix after it none - that must stop; a comment is no case.
function New-Commit($subject, $files, $added = 0, $message = " * x") {
    [pscustomobject]@{ Subject = $subject; Files = $files; Message = $message; AddedCases = $added }
}
$fix = New-Commit "8d5bf7b fix" @("engine/composer.py", "cpp/engine/composer.cpp", "tests/test_engine.py")
$earlier = New-Commit "b56e40f 817" @("engine/composer.py", "cpp/tools/autocorrect_cases.txt") 2
$versionCommit = New-Commit "0ecf7c2 version" @("VERSION")
$cases = New-Commit "3b8fcfa cases" @("cpp/tools/tsf_test.txt") 7
$comment = New-Commit "54c3315 e2e-args" @("cpp/tools/autocorrect_cases.txt", "tools/e2e_all.ps1") 0
# (release.ps1's typed parameters stay in this scope after the dot-source: a fixture named $version
# was turned into a string by [string]$Version)
Check "e2e cases: fixtures are commits" (@($fix, $earlier, $versionCommit, $cases, $comment | Where-Object { $null -eq $_.Subject }).Count) 0
Check "e2e cases: 3.1.5 as released" ((Get-MissingE2eCases -Commits @($versionCommit, $fix, $earlier)) -like "引擎改了（8d5bf7b fix）*cpp\tools*") $true
Check "e2e cases: cases after the fix" (Get-MissingE2eCases -Commits @($cases, $versionCommit, $fix, $earlier)) $null
Check "e2e cases: only a comment after" ((Get-MissingE2eCases -Commits @($comment, $versionCommit, $fix, $earlier)) -ne $null) $true
Check "e2e cases: in the same commit" (Get-MissingE2eCases -Commits @($versionCommit, $earlier)) $null
Check "e2e cases: approved none" (Get-MissingE2eCases -Commits @($versionCommit, (New-Commit "a speed" @("cpp/engine/lm.cpp") 0 " * 開發工具：a`n * 實機測試：不需要新案例（只改速度）"))) $null
Check "e2e cases: no engine" (Get-MissingE2eCases -Commits @((New-Commit "a tip" @("cpp/tip/ui.cpp", "docs/a.md")))) $null
Check "e2e cases: nothing" (Get-MissingE2eCases -Commits @()) $null
Check "e2e cases: backslashes" ((Get-MissingE2eCases -Commits @((New-Commit "a" @("cpp\engine\lm.cpp")))) -ne $null) $true
Check "e2e cases: cases before the fix" ((Get-MissingE2eCases -Commits @($fix, $cases)) -ne $null) $true
# added case lines in a case-file diff: not comments, not removed lines, not the file header
$diff = @("diff --git a/cpp/tools/x.txt b/cpp/tools/x.txt", "+++ b/cpp/tools/x.txt", "@@ -1 +1,4 @@",
          "+# e2e-args: -Config a.json", "+# keys => text in a comment", "-old{ENTER} => 舊", "+su3{ENTER} => 你", "+y4u.6{ENTER} => 自由")
Check "added case lines" (Get-AddedCaseLines $diff) 2
Check "added case lines: comments only" (Get-AddedCaseLines @("+# e2e-args: -User u.json")) 0
Check "added case lines: none" (Get-AddedCaseLines @()) 0

# publish's gate: the record tools\e2e_all.ps1 writes, of this commit and of the DLL and engine in bin\
$rec = '{"head": "abc123", "dll": "D11", "engine": "E11"}'
Check "e2e record ok" (Get-E2eRecordProblem -RecordText $rec -Head "abc123" -Dll "D11" -Engine "E11") $null
Check "e2e record missing" ((Get-E2eRecordProblem -RecordText "" -Head "abc123" -Dll "D11" -Engine "E11") -like "沒有端到端測試的紀錄*tools\e2e_all.ps1*") $true
Check "e2e record other commit" ((Get-E2eRecordProblem -RecordText $rec -Head "def456" -Dll "D11" -Engine "E11") -like "端到端測試是在另一個提交（abc123）跑的*") $true
Check "e2e record other dll" ((Get-E2eRecordProblem -RecordText $rec -Head "abc123" -Dll "D22" -Engine "E11") -like "*DLL 不是這次建置的*") $true
Check "e2e record other engine" ((Get-E2eRecordProblem -RecordText $rec -Head "abc123" -Dll "D11" -Engine "E22") -like "*引擎不是這次建置的*") $true
Check "e2e record no engine in it" ((Get-E2eRecordProblem -RecordText '{"head": "abc123", "dll": "D11"}' -Head "abc123" -Dll "D11" -Engine "E11") -ne $null) $true
Check "e2e record nothing built" ((Get-E2eRecordProblem -RecordText $rec -Head "abc123" -Dll "" -Engine "") -ne $null) $true
Check "e2e record garbage" ((Get-E2eRecordProblem -RecordText "not json" -Head "abc123" -Dll "D11" -Engine "E11") -like "*讀不懂*") $true

# every case file is found, with its options; probes without "=>" are not case files
. (Join-Path $PSScriptRoot "e2e_all.ps1")
$fixture = Join-Path $env:TEMP "dualityime-e2e-cases-test"
New-Item -ItemType Directory -Force $fixture | Out-Null
[IO.File]::WriteAllText((Join-Path $fixture "b_cases.txt"), "# x`n# e2e-args: -Config cpp\tools\b.json`nsu3{ENTER} => 你`n")
[IO.File]::WriteAllText((Join-Path $fixture "a_plain.txt"), "# y`nsu3{ENTER} => 你`n")
[IO.File]::WriteAllText((Join-Path $fixture "c_probe.txt"), "# a probe`nsu3{ENTER}`n# not => a case`n")
$found = @(Get-E2eCaseFiles $fixture)
Check "case files" (($found | ForEach-Object { "$(Split-Path $_.File -Leaf):$($_.Args -join ' ')" }) -join " | ") "a_plain.txt: | b_cases.txt:-Config cpp\tools\b.json"
# (in the repo: the default cases are found, the probes for other input methods are not)
$repoCases = @(Get-E2eCaseFiles (Join-Path (Split-Path $PSScriptRoot -Parent) "cpp\tools") | ForEach-Object { Split-Path $_.File -Leaf })
Check "repo case files" (($repoCases -contains "tsf_test.txt") -and -not ($repoCases -contains "mixed_cases.txt") -and -not ($repoCases -contains "compare_probe.txt")) $true
Remove-Item $fixture -Recurse -Force -ErrorAction SilentlyContinue

if ($fail -gt 0) { Write-Host "$fail FAILED"; exit 1 }
Write-Host "ALL PASSED"
