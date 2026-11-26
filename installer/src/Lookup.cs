// 兩儀輸入法 — 框選查詢: select some text, tap Ctrl twice (the IME starts this
// program), and a small window shows its zhuyin, reads it aloud, and can add
// it to the 個人詞庫 (dictionary.json) as a word or as a shortcut's text.
// The selection is read through UI Automation, never through the clipboard,
// so whatever the user copied stays where it is.
// Mostly written with Claude (claude-opus-5-5).
//
//   lookup.exe                 look up the selection in the focused program
//   lookup.exe --text 華碩     look up this text (testing)
//   lookup.exe --print 華碩    print the zhuyin and exit (testing)
//   lookup.exe --add-word 華碩, --add-shortcut addr 台北市
//                              add to the 個人詞庫 and print what happened (testing)
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Speech.Synthesis;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

public static class Lookup {  // (public: tools\lookup_test.cs tests its Japanese, #36 plan 7)
    const int MaxPhrase = 8, MaxWord = 8, MaxText = 255, MaxKeys = 8;
    static readonly string DataDir = Path.Combine(
        Environment.GetEnvironmentVariable("APPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DualityIME");

    [STAThread]
    static int Main(string[] args) {
        string text = null;
        if (args.Length >= 2 && args[0] == "--print") return Print(Zhuyin(args[1]));
        if (args.Length >= 2 && args[0] == "--add-word") {
            string word = AsWord(args[1]);
            if (!CanBeWord(word)) { Print("「" + word + "」不能加入自訂詞彙"); return 1; }
            return Print(AddWord(word, Zhuyin(word)));
        }
        if (args.Length >= 3 && args[0] == "--add-shortcut") return Print(AddShortcut(args[1], args[2]));
        if (args.Length >= 2 && args[0] == "--text") text = args[1];
        // one window at a time: a double Ctrl tap could start several of us at once. Made by a
        // lookup.exe running as administrator (started from such a program), the mutex and the event
        // are its own: then this one just opens its window, without the one-window rule
        bool first = true;
        try {
            single = new Mutex(true, @"Local\DualityIME.Lookup", out first);
            CloseRequest = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\DualityIME.Lookup.Close");
        } catch (Exception) {
            first = true;
            CloseRequest = null;
        }
        if (!first) {
            // another one: a window of ours left open (behind another program, say) is asked to close
            // and this takes its place; one still starting drops the request when its window comes
            // up (LookupWindow.OnShown) and keeps its turn, as does one asking for a 映射詞彙 code.
            // (Asked through an event of our own: its window has a hidden owner, so CloseMainWindow
            // cannot find it)
            CloseRequest.Set();
            // (3 s: closing while it reads aloud, then the process exiting, can take more than a second)
            try { first = single.WaitOne(3000); } catch (AbandonedMutexException) { first = true; }
        }
        if (!first) return 0;
        if (text == null) text = SelectedTextWithin(2000);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new LookupWindow((text ?? "").Trim()));
        return 0;
    }

    static int Print(string s) {
        // (a window program has no console of its own: write the bytes to whatever stdout is)
        byte[] bytes = Encoding.UTF8.GetBytes(s + "\n");
        using (Stream o = Console.OpenStandardOutput()) o.Write(bytes, 0, bytes.Length);
        return 0;
    }

    static Mutex single;  // (held for the whole run)
    // set by a lookup started while this one's window is up: the window closes (null: none to share)
    public static EventWaitHandle CloseRequest;

    // ---- the selection ----------------------------------------------------------------
    // Through UI Automation's own COM interface: System.Windows.Automation's TextPatternRange.GetText
    // crashes inside UIAutomationCore.dll (7.2.22621.4249: an access violation no catch stops) in
    // every program, so the window never came up (2026-11-04).
    const int UIA_TextPatternId = 10014;
    static readonly Guid CUIAutomation = new Guid("ff48dba4-60ef-4201-aa87-54103eef594e");

    // On a thread of its own in the MTA (Microsoft's advice for UI Automation clients: not from a UI
    // thread), and no longer than ms: a program slow to answer gives "沒有選取文字" instead of a
    // lookup.exe left waiting with the one-window mutex
    static string SelectedTextWithin(int ms) {
        string found = null;
        var t = new Thread(() => { found = SelectedText(); });
        t.IsBackground = true;  // (still waiting when the window closes: it does not keep us running)
        t.SetApartmentState(ApartmentState.MTA);
        t.Start();
        return t.Join(ms) ? found : null;
    }

    static string SelectedText() {
        try {
            var uia = (IUIAutomation)Activator.CreateInstance(Type.GetTypeFromCLSID(CUIAutomation));
            IUIAutomationElement e;
            IUIAutomationTreeWalker walker;
            if (uia.GetFocusedElement(out e) != 0 || uia.get_RawViewWalker(out walker) != 0) return null;
            // the focused element, or a parent that holds the selection: in Chrome a heading of a GitHub
            // README is 7 levels under the document that has it, and the control view skips the
            // document (2026-11-04); the first one with selected text
            for (int up = 0; e != null && up < 20; up++) {
                object pattern;
                var text = e.GetCurrentPattern(UIA_TextPatternId, out pattern) == 0 ? pattern as IUIAutomationTextPattern : null;
                IUIAutomationTextRangeArray ranges;
                int count;
                IUIAutomationTextRange range;
                string s;
                if (text != null && text.GetSelection(out ranges) == 0 && ranges != null &&
                    ranges.get_Length(out count) == 0 && count > 0 && ranges.GetElement(0, out range) == 0 && range != null &&
                    range.GetText(MaxText + 1, out s) == 0 && !string.IsNullOrWhiteSpace(s))
                    return s;
                IUIAutomationElement parent;
                if (walker.GetParentElement(e, out parent) != 0) break;
                e = parent;
            }
        } catch (Exception) { }
        return null;
    }

