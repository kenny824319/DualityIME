// 兩儀輸入法 updater: asks the project's GitHub Releases for a newer installer, downloads it, runs it
// (it raises its own permission prompt) and checks the version that was installed.
// Used by settings.exe (Settings.cs, RunUpdate). Tests: tools\settings_test.cs (fake release lists in
// files, never GitHub): DUALITYIME_UPDATE_TEST_API = a file with the releases JSON,
// DUALITYIME_UPDATE_TEST_INSTALLED = a version.txt to read instead of the installed one.
//
// Codes (shown on the page as U-<code>): 0 latest, 10 a newer version is available (Check),
// 20 cancelled (permission refused), 30 nothing newer to install (Run), 101 GitHub not reachable, 201 download failed or not the
// announced file, 301 the installer failed, 401 the installer's payload or the installed version is not the announced one.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

public static class Updater {
    const string ApiUrl = "https://api.github.com/repos/kenny824319/DualityIME/releases";
    // (the list is newest first and holds every develop push as a pre-release: ask for the most,
    // and for the latest stable on its own so it cannot fall off the page)
    const string TestApi = "DUALITYIME_UPDATE_TEST_API";
    const string TestInstalled = "DUALITYIME_UPDATE_TEST_INSTALLED";
    static readonly Regex Tag = new Regex(@"^v(\d+)\.(\d+)\.(\d+)$");

    public class Release {
        public string Version;
        public string Url;
        public long Size;
        public string Sha256;   // lowercase hex, or null when GitHub gave no digest
    }

    // numeric per field: 3.2.0 > 3.1.12; a missing or bad field counts as 0
    public static int Compare(string a, string b) {
        int[] x = Fields(a), y = Fields(b);
        for (int i = 0; i < Math.Max(x.Length, y.Length); i++) {
            int p = i < x.Length ? x[i] : 0, q = i < y.Length ? y[i] : 0;
            if (p != q) return p > q ? 1 : -1;
        }
        return 0;
    }

    static int[] Fields(string v) {
        string[] parts = (v ?? "").Trim().Split('.');
        var f = new int[parts.Length];
        for (int i = 0; i < parts.Length; i++) {
            int n;
            f[i] = int.TryParse(parts[i], out n) && n > 0 ? n : 0;
        }
        return f;
    }

    // the newest release that is newer than `installed`, or null
    public static Release Pick(string releasesJson, bool beta, string installed) {
        Release best = Highest(releasesJson, beta);
        return best != null && Compare(best.Version, installed) > 0 ? best : null;
    }

    // the highest version among the usable releases ("" when none); includePrereleases: the beta line
    public static string Newest(string releasesJson, bool includePrereleases) {
        Release r = Highest(releasesJson, includePrereleases);
        return r == null ? "" : r.Version;
    }

    // non-draft releases tagged vX.Y.Z that carry DualityIME-X.Y.Z-setup.exe; the highest one
    static Release Highest(string releasesJson, bool beta) {
        object[] list;
        try { list = new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.DeserializeObject(releasesJson) as object[]; } catch { return null; }
        if (list == null) return null;
        Release best = null;
        foreach (object item in list) {
            var rel = item as Dictionary<string, object>;
            if (rel == null) continue;
            if (Flag(rel, "draft") || (Flag(rel, "prerelease") && !beta)) continue;
            Match m = Tag.Match(Text(rel, "tag_name"));
            if (!m.Success) continue;
            string version = m.Groups[1].Value + "." + m.Groups[2].Value + "." + m.Groups[3].Value;
            if (best != null && Compare(version, best.Version) <= 0) continue;
            var assets = rel.ContainsKey("assets") ? rel["assets"] as object[] : null;
            if (assets == null) continue;
            foreach (object ai in assets) {
                var asset = ai as Dictionary<string, object>;
                if (asset == null || Text(asset, "name") != "DualityIME-" + version + "-setup.exe") continue;
                string url = Text(asset, "browser_download_url");
                if (url == "") continue;
                string digest = Text(asset, "digest").ToLowerInvariant();
                long size = 0;
                try { if (asset.ContainsKey("size") && asset["size"] != null) size = Convert.ToInt64(asset["size"]); } catch { }
                best = new Release {
                    Version = version, Url = url, Size = size,
                    Sha256 = digest.StartsWith("sha256:") ? digest.Substring(7) : null
                };
                break;
            }
        }
        return best;
    }

    static bool Flag(Dictionary<string, object> d, string k) { return d.ContainsKey(k) && d[k] is bool && (bool)d[k]; }
    static string Text(Dictionary<string, object> d, string k) { return d.ContainsKey(k) && d[k] != null ? d[k].ToString() : ""; }

