# Release script for 兩儀輸入法 (DualityIME).
#   powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Channel develop|main -Phase prepare|publish
#                [-Repo kenny824319/DualityIME] [-Version X.Y.Z] [-WhatIf] [-Trailers "line|line"]
# prepare: bump VERSION (develop X.Y.(Z+1), main X.(Y+1).0), commit, run the full check, build the
#          release binaries (cpp\publish.cmd) and the installer, then print the commit to push. develop with no packaged change: nothing happens.
#          develop stops first when origin/main has commits develop lacks (main's X.(Y+1).0 bump): merge main
#          back into develop, else its pre-releases would come out below the stable release.
#          It also stops when no commit at or after the newest change of engine/ or cpp/engine/ since the
#          last tag adds an end-to-end case line ("keys => text" in cpp/tools/*.txt; Get-MissingE2eCases).
# Between them: install the installer just built, run tools\e2e_all.ps1 (every end-to-end case file), push.
# publish: after the commit is pushed and HEAD equals origin/<branch>, and out\e2e-record.json is of this
#          commit and of the DLL in bin\ (Get-E2eRecordProblem), create the GitHub Release
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
    # lines at the end of the version commit, separated by | (Claude passes its session's attribution);
    # none by default. One string: -File passes an array parameter as a single element.
    [string]$Trailers = ""
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

# prepare's gate. Commits: those since the last release, newest first, each {Subject, Files, Message,
# AddedCases} (AddedCases: the case lines, "keys => text", the commit adds to cpp/tools/*.txt).
# $null when no commit changed the engine (engine/, cpp/engine/), or the newest one that did is followed
# (itself included) by one that adds a case line or says none is needed (" * 實機測試：不需要新案例
# （理由）", written only with the user's OK); else why it stops. A 選字 fix adds the reported sentences
# as end-to-end cases: 3.1.5 fixed 自由 / 現象 with unit tests only (the user, 2026-11-06), after an
# earlier fix of the same release had added its cases - so "a case file changed since the last release"
# let it through; and a comment changed in a case file is no case.
function Get-AddedCaseLines {
    # (a diff of case files: the added lines that are cases - not comments, not the +++ header)
    param([string[]]$DiffLines)
    return @($DiffLines | Where-Object { $_ -match '^\+(?!\+\+ )(?!#).* => ' }).Count
}

function Get-MissingE2eCases {
    param([object[]]$Commits)
    $covered = $false
    foreach ($c in @($Commits)) {
        $paths = @($c.Files | Where-Object { $_ } | ForEach-Object { ($_ -replace '\\', '/').Trim() })
        if ($c.AddedCases -gt 0 -or "$($c.Message)" -match '(?m)^\s*\* 實機測試：不需要新案例') { $covered = $true }
        if (@($paths | Where-Object { $_ -like "engine/*" -or $_ -like "cpp/engine/*" }).Count) {
            if ($covered) { return $null }
            return "引擎改了（$($c.Subject)），之後卻沒有新增端到端案例：把回報的句子加進 cpp\tools 的案例檔；" +
                   "真的不需要時，經使用者同意在提交訊息寫「 * 實機測試：不需要新案例（理由）」"
        }
    }
    return $null
}

# publish's gate: $null when out\e2e-record.json (tools\e2e_all.ps1 writes it when every end-to-end case
# file passed) is of this commit and of the DLL and engine built in bin\, else why it stops. 3.1.5 went
# out without them (the user, 2026-11-06: 「少了端到端就不叫完整測試」).
function Get-E2eRecordProblem {
    param([string]$RecordText, [string]$Head, [string]$Dll, [string]$Engine)
    $fix = "安裝 out 裡這一版的安裝檔，再跑 tools\e2e_all.ps1（跑的時候不要碰鍵盤），全部通過後重跑 -Phase publish"
    if (-not $RecordText) { return "沒有端到端測試的紀錄（out\e2e-record.json）：$fix" }
    try { $r = $RecordText | ConvertFrom-Json } catch { return "端到端測試的紀錄讀不懂：$fix" }
    if ($r.head -ne $Head) { return "端到端測試是在另一個提交（$($r.head)）跑的，不是 ${Head}：$fix" }
    if ($r.dll -ne $Dll) { return "端到端測試用的輸入法 DLL 不是這次建置的 bin\DualityIME64.dll：$fix" }
    if ($r.engine -ne $Engine) { return "端到端測試用的引擎不是這次建置的 bin\DualityIMEEngine.exe：$fix" }
    return $null
}