    // ---- zhuyin, from the IME's own dictionary ----------------------------------------------
    static Dictionary<string, string> readings;
    static string LexiconPath() {
        string here = AppDomain.CurrentDomain.BaseDirectory;
        foreach (string p in new[] { Path.Combine(here, @"data\lexicon.txt"), Path.Combine(here, @"..\data\lexicon.txt") })
            if (File.Exists(p)) return p;
        return null;
    }
    static void LoadReadings() {
        if (readings != null) return;
        readings = new Dictionary<string, string>();
        var best = new Dictionary<string, double>();
        string path = LexiconPath();
        if (path == null) return;
        foreach (string line in File.ReadLines(path, Encoding.UTF8)) {
            string[] f = line.Split('\t');
            double score;
            if (f.Length < 3 || !double.TryParse(f[2], NumberStyles.Float, CultureInfo.InvariantCulture, out score)) continue;
            double old;
            if (!best.TryGetValue(f[1], out old) || score > old) { best[f[1]] = score; readings[f[1]] = f[0]; }
        }
    }
    public static bool IsHan(char c) { return c >= 0x3400 && c <= 0x9FFF || c >= 0xF900 && c <= 0xFAFF; }

    // Longest dictionary words first, so 重 in 重要 and in 重新 get their own readings.
    // Returns syllables separated by spaces ("" for text with no Chinese in it).
    public static string Zhuyin(string text) {
        LoadReadings();
        var parts = new List<string>();
        int i = 0;
        while (i < text.Length) {
            if (!IsHan(text[i])) { i++; continue; }
            int len = Math.Min(MaxPhrase, text.Length - i);
            for (; len > 1; len--)
                if (readings.ContainsKey(text.Substring(i, len))) break;
            string r;
            parts.Add(readings.TryGetValue(text.Substring(i, len), out r) ? r.Replace("-", " ") : "？");
            i += len;
        }
        return string.Join(" ", parts);
    }

    // ---- 日文 (#36 plan 7): only with 日文輸入 on (the user 2026-11-24) ----------------------------------------------
    public static bool JapaneseOn() { return Setting("ja_enabled", "False") == "True"; }

    static string DataFile(string name) {
        string here = AppDomain.CurrentDomain.BaseDirectory;
        foreach (string p in new[] { Path.Combine(here, "data", name), Path.Combine(here, "..", "data", name) })
            if (File.Exists(p)) return p;
        return null;
    }
    static bool IsKana(char c) { return c >= 0x3041 && c <= 0x3096 || c >= 0x30A1 && c <= 0x30F6 || c == 0x30FC; }
    public static bool HasKana(string s) { return s.Any(IsKana); }
    // A selection read as Japanese - the Japanese voice, no 注音 - when it has kana, whether a reading came or not (digits,
    // a long passage: findings #219)
    public static bool IsJapanese(string text, bool japaneseOn) { return japaneseOn && JapaneseKana(text ?? ""); }
    // Kana that make a selection Japanese: more than a lone の or ノ - Chinese written 好吃の店 has one (findings #220)
    static bool JapaneseKana(string s) {
        var kana = s.Where(IsKana).ToList();
        return kana.Count >= 2 || kana.Any(c => c != 'の' && c != 'ノ');
    }
    // The Chinese row (注音, 朗讀 in Chinese, 加入自訂詞彙) too: a selection with 注音 that is not Japanese, or Japanese no
    // reading came for - a lone の in Chinese (好吃の店) is no reason to drop it (findings #220)
    public static bool ChineseRow(string text, bool japaneseOn) {
        return ChineseRow(Zhuyin(text ?? ""), IsJapanese(text, japaneseOn), JaReadings(text, japaneseOn));
    }
    // (the window's own call, with what it has worked out already - the same rule, findings #229)
    public static bool ChineseRow(string zhuyin, bool japanese, List<string> readings) {
        return zhuyin.Length > 0 && (!japanese || readings.Count == 0);
    }
    static string Hiragana(string s) {
        return new string(s.Select(c => c >= 0x30A1 && c <= 0x30F6 ? (char)(c - 0x60) : c).ToArray());
    }

    // The Japanese readings of a selection (hiragana), or none: all kana - itself in hiragana; else the words of
    // data\ja_dict.bin written exactly so, their readings cheapest first, at most 3 (tools/build_ja_dict.py's JD01 layout)
    // - the marks around it kept (大丈夫？, findings #216); else, with kana in it (the spec's 有假名的, findings #209:
    // 日本語を話します, ありがとう。), the reading of the cheapest way to cut it into words: each word's cost and the
    // connection costs between them (data\ja_conn.bin), as the engine's conversion does it the other way round
    // (findings #218: 見に行こう reads いこう, not each word's own cheapest), a kana no word holds as itself, 。、？！ kept,
    // other marks, spaces, letters and emoji left out; none when a kanji is in no word, or with digits (a number's
    // reading is not the digits', findings #217).
    const int PieceMax = 12, SentenceMax = 60;  // (the longest word tried at a place; a longer selection is not read)
    const int PieceFallback = 20000;  // (a character no word holds, as engine/ja_convert.py FALLBACK_COST)
    static bool IsMark(char c) { return char.IsPunctuation(c) || char.IsSymbol(c) || char.IsWhiteSpace(c); }

