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
Check "prepare steps" ((Get-PrepareSteps) -join " | ") 'py -3 .claude/scripts/check.py | cmd /c cpp\publish.cmd | powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Release'

# develop's prepare stops while origin/main has commits develop lacks (main's X.(Y+1).0 bump)
Check "develop: no origin/main" (Get-DevelopBehindMain -MainExists $false -MainIsAncestor $false) $null
Check "develop: main merged" (Get-DevelopBehindMain -MainExists $true -MainIsAncestor $true) $null
Check "develop: main ahead" ((Get-DevelopBehindMain -MainExists $true -MainIsAncestor $false) -like "main 有 develop 沒有的提交：先把 main 合回 develop*") $true

# after a main release: how develop catches up (printed, never run)
Check "sync develop fast-forward" ((Get-SyncDevelopCommands -DevelopIsAncestor $true) -join " | ") "git push origin main:develop"
Check "sync develop merge" ((Get-SyncDevelopCommands -DevelopIsAncestor $false) -join " | ") "git checkout develop | git merge main | git push origin develop | git checkout main"

# release notes
Check "notes no tag" (Get-ReleaseNotes -Version "3.0.0" -Subjects @()) "兩儀輸入法 3.0.0"
Check "notes subjects" (Get-ReleaseNotes -Version "3.0.1" -Subjects @(" * a", " + b")) " * a`n + b"

if ($fail -gt 0) { Write-Host "$fail FAILED"; exit 1 }
Write-Host "ALL PASSED"
