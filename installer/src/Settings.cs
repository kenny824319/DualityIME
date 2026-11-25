// 兩儀輸入法 settings.exe: the settings app. A window holding a web page (ui\index.html, shown by
// WebView2, the Edge engine in Windows); this program does what the page cannot: read and write
// the settings and the personal dictionary, Windows' input method settings, updates, removing.
//
// The page talks to it with messages: {"id": 1, "op": "setConfig", "args": {...}} and gets
// {"id": 1, "ok": true, "result": ...} back (see Handle). It only ever shows the local page:
// any other address is refused, or opened in the browser if it is GitHub.
//   settings.exe [--page general|complete|dict|look|help|about] [--dev] [--data <folder>]
//   --dev: developer tools; --data: another folder for config.json etc. (tests)
//   settings.exe --migrate-user [roamingDir]: no window; moves this account's 2.x data from roamingDir\SmartZhuyin
//   to roamingDir\DualityIME (the engine passes its own Roaming folder; without it: the APPDATA variable, so a
//   test can point it at a temporary folder, then Windows' Roaming folder). Exit code = Migrate.MoveResult.
//   Started with the window (no --data), it first moves 2.x data still waiting (Migrate.Pending), so a setting
//   changed before the engine ever ran cannot keep the 2.x data from moving.
// Mostly written with Claude (claude-opus-5-5).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

static class Program {
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindow(string cls, string title);

    public const string Title = "兩儀輸入法 設定";

    [STAThread]
    static int Main(string[] args) {
        if (args.Length > 0 && args[0] == "--migrate-user") {
            // no window, no mutex: the engine runs this once for an account that still has 2.x data
            string roaming = args.Length > 1 && args[1] != "" ? args[1] : Environment.GetEnvironmentVariable("APPDATA");
            if (string.IsNullOrEmpty(roaming)) roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            try {
                return (int)Migrate.MoveRoaming(roaming);
            } catch { return (int)Migrate.MoveResult.Failed; }
        }
        bool fresh;
        using (var single = new Mutex(true, "DualityIME.Settings." + Environment.UserName, out fresh)) {
            if (!fresh) {
                // already open: bring it to the front
                IntPtr h = FindWindow(null, Title);
                if (h != IntPtr.Zero) { ShowWindow(h, 9); SetForegroundWindow(h); }
                return 0;
            }
            SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string page = "", data = null;
            bool dev = false;
            for (int i = 0; i < args.Length; i++) {
                if (args[i] == "--page" && i + 1 < args.Length) page = args[++i];
                else if (args[i] == "--data" && i + 1 < args.Length) data = args[++i];
                else if (args[i] == "--dev") dev = true;
            }
            // 2.x data not moved yet (the engine has not run for this account): moved before config.json is read
            // (not moved: the window stays closed, or a setting saved in it would count as 3.0 data and the 2.x data
            // would never be moved; the next try is the next start of the input method or of this window)
            if (data == null) {
                Migrate.MoveResult moved;
                try { moved = Migrate.MoveBeforeWindow(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData)); }
                catch { moved = Migrate.MoveResult.Failed; }
                if (moved == Migrate.MoveResult.Failed) {
                    MessageBox.Show("舊版（智慧輸入）的打字記憶與設定還沒搬到兩儀輸入法，這次沒有搬成功（可能有其他程式正在使用那些檔案）。\n\n" +
                                    "請稍後再開設定，或重新開機後再試。", Title);
                    return 0;
                }
            }
            Application.Run(new SettingsForm(page, dev, data));
            return 0;
        }
    }
}

public class SettingsForm : Form {
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    const string Host = "settings.dualityime";
    // where 回報問題 opens (the project's issues page)
    const string IssuesUrl = "https://github.com/kenny824319/DualityIME/issues/new";

    readonly string page;
    readonly bool dev;
    readonly bool dark;
    readonly WebView2 web = new WebView2();
    readonly Backend backend;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

    public SettingsForm(string page, bool dev, string data) {
        this.page = page;
        this.dev = dev;
        backend = new Backend(this, data);
        dark = Backend.SystemIsDark();
        Text = Program.Title;
        string icon = backend.Find("icon.ico", @"installer\app.ico");
        if (icon != null) { try { Icon = new Icon(icon); } catch { } }
        float scale = 1f;
        using (Graphics g = CreateGraphics()) scale = g.DpiX / 96f;
        ClientSize = new Size((int)(1000 * scale), (int)(680 * scale));
        MinimumSize = new Size((int)(760 * scale), (int)(520 * scale));
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = dark ? Color.FromArgb(32, 32, 32) : Color.FromArgb(243, 243, 243);
        web.Dock = DockStyle.Fill;
        web.DefaultBackgroundColor = BackColor;
        Controls.Add(web);
        Load += async (s, e) => await Start();
    }

    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        int on = dark ? 1 : 0;
        DwmSetWindowAttribute(Handle, 20, ref on, 4);  // dark title bar in dark mode
    }

    async Task Start() {
        string ui = backend.FindDir("ui", @"settings\ui");
        if (ui == null) { MessageBox.Show("找不到設定工具的畫面檔案，請重新安裝兩儀輸入法。", Program.Title); Close(); return; }
        try {
            string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DualityIME", "WebView2");
            var env = await CoreWebView2Environment.CreateAsync(null, data);
            await web.EnsureCoreWebView2Async(env);
        } catch (WebView2RuntimeNotFoundException) {
            var r = MessageBox.Show("設定工具需要 Microsoft Edge WebView2（Windows 內建的網頁元件），這台電腦找不到它。\n\n要開啟下載頁面嗎？",
                Program.Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
            if (r == DialogResult.Yes) Backend.OpenUrl("https://go.microsoft.com/fwlink/p/?LinkId=2124703");
            Close();
            return;
        }
        CoreWebView2 core = web.CoreWebView2;
        core.Settings.AreDevToolsEnabled = dev;
        core.Settings.AreDefaultContextMenusEnabled = dev;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsZoomControlEnabled = false;
        core.Settings.AreBrowserAcceleratorKeysEnabled = dev;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.SetVirtualHostNameToFolderMapping(Host, ui, CoreWebView2HostResourceAccessKind.DenyCors);
        core.NavigationStarting += (s, e) => {
            // only our page; GitHub links open in the browser, anything else is refused
            if (e.Uri.StartsWith("https://" + Host + "/")) return;
            e.Cancel = true;
            Backend.OpenUrl(e.Uri);
        };
        core.NewWindowRequested += (s, e) => { e.Handled = true; Backend.OpenUrl(e.Uri); };
        core.WebMessageReceived += OnMessage;
        core.Navigate("https://" + Host + "/index.html" + (page != "" ? "#" + Uri.EscapeDataString(page) : ""));
    }

    void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e) {
        Dictionary<string, object> msg;
        try { msg = json.DeserializeObject(e.WebMessageAsJson) as Dictionary<string, object>; } catch { return; }
        if (msg == null || !msg.ContainsKey("id") || !msg.ContainsKey("op")) return;
        object id = msg["id"];
        string op = msg["op"] as string ?? "";
        var args = (msg.ContainsKey("args") ? msg["args"] : null) as Dictionary<string, object> ?? new Dictionary<string, object>();
        if (Backend.Slow(op)) {
            // PowerShell and updates take a while: off the window's thread, answered when done
            Task.Run(() => {
                object result; string error;
                Run(op, args, out result, out error);
                BeginInvoke(new Action(() => Reply(id, result, error)));
            });
        } else {
            object result; string error;
            Run(op, args, out result, out error);
            Reply(id, result, error);
        }
    }

    void Run(string op, Dictionary<string, object> args, out object result, out string error) {
        result = null; error = null;
        try { result = backend.Handle(op, args); } catch (Exception ex) { error = ex.Message; }
    }

    void Reply(object id, object result, string error) {
        if (web.CoreWebView2 == null) return;
        var reply = new Dictionary<string, object> { { "id", id }, { "ok", error == null }, { "result", result }, { "error", error } };
        web.CoreWebView2.PostWebMessageAsJson(json.Serialize(reply));
    }

    public string IssuesLink(string title, string body) {
        return IssuesUrl + "?title=" + Uri.EscapeDataString(title) + "&body=" + Uri.EscapeDataString(body);
    }
}

// What the page asks for. Every op takes a dictionary of arguments and returns something the
// page gets as JSON.
public class Backend {
    readonly SettingsForm form;
    readonly string exeDir = AppDomain.CurrentDomain.BaseDirectory;
    readonly string dataDir;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    const string OurKey = @"Software\DualityIME";
    Dictionary<string, string> readings;  // 詞 -> 注音, for 填入注音 (loaded the first time)
    Dictionary<string, object> backup;    // the backup file opened for 匯入, until it is restored

    // the settings the IME reads (engine/config.py has the same defaults)
    static readonly Dictionary<string, object> Defaults = new Dictionary<string, object> {
        { "autocorrect", false }, { "long_sentence", true }, { "lm_context", true }, { "lm_model", "light" },
        { "lm_context_len_plus", 64 }, { "lm_context_len_max", 64 }, { "punctuation", "half" }, { "space", "half" },
        { "full_width", false }, { "ctrl_full_punct", true }, { "symbols_stay_open", false }, { "preview", "keys_hint" }, { "shift_toggle", true }, { "start_mode", "mixed" },
        { "completion", true }, { "remember_words", true }, { "forget_words", 0 }, { "forget_emails", 0 }, { "forget_list", new object[0] }, { "import_memory", 0 }, { "lookup", true },
        { "lookup_size", "small" }, { "game_keys", true }, { "cand_font", "Microsoft JhengHei UI" },
        { "cand_font_size", 18 }, { "cand_per_row", 1 }, { "cand_layout", "" }, { "cand_keys", "123456789" },
        { "cand_columns", 0 },
        { "appearance", new Dictionary<string, object>() },  // 自訂外觀 (#22): {} = the default look (never changed in place)
        { "simplified", false }, { "simplified_hotkey", false }, { "simplified_key", "Ctrl+F1" }  // 簡體輸出 (#32)
    };