    sealed class PieceCell {
        public long Cost;
        public int Rid;
        public PieceCell Prev;
        public string Reading;
    }

    public static List<string> JaReadings(string text, bool japaneseOn) {
        var none = new List<string>();
        text = (text ?? "").Trim();
        if (!japaneseOn || text.Length == 0) return none;
        if (text.All(IsKana)) return new List<string> { Hiragana(text) };
        int a = 0, b = text.Length;
        while (a < b && IsMark(text[a])) a++;
        while (b > a && IsMark(text[b - 1])) b--;
        string core = text.Substring(a, b - a);
        if (core.Length == 0) return none;
        string lead = new string(text.Substring(0, a).Where(RomajiPunct.ContainsKey).ToArray());
        string trail = new string(text.Substring(b).Where(RomajiPunct.ContainsKey).ToArray());
        string path = DataFile("ja_dict.bin");
        if (path == null) return none;
        byte[] d;
        try { d = File.ReadAllBytes(path); } catch (Exception) { return none; }
        if (d.Length < 16 || Encoding.ASCII.GetString(d, 0, 4) != "JD01") return none;
        int nRead = BitConverter.ToInt32(d, 4), nEnt = BitConverter.ToInt32(d, 8), unknown = BitConverter.ToUInt16(d, 14);
        int pos = 16, roff = pos, estart = roff + 4 * (nRead + 1), ents = estart + 4 * (nRead + 1);
        int rUnitsAt = ents + 12 * nEnt;
        int rUnits = BitConverter.ToInt32(d, rUnitsAt), rblob = rUnitsAt + 4;
        int sraw = rblob + 2 * rUnits + 4;
        bool piece = JapaneseKana(text) && text.Length <= SentenceMax && !text.Any(char.IsDigit);
        // (the words a reading could be made of: every part of the selection up to PieceMax long - and the word itself)
        var parts = new HashSet<string> { core };
        if (piece)
            for (int i = 0; i < text.Length; i++)
                for (int len = 1; len <= PieceMax && i + len <= text.Length; len++) parts.Add(text.Substring(i, len));
        var lengths = new HashSet<int>(parts.Select(p => p.Length));
        var found = new List<KeyValuePair<int, string>>();  // (cost, reading) of the words written as the word itself
        var words = new Dictionary<string, List<int[]>>();  // part -> its entries: lid, rid, cost, reading's index
        var readingOf = new List<string>();
        for (int i = 0; i < nRead; i++) {
            int e0 = BitConverter.ToInt32(d, estart + 4 * i), e1 = BitConverter.ToInt32(d, estart + 4 * (i + 1));
            for (int e = e0; e < e1; e++) {
                int at = ents + 12 * e;
                int slen = BitConverter.ToUInt16(d, at + 6);
                if (!lengths.Contains(slen)) continue;
                string surface = Encoding.Unicode.GetString(d, sraw + 2 * BitConverter.ToInt32(d, at + 8), 2 * slen);
                if (!parts.Contains(surface)) continue;
                int r0 = BitConverter.ToInt32(d, roff + 4 * i), r1 = BitConverter.ToInt32(d, roff + 4 * (i + 1));
                string reading = Encoding.Unicode.GetString(d, rblob + 2 * r0, 2 * (r1 - r0));
                if (!reading.All(IsKana)) continue;
                int cost = BitConverter.ToInt16(d, at + 4);
                if (surface == core) found.Add(new KeyValuePair<int, string>(cost, reading));
                List<int[]> list;
                if (!words.TryGetValue(surface, out list)) words[surface] = list = new List<int[]>();
                readingOf.Add(reading);
                list.Add(new[] { BitConverter.ToUInt16(d, at), BitConverter.ToUInt16(d, at + 2), cost, readingOf.Count - 1 });
            }
        }
        if (found.Count > 0)
            return found.OrderBy(f => f.Key).Select(f => lead + f.Value + trail).Distinct().Take(3).ToList();
        if (!piece) return none;
        // the connection costs (JC01: uint16 n, uint16 0, int16 cost[rid * n + lid]); none there: the words' costs alone
        byte[] conn = null;
        int n = 0;
        string cpath = DataFile("ja_conn.bin");
        try {
            if (cpath != null) {
                conn = File.ReadAllBytes(cpath);
                if (conn.Length < 8 || Encoding.ASCII.GetString(conn, 0, 4) != "JC01") conn = null;
                else n = BitConverter.ToUInt16(conn, 4);
            }
        } catch (Exception) { conn = null; }
        Func<int, int, int> link = (rid, lid) =>
            conn != null && rid < n && lid < n ? BitConverter.ToInt16(conn, 8 + 2 * (rid * n + lid)) : 0;
        var ends = new Dictionary<int, PieceCell>[text.Length + 1];  // position -> right id -> the cheapest way there
        for (int i = 0; i <= text.Length; i++) ends[i] = new Dictionary<int, PieceCell>();
        ends[0][0] = new PieceCell { Cost = 0, Rid = 0, Reading = "" };
        for (int i = 0; i < text.Length; i++) {
            if (ends[i].Count == 0) continue;
            var nodes = new List<Tuple<int, int, int, int, string>>();  // (length, lid, rid, cost, reading)
            for (int len = 1; len <= PieceMax && i + len <= text.Length; len++) {
                List<int[]> list;
                if (words.TryGetValue(text.Substring(i, len), out list))
                    foreach (int[] w in list) nodes.Add(Tuple.Create(len, w[0], w[1], w[2], readingOf[w[3]]));
            }
            char c = text[i];
            bool pair = char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]);
            if (IsKana(c)) nodes.Add(Tuple.Create(1, unknown, unknown, PieceFallback, Hiragana(c.ToString())));
            else if (RomajiPunct.ContainsKey(c)) nodes.Add(Tuple.Create(1, unknown, unknown, PieceFallback, c.ToString()));
            else if (pair) {  // (a kanji outside the BMP - U+20000..U+3FFFF - no word holds: none, findings #213; an emoji
                              // left out, #214)
                int cp = char.ConvertToUtf32(c, text[i + 1]);
                if (cp < 0x20000 || cp > 0x3FFFF) nodes.Add(Tuple.Create(2, unknown, unknown, PieceFallback, ""));
            } else if (!IsHan(c)) nodes.Add(Tuple.Create(1, unknown, unknown, PieceFallback, ""));  // (a mark, a letter)
            foreach (var node in nodes)
                foreach (PieceCell prev in ends[i].Values) {
                    long total = prev.Cost + link(prev.Rid, node.Item2) + node.Item4;
                    PieceCell was;
                    var into = ends[i + node.Item1];
                    if (!into.TryGetValue(node.Item3, out was) || total < was.Cost)
                        into[node.Item3] = new PieceCell { Cost = total, Rid = node.Item3, Prev = prev, Reading = node.Item5 };
                }
        }
        PieceCell end = null;
        long endCost = 0;
        foreach (PieceCell cell in ends[text.Length].Values) {
            long total = cell.Cost + link(cell.Rid, 0);
            if (end == null || total < endCost) { end = cell; endCost = total; }
        }
        if (end == null) return none;  // (a kanji no word holds)
        var pieces = new List<string>();
        for (PieceCell cell = end; cell != null && cell.Prev != null; cell = cell.Prev) pieces.Add(cell.Reading);
        pieces.Reverse();
        string joined = string.Concat(pieces);
        return joined.Length > 0 ? new List<string> { joined } : none;
    }

    // 加入自訂詞彙（日文） from a selection (findings #212): the word without its marks, symbols and spaces (everything else
    // kept - a kanji of two UTF-16 halves, 〇: findings #213), the reading its hiragana and ー - a pieced reading keeps
    // 。、？！, which a 日文自訂詞彙's reading may not hold.
    public static Tuple<string, string> JaWordToAdd(string text, string reading) {
        // (and the invisible format characters a page's text carries - a zero-width space, a soft hyphen: findings #214;
        // outside the BMP, as the pieced reading: a kanji kept, an emoji left out - findings #215)
        var word = new StringBuilder();
        text = text ?? "";
        for (int i = 0; i < text.Length; i++) {
            char c = text[i];
            if (char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1])) {
                int cp = char.ConvertToUtf32(c, text[i + 1]);
                if (cp >= 0x20000 && cp <= 0x3FFFF) word.Append(c).Append(text[i + 1]);
                i++;
            } else if (!char.IsSurrogate(c) && !char.IsPunctuation(c) && !char.IsSymbol(c) && !char.IsSeparator(c) &&
                       !char.IsWhiteSpace(c) && !char.IsControl(c) && CharUnicodeInfo.GetUnicodeCategory(c) != UnicodeCategory.Format)
                word.Append(c);
        }
        string kana = new string((reading ?? "").Where(c => c >= 0x3041 && c <= 0x3096 || c == 'ー').ToArray());
        return Tuple.Create(word.ToString(), kana);
    }

    // The keys that type a reading (engine/ja_romaji.py to_romaji over romaji_table, data\ja_romaji.tsv): ん nn, っ the
    // next spelling's first letter (xtu before a vowel or n), ー - and 。、？！ as typed; "" when a character has none.
    static Dictionary<string, string> spellings;
    static readonly Dictionary<char, string> RomajiPunct = new Dictionary<char, string> {
        { '。', "." }, { '、', "," }, { '？', "?" }, { '！', "!" }, { 'ー', "-" } };
    static void LoadSpellings() {
        if (spellings != null) return;
        spellings = new Dictionary<string, string>();
        string path = DataFile("ja_romaji.tsv");
        if (path == null) return;
        Func<string, Tuple<bool, int, string>> key = inp => Tuple.Create(inp.Any(c => "cqxlv".IndexOf(c) >= 0), inp.Length, inp);
        foreach (string line in File.ReadLines(path, Encoding.UTF8)) {
            string[] f = line.Split('\t');
            if (f.Length < 2 || (f.Length > 2 && f[2].Length > 0)) continue;  // (a rule leaving letters pending: none)
            string inp = f[0], outp = f[1];
            if (inp.Length == 0 || !inp.All(c => c >= 'a' && c <= 'z') || outp == "ん" || outp == "っ") continue;
            string cur;
            if (!spellings.TryGetValue(outp, out cur) || Comparer<Tuple<bool, int, string>>.Default.Compare(key(inp), key(cur)) < 0)
                spellings[outp] = inp;
        }
    }
    public static string Romaji(string reading) {
        LoadSpellings();
        var outp = new StringBuilder();
        int i = 0;
        while (i < reading.Length) {
            char c = reading[i];
            if (c == 'ん') { outp.Append("nn"); i++; continue; }
            if (c == 'っ') {
                string next = null;
                foreach (int k in new[] { 2, 1 })
                    if (i + 1 + k <= reading.Length && spellings.TryGetValue(reading.Substring(i + 1, k), out next)) break;
                    else next = null;
                outp.Append(next != null && "aiueon".IndexOf(next[0]) < 0 ? next.Substring(0, 1) : "xtu");
                i++;
                continue;
            }
            string p;
            if (RomajiPunct.TryGetValue(c, out p)) { outp.Append(p); i++; continue; }
            bool done = false;
            foreach (int k in new[] { 2, 1 }) {
                string sp;
                if (i + k <= reading.Length && spellings.TryGetValue(reading.Substring(i, k), out sp)) {
                    outp.Append(sp);
                    i += k;
                    done = true;
                    break;
                }
            }
            if (!done) return "";
        }
        return outp.ToString();
    }

    // 加入自訂詞彙（日文）: into ja_dictionary.json (the file the settings window's 日文 自訂詞彙 edits - Settings.cs
    // SaveJaWords, the same rules); one reading may hold several words (the user 2026-11-26): a word of a reading there
    // already goes after its words, the same word and reading once
    public static string AddJaWord(string word, string reading) { return AddJaWord(word, reading, DataDir); }
    public static string AddJaWord(string word, string reading, string dir) {
        word = (word ?? "").Trim();
        reading = (reading ?? "").Trim();
        if (word.Length == 0 || word.Length > 30) return "「" + word + "」不能加入自訂詞彙（日文）";
        if (reading.Length == 0 || reading.Length > 30 || !reading.All(c => c >= 0x3041 && c <= 0x3096 || c == 0x30FC))
            return "讀音只能用平假名（長音用ー）";
        string path = Path.Combine(dir, "ja_dictionary.json");
        var rows = new List<object>();
        try {
            var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
            object w;
            if (j != null && j.TryGetValue("words", out w) && w is ArrayList)
                foreach (object r in (ArrayList)w) {
                    var pair = r as ArrayList;
                    if (pair != null && pair.Count == 2 && pair[0] is string && pair[1] is string) rows.Add(new object[] { pair[0], pair[1] });
                }
        } catch (Exception) { }
        foreach (object[] r in rows)
            if ((string)r[0] == word && (string)r[1] == reading)
                return "「" + word + "」（" + reading + "）已經在自訂詞彙（日文）裡";
        rows.Add(new object[] { word, reading });
        try {
            Directory.CreateDirectory(dir);
            File.WriteAllText(path, new JavaScriptSerializer().Serialize(new Dictionary<string, object> { { "format", 1 }, { "words", rows } }),
                              new UTF8Encoding(false));
        } catch (Exception ex) { return "無法加入：" + ex.Message; }
        return "已加入自訂詞彙（日文）：" + word + "（" + reading + "）";
    }

    // ---- 個人詞庫 (the same file the settings window edits) ------------------------------------
    static string DictionaryPath { get { return Path.Combine(DataDir, "dictionary.json"); } }
    static Dictionary<string, object> ReadDictionary() {
        try {
            var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(DictionaryPath, Encoding.UTF8));
            if (d != null) return d;
        } catch (Exception) { }
        return new Dictionary<string, object>();
    }
    static List<string[]> Pairs(Dictionary<string, object> d, string kind) {
        var list = new List<string[]>();
        object v;
        if (d.TryGetValue(kind, out v) && v is IEnumerable)
            foreach (object pair in (IEnumerable)v) {
                var p = pair as object[] ?? (pair as ArrayList != null ? ((ArrayList)pair).ToArray() : null);
                if (p != null && p.Length == 2) list.Add(new[] { Convert.ToString(p[0]), Convert.ToString(p[1]) });
            }
        return list;
    }
    static string Json(string s) { return new JavaScriptSerializer().Serialize(s); }
    static void WriteDictionary(List<string[]> words, List<string[]> shortcuts) {
        var sb = new StringBuilder("{\n");
        foreach (var kind in new[] { Tuple.Create("words", words), Tuple.Create("shortcuts", shortcuts) }) {
            sb.Append("  \"").Append(kind.Item1).Append("\": [\n");
            sb.Append(string.Join(",\n", kind.Item2.Select(p => "    [" + Json(p[0]) + ", " + Json(p[1]) + "]")));
            sb.Append(kind.Item1 == "words" ? "\n  ],\n" : "\n  ]\n");
        }
        sb.Append("}\n");
        Directory.CreateDirectory(DataDir);
        string tmp = DictionaryPath + ".tmp";
        File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
        if (File.Exists(DictionaryPath)) File.Replace(tmp, DictionaryPath, null); else File.Move(tmp, DictionaryPath);
    }
    // Returns a message for the user.
    public static string AddWord(string word, string zhuyin) {
        var d = ReadDictionary();
        var words = Pairs(d, "words");
        // (an English word, or the English of a mixed one, is the same whatever its case)
        if (words.Any(p => p[0] == word ||
                           (IsEnglishWord(word) || IsMixedWord(word)) && p[0].Equals(word, StringComparison.OrdinalIgnoreCase)))
            return "「" + word + "」已經在自訂詞彙裡了";
        words.Add(new[] { word, zhuyin });
        WriteDictionary(words, Pairs(d, "shortcuts"));
        return "已加入自訂詞彙";
    }
    public static string AddShortcut(string keys, string text) {
        var d = ReadDictionary();
        var shortcuts = Pairs(d, "shortcuts");
        if (shortcuts.Any(p => p[0].Equals(keys, StringComparison.OrdinalIgnoreCase))) return "捷徑「" + keys + "」已經用過了";
        if (shortcuts.Any(p => p[1] == text)) return "這段文字已經有捷徑了";
        shortcuts.Add(new[] { keys, text });
        WriteDictionary(Pairs(d, "words"), shortcuts);
        return "已加入映射詞彙";
    }
    // A word for 自訂詞彙: Chinese (with its 注音), an English word (no 注音), or one mixing them (the 注音 of its
    // Chinese characters), as the settings page takes them (settings/ui/app.js checkRow); s as AsWord gives it
    public static bool CanBeWord(string s) {
        return s.Length > 0 && s.Length <= MaxWord && s.All(IsHan) || IsEnglishWord(s) || IsMixedWord(s);
    }
    // what is selected as a word: no space at either end, one space where there were several (PQC 功能)
    public static string AsWord(string s) { return string.Join(" ", s.Split((char[])null, StringSplitOptions.RemoveEmptyEntries)); }
    static bool Letter(char c) { return c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z'; }
    static bool Alnum(char c) { return Letter(c) || c >= '0' && c <= '9'; }
    // - or ' between letters or digits
    static bool Joins(string s, int i) { return (s[i] == '-' || s[i] == '\'') && i > 0 && i < s.Length - 1 && Alnum(s[i - 1]) && Alnum(s[i + 1]); }
    // a letter first, letters and digits, - or ' between them (Kazuma, e-sports, rock'n'roll, iPhone15), 2 to 32
    public static bool IsEnglishWord(string s) {
        if (s.Length < 2 || s.Length > 32 || !Letter(s[0]) || !Alnum(s[s.Length - 1])) return false;
        for (int i = 0; i < s.Length; i++)
            if (!Alnum(s[i]) && !Joins(s, i)) return false;
        return true;
    }
    // Chinese with English letters or digits (PQC技法的ABC功能, PQC 功能): engine/lexicon.py is_custom_mixed
    public static bool IsMixedWord(string s) {
        int han = s.Count(IsHan);
        if (s.Length > 32 || han == 0 || han > MaxWord || !s.Any(Alnum) || s.StartsWith(" ") || s.EndsWith(" ") || s.Contains("  "))
            return false;
        for (int i = 0; i < s.Length; i++)
            if (!IsHan(s[i]) && !Alnum(s[i]) && s[i] != ' ' && !Joins(s, i)) return false;
        return true;
    }
    public static bool CanBeShortcutText(string s) { return s.Length > 0 && s.Length <= MaxText; }
    public static bool IsShortcutKeys(string s) {
        // English letters and digits (typed keys), or Chinese (the characters shown): not both
        return s.Length > 0 && s.Length <= MaxKeys && (s.All(c => c < 128 && char.IsLetterOrDigit(c)) || s.All(IsHan));
    }

    // ---- settings ------------------------------------------------------------------------
    public static string Setting(string name, string fallback) {
        try {
            var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                File.ReadAllText(Path.Combine(DataDir, "config.json"), Encoding.UTF8));
            object v;
            if (d != null && d.TryGetValue(name, out v) && v != null) return Convert.ToString(v);
        } catch (Exception) { }
        return fallback;
    }
    public static bool DarkMode() {
        object v = Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1);
        return v is int && (int)v == 0;
    }
}

