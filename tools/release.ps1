# Release script for 兩儀輸入法 (DualityIME).
#   powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Channel develop|main -Phase prepare|publish
#                [-Repo kenny824319/DualityIME] [-Version X.Y.Z] [-WhatIf]
# prepare: bump VERSION (develop X.Y.(Z+1), main X.(Y+1).0), commit, run the full check, build the
#          release binaries (cpp\publish.cmd) and the installer, then print the commit to push. develop with no packaged change: nothing happens.
#          develop stops first when origin/main has commits develop lacks (main's X.(Y+1).0 bump): merge main
#          back into develop, else its pre-releases would come out below the stable release.
# publish: after the commit is pushed and HEAD equals origin/<branch>, create the GitHub Release
#          (develop = prerelease). The tag is created by GitHub (--target), never locally. main then
#          prints the commands that bring develop up to main (it does not run them).
# -Version (publish only): release that version as it is, no bump (the new repo's first v3.0.0).
# -WhatIf: print the commands that would change something, run none of them.
# This script never pushes, never creates a local tag and never deletes a tag or a Release.
param(
    [ValidateSet("develop", "main")][string]$Channel,
    [ValidateSet("prepare", "publish")][string]$Phase,
    [string]$Repo = "kenny824319/DualityIME",
    [string]$Version,
    [switch]$WhatIf,
    [string[]]$Trailers = @(
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        "Claude-Session: https://claude.ai/code/session_01NCF4Pg85NZUud1WuxKmjkq")
)

function Get-NextVersion {
    param([Parameter(Mandatory)][string]$Current, [Parameter(Mandatory)][ValidateSet("develop", "main")][string]$Channel)
    if ($Current -notmatch '^(\d+)\.(\d+)\.(\d+)$') { throw "Not X.Y.Z: $Current" }
    $x = [int]$Matches[1]; $y = [int]$Matches[2]; $z = [int]$Matches[3]
    if ($Channel -eq "develop") { return "$x.$y.$($z + 1)" }
    return "$x.$($y + 1).0"
}

# True when any path ends up in the installer: cpp/, installer/, settings/, data/ or VERSION itself.
function Test-PackagedChange {
    param([string[]]$Files)
    foreach ($f in @($Files)) {
        $p = ($f -replace '\\', '/').Trim()
        if ($p -eq "VERSION" -or $p -like "cpp/*" -or $p -like "installer/*" -or $p -like "settings/*" -or $p -like "data/*") {
            return $true
        }
    }
    return $false
}

# develop's prepare: $null when it may go on, else why it stops. origin/main not there (a new repo): go on.
function Get-DevelopBehindMain {
    param([bool]$MainExists, [bool]$MainIsAncestor)
    if (-not $MainExists -or $MainIsAncestor) { return $null }
    return "main 有 develop 沒有的提交：先把 main 合回 develop（git merge origin/main），再重跑 -Phase prepare"
}

# After a main release: the commands that bring develop up to main. develop an ancestor of main: a
# fast-forward push; otherwise (develop has its own commits) a merge on develop, then its push.
function Get-SyncDevelopCommands {
    param([bool]$DevelopIsAncestor)
    if ($DevelopIsAncestor) { return @("git push origin main:develop") }
    return @("git checkout develop", "git merge main", "git push origin develop", "git checkout main")
}

# The version a prepare run releases. When v<Current> has no tag yet and HEAD is this script's own
# " * 版本 <Current>" commit, that bump is unreleased (a check/build/publish failed after it): keep it.
function Get-PrepareVersion {
    param([string]$Current, [string]$Channel, [bool]$TagExists, [string]$HeadSubject)
    if (-not $TagExists -and $HeadSubject -ceq " * 版本 $Current") { return $Current }
    return Get-NextVersion -Current $Current -Channel $Channel
}

# What -Phase prepare runs after the version commit, in this order: the full check (it records the tree
# that is pushed), the release binaries into bin\, then the installer that packs them (spec 9.2 step 1).
function Get-PrepareSteps {
    return @("py -3 .claude/scripts/check.py",
             "cmd /c cpp\publish.cmd",
             "powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Release")
}

function Get-ReleaseArguments {
    param([string]$Version, [string]$Repo, [string]$Sha, [string]$NotesFile, [string]$Channel)
    $a = @("release", "create", "v$Version", "out\DualityIME-$Version-setup.exe", "--repo", $Repo, "--target", $Sha,
           "--title", "兩儀輸入法 $Version", "--notes-file", $NotesFile)
    if ($Channel -eq "develop") { $a += "--prerelease" }
    return $a
}

function Get-ReleaseNotes {
    param([string]$Version, [string[]]$Subjects)
    $s = @($Subjects | Where-Object { $_ -and $_.Trim() })
    if ($s.Count -eq 0) { return "兩儀輸入法 $Version" }
    return ($s -join "`n")
}

