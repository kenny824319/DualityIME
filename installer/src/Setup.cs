// 兩儀輸入法 installer (DualityIME-X.Y.Z-setup.exe): one file that carries everything it installs
// (payload.zip, embedded by installer\build.ps1 -Release) and works from any folder. It runs as the
// user (setup.manifest: asInvoker) and asks for administrator rights once:
//   1. (user)          payload -> %TEMP%\DualityIME-setup-<ticks>\ (VERSION, user.ps1: what runs as the user);
//                      a first install = no trace of 2.x (智慧輸入) or 3.0; this user's keyboard-list
//                      entries of ours are noted
//   2. (administrator) this file again with --machine: it unpacks its own payload once more, into
//                      %ProgramFiles%\DualityIME.setup-<ticks>\ (a folder only administrators can write,
//                      checked: nothing running as the user can swap install.ps1 or a DLL before it runs),
//                      runs install.ps1 from there (stops the engines, removes the 2.x program, puts the
//                      files into %ProgramFiles%\DualityIME, registers the DLLs, HKLM entries, shortcut),
//                      then deletes all of that folder but install.log (it and the folder go when Windows
//                      next starts). Step 1 picks <ticks> and reads the log from there once step 2 ended:
//                      the administrator never writes into a folder this user can write to.
//   3. (user)          %APPDATA%\SmartZhuyin -> %APPDATA%\DualityIME (Migrate.cs), then user.ps1:
//                      HKCU values, the keyboard list (only when no program was installed: a first
//                      install, or again after uninstalling), starts the engine
// The same file without a payload is uninstall.exe in the install folder. Uninstalling (either file):
//   1. (user)          uninstall.ps1, user.ps1, language.ps1, remove-pime.ps1 -> %TEMP%\DualityIME-uninstall-<ticks>\
//                      (from the payload, else from next to this file; the install folder is deleted);
//                      this user runs user.ps1 from there, and the go file is there
//   2. (administrator) this file again with --uninstall-machine: the same scripts copied once more, by
//                      itself, into %ProgramFiles%\DualityIME.uninstall-<ticks>\ (only administrators can
//                      write there, checked), uninstall.ps1 started from there, waiting: refused = nothing
//                      has changed (exit 20)
//   3. (user)          user.ps1 -Uninstall: the default input method back, ours out of the keyboard list
//                      (中文(台灣) stays); then "go" to uninstall.ps1 (a failure: "stop", nothing removed)
//   4. (administrator) uninstall.ps1: engines, Run value, regsvr32 /u, PIME, the folder, Settings > Apps, shortcut;
//                      its log stays in its own folder (deleted when Windows next starts) for step 1 to read
//   5. (user)          user.ps1 -Cleanup [-RemoveUserData]: hotkeys, HKCU\Software\DualityIME,
//                      %LOCALAPPDATA%\DualityIME, %APPDATA%\DualityIME when asked
//   setup.exe [--update] [--expect-version X.Y.Z] [--no-pause]
//   setup.exe --uninstall [--remove-data] [--remove-pime] [--all] [--no-pause]     (--all = both)
//   setup.exe --list-payload       prints the files it carries (the build's check)
//   setup.exe --machine --expect-version X.Y.Z --ticks <n> [--update] [--from2x]   (install step 2 only; administrator)
//   setup.exe --uninstall-machine --ticks <n> --wait-for <file> [--remove-pime]      (uninstall step 2 only; administrator)
// Exit codes: 0 ok, 20 administrator rights refused, 40 the payload is not the --expect-version
// (checked before anything is touched; the updater passes it), other = failure.
// Mostly written with Claude (claude-opus-5-5).
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32;

static class Setup {
    public const int Ok = 0, Failed = 1, Cancelled = 20, WrongVersion = 40;
    const string Clsid = "{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}";
    const string UninstallKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\";
    const string RunKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
    static readonly string[] Names = { "SmartZhuyin", "DualityIME" };   // 2.x, 3.0