class LookupWindow : Form {
    readonly string text, zhuyin;
    readonly List<string> ja;  // (#36 plan 7: its Japanese readings, with 日文輸入 on)
    readonly Label status;
    readonly Color bg, fg, sub, accent, line;
    SpeechSynthesizer voice;
    bool asking;  // a dialog of ours is up: losing focus to it must not close the window

    public LookupWindow(string text) {
        this.text = text.Length > 255 ? text.Substring(0, 255) : text;
        zhuyin = Lookup.Zhuyin(this.text);
        bool japaneseOn = Lookup.JapaneseOn();
        ja = Lookup.JaReadings(this.text, japaneseOn);
        bool kana = Lookup.IsJapanese(this.text, japaneseOn);  // (with kana: Japanese - its reading, not 注音)
        bool dark = Lookup.DarkMode();
        bg = dark ? Color.FromArgb(0x2b, 0x2b, 0x2b) : Color.White;
        fg = dark ? Color.FromArgb(0xf3, 0xf3, 0xf3) : Color.FromArgb(0x1b, 0x1b, 0x1b);
        sub = dark ? Color.FromArgb(0xa8, 0xa8, 0xa8) : Color.FromArgb(0x61, 0x61, 0x61);
        accent = dark ? Color.FromArgb(0x4c, 0xc2, 0xff) : Color.FromArgb(0x00, 0x67, 0xc0);
        line = dark ? Color.FromArgb(0x3b, 0x3b, 0x3b) : Color.FromArgb(0xe2, 0xe2, 0xe2);
        float scale;
        using (var g = CreateGraphics()) scale = g.DpiX / 96f;
        string size = Lookup.Setting("lookup_size", "small");
        float zoom = size == "large" ? 1.5f : size == "medium" ? 1.25f : 1f;
        Func<float, int> px = v => (int)Math.Round(v * scale * zoom);

        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false; TopMost = true; KeyPreview = true;
        StartPosition = FormStartPosition.Manual;
        BackColor = bg; ForeColor = fg;
        Text = "框選查詢";
        Font = new Font("Microsoft JhengHei UI", 10f * zoom);
        int w = px(400), y = px(14);

        Action<Control> add = c => Controls.Add(c);
        if (this.text.Length == 0) {
            add(MakeLabel("沒有選取文字", new Font(Font.FontFamily, 12f * zoom, FontStyle.Bold), fg, px(16), y, w - px(32), px(28)));
            y += px(32);
            add(MakeLabel("先選取一段文字再連按兩次 Ctrl。有些程式不提供選取的文字，這時查不到。", Font, sub, px(16), y, w - px(32), px(44)));
            y += px(52);
        } else {
            var big = new Font(Font.FontFamily, 18f * zoom, FontStyle.Bold);
            int h = this.text.Length > 12 ? px(64) : px(36);
            add(MakeLabel(this.text, big, fg, px(16), y, w - px(32), h));
            y += h + px(4);
            bool chinese = Lookup.ChineseRow(zhuyin, kana, ja);  // (findings #220, #229)
            string reading = chinese ? zhuyin : kana ? "" : (IsEnglish(this.text) ? "英文" : "");
            if (reading.Length > 0) {
                add(MakeLabel(reading, new Font(Font.FontFamily, 12f * zoom), accent, px(16), y, w - px(32), px(28)));
                y += px(34);
            }
            // 日文 (#36 plan 7): its reading and the romaji that types it - under the 注音 of kanji alone (今日 →
            // ㄐㄧㄣ ㄖˋ, then きょう kyou), in its place for text with kana
            // (the commonest reading alone, as the design's example: three ran past the window with rare ones - 今日 いまたち)
            string jaLine = ja.Count > 0 ? (ja[0] + " " + Lookup.Romaji(ja[0])).Trim() : "";
            if (jaLine.Length > 0) {
                add(MakeLabel(jaLine, new Font(Font.FontFamily, 12f * zoom), accent, px(16), y, w - px(32), px(28)));
                y += px(34);
            }
            int x = px(16);
            string word = Lookup.AsWord(this.text);
            if (!kana || chinese) {
                x = AddButton("🔊 朗讀", x, y, px(96), px(32), (s, e) => Speak(zhuyin.Length > 0 ? "zh-TW" : "en-US"));
                if (Lookup.CanBeWord(word))
                    x = AddButton("加入自訂詞彙", x, y, px(120), px(32), (s, e) => Report(Lookup.AddWord(word, Lookup.Zhuyin(word))));
                if (Lookup.CanBeShortcutText(this.text))
                    AddButton("加入映射詞彙", x, y, px(120), px(32), (s, e) => AskShortcut());
                y += px(40);
                x = px(16);
            }
            if (ja.Count > 0 || kana) {  // (the Japanese buttons: alone for text with kana, a row of their own under kanji
                // alone; no reading - digits, a long passage - the voice alone, findings #219)
                bool alone = kana && !chinese;  // (the Japanese row alone: 朗讀 is the Japanese voice)
                x = AddButton(alone ? "🔊 朗讀" : "🔊 日文朗讀", x, y, px(alone ? 96 : 120), px(32), (s, e) => Speak("ja-JP"));
                if (ja.Count > 0) {
                    var toAdd = Lookup.JaWordToAdd(this.text, ja[0]);
                    AddButton("加入自訂詞彙（日文）", x, y, px(170), px(32), (s, e) => Report(Lookup.AddJaWord(toAdd.Item1, toAdd.Item2)));
                }
                y += px(40);
            }
        }
        status = MakeLabel("", Font, sub, px(16), y, w - px(32), px(24));
        add(status);
        y += px(28);
        ClientSize = new Size(w, y);

        // next to the mouse, inside the screen
        Point m = Cursor.Position;
        Rectangle area = Screen.FromPoint(m).WorkingArea;
        Location = new Point(Math.Max(area.Left, Math.Min(m.X + px(12), area.Right - Width)),
                             Math.Max(area.Top, Math.Min(m.Y + px(16), area.Bottom - Height)));
        KeyDown += (s, e) => { if (e.KeyCode == Keys.Escape) Close(); };
        Deactivate += (s, e) => { if (!asking) Close(); };
        Paint += (s, e) => { using (var p = new Pen(line)) e.Graphics.DrawRectangle(p, 0, 0, Width - 1, Height - 1); };
    }

