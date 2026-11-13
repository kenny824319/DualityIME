"""Checks that the C++ engine gives exactly the same results as the Python one.

    python tools/compare.py [count] [seed]

Types the same key scripts into both (tools/trace.py and cpp's sz_trace.exe):
hand-written cases plus `count` random ones (sentences from the dictionary,
English words, punctuation, numbers, candidate picks, Tab, undo, Caps Lock,
the symbol keyboard and random keys), and compares the state after every key.
Build the C++ side first: cpp\\build.cmd
"""
import json
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine.lexicon import Lexicon, load_english  # noqa: E402
from engine.zhuyin import reading_to_keys  # noqa: E402

EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_trace.exe")

CASES = [
    # (before any pick of 試: with one, 試試 comes without the model too)
    "ji3y94u h4g4g4{ENTER}",  # 我再一次試試: 試試 only with 長句選字加強 (逝世 without)
    # (first: the presses counted are those of a list nothing has reordered yet)
    # a pick inside a word of the dictionary that leaves none (去世 -> 去試): a word the second time
    # (試 is the 11th of the list, the 2nd once picked); 世 picked back (the 9th, then the 3rd) forgets
    # it, and it is learned again as the first time
    "".join("fm4g4u vu84{LEFT}{LEFT}{LEFT}" + "{DOWN}" * n + "{ENTER}{ENTER}fm4g4u vu84{ENTER}" for n in (11, 2, 9, 2, 2, 3))
    + "yj3zj4fm4g4xk7{ENTER}",
    # 注音自動校正 with the sentence around it: a tone left out (5k邊: 這邊), a punctuation key out of
    # order (;e: 剛), a key next to the right one spelling a word (現役 -> 信義, 你老 -> 你好), and 現 picked
    # back twice (31st in the list): as typed from then on
    "5k1u0 {ENTER}e;e; {ENTER}e; ;e {ENTER},u3{ENTER}su3xl3a87{ENTER}w961o3g4vu04u4fm {ENTER}",
    ("w961o3g4vu04u4fm {HOME}{RIGHT}{RIGHT}{RIGHT}" + "{DOWN}" * 31 + "{ENTER}{ENTER}") * 2 + "w961o3g4vu04u4fm {ENTER}",
    "ok hi go su ios 5k ", "5ke; {HOME}{DOWN}3{END}e; 1u0 {ENTER}",  # (浙 picked for 5k: kept as more comes)
    # a word across the phrase sent last (怎麼 sent, then 辦: not 半), and 長句選字加強 keeping cohesive
    # words whole (新舊, 選字, 有件事倒是) but not two common characters (我在 for 我再看看)
    "yk3ai7{ENTER}104{ENTER}1j4xu3vu;3yk3ai7{ENTER}104{ENTER}",
    "1j6ul4sj/4gk6ai7vup ru.4rul31p3{ENTER}vm03y4{ENTER}ji3y94vm03y4{ENTER}ji3y94d04d04{ENTER}",
    "xu/4j94u.3ru04g42l4g4ul4su3qu/6ej u vu84{ENTER}",
    # digits out of order at the end of a run. 817 is a number in AUTOCORRECT's keys.json (picked back
    # twice), so with that file these lines go the "picked twice" way and never ask the model; they
    # still compare the cases around it: Chinese or a syllable being typed after it (817個, 817e),
    # English after it, a number the model says no to (電話是824)
    "ji3ap7y.3817{ENTER}ji3ap7y.3817 ok{ENTER}nj04xk7817{ENTER}hello ji3ap7y.3817{ENTER}",
    "z;6ru0 cl4a83g4817{ENTER}2u04cj84g4824{ENTER}ql3xk7817 km{ENTER}",
    "ji3ap7y.3817ek7{ENTER}ji3ap7y.3817e{BACK}{ENTER}ji3ap7y.38172jo4817{ENTER}",
    # the model's answer itself, with 814 (not in keys.json): 趕快把 and 你先爸 come from the model;
    # 我們走814個814 asks after 個, not after 我們走罷個
    "e03dj94814{ENTER}su3vu0 814{ENTER}ji3ap7y.3814ek7814{ENTER}ji3ap7y.3814xk7814{ENTER}",
    # a 1-2 letter word after English that starts a common word is zhuyin (B 因為, 為 typed ㄨㄟˊ too,
    # thank 一個); one that starts a less common one stays English (look up 此操作, look up 還是)
    "cl3 B up jo6su3g4{ENTER}", "hello up jo4{ENTER}", "thank u ek4{ENTER}", "look up h3hl yji4{ENTER}",
    "look up c96g4{ENTER}", "hello up jo6",
    # (another tone of a tone put back: 哲 third in ↓ at 這邊, then 遮 further down; kept, then learned)
    "5k1u0 {HOME}{DOWN}3{ENTER}{ENTER}5k1u0 {ENTER}vu8y94{HOME}" + "{DOWN}" * 23 + "{ENTER}{ENTER}vu8y94{ENTER}",
    "u.vu/6{HOME}{DOWN}2{ENTER}u.vu/6{ENTER}",  # (有 picked at 遊行: 有形, the rest with the new tone)
    # (no punctuation key reordered before digits after English, but after Chinese; no tone put back right after English; a tone that
    # makes a word of the personal dictionary: 哲宇; ;e back as typed from the end of the list, twice)
    "no.2 ch.2 ji3.24{ENTER}v3.2 su3.2 {ENTER}you are 3.2 {ENTER}su5k1u0 ji35k1u0 {ENTER}5km3{ENTER}",
    ("su3;e {HOME}{RIGHT}" + "{DOWN}" * 35 + "{ENTER}{ENTER}") * 2 + "su3;e {ENTER}",
    ("e;e; {HOME}" + "{DOWN}" * 36 + "{ENTER}{ENTER}") * 2 + "e;e; {ENTER}",  # (e; back as typed, last of 36, twice)
    # 匹配 typed ㄆㄧ ㄆㄟˋ (ㄆㄧˇ in the dictionary): 注音自動校正 leaves ㄆㄟˋ (not 批判); 匹 picked: 匹配 learned
    "qu qo4{ENTER}qu qo4{HOME}{DOWN}2{ENTER}qu qo4{ENTER}",
    # a pick beside a character on its own, no word (重 + 繪): half a pick of the pair, a word the second time
    "cjo4{DOWN}1{ENTER}cjo4{DOWN}1{ENTER}y42u/4tj/6cjo4{DOWN}5{ENTER}y42u/4tj/6cjo4{DOWN}5{ENTER}"
    "y42u/4tj/6cjo4{ENTER}tj/6cjo4{HOME}{DOWN}2{ENTER}",
    # words the dictionary has with other tones (下載 is ㄗㄞˇ there): learned from a pick, and back
    "vu84y94{DOWN}3{ENTER}vu84y94{ENTER}vu84y94{DOWN}1{ENTER}vu84y94{ENTER}",
    "ji3vu84y94{DOWN}3{ENTER}vu84y94{ENTER}", "s83vu, {HOME}{DOWN}3{ENTER}s83vu, {ENTER}",
    # ... forgotten when picked back (巴西 -> 八西), and none beside a pick that makes a word (打工|作)
    "18 vu7{HOME}{DOWN}3{ENTER}18 vu7{HOME}{DOWN}3{ENTER}18 vu7{ENTER}", "283ej/ yji {HOME}{RIGHT}{DOWN}2{ENTER}ej/ yji {ENTER}",
    # 注音自動校正 and its memories (keys.json in AUTOCORRECT): a pick kept when 813 is picked as 把,
    # a learned reading not in an address, 代碼記憶 back as before
    "u.3813ek7cl3{LEFT}{DOWN}2{HOME}{RIGHT}{DOWN}1{ENTER}", "abc@si3.com si3{ENTER}", "mp3{DOWN}1{ENTER}mp3{ENTER}",
    "claude{ENTER}claude{ENTER}cla{TAB}", "Claude{ENTER}Claude{ENTER}cla{TAB}{ENTER}Cla{TAB}",
    "claude claude{ENTER}cla", "ji3claude{ENTER}ji3claude{ENTER}ji3cla{TAB}",
    "abcc12345678@example.com{ENTER}gmai", "gpt4 python3 don't{ENTER}gpt4 python3 don't{ENTER}don",
    "{CAPS}cla{CAPS}{TAB}", "claud{TAB}",
    # (a next character whose reading is the dictionary's only inside its word: the rest of the word)
    "ej93j0 {TAB}{ENTER}ej93j0 {TAB}{BACK}{ENTER}",
    "kaz{TAB}", "kazuma ", "su3kazuma{ENTER}", "KAZ{TAB}", "dd{DOWN}1", "1j8{HOME}{UP}{ENTER}{ENTER}1j8{DOWN}1", "cc{HOME}{UP}{ENTER}{ENTER}su3cc{DOWN}{ENTER}", "1j84{HOME}{UP}{ENTER}", "hello{HOME}{UP}", "ok{HOME}{UP}", "lol{HOME}{UP}", "187", "ji318", "2026 ", "5j", "OK{HOME}{UP}", "dd{HOME}{UP}",
    # an unfinished syllable back to its keys where it shows as zhuyin (preview zhuyin: ↓ and the bar have su,
    # this once: su is ㄋㄧ again after it); the other previews offer ㄋㄧ only
    "su{DOWN}2{ENTER}su3su{DOWN}", "su{HOME}{UP}{ENTER}{ENTER}su3su{ENTER}", "t{HOME}{UP}{ENTER}su3t{DOWN}{ESC}{ENTER}",
    "a{HOME}{UP}{ENTER}{ENTER}su3a{HOME}{RIGHT}{UP}{ENTER}{ENTER}", "{CAPS}su{CAPS}{HOME}{UP}{ENTER}{ENTER}",
    # (keys with a digit are no code of 代碼記憶 after two such picks: 1j is ㄅㄨ still; a punctuation key
    # after Chinese is offered neither way)
    "1j{DOWN}2{ENTER}1j{DOWN}2{ENTER}1j{ENTER}su31j{HOME}{RIGHT}{UP}{ENTER}{ENTER}1j{ENTER}su3cl3,{LEFT}{UP}{ESC}{DOWN}{ESC}{ENTER}",
    # keys put back as English from the bar (gj of 輸), then the syllable picked back from the bar: forgotten
    # (輸入 came out gj 入 for good); the bar's label trying the pick out forgets nothing
    "gj {HOME}{UP}{RIGHT}{RIGHT}{ENTER}{ENTER}gj bj4{HOME}{UP}{RIGHT}{ENTER}{ENTER}gj bj4{ENTER}gj {HOME}{UP}{ENTER}{ENTER}",
    "gj {HOME}{UP}{RIGHT}{RIGHT}{ENTER}{ENTER}gj bj4{HOME}{UP}{ENTER}{ENTER}gj bj4{ENTER}",
    "vup {HOME}{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}{ENTER}vup ej/ s/6{HOME}{UP}{RIGHT}{ENTER}{ENTER}vup ej/ s/6{ENTER}vup {ENTER}",
    "vup {HOME}{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}{ENTER}vup {HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}vup {ENTER}",
    # (an English word with keys right after it: gjbj4 is gj入, gj, - its own keys are the word forgotten)
    "gj {HOME}{UP}{RIGHT}{RIGHT}{ENTER}{ENTER}gjbj4{HOME}{UP}{ENTER}{ENTER}gjbj4{ENTER}gj {ENTER}",
    "gj {HOME}{UP}{RIGHT}{RIGHT}{ENTER}{ENTER}gj,{HOME}{UP}{ENTER}{ENTER}gj bj4{ENTER}",
    # 齊 picked before 一個 (typed ㄍㄜˋ, the dictionary's is ㄍㄜ˙) three times: no 齊一個 (星期一個 stays)
    ("ji3gji fu6u ek4xk7{HOME}{RIGHT}{RIGHT}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}" * 3) + "vu/ fu6u ek4{ENTER}",
    "su3{^,}cl3{^.}", "{^?}{^[}hello{^]}{^a}{^-}", "hello``", "hello`1", "abc~", "su3`{ESC}",
    "g4{DOWN}{RIGHT}{RIGHT}{DOWN}{LEFT}{UP}{LEFT}{ESC}{ESC}", "g4{DOWN}{RIGHT}{RIGHT}{RIGHT}{RIGHT}{RIGHT}{PGDN}{PGUP}2",
    "u4{DOWN}{RIGHT}{PGDN}{PGDN}{PGDN}{PGDN}{PGDN}{PGDN}{PGDN}{RIGHT}{ENTER}{ENTER}", "fu,6{DOWN}{RIGHT}{RIGHT}{ENTER}",
    "/continue{ENTER}/continue{ENTER}/con{TAB}{ENTER}", "/usr/bin{ENTER}/usr/bin{ENTER}/us", "//comment{ENTER}",
    "hello {ENTER}a{LEFT}{UP}{ENTER}{ENTER}", "su{LEFT}{LEFT}{UP}{ENTER}{ENTER}", "sugata{LEFT}{UP}", "up{LEFT}{LEFT}{UP}{RIGHT}{ENTER}",
    "vu04{DOWN}3{ENTER}y94{DOWN}1{ENTER}vu04y94{ENTER}", "j4{DOWN}2{ENTER}cjo4{DOWN}1{ENTER}su3j4cjo4{ENTER}",
    "u {DOWN}5{ENTER}u {DOWN}5{ENTER}u d9 g3{ENTER}u ",
    "su3{DOWN}2{ENTER}su3{DOWN}2{ENTER}su3{DOWN}2{ENTER}vu,4vu,7su3{ENTER}su3gji {ENTER}ji394su3{ENTER}",
    "w8 {DOWN}2{ENTER}w8 {DOWN}2{ENTER}w8 {DOWN}2{ENTER}ji3ep w8 gji {ENTER}",
    "github.com/anthropics{ENTER}github.com/anthropics{ENTER}anth", "code.py okay? I love claude.{ENTER}",
    "su3cl3", "ji3ap7", "m/4googlen. ", "googlen. ", "done. ", "m/4done. {DOWN}1", "claudezp vu ",
    "claudefm4", "python3", "gpt4", "slack ", "104", "10{DOWN}4", "18 ", "18{ENTER}", "su3,", "su3,{ENTER}",
    "w96{TAB}{TAB}", "w96{RIGHT}", "internati{TAB}", "abc@example.com ", "abc{TAB}", "{CAPS}su3{CAPS}",
    "{CAPS}usb{CAPS} ", "`1", "`{TAB}{DOWN}{ENTER}", "~", "su3~cl3", "{FW}~{FW}", "y4u.6{ENTER}vu04vu;4{ENTER}ji3y4u.6{ENTER}", "su3{LEFT}{DOWN}2", "su3cl3{HOME}{DOWN}{DOWN}{ENTER}",
    "up {UNDO}", "up {DOWN}1", "look up ", "ssu3", "us3", "su3{BACK}", "su3cl3{BACK}{BACK}", "{#1}{#2}",
    "su3{#5}", "1", "1{DOWN}1", "Python寫", "su3 cl3!", "a\"b", "{FW}abc {FW}", "su3[", "{LB}", "you are 18",
    "ji3 m/4 google n. vu04", "ru,4ru,4", "5j/ ej/4 5j/ ej/4 ", "su3{DOWN}{PGDN}{PGUP}{DOWN}{DOWN} ",
    "0 5j; pime103", "0 5j; pime103{LEFT}{DOWN}2", "0 5j; pime103{LEFT}{LEFT}{LEFT}{RIGHT}{RIGHT}{DOWN}1",
    "room104{LEFT}{DOWN}1", "m/4pime{#1}{#0}{#3}{DOWN}1", "m/4hello{DOWN}", "m/4pime103{DOWN}2{DOWN}1{DOWN}2{DOWN}{ESC}", "look up {DOWN}2{DOWN}1", "m/4pime103{DOWN}{PGDN}{DOWN}{DOWN}{DOWN} {LEFT}{LEFT}{DOWN}{ESC}{END}{DOWN}2{DOWN}",
    "m/4pime103{DOWN}{PGDN}4", "m/4pime103{DOWN}{PGDN}4{LEFT}{LEFT}{DOWN}2", "m/4pime103{DOWN}{PGDN}4{DOWN}2{ENTER}",
    "m/4pime103{DOWN}2{DOWN}{PGDN}4{LEFT}{LEFT}{DOWN}2{DOWN}{PGDN}4",
    "m/4pime103{DOWN}2{LEFT}{LEFT}{DOWN}1", "m/4pime103{DOWN}2{LEFT}{BACK}", "m/4pime103{HOME}{DEL}a{RIGHT}{RIGHT}b",
    "su3cl3hello{LEFT}{LEFT}{BACK}x{END}{DOWN}", "abc def{HOME}{RIGHT}{DEL}{DEL}{LEFT}{DOWN}", "room104{LEFT}{LEFT}{LEFT}{DOWN}1", "pythong4", "hellog4", "workg4", "dog4", "fingersu ", "google,ji3",
    "python.ji3", "python3ji3", "e-mailji3", "well-knownji3", "user'sji3", "asdfji3", "2024", "ssu3", "gpt4",
    "su3cl3{LEFT}{UP}{RIGHT}{ENTER}", "su3cl3{LEFT}{UP}{ENTER}{UP}{ENTER}", "up {LEFT}{UP}{RIGHT}{RIGHT}{ENTER}",
    "m/4pime103{LEFT}{UP}{RIGHT}{ENTER}{UP}{ENTER}", "ji3187{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}su3", "youtube2; 5j/ ", "sugatau,4au04", "code-rev{TAB}", "code-revi{TAB} ", "user/pro{TAB}", "file_nam{TAB}", "e-mai{TAB}", "abc.inf{TAB}", "sugatau,4au04{LEFT}{LEFT}{LEFT}{UP}{ENTER}{ENTER}", "sugatau,4au04{LEFT}{LEFT}{LEFT}{LEFT}{UP}{RIGHT}{ENTER}", "pime103{LEFT}{LEFT}{UP}{ENTER}", "googlen. {LEFT}{LEFT}{LEFT}{UP}{ENTER}x", "hellou,4world {LEFT}{LEFT}{LEFT}{LEFT}{LEFT}{LEFT}{LEFT}{LEFT}{UP}{ENTER}", "rup wu0 u,4au04{LEFT}{LEFT}su3", "su3cl3{LEFT}ji3", "gogle {LEFT}{LEFT}{LEFT}{LEFT}o", "rup wu0 hk4g4g6yji4u,4au04{LEFT}{LEFT}{LEFT}{LEFT}{DOWN}{ENTER}x", "rup wu0 hk4g4g6yji4u,4au04{LEFT}{LEFT}{LEFT}{LEFT}{DOWN}3x", "sugatau,4au04{LEFT}{LEFT}{LEFT}{LEFT}{UP}{RIGHT}{ENTER}", "hellou,4", "1,000", "ji3,4", "[", "'su3", "=su3", "?", "(su3)", "su3[", "su3cl3ji3m/4google2; 5j/ 56vu/6setup.exea87?su3cl3ji3m/4google2; 5j/ 56vu/6setup.exea87?su3cl3ji3m/4google2; 5j/ 56vu/6setup.exea87?su3cl3ji3m/4google2; 5j/ 56vu/6setup.exea87?", "google2; 5j/ {LEFT}{LEFT}{LEFT}{LEFT}{UP}{ENTER}", "win10, ji3", "done; ", "python3; ", "ji3187{UP}{ESC}su3", "ji3187{UP}{RIGHT}x", "dog4{UP}1", "pime103{UP}{RIGHT} ", "su3cl3{LEFT}{LEFT}{DOWN}2", "su3cl3{LEFT}{UP}{LEFT}{ESC}{RIGHT}x",
    "1j4{DOWN}1{ENTER}1j4{DOWN}1{ENTER}1j4{DOWN}1{ENTER}1j4{DOWN}1{ENTER}1j4y.4{ENTER}1j4zp4{ENTER}",
    "1j42jo4{HOME}{DOWN}1{ENTER}1j42jo4{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}1j42jo4{ENTER}g3{DOWN}2{ENTER}d9 g3{HOME}{DOWN}2{ENTER}",
        "ej/ g4{DOWN}{ENTER}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}", "g4g6{DOWN}{ENTER}{ENTER}", "ej/ g4{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}",
    "rup wu0 {DOWN}{DOWN}{ENTER}{BACK}gj3{ENTER}", "rup wu0 {DOWN}{DOWN}{ENTER}{BACK}y3{ENTER}", "u4rup wu0 wu0 fu4{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{ENTER}{ENTER}",
    "gp6ak7{DOWN}{DOWN}{ENTER}{ENTER}gp6ak7{ENTER}", "rup wu0 {DOWN}{DOWN}{DOWN}{ENTER}{UNDO}{ESC}{ESC}", "1j42jo4{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{DOWN}{DOWN}{ENTER}{ENTER}1j42jo4{ENTER}",
    "rup wu0 {DOWN}{ENTER}{ENTER}rup wu0 {ENTER}", "1j42jo4{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}1j42jo4{ENTER}", "rup wu0 wu0 fu4{LEFT}{LEFT}{DOWN}{ENTER}{ENTER}",
    "su3cl3{DOWN}{DOWN}{ENTER}{ENTER}su3cl3{ENTER}", "rup wu0 wu0 fu4{HOME}{RIGHT}{DOWN}{DOWN}{ENTER}{ENTER}rup wu0 {ENTER}",
    "1j42jo4{HOME}{DOWN}1{ENTER}1j42jo4{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}{UNDO}{ESC}{ESC}1j42jo4{ENTER}",
    "m/4pime103{LEFT}{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}{HOME}{RIGHT}{UP}{ENTER}", "m/4{#1}{#0}{#4}{DOWN}2", "ji3abc12345678@example.com ", "0 5j; pime103{HOME}{DOWN}{ESC}{END}{LEFT}{LEFT}{DOWN}",
    # typing memory: an e-mail typed once is offered (a URL is not); one letter put back as typed is not learned
    "qwmail@example.com{ENTER}qwm{TAB}{ENTER}", "https://example.com/abc {ENTER}htt{TAB}",
    "ji3{ENTER}t {HOME}{UP}{RIGHT}{ENTER}{ENTER}ji3{ENTER}t ",
    # numpad keys with the caret moved back: in at the caret
    "ji3u.3ek7{LEFT}{#1}{#0}{ENTER}", "abcd{LEFT}{LEFT}{#5}{BACK}{#6}{RIGHT}{#7}{ENTER}",
    "rup wu0 u,4au04{LEFT}{LEFT}{#3}{DOWN}", "ji3u.3ek7{HOME}{#9}{END}{#8}{LEFT}{LEFT}{BACK}{ENTER}",
    "ji3187{UP}{#5}{ENTER}", "ji3187{LEFT}{UP}{#5}{ENTER}",
    # an English word sent twice is English when the keys are all of it (mochi + 5p: mochi真)
    "mochi {ENTER}rup wu0 2k7mochi5p 2k7a06cl3t {ENTER}mochi {ENTER}rup wu0 2k7mochi5p 2k7a06cl3t {ENTER}",
    "Mochi {ENTER}Mochi {ENTER}mochi1o4{ENTER}mochia1o4{ENTER}", "mochi {ENTER}mochi {ENTER}mochi5p {LEFT}{UP}{ESC}{DOWN}",
    # ...and left alone by 注音自動校正 (↓: no 數 碩 蟀 for gjie), and no 注音文 on ↑
    "gjie{DOWN}{ESC}{ESC}gjie {ENTER}gjie {ENTER}gjie{DOWN}{ESC}{UP}{ESC}{ESC}",
    # ...sent the second time in the head of a long sentence: the picks after it stay on their keys
    "mochi {ENTER}mochi rup wu0 ji3cl3 mochi5p 2k7a06cl3t {LEFT}{LEFT}{DOWN}2" + "ji3cl3 " * 16 + "{ENTER}",
    # capitals, then zhuyin in lower case (PQC功能, UI上), a word going on in lower case (PDFs)
    "PQCej/ s/6{ENTER}UIg;4{ENTER}PDFs{ENTER}PDFsu3{ENTER}ji3ap7UIg;4{ENTER}",
    # ...an acronym that is no English word, then a first tone of letters alone, a tone digit, a syllable that
    # starts with a digit key (AMI新功能, AMI三個, API包括); a syllable of digits alone is a model's number
    "AMIvup ej/ s/6{ENTER}AIvup ej/ s/6{ENTER}AMIn0 ek4{ENTER}API1l dji4{ENTER}AMI5j/ jp6{ENTER}OK5 2l4xk7{ENTER}",
    "PS5 {ENTER}V8 {ENTER}COVID19 {ENTER}SQLite {ENTER}NVMe {ENTER}PDFtk {ENTER}JSONify {ENTER}IPsec {ENTER}UIgj {ENTER}",
    # a term that ends in one lower-case letter, or a digit and a letter, stays as typed; PS5 是
    "RNAi {ENTER}SQLi {ENTER}DeFi {ENTER}USDt {ENTER}dBu {ENTER}mmHg g4{ENTER}MP5k {ENTER}D2l {ENTER}ji3RNAi {ENTER}"
    "PS5 g4{ENTER}PS5 2k7{ENTER}APIu ek4{ENTER}API1l {ENTER}AMIu u;4{ENTER}AMIu 2u03{ENTER}dBu u;4{ENTER}USDt 2k7{ENTER}",
    # a capital right after an operator, then a syllable of one letter (A+B一樣, #30); Xu 樣 as typed
    "A+Bu u;42k7ej/ s/6{ENTER}A=Bu u;4{ENTER}A/Bu u;4{ENTER}A+Bu {ENTER}Xu u;4{ENTER}A+B1l dji4{ENTER}",
    # a syllable only the personal dictionary has: of a word of one character (蹦 ㄅㄧㄤˋ, 掰 ㄅㄞ˙ = 197)
    # it is typed, and 注音自動校正 knows it (1i;4); of a longer word only (the ㄅㄧㄤˇ of 電餅, the ㄉㄞ˙ of
    # 呆呆 = 297) it is one inside that word (after its syllables before it; the first, before its next
    # one: 餅噹), and elsewhere its keys stay as typed (有297個, 我1u;3); not finished yet too (電 + eu;)
    "29 297{ENTER}u.3297ek7{ENTER}297{ENTER}room 297{ENTER}296297{ENTER}29 {ENTER}297{ENTER}ep su3gji 29 297au/6wu0 {ENTER}",
    "1u;32; {ENTER}2841u;32u04{ENTER}2841u;3{ENTER}2u04eu;{ENTER}2u04eu;3cl3{ENTER}ji3eu;3{ENTER}eu;3{ENTER}",
    # (numbers after English though they are the word's keys; a word of three not typed to its end; ↓ after
    # such a syllable lists its word)
    "you are 29 297{ENTER}room 29 297{ENTER}ok 29 297cl3{ENTER}2841u;{ENTER}2841u;3{DOWN}{ESC}{ENTER}29 297{DOWN}{ESC}{ENTER}"
    "29 297{DOWN}{ENTER}{ENTER}2u041u;3{DOWN}{DOWN}{ENTER}{ENTER}su329 297{LEFT}{DOWN}{ESC}{ENTER}",
    "29 297{HOME}{UP}{ESC}{DOWN}{ESC}{RIGHT}{UP}{ESC}{DOWN}{ESC}{END}{BACK}{ENTER}29 297{LEFT}{BACK}{END}{ENTER}"
    "2u041u;3{LEFT}{UP}{ENTER}{ENTER}2u041u;3{HOME}{DEL}{ENTER}",
    "ji31u;4{ENTER}g41u;4xk7{ENTER}ji31i;4{ENTER}197{ENTER}u.3197ek7{ENTER}room 197{ENTER}",
    "2u041u;3{ENTER}ji3ap71u;3cl3{ENTER}1u;3{DOWN}{ESC}{ENTER}1i;3cl3{ENTER}"
    "ji3ap71u;3cl3{LEFT}{LEFT}{LEFT}{RIGHT}{UP}{ESC}{RIGHT}{BACK}{END}{ENTER}ji31u;3" + "ji3cl3 " * 22 + "{ENTER}",
    # a name picked right again and again (made-up ones, the first character a family name: 葉冠霆): the first
    # and the last picked with one between them (half of the name, a word the second time), typed again, after
    # other words; the second sentence taken back ({UNDO}: what it taught too)
    ("u,4ej04wu/6{END}{DOWN}" + "{DOWN}" * 5 + "{ENTER}{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}") * 2
    + "u,4ej04wu/6{ENTER}ji35l3u,4ej04wu/6{ENTER}u,4ej04wu/6gji cl3{ENTER}",
    "u,4ej04wu/6{END}{DOWN}{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}"
    "u,4ej04wu/6{END}{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{ENTER}{ENTER}{UNDO}{ESC}{ESC}u,4ej04wu/6{ENTER}",
    # ... the same picks where the first character is no family name (瀋家叡): the characters and the pairs
    # only; 林祤辰, where a pick changes the character after it
    "gp3ru8 bjo4{HOME}{RIGHT}{RIGHT}{DOWN}{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}"
    "gp3ru8 bjo4{HOME}{RIGHT}{RIGHT}{DOWN}{ENTER}{HOME}{DOWN}{ENTER}{ENTER}gp3ru8 bjo4{ENTER}ji35l3gp3ru8 bjo4{ENTER}",
    "xup6m3tp6{HOME}{RIGHT}" + "{DOWN}" * 27 + "{ENTER}{ENTER}xup6m3tp6{HOME}{RIGHT}" + "{DOWN}" * 8 + "{ENTER}{ENTER}"
    "xup6m3tp6{HOME}{RIGHT}{RIGHT}" + "{DOWN}" * 7 + "{ENTER}{ENTER}xup6m3tp6{HOME}{RIGHT}{RIGHT}{DOWN}{ENTER}{ENTER}"
    "xup6m3tp6{ENTER}ji35l3xup6m3tp6{ENTER}",
    "xup6m3tp6{HOME}{RIGHT}{RIGHT}" + "{DOWN}" * 7 + "{ENTER}{HOME}{RIGHT}" + "{DOWN}" * 27 + "{ENTER}{ENTER}"
    "xup6m3tp6{ENTER}ji35l3xup6m3tp6{ENTER}",
    # 霆 picked twice in 蔣冠廷: 冠霆 is a word then, and with it the 蔣 just sent would read 獎 from then on
    # (長句選字加強 on): 蔣冠霆 is learned as it was sent, and comes by itself the third time
    "ru;3ej04wu/6{HOME}{RIGHT}{RIGHT}" + "{DOWN}" * 6 + "{ENTER}{ENTER}ru;3ej04wu/6{HOME}{RIGHT}{RIGHT}{DOWN}{ENTER}{ENTER}"
    "ru;3ej04wu/6{ENTER}ji35l3ru;3ej04wu/6{ENTER}ru;3ej04wu/6g4{ENTER}",
    # ... the first and the last of six picked (no word of six), every character picked (a word at once),
    # the name corrected back (forgotten)
    "gp3ru8 bjo4gp3ru8 bjo4{END}{LEFT}{DOWN}{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}"
    "gp3ru8 bjo4{HOME}{RIGHT}{DOWN}{DOWN}{ENTER}{HOME}{DOWN}{DOWN}{ENTER}{END}{LEFT}{DOWN}{DOWN}{ENTER}{ENTER}"
    "gp3ru8 bjo4{HOME}{DOWN}{DOWN}{DOWN}{ENTER}{ENTER}gp3ru8 bjo4{ENTER}",
    # 自訂詞彙 (#13): typed to its start - letters and digits 1, Chinese characters 2, 3 in all - the Tab hint is the
    # rest of the word whole and Tab writes it as written there; keys compared, not what is shown; the most taken
    # with Tab first (taken twice below: PQC功能 before PQC技法的ABC功能 from then on); typed whole it stays as typed
    "PQC{TAB}{ENTER}pqc{TAB}{ENTER}PQ{TAB}{ENTER}AIej/ {TAB}{ENTER}8 Q{TAB}{ENTER}5Gg.3{TAB}{ENTER}284ej3{TAB}{ENTER}",
    "PQCru4{TAB}{ENTER}PQCx{TAB}{ENTER}ji3ap7PQC{TAB}{ENTER}PQC{TAB}{BACK}{BACK}{ENTER}xPQC{TAB}{ENTER}",
    "PQCej/ s/6{ENTER}PQCej/ s/6{ENTER}PQC{DOWN}{ESC}{TAB}{ENTER}pqcej/ s/6{ENTER}New{TAB}{ENTER}qm8 {TAB}{ENTER}",
    "kaz{TAB}{ENTER}KAZ{TAB}{ENTER}{CAPS}kaz{TAB}{CAPS}{ENTER}{CAPS}pqc{TAB}{CAPS}{ENTER}iph{TAB}{ENTER}",
    "q9 vmp {TAB}{ENTER}284{TAB}{ENTER}284ej3{HOME}{RIGHT}{DOWN}{DOWN}{ENTER}{TAB}{ENTER}",
    # ...over a character picked or 注音 taken from the bar in what is typed: written as it is all the same
    "PQCru4{DOWN}{DOWN}{DOWN}{ENTER}{TAB}{ENTER}PQCru4{LEFT}{UP}{ENTER}{END}{TAB}{ENTER}"
    "pqc{HOME}{UP}{ENTER}{END}{TAB}{ENTER}aiej/ {DOWN}{DOWN}{ENTER}{TAB}{ENTER}",
    # a word whose 注音 is none (？ from 框選查詢) is no hint
    "ji3ap7{TAB}{ENTER}tp6{TAB}{ENTER}",
    # characters' names (#14): a part of one shown as written (艾連 picked over 愛憐) leads to the rest with Tab,
    # remembered the next time; Tab and Backspace; 竈門 -> 炭治郎; letters (蒙其 -> ·D·魯夫); 我愛 of 我愛羅 and 時光 (a
    # word of the dictionary) lead to none; the whole name picked from the list gets its ·, and the list ends
    # with the names a part starts
    "94xu06{HOME}{DOWN}{DOWN}{ENTER}{END}{TAB}{ENTER}94xu06{TAB}{ENTER}94xu06{TAB}{BACK}{BACK}{BACK}2k7{ENTER}",
    "yl4ap6{TAB}{ENTER}a/6fu6{HOME}{DOWN}{ENTER}{END}{TAB}{ENTER}ji394{TAB}{ENTER}g6ej; {TAB}{ENTER}",
    "94xu06u,4d83{HOME}{DOWN}{ENTER}{ENTER}94xu06{HOME}" + "{DOWN}" * 48 + "{ENTER}{ENTER}g6ej; {HOME}{UP}{ENTER}",
    # (review 2026-11-13) the whole name picked keeps what follows as shown (艾連·葉卡和三立, not 河); typed over
    # a · (洛琪希米格路迪亞 + Tab, 艾連葉 picked as 艾連·葉卡): every · written; the bar at a · offers nothing
    "94xu06u,4d83ck6n0 xu4{HOME}{DOWN}{ENTER}{ENTER}xji4fu6vu au3ek6xj42u6u83{TAB}{ENTER}"
    "94xu06u,4{HOME}" + "{DOWN}" * 45 + "{ENTER}{ENTER}94xu06{TAB}{LEFT}{LEFT}{LEFT}{UP}{ENTER}{ENTER}",
    # ...and any start of 2+ characters of a name in the list, people's too (我愛 -> 我愛羅, 田馥 -> 田馥甄)
    "ji394{HOME}" + "{DOWN}" * 40 + "{ENTER}{ENTER}wu06zj4{HOME}" + "{DOWN}" * 30 + "{ENTER}{ENTER}",
    # the candidate list's keys and arrows (#23; 1234qweras and a grid in the last settings): a key picks its place,
    # keys not in the set do nothing, the grid's arrows across rows and pages
    "u4{DOWN}{RIGHT}{RIGHT}{RIGHT}{RIGHT}{DOWN}{DOWN}{DOWN}{UP}{LEFT}{ENTER}u4{DOWN}{PGDN}s{ENTER}"
    "u4{DOWN}Q5z{ESC}{ENTER}u4{DOWN}{RIGHT}{RIGHT}{LEFT}w{ENTER}",
    # 快速填入 (#21): ` then Space opens it, ↑↓ Enter send one, letters and digits narrow it (whatever the case),
    # Backspace, Esc; while typing (sent with what was typed); moved in the symbol keyboard, Space is a symbol
    "` {DOWN}{DOWN}{UP}{DOWN}{ENTER}` em{ENTER}` NAME2{ENTER}` x{BACK}{BACK}{BACK}` {ESC}{ENTER}"
    "ji3` {ENTER}ji3` {ESC}{ENTER}`{RIGHT} `1` z{ENTER}"
    # (a numpad digit narrows it, also while typing; Ctrl + a mark closes it)
    "ji3` em{#1}{ENTER}` {^,}ji3` {^.}",
    # the particles of everyday talk, and the keys next to them
    "cl3xji7{ENTER}2jo4u,7{ENTER}c9 su3cl3{ENTER}i7 97 o7 core i7 {ENTER}",
]