    // ---- the installed version ------------------------------------------------------------------
    public static string InstalledPath() {
        string test = Environment.GetEnvironmentVariable(TestInstalled);
        if (!string.IsNullOrEmpty(test)) return test;
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"DualityIME\version.txt");
    }

    // first line of version.txt, "" when there is none
    public static string InstalledVersion() {
        try {
            string[] lines = File.ReadAllLines(InstalledPath());
            return lines.Length > 0 ? lines[0].Trim() : "";
        } catch { return ""; }
    }

    // ---- the steps ------------------------------------------------------------------------------
    public static int Check(bool beta, out string version) {
        string stable, newestBeta;
        return Check(beta, out version, out stable, out newestBeta);
    }

    // also tells the newest stable and the newest of all releases (shown on the page; "" when none)
    public static int Check(bool beta, out string version, out string stable, out string newestBeta) {
        version = ""; stable = ""; newestBeta = "";
        string json = FetchReleases();
        if (json == null) return 101;
        stable = Newest(json, false);
        newestBeta = Newest(json, true);
        Release r = Pick(json, beta, InstalledVersion());
        if (r == null) return 0;
        version = r.Version;
        return 10;
    }

    public static int Run(bool beta, out string version) {
        version = "";
        string json = FetchReleases();
        if (json == null) return 101;
        Release r = Pick(json, beta, InstalledVersion());
        if (r == null) return 30;
        version = r.Version;

        string file = Download(r);
        if (file == null) return 201;
        try {
            int code = Install(file, r.Version);
            if (code != 0) return code;
            return InstalledVersion() == r.Version ? 0 : 401;
        } finally {
            try { File.Delete(file); } catch { }
        }
    }

    // the installer raises its own permission prompt (it asks for administrator rights itself): started
    // through the shell, not with a runas verb, so a refusal comes back as its exit code 20
    // --expect-version: the installer refuses (exit 40) when its payload is not that version
    static int Install(string file, string version) {
        try {
            var psi = new ProcessStartInfo(file, "--update --expect-version " + version) { UseShellExecute = true };
            using (var p = Process.Start(psi)) {
                if (p == null) return 301;
                p.WaitForExit();
                if (p.ExitCode == 20) return 20;
                if (p.ExitCode == 40) return 401;
                return p.ExitCode == 0 ? 0 : 301;
            }
        } catch (Win32Exception e) {
            return e.NativeErrorCode == 1223 ? 20 : 301;   // 1223: the permission prompt was refused
        } catch {
            return 301;
        }
    }

    // ---- network and files ----------------------------------------------------------------------
    static WebRequest Open(string url) {
        try { ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072; } catch { }   // TLS 1.2
        WebRequest req = WebRequest.Create(url);
        req.Timeout = 20000;
        var http = req as HttpWebRequest;
        if (http != null) {
            string installed = InstalledVersion();
            http.UserAgent = "DualityIME/" + (installed == "" ? "0" : installed);
            http.Accept = "application/vnd.github+json";
            http.ReadWriteTimeout = 30000;
        }
        return req;
    }

    // the releases JSON (the list and the latest stable merged), or null when it cannot be read
    static string FetchReleases() {
        try {
            var json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            string list = null, latest = null;
            string test = Environment.GetEnvironmentVariable(TestApi);
            if (!string.IsNullOrEmpty(test)) {
                string path = test.StartsWith("file:", StringComparison.OrdinalIgnoreCase) ? new Uri(test).LocalPath : test;
                if (Directory.Exists(path)) {   // releases.json and latest.json (missing = 404)
                    list = File.ReadAllText(Path.Combine(path, "releases.json"), Encoding.UTF8);
                    string lp = Path.Combine(path, "latest.json");
                    if (File.Exists(lp)) latest = File.ReadAllText(lp, Encoding.UTF8);
                } else list = File.ReadAllText(path, Encoding.UTF8);
            } else {
                list = Read(ApiUrl + "?per_page=100", false);
                latest = Read(ApiUrl + "/latest", true);
            }
            var all = json.DeserializeObject(list) as object[];
            if (all == null) return null;
            var merged = new List<object>(all);
            var one = latest == null ? null : json.DeserializeObject(latest) as Dictionary<string, object>;
            if (one != null) {
                string tag = Text(one, "tag_name");
                bool known = false;
                foreach (object o in all) {
                    var d = o as Dictionary<string, object>;
                    if (d != null && Text(d, "tag_name") == tag) known = true;
                }
                if (!known) merged.Add(one);
            }
            return json.Serialize(merged);
        } catch { return null; }
    }

    // the body at url; null for a 404 when that is allowed (no stable release yet); other failures throw
    static string Read(string url, bool nullOn404) {
        try {
            using (var resp = Open(url).GetResponse())
            using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return rd.ReadToEnd();
        } catch (WebException e) {
            var http = e.Response as HttpWebResponse;
            if (nullOn404 && http != null && http.StatusCode == HttpStatusCode.NotFound) return null;
            throw;
        }
    }

    // downloads the installer into %TEMP%\DualityIME-update\; null when it fails or is not the announced file
    static string Download(Release r) {
        string dir = Path.Combine(Path.GetTempPath(), "DualityIME-update");
        string file = Path.Combine(dir, "DualityIME-" + r.Version + "-setup.exe");
        try {
            Directory.CreateDirectory(dir);
            if (File.Exists(file)) File.Delete(file);
            using (var resp = Open(r.Url).GetResponse())
            using (var src = resp.GetResponseStream())
            using (var dst = File.Create(file))
                src.CopyTo(dst);
            if (r.Size > 0 && new FileInfo(file).Length != r.Size) throw new InvalidDataException("size");
            if (r.Sha256 != null) {
                using (var sha = SHA256.Create())
                using (var fs = File.OpenRead(file)) {
                    string hex = BitConverter.ToString(sha.ComputeHash(fs)).Replace("-", "").ToLowerInvariant();
                    if (hex != r.Sha256) throw new InvalidDataException("hash");
                }
            }
            return file;
        } catch {
            try { File.Delete(file); } catch { }
            return null;
        }
    }
}