    static int Main(string[] args) {
        try { Console.OutputEncoding = new UTF8Encoding(false); } catch { }
        var a = args.ToList();
        if (a.Contains("--list-payload")) return ListPayload();
        if (a.Contains("--uninstall-machine")) return UninstallMachine(a);   // (uninstall step 2: hidden, no pause)
        string expect = Arg(a, "--expect-version");
        if (expect != null) {
            string have = null;
            using (Stream s = OpenPayload()) if (s != null) have = PayloadVersion(s);
            if (have != expect.Trim()) {
                Console.WriteLine("這個安裝檔是 " + (have ?? "（沒有版本）") + "，不是要安裝的 " + expect + "，不安裝。");
                return WrongVersion;
            }
        }
        if (a.Contains("--machine")) return Machine(a);   // (install step 2: no pause; step 1 reads its log)
        bool update = a.Contains("--update");
        bool uninstall = a.Contains("--uninstall");
        int code;
        try { code = uninstall ? Uninstall(ParseUninstall(a)) : Install(update); }
        catch (Exception e) {
            Console.WriteLine();
            Console.WriteLine((uninstall ? "移除失敗：" : "安裝失敗：") + e.Message);
            code = Failed;
        }
        if ((uninstall || !update) && !a.Contains("--no-pause")) {
            Console.WriteLine();
            Console.Write("按 Enter 關閉");
            Console.ReadLine();
        }
        return code;
    }

    static string Arg(List<string> a, string name) {
        int i = a.IndexOf(name);
        return i >= 0 && i + 1 < a.Count ? a[i + 1] : null;
    }

    // ---- the payload ------------------------------------------------------------------------------
    static Stream OpenPayload() {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip");
    }

    public static List<string> PayloadFiles(Stream zip) {
        using (var z = new ZipArchive(zip, ZipArchiveMode.Read, true))
            return z.Entries.Where(e => e.Name != "").Select(e => e.FullName.Replace('/', '\\')).ToList();
    }

    // the first line of the payload's VERSION, null when it has none
    public static string PayloadVersion(Stream zip) {
        using (var z = new ZipArchive(zip, ZipArchiveMode.Read, true)) {
            ZipArchiveEntry e = z.GetEntry("VERSION");
            if (e == null) return null;
            using (var r = new StreamReader(e.Open(), Encoding.UTF8)) {
                string line = r.ReadLine();
                return line == null ? null : line.Trim();
            }
        }
    }

    static int ListPayload() {
        using (Stream s = OpenPayload()) {
            if (s == null) { Console.WriteLine("no payload"); return Failed; }
            foreach (string f in PayloadFiles(s)) Console.WriteLine(f);
        }
        return Ok;
    }