PUNCT = list(",.;?!:\"'[]()<>-_=+/\\|@#$%^&*")


def readings_pool(lexicon):
    """Common phrases with their readings."""
    pool = []
    for reading, lst in lexicon.entries.items():
        for phrase, score in lst[:2]:
            if score > -5.5 and len(phrase) == reading.count("-") + 1:
                pool.append(reading.split("-"))
    return pool


def random_case(rng, pool, english):
    parts = []
    for _ in range(rng.randint(1, 7)):
        r = rng.random()
        if r < 0.55:
            parts.append("".join(reading_to_keys(s) for s in rng.choice(pool)))
        elif r < 0.68:
            w = rng.choice(english)
            if rng.random() < 0.2:
                w = w.capitalize()
            parts.append(w + (" " if rng.random() < 0.6 else ""))
        elif r < 0.73:
            parts.append(rng.choice(PUNCT))
        elif r < 0.77:
            parts.append(str(rng.randint(0, 3000)) + rng.choice(["", " ", "."]))
        elif r < 0.85:
            parts.append(rng.choice(["{DOWN}", "{DOWN}{DOWN}", "{UP}"]) + rng.choice(["1", "2", "3", " ", "{ENTER}",
                                                                                 "{ESC}", "{RIGHT}2", "{PGDN}1"]))
        elif r < 0.89:
            parts.append(rng.choice(["{TAB}", "{RIGHT}", "{LEFT}", "{HOME}", "{END}", "{BACK}", "{STAB}", "{DEL}",
                                     "{LEFT}{UP}{ENTER}", "{LEFT}{UP}{RIGHT}{ENTER}", "{UP}{RIGHT}{ENTER}", "{UP}{RIGHT}{RIGHT}{RIGHT} ", "{LEFT}{LEFT}{UP}{RIGHT}{RIGHT} "]))
        elif r < 0.91:
            parts.append(rng.choice(["{ENTER}{UNDO}", "{ENTER}", "{ESC}", "{CAPS}", "{FW}", "{RESET}",
                                     "addr{DOWN}1", "sig", "su3cl3{DOWN}", "ㄏㄨㄚˊ", "ji3w96j0 {DOWN}1", "w96j0 2k7{DOWN}", "dp32k6ru {DOWN}1", "kfc{DOWN}",
                                     "si3cl3a87", "cl3817", "su3xl3a87{LEFT}{LEFT}{DOWN}", "u.3813ek7",
                                     "si3{DOWN}1", "817{DOWN}{END}{ENTER}", "rup qu0 {LEFT}{DOWN}",
                                     "mp3{DOWN}1", "ps4{LEFT}{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}", "h264g4{LEFT}{LEFT}{UP}{ENTER}"]))
        elif r < 0.93:
            parts.append("`" + rng.choice(["1", "q", "{TAB}x", "{DOWN}{RIGHT} ", "{ESC}", "{PGUP}{ENTER}"]))
        elif r < 0.95:
            parts.append("{#" + rng.choice("0123456789+-*/.") + "}")
        else:
            keys = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz1234567890,./;- ") for _ in range(rng.randint(1, 8)))
            parts.append(keys)
    return "".join(parts)


