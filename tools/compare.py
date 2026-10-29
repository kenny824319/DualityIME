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
    "abcc12345678@gmail.com{ENTER}gmai", "gpt4 python3 don't{ENTER}gpt4 python3 don't{ENTER}don",
    "{CAPS}cla{CAPS}{TAB}", "claud{TAB}",
    "kaz{TAB}", "kazuma ", "su3kazuma{ENTER}", "KAZ{TAB}", "dd{DOWN}1", "1j8{HOME}{UP}{ENTER}{ENTER}1j8{DOWN}1", "cc{HOME}{UP}{ENTER}{ENTER}su3cc{DOWN}{ENTER}", "1j84{HOME}{UP}{ENTER}", "hello{HOME}{UP}", "ok{HOME}{UP}", "lol{HOME}{UP}", "187", "ji318", "2026 ", "5j", "OK{HOME}{UP}", "dd{HOME}{UP}",
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
    "w96{TAB}{TAB}", "w96{RIGHT}", "internati{TAB}", "abc@gmail.com ", "abc{TAB}", "{CAPS}su3{CAPS}",
    "{CAPS}usb{CAPS} ", "`1", "`{TAB}{DOWN}{ENTER}", "~", "su3{LEFT}{DOWN}2", "su3cl3{HOME}{DOWN}{DOWN}{ENTER}",
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
    "m/4pime103{LEFT}{UP}{RIGHT}{RIGHT}{RIGHT}{ENTER}{HOME}{RIGHT}{UP}{ENTER}", "m/4{#1}{#0}{#4}{DOWN}2", "ji3abc12345678@gmail.com ", "0 5j; pime103{HOME}{DOWN}{ESC}{END}{LEFT}{LEFT}{DOWN}",
    # typing memory: an e-mail typed once is offered (a URL is not); one letter put back as typed is not learned
    "qwmail@example.com{ENTER}qwm{TAB}{ENTER}", "https://example.com/abc {ENTER}htt{TAB}",
    "ji3{ENTER}t {HOME}{UP}{RIGHT}{ENTER}{ENTER}ji3{ENTER}t ",
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
]
DICTIONARY = {
    "words": [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"], ["史芸", "ㄕˇㄩㄣˊ"], ["好", "ㄏㄠˇ"], ["ㄎㄎ", ""], ["Kazuma", ""]],
    "shortcuts": [["addr", "台北市信義區"], ["sig", "祝 順心"], ["cl3", "你好嗎"], ["台灣", "Taiwan"], ["KFC", "肯德基"]],
}

# 校正記憶 and 代碼記憶 (keys.json), for every setting: si3 is 你, 817 and qu0  as typed (注音自動校正 on);
# mp3 English (on or off); an "eten" part is read by neither and dropped
AUTOCORRECT = {"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 3}, "817": {"": 2}, "qu0 ": {"": 2}, "mp3": {"en": 2}},
                                         "used": {"si3": {"ㄋㄧˇ": 20000}, "817": {"": 20000}, "qu0 ": {"": 20000},
                                                  "mp3": {"en": 20000}}},
               "eten": {"picks": {"cl3": {"": 5}}, "used": {"cl3": {"": 20000}}}}

IMPORTED = {"phrases": {"ㄊㄞˊ-ㄨㄢ": {"臺灣": 30}, "ㄋㄧˇ-ㄏㄠˇ": {"妳好": 12}}, "english": ["up", "t"],
            "words": {"python": 5}, "emails": {"kazumasan@example.com": 3}}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode("utf-8").split("\n")


def compare(cases, config, tmp):
    script = os.path.join(tmp, "script.txt")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(cases) + "\n")
    for side in ("py", "cpp"):  # each its own files: 全形 on/off is saved in config.json
        os.makedirs(os.path.join(tmp, side), exist_ok=True)
        for name in ("user.json", "keys.json"):
            if os.path.exists(os.path.join(tmp, side, name)):
                os.remove(os.path.join(tmp, side, name))
        files = [("config.json", config), ("dictionary.json", DICTIONARY)]
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
