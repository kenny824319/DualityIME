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
        { "autocorrect", false }, { "long_sentence", true }, { "punctuation", "half" }, { "space", "half" },
        { "full_width", false }, { "ctrl_punct", false }, { "preview", "keys_hint" }, { "shift_toggle", true }, { "start_mode", "mixed" },
        { "completion", true }, { "remember_words", true }, { "forget_words", 0 }, { "forget_emails", 0 }, { "import_memory", 0 }, { "lookup", true },
        { "lookup_size", "small" }, { "game_mode", false }, { "cand_font", "Microsoft JhengHei UI" },
        { "cand_font_size", 18 }, { "cand_per_row", 1 }
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
            case "restoreBackup": return RestoreBackup(Bool(a, "memory"), Bool(a, "words"), Bool(a, "shortcuts"), Bool(a, "config"));
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
            { "words", ((List<object>)dict["words"]).Count },
            { "shortcuts", ((List<object>)dict["shortcuts"]).Count },
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

    // 已記憶項目: { N, M }. N = the typing habits 全部清除 clears: phrase picks (each reading and
    // phrase), keys learned as English (one letter or digit is not: the engine ignores it), English words, and
    // the key strings in 校正記憶 / 代碼記憶 (keys.json). M = the remembered e-mail addresses.
    // 全部清除 asked and the engine not there yet (it clears at its next key: config.json's forget_words
    // above the forgot it noted in user.json, the same test as the engine's): N counts 0, and M too
    // for forget_emails, so the page shows at once what the engine is about to leave.
    public int[] RememberedCount() {
        int habits = 0, emails = 0;
        Dictionary<string, object> user = null, keys = null;
        try { user = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "user.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        try { keys = json.DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "keys.json"), Encoding.UTF8)) as Dictionary<string, object>; } catch { }
        var config = ReadConfig();
        bool clearHabits = Long(config, "forget_words") > Long(user, "forgot");
        bool clearEmails = Long(config, "forget_emails") > Long(user, "forgot_emails");
        if (clearHabits) {
            keys = null;
            if (user != null) { user.Remove("phrases"); user.Remove("english"); user.Remove("words"); }
        }
        if (clearEmails && user != null) user.Remove("emails");
        if (user != null) {
            var phrases = user.ContainsKey("phrases") ? user["phrases"] as Dictionary<string, object> : null;
            if (phrases != null)
                foreach (object bucket in phrases.Values) {
                    var b = bucket as Dictionary<string, object>;
                    if (b != null) habits += b.Count;
                }
            var english = user.ContainsKey("english") ? user["english"] as object[] : null;
            if (english != null)
                foreach (object e in english) {
                    string s = e as string;
                    if (s != null && !Migrate.IsOneKey(s)) habits++;
                }
            var words = user.ContainsKey("words") ? user["words"] as Dictionary<string, object> : null;
            if (words != null) habits += words.Count;
            var mails = user.ContainsKey("emails") ? user["emails"] as Dictionary<string, object> : null;
            if (mails != null) emails = mails.Count;
        }
        var part = keys != null && keys.ContainsKey("standard") ? keys["standard"] as Dictionary<string, object> : null;
        var picks = part != null && part.ContainsKey("picks") ? part["picks"] as Dictionary<string, object> : null;
        if (picks != null) habits += picks.Count;
        return new[] { habits, emails };
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

    object SetConfig(string key, object value) {
        if (!Defaults.ContainsKey(key)) throw new Exception("unknown setting " + key);
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
            string word = "" + w;
            string r;
            if (readings.TryGetValue(word, out r)) { outList.Add(r.Replace("-", " ")); continue; }
            var syl = new List<string>();
            foreach (char ch in word) {
                if (!readings.TryGetValue(ch.ToString(), out r)) { syl = null; break; }
                syl.Add(r);
            }
            outList.Add(syl == null ? "" : string.Join(" ", syl));
        }
        return outList;
    }

    // ---- 匯出與匯入: one file with the learned picks (user.json), 個人詞庫 and the settings ----------
    // {"app": "兩儀輸入法", "format": 1, "created": "...", "memory": {...}, "dictionary": {...}, "config": {...}}
    const int BackupFormat = 1;
    static readonly string[] Counters = { "forget_words", "forget_emails", "import_memory" };  // this computer's own, never copied

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
        var data = new Dictionary<string, object> {
            { "app", Migrate.AppName }, { "format", BackupFormat }, { "created", DateTime.Now.ToString("yyyy-MM-dd HH:mm") },
            { "memory", memory ?? new Dictionary<string, object>() }, { "dictionary", ReadDictionary() }, { "config", config }
        };
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
            { "config", data.ContainsKey("config") && data["config"] is Dictionary<string, object> }
        };
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
    public object RestoreBackup(bool memory, bool words, bool shortcuts, bool config) {
        if (backup == null) throw new Exception("no backup opened");
        var c = ReadConfig();
        var theirConfig = backup.ContainsKey("config") ? backup["config"] as Dictionary<string, object> : null;
        if (config && theirConfig != null)
            foreach (var kv in theirConfig)
                if (Defaults.ContainsKey(kv.Key) && Array.IndexOf(Counters, kv.Key) < 0) c[kv.Key] = kv.Value;
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
        var theirMemory = backup.ContainsKey("memory") ? backup["memory"] as Dictionary<string, object> : null;
        if (memory && theirMemory != null) {
            // only the IME writes user.json: it merges this file on its next key (import_memory bumped)
            Directory.CreateDirectory(dataDir);
            File.WriteAllText(Path.Combine(dataDir, "import-memory.json"), json.Serialize(theirMemory), new UTF8Encoding(false));
            c["import_memory"] = Convert.ToInt32(c.ContainsKey("import_memory") ? c["import_memory"] : 0) + 1;
        }
        WriteConfig(c);
        backup = null;
        return new Dictionary<string, object> { { "words", addedWords }, { "shortcuts", addedShortcuts } };
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
