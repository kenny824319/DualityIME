// 兩儀輸入法: 2.x data (智慧輸入, %APPDATA%\SmartZhuyin) to 3.0 (%APPDATA%\DualityIME), the one
// converter: setup.exe moves the data folder, the settings app converts a 2.x export it imports.
//   config.json: fix_doubles on -> autocorrect on; layout and fix_doubles removed; when the data folder
//                moves, the 全部清除 counters (forget_words, forget_emails) are set to what the 2.x
//                user.json noted as handled (no clear pending)
//   keys.json: only the standard keyboard ("eten" dropped)
//   a backup's memory: 校正記憶 under its old name "autocorrect" becomes "keys"
// Mostly written with Claude (claude-opus-5-5).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

public static class Migrate {
    public const string OldAppName = "智慧輸入";
    public const string AppName = "兩儀輸入法";
    public const string OldDataFolder = "SmartZhuyin";
    public const string DataFolder = "DualityIME";

    public enum MoveResult { Nothing, Moved, KeptExisting, Failed }

    // Copied as they are / converted. Afterwards only these, their *.tmp and languages-checked are deleted from
    // the old folder; anything else (an export the user saved there) stays, and so does the folder then
    static readonly string[] AsIs = { "dictionary.json", "keyboard.json" };
    static readonly string[] Converted = { "user.json", "config.json", "keys.json", "import-memory.json" };

    static JavaScriptSerializer NewJson() {
        return new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    }

    public static Dictionary<string, object> Config(Dictionary<string, object> old) {
        var c = new Dictionary<string, object>(old);
        object fix;
        if (c.TryGetValue("fix_doubles", out fix) && fix is bool && (bool)fix) c["autocorrect"] = true;
        c.Remove("fix_doubles");
        c.Remove("layout");
        return c;
    }

    public static Dictionary<string, object> Keys(Dictionary<string, object> old) {
        object standard;
        if (!old.TryGetValue("standard", out standard) || !(standard is Dictionary<string, object>))
            standard = new Dictionary<string, object> { { "picks", new Dictionary<string, object>() }, { "used", new Dictionary<string, object>() } };
        return new Dictionary<string, object> { { "format", 1 }, { "standard", standard } };
    }

    static bool IsUrl(string w) {
        return w.IndexOf("://", StringComparison.Ordinal) >= 0 || w.StartsWith("www.", StringComparison.OrdinalIgnoreCase);
    }

    static Dictionary<string, object> Sub(Dictionary<string, object> d, string key) {
        object v;
        var s = d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null;
        return s == null ? new Dictionary<string, object>() : new Dictionary<string, object>(s);
    }

    // One letter or digit key, with or without a space (t, a, 5): the engines never keep it as English
    // (engine/lexicon.py is_one_key, the same rule)
    public static bool IsOneKey(string raw) {
        string r = raw.TrimEnd(' ');
        if (r.Length != 1) return false;
        char ch = r[0];
        return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9');
    }

    static int Count(object v) {
        try { return Convert.ToInt32(v); } catch { return 1; }
    }

    // user.json (or the user part of a backup) 2.x -> 3.0: e-mail addresses leave "words" for "emails" (with their
    // day), URLs are dropped, one-key "english" entries (IsOneKey) go. Anything else is copied; a 3.0 file comes out as it is.
    public static Dictionary<string, object> User(Dictionary<string, object> old) {
        var u = new Dictionary<string, object>(old);
        var words = Sub(old, "words");
        var emails = Sub(old, "emails");
        var used = Sub(old, "used");
        var wordUsed = Sub(used, "words");
        var emailUsed = Sub(used, "emails");
        foreach (string w in emails.Keys.ToList())
            if (IsUrl(w)) { emails.Remove(w); emailUsed.Remove(w); }
        foreach (string w in words.Keys.ToList()) {
            if (IsUrl(w)) { words.Remove(w); wordUsed.Remove(w); continue; }
            if (w.IndexOf('@') < 0) continue;
            emails[w] = emails.ContainsKey(w) ? Count(emails[w]) + Count(words[w]) : words[w];
            if (wordUsed.ContainsKey(w) && !emailUsed.ContainsKey(w)) emailUsed[w] = wordUsed[w];
            words.Remove(w);
            wordUsed.Remove(w);
        }
        used["words"] = wordUsed;
        used["emails"] = emailUsed;
        u["words"] = words;
        u["emails"] = emails;
        u["used"] = used;
        object english;
        if (old.TryGetValue("english", out english) && english is System.Collections.IEnumerable && !(english is string))
            u["english"] = ((System.Collections.IEnumerable)english).Cast<object>()
                .Where(e => !(e is string) || !IsOneKey((string)e)).ToList();
        return u;
    }

    public static Dictionary<string, object> Memory(Dictionary<string, object> memory) {
        var m = User(memory);
        if (m.ContainsKey("autocorrect")) {
            if (!m.ContainsKey("keys")) m["keys"] = m["autocorrect"];
            m.Remove("autocorrect");
        }
        var keys = m.ContainsKey("keys") ? m["keys"] as Dictionary<string, object> : null;
        if (keys != null) m["keys"] = Keys(keys);
        return m;
    }

