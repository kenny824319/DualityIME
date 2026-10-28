// Tests the settings app's side that writes files (Settings.cs, Backend) in a temporary folder:
// config.json, dictionary.json, 填入注音, 備份與還原. Built and run by tools\test_settings.ps1.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

static class SettingsTest {
    static int failed = 0;
    static void Check(string name, bool ok, string detail) {
        Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok ? "" : "   (" + detail + ")"));
        if (!ok) failed++;
    }

    static int Main(string[] args) {
        Console.OutputEncoding = Encoding.UTF8;
        string dir = Path.Combine(Path.GetTempPath(), "sz-settings-test");
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
        Directory.CreateDirectory(dir);
        var json = new JavaScriptSerializer();
        try {
            var b = new Backend(null, dir);
            var none = new Dictionary<string, object>();
            Func<string, Dictionary<string, object>, object> call = (op, a) => b.Handle(op, a ?? none);

            var state = (Dictionary<string, object>)call("state", null);
            var cfg = (Dictionary<string, object>)state["config"];
            Check("defaults without config.json", (string)cfg["preview"] == "keys_hint" && (bool)cfg["completion"], json.Serialize(cfg));

            call("setConfig", new Dictionary<string, object> { { "key", "preview" }, { "value", "zhuyin" } });
            call("setConfig", new Dictionary<string, object> { { "key", "cand_font_size" }, { "value", 22 } });
            call("setConfig", new Dictionary<string, object> { { "key", "game_mode" }, { "value", true } });
            var saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("setConfig writes config.json", (string)saved["preview"] == "zhuyin" && (int)saved["cand_font_size"] == 22 &&
                  (bool)saved["game_mode"], json.Serialize(saved));

            File.WriteAllText(Path.Combine(dir, "config.json"), "{\"preview\": \"keys_hint\", \"theme\": \"x\"}", new UTF8Encoding(false));
            call("setConfig", new Dictionary<string, object> { { "key", "lookup" }, { "value", false } });
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("other keys in config.json are kept", (string)saved["theme"] == "x" && (bool)saved["lookup"] == false, json.Serialize(saved));

            bool refused = false;
            try { call("setConfig", new Dictionary<string, object> { { "key", "nope" }, { "value", 1 } }); } catch { refused = true; }
            Check("unknown setting refused", refused, "");

            call("forgetWords", null);
            call("forgetWords", new Dictionary<string, object> { { "emails", false } });
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("forgetWords counts up (not the e-mails)", (int)saved["forget_words"] == 2 && (int)saved["forget_emails"] == 0,
                  json.Serialize(saved));
            call("forgetWords", new Dictionary<string, object> { { "emails", true } });
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("forgetWords with the e-mails counts both up", (int)saved["forget_words"] == 3 && (int)saved["forget_emails"] == 1,
                  json.Serialize(saved));

            // 已記憶項目: N typing habits (phrase picks, keys as English but one letter, English words,
            // keys.json's key strings) and M e-mails
            state = (Dictionary<string, object>)call("state", null);
            Check("nothing remembered yet", (int)state["remembered"] == 0 && (int)state["rememberedEmails"] == 0, json.Serialize(state));
            File.WriteAllText(Path.Combine(dir, "user.json"),
                "{\"phrases\": {\"ㄊㄞˊ-ㄨㄢ\": {\"臺灣\": 5, \"台灣\": 1}, \"ㄅㄨˋ\": {\"布\": 2}}, \"english\": [\"t\", \"5 \", \"kfc\"], " +
                "\"words\": {\"claude\": 2}, \"emails\": {\"me@example.com\": 1, \"you@example.com\": 3}, \"forgot\": 3, \"forgot_emails\": 1}",
                new UTF8Encoding(false));   // (the engine has handled the three 全部清除 above)
            File.WriteAllText(Path.Combine(dir, "keys.json"),
                "{\"format\": 1, \"standard\": {\"picks\": {\"817\": {\"\": 2}, \"mp3\": {\"en\": 2}}, \"used\": {}}}", new UTF8Encoding(false));
            state = (Dictionary<string, object>)call("state", null);
            Check("remembered counts", (int)state["remembered"] == 3 + 1 + 1 + 2 && (int)state["rememberedEmails"] == 2, json.Serialize(state));
            // 全部清除: the engine clears at its next key; until then the count is already what it will leave
            call("forgetWords", new Dictionary<string, object> { { "emails", false } });
            state = (Dictionary<string, object>)call("state", null);
            Check("after 全部清除 the typing habits count 0, the e-mails stay", (int)state["remembered"] == 0 &&
                  (int)state["rememberedEmails"] == 2, json.Serialize(state));
            call("forgetWords", new Dictionary<string, object> { { "emails", true } });
            state = (Dictionary<string, object>)call("state", null);
            Check("after 全部清除 with the e-mails both count 0", (int)state["remembered"] == 0 &&
                  (int)state["rememberedEmails"] == 0, json.Serialize(state));
            // the engine cleared (user.json notes the counters it handled) and learned again: counted as usual
            File.WriteAllText(Path.Combine(dir, "user.json"),
                "{\"phrases\": {\"ㄅㄨˋ\": {\"布\": 2}}, \"english\": [], \"words\": {}, \"emails\": {\"a@b.com\": 1}, \"forgot\": 5, \"forgot_emails\": 2}",
                new UTF8Encoding(false));
            File.WriteAllText(Path.Combine(dir, "keys.json"), "{\"format\": 1, \"standard\": {\"picks\": {}, \"used\": {}}}", new UTF8Encoding(false));
            state = (Dictionary<string, object>)call("state", null);
            Check("once the engine cleared, what it learned since counts", (int)state["remembered"] == 1 &&
                  (int)state["rememberedEmails"] == 1, json.Serialize(state));
            File.Delete(Path.Combine(dir, "user.json"));
            File.Delete(Path.Combine(dir, "keys.json"));

            call("saveDictionary", new Dictionary<string, object> {
                { "kind", "words" }, { "rows", new object[] { new object[] { "華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ" } } } });
            call("saveDictionary", new Dictionary<string, object> {
                { "kind", "shortcuts" }, { "rows", new object[] { new object[] { "addr", "台北市 \"信義區\"\n101" } } } });
            var d = (Dictionary<string, object>)call("dictionary", null);
            var words = (List<object>)d["words"];
            var shortcuts = (List<object>)d["shortcuts"];
            Check("dictionary saved and read back", words.Count == 1 && (string)((object[])words[0])[0] == "華碩" &&
                  shortcuts.Count == 1 && (string)((object[])shortcuts[0])[1] == "台北市 \"信義區\"\n101", json.Serialize(d));
            state = (Dictionary<string, object>)call("state", null);
            Check("state counts the dictionary", (int)state["words"] == 1 && (int)state["shortcuts"] == 1, json.Serialize(state));

            var r = (List<object>)call("readings", new Dictionary<string, object> { { "words", new object[] { "台灣", "你好嗎", "㐀㐁" } } });
            Check("填入注音 from the lexicon", (string)r[0] == "ㄊㄞˊ ㄨㄢ" && ((string)r[1]).Split(' ').Length == 3 && (string)r[2] == "",
                  string.Join(" | ", r));

            // 匯出與匯入: one file; importing merges into what is here, the memory through import-memory.json
            File.WriteAllText(Path.Combine(dir, "user.json"),
                "{\"phrases\": {\"ㄊㄞˊ-ㄨㄢ\": {\"臺灣\": 5}}, \"english\": [], \"words\": {\"me@example.com\": 2}, \"forgot\": 3}", new UTF8Encoding(false));
            File.WriteAllText(Path.Combine(dir, "dictionary.json"),
                "{\"words\": [[\"華碩\", \"ㄏㄨㄚˊ ㄕㄨㄛˋ\"]], \"shortcuts\": [[\"kfc\", \"肯德基\"]]}", new UTF8Encoding(false));
            // 校正記憶 and 代碼記憶: a file of their own, exported with the memory
            File.WriteAllText(Path.Combine(dir, "keys.json"),
                "{\"format\": 1, \"standard\": {\"picks\": {\"817\": {\"\": 2}}, \"used\": {\"817\": {\"\": 20000}}}, \"eten\": {\"picks\": {}, \"used\": {}}}",
                new UTF8Encoding(false));
            call("setConfig", new Dictionary<string, object> { { "key", "preview" }, { "value", "zhuyin" } });
            call("forgetWords", null);
            string file = Path.Combine(dir, "backup.json");
            b.WriteBackup(file);
            var saved2 = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(file, Encoding.UTF8));
            var savedConfig = (Dictionary<string, object>)saved2["config"];
            Check("backup holds memory, dictionary and settings, not the counters",
                  (string)saved2["app"] == "兩儀輸入法" && ((Dictionary<string, object>)saved2["memory"]).ContainsKey("phrases") &&
                  !((Dictionary<string, object>)saved2["memory"]).ContainsKey("forgot") && (string)savedConfig["preview"] == "zhuyin" &&
                  !savedConfig.ContainsKey("forget_words") && !savedConfig.ContainsKey("forget_emails") &&
                  !savedConfig.ContainsKey("import_memory"), json.Serialize(saved2));
            Check("the export holds 校正記憶 with the memory",
                  ((Dictionary<string, object>)saved2["memory"]).ContainsKey("keys"), json.Serialize(saved2));

            // another computer: different settings and dictionary, one row the same
            File.Delete(Path.Combine(dir, "user.json"));
            call("setConfig", new Dictionary<string, object> { { "key", "preview" }, { "value", "keys_hint" } });
            File.WriteAllText(Path.Combine(dir, "dictionary.json"),
                "{\"words\": [[\"華碩\", \"ㄏㄨㄚˊ ㄕㄨㄛˊ\"], [\"史芸\", \"ㄕˇ ㄩㄣˊ\"]], \"shortcuts\": []}", new UTF8Encoding(false));
            var info = (Dictionary<string, object>)b.ReadBackup(file);
            Check("opening a backup tells what it holds (校正記憶 counted with the memory)", (int)info["memory"] == 3 && (int)info["words"] == 1 &&
                  (int)info["shortcuts"] == 1 && (bool)info["config"], json.Serialize(info));
            int forgetBefore = (int)((Dictionary<string, object>)json.DeserializeObject(
                File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8)))["forget_words"];
            var done = (Dictionary<string, object>)call("restoreBackup", new Dictionary<string, object> {
                { "memory", true }, { "words", true }, { "shortcuts", true }, { "config", false } });
            d = (Dictionary<string, object>)call("dictionary", null);
            var restoredWords = (List<object>)d["words"];
            Check("restore merges the dictionary (a word here is kept as it is)", (int)done["words"] == 0 && (int)done["shortcuts"] == 1 &&
                  restoredWords.Count == 2 && (string)((object[])restoredWords[0])[1] == "ㄏㄨㄚˊ ㄕㄨㄛˊ" &&
                  ((List<object>)d["shortcuts"]).Count == 1, json.Serialize(d));
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("restore without the settings keeps them; the IME is told to merge the memory",
                  (string)saved["preview"] == "keys_hint" && (int)saved["import_memory"] == 1 && (int)saved["forget_words"] == forgetBefore &&
                  File.Exists(Path.Combine(dir, "import-memory.json")), json.Serialize(saved));
            Check("校正記憶 goes to the IME with the memory",
                  File.ReadAllText(Path.Combine(dir, "import-memory.json"), Encoding.UTF8).Contains("\"keys\""), "import-memory.json");
            b.ReadBackup(file);
            call("restoreBackup", new Dictionary<string, object> { { "config", true } });
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("restore with the settings takes them (counters stay this computer's)",
                  (string)saved["preview"] == "zhuyin" && (int)saved["import_memory"] == 1 && (int)saved["forget_words"] == forgetBefore, json.Serialize(saved));
            File.WriteAllText(file, "{\"words\": []}", new UTF8Encoding(false));
            var bad = (Dictionary<string, object>)b.ReadBackup(file);
            Check("a file that is not a backup is refused", bad.ContainsKey("error"), json.Serialize(bad));
            File.WriteAllText(file, "{\"app\": \"other\", \"format\": 1}", new UTF8Encoding(false));
            bad = (Dictionary<string, object>)b.ReadBackup(file);
            Check("a file of another app is refused with the message", (string)bad["error"] == "這不是兩儀輸入法匯出的檔案", json.Serialize(bad));

            // the export is 3.0
            b.WriteBackup(file);
            var exported = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(file, Encoding.UTF8));
            Check("export is 3.0", (string)exported["app"] == "兩儀輸入法" && (int)exported["format"] == 1, json.Serialize(exported));

            // import of a 2.x file: converted through Migrate
            File.Delete(Path.Combine(dir, "import-memory.json"));
            File.WriteAllText(file,
                "{\"app\":\"智慧輸入\",\"format\":1,\"created\":\"2026-01-01 10:00\"," +
                "\"memory\":{\"phrases\":{\"ㄊㄞˊ-ㄨㄢ\":{\"臺灣\":5}},\"english\":[\"t\",\"kfc\"],\"words\":{\"claude\":2,\"a@b.com\":1,\"https://x.com\":1},\"used\":{\"words\":{\"a@b.com\":20000}},\"autocorrect\":{\"format\":1,\"standard\":{\"picks\":{\"817\":{\"\":2}},\"used\":{}},\"eten\":{\"picks\":{\"5\":{\"\":1}},\"used\":{}}}}," +
                "\"dictionary\":{\"words\":[],\"shortcuts\":[]},\"config\":{\"fix_doubles\":true,\"layout\":\"eten\",\"preview\":\"zhuyin\"}}",
                new UTF8Encoding(false));
            var old2 = b.ReadBackup(file) as Dictionary<string, object>;
            Check("import of a 2.x file reads and counts the converted memory",
                  old2 != null && !old2.ContainsKey("error") && (int)old2["memory"] == 4, json.Serialize(old2));
            call("restoreBackup", new Dictionary<string, object> { { "memory", true }, { "config", true } });
            saved = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(Path.Combine(dir, "config.json"), Encoding.UTF8));
            Check("a 2.x import leaves 3.0 settings",
                  saved.ContainsKey("autocorrect") && (bool)saved["autocorrect"] && !saved.ContainsKey("fix_doubles") && !saved.ContainsKey("layout"),
                  json.Serialize(saved));
            string im = File.ReadAllText(Path.Combine(dir, "import-memory.json"), Encoding.UTF8);
            Check("a 2.x import hands the IME keys, not autocorrect", im.Contains("\"keys\"") && !im.Contains("\"autocorrect\""), im);
            var imObj = Obj(json, im);
            Check("a 2.x import hands the IME emails, no URL",
                  imObj.ContainsKey("emails") && ((Dictionary<string, object>)imObj["emails"]).ContainsKey("a@b.com") &&
                  !im.Contains("x.com") && !im.Contains("\"t\""), im);

            var k = (Dictionary<string, object>)call("hotkeys", null);
            Check("Windows' hotkeys read (not changed)", k.ContainsKey("language") && k.ContainsKey("layout"), json.Serialize(k));

            MigrateTests(dir, json);
            UpdaterTests(dir, json);
        } catch (Exception e) {
            Console.WriteLine("ERROR " + e);
            failed++;
        } finally {
            Directory.Delete(dir, true);
        }
        Console.WriteLine(failed == 0 ? "all passed" : failed + " failed");
        return failed;
    }

    static Dictionary<string, object> Obj(JavaScriptSerializer json, string text) {
        return (Dictionary<string, object>)json.DeserializeObject(text);
    }

    static Dictionary<string, object> ReadObj(JavaScriptSerializer json, string path) {
        return Obj(json, File.ReadAllText(path, Encoding.UTF8));
    }

    // 2.x data (智慧輸入, %APPDATA%\SmartZhuyin) to 3.0 (Migrate.cs); each check in a folder of its own
    static void MigrateTests(string dir, JavaScriptSerializer json) {
        var utf8 = new UTF8Encoding(false);
        Func<string, string> fresh = name => {
            string p = Path.Combine(dir, name);
            Directory.CreateDirectory(p);
            return p;
        };

        var c = Migrate.Config(Obj(json, "{\"layout\":\"eten\",\"fix_doubles\":true,\"autocorrect\":false,\"preview\":\"zhuyin\"}"));
        Check("migrate config", c.ContainsKey("autocorrect") && (bool)c["autocorrect"] && (string)c["preview"] == "zhuyin" &&
              !c.ContainsKey("layout") && !c.ContainsKey("fix_doubles"), json.Serialize(c));

        c = Migrate.Config(Obj(json, "{\"fix_doubles\":false,\"autocorrect\":true}"));
        Check("migrate config off", c.ContainsKey("autocorrect") && (bool)c["autocorrect"] && !c.ContainsKey("fix_doubles"), json.Serialize(c));

        var k = Migrate.Keys(Obj(json, "{\"format\":1,\"standard\":{\"picks\":{\"817\":{\"\":2}},\"used\":{}},\"eten\":{\"picks\":{\"x\":{\"\":5}},\"used\":{}}}"));
        bool keysOk = false;
        try {
            var picks = (Dictionary<string, object>)((Dictionary<string, object>)k["standard"])["picks"];
            keysOk = k.Count == 2 && k.ContainsKey("format") && k.ContainsKey("standard") &&
                     (int)((Dictionary<string, object>)picks["817"])[""] == 2;
        } catch { }
        Check("migrate keys", keysOk, json.Serialize(k));

        var um = Obj(json, "{\"phrases\":{\"a\":{\"b\":1}},\"english\":[\"t\",\"kfc\"],\"forgot\":3,\"forgot_emails\":1,\"extra\":7," +
            "\"words\":{\"claude\":3,\"a@b.com\":1,\"https://x.com\":1,\"www.y.org\":2}," +
            "\"used\":{\"phrases\":{\"a\":{\"b\":5}},\"words\":{\"claude\":100,\"a@b.com\":200,\"https://x.com\":300,\"www.y.org\":400}}}");
        var u1 = Migrate.User(um);
        bool userOk = false;
        try {
            var w = (Dictionary<string, object>)u1["words"];
            var em = (Dictionary<string, object>)u1["emails"];
            var us = (Dictionary<string, object>)u1["used"];
            var uw = (Dictionary<string, object>)us["words"];
            var ue = (Dictionary<string, object>)us["emails"];
            userOk = w.Count == 1 && (int)w["claude"] == 3 && em.Count == 1 && (int)em["a@b.com"] == 1 &&
                     ue.Count == 1 && (int)ue["a@b.com"] == 200 && uw.Count == 1 && (int)uw["claude"] == 100 &&
                     !json.Serialize(u1).Contains("x.com") && !json.Serialize(u1).Contains("y.org") &&
                     ((System.Collections.IList)u1["english"]).Count == 1 && (string)((System.Collections.IList)u1["english"])[0] == "kfc" &&
                     (int)u1["forgot"] == 3 && (int)u1["forgot_emails"] == 1 && (int)u1["extra"] == 7 &&
                     ((Dictionary<string, object>)u1["phrases"]).ContainsKey("a") && ((Dictionary<string, object>)us["phrases"]).ContainsKey("a");
        } catch { }
        Check("migrate user memory", userOk, json.Serialize(u1));
        Check("migrate user memory twice", json.Serialize(Migrate.User(u1)) == json.Serialize(u1), json.Serialize(Migrate.User(u1)));

        // one letter or digit key, with or without spaces, is never English (the engines' is_one_key)
        var oneKey = Migrate.User(Obj(json, "{\"english\":[\"5\",\"5 \",\"t  \",\"Z\",\"55\",\"kfc\",\"ㄅ\",\"@\",\" 5\",7]}"));
        string kept1 = json.Serialize(oneKey["english"]);
        Check("migrate drops one-key english (letter or digit, spaces after it)",
              kept1 == "[\"55\",\"kfc\",\"ㄅ\",\"@\",\" 5\",7]", kept1);
        Check("IsOneKey", Migrate.IsOneKey("5") && Migrate.IsOneKey("0 ") && Migrate.IsOneKey("a") && Migrate.IsOneKey("Z ") &&
              !Migrate.IsOneKey("55") && !Migrate.IsOneKey("") && !Migrate.IsOneKey(" ") && !Migrate.IsOneKey("ㄅ") &&
              !Migrate.IsOneKey("@") && !Migrate.IsOneKey(" 5") && !Migrate.IsOneKey("５"), "");

        var backup = Migrate.Backup(Obj(json, "{\"app\":\"智慧輸入\",\"format\":1," +
            "\"memory\":{\"phrases\":{},\"autocorrect\":{\"format\":1,\"standard\":{\"picks\":{\"817\":{\"\":2}},\"used\":{}},\"eten\":{\"picks\":{},\"used\":{}}}}," +
            "\"config\":{\"fix_doubles\":true,\"layout\":\"standard\"}}"));
        bool backupOk = false;
        try {
            var memory = (Dictionary<string, object>)backup["memory"];
            var config = (Dictionary<string, object>)backup["config"];
            backupOk = (string)backup["app"] == "兩儀輸入法" && memory.ContainsKey("keys") && !memory.ContainsKey("autocorrect") &&
                       !((Dictionary<string, object>)memory["keys"]).ContainsKey("eten") && (bool)config["autocorrect"] &&
                       !config.ContainsKey("fix_doubles");
        } catch { }
        Check("migrate backup", backupOk, json.Serialize(backup));

        // the six files of a 2.x data folder
        Action<string> writeOld = old => {
            Directory.CreateDirectory(old);
            File.WriteAllText(Path.Combine(old, "user.json"), "{\"phrases\": {\"ㄊㄞˊ-ㄨㄢ\": {\"臺灣\": 5}}, \"english\": [], \"words\": {\"a@b.com\": 1, \"https://x.com\": 1}}", utf8);
            File.WriteAllText(Path.Combine(old, "dictionary.json"), "{\"words\": [[\"華碩\", \"ㄏㄨㄚˊ ㄕㄨㄛˋ\"]], \"shortcuts\": []}", utf8);
            File.WriteAllText(Path.Combine(old, "keyboard.json"), "{\"x\": 1, \"y\": 2}", utf8);
            File.WriteAllText(Path.Combine(old, "config.json"), "{\"layout\": \"eten\", \"fix_doubles\": true, \"preview\": \"zhuyin\"}", utf8);
            File.WriteAllText(Path.Combine(old, "keys.json"),
                "{\"format\": 1, \"standard\": {\"picks\": {\"817\": {\"\": 2}}, \"used\": {}}, \"eten\": {\"picks\": {}, \"used\": {}}}", utf8);
            File.WriteAllText(Path.Combine(old, "import-memory.json"),
                "{\"phrases\": {}, \"autocorrect\": {\"format\": 1, \"standard\": {\"picks\": {}, \"used\": {}}}}", utf8);
        };
        string[] six = { "user.json", "dictionary.json", "keyboard.json", "config.json", "keys.json", "import-memory.json" };

        string root = fresh("migrate-moves");
        string oldDir = Path.Combine(root, "SmartZhuyin"), newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "user.json.tmp"), "{", utf8);  // 2.x leftovers, deleted with the folder
        File.WriteAllText(Path.Combine(oldDir, "languages-checked"), "", utf8);
        byte[] userBefore = File.ReadAllBytes(Path.Combine(oldDir, "user.json"));
        var moved = Migrate.MoveUserData(oldDir, newDir);
        bool movedOk = false;
        string movedDetail = moved.ToString();
        try {
            bool all = true;
            foreach (string f in six) all &= File.Exists(Path.Combine(newDir, f));
            var newConfig = ReadObj(json, Path.Combine(newDir, "config.json"));
            var newKeys = ReadObj(json, Path.Combine(newDir, "keys.json"));
            var newImport = ReadObj(json, Path.Combine(newDir, "import-memory.json"));
            movedDetail += " " + json.Serialize(newConfig) + " " + json.Serialize(newKeys) + " " + json.Serialize(newImport);
            movedOk = moved == Migrate.MoveResult.Moved && all && !Directory.Exists(oldDir) &&
                      (bool)newConfig["autocorrect"] && !newConfig.ContainsKey("layout") && !newConfig.ContainsKey("fix_doubles") &&
                      !newKeys.ContainsKey("eten") && newKeys.ContainsKey("standard") &&
                      newImport.ContainsKey("keys") && !newImport.ContainsKey("autocorrect") &&
                      ReadObj(json, Path.Combine(newDir, "user.json")).ContainsKey("emails") &&
                      ((Dictionary<string, object>)ReadObj(json, Path.Combine(newDir, "user.json"))["emails"]).ContainsKey("a@b.com") &&
                      !File.ReadAllText(Path.Combine(newDir, "user.json"), utf8).Contains("x.com") &&
                      Directory.GetFiles(newDir, "*.tmp").Length == 0;
        } catch (Exception e) { movedDetail += " " + e.Message; }
        Check("migrate moves user data", movedOk, movedDetail);

        root = fresh("migrate-keeps");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        Directory.CreateDirectory(newDir);
        File.WriteAllText(Path.Combine(newDir, "user.json"), "{\"phrases\": {}, \"english\": [\"mine\"]}", utf8);
        byte[] newUser = File.ReadAllBytes(Path.Combine(newDir, "user.json"));
        var kept = Migrate.MoveUserData(oldDir, newDir);
        bool oldAll = true;
        foreach (string f in six) oldAll &= File.Exists(Path.Combine(oldDir, f));
        Check("migrate keeps existing 3.0 data", kept == Migrate.MoveResult.KeptExisting && oldAll &&
              Convert.ToBase64String(File.ReadAllBytes(Path.Combine(newDir, "user.json"))) == Convert.ToBase64String(newUser) &&
              Directory.GetFiles(newDir).Length == 1, kept.ToString());

        // a 2.x file that cannot be deleted (read-only) does not make a finished move fail
        root = fresh("migrate-stuck-old");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        string stuck = Path.Combine(oldDir, "user.json");
        File.SetAttributes(stuck, FileAttributes.ReadOnly);
        var stuckMove = Migrate.MoveUserData(oldDir, newDir);
        bool stuckAll = true;
        foreach (string f in six) stuckAll &= File.Exists(Path.Combine(newDir, f));
        Check("migrate finishes when a 2.x file cannot be deleted", stuckMove == Migrate.MoveResult.Moved && stuckAll &&
              Migrate.Migrated(newDir) && !File.Exists(Path.Combine(newDir, "migrating")) && File.Exists(stuck),
              stuckMove.ToString());
        File.SetAttributes(stuck, FileAttributes.Normal);

        root = fresh("migrate-broken");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"layout\": \"eten\",", utf8);
        byte[] brokenBefore = File.ReadAllBytes(Path.Combine(oldDir, "config.json"));
        var broken = Migrate.MoveUserData(oldDir, newDir);
        string newBroken = File.Exists(Path.Combine(newDir, "config.json"))
            ? Convert.ToBase64String(File.ReadAllBytes(Path.Combine(newDir, "config.json"))) : "(missing)";
        Check("migrate copies broken json as is", broken == Migrate.MoveResult.Moved && newBroken == Convert.ToBase64String(brokenBefore),
              broken + " " + newBroken);

        root = fresh("migrate-own-files");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"preview\": \"zhuyin\"}", utf8);
        File.WriteAllText(Path.Combine(oldDir, "mine.json"), "{\"app\": \"智慧輸入\", \"memory\": {}}", utf8);
        byte[] mineBefore = File.ReadAllBytes(Path.Combine(oldDir, "mine.json"));
        var own = Migrate.MoveUserData(oldDir, newDir);
        string mineAfter = File.Exists(Path.Combine(oldDir, "mine.json"))
            ? Convert.ToBase64String(File.ReadAllBytes(Path.Combine(oldDir, "mine.json"))) : "(missing)";
        Check("migrate keeps the user's own files", own == Migrate.MoveResult.Moved && File.Exists(Path.Combine(newDir, "config.json")) &&
              Directory.Exists(oldDir) && mineAfter == Convert.ToBase64String(mineBefore) &&
              !File.Exists(Path.Combine(oldDir, "config.json")), own + " " + mineAfter);

        root = fresh("migrate-nothing");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        var nothing = Migrate.MoveUserData(oldDir, newDir);
        Check("migrate nothing", nothing == Migrate.MoveResult.Nothing && !Directory.Exists(newDir), nothing.ToString());

        // settings.exe --migrate-user (what the engine runs for another account): APPDATA pointed at a temp folder
        root = fresh("migrate-user");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"fix_doubles\": true, \"preview\": \"zhuyin\"}", utf8);
        string exe = typeof(Backend).Assembly.Location;
        int code = -1;
        string cmdDetail = "";
        try {
            var psi = new System.Diagnostics.ProcessStartInfo(exe, "--migrate-user") { UseShellExecute = false, CreateNoWindow = true };
            psi.EnvironmentVariables["APPDATA"] = root;
            using (var p = System.Diagnostics.Process.Start(psi)) {
                if (!p.WaitForExit(30000)) { p.Kill(); cmdDetail = "timeout"; }
                else code = p.ExitCode;
            }
        } catch (Exception e) { cmdDetail = e.Message; }
        bool cmdOk = false;
        try {
            var cfg2 = ReadObj(json, Path.Combine(newDir, "config.json"));
            cmdDetail += " " + code + " " + json.Serialize(cfg2);
            cmdOk = code == (int)Migrate.MoveResult.Moved && (bool)cfg2["autocorrect"] && !cfg2.ContainsKey("fix_doubles") &&
                    (string)cfg2["preview"] == "zhuyin" && !Directory.Exists(oldDir);
        } catch (Exception e) { cmdDetail += " " + code + " " + e.Message; }
        Check("migrate-user command", cmdOk, cmdDetail);

        // --migrate-user <roamingDir>: the folder given (the engine's own Roaming folder) wins over APPDATA
        root = fresh("migrate-user-arg");
        string elsewhere = fresh("migrate-user-arg-appdata");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"preview\": \"zhuyin\"}", utf8);
        Directory.CreateDirectory(Path.Combine(elsewhere, "SmartZhuyin"));
        File.WriteAllText(Path.Combine(elsewhere, "SmartZhuyin", "config.json"), "{\"preview\": \"zhuyin\"}", utf8);
        code = -1;
        cmdDetail = "";
        try {
            var psi = new System.Diagnostics.ProcessStartInfo(exe, "--migrate-user \"" + root + "\"") { UseShellExecute = false, CreateNoWindow = true };
            psi.EnvironmentVariables["APPDATA"] = elsewhere;
            using (var p = System.Diagnostics.Process.Start(psi)) {
                if (!p.WaitForExit(30000)) { p.Kill(); cmdDetail = "timeout"; }
                else code = p.ExitCode;
            }
        } catch (Exception e) { cmdDetail = e.Message; }
        Check("migrate-user command with the Roaming folder given",
              code == (int)Migrate.MoveResult.Moved && File.Exists(Path.Combine(newDir, "config.json")) && !Directory.Exists(oldDir) &&
              File.Exists(Path.Combine(elsewhere, "SmartZhuyin", "config.json")) && !Directory.Exists(Path.Combine(elsewhere, "DualityIME")),
              code + " " + cmdDetail);

        // a move killed half way (no rollback ran): the marker and one copied file in the 3.0 folder, the 2.x
        // folder whole. Not taken for done; the next run keeps the copied file, copies the rest, removes the old folder
        root = fresh("migrate-killed");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        Directory.CreateDirectory(newDir);
        File.WriteAllBytes(Path.Combine(newDir, Migrate.InProgress), new byte[0]);
        File.Copy(Path.Combine(oldDir, "dictionary.json"), Path.Combine(newDir, "dictionary.json"));
        bool notDone = !Migrate.Migrated(newDir) && Migrate.Pending(oldDir, newDir);
        var resumed = Migrate.MoveUserData(oldDir, newDir);
        bool allNew = true;
        foreach (string f in six) allNew &= File.Exists(Path.Combine(newDir, f));
        string resumedDetail = resumed + " notDone " + notDone + " all " + allNew + " " + string.Join(",", Directory.GetFiles(newDir).Select(Path.GetFileName));
        bool resumedOk = false;
        try {
            resumedOk = notDone && resumed == Migrate.MoveResult.Moved && allNew && !Directory.Exists(oldDir) &&
                        !File.Exists(Path.Combine(newDir, Migrate.InProgress)) && Migrate.Migrated(newDir) &&
                        (bool)ReadObj(json, Path.Combine(newDir, "config.json"))["autocorrect"] &&
                        ((Dictionary<string, object>)ReadObj(json, Path.Combine(newDir, "user.json"))["emails"]).ContainsKey("a@b.com") &&
                        Directory.GetFiles(newDir, "*.tmp").Length == 0;
        } catch (Exception e) { resumedDetail += " " + e.Message; }
        Check("a move killed half way is finished by the next run", resumedOk, resumedDetail);

        // killed while writing a temporary file: the leftover goes, the file is written whole from the 2.x one
        root = fresh("migrate-killed-tmp");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        Directory.CreateDirectory(newDir);
        File.WriteAllBytes(Path.Combine(newDir, Migrate.InProgress), new byte[0]);
        File.WriteAllText(Path.Combine(newDir, "user.json.migrate-1-2.tmp"), "{\"phr", utf8);
        var expectUser = json.Serialize(Migrate.User(ReadObj(json, Path.Combine(oldDir, "user.json"))));
        var tmpResumed = Migrate.MoveUserData(oldDir, newDir);
        string tmpDetail = tmpResumed.ToString();
        bool tmpOk = false;
        try {
            string gotUser = File.ReadAllText(Path.Combine(newDir, "user.json"), utf8);
            tmpDetail += " " + gotUser + " tmp " + string.Join(",", Directory.GetFiles(newDir, "*.tmp").Select(Path.GetFileName));
            tmpOk = tmpResumed == Migrate.MoveResult.Moved && gotUser == expectUser &&
                    Directory.GetFiles(newDir, "*.tmp").Length == 0 && !Directory.Exists(oldDir) &&
                    !File.Exists(Path.Combine(newDir, Migrate.InProgress));
        } catch (Exception e) { tmpDetail += " " + e.Message; }
        Check("a move killed while writing a temporary file: the leftover goes, the file is whole", tmpOk, tmpDetail);

        // killed after the old folder was removed, before the marker was: the next run only removes the marker
        root = fresh("migrate-killed-late");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(newDir);
        File.WriteAllText(Path.Combine(newDir, "user.json"), "{\"phrases\": {}}", utf8);
        File.WriteAllBytes(Path.Combine(newDir, Migrate.InProgress), new byte[0]);
        bool lateBefore = !Migrate.Migrated(newDir);
        var late = Migrate.MoveUserData(oldDir, newDir);
        Check("a move killed after removing the old folder only leaves its marker, removed next time",
              lateBefore && late == Migrate.MoveResult.Nothing && !File.Exists(Path.Combine(newDir, Migrate.InProgress)) &&
              Migrate.Migrated(newDir) && File.Exists(Path.Combine(newDir, "user.json")), late + " " + lateBefore);

        // the marker never outlives a finished move
        root = fresh("migrate-marker-gone");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        var plain = Migrate.MoveUserData(oldDir, newDir);
        Check("a finished move leaves no marker", plain == Migrate.MoveResult.Moved && !File.Exists(Path.Combine(newDir, Migrate.InProgress)),
              plain.ToString());

        // the 全部清除 counters of a moved config.json: what the 2.x user.json noted as handled, so nothing is cleared
        // (a 2.x config ahead of its user.json would otherwise clear all typing memory at the first settings change)
        root = fresh("migrate-counters");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"forget_words\": 7, \"forget_emails\": 5, \"preview\": \"zhuyin\"}", utf8);
        File.WriteAllText(Path.Combine(oldDir, "user.json"), "{\"phrases\": {}, \"forgot\": 3, \"forgot_emails\": 2}", utf8);
        var counted = Migrate.MoveUserData(oldDir, newDir);
        string countDetail = counted.ToString();
        bool countOk = false;
        try {
            var mc = ReadObj(json, Path.Combine(newDir, "config.json"));
            var u = ReadObj(json, Path.Combine(newDir, "user.json"));
            countDetail += " " + json.Serialize(mc) + " " + json.Serialize(u);
            countOk = counted == Migrate.MoveResult.Moved && Convert.ToInt64(mc["forget_words"]) == 3 &&
                      Convert.ToInt64(mc["forget_emails"]) == 2 && Convert.ToInt64(u["forgot"]) == 3 && (string)mc["preview"] == "zhuyin";
        } catch (Exception e) { countDetail += " " + e.Message; }
        Check("a moved config has no 全部清除 pending", countOk, countDetail);

        root = fresh("migrate-counters-no-user");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"forget_words\": 4, \"forget_emails\": 1}", utf8);
        counted = Migrate.MoveUserData(oldDir, newDir);
        countDetail = counted.ToString();
        countOk = false;
        try {
            var mc = ReadObj(json, Path.Combine(newDir, "config.json"));
            countDetail += " " + json.Serialize(mc);
            countOk = counted == Migrate.MoveResult.Moved && Convert.ToInt64(mc["forget_words"]) == 0 && Convert.ToInt64(mc["forget_emails"]) == 0;
        } catch (Exception e) { countDetail += " " + e.Message; }
        Check("a moved config without a 2.x user.json starts its counters at 0", countOk, countDetail);

        root = fresh("migrate-counters-bad-user");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{\"forget_words\": 4}", utf8);
        File.WriteAllText(Path.Combine(oldDir, "user.json"), "{\"phrases\": ", utf8);
        counted = Migrate.MoveUserData(oldDir, newDir);
        countDetail = counted.ToString();
        countOk = false;
        try {
            var mc = ReadObj(json, Path.Combine(newDir, "config.json"));
            countDetail += " " + json.Serialize(mc);
            countOk = counted == Migrate.MoveResult.Moved && Convert.ToInt64(mc["forget_words"]) == 0;
        } catch (Exception e) { countDetail += " " + e.Message; }
        Check("a moved config with an unreadable 2.x user.json starts its counters at 0", countOk, countDetail);

        // the settings window started before the engine ever ran: the 2.x data is moved first; once moved, left alone
        root = fresh("migrate-window");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        var window = Migrate.MoveBeforeWindow(root);
        allNew = Directory.Exists(newDir);
        if (allNew) foreach (string f in six) allNew &= File.Exists(Path.Combine(newDir, f));
        Directory.CreateDirectory(oldDir);
        File.WriteAllText(Path.Combine(oldDir, "config.json"), "{}", utf8);   // (2.x run again afterwards)
        var windowAgain = Migrate.MoveBeforeWindow(root);
        Check("the settings window moves 2.x data waiting, and only then",
              window == Migrate.MoveResult.Moved && allNew && windowAgain == Migrate.MoveResult.Nothing &&
              File.Exists(Path.Combine(oldDir, "config.json")), window + " " + windowAgain + " " + allNew);

        // a move that fails (user.json locked by another program) leaves no 3.0 folder behind, and the next run moves everything
        root = fresh("migrate-retry");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        Migrate.MoveResult first;
        using (new FileStream(Path.Combine(oldDir, "user.json"), FileMode.Open, FileAccess.Read, FileShare.None))
            first = Migrate.MoveUserData(oldDir, newDir);
        bool oldKept = true;
        foreach (string f in six) oldKept &= File.Exists(Path.Combine(oldDir, f));
        Check("a failed move leaves no 3.0 folder and the 2.x data as it was",
              first == Migrate.MoveResult.Failed && !Directory.Exists(newDir) && oldKept && !Migrate.Migrated(newDir),
              first + " newDir " + Directory.Exists(newDir) + " old " + oldKept);
        var second = Migrate.MoveUserData(oldDir, newDir);
        allNew = true;
        if (Directory.Exists(newDir)) foreach (string f in six) allNew &= File.Exists(Path.Combine(newDir, f));
        else allNew = false;
        Check("the next run after a failed move moves everything",
              second == Migrate.MoveResult.Moved && allNew && !Directory.Exists(oldDir), second + " " + allNew);

        // a 3.0 folder holding none of the data files (only the symbol keyboard's position, or a file of the
        // user's) is not migrated yet: the data moves, 3.0's own keyboard.json and the other file stay
        root = fresh("migrate-unknown-file");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        Directory.CreateDirectory(newDir);
        File.WriteAllText(Path.Combine(newDir, "notes.txt"), "mine", utf8);
        File.WriteAllText(Path.Combine(newDir, "keyboard.json"), "{\"x\": 9, \"y\": 9}", utf8);
        var unknown = Migrate.MoveUserData(oldDir, newDir);
        allNew = true;
        foreach (string f in six) allNew &= File.Exists(Path.Combine(newDir, f));
        Check("a 3.0 folder with only other files still gets the 2.x data",
              unknown == Migrate.MoveResult.Moved && allNew && !Directory.Exists(oldDir) &&
              File.ReadAllText(Path.Combine(newDir, "notes.txt"), utf8) == "mine" &&
              File.ReadAllText(Path.Combine(newDir, "keyboard.json"), utf8) == "{\"x\": 9, \"y\": 9}" &&
              Directory.GetFiles(newDir, "*.tmp").Length == 0,
              unknown + " " + allNew + " " + string.Join(",", Directory.GetFiles(newDir)));

        // two moves at once (setup.exe and the engine's settings.exe --migrate-user): one moves, the other
        // finds it done; nothing is lost and no temporary file stays
        root = fresh("migrate-together");
        oldDir = Path.Combine(root, "SmartZhuyin");
        newDir = Path.Combine(root, "DualityIME");
        writeOld(oldDir);
        var codes = new List<int>();
        string togetherDetail = "";
        try {
            var procs = new List<System.Diagnostics.Process>();
            for (int i = 0; i < 3; i++) {
                var psi = new System.Diagnostics.ProcessStartInfo(exe, "--migrate-user") { UseShellExecute = false, CreateNoWindow = true };
                psi.EnvironmentVariables["APPDATA"] = root;
                procs.Add(System.Diagnostics.Process.Start(psi));
            }
            foreach (var p in procs) {
                if (!p.WaitForExit(30000)) { p.Kill(); togetherDetail += "timeout "; }
                else codes.Add(p.ExitCode);
                p.Dispose();
            }
        } catch (Exception e) { togetherDetail += e.Message; }
        allNew = Directory.Exists(newDir);
        if (allNew) foreach (string f in six) allNew &= File.Exists(Path.Combine(newDir, f));
        togetherDetail += " codes " + string.Join(",", codes) + " all " + allNew;
        Check("two moves at once: one moves, the others find it done",
              codes.Count == 3 && codes.Count(x => x == (int)Migrate.MoveResult.Moved) == 1 &&
              codes.All(x => x == (int)Migrate.MoveResult.Moved || x == (int)Migrate.MoveResult.KeptExisting ||
                             x == (int)Migrate.MoveResult.Nothing) &&
              allNew && !Directory.Exists(oldDir) && Directory.GetFiles(newDir, "*.tmp").Length == 0 &&
              ((Dictionary<string, object>)ReadObj(json, Path.Combine(newDir, "user.json"))["emails"]).ContainsKey("a@b.com"),
              togetherDetail);
    }

    // the update check against fake release lists (files in a temp folder; never GitHub)
    static string Releases(params string[] items) { return "[" + string.Join(",", items) + "]"; }

    static string Rel(string tag, bool prerelease, bool draft, params string[] assetNames) {
        var assets = new List<string>();
        foreach (string n in assetNames)
            assets.Add("{\"name\":\"" + n + "\",\"size\":1234,\"digest\":\"sha256:abcd\",\"browser_download_url\":\"https://example.invalid/" + n + "\"}");
        return "{\"tag_name\":\"" + tag + "\",\"draft\":" + (draft ? "true" : "false") + ",\"prerelease\":" + (prerelease ? "true" : "false") +
               ",\"assets\":[" + string.Join(",", assets) + "]}";
    }

    static string Setup(string v) { return "DualityIME-" + v + "-setup.exe"; }

    static void UpdaterTests(string dir, JavaScriptSerializer json) {
        Check("compare versions", Updater.Compare("3.2.0", "3.1.12") > 0 && Updater.Compare("3.1.12", "3.1.2") > 0 &&
              Updater.Compare("3.0.0", "3.0.0") == 0 && Updater.Compare("3.0", "3.0.0") == 0 && Updater.Compare("x", "0.0.0") == 0 &&
              Updater.Compare("3.0.1", "3.1.0") < 0, "");

        string both = Releases(Rel("v3.1.0", false, false, Setup("3.1.0")), Rel("v3.1.5", true, false, Setup("3.1.5")));
        var r = Updater.Pick(both, false, "3.0.0");
        Check("pick stable", r != null && r.Version == "3.1.0" && r.Size == 1234 && r.Sha256 == "abcd" &&
              r.Url == "https://example.invalid/" + Setup("3.1.0"), r == null ? "null" : r.Version);
        r = Updater.Pick(both, true, "3.0.0");
        Check("pick beta", r != null && r.Version == "3.1.5", r == null ? "null" : r.Version);
        Check("pick no downgrade", Updater.Pick(both, false, "3.1.5") == null && Updater.Pick(both, true, "3.1.5") == null, "");

        string noInstaller = Releases(Rel("v3.2.0", false, false, "notes.txt"), Rel("v3.1.0", false, false, Setup("3.1.0")));
        r = Updater.Pick(noInstaller, false, "3.0.0");
        Check("pick skips releases without installer", r != null && r.Version == "3.1.0", r == null ? "null" : r.Version);

        string drafts = Releases(Rel("v3.2.0", false, true, Setup("3.2.0")), Rel("v3.1.0", false, false, Setup("3.1.0")));
        r = Updater.Pick(drafts, true, "3.0.0");
        Check("pick skips drafts", r != null && r.Version == "3.1.0", r == null ? "null" : r.Version);

        string odd = Releases(Rel("3.4.0", false, false, Setup("3.4.0")), Rel("v3.3.0-rc1", false, false, Setup("3.3.0-rc1")),
                              Rel("v3.1.12", false, false, Setup("3.1.12")), Rel("v3.1.2", false, false, Setup("3.1.2")));
        r = Updater.Pick(odd, false, "3.0.0");
        Check("pick takes only vX.Y.Z tags, highest by number", r != null && r.Version == "3.1.12", r == null ? "null" : r.Version);
        Check("pick with a wrongly named installer", Updater.Pick(Releases(Rel("v3.5.0", false, false, Setup("3.4.0"))), false, "3.0.0") == null, "");
        Check("pick of garbage", Updater.Pick("not json", false, "3.0.0") == null, "");
        // GitHub's list of 100 releases with their notes can pass the serializer's default limit (2,097,152 characters)
        string notes = new string('x', 2200000);
        string big = "[{\"tag_name\":\"v3.9.0\",\"draft\":false,\"prerelease\":false,\"body\":\"" + notes + "\",\"assets\":[" +
                     "{\"name\":\"" + Setup("3.9.0") + "\",\"size\":1234,\"digest\":\"sha256:abcd\",\"browser_download_url\":\"https://example.invalid/x\"}]}]";
        r = Updater.Pick(big, false, "3.0.0");
        Check("pick from a release list over 2 MB", big.Length > 2100000 && r != null && r.Version == "3.9.0", r == null ? "null" : r.Version);

        string lines = Releases(Rel("v3.3.0", false, false, Setup("3.3.0")), Rel("v3.3.5", true, false, Setup("3.3.5")));
        Check("newest stable", Updater.Newest(lines, false) == "3.3.0", Updater.Newest(lines, false));
        Check("newest beta", Updater.Newest(lines, true) == "3.3.5", Updater.Newest(lines, true));
        string after = Releases(Rel("v3.3.0", false, false, Setup("3.3.0")));
        Check("newest beta after release", Updater.Newest(after, true) == "3.3.0", Updater.Newest(after, true));
        Check("newest of nothing", Updater.Newest("[]", true) == "", "");
        Check("pick ignores newer beta when off", Updater.Pick(lines, false, "3.3.0") == null, "");

        string work = Path.Combine(dir, "update");
        Directory.CreateDirectory(work);
        string api = Path.Combine(work, "releases.json");
        string installed = Path.Combine(work, "version.txt");
        File.WriteAllText(installed, "3.0.0\r\n", new UTF8Encoding(false));
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_INSTALLED", installed);

        string version;
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", Path.Combine(work, "missing.json"));
        Check("update check offline", Updater.Check(false, out version) == 101 && version == "", version);
        Check("update run offline", Updater.Run(false, out version) == 101, version);

        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", api);
        File.WriteAllText(api, "<html>rate limited</html>", new UTF8Encoding(false));
        Check("update check with a reply that is not a list", Updater.Check(false, out version) == 101, version);

        File.WriteAllText(api, both, new UTF8Encoding(false));
        Check("update check finds the stable release", Updater.Check(false, out version) == 10 && version == "3.1.0", version);
        Check("update check finds the beta release", Updater.Check(true, out version) == 10 && version == "3.1.5", version);
        string st, bt;
        Updater.Check(false, out version, out st, out bt);
        Check("update check tells the stable and the beta line", st == "3.1.0" && bt == "3.1.5", st + " " + bt);
        File.WriteAllText(installed, "3.1.5\r\n", new UTF8Encoding(false));
        Check("update check when up to date", Updater.Check(true, out version) == 0, version);
        Check("update run when up to date installs nothing", Updater.Run(true, out version) == 30 && version == "", version);

        // the newest stable can be missing from the first page of the list: it comes from latest.json
        string pages = Path.Combine(work, "pages");
        string pages2 = Path.Combine(work, "pages-no-latest");
        Directory.CreateDirectory(pages);
        Directory.CreateDirectory(pages2);
        string onlyPre = Releases(Rel("v3.3.9", true, false, Setup("3.3.9")));
        File.WriteAllText(Path.Combine(pages, "releases.json"), onlyPre, new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(pages, "latest.json"), Rel("v3.3.0", false, false, Setup("3.3.0")), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(pages2, "releases.json"), onlyPre, new UTF8Encoding(false));
        File.WriteAllText(installed, "3.0.0\r\n", new UTF8Encoding(false));
        string st0, bt0;
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", pages);
        Check("stable beyond the first page", Updater.Check(false, out version, out st0, out bt0) == 10 && version == "3.3.0" &&
              st0 == "3.3.0" && bt0 == "3.3.9", version + " " + st0 + " " + bt0);
        Check("beta still sees the pre-release", Updater.Check(true, out version) == 10 && version == "3.3.9", version);
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", pages2);
        Check("no latest.json means no stable", Updater.Check(false, out version, out st0, out bt0) == 0 && st0 == "" && bt0 == "3.3.9", st0 + " " + bt0);
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", api);

        // a download that is not the announced file is refused before it runs (file: stands in for https:)
        File.WriteAllText(installed, "3.0.0\r\n", new UTF8Encoding(false));
        string fake = Path.Combine(work, Setup("3.1.0"));
        File.WriteAllText(fake, "this is not an installer", new UTF8Encoding(false));
        string fakeUrl = new Uri(fake).AbsoluteUri;
        File.WriteAllText(api, "[{\"tag_name\":\"v3.1.0\",\"draft\":false,\"prerelease\":false,\"assets\":[{\"name\":\"" + Setup("3.1.0") +
                          "\",\"size\":5,\"browser_download_url\":\"" + fakeUrl + "\"}]}]", new UTF8Encoding(false));
        Check("update run refuses a download of the wrong size", Updater.Run(false, out version) == 201, version);
        File.WriteAllText(api, "[{\"tag_name\":\"v3.1.0\",\"draft\":false,\"prerelease\":false,\"assets\":[{\"name\":\"" + Setup("3.1.0") +
                          "\",\"size\":24,\"digest\":\"sha256:00\",\"browser_download_url\":\"" + fakeUrl + "\"}]}]", new UTF8Encoding(false));
        Check("update run refuses a download with the wrong hash", Updater.Run(false, out version) == 201, version);
        File.WriteAllText(api, "[{\"tag_name\":\"v3.1.0\",\"draft\":false,\"prerelease\":false,\"assets\":[{\"name\":\"" + Setup("3.1.0") +
                          "\",\"size\":24,\"browser_download_url\":\"" + new Uri(Path.Combine(work, "gone.exe")).AbsoluteUri + "\"}]}]", new UTF8Encoding(false));
        Check("update run when the download fails", Updater.Run(false, out version) == 201, version);

        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_API", null);
        Environment.SetEnvironmentVariable("DUALITYIME_UPDATE_TEST_INSTALLED", null);
    }
}