function Get-ReleaseArguments {
    param([string]$Version, [string]$Repo, [string]$Sha, [string]$NotesFile, [string]$Channel)
    $a = @("release", "create", "v$Version", "out\DualityIME-$Version-setup.exe", "--repo", $Repo, "--target", $Sha,
           "--title", "兩儀輸入法 $Version", "--notes-file", $NotesFile)
    if ($Channel -eq "develop") { $a += "--prerelease" }
    return $a
}

# The tag the release notes start from. develop: the nearest tag (the previous pre-release). main: the
# latest stable release, because develop's pre-release tags are merged in too and the nearest one would
# leave out everything since the last stable; the nearest tag when there is no stable release yet.
function Get-NotesBase {
    param([string]$Channel, [string]$Nearest, [string]$LatestStable)
    if ($Channel -eq "main" -and $LatestStable) { return $LatestStable }
    return $Nearest
}

# Messages: whole commit messages, newest first. Each item line ( * / + ) is a line of its own (git's
# %s joins the lines of the first paragraph); trailers and the version bump are left out; a message
# in another format gives its first line.
# The version commit's message: the bump, then the trailers given as "line|line" (none: no blank line).
function Get-VersionMessage {
    param([string]$Version, [string]$Trailers)
    $lines = @(($Trailers -split '\|') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $msg = " * 版本 $Version`n"
    if ($lines.Count) { $msg += "`n" + ($lines -join "`n") + "`n" }
    return $msg
}

# Items users don't notice, by how they start: tests, tools, the manual, review notes, the to-do
# list, handoffs, the push rules, the release notes themselves. ("問題修正。" items always stay.)
$InternalItem = '^\s*[*+] (新增)?(實機測試|測試|開發工具|使用說明|審查備忘|待辦|交接文件|推送檢查|更新README|README|發正式版|發版)'

function Get-ReleaseNotes {
    param([string]$Version, [string[]]$Messages)
    $lines = @()
    foreach ($m in $Messages) {
        $all = @(($m -split "\r?\n") | Where-Object { $_.Trim() })
        if ($all.Count -eq 0) { continue }
        $items = @($all | Where-Object { $_ -match '^\s*[*+] ' })
        if ($items.Count -eq 0) { $lines += $all[0]; continue }
        $lines += @($items | Where-Object { $_ -notmatch '^\s*\* 版本 \d+\.\d+\.\d+\s*$' -and $_ -notmatch $InternalItem })
    }
    if ($lines.Count -eq 0) { return "兩儀輸入法 $Version" }
    return ($lines -join "`n")
}

# The program to run: the one found on PATH, else the usual install path if it is there, else ""
# (not installed: the caller stops, instead of running a path that is not there and reading the
# exit code the command before left - 3.1.3 was reported published with no gh on the computer)
function Resolve-Command {
    param([string]$OnPath, [string]$Usual)
    if ($OnPath) { return $OnPath }
    if ($Usual -and (Test-Path -LiteralPath $Usual)) { return $Usual }
    return ""
}

# ---- everything below runs only when the script is run, not when a test dot-sources it ----
if ($MyInvocation.InvocationName -eq ".") { return }

# git prints UTF-8; read it as such (the subject comparison of Get-PrepareVersion has Chinese in it)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$gh = Resolve-Command -OnPath (Get-Command gh -ErrorAction SilentlyContinue).Source -Usual "C:\Program Files\GitHub CLI\gh.exe"

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
    # (a program that is not there sets no exit code: the one before it would read as success)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { Stop-Release "$Exe not found; $line was not run. $FailMessage" }
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
    # (since the last release: the nearest tag; none yet, a new repo: no gate)
    $lastTag = (& git describe --tags --abbrev=0 HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $lastTag) {
        $since = @()
        foreach ($sha in @(Invoke-Git rev-list "$lastTag..HEAD")) {
            $since += [pscustomobject]@{
                Subject = (@(Invoke-Git log -1 --format=%h%x20%s $sha)[0])
                Files = @(Invoke-Git -c core.quotepath=false show --name-only --format= $sha)
                Message = (@(Invoke-Git log -1 --format=%B $sha) -join "`n")
                AddedCases = Get-AddedCaseLines @(Invoke-Git -c core.quotepath=false show --format= -U0 $sha -- "cpp/tools/*.txt")
            }
        }
        $missing = Get-MissingE2eCases -Commits $since
        if ($missing) {
            if ($WhatIf) { Write-Host "[WhatIf] note: $missing (a real run would stop here)" }
            else { Stop-Release $missing }
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
    $msg = Get-VersionMessage -Version $next -Trailers $Trailers
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
    Write-Host "Version $next is committed, checked and built. Then, in this order:"
    Write-Host "  install out\DualityIME-$next-setup.exe (the user: administrator rights)"
    Write-Host "  powershell -ExecutionPolicy Bypass -File tools\e2e_all.ps1   (every end-to-end case file; keep off the keyboard)"
    Write-Host "  git push origin $branch"
    Write-Host "  -Phase publish (it refuses without the end-to-end record of this commit and this DLL)"
    exit 0
}

# ---- publish ----
if (-not $gh) {
    $ghHint = "GitHub CLI (gh) is not installed: winget install GitHub.cli, then gh auth login"
    if ($WhatIf) { Write-Host "[WhatIf] note: $ghHint; a real run would stop here"; $gh = "gh" }
    else { Stop-Release $ghHint }
}
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
$recordFile = Join-Path $root "out\e2e-record.json"
$builtDll = Join-Path $root "bin\DualityIME64.dll"
$recordText = if (Test-Path $recordFile) { [IO.File]::ReadAllText($recordFile) } else { "" }
$dllHash = if (Test-Path $builtDll) { (Get-FileHash -LiteralPath $builtDll -Algorithm SHA256).Hash } else { "" }
$builtEngine = Join-Path $root "bin\DualityIMEEngine.exe"
$engineHash = if (Test-Path $builtEngine) { (Get-FileHash -LiteralPath $builtEngine -Algorithm SHA256).Hash } else { "" }
$e2eProblem = Get-E2eRecordProblem -RecordText $recordText -Head $head -Dll $dllHash -Engine $engineHash
if ($e2eProblem) {
    if ($WhatIf) { Write-Host "[WhatIf] note: $e2eProblem (a real run would stop here)" }
    else { Stop-Release $e2eProblem }
}

$nearest = (& git describe --tags --abbrev=0 HEAD)
if ($LASTEXITCODE -ne 0) { $nearest = "" }
$latestStable = ""
if ($Channel -eq "main") {
    # (gh release view without a tag: the latest non-prerelease Release; none yet: "")
    $latestStable = (& $gh release view --repo $Repo --json tagName -q .tagName 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not (Test-TagExists "$latestStable")) { $latestStable = "" }
}
$prev = Get-NotesBase -Channel $Channel -Nearest $nearest -LatestStable $latestStable
if ($prev) {
    # (%x1e ends each message: a message has several lines)
    $messages = @(((Invoke-Git log "$prev..HEAD" --format=%B%x1e) -join "`n") -split [char]0x1e)
} else { $messages = @() }
$notes = Get-ReleaseNotes -Version $ver -Messages $messages
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