    public static Dictionary<string, object> Backup(Dictionary<string, object> data) {
        var b = new Dictionary<string, object>(data);
        b["app"] = AppName;
        var config = b.ContainsKey("config") ? b["config"] as Dictionary<string, object> : null;
        if (config != null) b["config"] = Config(config);
        var memory = b.ContainsKey("memory") ? b["memory"] as Dictionary<string, object> : null;
        if (memory != null) b["memory"] = Memory(memory);
        return b;
    }

    // A moved config.json's 全部清除 counters = the ones the 2.x user.json noted as handled (0 without
    // it, or when it cannot be read): the engine clears all typing memory when forget_words is above
    // user.json's forgot, and a 2.x config whose counter ran ahead would clear everything at the first
    // settings change.
    public static Dictionary<string, object> ConfigCounters(Dictionary<string, object> config, Dictionary<string, object> oldUser) {
        var c = new Dictionary<string, object>(config);
        foreach (var pair in new[] { new[] { "forget_words", "forgot" }, new[] { "forget_emails", "forgot_emails" } }) {
            object v;
            long n = 0;
            if (oldUser != null && oldUser.TryGetValue(pair[1], out v) && v != null && !(v is bool) && !(v is string)) {
                try { n = Math.Max(0, Convert.ToInt64(v)); } catch { n = 0; }
            }
            c[pair[0]] = n;
        }
        return c;
    }

    static Dictionary<string, object> ReadJson(byte[] bytes) {
        try { return NewJson().DeserializeObject(new UTF8Encoding(false).GetString(bytes).TrimStart('﻿')) as Dictionary<string, object>; }
        catch { return null; }
    }

    // The bytes 3.0 gets for one 2.x file: converted when it is JSON this knows, else as it is
    static byte[] ConvertFile(string name, byte[] old, string oldDir) {
        if (Array.IndexOf(Converted, name) < 0) return old;
        var json = NewJson();
        Dictionary<string, object> data = ReadJson(old);
        if (data == null) return old;
        if (name == "config.json") {
            Dictionary<string, object> oldUser = null;
            try {
                string user = Path.Combine(oldDir, "user.json");
                if (File.Exists(user)) oldUser = ReadJson(File.ReadAllBytes(user));
            } catch { }
            data = ConfigCounters(Config(data), oldUser);
        }
        else if (name == "keys.json") data = Keys(data);
        else if (name == "user.json") data = User(data);
        else data = Memory(data);
        return new UTF8Encoding(false).GetBytes(json.Serialize(data));
    }

    // The one "already migrated" rule (the installer, settings.exe --migrate-user, the settings window and
    // the engine's migrate_user_data in cpp/server/engine_main.cpp check the same names): the 3.0 folder
    // holds at least one of these and no InProgress file. Its mere existence is not enough: the symbol
    // keyboard writes keyboard.json there, and a failed move may have created it.
    public static readonly string[] MigratedMarkers = { "user.json", "config.json", "keys.json", "dictionary.json" };

    // Written in the 3.0 folder before the first file is copied, deleted after the old files are: a move
    // killed half way (its rollback never ran) is not taken for done; the next run copies what is missing.
    public const string InProgress = "migrating";

    public static bool Migrated(string newDir) {
        return !File.Exists(Path.Combine(newDir, InProgress)) && MigratedMarkers.Any(n => File.Exists(Path.Combine(newDir, n)));
    }

    // 2.x data waiting to be moved (what the settings window checks before it reads config.json)
    public static bool Pending(string oldDir, string newDir) {
        if (File.Exists(Path.Combine(newDir, InProgress))) return true;
        return Directory.Exists(oldDir) && !Migrated(newDir) && AsIs.Concat(Converted).Any(n => File.Exists(Path.Combine(oldDir, n)));
    }

    // %APPDATA%\SmartZhuyin -> %APPDATA%\DualityIME: written (and read back) first, then the moved files
    // are deleted from the old folder (the folder too when nothing else is left in it). A 3.0 folder that
    // already holds data (Migrated) is left alone, and so is the old one; a file 3.0 has already written
    // (keyboard.json, import-memory.json) is kept and its 2.x one not copied. One move at a time per
    // folder (the installer, the settings window and the engine's settings.exe --migrate-user may run
    // together): the second waits, then finds the data moved (KeptExisting). A failure leaves no half move
    // behind: what this run wrote goes, and so does the 3.0 folder when it made it and it is empty again
    // (the next run tries again). A run killed half way leaves InProgress behind: the next run keeps what
    // it finds in the 3.0 folder, copies the rest and finishes the move.
    public static MoveResult MoveUserData(string oldDir, string newDir) {
        if (!Directory.Exists(oldDir) && !File.Exists(Path.Combine(newDir, InProgress))) return MoveResult.Nothing;
        System.Threading.Mutex lockOne;
        // (refused: another run at another integrity level holds it right now; this one tries again next time)
        try { lockOne = new System.Threading.Mutex(false, MutexName(newDir)); } catch { return MoveResult.Failed; }
        using (var one = lockOne) {
            bool mine = false;
            try {
                try { mine = one.WaitOne(30000); }
                catch (System.Threading.AbandonedMutexException) { mine = true; }   // (the other run died: its own rollback did not run)
                if (!mine) return MoveResult.Failed;
                return MoveLocked(oldDir, newDir);
            } finally {
                if (mine) one.ReleaseMutex();
            }
        }
    }