# ---- everything below runs only when the script is run, not when a test dot-sources it ----
if ($MyInvocation.InvocationName -eq ".") { return }

# git prints UTF-8; read it as such (the subject comparison of Get-PrepareVersion has Chinese in it)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }

function Stop-Release($msg) { Write-Host "STOP: $msg"; exit 1 }

# Runs git, returns its output lines; stops the release on a non-zero exit.
function Invoke-Git {
    $out = & git @args
    if ($LASTEXITCODE -ne 0) { Stop-Release "git $($args -join ' ') failed (exit $LASTEXITCODE)" }
    return @($out)
}

# A command that changes something: printed under -WhatIf, run otherwise.
function Invoke-Change([string]$Exe, [string[]]$Arguments, [string]$FailMessage) {
    $line = "$Exe $($Arguments -join ' ')"
    if ($WhatIf) { Write-Host "[WhatIf] $line"; return }
    Write-Host "> $line"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        if ($FailMessage) { Stop-Release "$line failed (exit $LASTEXITCODE). $FailMessage" }
        Stop-Release "$line failed (exit $LASTEXITCODE)"
    }
}

if (-not $Channel -or -not $Phase) { Stop-Release "give -Channel develop|main and -Phase prepare|publish" }
if ($Version -and $Phase -ne "publish") { Stop-Release "-Version works with -Phase publish only" }
if ($Version -and $Version -notmatch '^\d+\.\d+\.\d+$') { Stop-Release "-Version is not X.Y.Z: $Version" }

$versionFile = Join-Path $root "VERSION"
$current = (Get-Content $versionFile | Select-Object -First 1).Trim()
$branch = @(Invoke-Git rev-parse --abbrev-ref HEAD)[0]
if ($branch -ne $Channel) { Stop-Release "the current branch is '$branch'; -Channel $Channel needs '$Channel'" }

