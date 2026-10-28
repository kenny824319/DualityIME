# Which language 兩儀輸入法 is set up under in this user's keyboard list: 中文(香港) (what the
# installer does; not a setting the user sees), or 中文(台灣). Under 中文(香港) Windows takes it
# for another language and Ctrl+Shift (between languages) reaches it from another 中文(台灣)
# input method:
# 華碩注音 (台灣) -> 兩儀輸入法 (香港) -> 日文. The text typed is the same (繁體); Office tags it
# 中文(香港) for proofing. The input method is registered for both (see the DLL); this only
# chooses the list it is in. Per user, no administrator rights needed.
# Before Windows 11 24H2 (build 26100) the installer uses 中文(台灣): there Windows also lists
# the US keyboard it loads for 中文(香港), as one more 「繁體中文(台灣) US」 to switch through.
# Dot-source it, then: Get-SzLanguage ("0404" / "0C04"; without a stored choice, the one the
# installer makes on this Windows), Set-SzLanguage $hk.

$SzTipId = "{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}{BFDAC825-E67C-4E87-B644-E6B5F7B3D08B}"
$SzKey = "HKCU:\Software\DualityIME"

Add-Type -Namespace DualityIMELang -Name Native -MemberDefinition @'
[DllImport("input.dll", CharSet = CharSet.Unicode)] public static extern bool InstallLayoutOrTip(string tip, uint flags);
'@ -ErrorAction SilentlyContinue

function Get-SzLanguage {
    $v = (Get-ItemProperty $SzKey -ErrorAction SilentlyContinue).Language
    if ($v -eq "zh-TW") { return "0404" }
    if ($v -eq "zh-HK") { return "0C04" }
    # not set (an install that never chose): what the installer would choose on this Windows
    if ([Environment]::OSVersion.Version.Build -ge 26100) { return "0C04" } else { return "0404" }
}

function Get-SzTip($lang) { return "$($lang):$SzTipId" }

function Set-SzLanguage([bool]$hk) {
    $new = if ($hk) { "0C04" } else { "0404" }
    $old = if ($hk) { "0404" } else { "0C04" }
    $newTip = Get-SzTip $new
    $oldTip = Get-SzTip $old
    if (-not (Test-Path $SzKey)) { New-Item $SzKey | Out-Null }   # (not -Force: keeps the other values)
    if (-not [DualityIMELang.Native]::InstallLayoutOrTip($newTip, 0)) { throw "無法把兩儀輸入法加進鍵盤清單" }
    # the other one goes, also when Windows lists it without it being in the language list
    [void][DualityIMELang.Native]::InstallLayoutOrTip($oldTip, 1)   # ILOT_UNINSTALL
    try {   # the default input method follows
        if ([string](Get-WinDefaultInputMethodOverride).InputMethodTip -eq $oldTip) {
            Set-SzDefault $newTip
        }
    } catch { }
    if (-not $hk) { Remove-SzAddedLanguage }
    New-ItemProperty -Path $SzKey -Name Language -Value $(if ($hk) { "zh-HK" } else { "zh-TW" }) -PropertyType String -Force | Out-Null
}

function Remove-SzAddedLanguage {
    # 中文(香港), if nothing of the user's is set up under it. (Windows files our 0C04 input method
    # under 中文(台灣) in the list, but loads a 中文(香港) input language with a US keyboard for it,
    # which Ctrl+Shift would go on stopping at, shown as 「繁體中文(台灣) US」.) Not only when it
    # came in for us: a reinstall over what an earlier version left finds it already there.
    Remove-ItemProperty $SzKey -Name AddedHK -ErrorAction SilentlyContinue   # (what earlier versions went by)
    $list = Get-WinUserLanguageList
    if (@($list | Where-Object { $_.LanguageTag -like "zh-*HK" }).Count) { return }   # the user's own 中文(香港)
    $others = @($list.InputMethodTips | Where-Object { $_ -like "0C04:*" -and $_ -notlike "*$SzTipId*" })
    if ($others.Count) { return }
    foreach ($kb in @("0C04:00000409", "0C04:00000C04")) {
        [void][DualityIMELang.Native]::InstallLayoutOrTip($kb, 1)   # ILOT_UNINSTALL
    }
    Remove-SzPreloadHK
}

# 中文(香港) out of what is loaded at sign-in (Keyboard Layout\Preload: values 1, 2, 3... in order,
# the rest renumbered). Never all of them. $preload: a stand-in key for the test (tools\test_setup.ps1).
function Remove-SzPreloadHK([string]$preload = "HKCU:\Keyboard Layout\Preload") {
    $p = Get-ItemProperty $preload -ErrorAction SilentlyContinue
    if (-not $p) { return }
    $names = @($p.PSObject.Properties | Where-Object { $_.Name -match '^\d+$' } | Sort-Object { [int]$_.Name })
    $keep = @($names | Where-Object { "$($_.Value)" -notmatch '0c04$' } | ForEach-Object { "$($_.Value)" })
    if ($keep.Count -eq $names.Count -or -not $keep.Count) { return }
    foreach ($n in $names) { Remove-ItemProperty $preload -Name $n.Name }
    for ($i = 0; $i -lt $keep.Count; $i++) { Set-ItemProperty $preload -Name "$($i + 1)" -Value $keep[$i] }
}

# The default input method. Making ours the default (or another one, while ours is set up under
# 中文(台灣)) has Windows add 中文(香港) (00000c04) to Preload: the input method is registered for both
# languages. Under 中文(台灣), and with no 中文(香港) of the user's in the list, it goes again.
# Under 中文(香港) (a 0C04 tip, or Language zh-HK) Preload stays as Windows has it.
function Set-SzDefault([string]$tip) {
    Set-WinDefaultInputMethodOverride -InputTip $tip
    if ($tip -like "0C04:*" -or (Get-SzLanguage) -ne "0404") { return }
    if (@(Get-WinUserLanguageList | Where-Object { $_.LanguageTag -like "zh-*HK" }).Count) { return }
    Remove-SzPreloadHK
}