    // roaming\SmartZhuyin -> roaming\DualityIME (settings.exe --migrate-user)
    public static MoveResult MoveRoaming(string roaming) {
        return MoveUserData(Path.Combine(roaming, OldDataFolder), Path.Combine(roaming, DataFolder));
    }

    // the settings window, before it reads anything: moves only when 2.x data is waiting (Pending)
    public static MoveResult MoveBeforeWindow(string roaming) {
        if (!Pending(Path.Combine(roaming, OldDataFolder), Path.Combine(roaming, DataFolder))) return MoveResult.Nothing;
        return MoveRoaming(roaming);
    }

    // a name per data folder (a user's own session namespace; \ is not allowed in the rest of the name)
    static string MutexName(string newDir) {
        string full = Path.GetFullPath(newDir).TrimEnd('\\').ToUpperInvariant();
        var hash = System.Security.Cryptography.SHA256.Create().ComputeHash(Encoding.UTF8.GetBytes(full));
        return @"Local\DualityIME-migrate-" + BitConverter.ToString(hash, 0, 16).Replace("-", "");
    }

    static MoveResult MoveLocked(string oldDir, string newDir) {
        string marker = Path.Combine(newDir, InProgress);
        if (!Directory.Exists(oldDir)) {
            // moved by the run this one waited for, or by one killed after it removed the old folder
            if (File.Exists(marker)) try { File.Delete(marker); } catch { return MoveResult.Failed; }
            return MoveResult.Nothing;
        }
        if (Migrated(newDir)) return MoveResult.KeptExisting;
        var names = AsIs.Concat(Converted).ToArray();
        bool made = !Directory.Exists(newDir);
        bool resumed = File.Exists(marker);
        // a killed run's temporary files (a resumed move only; this run holds the lock, so none is in use)
        if (resumed)
            foreach (string name in names)
                foreach (string f in Directory.GetFiles(newDir, name + ".migrate-*.tmp"))
                    try { File.Delete(f); } catch { }
        // this run's own temporary names: another program's leftovers are never taken for ours, nor ours deleted by it
        string tag = ".migrate-" + System.Diagnostics.Process.GetCurrentProcess().Id + "-" + DateTime.UtcNow.Ticks + ".tmp";
        var written = new List<string>();
        var moved = new List<string>();
        try {
            Directory.CreateDirectory(newDir);
            if (!resumed) File.WriteAllBytes(marker, new byte[0]);
            foreach (string name in names) {
                string from = Path.Combine(oldDir, name);
                if (!File.Exists(from)) continue;
                string to = Path.Combine(newDir, name), tmp = to + tag;
                if (File.Exists(to)) { moved.Add(name); continue; }   // 3.0 wrote it already: kept (spec 7.2)
                byte[] data = ConvertFile(name, File.ReadAllBytes(from), oldDir);
                // whole on disk under the temporary name, then renamed: a run killed at any point leaves either
                // no file at `to` or a complete one (File.Move fails when `to` is there)
                using (var f = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    f.Write(data, 0, data.Length);
                    f.Flush(true);
                }
                try {
                    File.Move(tmp, to);
                    written.Add(to);
                } finally { if (File.Exists(tmp)) File.Delete(tmp); }
                if (!File.ReadAllBytes(to).SequenceEqual(data)) throw new IOException(to + " was not written");
                moved.Add(name);
            }
        } catch {
            // nothing half moved: what this run wrote goes, the 2.x folder stays (moved again next time)
            foreach (string f in written) try { File.Delete(f); } catch { }
            foreach (string f in names) try { File.Delete(Path.Combine(newDir, f) + tag); } catch { }
            // (a resumed move keeps its marker: what the killed run copied is still there, not complete)
            if (!resumed) try { File.Delete(marker); } catch { }
            if (made) try { if (Directory.Exists(newDir) && !Directory.EnumerateFileSystemEntries(newDir).Any()) Directory.Delete(newDir); } catch { }
            return MoveResult.Failed;
        }
        // everything is in the 3.0 folder now: the marker goes first, and the 2.x leftovers are tidied up as far
        // as they can be (one that cannot be deleted, read-only or held open, never turns a finished move into
        // Failed: the engine would refuse to save at every start)
        try { File.Delete(marker); } catch { return MoveResult.Failed; }
        foreach (string name in moved) try { File.Delete(Path.Combine(oldDir, name)); } catch { }
        foreach (string name in names) try { File.Delete(Path.Combine(oldDir, name + ".tmp")); } catch { }
        try { File.Delete(Path.Combine(oldDir, "languages-checked")); } catch { }
        try { if (!Directory.EnumerateFileSystemEntries(oldDir).Any()) Directory.Delete(oldDir); } catch { }
        return MoveResult.Moved;
    }
}