function Test-TagExists([string]$Tag) {
    & git rev-parse -q --verify "refs/tags/$Tag" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-RefExists([string]$Ref) {
    & git rev-parse -q --verify $Ref | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# git merge-base --is-ancestor: 0 yes, 1 no, anything else an error (stops)
function Test-IsAncestor([string]$Old, [string]$New) {
    & git merge-base --is-ancestor $Old $New
    if ($LASTEXITCODE -eq 0) { return $true }
    if ($LASTEXITCODE -eq 1) { return $false }
    Stop-Release "git merge-base --is-ancestor $Old $New failed (exit $LASTEXITCODE)"
}

if ($Phase -eq "prepare") {
    if (@(Invoke-Git status --porcelain).Count -gt 0) {
        if ($WhatIf) { Write-Host "[WhatIf] note: the working tree is not clean (a real run would stop here)" }
        else { Stop-Release "the working tree is not clean; commit or stash first" }
    }
    if ($WhatIf) { Write-Host "[WhatIf] git fetch --tags" } else { Invoke-Git fetch --tags | Out-Null }
    if ($Channel -eq "develop") {
        $mainExists = Test-RefExists "refs/remotes/origin/main"
        $behind = Get-DevelopBehindMain -MainExists $mainExists -MainIsAncestor ($mainExists -and (Test-IsAncestor "origin/main" "HEAD"))
        if ($behind) {
            if ($WhatIf) { Write-Host "[WhatIf] note: $behind (a real run would stop here)" }
            else { Stop-Release $behind }
        }
    }
    $tagExists = Test-TagExists "v$current"
    $headSubject = @(Invoke-Git log -1 --format=%s)[0]
    $next = Get-PrepareVersion -Current $current -Channel $Channel -TagExists $tagExists -HeadSubject $headSubject
    $resume = ($next -eq $current)
    if (-not $resume) {
        if ($tagExists) { $changed = @(Invoke-Git -c core.quotepath=false diff --name-only "v$current..HEAD") }
        else { $changed = @(Invoke-Git -c core.quotepath=false ls-files) }   # no tag yet: everything changed
        if ($Channel -eq "develop" -and -not (Test-PackagedChange -Files $changed)) {
            Write-Host "沒有程式變動，不發版"
            exit 0
        }
    }
    $headSha = @(Invoke-Git rev-parse --short HEAD)[0]
    $failMsg = "VERSION $next 已提交（$headSha）；修好後重跑 -Phase prepare（不會再加版號），或自行決定是否還原這個提交"
    $msgFile = Join-Path $env:TEMP "dualityime-release-msg.txt"
    $msg = " * 版本 $next`n`n" + ($Trailers -join "`n") + "`n"
    if ($WhatIf) {
        if ($resume) { Write-Host "[WhatIf] HEAD is the unreleased version commit of ${next}: no second bump" }
        else {
            Write-Host "[WhatIf] write VERSION = $next"
            Write-Host "[WhatIf] git add VERSION"
            Write-Host "[WhatIf] git commit -F <message file>, message:"
            Write-Host ($msg.TrimEnd() -replace '(?m)^', '           ')
        }
        foreach ($step in Get-PrepareSteps) { Write-Host "[WhatIf] $step" }
        Write-Host "[WhatIf] then print: git push origin $branch"
        exit 0
    }
    if ($resume) { Write-Host "HEAD is the unreleased version commit of ${next}: not bumping again, re-running check and build" }
    else {
        $raw = [IO.File]::ReadAllText($versionFile)
        $eol = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
        [IO.File]::WriteAllText($versionFile, "$next$eol", (New-Object Text.UTF8Encoding($false)))
        [IO.File]::WriteAllText($msgFile, $msg, (New-Object Text.UTF8Encoding($false)))
        Invoke-Change "git" @("add", "VERSION")
        Invoke-Change "git" @("commit", "-F", $msgFile)
        $headSha = @(Invoke-Git rev-parse --short HEAD)[0]
        $failMsg = "VERSION $next 已提交（$headSha）；修好後重跑 -Phase prepare（不會再加版號），或自行決定是否還原這個提交"
    }
    # VERSION is committed before the check, so the check records the tree that will be pushed; then
    # cpp\publish.cmd builds the DLLs and the engine of this commit into bin\ before the installer packs bin\.
    foreach ($step in Get-PrepareSteps) {
        $parts = @($step -split ' ')
        Invoke-Change $parts[0] @($parts[1..($parts.Count - 1)]) $failMsg
    }
    Write-Host ""
    Write-Host "Version $next is committed, checked and built. Push it, then run -Phase publish:"
    Write-Host "  git push origin $branch"
    exit 0
}

# ---- publish ----
if ($WhatIf) { Write-Host "[WhatIf] git fetch --tags" } else { Invoke-Git fetch --tags | Out-Null }
$head = @(Invoke-Git rev-parse HEAD)[0]
$remote = & git rev-parse -q --verify "refs/remotes/origin/$branch"
if ($LASTEXITCODE -ne 0) { Stop-Release "origin/$branch does not exist; push the branch first" }
if ($head -ne $remote) {
    if ($WhatIf) { Write-Host "[WhatIf] note: HEAD ($head) is not origin/$branch ($remote); a real run would stop here" }
    else { Stop-Release "HEAD ($head) is not origin/$branch ($remote); push first" }
}
$ver = if ($Version) { $Version } else { $current }
if ($ver -notmatch '^\d+\.\d+\.\d+$') { Stop-Release "VERSION is not X.Y.Z: $ver" }
if ($Version -and $Version -ne $current) { Stop-Release "-Version $Version differs from the VERSION file ($current)" }
if (Test-TagExists "v$ver") { Stop-Release "tag v$ver already exists; this script never touches an existing tag or Release" }
$installer = Join-Path $root "out\DualityIME-$ver-setup.exe"
if (-not (Test-Path $installer)) {
    $hint = if ($Version) { "run installer\build.ps1 -Release" } else { "run -Phase prepare first" }
    if ($WhatIf) { Write-Host "[WhatIf] note: $installer is missing ($hint); a real run would stop here" }
    else { Stop-Release "$installer is missing; $hint" }
}

$prev = (& git describe --tags --abbrev=0 HEAD)
if ($LASTEXITCODE -eq 0 -and $prev) {
    $subjects = @(Invoke-Git log "$prev..HEAD" --format=%s)
} else { $subjects = @() }
$notes = Get-ReleaseNotes -Version $ver -Subjects $subjects
$notesFile = Join-Path $env:TEMP "dualityime-release-notes-$ver.txt"
[IO.File]::WriteAllText($notesFile, $notes + "`n", (New-Object Text.UTF8Encoding($false)))
if ($WhatIf) { Write-Host "[WhatIf] release notes ($notesFile):"; Write-Host ($notes -replace '(?m)^', '           ') }

$ghArgs = Get-ReleaseArguments -Version $ver -Repo $Repo -Sha $head -NotesFile $notesFile -Channel $Channel
Invoke-Change $gh $ghArgs
if (-not $WhatIf) { Write-Host "Release v$ver created on $Repo." }
if ($Channel -eq "main" -and (Test-RefExists "refs/remotes/origin/develop")) {
    # develop carries on from this release (its next pre-release is above it); this script never pushes
    Write-Host ""
    Write-Host "Bring develop up to main (this script runs none of these):"
    foreach ($line in Get-SyncDevelopCommands -DevelopIsAncestor (Test-IsAncestor "origin/develop" "HEAD")) { Write-Host "  $line" }
}