    protected override void OnShown(EventArgs e) {
        base.OnShown(e);
        Activate();  // so Esc and clicking elsewhere work
        // another lookup asks this one to make way (Lookup.CloseRequest). A request sent while this
        // one was still starting (reading a slow program's selection) is dropped: this one keeps its
        // turn, and its sender gives up (it may already have, and acting on its request now would
        // close this window with no other to replace it)
        if (Lookup.CloseRequest != null) {
            Lookup.CloseRequest.Reset();
            WaitForCloseRequest();
        }
    }

    void WaitForCloseRequest() {
        ThreadPool.RegisterWaitForSingleObject(Lookup.CloseRequest, (state, timedOut) => {
            try {
                // (not while 加入映射詞彙 asks for a code: what is typed there would be lost; the other
                // lookup gives up and this one waits for the next request)
                BeginInvoke((Action)(() => { if (asking) WaitForCloseRequest(); else Close(); }));
            } catch (Exception) { }
        }, null, Timeout.Infinite, true);
    }

    static bool IsEnglish(string s) { return s.Any(char.IsLetter) && s.All(c => c < 128); }

    Label MakeLabel(string t, Font f, Color c, int x, int y, int w, int h) {
        return new Label { Text = t, Font = f, ForeColor = c, BackColor = bg, Location = new Point(x, y), Size = new Size(w, h),
                           UseMnemonic = false, AutoEllipsis = true };
    }
    int AddButton(string t, int x, int y, int w, int h, EventHandler click) {
        var b = new Button { Text = t, Location = new Point(x, y), Size = new Size(w, h), FlatStyle = FlatStyle.Flat,
                             BackColor = bg, ForeColor = fg, UseMnemonic = false };
        b.FlatAppearance.BorderColor = line;
        b.Click += click;
        Controls.Add(b);
        return x + w + 8;
    }
    void Report(string message) { status.Text = message; }

