// Tests the installer's own logic (installer\src\Setup.cs) without installing anything: the first-install
// detection with stand-ins for the folders and the registry, the keyboard-list entries it notes, reading
// the payload, and (a copy of setup.exe carrying a fake payload, args[0]) --list-payload and
// --expect-version. Built and run by tools\test_setup.ps1.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;

static class SetupTest {
    static int failed = 0;
    static void Check(string name, bool ok, string detail) {
        Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok ? "" : "   (" + detail + ")"));
        if (!ok) failed++;
    }

    static int Main(string[] args) {
        Console.OutputEncoding = Encoding.UTF8;
        string dir = Path.Combine(Path.GetTempPath(), "dualityime-setup-test", "traces");
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
        string appData = Path.Combine(dir, "AppData"), programFiles = Path.Combine(dir, "Program Files");
        Directory.CreateDirectory(appData);
        Directory.CreateDirectory(programFiles);

        // ---- first install: no trace of 2.x or 3.0 ----
        var hkcu = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var hklm = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var values = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        Func<List<string>> traces = () => Setup.Traces(appData, programFiles, k => hkcu.Contains(k), k => hklm.Contains(k),
                                                       (k, n) => values.Contains(k + "|" + n));
        var t = traces();
        Check("nothing installed = first install", t.Count == 0, string.Join(", ", t));

        Directory.CreateDirectory(Path.Combine(appData, "Other"));
        Directory.CreateDirectory(Path.Combine(programFiles, "Other"));
        hkcu.Add(@"Software\Other");
        t = traces();
        Check("other programs are no trace", t.Count == 0, string.Join(", ", t));

        // a program folder holding the engine (what counts as installed there)
        Action<string> Engine = name => {
            Directory.CreateDirectory(Path.Combine(programFiles, name));
            File.WriteAllText(Path.Combine(programFiles, name, name + "Engine.exe"), "x");
        };
        Action clear = () => {
            foreach (string name in new[] { "SmartZhuyin", "DualityIME" }) {
                if (Directory.Exists(Path.Combine(appData, name))) Directory.Delete(Path.Combine(appData, name), true);
                if (Directory.Exists(Path.Combine(programFiles, name))) Directory.Delete(Path.Combine(programFiles, name), true);
            }
            hkcu.Clear(); hklm.Clear(); values.Clear();
        };
        var cases = new List<KeyValuePair<string, Action>> {
            new KeyValuePair<string, Action>(@"%APPDATA%\SmartZhuyin", () => Directory.CreateDirectory(Path.Combine(appData, "SmartZhuyin"))),
            new KeyValuePair<string, Action>(@"%APPDATA%\DualityIME", () => Directory.CreateDirectory(Path.Combine(appData, "DualityIME"))),
            new KeyValuePair<string, Action>(@"%ProgramFiles%\SmartZhuyin", () => Engine("SmartZhuyin")),
            new KeyValuePair<string, Action>(@"%ProgramFiles%\DualityIME", () => Engine("DualityIME")),
            new KeyValuePair<string, Action>(@"HKCU\Software\SmartZhuyin", () => hkcu.Add(@"Software\SmartZhuyin")),
            new KeyValuePair<string, Action>(@"HKCU\Software\DualityIME", () => hkcu.Add(@"Software\DualityIME")),
            new KeyValuePair<string, Action>(@"Uninstall\SmartZhuyin", () => hklm.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\SmartZhuyin")),
            new KeyValuePair<string, Action>(@"Uninstall\DualityIME", () => hklm.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DualityIME")),
            new KeyValuePair<string, Action>(@"Run SmartZhuyinEngine", () => values.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Run|SmartZhuyinEngine")),
            new KeyValuePair<string, Action>(@"Run DualityIMEEngine", () => values.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Run|DualityIMEEngine")),
        };
        foreach (var c in cases) {
            clear();
            c.Value();
            t = traces();
            Check("a trace: " + c.Key, t.Count == 1 && t[0].EndsWith(c.Key), string.Join(", ", t));
        }

        // what an upgrade or an uninstall leaves in Program Files until the next restart: no install
        clear();
        foreach (string name in new[] { "SmartZhuyin", "DualityIME" }) {
            Directory.CreateDirectory(Path.Combine(programFiles, name));
            File.WriteAllText(Path.Combine(programFiles, name, name + "64.dll"), "x");
            File.WriteAllText(Path.Combine(programFiles, name, name + "32.dll.old-123"), "x");
        }
        Directory.CreateDirectory(Path.Combine(programFiles, "DualityIME.removed-123"));
        File.WriteAllText(Path.Combine(programFiles, "DualityIME.removed-123", "DualityIMEEngine.exe"), "x");
        t = traces();
        Check("program folders with only leftovers (no engine, no keys): no trace", t.Count == 0, string.Join(", ", t));
        Check("leftovers + data kept: not installed (goes back into the list)",
              !Setup.ProgramInstalled(t.Concat(new[] { @"%APPDATA%\DualityIME" })), string.Join(", ", t));
        Engine("SmartZhuyin");
        t = traces();
        Check("the folder with the engine: installed", Setup.ProgramInstalled(t), string.Join(", ", t));
        clear();
        hklm.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DualityIME");
        t = traces();
        Check("the Uninstall key only: installed", Setup.ProgramInstalled(t), string.Join(", ", t));
        clear();
        values.Add(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Run|SmartZhuyinEngine");
        t = traces();
        Check("the Run value only: installed", Setup.ProgramInstalled(t), string.Join(", ", t));
        clear();

        // ---- the keyboard list: added when no program is installed (also again after uninstalling) ----
        Check("no traces: added to the list", !Setup.ProgramInstalled(new string[0]), "");
        Check("only data an uninstall left: added to the list",
              !Setup.ProgramInstalled(new[] { @"%APPDATA%\DualityIME", @"%APPDATA%\SmartZhuyin", @"HKCU\Software\SmartZhuyin" }), "");
        foreach (string p in new[] { @"%ProgramFiles%\DualityIME", @"%ProgramFiles%\SmartZhuyin",
                                     @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DualityIME",
                                     @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run DualityIMEEngine" })
            Check("a program installed: the list stays (" + p + ")", Setup.ProgramInstalled(new[] { @"%APPDATA%\DualityIME", p }), "");

        // ---- upgraded from 2.x: a 2.x program trace, not data and not 3.0 ----
        Check("no traces: not from 2.x", !Setup.UpgradedFrom2x(new string[0]), "");
        Check("2.x data only: not from 2.x", !Setup.UpgradedFrom2x(new[] { @"%APPDATA%\SmartZhuyin", @"HKCU\Software\SmartZhuyin" }), "");
        Check("3.0 program only: not from 2.x", !Setup.UpgradedFrom2x(new[] { @"%ProgramFiles%\DualityIME",
              @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DualityIME",
              @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run DualityIMEEngine" }), "");
        foreach (string p in new[] { @"%ProgramFiles%\SmartZhuyin",
                                     @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\SmartZhuyin",
                                     @"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run SmartZhuyinEngine" })
            Check("2.x program trace: from 2.x (" + p + ")", Setup.UpgradedFrom2x(new[] { @"%ProgramFiles%\DualityIME", p }), "");

        // ---- the keyboard-list entries of ours it notes ----
        var tips = Setup.OurTips(new[] {
            "CachedLanguageName", "0409:00000409",
            "0404:{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}{BFDAC825-E67C-4E87-B644-E6B5F7B3D08B}",
            "0C04:{7f9854c9-f3d8-42db-bb12-9f4dd03a6090}{BFDAC825-E67C-4E87-B644-E6B5F7B3D08B}",
            "0404:{7F9854C9-F3D8-42DB-BB12-9F4DD03A6090}{BFDAC825-E67C-4E87-B644-E6B5F7B3D08B}",
            "0411:{D5A86FD5-5308-47EA-AD16-9C4EB160EC3C}{773EB24E-CA1D-4B1B-B420-FA985BB0B80D}" });
        Check("our keyboard-list entries, each once", tips.Count == 2 && tips[0].StartsWith("0404:") && tips[1].StartsWith("0C04:"),
              string.Join(" ", tips));

        // ---- the payload ----
        var ms = new MemoryStream();
        using (var z = new ZipArchive(ms, ZipArchiveMode.Create, true)) {
            using (var w = new StreamWriter(z.CreateEntry("VERSION").Open())) w.Write(" 3.1.2\r\n");
            using (var w = new StreamWriter(z.CreateEntry("ui/index.html").Open())) w.Write("x");
        }
        ms.Position = 0;
        Check("payload version", Setup.PayloadVersion(ms) == "3.1.2", "");
        ms.Position = 0;
        var files = Setup.PayloadFiles(ms);
        Check("payload files", files.Count == 2 && files.Contains(@"ui\index.html") && files.Contains("VERSION"), string.Join(", ", files));

        // ---- step 2 (administrator): unpacked again where only administrators can write ----
        Check("machine folder", Setup.MachineDir(@"C:\Program Files", 123) == @"C:\Program Files\DualityIME.setup-123",
              Setup.MachineDir(@"C:\Program Files", 123));
        Check("machine folder is no install trace", !Setup.MachineDir(@"C:\Program Files", 1).EndsWith(@"\DualityIME"), "");
        Check("uninstall machine folder", Setup.MachineDir(@"C:\Program Files", 7, "uninstall") == @"C:\Program Files\DualityIME.uninstall-7",
              Setup.MachineDir(@"C:\Program Files", 7, "uninstall"));
        // (no log path among them: step 2 writes its log only in its own folder, step 1 reads it from there)
        Check("step 2 arguments", string.Join(" ", Setup.InstallMachineArgs("3.0.1", 123, false, false)) ==
              "--machine --expect-version 3.0.1 --ticks 123" &&
              string.Join(" ", Setup.InstallMachineArgs("3.0.1", 5, true, true)) == "--machine --expect-version 3.0.1 --ticks 5 --update --from2x",
              string.Join(" ", Setup.InstallMachineArgs("3.0.1", 5, true, true)));
        // step 2 failed before it had a folder (no log): step 1 says so instead of pointing at nothing
        Check("no log after a failed step 2: a reason",
              Setup.MissingLogReason(false, 1, false).Contains("沒有留下記錄") && Setup.MissingLogReason(false, 1, false).Contains("安裝檔") &&
              Setup.MissingLogReason(true, 1, false).Contains("沒有留下記錄") && Setup.MissingLogReason(true, 1, false).Contains("移除"), "");
        Check("a log shown, success or refused: no extra reason",
              Setup.MissingLogReason(false, 1, true) == null && Setup.MissingLogReason(false, 0, false) == null &&
              Setup.MissingLogReason(false, Setup.Cancelled, false) == null && Setup.MissingLogReason(true, 1, true) == null, "");
        // step 2's folder once it is done: only the log stays (for step 1 to read)
        string done = Path.Combine(dir, "machine-done");
        Directory.CreateDirectory(Path.Combine(done, "x64"));
        foreach (string f in new[] { "install.ps1", "install.log", @"x64\DualityIME64.dll" }) File.WriteAllText(Path.Combine(done, f), f);
        Setup.KeepOnly(done, "install.log");
        Check("step 2's folder keeps only its log", string.Join(" ", Directory.GetFileSystemEntries(done).Select(Path.GetFileName)) == "install.log",
              string.Join(" ", Directory.GetFileSystemEntries(done).Select(Path.GetFileName)));
        string pf = Environment.GetEnvironmentVariable("ProgramW6432");
        if (string.IsNullOrEmpty(pf)) pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        Check("Program Files: only administrators can write (where step 2 unpacks)",
              Setup.OnlyAdminsCanWrite(System.IO.Directory.GetAccessControl(pf)), pf);
        Check("this user's temp folder: not only administrators (never run elevated from there)",
              !Setup.OnlyAdminsCanWrite(System.IO.Directory.GetAccessControl(dir)), dir);
        var admins = new System.Security.Principal.SecurityIdentifier("S-1-5-32-544");
        var users = new System.Security.Principal.SecurityIdentifier("S-1-5-32-545");
        var me = System.Security.Principal.WindowsIdentity.GetCurrent().User;
        Func<System.Security.Principal.SecurityIdentifier, System.Security.AccessControl.DirectorySecurity> acl = owner => {
            var ds = new System.Security.AccessControl.DirectorySecurity();
            ds.SetAccessRuleProtection(true, false);
            ds.SetOwner(owner);
            ds.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(admins, System.Security.AccessControl.FileSystemRights.FullControl,
                System.Security.AccessControl.InheritanceFlags.ContainerInherit | System.Security.AccessControl.InheritanceFlags.ObjectInherit,
                System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
            ds.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(users, System.Security.AccessControl.FileSystemRights.ReadAndExecute,
                System.Security.AccessControl.AccessControlType.Allow));
            return ds;
        };
        Check("administrators own and write, users read: safe", Setup.OnlyAdminsCanWrite(acl(admins)), "");
        Check("owned by this user: not safe", !Setup.OnlyAdminsCanWrite(acl(me)), "");
        Check("owned by the administrator account step 2 runs as: safe", Setup.OnlyAdminsCanWrite(acl(me), me.Value), "");
        Check("owned by another account than step 2's: not safe",
              !Setup.OnlyAdminsCanWrite(acl(me), "S-1-5-21-1-2-3-1001"), "");
        var usersWrite = acl(admins);
        usersWrite.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(users, System.Security.AccessControl.FileSystemRights.CreateFiles,
            System.Security.AccessControl.AccessControlType.Allow));
        Check("users may create files: not safe", !Setup.OnlyAdminsCanWrite(usersWrite), "");
        var inside = acl(admins);
        inside.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(me, System.Security.AccessControl.FileSystemRights.Modify,
            System.Security.AccessControl.InheritanceFlags.ObjectInherit, System.Security.AccessControl.PropagationFlags.InheritOnly,
            System.Security.AccessControl.AccessControlType.Allow));
        Check("this user may change the files made inside: not safe", !Setup.OnlyAdminsCanWrite(inside), "");

        // ---- uninstall: the options, the scripts it copies to %TEMP%, the arguments ----
        Func<string, Setup.UninstallOptions> parse = s => Setup.ParseUninstall(s.Split(' ').ToList());
        var o = parse("--uninstall");
        Check("--uninstall keeps data and PIME", !o.RemoveData && !o.RemovePime, "");
        o = parse("--uninstall --all");
        Check("--all = --remove-data --remove-pime", o.RemoveData && o.RemovePime, "");
        o = parse("--uninstall --remove-data --no-pause");
        Check("--remove-data only", o.RemoveData && !o.RemovePime, "");
        o = parse("--uninstall --remove-pime");
        Check("--remove-pime only", !o.RemoveData && o.RemovePime, "");
        Check("machine args", string.Join(" ", Setup.MachineArgs(parse("--uninstall"), "g")) == "-WaitFor g" &&
              string.Join(" ", Setup.MachineArgs(parse("--uninstall --all"), "g")) == "-WaitFor g -RemovePime",
              string.Join(" ", Setup.MachineArgs(parse("--uninstall --all"), "g")));
        // uninstall step 2: this file again (it copies the scripts to its own administrators-only folder), never
        // a script from the user's %TEMP%; the go file there is only read
        Check("uninstall step 2 arguments",
              string.Join(" ", Setup.UninstallMachineArgs(parse("--uninstall"), @"C:\t\go", 9)) == @"--uninstall-machine --ticks 9 --wait-for C:\t\go" &&
              string.Join(" ", Setup.UninstallMachineArgs(parse("--uninstall --all"), "g", 9)) == "--uninstall-machine --ticks 9 --wait-for g --remove-pime",
              string.Join(" ", Setup.UninstallMachineArgs(parse("--uninstall --all"), "g", 9)));
        Check("cleanup args", string.Join(" ", Setup.CleanupArgs(parse("--uninstall --remove-pime"))) == "-Cleanup" &&
              string.Join(" ", Setup.CleanupArgs(parse("--uninstall --all"))) == "-Cleanup -RemoveUserData",
              string.Join(" ", Setup.CleanupArgs(parse("--uninstall --all"))));
        Check("the scripts it copies", string.Join(" ", Setup.UninstallScripts) == "uninstall.ps1 user.ps1 language.ps1 remove-pime.ps1",
              string.Join(" ", Setup.UninstallScripts));

        string installed = Path.Combine(dir, "installed"), temp = Path.Combine(dir, "temp");
        Directory.CreateDirectory(installed);
        foreach (string f in Setup.UninstallScripts.Concat(new[] { "install.ps1", "uninstall.exe" }))
            File.WriteAllText(Path.Combine(installed, f), "from folder " + f);
        var miss = Setup.CopyScripts(installed, null, temp);
        var copied = Directory.GetFiles(temp).Select(Path.GetFileName).OrderBy(x => x).ToList();
        Check("scripts copied from the install folder, nothing else",
              miss.Count == 0 && string.Join(" ", copied) == "language.ps1 remove-pime.ps1 uninstall.ps1 user.ps1" &&
              File.ReadAllText(Path.Combine(temp, "user.ps1")) == "from folder user.ps1",
              string.Join(" ", copied) + " missing " + string.Join(" ", miss));
        File.Delete(Path.Combine(installed, "user.ps1"));
        Directory.Delete(temp, true);
        miss = Setup.CopyScripts(installed, null, temp);
        Check("a script missing from the install folder", miss.Count == 1 && miss[0] == "user.ps1", string.Join(" ", miss));

        var zipped = new MemoryStream();
        using (var z = new ZipArchive(zipped, ZipArchiveMode.Create, true))
            foreach (string f in Setup.UninstallScripts.Concat(new[] { "VERSION" }))
                using (var w = new StreamWriter(z.CreateEntry(f).Open())) w.Write("from payload " + f);
        zipped.Position = 0;
        Directory.Delete(temp, true);
        miss = Setup.CopyScripts(installed, zipped, temp);   // (the payload wins over the folder)
        copied = Directory.GetFiles(temp).Select(Path.GetFileName).OrderBy(x => x).ToList();
        Check("scripts copied from the payload",
              miss.Count == 0 && string.Join(" ", copied) == "language.ps1 remove-pime.ps1 uninstall.ps1 user.ps1" &&
              File.ReadAllText(Path.Combine(temp, "uninstall.ps1")) == "from payload uninstall.ps1",
              string.Join(" ", copied) + " missing " + string.Join(" ", miss));

        // ---- setup.exe with a fake payload (VERSION 1.2.3): never past --expect-version ----
        if (args.Length > 0) {
            string output;
            int code = Run(args[0], "--list-payload", out output);
            Check("--list-payload", code == 0 && output.Contains("VERSION") && output.Contains(@"data\a.txt"), code + ": " + output);
            code = Run(args[0], "--expect-version 9.9.9 --update", out output);
            Check("--expect-version other than the payload: exit 40", code == Setup.WrongVersion && output.Contains("1.2.3"), code + ": " + output);
            code = Run(args[0], "--update --expect-version 1.2.30", out output);
            Check("--expect-version is the whole version", code == Setup.WrongVersion, code + ": " + output);
            // the fake payload has no scripts: it stops before asking for administrator rights
            code = Run(args[0], "--uninstall --all --no-pause", out output);
            Check("--uninstall, scripts not in the payload: exit 1, nothing done",
                  code == Setup.Failed && output.Contains("uninstall.ps1") && output.Contains("什麼都沒有改變"), code + ": " + output);
            // step 2 without the ticks naming its folder: stops before making anything (safe to run elevated)
            code = Run(args[0], "--machine --expect-version 1.2.3", out output);
            Check("--machine without --ticks: exit 1, nothing done", code == Setup.Failed && output.Contains("--ticks"), code + ": " + output);
            code = Run(args[0], "--uninstall-machine --wait-for nowhere", out output);
            Check("--uninstall-machine without --ticks: exit 1, nothing done", code == Setup.Failed && output.Contains("--ticks"), code + ": " + output);
        }
        if (args.Length > 1) {
            string output;
            int code = Run(args[1], "--expect-version 1.2.3 --update", out output);
            Check("--expect-version without a payload: exit 40", code == Setup.WrongVersion, code + ": " + output);
            // uninstall.exe in a folder without the scripts: the same
            code = Run(args[1], "--uninstall --no-pause", out output);
            Check("uninstall.exe without the scripts next to it: exit 1, nothing done",
                  code == Setup.Failed && output.Contains("user.ps1") && output.Contains("什麼都沒有改變"), code + ": " + output);
        }

        Directory.Delete(dir, true);
        Console.WriteLine(failed == 0 ? "all passed" : failed + " failed");
        return failed == 0 ? 0 : 1;
    }

    static int Run(string exe, string args, out string output) {
        var psi = new ProcessStartInfo(exe, args) {
            UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true, StandardOutputEncoding = Encoding.UTF8
        };
        using (Process p = Process.Start(psi)) {
            output = p.StandardOutput.ReadToEnd();
            if (!p.WaitForExit(20000)) { try { p.Kill(); } catch { } output += " (timed out)"; return -1; }
            return p.ExitCode;
        }
    }
}