    public Backend(SettingsForm form, string data) {
        this.form = form;
        dataDir = data ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "DualityIME");
    }

    public static bool Slow(string op) {
        return op == "system" || op == "setSystem" || op == "checkUpdate" || op == "runUpdate";
    }

    public object Handle(string op, Dictionary<string, object> a) {
        switch (op) {
            case "state": return State();
            case "setConfig": return SetConfig(Str(a, "key"), a.ContainsKey("value") ? a["value"] : null);
            case "forgetWords": {
                // 全部清除: the engine clears the typing memory (forget_words), the e-mails too when ticked
                var c = ReadConfig();
                var bump = Bool(a, "emails") ? new[] { "forget_words", "forget_emails" } : new[] { "forget_words" };
                foreach (string k in bump) c[k] = Convert.ToInt32(c.ContainsKey(k) ? c[k] : 0) + 1;
                WriteConfig(c);
                return 0;
            }
            case "memory": return MemoryList(Today());  // 記憶清單 (#65)
            case "forgetMemory": return ForgetMemory(a);
            case "symbols": return ReadSymbols();  // 符號鍵盤自訂 (#69)
            case "saveSymbols": return SaveSymbols(a);
            case "resetSymbols":
                if (File.Exists(SymbolsPath)) File.Delete(SymbolsPath);
                return true;
            case "system": return RunSystem("state", 0);
            case "setSystem": return RunSystem(Str(a, "action"), Bool(a, "on") ? 1 : 0);
            case "openDataFolder":
                Directory.CreateDirectory(dataDir);
                Process.Start("explorer.exe", "\"" + dataDir + "\"");
                return true;
            case "resetKeyboard":
                File.Delete(Path.Combine(dataDir, "keyboard.json"));
                return true;
            case "dictionary": return ReadDictionary();
            case "saveDictionary": return SaveDictionary(a);
            case "quickFill": return ReadQuickFill();
            case "saveQuickFill": return SaveQuickFill(a);
            case "readings": return Readings(a);
            case "exportBackup":
                using (var s = new SaveFileDialog { Filter = "兩儀輸入法資料 (*.json)|*.json", FileName = "兩儀輸入法資料-" + DateTime.Now.ToString("yyyyMMdd") + ".json" }) {
                    if (s.ShowDialog(form) != DialogResult.OK) return false;
                    return WriteBackup(s.FileName);
                }
            case "openBackup":
                using (var o = new OpenFileDialog { Filter = "兩儀輸入法資料 (*.json)|*.json|所有檔案|*.*" }) {
                    if (o.ShowDialog(form) != DialogResult.OK) return null;
                    return ReadBackup(o.FileName);
                }
            case "pickImage":  // 自訂外觀 (#22): {file, url} of the photo copied in, or null
                using (var o = new OpenFileDialog { Filter = "圖片 (*.jpg;*.jpeg;*.png;*.gif)|*.jpg;*.jpeg;*.png;*.gif" }) {
                    if (o.ShowDialog(form) != DialogResult.OK) return null;
                    string file = ImportImage(o.FileName, Str(a, "slot"));
                    // (name: the user's file as it was named, shown on the page)
                    return new Dictionary<string, object> { { "file", file }, { "url", ImageUrl(file) }, { "name", Path.GetFileName(o.FileName) } };
                }
            case "imageUrl": return ImageUrl(Str(a, "file"));
            case "removeImage": RemoveImage(Str(a, "slot")); return true;
            case "restoreBackup":
                return RestoreBackup(Bool(a, "memory"), Bool(a, "words"), Bool(a, "shortcuts"), Bool(a, "config"), Bool(a, "quickFill"));
            case "setBeta":
                using (var k = Registry.CurrentUser.CreateSubKey(OurKey)) k.SetValue("UpdateBranch", Bool(a, "on") ? "develop" : "main");
                return true;
            case "checkUpdate": return RunUpdate(true);
            case "runUpdate": return RunUpdate(false);
            case "report": {
                string code = Str(a, "code");
                string body = "版本：" + Version() + "\n錯誤代碼：" + code + "\nWindows：" + Environment.OSVersion.VersionString + "\n\n（請描述發生了什麼事）";
                OpenUrl(form.IssuesLink("更新失敗 " + code, body));
                return true;
            }
            case "openUrl": OpenUrl(Str(a, "url")); return true;
            case "hotkeys": return Hotkeys();
            case "openHotkeys":
                // Windows' own 輸入語言快速鍵 (進階按鍵設定): the IME follows it and never changes it
                Process.Start("rundll32.exe", "shell32.dll,Control_RunDLL input.dll,,{C07337D3-DB2C-4D0B-9A93-B722A6C106E2}{HOTKEYS}");
                return true;
            case "uninstall": return Uninstall(Bool(a, "removePime"), Bool(a, "removeData"));
        }
        throw new Exception("unknown op " + op);
    }

    // ---- files and folders --------------------------------------------------------------------
    public string Find(string installed, string inProject) {
        // a file next to this program (installed), else in the project folder (bin\settings.exe)
        string a = Path.Combine(exeDir, installed);
        if (File.Exists(a)) return a;
        string b = Path.Combine(exeDir, "..", inProject);
        return File.Exists(b) ? Path.GetFullPath(b) : null;
    }

    public string FindDir(string installed, string inProject) {
        string a = Path.Combine(exeDir, installed);
        if (Directory.Exists(a)) return a;
        string b = Path.Combine(exeDir, "..", inProject);
        return Directory.Exists(b) ? Path.GetFullPath(b) : null;
    }

    public static bool SystemIsDark() {
        object v = Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1);
        return v is int && (int)v == 0;
    }

    public static void OpenUrl(string url) {
        // only GitHub (the project) and Microsoft's WebView2 download, in the default browser
        if (url == null) return;
        if (url.StartsWith("https://github.com/") || url.StartsWith("https://go.microsoft.com/fwlink/")) {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }
    }

    static string Str(Dictionary<string, object> a, string k) { return a.ContainsKey(k) && a[k] != null ? a[k].ToString() : ""; }
    static bool Bool(Dictionary<string, object> a, string k) { return a.ContainsKey(k) && a[k] is bool && (bool)a[k]; }

    // ---- state --------------------------------------------------------------------------------
    string Version() {
        string v = Find("version.txt", "VERSION");
        if (v == null) return "";
        try { return File.ReadAllLines(v).FirstOrDefault() ?? ""; } catch { return ""; }
    }

    object State() {
        var dict = ReadDictionary();
        int[] remembered = RememberedCount();
        return new Dictionary<string, object> {
            { "config", ReadConfig() },
            { "version", Version() },
            { "dataDir", dataDir },
            { "remembered", remembered[0] },
            { "rememberedEmails", remembered[1] },
            { "rememberedSimplified", remembered[2] < 0 ? null : (object)remembered[2] },
            { "words", ((List<object>)dict["words"]).Count },
            { "shortcuts", ((List<object>)dict["shortcuts"]).Count },
            { "quickFill", ReadQuickFill().Count },
            { "beta", (Registry.GetValue(@"HKEY_CURRENT_USER\" + OurKey, "UpdateBranch", "") as string) == "develop" },
            { "canUpdate", File.Exists(Updater.InstalledPath()) },
            { "hasPime", HasPime() },
            { "dark", SystemIsDark() }
        };
    }

    // PIME, which versions before 2.0 needed, is still on this computer (the same check as Find-Pime
    // in installer\remove-pime.ps1): only then does 解除安裝 offer to remove it
    static bool HasPime() {
        try {
            string dir = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\PIME", "", null) as string;
            if (string.IsNullOrEmpty(dir))
                dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "PIME");
            return File.Exists(Path.Combine(dir, "PIMELauncher.exe"));
        } catch {
            return false;
        }
    }

    // 已記憶項目: { N, M, S } - the rows of 記憶清單 (#65, the user 2026-11-24: the count agrees with the list): N =
    // 選字 + 英文字 + 按鍵, M = Email, S = 簡體模式, -1 without user_simplified.json (簡體輸出 never used, #32).
    public int[] RememberedCount() { return RememberedCount(MemoryList(Today())); }

    static int[] RememberedCount(Dictionary<string, object> list) {
        Func<string, int> n = k => list[k] == null ? -1 : ((List<Dictionary<string, object>>)list[k]).Count;
        return new[] { n("picks") + n("words") + n("keys"), n("emails"), n("simplified") };
    }

    // ---- 記憶清單 (#65): what the memory holds that affects typing, as the engine reads it -------------------------
    // The same rows as engine/lexicon.py memory_list (tests/data/memory_list/expected.json holds the rule for both):
    // counts faded (each FORGET_DAYS days unused halve them; e-mails never fade), rounded, at least 1; English words
    // from WORD_MIN_USES uses, a 校正記憶 choice from CORRECTION_MIN_USES and above the next, a Mainland word from
    // REPLACE_MIN_USES; with 注音自動校正 off only the codes read as English and the split keys act. What the engine is
    // about to remove is left out already: 全部清除 asked (forget_words above the forgot in user.json, M for
    // forget_emails) and the rows waiting in forget_list (an id above forgot.json's applied).
    const int ForgetDays = 20, WordMinUses = 2, CorrectionMinUses = 2, ReplaceMinUses = 2;
    const string SplitMark = "→", AsEnglish = "en";

    public static int Today() {
        return (int)Math.Floor((DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalDays);
    }

    static Dictionary<string, object> Sub(Dictionary<string, object> d, string k) {
        return d != null && d.ContainsKey(k) ? d[k] as Dictionary<string, object> : null;
    }

    static double Num(object v) {
        if (v == null || v is bool || v is string) return double.NaN;
        try { return Convert.ToDouble(v); } catch { return double.NaN; }
    }

    static int? DayOf(Dictionary<string, object> days, string key) {
        double d = Num(days != null && days.ContainsKey(key) ? days[key] : null);
        return double.IsNaN(d) ? (int?)null : (int)d;
    }

    static double Faded(double count, int? day, int today) {
        return day == null ? count : count / Math.Pow(2, Math.Floor((today - day.Value) / (double)ForgetDays));
    }

    static Dictionary<string, object> MemoryRow(string kind, string key, string value, string shown, string reading,
                                                double? count, int? day, string note) {
        return new Dictionary<string, object> {
            { "kind", kind }, { "key", key }, { "value", value }, { "shown", shown }, { "reading", reading },
            { "count", count == null ? null : (object)Math.Max(1, (int)Math.Floor(count.Value + 0.5)) },
            { "day", day == null ? null : (object)day.Value }, { "note", note }
        };
    }

    static List<Dictionary<string, object>> Ordered(List<Dictionary<string, object>> rows) {
        return rows.OrderBy(r => r["day"] == null ? 1 : 0).ThenByDescending(r => r["day"] == null ? 0 : (int)r["day"])
                   .ThenBy(r => (string)r["shown"], StringComparer.Ordinal).ToList();
    }

    Dictionary<string, object> ReadData(string name) { return ReadJson(Path.Combine(dataDir, name)); }

    // forgot.json's applied: the last forget_list id the engine applied (0 without the file)
    int AppliedForget() { return (int)Long(ReadData("forgot.json"), "applied"); }

    public Dictionary<string, object> MemoryList(int today) {
        var user = ReadData("user.json");
        var keys = Sub(ReadData("keys.json"), "standard");
        bool simpFile = File.Exists(Path.Combine(dataDir, "user_simplified.json"));
        var simp = simpFile ? ReadData("user_simplified.json") : null;
        var config = ReadConfig();
        bool autocorrect = config.ContainsKey("autocorrect") && config["autocorrect"] is bool && (bool)config["autocorrect"];
        bool clearHabits = Long(config, "forget_words") > Long(user, "forgot");
        bool clearEmails = Long(config, "forget_emails") > Long(user, "forgot_emails");
        var waiting = new HashSet<string>();  // "kind\0key\0value" of the rows forget_list is about to remove
        int applied = AppliedForget();
        foreach (object o in (config.ContainsKey("forget_list") ? config["forget_list"] as object[] : null) ?? new object[0]) {
            var it = o as Dictionary<string, object>;
            double id = Num(it != null && it.ContainsKey("id") ? it["id"] : null);
            if (double.IsNaN(id) || id <= applied || !(it.ContainsKey("kind") && it["kind"] is string) ||
                !(it.ContainsKey("key") && it["key"] is string) || !(it.ContainsKey("value") && it["value"] is string))
                continue;
            waiting.Add(it["kind"] + "\0" + it["key"] + "\0" + it["value"]);
        }
        Func<Dictionary<string, object>, bool> kept = r =>
            !waiting.Contains(r["kind"] + "\0" + r["key"] + "\0" + r["value"]);

        var picks = new List<Dictionary<string, object>>();
        var words = new List<Dictionary<string, object>>();
        var emails = new List<Dictionary<string, object>>();
        var keyRows = new List<Dictionary<string, object>>();
        var used = Sub(user, "used");
        if (!clearHabits) {
            var phrases = Sub(user, "phrases");
            var phraseUsed = Sub(used, "phrases");
            if (phrases != null)
                foreach (var r in phrases) {
                    var bucket = r.Value as Dictionary<string, object>;
                    if (bucket == null) continue;
                    var days = Sub(phraseUsed, r.Key);
                    foreach (var p in bucket) {
                        double n = Num(p.Value);
                        if (double.IsNaN(n) || n <= 0) continue;
                        int? day = DayOf(days, p.Key);
                        picks.Add(MemoryRow("pick", r.Key, p.Key, p.Key, r.Key.Replace("-", " "), Faded(n, day, today), day, ""));
                    }
                }
            var wordCounts = Sub(user, "words");
            if (wordCounts != null)
                foreach (var w in wordCounts) {
                    double n = Num(w.Value);
                    int? day = DayOf(Sub(used, "words"), w.Key);
                    if (!double.IsNaN(n) && Faded(n, day, today) >= WordMinUses)
                        words.Add(MemoryRow("word", w.Key, "", w.Key, "", Faded(n, day, today), day, ""));
                }
            var englishDone = new HashSet<string>();
            var keyPicks = Sub(keys, "picks");
            var keyUsed = Sub(keys, "used");
            if (keyPicks != null)
                foreach (var k in keyPicks) {
                    var bucket = k.Value as Dictionary<string, object>;
                    if (bucket == null) continue;
                    var days = Sub(keyUsed, k.Key);
                    // CorrectionMemory.choice: the best faded count, CorrectionMinUses or more and above the next one
                    string best = null;
                    double top = double.NegativeInfinity, second = double.NegativeInfinity;
                    foreach (var c in bucket) {
                        double n = Num(c.Value);
                        double f = double.IsNaN(n) || n == 0 ? 0 : Faded(n, DayOf(days, c.Key), today);
                        if (f > top) { second = top; top = f; best = c.Key; }
                        else if (f > second) second = f;
                    }
                    if (best == null || top < CorrectionMinUses || top <= second) continue;
                    bool split = k.Key.Contains(SplitMark);
                    string shown = k.Key.Replace(SplitMark, " ");
                    int? when = DayOf(days, best);
                    if (best == AsEnglish) {
                        keyRows.Add(MemoryRow("english", k.Key, "", shown, "", top, when, "照打成英文"));
                        englishDone.Add(k.Key);
                        continue;
                    }
                    if (!autocorrect && !split) continue;
                    string note = best == "" ? "照打，不校正" :
                        split ? "英文後面的 " + k.Key.Substring(k.Key.IndexOf(SplitMark) + SplitMark.Length) + " 讀成這個音" :
                        "打錯時照這個改（注音自動校正）";
                    keyRows.Add(MemoryRow("correction", k.Key, best, shown, best.Replace("-", " "), top, when, note));
                }
            foreach (object e in (user != null && user.ContainsKey("english") ? user["english"] as object[] : null) ?? new object[0]) {
                string s = e as string;
                if (s != null && !Migrate.IsOneKey(s) && !englishDone.Contains(s))
                    keyRows.Add(MemoryRow("english", s, "", s, "", null, null, "照打成英文"));
            }
        }
        if (!clearEmails) {
            var mails = Sub(user, "emails");
            if (mails != null)
                foreach (var m in mails) {
                    double n = Num(m.Value);
                    if (!double.IsNaN(n) && n > 0) emails.Add(MemoryRow("email", m.Key, "", m.Key, "", n, DayOf(Sub(used, "emails"), m.Key), ""));
                }
        }
        List<Dictionary<string, object>> simpRows = null;
        if (simpFile) {
            simpRows = new List<Dictionary<string, object>>();
            if (!clearHabits && simp != null) {
                var simpUsed = Sub(Sub(simp, "used"), "phrases");
                var phrases = Sub(simp, "phrases");
                if (phrases != null)
                    foreach (var r in phrases) {
                        var bucket = r.Value as Dictionary<string, object>;
                        if (bucket == null) continue;
                        foreach (var p in bucket) {
                            double n = Num(p.Value);
                            if (double.IsNaN(n) || n <= 0) continue;
                            int? day = DayOf(Sub(simpUsed, r.Key), p.Key);
                            simpRows.Add(MemoryRow("simp_pick", r.Key, p.Key, p.Key, r.Key.Replace("-", " "), Faded(n, day, today), day, "簡體選字"));
                        }
                    }
                var terms = Sub(simp, "replacements");
                var termUsed = Sub(simp, "replacements_used");
                if (terms != null)
                    foreach (var w in terms) {
                        var bucket = w.Value as Dictionary<string, object>;
                        if (bucket == null) continue;
                        foreach (var t in bucket) {
                            double n = Num(t.Value);
                            int? day = DayOf(Sub(termUsed, w.Key), t.Key);
                            if (!double.IsNaN(n) && Faded(n, day, today) >= ReplaceMinUses)
                                simpRows.Add(MemoryRow("simp_term", w.Key, t.Key, w.Key, t.Key, Faded(n, day, today), day, "大陸用詞"));
                        }
                    }
            }
            simpRows = Ordered(simpRows.Where(kept).ToList());
        }
        return new Dictionary<string, object> {
            { "picks", Ordered(picks.Where(kept).ToList()) }, { "words", Ordered(words.Where(kept).ToList()) },
            { "emails", Ordered(emails.Where(kept).ToList()) }, { "keys", Ordered(keyRows.Where(kept).ToList()) },
            { "simplified", simpRows }
        };
    }

    // 記憶清單's 刪除: each row [kind, key, value] appended to config.json's forget_list with the next id; the items the
    // engine applied already (forgot.json) dropped, so the list stays short. Returns how many were added.
    int ForgetMemory(Dictionary<string, object> a) {
        var c = ReadConfig();
        int applied = AppliedForget();
        var list = new List<object>();
        int next = applied;
        foreach (object o in (c.ContainsKey("forget_list") ? c["forget_list"] as object[] : null) ?? new object[0]) {
            var it = o as Dictionary<string, object>;
            double id = Num(it != null && it.ContainsKey("id") ? it["id"] : null);
            if (double.IsNaN(id) || id <= applied) continue;  // (applied, or nothing the engine would read)
            list.Add(it);
            next = Math.Max(next, (int)id);
        }
        int added = 0, before = next;
        foreach (object o in (a.ContainsKey("items") ? a["items"] as object[] : null) ?? new object[0]) {
            var row = o as object[];
            if (row == null || row.Length != 3 || !row.All(x => x is string)) continue;
            list.Add(new Dictionary<string, object> { { "id", ++next }, { "kind", row[0] }, { "key", row[1] }, { "value", row[2] } });
            added++;
        }
        c["forget_list"] = list;
        WriteConfig(c);
        // a backup restored and not merged yet (import-memory.json waits for the engine): the engine applies
        // forget_list first and merges the import after, so a row deleted now would come back from it - it goes
        // from there too (findings #116), the rows of this call only: one deleted before the backup was restored
        // comes back from it, as the user decided (findings #119)
        string importPath = Path.Combine(dataDir, "import-memory.json");
        var waiting = File.Exists(importPath) ? ReadJson(importPath) : null;
        if (waiting != null) {
            DropWaiting(waiting, before);
            File.WriteAllText(importPath, json.Serialize(waiting), new UTF8Encoding(false));
        }
        return added;
    }

    // a counter in config.json or user.json (0 when missing or not a number)
    static long Long(Dictionary<string, object> d, string key) {
        object v;
        if (d == null || !d.TryGetValue(key, out v) || v == null || v is bool || v is string) return 0;
        try { return Convert.ToInt64(v); } catch { return 0; }
    }

    // ---- Windows' keyboard-switch hotkeys (read only: the IME lets Windows handle them) --------
    static string HotkeyName(object v) {
        switch ((v ?? "").ToString()) {
            case "1": return "Left Alt+Shift";
            case "2": return "Ctrl+Shift";
            case "4": return "`（重音符號）";
            default: return "";  // 3 or missing: none
        }
    }

    object Hotkeys() {
        const string toggle = @"HKEY_CURRENT_USER\Keyboard Layout\Toggle";
        object language = Registry.GetValue(toggle, "Language Hotkey", null) ?? Registry.GetValue(toggle, "Hotkey", "1");
        object layout = Registry.GetValue(toggle, "Layout Hotkey", "2");
        return new Dictionary<string, object> { { "language", HotkeyName(language) }, { "layout", HotkeyName(layout) } };
    }

    // ---- config.json --------------------------------------------------------------------------
    string ConfigPath { get { return Path.Combine(dataDir, "config.json"); } }

    Dictionary<string, object> ReadConfig() {
        var c = new Dictionary<string, object>(Defaults);
        try {
            var saved = json.DeserializeObject(File.ReadAllText(ConfigPath, Encoding.UTF8)) as Dictionary<string, object>;
            if (saved != null) foreach (var kv in saved) c[kv.Key] = kv.Value;  // (other keys kept as they are)
        } catch { }
        return c;
    }

    void WriteConfig(Dictionary<string, object> c) {
        Directory.CreateDirectory(dataDir);
        string tmp = ConfigPath + ".tmp";
        File.WriteAllText(tmp, json.Serialize(c), new UTF8Encoding(false));
        File.Copy(tmp, ConfigPath, true);  // (the IME reloads it when it changes)
        File.Delete(tmp);
    }

    // 簡體輸出's key (#32): what is wrong with it, or null. Ctrl and / or Alt (Shift may join) and one key: A-Z, 0-9,
    // F1-F12 (the IME reads it so: text_service.cpp parse_hotkey); never Win, nor Ctrl+Shift / Alt+Shift alone
    public static string HotkeyProblem(string text) {
        bool ctrl = false, alt = false, shift = false, win = false;
        string main = null;
        foreach (string part in (text ?? "").Split('+')) {
            if (part == "Ctrl") ctrl = true;
            else if (part == "Alt") alt = true;
            else if (part == "Shift") shift = true;
            else if (part == "Win") win = true;
            else if (main == null) main = part;
            else return "請按下包含 Ctrl 或 Alt 的組合鍵";
        }
        bool closes = alt && !ctrl && main == "F4";  // (Alt+F4 closes the window: never the IME's, the user's call 2026-11-14)
        if (win || closes || (main == null && (ctrl || alt))) return "這組鍵是 Windows 用的，請換一組";
        // (copy, paste, cut, undo, redo, select all, save: every program's, review 2026-11-14)
        if (ctrl && !alt && !shift && main != null && main.Length == 1 && "CVXZYAS".IndexOf(main[0]) >= 0)
            return "這組鍵是複製、貼上這類常用的鍵，請換一組";
        bool key = main != null && ((main.Length == 1 && ((main[0] >= 'A' && main[0] <= 'Z') || (main[0] >= '0' && main[0] <= '9'))) ||
                                    (main.Length >= 2 && main.Length <= 3 && main[0] == 'F' && main.Substring(1).All(char.IsDigit) &&
                                     int.Parse(main.Substring(1)) >= 1 && int.Parse(main.Substring(1)) <= 12));
        if (!key || !(ctrl || alt)) return "請按下包含 Ctrl 或 Alt 的組合鍵";
        return null;
    }

    object SetConfig(string key, object value) {
        if (!Defaults.ContainsKey(key)) throw new Exception("unknown setting " + key);
        if (key == "simplified_key") {
            string problem = HotkeyProblem(value as string);
            if (problem != null) throw new Exception(problem);
        }
        var c = ReadConfig();
        c[key] = value;
        WriteConfig(c);
        return c;
    }

    // ---- Windows' input method settings (settings-system.ps1) ---------------------------------
    object RunSystem(string action, int on) {
        string script = Find("settings-system.ps1", @"installer\settings-system.ps1");
        if (script == null) throw new Exception("settings-system.ps1 not found");
        var psi = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Action " + action + " -On " + on);
        psi.UseShellExecute = false; psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true; psi.StandardOutputEncoding = Encoding.UTF8;
        using (var p = Process.Start(psi)) {
            string output = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            string last = output.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith("{"));
            if (last == null) throw new Exception("no answer");
            return json.DeserializeObject(last);
        }
    }

    // ---- 個人詞庫: dictionary.json {"words": [["詞", "注音"]...], "shortcuts": [["捷徑", "內容"]...]} ----
    string DictPath { get { return Path.Combine(dataDir, "dictionary.json"); } }

    Dictionary<string, object> ReadDictionary() {
        var d = new Dictionary<string, object> { { "words", new List<object>() }, { "shortcuts", new List<object>() } };
        try {
            var j = json.DeserializeObject(File.ReadAllText(DictPath, Encoding.UTF8)) as Dictionary<string, object>;
            foreach (string kind in new[] { "words", "shortcuts" }) {
                object[] rows = j != null && j.ContainsKey(kind) ? j[kind] as object[] : null;
                if (rows == null) continue;
                foreach (object r in rows) {
                    object[] pair = r as object[];
                    if (pair != null && pair.Length == 2) ((List<object>)d[kind]).Add(new object[] { "" + pair[0], "" + pair[1] });
                }
            }
        } catch { }
        return d;
    }

    object SaveDictionary(Dictionary<string, object> a) {
        // the page checked the rows; written as the IME expects (it re-reads it on the next key)
        var d = ReadDictionary();
        string kind = Str(a, "kind");
        if (kind != "words" && kind != "shortcuts") throw new Exception("kind");
        var rows = new List<object>();
        foreach (object r in (a["rows"] as object[]) ?? new object[0]) {
            object[] pair = r as object[];
            if (pair != null && pair.Length == 2) rows.Add(new object[] { "" + pair[0], "" + pair[1] });
        }
        d[kind] = rows;
        Directory.CreateDirectory(dataDir);
        File.WriteAllText(DictPath, json.Serialize(d), new UTF8Encoding(false));
        return rows.Count;
    }

    // ---- 自訂外觀 (#22): the photos, appearance\<slot>.jpg|png|gif next to config.json ------------------------
    static readonly string[] ImageSlots = { "all", "cand", "tip", "mode", "notice", "fill", "keyboard", "bar" };
    string AppearanceDir { get { return Path.Combine(dataDir, "appearance"); } }

    static readonly string[] ImageExts = { ".jpg", ".png", ".gif" };  // (.gif: an animated GIF, #56; an APNG is a .png)

    // a file name of the folder: <slot>.jpg, .png or .gif, nothing else (a backup names them too)
    static bool ImageName(string name) {
        // (no path in it: ..\all.png or C:\x\all.png from a backup would be written outside the folder)
        if (string.IsNullOrEmpty(name) || name.IndexOfAny(new[] { '\\', '/', ':' }) >= 0 || Path.GetFileName(name) != name) return false;
        string ext = Path.GetExtension(name).ToLowerInvariant();
        return Array.IndexOf(ImageExts, ext) >= 0 && Array.IndexOf(ImageSlots, Path.GetFileNameWithoutExtension(name)) >= 0;
    }

    // 動圖 (#56): what an animation copied in may be - its file, its frames, its long side, and its frames composited
    // together (what the IME keeps of it: cpp/tip/appearance.cpp MAX_ANIM_BYTES; past that it would show a still)
    const long MaxAnimFile = 20L << 20, MaxAnimBytes = 64L << 20;
    const int MaxAnimFrames = 300, MaxAnimSide = 1200;

    static void CheckAnimation(long fileBytes, int w, int h, int frames) {
        if (fileBytes > MaxAnimFile || frames > MaxAnimFrames || Math.Max(w, h) > MaxAnimSide || (long)w * h * 4 * frames > MaxAnimBytes)
            throw new Exception("動圖太大，請換一張檔案較小、張數較少的");
    }

    // an APNG's {width, height, frames} (its acTL, before the image data), or null: a PNG of no animation, or none
    static int[] ApngInfo(byte[] b) {
        byte[] sig = { 0x89, (byte)'P', (byte)'N', (byte)'G', 13, 10, 26, 10 };
        if (b.Length < 8 || !b.Take(8).SequenceEqual(sig)) return null;
        Func<int, long> be32 = (i) => ((long)b[i] << 24) | ((long)b[i + 1] << 16) | ((long)b[i + 2] << 8) | b[i + 3];
        long w = 0, h = 0;
        for (long pos = 8; pos + 12 <= b.Length;) {
            long n = be32((int)pos);
            string type = Encoding.ASCII.GetString(b, (int)pos + 4, 4);
            if (n > b.Length - pos - 12) return null;
            if (type == "IHDR" && n >= 8) { w = be32((int)pos + 8); h = be32((int)pos + 12); }
            else if (type == "acTL" && n >= 8) return new[] { (int)Math.Min(w, int.MaxValue), (int)Math.Min(h, int.MaxValue), (int)Math.Min(be32((int)pos + 8), int.MaxValue) };
            else if (type == "IDAT") return null;
            pos += 12 + n;
        }
        return null;
    }

    void MakeAppearanceDir() {
        if (Directory.Exists(AppearanceDir)) return;
        Directory.CreateDirectory(AppearanceDir);
        // apps in an AppContainer (the Windows search box, Start, Store apps) draw the IME's windows in their own
        // process: they may read this folder (ALL APPLICATION PACKAGES), not the rest of the data folder
        try {
            var acl = Directory.GetAccessControl(AppearanceDir);
            acl.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(
                new System.Security.Principal.SecurityIdentifier("S-1-15-2-1"),
                System.Security.AccessControl.FileSystemRights.ReadAndExecute,
                System.Security.AccessControl.InheritanceFlags.ContainerInherit | System.Security.AccessControl.InheritanceFlags.ObjectInherit,
                System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
            Directory.SetAccessControl(AppearanceDir, acl);
        } catch { }  // (then those apps show the colors only)
    }

    // copies a JPG or PNG in as <slot>.jpg|png (the slot's other files go), the long side 1200 at most; an animated GIF
    // or APNG as it is (#56: <slot>.gif, an APNG <slot>.png), within the limits above; a GIF of one frame as a PNG
    public string ImportImage(string source, string slot) {
        if (Array.IndexOf(ImageSlots, slot) < 0) throw new Exception("unknown slot " + slot);
        byte[] bytes = File.ReadAllBytes(source);
        int[] apng = ApngInfo(bytes);
        if (apng != null && apng[2] > 1) {
            CheckAnimation(bytes.Length, apng[0], apng[1], apng[2]);
            return StoreImage(slot, ".png", bytes);
        }
        string ext;
        System.Drawing.Imaging.ImageFormat format;
        using (var ms = new MemoryStream(bytes)) {
            Image img;
            try { img = Image.FromStream(ms); } catch { throw new Exception("請選擇 JPG、PNG 或 GIF 圖片"); }
            using (img) {
                if (img.RawFormat.Equals(System.Drawing.Imaging.ImageFormat.Jpeg)) { ext = ".jpg"; format = System.Drawing.Imaging.ImageFormat.Jpeg; }
                else if (img.RawFormat.Equals(System.Drawing.Imaging.ImageFormat.Png)) { ext = ".png"; format = System.Drawing.Imaging.ImageFormat.Png; }
                else if (img.RawFormat.Equals(System.Drawing.Imaging.ImageFormat.Gif)) {
                    int frames = img.GetFrameCount(System.Drawing.Imaging.FrameDimension.Time);
                    if (frames > 1) {
                        CheckAnimation(bytes.Length, img.Width, img.Height, frames);
                        return StoreImage(slot, ".gif", bytes);
                    }
                    ext = ".png";  // (one frame: a still, as the others)
                    format = System.Drawing.Imaging.ImageFormat.Png;
                }
                else throw new Exception("請選擇 JPG、PNG 或 GIF 圖片");
                // a photo taken upright on a phone has EXIF orientation: turned upright here (the IME does not read
                // it), and saved again without it, so the page and the IME show the same
                if (Array.IndexOf(img.PropertyIdList, 0x0112) >= 0) {
                    byte[] o = img.GetPropertyItem(0x0112).Value;
                    switch (o != null && o.Length > 0 ? o[0] : 1) {
                        case 2: img.RotateFlip(RotateFlipType.RotateNoneFlipX); break;
                        case 3: img.RotateFlip(RotateFlipType.Rotate180FlipNone); break;
                        case 4: img.RotateFlip(RotateFlipType.Rotate180FlipX); break;
                        case 5: img.RotateFlip(RotateFlipType.Rotate90FlipX); break;
                        case 6: img.RotateFlip(RotateFlipType.Rotate90FlipNone); break;
                        case 7: img.RotateFlip(RotateFlipType.Rotate270FlipX); break;
                        case 8: img.RotateFlip(RotateFlipType.Rotate270FlipNone); break;
                    }
                }
                int longSide = Math.Max(img.Width, img.Height);
                int w = Math.Max(1, longSide > 1200 ? img.Width * 1200 / longSide : img.Width);
                int h = Math.Max(1, longSide > 1200 ? img.Height * 1200 / longSide : img.Height);
                using (var bmp = new Bitmap(w, h, System.Drawing.Imaging.PixelFormat.Format32bppArgb)) {
                    using (var g = Graphics.FromImage(bmp)) {
                        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                        g.DrawImage(img, 0, 0, w, h);
                    }
                    using (var outMs = new MemoryStream()) {
                        bmp.Save(outMs, format);
                        bytes = outMs.ToArray();
                    }
                }
            }
        }
        return StoreImage(slot, ext, bytes);
    }

    // the photo written as <slot><ext>, the slot's files of the other kinds removed
    string StoreImage(string slot, string ext, byte[] bytes) {
        MakeAppearanceDir();
        string name = slot + ext;
        File.WriteAllBytes(Path.Combine(AppearanceDir, name), bytes);
        foreach (string e in ImageExts) {
            string other = Path.Combine(AppearanceDir, slot + e);
            if (e != ext && File.Exists(other)) File.Delete(other);
        }
        return name;
    }

    // the photo as a data: URL for the page's preview, or null
    string ImageUrl(string file) {
        if (!ImageName(file)) return null;
        string p = Path.Combine(AppearanceDir, file);
        if (!File.Exists(p)) return null;
        string e = Path.GetExtension(file).ToLowerInvariant();
        string mime = e == ".png" ? "image/png" : e == ".gif" ? "image/gif" : "image/jpeg";
        return "data:" + mime + ";base64," + Convert.ToBase64String(File.ReadAllBytes(p));
    }

    void RemoveImage(string slot) {
        if (Array.IndexOf(ImageSlots, slot) < 0) return;
        foreach (string ext in ImageExts) {
            string p = Path.Combine(AppearanceDir, slot + ext);
            if (File.Exists(p)) File.Delete(p);
        }
    }

    // ---- 快速填入 (#21): quick_fill.json {"format": 1, "items": [["內容", "捷徑"]...]} -----------------------
    string QuickFillPath { get { return Path.Combine(dataDir, "quick_fill.json"); } }

    // what the page shows: no file yet (never saved) the samples, as the engines list them; a file, even empty,
    // what it holds (#33)
    List<object> ReadQuickFill() {
        return SavedQuickFill() ?? QuickFillSamples.Select(p => (object)new object[] { p[0], p[1] }).ToList();
    }

    // what the user saved, or null when never: the backup and restore go by this, never by the samples
    List<object> SavedQuickFill() { return File.Exists(QuickFillPath) ? QuickFillRows(ReadJson(QuickFillPath)) : null; }

    // 快速填入 until quick_fill.json is first saved (engine/lexicon.py QUICK_FILL_SAMPLES, cpp/engine/lexicon.cpp)
    public static readonly string[][] QuickFillSamples = {
        new[] { "sample@example.com", "email" }, new[] { "王小明", "name1" }, new[] { "John", "name2" },
        new[] { "Xiaoming Wang", "name3" }, new[] { "0912345678", "phone" }, new[] { "A123456789", "id" },
        new[] { "西元_年_月_日", "birth" }, new[] { "_市_區_路_巷_號_樓", "addr" }, new[] { "感謝您的幫忙", "tks" },
        new[] { "(*'∀')~♥", "em1" }, new[] { "( ˙ 3 ˙ )", "em2" }, new[] { "(\"∀\")", "em3" }, new[] { "∑( ° Δ °)", "em4" },
    };

    Dictionary<string, object> ReadJson(string path) {
        try { return json.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>; } catch { return null; }
    }

    static List<object> QuickFillRows(Dictionary<string, object> j) {
        var rows = new List<object>();
        object[] items = j != null && j.ContainsKey("items") ? j["items"] as object[] : null;
        if (items != null)
            foreach (object r in items) {
                object[] pair = r as object[];
                if (pair != null && pair.Length == 2 && pair[0] is string && pair[1] is string && (string)pair[0] != "")
                    rows.Add(new object[] { pair[0], pair[1] });
            }
        return rows;
    }

    // what is wrong with a row of 快速填入, or null: 內容 1-255 characters on one line, 捷徑 1-8 letters and digits
    public static string QuickFillProblem(string text, string shortcut) {
        if (string.IsNullOrEmpty(text)) return "請輸入內容";
        if (text.Length > 255) return "內容最多 255 個字";
        if (text.IndexOf('\n') >= 0 || text.IndexOf('\r') >= 0) return "內容不能換行";
        if (string.IsNullOrEmpty(shortcut) || shortcut.Length > 8 || !shortcut.All(ch => ch < 128 && char.IsLetterOrDigit(ch)))
            return "捷徑請用 1 到 8 個英文字母或數字";
        return null;
    }

    object SaveQuickFill(Dictionary<string, object> a) {
        // the page checked the rows; checked again here (a bad one, or the same 捷徑 twice: nothing written)
        var rows = new List<object>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (object r in (a["rows"] as object[]) ?? new object[0]) {
            object[] pair = r as object[];
            if (pair == null || pair.Length != 2) continue;
            string text = "" + pair[0], shortcut = "" + pair[1];
            string problem = QuickFillProblem(text, shortcut);
            if (problem != null) throw new Exception(problem);
            if (!seen.Add(shortcut)) throw new Exception("捷徑「" + shortcut + "」重複了");
            rows.Add(new object[] { text, shortcut });
        }
        Directory.CreateDirectory(dataDir);
        var data = new Dictionary<string, object> { { "format", 1 }, { "items", rows } };
        File.WriteAllText(QuickFillPath, json.Serialize(data), new UTF8Encoding(false));
        return rows.Count;
    }

    static bool IsHan(char c) { return c >= 0x3400 && c <= 0x9FFF || c >= 0xF900 && c <= 0xFAFF; }  // (as Lookup.IsHan)

    object Readings(Dictionary<string, object> a) {
        // 注音 for each word, from the dictionary the IME uses ("" if unknown)
        if (readings == null) {
            readings = new Dictionary<string, string>();
            var best = new Dictionary<string, double>();
            string lex = Find(@"data\lexicon.txt", @"data\lexicon.txt");
            if (lex != null) {
                foreach (string line in File.ReadLines(lex, Encoding.UTF8)) {
                    string[] f = line.Split('\t');
                    if (f.Length < 3) continue;
                    double score;
                    if (!double.TryParse(f[2], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out score)) continue;
                    double old;
                    if (!best.TryGetValue(f[1], out old) || score > old) { best[f[1]] = score; readings[f[1]] = f[0]; }
                }
            }
        }
        var outList = new List<object>();
        foreach (object w in (a["words"] as object[]) ?? new object[0]) {
            // each run of Chinese characters: the run as a word of the lexicon, else one character at a time;
            // English letters, digits and spaces have no 注音 (PQC功能: ㄍㄨㄥ ㄋㄥˊ, #13)
            string word = "" + w;
            var syl = new List<string>();
            int i = 0;
            while (syl != null && i < word.Length) {
                if (!IsHan(word[i])) { i++; continue; }
                int j = i;
                while (j < word.Length && IsHan(word[j])) j++;
                string run = word.Substring(i, j - i), r;
                if (readings.TryGetValue(run, out r)) syl.Add(r.Replace("-", " "));
                else
                    foreach (char ch in run) {
                        if (!readings.TryGetValue(ch.ToString(), out r)) { syl = null; break; }
                        syl.Add(r);
                    }
                i = j;
            }
            outList.Add(syl == null ? "" : string.Join(" ", syl));
        }
        return outList;
    }

    // ---- 匯出與匯入: one file with the learned picks (user.json), 個人詞庫 and the settings ----------
    // {"app": "兩儀輸入法", "format": 1, "created": "...", "memory": {...}, "dictionary": {...}, "config": {...}}
    const int BackupFormat = 1;
    static readonly string[] Counters = { "forget_words", "forget_emails", "forget_list", "import_memory" };  // this computer's own, never copied

    // ---- 符號鍵盤自訂 (#69): symbols.json, written here and read by the engine - the same rules as engine/symbols.py -------
    // {"format": 1, "pages": [{"builtin": 0|1|2|null, "name": "...", "hidden": false, "keys": [47 strings]}]} in the
    // order shown. A table the engine would refuse is never written: it would show the built-in pages instead.
    string SymbolsPath { get { return Path.Combine(dataDir, "symbols.json"); } }
    const string SymbolKeyNames = "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./";  // engine/symbols.py KEYS
    // the built-in pages, as engine/symbols.py PAGES (tests/test_engine.py checks they agree)
    public static readonly string[] BuiltinPageNames = { "一般", "數學", "特殊" };
    public static readonly string[] BuiltinPageKeys = {
        "`1234567890-=《》〈〉“”【】（）〔〕＼※＊○●「」『』：；、！？…‥—｛｝，。／",
        "≦≧＜＞∈∉∃°℃℉±－＝√∞∩∪∂∀∇Δ×÷＋≠≒μαβγδΩ∵∴∑πΦ∫∬∭§ρ∧∨λ′″",
        "〰┌┬┐¿¡☑☒✓✗©®™├┼┤↔↕↖↗↙↘←→↑↓└┴┘♫◉♀♂★■●▲₩¥£€☺☹♥♦♠♣" };

    // never starts a character of its own (engine/symbols.py _EXTEND)
    static bool SymbolExtend(int cp) {
        return (cp >= 0x0300 && cp <= 0x036F) || (cp >= 0x1AB0 && cp <= 0x1AFF) || (cp >= 0x1DC0 && cp <= 0x1DFF) ||
               cp == 0x200D || (cp >= 0x20D0 && cp <= 0x20FF) || (cp >= 0x3099 && cp <= 0x309A) ||
               (cp >= 0xFE00 && cp <= 0xFE0F) || (cp >= 0xFE20 && cp <= 0xFE2F) || (cp >= 0x1F3FB && cp <= 0x1F3FF) ||
               (cp >= 0xE0020 && cp <= 0xE007F);
    }

    static IEnumerable<string> CodePoints(string s) {
        for (int i = 0; i < s.Length; ) {
            int n = char.IsHighSurrogate(s[i]) && i + 1 < s.Length && char.IsLowSurrogate(s[i + 1]) ? 2 : 1;
            yield return s.Substring(i, n);
            i += n;
        }
    }

    static int CodePoint(string c) { return c.Length == 2 ? char.ConvertToUtf32(c[0], c[1]) : c[0]; }

    // what a reader sees as one character each (engine/symbols.py _characters)
    public static List<string> SymbolCharacters(string s) {
        var outp = new List<string>();
        bool joined = false, flag = false;
        foreach (string c in CodePoints(s)) {
            int cp = CodePoint(c);
            if (SymbolExtend(cp)) {
                if (outp.Count == 0) outp.Add(c); else outp[outp.Count - 1] += c;
                joined = cp == 0x200D;
                continue;
            }
            bool ri = cp >= 0x1F1E6 && cp <= 0x1F1FF;
            if (outp.Count > 0 && (joined || (ri && flag))) {
                outp[outp.Count - 1] += c;
                flag = false;
            } else {
                outp.Add(c);
                flag = ri;
            }
            joined = false;
        }
        return outp;
    }

    public static bool OneSymbol(string s) { return SymbolCharacters(s).Count <= 1; }

    // a page name's width: printable ASCII 1 cell, any other character 2 (engine/symbols.py name_cells)
    public static int NameCells(string s) {
        int n = 0;
        foreach (string c in SymbolCharacters(s)) n += c.Length == 1 && c[0] >= 0x20 && c[0] <= 0x7E ? 1 : 2;
        return n;
    }

    static bool BlankName(string s) {  // engine/symbols.py _BLANK
        foreach (string c in CodePoints(s)) {
            int cp = CodePoint(c);
            if (!((cp >= 0x09 && cp <= 0x0D) || (cp >= 0x1C && cp <= 0x20) || cp == 0x85 || cp == 0xA0 || cp == 0x1680 ||
                  (cp >= 0x2000 && cp <= 0x200A) || cp == 0x2028 || cp == 0x2029 || cp == 0x202F || cp == 0x205F || cp == 0x3000))
                return false;
        }
        return true;
    }

    // the first thing wrong with a table, in the user's words; null when the engine will take it
    static string SymbolProblem(object[] pages) {
        if (pages == null || pages.Length == 0) return "符號鍵盤至少要有一個類別";
        var builtins = new HashSet<int>();
        int shown = 0;
        foreach (object o in pages) {
            var p = o as Dictionary<string, object>;
            if (p == null) return "符號鍵盤的資料格式不對";
            var keys = p.ContainsKey("keys") ? p["keys"] as object[] : null;
            if (keys == null || keys.Length != SymbolKeyNames.Length) return "每個類別要有 47 個鍵";
            object hidden = p.ContainsKey("hidden") ? p["hidden"] : false;
            if (!(hidden is bool)) return "符號鍵盤的資料格式不對";
            for (int i = 0; i < keys.Length; i++) {
                string k = keys[i] as string;
                if (k == null) return "符號鍵盤的資料格式不對";
                if (!OneSymbol(k))
                    return "「" + char.ToUpperInvariant(SymbolKeyNames[i]) + "」鍵只能放一個字或一個 emoji（較長的內容請用「快速填入」）";
            }
            object b = p.ContainsKey("builtin") ? p["builtin"] : null;
            if (b == null) {
                string name = p.ContainsKey("name") ? p["name"] as string : null;
                if (name == null || BlankName(name)) return "類別名稱不能空白";
                if (NameCells(name) > 8) return "類別名稱「" + name + "」太長（最多 4 個中文字或 8 個英文字母）";
            } else if (!(b is int) || (int)b < 0 || (int)b >= BuiltinPageNames.Length || !builtins.Add((int)b)) {
                return "符號鍵盤的資料格式不對";
            }
            if (!(bool)hidden) shown++;
        }
        if (shown == 0) return "至少要顯示一個類別";
        if (shown > 10) return "符號鍵盤上最多顯示 10 個類別";
        return null;
    }

    static List<object> BuiltinSymbolPages() {
        var pages = new List<object>();
        for (int i = 0; i < BuiltinPageNames.Length; i++)
            pages.Add(new Dictionary<string, object> {
                { "builtin", i }, { "name", BuiltinPageNames[i] }, { "hidden", false },
                { "keys", BuiltinPageKeys[i].Select(ch => ch.ToString()).ToArray() } });
        return pages;
    }

    // the table as the editor needs it: every page, hidden ones too; the built-in three without a file, or with one
    // the engine would refuse (it shows the built-in pages then too)
    object ReadSymbols() {
        var data = File.Exists(SymbolsPath) ? ReadJson(SymbolsPath) : null;
        object[] pages = data != null && data.ContainsKey("pages") ? data["pages"] as object[] : null;
        if (SymbolProblem(pages) != null) pages = BuiltinSymbolPages().ToArray();
        return new Dictionary<string, object> { { "pages", pages }, { "builtinNames", BuiltinPageNames } };
    }

    // writes symbols.json (a built-in page under its own name, whatever it was sent with), or {"error": ...}
    object SaveSymbols(Dictionary<string, object> a) {
        var pages = a != null && a.ContainsKey("pages") ? a["pages"] as object[] : null;
        string problem = SymbolProblem(pages);
        if (problem != null) return new Dictionary<string, object> { { "error", problem } };
        var stored = new List<object>();
        foreach (object o in pages) {
            var p = (Dictionary<string, object>)o;
            object b = p.ContainsKey("builtin") ? p["builtin"] : null;
            stored.Add(new Dictionary<string, object> {
                { "builtin", b }, { "name", b == null ? p["name"] : BuiltinPageNames[(int)b] },
                { "hidden", p.ContainsKey("hidden") && (bool)p["hidden"] }, { "keys", p["keys"] } });
        }
        Directory.CreateDirectory(dataDir);
        string tmp = SymbolsPath + ".tmp";
        File.WriteAllText(tmp, json.Serialize(new Dictionary<string, object> { { "format", 1 }, { "pages", stored } }),
                          new UTF8Encoding(false));
        File.Copy(tmp, SymbolsPath, true);  // (the engine reads it again when it changes)
        File.Delete(tmp);
        return true;
    }

    // A backup leaves out the rows waiting in forget_list (deleted in 記憶清單, the engine not there yet), from where
    // each kind is kept - as engine/lexicon.py forget_items removes them (the list itself is a Counter: never copied)
    // above: only the rows with a higher id (ForgetMemory: the rows of this call - findings #119)
    void DropWaiting(Dictionary<string, object> memory, int above = 0) {
        var config = ReadConfig();
        int applied = Math.Max(AppliedForget(), above);
        Action<Dictionary<string, object>, string, string> nested = (table, key, value) => {
            var bucket = Sub(table, key);
            if (bucket == null) return;
            bucket.Remove(value);
            if (bucket.Count == 0) table.Remove(key);
        };
        var used = Sub(memory, "used");
        var keys = Sub(Sub(memory, "keys"), "standard");
        var simp = Sub(memory, "simplified");
        foreach (object o in (config.ContainsKey("forget_list") ? config["forget_list"] as object[] : null) ?? new object[0]) {
            var it = o as Dictionary<string, object>;
            double id = Num(it != null && it.ContainsKey("id") ? it["id"] : null);
            if (double.IsNaN(id) || id <= applied) continue;
            string kind = it.ContainsKey("kind") ? it["kind"] as string : null;
            string key = it.ContainsKey("key") ? it["key"] as string : null;
            string value = it.ContainsKey("value") ? it["value"] as string : null;
            if (kind == null || key == null || value == null) continue;
            switch (kind) {
                case "pick":
                    if (Sub(memory, "phrases") != null) nested(Sub(memory, "phrases"), key, value);
                    if (Sub(used, "phrases") != null) nested(Sub(used, "phrases"), key, value);
                    break;
                case "word": case "email": {
                    string name = kind == "word" ? "words" : "emails";
                    if (Sub(memory, name) != null) Sub(memory, name).Remove(key);
                    if (Sub(used, name) != null) Sub(used, name).Remove(key);
                    break;
                }
                case "correction": case "english": {
                    string choice = kind == "english" ? AsEnglish : value;
                    if (Sub(keys, "picks") != null) nested(Sub(keys, "picks"), key, choice);
                    if (Sub(keys, "used") != null) nested(Sub(keys, "used"), key, choice);
                    if (kind == "english" && memory.ContainsKey("english") && memory["english"] is object[])
                        memory["english"] = ((object[])memory["english"]).Where(e => (e as string) != key).ToArray();
                    break;
                }
                case "simp_pick":
                    if (Sub(simp, "phrases") != null) nested(Sub(simp, "phrases"), key, value);
                    if (Sub(Sub(simp, "used"), "phrases") != null) nested(Sub(Sub(simp, "used"), "phrases"), key, value);
                    break;
                case "simp_term":
                    if (Sub(simp, "replacements") != null) nested(Sub(simp, "replacements"), key, value);
                    if (Sub(simp, "replacements_used") != null) nested(Sub(simp, "replacements_used"), key, value);
                    break;
            }
        }
    }

    public bool WriteBackup(string path) {
        var config = ReadConfig();
        foreach (string k in Counters) config.Remove(k);
        Dictionary<string, object> memory = null;
        try { memory = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "user.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        if (memory != null) memory.Remove("forgot");
        // 校正記憶 and 代碼記憶 (keys.json, a file of their own) go with the memory
        Dictionary<string, object> corrections = null;
        try { corrections = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "keys.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        if (corrections != null) {
            if (memory == null) memory = new Dictionary<string, object>();
            memory["keys"] = corrections;
        }
        // 自訂詞彙 taken with Tab (custom_picks.json, a file of its own) too
        Dictionary<string, object> customPicks = null;
        try { customPicks = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "custom_picks.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        if (customPicks != null) {
            if (memory == null) memory = new Dictionary<string, object>();
            memory["custom_picks"] = customPicks;
        }
        // 簡體輸出's own memory (user_simplified.json, #32) too; the IME merges it on restore (import_memory)
        Dictionary<string, object> simplified = null;
        try { simplified = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "user_simplified.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        if (simplified != null) {
            if (memory == null) memory = new Dictionary<string, object>();
            memory["simplified"] = simplified;
        }
        if (memory != null) DropWaiting(memory);  // 記憶清單 (#65): rows deleted, the engine not there yet
        var data = new Dictionary<string, object> {
            { "app", Migrate.AppName }, { "format", BackupFormat }, { "created", DateTime.Now.ToString("yyyy-MM-dd HH:mm") },
            { "memory", memory ?? new Dictionary<string, object>() }, { "dictionary", ReadDictionary() }, { "config", config },
            { "appearance_images", BackupImages() }  // (自訂外觀's photos, #22: {file: base64}; restored with 設定)
        };
        // 快速填入 (#21: an older version reading the backup leaves it), only once saved: the samples shown before
        // that are not the user's and would join another computer's list on restore (#33)
        var savedFill = SavedQuickFill();
        if (savedFill != null) data["quick_fill"] = savedFill;
        // 符號鍵盤自訂 (#69), restored with 設定 (an older version reading the backup leaves it); the built-in pages are
        // carried too, as {"builtin": true}: restored, they replace a table of the other computer's own (findings #124)
        var symbols = File.Exists(SymbolsPath) ? ReadJson(SymbolsPath) : null;
        data["symbols"] = symbols ?? new Dictionary<string, object> { { "format", 1 }, { "builtin", true } };
        File.WriteAllText(path, json.Serialize(data), new UTF8Encoding(false));
        return true;
    }

    // What the backup holds, for the 匯入 window; {"error": ...} if it is not one
    public object ReadBackup(string path) {
        backup = null;
        Dictionary<string, object> data = null;
        try { data = json.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        string app = data == null ? null : Str(data, "app");
        if (app != Migrate.AppName && app != Migrate.OldAppName) return new Dictionary<string, object> { { "error", "這不是兩儀輸入法匯出的檔案" } };
        int format;
        if (!int.TryParse(Str(data, "format"), out format) || format > BackupFormat)
            return new Dictionary<string, object> { { "error", "這個檔案來自較新版本的兩儀輸入法，請先更新" } };
        data = Migrate.Backup(data);  // a 2.x file (智慧輸入) becomes 3.0 here
        backup = data;
        var memory = data.ContainsKey("memory") ? data["memory"] as Dictionary<string, object> : null;
        int learned = 0;
        if (memory != null) {
            var phrases = memory.ContainsKey("phrases") ? memory["phrases"] as Dictionary<string, object> : null;
            if (phrases != null)
                foreach (object bucket in phrases.Values) {
                    var b = bucket as Dictionary<string, object>;
                    if (b != null) learned += b.Count;
                }
            var words = memory.ContainsKey("words") ? memory["words"] as Dictionary<string, object> : null;
            if (words != null) learned += words.Count;
            var emails = memory.ContainsKey("emails") ? memory["emails"] as Dictionary<string, object> : null;
            if (emails != null) learned += emails.Count;
            // 校正記憶 and 代碼記憶 count too: their key strings
            var corrections = memory.ContainsKey("keys") ? memory["keys"] as Dictionary<string, object> : null;
            var part = corrections != null && corrections.ContainsKey("standard") ? corrections["standard"] as Dictionary<string, object> : null;
            var picks = part != null && part.ContainsKey("picks") ? part["picks"] as Dictionary<string, object> : null;
            if (picks != null) learned += picks.Count;
        }
        var dict = BackupDictionary();
        return new Dictionary<string, object> {
            { "created", Str(data, "created") }, { "memory", learned },
            { "words", ((List<object>)dict["words"]).Count }, { "shortcuts", ((List<object>)dict["shortcuts"]).Count },
            { "quickFill", BackupQuickFill().Count },
            { "config", data.ContainsKey("config") && data["config"] is Dictionary<string, object> }
        };
    }

    Dictionary<string, object> BackupImages() {
        var images = new Dictionary<string, object>();
        if (Directory.Exists(AppearanceDir))
            foreach (string p in Directory.GetFiles(AppearanceDir))
                if (ImageName(Path.GetFileName(p))) images[Path.GetFileName(p)] = Convert.ToBase64String(File.ReadAllBytes(p));
        return images;
    }

    List<object> BackupQuickFill() {
        var j = backup != null ? new Dictionary<string, object> { { "items", backup.ContainsKey("quick_fill") ? backup["quick_fill"] : null } } : null;
        return QuickFillRows(j);
    }

    Dictionary<string, object> BackupDictionary() {
        var d = new Dictionary<string, object> { { "words", new List<object>() }, { "shortcuts", new List<object>() } };
        var j = backup != null && backup.ContainsKey("dictionary") ? backup["dictionary"] as Dictionary<string, object> : null;
        foreach (string kind in new[] { "words", "shortcuts" }) {
            object[] rows = j != null && j.ContainsKey(kind) ? j[kind] as object[] : null;
            if (rows == null) continue;
            foreach (object r in rows) {
                object[] pair = r as object[];
                if (pair != null && pair.Length == 2 && ("" + pair[0]) != "") ((List<object>)d[kind]).Add(new object[] { "" + pair[0], "" + pair[1] });
            }
        }
        return d;
    }

    // Merged into what is here: nothing here is removed, and a row already here is kept as it is
    public object RestoreBackup(bool memory, bool words, bool shortcuts, bool config, bool quickFill = false) {
        if (backup == null) throw new Exception("no backup opened");
        var c = ReadConfig();
        var theirConfig = backup.ContainsKey("config") ? backup["config"] as Dictionary<string, object> : null;
        if (config && theirConfig != null) {
            // a backup of before #23 has no candidate layout: its cand_per_row is the layout then, the keys 1-9
            if (!theirConfig.ContainsKey("cand_layout"))
                foreach (var k in new[] { "cand_layout", "cand_keys", "cand_columns" }) c[k] = Defaults[k];
            foreach (var kv in theirConfig)
                if (Defaults.ContainsKey(kv.Key) && Array.IndexOf(Counters, kv.Key) < 0) c[kv.Key] = kv.Value;
            // 自訂外觀's photos come with the settings (a backup of before #22 has none: those here stay)
            var images = backup.ContainsKey("appearance_images") ? backup["appearance_images"] as Dictionary<string, object> : null;
            if (images != null)
                foreach (var kv in images) {
                    if (!ImageName(kv.Key) || !(kv.Value is string)) continue;
                    byte[] bytes;
                    try { bytes = Convert.FromBase64String((string)kv.Value); } catch { continue; }
                    MakeAppearanceDir();
                    File.WriteAllBytes(Path.Combine(AppearanceDir, kv.Key), bytes);
                }
        }
        // 符號鍵盤自訂 (#69) comes with 設定, the whole table; a backup without it (older) leaves this one. The built-in
        // pages, or a table the engine would refuse (that computer showed the built-in pages), replace this one's own
        // (findings #124)
        var theirSymbols = config && backup.ContainsKey("symbols") ? backup["symbols"] as Dictionary<string, object> : null;
        if (theirSymbols != null) {
            var pages = theirSymbols.ContainsKey("pages") ? theirSymbols["pages"] as object[] : null;
            if (SymbolProblem(pages) == null) SaveSymbols(new Dictionary<string, object> { { "pages", pages } });
            else if (File.Exists(SymbolsPath)) File.Delete(SymbolsPath);
        }
        int addedWords = 0, addedShortcuts = 0;
        if (words || shortcuts) {
            var d = ReadDictionary();
            var theirs = BackupDictionary();
            var mine = (List<object>)d["words"];
            var maps = (List<object>)d["shortcuts"];
            Func<object, int, string> cell = (row, i) => "" + ((object[])row)[i];
            if (words)
                foreach (object r in (List<object>)theirs["words"])
                    if (!mine.Any(m => string.Equals(cell(m, 0), cell(r, 0), StringComparison.OrdinalIgnoreCase))) { mine.Add(r); addedWords++; }
            if (shortcuts)
                foreach (object r in (List<object>)theirs["shortcuts"])
                    if (!maps.Any(m => string.Equals(cell(m, 0), cell(r, 0), StringComparison.OrdinalIgnoreCase) || cell(m, 1) == cell(r, 1))) { maps.Add(r); addedShortcuts++; }
            Directory.CreateDirectory(dataDir);
            File.WriteAllText(DictPath, json.Serialize(d), new UTF8Encoding(false));
        }
        int addedQuickFill = 0;
        if (quickFill) {  // 快速填入: a 捷徑 already here is kept as it is
            // (what the file holds: never saved, nothing here; the samples ReadQuickFill shows then would keep the
            // backup's own email, phone ... out, #33)
            var saved = SavedQuickFill();
            var mine = saved ?? new List<object>();
            foreach (object r in BackupQuickFill()) {
                object[] pair = (object[])r;
                string text = "" + pair[0], shortcut = "" + pair[1];
                if (QuickFillProblem(text, shortcut) != null) continue;
                if (mine.Any(m => string.Equals("" + ((object[])m)[1], shortcut, StringComparison.OrdinalIgnoreCase))) continue;
                mine.Add(new object[] { text, shortcut });
                addedQuickFill++;
            }
            if (saved != null || addedQuickFill > 0) {  // (never saved and nothing taken: the samples stay)
                Directory.CreateDirectory(dataDir);
                File.WriteAllText(QuickFillPath, json.Serialize(new Dictionary<string, object> { { "format", 1 }, { "items", mine } }),
                                  new UTF8Encoding(false));
            }
        }
        var theirMemory = backup.ContainsKey("memory") ? backup["memory"] as Dictionary<string, object> : null;
        if (memory && theirMemory != null) {
            // only the IME writes user.json: it merges this file on its next key (import_memory bumped)
            Directory.CreateDirectory(dataDir);
            File.WriteAllText(Path.Combine(dataDir, "import-memory.json"), json.Serialize(theirMemory), new UTF8Encoding(false));
            c["import_memory"] = Convert.ToInt32(c.ContainsKey("import_memory") ? c["import_memory"] : 0) + 1;
        }
        WriteConfig(c);
        backup = null;
        return new Dictionary<string, object> { { "words", addedWords }, { "shortcuts", addedShortcuts }, { "quickFill", addedQuickFill } };
    }

    // ---- updates ------------------------------------------------------------------------------
    object RunUpdate(bool checkOnly) {
        // {code: 0 latest / 10 available / 20 cancelled / other = error code, version: "3.1.0",
        //  and for a check: stable / beta = the newest of each line} (Updater.cs)
        bool beta = (Registry.GetValue(@"HKEY_CURRENT_USER\" + OurKey, "UpdateBranch", "") as string) == "develop";
        string version = "", stable = "", newest = "";
        int code;
        try { code = checkOnly ? Updater.Check(beta, out version, out stable, out newest) : Updater.Run(beta, out version); }
        catch { code = 101; }
        var result = new Dictionary<string, object> { { "code", code }, { "version", version } };
        if (checkOnly) { result["stable"] = stable; result["beta"] = newest; }
        return result;
    }

    // ---- 移除 -----------------------------------------------------------------------------------
    // uninstall.exe next to this program does it (in its own window, asking for administrator rights
    // itself); this window closes so nothing of it is in use in the install folder
    object Uninstall(bool removePime, bool removeData) {
        string exe = Path.Combine(exeDir, "uninstall.exe");
        string args = "--uninstall" + (removeData ? " --remove-data" : "") + (removePime ? " --remove-pime" : "");
        try {
            // (the current folder: not the install folder, which it deletes)
            Process p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = true, WorkingDirectory = Path.GetTempPath() });
            if (p != null) p.Dispose();
        } catch (Exception) {
            return false;  // not started (no uninstall.exe here: a development build)
        }
        form.BeginInvoke(new Action(() => form.Close()));
        return true;
    }
}