# settings tried in turn (the user's own dictionary is there for all of them)
CONFIGS = [
    {},
    {"autocorrect": True, "preview": "zhuyin", "punctuation": "full", "space": "full"},
    {"full_width": True, "completion": False, "remember_words": False, "cand_per_row": 9},
    {"import_memory": 1},  # a backup restored (備份與還原): IMPORTED is merged before typing
    {"autocorrect": True},  # 注音自動校正
    {"long_sentence": False},  # 長句選字加強 off ({} above: on, the default)
    {"cand_keys": "1234qweras", "cand_layout": "grid"},  # other candidate keys, a grid (#23)
]
DICTIONARY = {
    "words": [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"], ["史芸", "ㄕˇㄩㄣˊ"], ["好", "ㄏㄠˇ"], ["ㄎㄎ", ""], ["Kazuma", ""],
              ["哲宇", "ㄓㄜˊ ㄩˇ"], ["蹦", "ㄅㄧㄤˋ"], ["電餅", "ㄉㄧㄢˋ ㄅㄧㄤˇ"], ["掰", "ㄅㄞ˙"],
              ["呆呆", "ㄉㄞ ㄉㄞ˙"], ["餅噹", "ㄅㄧㄤˇ ㄉㄤ"], ["大餅店", "ㄉㄚˋ ㄅㄧㄤˇ ㄉㄧㄢˋ"], ["電講", "ㄉㄧㄢˋ ㄍㄧㄤˇ"],
              ["PQC技法的ABC功能", "ㄐㄧˋ ㄈㄚˇ ㄉㄜ˙ ㄍㄨㄥ ㄋㄥˊ"], ["PQC功能", "ㄍㄨㄥ ㄋㄥˊ"], ["AI功能", "ㄍㄨㄥ ㄋㄥˊ"],
              ["阿Q精神", "ㄚ ㄐㄧㄥ ㄕㄣˊ"], ["5G手機", "ㄕㄡˇ ㄐㄧ"], ["大谷翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"],
              ["New York披薩", "ㄆㄧ ㄙㄚˋ"], ["iPhone15", ""], ["拍胸脯保證", "ㄆㄞ ㄒㄩㄥ ㄆㄨˇ ㄅㄠˇ ㄓㄥˋ"],
              ["陳㐀", "ㄔㄣˊ ？"], ["我們㐀", "ㄨㄛˇ ㄇㄣ˙ ？"]],  # (框選查詢 writes ？ for a character it has no reading of)
    "shortcuts": [["addr", "台北市信義區"], ["sig", "祝 順心"], ["cl3", "你好嗎"], ["台灣", "Taiwan"], ["KFC", "肯德基"]],
}

