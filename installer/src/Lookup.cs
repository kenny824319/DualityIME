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
using System.Web.Script.Serialization;
using System.Windows.Automation;
using System.Windows.Automation.Text;
using System.Windows.Forms;
using Microsoft.Win32;

static class Lookup {
    const int MaxPhrase = 8, MaxWord = 8, MaxText = 255, MaxKeys = 8;
    static readonly string DataDir = Path.Combine(
        Environment.GetEnvironmentVariable("APPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DualityIME");

    [STAThread]
    static int Main(string[] args) {
        string text = null;
        if (args.Length >= 2 && args[0] == "--print") return Print(Zhuyin(args[1]));
        if (args.Length >= 2 && args[0] == "--add-word") return Print(AddWord(args[1], Zhuyin(args[1])));
        if (args.Length >= 3 && args[0] == "--add-shortcut") return Print(AddShortcut(args[1], args[2]));
        if (args.Length >= 2 && args[0] == "--text") text = args[1];
        if (text == null) text = SelectedText();
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

    // ---- the selection ----------------------------------------------------------------
    static string SelectedText() {
        try {
            AutomationElement e = AutomationElement.FocusedElement;
            // the focused element, or a parent that holds the text (a document around a caret)
            for (int up = 0; e != null && up < 4; up++) {
                object pattern;
                if (e.TryGetCurrentPattern(TextPattern.Pattern, out pattern)) {
                    TextPatternRange[] ranges = ((TextPattern)pattern).GetSelection();
                    if (ranges.Length > 0) {
                        string s = ranges[0].GetText(MaxText + 1);
                        if (!string.IsNullOrWhiteSpace(s)) return s;
                    }
                }
                e = TreeWalker.ControlViewWalker.GetParent(e);
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
        if (words.Any(p => p[0] == word)) return "「" + word + "」已經在自訂詞彙裡了";
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
    public static bool CanBeWord(string s) { return s.Length > 0 && s.Length <= MaxWord && s.All(IsHan); }
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
    readonly Label status;
    readonly Color bg, fg, sub, accent, line;
    SpeechSynthesizer voice;
    bool asking;  // a dialog of ours is up: losing focus to it must not close the window

    public LookupWindow(string text) {
        this.text = text.Length > 255 ? text.Substring(0, 255) : text;
        zhuyin = Lookup.Zhuyin(this.text);
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
            bool chinese = zhuyin.Length > 0;
            string reading = chinese ? zhuyin : (IsEnglish(this.text) ? "英文" : "");
            if (reading.Length > 0) {
                add(MakeLabel(reading, new Font(Font.FontFamily, 12f * zoom), accent, px(16), y, w - px(32), px(28)));
                y += px(34);
            }
            int x = px(16);
            x = AddButton("🔊 朗讀", x, y, px(96), px(32), (s, e) => Speak());
            if (Lookup.CanBeWord(this.text))
                x = AddButton("加入自訂詞彙", x, y, px(120), px(32), (s, e) => Report(Lookup.AddWord(this.text, zhuyin)));
            if (Lookup.CanBeShortcutText(this.text))
                AddButton("加入映射詞彙", x, y, px(120), px(32), (s, e) => AskShortcut());
            y += px(40);
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

    void Speak() {
        try {
            if (voice == null) voice = new SpeechSynthesizer();
            string culture = zhuyin.Length > 0 ? "zh-TW" : "en-US";
            var v = voice.GetInstalledVoices().FirstOrDefault(x => x.Enabled && x.VoiceInfo.Culture.Name == culture)
                 ?? voice.GetInstalledVoices().FirstOrDefault(x => x.Enabled && x.VoiceInfo.Culture.Name.StartsWith(culture.Substring(0, 2)));
            if (v == null) {
                Report(culture == "zh-TW" ? "沒有安裝中文語音（設定 > 時間與語言 > 語音）" : "沒有安裝英文語音");
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