    void Speak(string culture) {
        try {
            if (voice == null) voice = new SpeechSynthesizer();
            var v = voice.GetInstalledVoices().FirstOrDefault(x => x.Enabled && x.VoiceInfo.Culture.Name == culture)
                 ?? voice.GetInstalledVoices().FirstOrDefault(x => x.Enabled && x.VoiceInfo.Culture.Name.StartsWith(culture.Substring(0, 2)));
            if (v == null) {
                Report(culture == "zh-TW" ? "沒有安裝中文語音（設定 > 時間與語言 > 語音）" :
                       culture == "ja-JP" ? "沒有安裝日文語音（設定 > 時間與語言 > 語音）" : "沒有安裝英文語音");
                return;
            }
            voice.SelectVoice(v.VoiceInfo.Name);
            voice.SpeakAsyncCancelAll();
            voice.SpeakAsync(text);
        } catch (Exception ex) {
            Report("無法朗讀：" + ex.Message);
        }
    }

    void AskShortcut() {
        asking = true;
        try {
            using (var f = new Form()) {
                f.Text = "加入映射詞彙"; f.FormBorderStyle = FormBorderStyle.FixedDialog; f.MinimizeBox = f.MaximizeBox = false;
                f.StartPosition = FormStartPosition.CenterParent; f.Font = Font; f.BackColor = bg; f.ForeColor = fg;
                f.ClientSize = new Size(320, 120); f.ShowInTaskbar = false;
                f.Controls.Add(new Label { Text = "捷徑代碼（英文字母和數字，或中文，最多 8 個字）：", Location = new Point(12, 12), Size = new Size(296, 22), BackColor = bg, ForeColor = fg });
                var box = new TextBox { Location = new Point(12, 40), Size = new Size(296, 26) };
                var ok = new Button { Text = "確定", DialogResult = DialogResult.OK, Location = new Point(132, 80), Size = new Size(84, 30) };
                var cancel = new Button { Text = "取消", DialogResult = DialogResult.Cancel, Location = new Point(224, 80), Size = new Size(84, 30) };
                f.Controls.AddRange(new Control[] { box, ok, cancel });
                f.AcceptButton = ok; f.CancelButton = cancel;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                string keys = box.Text.Trim();
                Report(Lookup.IsShortcutKeys(keys) ? Lookup.AddShortcut(keys, text) : "捷徑代碼請用英文字母和數字，或全部用中文，最多 8 個字");
            }
        } finally {
            asking = false;
            Activate();
        }
    }