# 快速填入 (quick_fill.json, #21): ` then Space lists them
QUICK_FILL = {"format": 1, "items": [["sample@example.com", "email"], ["王小明", "name1"], ["John", "name2"],
                                    ["(*'∀')~♥", "em1"], ["感謝您的幫忙", "tks"]]}

# 校正記憶 and 代碼記憶 (keys.json), for every setting: si3 is 你, 817 and qu0  as typed (注音自動校正 on);
# mp3 English (on or off); an "eten" part is read by neither and dropped
AUTOCORRECT = {"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 3}, "817": {"": 2}, "qu0 ": {"": 2}, "mp3": {"en": 2}},
                                         "used": {"si3": {"ㄋㄧˇ": 20000}, "817": {"": 20000}, "qu0 ": {"": 20000},
                                                  "mp3": {"en": 20000}}},
               "eten": {"picks": {"cl3": {"": 5}}, "used": {"cl3": {"": 20000}}}}

# (字 and 像 written for good: repick may not put them into 自由, 現象 - variant_is_written, VARIANT_PAIR)
IMPORTED = {"phrases": {"ㄊㄞˊ-ㄨㄢ": {"臺灣": 30}, "ㄋㄧˇ-ㄏㄠˇ": {"妳好": 12}, "ㄗˋ": {"字": 7}, "ㄒㄧㄤˋ": {"像": 8}},
            "english": ["up", "t"],
            "words": {"python": 5}, "emails": {"kazumasan@example.com": 3}}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode("utf-8").split("\n")