    // ---- what is on this computer -----------------------------------------------------------------
    // Traces of an earlier install, 2.x (智慧輸入) or 3.0 (spec 7.1); none = a first install
    public static List<string> Traces(string appData, string programFiles, Func<string, bool> hkcuKey,
                                      Func<string, bool> hklmKey, Func<string, string, bool> hklmValue) {
        var found = new List<string>();
        foreach (string name in Names) {
            if (Directory.Exists(Path.Combine(appData, name))) found.Add(@"%APPDATA%\" + name);
            // the program folder only while it holds the engine: what an upgrade or an uninstall left there
            // until the next restart (files still in use, scheduled for delete) is no install
            if (File.Exists(Path.Combine(programFiles, name, name + "Engine.exe"))) found.Add(@"%ProgramFiles%\" + name);
            if (hkcuKey(@"Software\" + name)) found.Add(@"HKCU\Software\" + name);
            if (hklmKey(UninstallKey + name)) found.Add(@"HKLM\" + UninstallKey + name);
            if (hklmValue(RunKey, name + "Engine")) found.Add(@"HKLM\" + RunKey + " " + name + "Engine");
        }
        return found;
    }

    // Of those traces, a program installed (2.x or 3.0), not only data an uninstall left (%APPDATA%):
    // without one, ours is not in the keyboard list (uninstalling took it out) and goes in again
    public static bool ProgramInstalled(IEnumerable<string> traces) {
        return traces.Any(t => !t.StartsWith(@"%APPDATA%\") && !t.StartsWith(@"HKCU\"));
    }

    // A 2.x program (not only its data) was there: programs already open still hold its old TSF DLL
    // and cannot reach the removed engine, so only signing out (or a restart) finishes the upgrade
    public static bool UpgradedFrom2x(IEnumerable<string> traces) {
        return traces.Any(t => t == @"%ProgramFiles%\SmartZhuyin"
                            || t == @"HKLM\" + UninstallKey + "SmartZhuyin"
                            || t == @"HKLM\" + RunKey + " SmartZhuyinEngine");
    }

    static RegistryKey Machine() { return RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64); }

    static bool HasKey(RegistryKey root, string path) {
        using (RegistryKey k = root.OpenSubKey(path)) return k != null;
    }

    static List<string> TracesHere() {
        Func<string, bool> hkcu = p => HasKey(Registry.CurrentUser, p);
        Func<string, bool> hklm = p => { using (RegistryKey m = Machine()) return HasKey(m, p); };
        Func<string, string, bool> value = (p, n) => {
            using (RegistryKey m = Machine())
            using (RegistryKey k = m.OpenSubKey(p))
                return k != null && k.GetValue(n) != null;
        };
        return Traces(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), ProgramFiles(), hkcu, hklm, value);
    }

    static string ProgramFiles() {
        // the 64-bit one (this runs as a 64-bit process on 64-bit Windows; the variable also tells a 32-bit one)
        string p = Environment.GetEnvironmentVariable("ProgramW6432");
        return string.IsNullOrEmpty(p) ? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) : p;
    }

    // Our entries ("0404:{CLSID}{profile}") among the names in this user's keyboard list
    // (HKCU\Control Panel\International\User Profile\<language>, one value per input method)
    public static List<string> OurTips(IEnumerable<string> names) {
        return names.Where(n => n.IndexOf(Clsid, StringComparison.OrdinalIgnoreCase) > 0 && n.IndexOf(':') == 4)
                    .Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    // Before anything changes: what of ours is in this user's keyboard list, and the default input method
    // when it is ours. A safety net for "an upgrade leaves the keyboard list and the default as they were":
    // user.ps1 puts back what of these is missing afterwards (install.ps1 never unregisters anything).
    static void NoteKeyboardList(out List<string> tips, out string defaultTip) {
        var names = new List<string>();
        defaultTip = "";
        try {
            using (RegistryKey up = Registry.CurrentUser.OpenSubKey(@"Control Panel\International\User Profile")) {
                if (up != null) {
                    foreach (string lang in up.GetSubKeyNames())
                        using (RegistryKey k = up.OpenSubKey(lang))
                            if (k != null) names.AddRange(k.GetValueNames());
                    string d = up.GetValue("InputMethodOverride") as string;
                    if (d != null && OurTips(new[] { d }).Count > 0) defaultTip = d;
                }
            }
        } catch { }
        tips = OurTips(names);
    }

    static string WindowsProblem() {
        Version os = Environment.OSVersion.Version;   // (setup.manifest declares Windows 10: the real version)
        if (os.Major < 10) return "需要 Windows 10 或 11，這台是 " + os + "。";
        string arch = null;
        try {
            using (RegistryKey m = Machine())
            using (RegistryKey k = m.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
                if (k != null) arch = k.GetValue("PROCESSOR_ARCHITECTURE") as string;
        } catch { }
        if (!Environment.Is64BitOperatingSystem || (arch != null && arch != "AMD64"))
            return "目前只支援 64 位元的 Intel／AMD 電腦（這台是 " + (arch ?? "32 位元") + "）。";
        return null;
    }

    // ---- running the scripts ----------------------------------------------------------------------
    static string Quote(string s) { return "\"" + s.Replace("\"", "") + "\""; }

    static string PowerShellArgs(string script, IEnumerable<string> args, bool nonInteractive = false) {
        return "-NoProfile " + (nonInteractive ? "-NonInteractive " : "") + "-ExecutionPolicy Bypass -File " + Quote(script) + " " +
               string.Join(" ", args.Select(x => x.StartsWith("-") ? x : Quote(x)));
    }

    // with administrator rights (Windows asks): the process, or null when the prompt was refused
    static Process StartElevated(string exe, string arguments, bool hidden) {
        var psi = new ProcessStartInfo(exe, arguments) {
            UseShellExecute = true, Verb = "runas",
            WindowStyle = hidden ? ProcessWindowStyle.Hidden : ProcessWindowStyle.Normal
        };
        try {
            Process p = Process.Start(psi);
            if (p == null) throw new Exception("無法啟動 " + Path.GetFileName(exe));
            return p;
        } catch (Win32Exception e) {
            if (e.NativeErrorCode == 1223) return null;   // ERROR_CANCELLED: the prompt was refused
            throw;
        }
    }

    // this file again with administrator rights (step 2): the process, or null when the prompt was refused
    static Process StartElevatedSelf(IEnumerable<string> args, bool hidden) {
        string self = Assembly.GetExecutingAssembly().Location;
        return StartElevated(self, string.Join(" ", args.Select(x => x.StartsWith("-") ? x : Quote(x))), hidden);
    }

    // ... and its exit code, or Cancelled when the prompt was refused
    static int RunElevatedSelf(IEnumerable<string> args, bool hidden) {
        using (Process p = StartElevatedSelf(args, hidden)) {
            if (p == null) return Cancelled;
            p.WaitForExit();
            return p.ExitCode;
        }
    }

    // install step 2's arguments: the version step 1 found in the payload (this file must still carry it),
    // the ticks naming its folder (step 1 reads the log from MachineDir(MachineRoot(), ticks))
    public static List<string> InstallMachineArgs(string version, long ticks, bool update, bool from2x) {
        var args = new List<string> { "--machine", "--expect-version", version, "--ticks", ticks.ToString() };
        if (update) args.Add("--update");
        if (from2x) args.Add("--from2x");
        return args;
    }

    // uninstall step 2's arguments: its folder's ticks, the go file uninstall.ps1 waits for (in step 1's folder:
    // only read), PIME too when asked
    public static List<string> UninstallMachineArgs(UninstallOptions o, string go, long ticks) {
        var args = new List<string> { "--uninstall-machine", "--ticks", ticks.ToString(), "--wait-for", go };
        if (o.RemovePime) args.Add("--remove-pime");
        return args;
    }

    // ---- step 2: the administrator part, run from where only administrators can write ----------------
    // %ProgramFiles%\DualityIME.setup-<ticks> or DualityIME.uninstall-<ticks> (no trace of an install: Traces
    // looks for DualityIME\DualityIMEEngine.exe)
    public static string MachineDir(string programFiles, long ticks, string kind = "setup") {
        return Path.Combine(programFiles, "DualityIME." + kind + "-" + ticks);
    }

    // where step 2's folder is made: Windows' own Program Files (not from a variable this user could set)
    static string MachineRoot() { return Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles); }

    static long Ticks(List<string> a) {
        long ticks;
        if (!long.TryParse(Arg(a, "--ticks") ?? "", out ticks) || ticks <= 0) throw new Exception("缺少 --ticks。");
        return ticks;
    }

    static readonly string[] AdminSids = {
        "S-1-5-18",       // SYSTEM
        "S-1-5-32-544",   // Administrators
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"   // TrustedInstaller
    };
    const string CreatorOwner = "S-1-3-0";   // stands for the owner of what is made inside (checked as the owner)

    const FileSystemRights WriteRights = FileSystemRights.WriteData | FileSystemRights.AppendData |
        FileSystemRights.WriteExtendedAttributes | FileSystemRights.WriteAttributes | FileSystemRights.Delete |
        FileSystemRights.DeleteSubdirectoriesAndFiles | FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership |
        (FileSystemRights)0x40000000 | (FileSystemRights)0x10000000;   // GENERIC_WRITE, GENERIC_ALL

    // Only SYSTEM, Administrators and TrustedInstaller (and `self`: the administrator account step 2 runs
    // as, the owner of what it makes when Windows is set to "object creator") own the folder or may write
    // to it or to what is made inside (rules for its files and subfolders count too); any other allowed
    // writer = not safe to run from
    public static bool OnlyAdminsCanWrite(DirectorySecurity security, string self = null) {
        Func<string, bool> trusted = sid => AdminSids.Contains(sid) || (self != null && sid == self);
        if (!trusted(security.GetOwner(typeof(SecurityIdentifier)).Value)) return false;
        foreach (FileSystemAccessRule r in security.GetAccessRules(true, true, typeof(SecurityIdentifier))) {
            if (r.AccessControlType != AccessControlType.Allow || (r.FileSystemRights & WriteRights) == 0) continue;
            string sid = r.IdentityReference.Value;
            if (!trusted(sid) && sid != CreatorOwner) return false;
        }
        return true;
    }

    static bool IsAdministrator() {
        using (WindowsIdentity id = WindowsIdentity.GetCurrent())
            return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
    }

    // step 2's folder, made new (not there before) and checked: only administrators can write to it
    static string NewAdminDir(string dir, string what) {
        if (!IsAdministrator()) throw new Exception("需要系統管理員權限（請執行 DualityIME-X.Y.Z-setup.exe 或 uninstall.exe）。");
        if (Directory.Exists(dir)) throw new Exception("暫存資料夾 " + dir + " 已經存在，請再執行一次。");
        Directory.CreateDirectory(dir);
        string self;
        using (WindowsIdentity id = WindowsIdentity.GetCurrent()) self = id.User.Value;
        if (!OnlyAdminsCanWrite(Directory.GetAccessControl(dir), self)) {
            try { Directory.Delete(dir, true); } catch { }
            throw new Exception("暫存資料夾 " + dir + " 不是只有系統管理員能寫入，為了安全不" + what + "。");
        }
        return dir;
    }

    // Everything in dir but the file `keep` deleted (step 2's folder once its work is done)
    public static void KeepOnly(string dir, string keep) {
        foreach (string f in Directory.GetFiles(dir))
            if (!string.Equals(Path.GetFileName(f), keep, StringComparison.OrdinalIgnoreCase)) try { File.Delete(f); } catch { }
        foreach (string d in Directory.GetDirectories(dir)) try { Directory.Delete(d, true); } catch { }
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
    static extern bool MoveFileEx(string existing, string newName, int flags);

    // Step 2 done: its folder keeps only the log, for step 1 to read after this process ends; the log and the
    // folder are deleted when Windows next starts (what cannot go now goes then too). No log: the folder goes now.
    static void LeaveLog(string dir, string log) {
        try {
            KeepOnly(dir, log);
            string file = Path.Combine(dir, log);
            if (!File.Exists(file)) { Directory.Delete(dir, true); return; }
            foreach (string f in Directory.GetFileSystemEntries(dir, "*", SearchOption.AllDirectories).OrderByDescending(x => x.Length))
                MoveFileEx(f, null, 4);   // MOVEFILE_DELAY_UNTIL_REBOOT (what KeepOnly could not delete, then the log)
            MoveFileEx(dir, null, 4);     // (after what is inside)
        } catch { }
    }

    static void AppendLog(string file, string text) {
        try { File.AppendAllText(file, text + Environment.NewLine, new UTF8Encoding(false)); } catch { }
    }

    // setup.exe --machine (step 2, with administrator rights; --expect-version was checked in Main): install.ps1
    // from a fresh copy of this file's own payload in an administrators-only folder; its log (install.log)
    // stays there for step 1.
    static int Machine(List<string> a) {
        try { Console.Title = "兩儀輸入法 安裝（系統管理員）"; } catch { }
        int code = Failed;
        string dir = null;
        try {
            dir = NewAdminDir(MachineDir(MachineRoot(), Ticks(a)), "安裝");
            using (Stream s = OpenPayload()) {
                if (s == null) throw new Exception("這個檔案不含安裝內容，請重新下載安裝檔。");
                using (var z = new ZipArchive(s, ZipArchiveMode.Read)) z.ExtractToDirectory(dir);
            }
            var args = new List<string> { "-Payload", dir };
            if (a.Contains("--update")) args.Add("-Update");
            if (a.Contains("--from2x")) args.Add("-From2x");
            code = RunHere(Path.Combine(dir, "install.ps1"), args);
        } catch (Exception e) {
            Console.WriteLine("安裝失敗：" + e.Message);
            if (dir != null) AppendLog(Path.Combine(dir, "install.log"), "      安裝失敗：" + e.Message);
            code = Failed;
        } finally {
            if (dir != null) LeaveLog(dir, "install.log");
        }
        return code;
    }

    // setup.exe / uninstall.exe --uninstall-machine (uninstall step 2, with administrator rights, hidden): the
    // scripts copied by this process into an administrators-only folder (from this file's payload, else from
    // next to it: the install folder), uninstall.ps1 run from there; its log (uninstall.log) stays there for step 1.
    static int UninstallMachine(List<string> a) {
        // (a process whose current folder is the install folder would keep it from being deleted)
        try { Environment.CurrentDirectory = Environment.SystemDirectory; } catch { }
        int code = Failed;
        string dir = null;
        try {
            string go = Arg(a, "--wait-for");
            if (string.IsNullOrEmpty(go)) throw new Exception("缺少 --wait-for。");
            dir = NewAdminDir(MachineDir(MachineRoot(), Ticks(a), "uninstall"), "移除");
            List<string> missing;
            using (Stream s = OpenPayload()) missing = CopyScripts(AppDomain.CurrentDomain.BaseDirectory, s, dir);
            if (missing.Count > 0) throw new Exception("找不到移除需要的檔案（" + string.Join("、", missing) + "）。");
            var o = new UninstallOptions { RemovePime = a.Contains("--remove-pime") };
            code = RunHere(Path.Combine(dir, "uninstall.ps1"), MachineArgs(o, go), true);
        } catch (Exception e) {
            Console.WriteLine("移除失敗：" + e.Message);
            if (dir != null) AppendLog(Path.Combine(dir, "uninstall.log"), "      移除失敗：" + e.Message);
            code = Failed;
        } finally {
            if (dir != null) LeaveLog(dir, "uninstall.log");
        }
        return code;
    }

    // true when there was a log to show
    static bool PrintLog(string log) {
        if (!File.Exists(log)) return false;
        foreach (string line in File.ReadAllLines(log, Encoding.UTF8)) Console.WriteLine(line);
        return true;
    }

    // Step 2 did not succeed and left no log (it stopped before making its folder: not an administrator,
    // no --ticks, the folder not safe...): the reason shown instead, null when there is nothing to add
    public static string MissingLogReason(bool uninstall, int code, bool logShown) {
        if (code == Ok || code == Cancelled || logShown) return null;
        return uninstall
            ? "移除程式的系統管理員步驟沒有完成，沒有留下記錄（結束代碼 " + code + "）；請再執行一次移除。"
            : "系統管理員步驟沒有完成，沒有留下記錄（結束代碼 " + code + "）；請再執行一次安裝檔。";
    }

    // as this process's user, in this window
    static int RunHere(string script, IEnumerable<string> args, bool nonInteractive = false) {
        var psi = new ProcessStartInfo("powershell.exe", PowerShellArgs(script, args, nonInteractive)) { UseShellExecute = false };
        using (Process p = Process.Start(psi)) {
            p.WaitForExit();
            return p.ExitCode;
        }
    }

    static void Step(int n, string text) {
        Console.WriteLine();
        Console.WriteLine("[" + n + "/3] " + text);
    }

    static void Say(string text) { Console.WriteLine("      " + text); }

    // ---- install ----------------------------------------------------------------------------------
    static int Install(bool update) {
        Console.Title = "兩儀輸入法 安裝";
        string problem = WindowsProblem();
        if (problem != null) { Console.WriteLine(problem); return Failed; }

        List<string> traces = TracesHere();
        bool first = traces.Count == 0;
        bool addToList = !ProgramInstalled(traces);   // a first install, or again after uninstalling
        bool from2x = UpgradedFrom2x(traces);
        List<string> tips;
        string defaultTip;
        NoteKeyboardList(out tips, out defaultTip);

        string dir = Path.Combine(Path.GetTempPath(), "DualityIME-setup-" + DateTime.Now.Ticks);
        try {
            Step(1, "解開安裝檔");
            using (Stream s = OpenPayload()) {
                if (s == null) { Say("這個檔案不含安裝內容，請重新下載安裝檔。"); return Failed; }
                using (var z = new ZipArchive(s, ZipArchiveMode.Read)) z.ExtractToDirectory(dir);
            }
            string version = File.ReadAllLines(Path.Combine(dir, "VERSION")).First().Trim();
            Say("兩儀輸入法 " + version + (first ? "（第一次安裝）" : ""));

            Step(2, "安裝程式（需要系統管理員權限）");
            // (not install.ps1 from this folder: anything running as this user could change it before it runs
            // elevated; and the log stays in step 2's own folder: the administrator writes nothing here)
            long ticks = DateTime.Now.Ticks;
            int code = RunElevatedSelf(InstallMachineArgs(version, ticks, update, from2x), update);
            bool logShown = PrintLog(Path.Combine(MachineDir(MachineRoot(), ticks), "install.log"));
            string noLog = MissingLogReason(false, code, logShown);
            if (noLog != null) Say(noLog);
            if (code == Cancelled) {
                Say("已取消：沒有取得系統管理員權限，電腦上什麼都沒有改變。");
                return Cancelled;
            }
            if (code != 0) {
                Say("安裝失敗（原因見上方）。");
                return Failed;
            }

            Step(3, "搬移舊版資料，設定這個使用者");
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            string oldDir = Path.Combine(appData, Migrate.OldDataFolder), newDir = Path.Combine(appData, Migrate.DataFolder);
            switch (Migrate.MoveUserData(oldDir, newDir)) {
                case Migrate.MoveResult.Moved:
                    Say("OK  舊版的打字記憶、個人詞庫與設定已搬到 " + newDir); break;
                case Migrate.MoveResult.KeptExisting:
                    Say("... " + newDir + " 已經有資料，沒有覆蓋；舊版的資料留在 " + oldDir); break;
                case Migrate.MoveResult.Failed:
                    Say("... 搬移舊版資料失敗，舊資料原封不動留在 " + oldDir + "；再執行一次安裝檔會再試"); break;
            }
            var userArgs = new List<string> { "-Payload", dir };
            if (addToList) userArgs.Add("-First");
            if (tips.Count > 0) { userArgs.Add("-HadTips"); userArgs.Add(string.Join(";", tips)); }
            if (defaultTip != "") { userArgs.Add("-HadDefault"); userArgs.Add(defaultTip); }
            if (RunHere(Path.Combine(dir, "user.ps1"), userArgs) != 0)
                Say("... 這個使用者的部分設定沒有完成（程式已安裝好）");

            Console.WriteLine();
            Console.WriteLine("完成！兩儀輸入法 " + version + " 已安裝。");
            if (from2x && !update) {
                ConsoleColor old = Console.ForegroundColor;
                Console.ForegroundColor = ConsoleColor.Yellow;
                Console.WriteLine("從舊版智慧輸入升級：請登出 Windows 再登入（或重新開機）來完成升級。在那之前，已經開著的程式裡打字可能會卡住或打不出中文。");
                Console.ForegroundColor = old;
            }
            if (addToList) {
                Console.WriteLine("已加進鍵盤清單：用 Win + 空白鍵 切換到兩儀輸入法。");
                Console.WriteLine("想讓它成為預設輸入法，可以在「兩儀輸入法設定」打開「設為預設輸入法」。");
            }
            return Ok;
        } finally {
            try { if (Directory.Exists(dir)) Directory.Delete(dir, true); } catch { }
        }
    }

    // ---- uninstall ----------------------------------------------------------------------------------
    public class UninstallOptions { public bool RemoveData, RemovePime; }

    public static UninstallOptions ParseUninstall(List<string> a) {
        bool all = a.Contains("--all");
        return new UninstallOptions { RemoveData = all || a.Contains("--remove-data"), RemovePime = all || a.Contains("--remove-pime") };
    }

    // what the uninstaller runs, copied to %TEMP% first (the install folder is deleted while they run; this
    // user runs user.ps1 from there) and again by step 2 into its administrators-only folder
    public static readonly string[] UninstallScripts = { "uninstall.ps1", "user.ps1", "language.ps1", "remove-pime.ps1" };

    // UninstallScripts into toDir: from the payload when this file carries one (setup.exe), else from
    // fromDir (next to uninstall.exe in the install folder). The names of those not found.
    public static List<string> CopyScripts(string fromDir, Stream payload, string toDir) {
        Directory.CreateDirectory(toDir);
        var missing = new List<string>();
        if (payload != null) {
            using (var z = new ZipArchive(payload, ZipArchiveMode.Read, true))
                foreach (string name in UninstallScripts) {
                    ZipArchiveEntry e = z.GetEntry(name);
                    if (e == null) missing.Add(name);
                    else e.ExtractToFile(Path.Combine(toDir, name), true);
                }
        } else {
            foreach (string name in UninstallScripts) {
                string from = Path.Combine(fromDir, name);
                if (File.Exists(from)) File.Copy(from, Path.Combine(toDir, name), true);
                else missing.Add(name);
            }
        }
        return missing;
    }

    // uninstall.ps1 (administrator): it waits for "go" in the file `go`
    public static List<string> MachineArgs(UninstallOptions o, string go) {
        var args = new List<string> { "-WaitFor", go };
        if (o.RemovePime) args.Add("-RemovePime");
        return args;
    }

    // user.ps1 after the machine part
    public static List<string> CleanupArgs(UninstallOptions o) {
        var args = new List<string> { "-Cleanup" };
        if (o.RemoveData) args.Add("-RemoveUserData");
        return args;
    }

    static void Signal(string go, string what) {
        // written whole, then renamed: uninstall.ps1 never reads half of it
        File.WriteAllText(go + ".tmp", what, Encoding.ASCII);
        File.Move(go + ".tmp", go);
    }

    static int Uninstall(UninstallOptions o) {
        Console.Title = "兩儀輸入法 移除";
        Console.WriteLine("移除兩儀輸入法");
        string dir = Path.Combine(Path.GetTempPath(), "DualityIME-uninstall-" + DateTime.Now.Ticks);
        // (a process whose current folder is the install folder would keep it from being deleted)
        Environment.CurrentDirectory = Path.GetTempPath();
        string go = Path.Combine(dir, "go");
        long ticks = DateTime.Now.Ticks;
        string machineLog = Path.Combine(MachineDir(MachineRoot(), ticks, "uninstall"), "uninstall.log");
        Process machine = null;
        bool signalled = false, listChanged = false;
        try {
            List<string> missing;
            using (Stream s = OpenPayload()) missing = CopyScripts(AppDomain.CurrentDomain.BaseDirectory, s, dir);
            if (missing.Count > 0) {
                Console.WriteLine("找不到移除需要的檔案（" + string.Join("、", missing) + "），電腦上什麼都沒有改變。" +
                                  "請用兩儀輸入法的安裝檔移除：DualityIME-X.Y.Z-setup.exe --uninstall");
                return Failed;
            }

            // administrator rights first: refused = nothing has changed (step 2 copies the scripts again, to a
            // folder only administrators can write: nothing running as this user can change what it runs)
            machine = StartElevatedSelf(UninstallMachineArgs(o, go, ticks), true);
            if (machine == null) {
                Console.WriteLine("已取消：沒有取得系統管理員權限，電腦上什麼都沒有改變。");
                return Cancelled;
            }

            // the administrator part waits for "go": if it has ended already, it can't remove the program,
            // and the keyboard list stays as it is
            Func<bool> machineGone = () => {
                if (!machine.HasExited) return false;
                string noLog = MissingLogReason(true, machine.ExitCode, PrintLog(machineLog));
                if (noLog != null) Say(noLog);
                Say("移除程式的部分意外結束了（結束代碼 " + machine.ExitCode + "），沒有移除程式" +
                    "；鍵盤清單" + (listChanged ? "已經整理過" : "沒有改變") + "。請再執行一次移除。");
                return true;
            };
            if (machineGone()) return Failed;
            Step(1, "從這個使用者的鍵盤清單移除");
            listChanged = true;
            if (RunHere(Path.Combine(dir, "user.ps1"), new[] { "-Uninstall" }) != 0) {
                Signal(go, "stop"); signalled = true;
                machine.WaitForExit();
                Say("沒有移除程式（鍵盤清單的整理沒有完成，原因見上方）。");
                return Failed;
            }
            if (machineGone()) return Failed;
            Signal(go, "go"); signalled = true;

            Step(2, "移除程式（系統管理員權限）");
            machine.WaitForExit();
            string missing2 = MissingLogReason(true, machine.ExitCode, PrintLog(machineLog));
            if (missing2 != null) Say(missing2);
            if (machine.ExitCode != 0) {
                Say("移除程式沒有完成（原因見上方）；再執行一次移除可以繼續。");
                return Failed;
            }

            Step(3, "清除這個使用者的設定");
            bool cleaned = RunHere(Path.Combine(dir, "user.ps1"), CleanupArgs(o)) == 0;

            Console.WriteLine();
            Console.WriteLine("已移除兩儀輸入法。");
            if (!o.RemoveData)
                Console.WriteLine("學習紀錄與設定保留在 " + Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), Migrate.DataFolder) +
                                  "，重新安裝後會接著使用。");
            if (!cleaned) {
                Console.WriteLine("這個使用者的部分設定沒有清除（原因見上方）。");
                return Failed;
            }
            return Ok;
        } finally {
            if (machine != null) {
                if (!signalled) { try { Signal(go, "stop"); } catch { } }
                try { machine.WaitForExit(30000); } catch { }
                machine.Dispose();
            }
            try { if (Directory.Exists(dir)) Directory.Delete(dir, true); } catch { }
        }
    }
}