    protected override void OnFormClosed(FormClosedEventArgs e) {
        if (voice != null) voice.Dispose();
        base.OnFormClosed(e);
    }
}

// ---- UI Automation's COM interface (UIAutomationClient.h), only the methods used ----------------
// The vtable order must match the header: the methods before the ones used are placeholders.
[ComImport, Guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomation {
    void CompareElements();
    void CompareRuntimeIds();
    void GetRootElement();
    void ElementFromHandle();
    void ElementFromPoint();
    [PreserveSig] int GetFocusedElement(out IUIAutomationElement element);
    void GetRootElementBuildCache();
    void ElementFromHandleBuildCache();
    void ElementFromPointBuildCache();
    void GetFocusedElementBuildCache();
    void CreateTreeWalker();
    void get_ControlViewWalker();
    void get_ContentViewWalker();
    [PreserveSig] int get_RawViewWalker(out IUIAutomationTreeWalker walker);
}

[ComImport, Guid("4042c624-389c-4afc-a630-9df854a541fc"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomationTreeWalker {
    [PreserveSig] int GetParentElement(IUIAutomationElement element, out IUIAutomationElement parent);
}

[ComImport, Guid("d22108aa-8ac5-49a5-837b-37bbb3d7591e"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomationElement {
    void SetFocus();
    void GetRuntimeId();
    void FindFirst();
    void FindAll();
    void FindFirstBuildCache();
    void FindAllBuildCache();
    void BuildUpdatedCache();
    void GetCurrentPropertyValue();
    void GetCurrentPropertyValueEx();
    void GetCachedPropertyValue();
    void GetCachedPropertyValueEx();
    void GetCurrentPatternAs();
    void GetCachedPatternAs();
    [PreserveSig] int GetCurrentPattern(int patternId, [MarshalAs(UnmanagedType.IUnknown)] out object pattern);
}

[ComImport, Guid("32eba289-3583-42c9-9c59-3b6d9a1e9b6a"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomationTextPattern {
    void RangeFromPoint();
    void RangeFromChild();
    [PreserveSig] int GetSelection(out IUIAutomationTextRangeArray ranges);
}

[ComImport, Guid("ce4ae76a-e717-4c98-81ea-47371d028eb6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomationTextRangeArray {
    [PreserveSig] int get_Length(out int length);
    [PreserveSig] int GetElement(int index, out IUIAutomationTextRange range);
}

[ComImport, Guid("a543cc6a-f4ae-494b-8239-c814481187a8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IUIAutomationTextRange {
    void Clone();
    void Compare();
    void CompareEndpoints();
    void ExpandToEnclosingUnit();
    void FindAttribute();
    void FindText();
    void GetAttributeValue();
    void GetBoundingRectangles();
    void GetEnclosingElement();
    [PreserveSig] int GetText(int maxLength, [MarshalAs(UnmanagedType.BStr)] out string text);
}