def compare(cases, config, tmp):
    script = os.path.join(tmp, "script.txt")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(cases) + "\n")
    for side in ("py", "cpp"):  # each its own files: 全形 on/off is saved in config.json
        os.makedirs(os.path.join(tmp, side), exist_ok=True)
        for name in ("user.json", "keys.json", "custom_picks.json"):
            if os.path.exists(os.path.join(tmp, side, name)):
                os.remove(os.path.join(tmp, side, name))
        files = [("config.json", config), ("dictionary.json", DICTIONARY), ("quick_fill.json", QUICK_FILL)]
        if config.get("import_memory"):
            files.append(("import-memory.json", IMPORTED))
        files.append(("keys.json", AUTOCORRECT))
        for name, data in files:
            with open(os.path.join(tmp, side, name), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
    py = run([sys.executable, os.path.join(ROOT, "tools", "trace.py"), os.path.join(tmp, "py", "user.json"), script,
              os.path.join(tmp, "py", "config.json")])
    cc = run([EXE, os.path.join(ROOT, "data"), os.path.join(tmp, "cpp", "user.json"), script,
              os.path.join(tmp, "cpp", "config.json")])
    case, keys = None, 0
    for i, (a, b) in enumerate(zip(py, cc)):
        if a.startswith("# "):
            case = a[2:]
        else:
            keys += 1
        if a != b:
            print(f"DIFFERENT with {config} in case {case!r} (line {i + 1}):")
            print("  python:", a.replace("\x1f", " / "))
            print("  c++   :", b.replace("\x1f", " / "))
            sys.exit(1)
    if len(py) != len(cc):
        sys.exit(f"DIFFERENT number of lines: python {len(py)}, c++ {len(cc)}")
    # both learned the same things
    with open(os.path.join(tmp, "py", "user.json"), encoding="utf-8") as f:
        learned_py = json.load(f)
    with open(os.path.join(tmp, "cpp", "user.json"), encoding="utf-8") as f:
        learned_cc = json.load(f)
    if learned_py != learned_cc:
        sys.exit(f"DIFFERENT user.json with {config}")
    # (校正記憶 and 代碼記憶 learned the same too)
    with open(os.path.join(tmp, "py", "keys.json"), encoding="utf-8") as f:
        corrected_py = json.load(f)
    with open(os.path.join(tmp, "cpp", "keys.json"), encoding="utf-8") as f:
        corrected_cc = json.load(f)
    if corrected_py != corrected_cc:
        sys.exit(f"DIFFERENT keys.json with {config}")
    # (自訂詞彙 taken with Tab counted the same: custom_picks.json, written once a word is taken)
    picks = []
    for side in ("py", "cpp"):
        path = os.path.join(tmp, side, "custom_picks.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                picks.append(json.load(f))
        else:
            picks.append(None)
    if picks[0] != picks[1]:
        sys.exit(f"DIFFERENT custom_picks.json with {config}")
    return keys


def main(count=2000, seed=1):
    if not os.path.exists(EXE):
        sys.exit(f"build the C++ engine first: {EXE} not found")
    lexicon = Lexicon()
    ranks = load_english()
    english = sorted(ranks, key=ranks.get)[:3000]
    pool = readings_pool(lexicon)
    total, cases_run = 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for config in CONFIGS:
            rng = random.Random(seed)
            cases = CASES + [random_case(rng, pool, english) for _ in range(count)]
            total += compare(cases, config, tmp)
            cases_run += len(cases)
    print(f"same: {cases_run} cases, {total} keys, {len(CONFIGS)} settings")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
