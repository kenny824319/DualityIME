// Talking to settings.exe: api("op", {args}) -> Promise of the result (see Settings.cs, Backend.Handle).
// Opened in a plain browser (to look at the page while working on it), a stand-in answers instead.
"use strict";

const api = (() => {
  const webview = window.chrome && window.chrome.webview;
  if (webview) {
    let next = 1;
    const waiting = new Map();
    webview.addEventListener("message", (e) => {
      const m = e.data;
      const w = waiting.get(m.id);
      if (!w) return;
      waiting.delete(m.id);
      if (m.ok) w.resolve(m.result); else w.reject(new Error(m.error || "error"));
    });
    return (op, args) => new Promise((resolve, reject) => {
      const id = next++;
      waiting.set(id, { resolve, reject });
      webview.postMessage({ id, op, args: args || {} });
    });
  }

  // ---- stand-in (browser preview only) ----
  const config = {
    autocorrect: false, long_sentence: true, lm_context: true, lm_model: "light", lm_context_len_plus: 64, lm_context_len_max: 64, punctuation: "half", space: "half", full_width: false, ctrl_full_punct: true, symbols_stay_open: false,
    preview: "keys_hint", shift_toggle: true, start_mode: "mixed", completion: true, remember_words: true,
    forget_words: 0, forget_emails: 0, forget_list: [], lookup: true, lookup_size: "small", game_keys: true,
    cand_font: "Microsoft JhengHei UI", cand_font_size: 18, cand_per_row: 1,
    cand_layout: "", cand_keys: "123456789", cand_columns: 0, appearance: {},
    simplified: false, simplified_hotkey: false, simplified_key: "Ctrl+F1"
  };
  const dict = { words: [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"]], shortcuts: [["addr", "台北市信義區…"]] };
  const images = {};  // 自訂外觀's photos: file -> data: URL
  // (the samples a new install starts with, #33: Settings.cs QuickFillSamples)
  let quickFill = [["sample@example.com", "email"], ["王小明", "name1"], ["John", "name2"], ["Xiaoming Wang", "name3"],
    ["0912345678", "phone"], ["A123456789", "id"], ["西元_年_月_日", "birth"], ["_市_區_路_巷_號_樓", "addr"],
    ["感謝您的幫忙", "tks"], ["(*'∀')~♥", "em1"], ["( ˙ 3 ˙ )", "em2"], ["(\"∀\")", "em3"], ["∑( ° Δ °)", "em4"]];
  // 記憶清單 (#65): rows as settings.exe's memory op gives them (Settings.cs MemoryList), shown as stored (注音, 大陸用詞
  // in 繁體); 已記憶項目 counts them, a 刪除 or 全部清除 takes them away. (A run of made-up English words past the 300
  // drawn at once, so the cut line shows.)
  const day = (m, d) => Math.floor(Date.UTC(2026, m - 1, d) / 86400000);
  const mem = (kind, key, value, shown, reading, count, used, note) =>
    ({ kind, key, value, shown, reading, count, day: used ? day(...used) : null, note });
  const memory = {
    picks: [mem("pick", "ㄊㄚ", "她", "她", "ㄊㄚ", 5, [11, 24], ""), mem("pick", "ㄒㄧㄝˇ", "寫", "寫", "ㄒㄧㄝˇ", 1, [11, 24], ""),
      mem("pick", "ㄋㄚˋ", "那", "那", "ㄋㄚˋ", 4, [11, 23], ""), mem("pick", "ㄋㄧˇ", "妳", "妳", "ㄋㄧˇ", 3, [11, 22], ""),
      mem("pick", "ㄌㄧㄠˇ-ㄐㄧㄝˇ", "瞭解", "瞭解", "ㄌㄧㄠˇ ㄐㄧㄝˇ", 3, [11, 21], ""),
      mem("pick", "ㄧㄠˋ-ㄕ˙", "鑰匙", "鑰匙", "ㄧㄠˋ ㄕ˙", 2, [11, 20], ""), mem("pick", "ㄕˋ-ㄕˊ", "適時", "適時", "ㄕˋ ㄕˊ", 1, [11, 18], ""),
      mem("pick", "ㄓㄜˊ", "哲", "哲", "ㄓㄜˊ", 2, [11, 15], "")],
    words: [mem("word", "accelerate", "", "accelerate", "", 3, [11, 24], ""), mem("word", "refactor", "", "refactor", "", 5, [11, 23], ""),
      mem("word", "Kubernetes", "", "Kubernetes", "", 2, [11, 19], ""), mem("word", "recieve", "", "recieve", "", 2, [11, 10], ""),
      ...Array.from({ length: 340 }, (_, i) => {
        const w = "sample" + String(i + 1).padStart(3, "0");
        return mem("word", w, "", w, "", 2, [10, 1 + (i % 28)], "");
      })],
    emails: [mem("email", "sample@example.com", "", "sample@example.com", "", 4, [11, 24], ""),
      mem("email", "work@example.org", "", "work@example.org", "", 2, [11, 20], ""),
      mem("email", "smaple@example.com", "", "smaple@example.com", "", 1, [11, 2], "")],
    keys: [mem("correction", "si3", "ㄋㄧˇ", "si3", "ㄋㄧˇ", 4, [11, 24], "打錯時照這個改（注音自動校正）"),
      mem("correction", "817", "", "817", "", 1, [11, 24], "照打，不校正"),
      mem("correction", "CP→56", "ㄓˊ", "CP 56", "ㄓˊ", 3, [11, 22], "英文後面的 56 讀成這個音"),
      mem("correction", "xi3", "ㄌㄧˇ", "xi3", "ㄌㄧˇ", 2, [11, 21], "打錯時照這個改（注音自動校正）"),
      mem("english", "mp3", "", "mp3", "", 2, [11, 19], "照打成英文"), mem("english", "gpt4", "", "gpt4", "", null, null, "照打成英文")],
    simplified: [mem("simp_pick", "ㄓㄜˋ", "這", "這", "ㄓㄜˋ", 6, [11, 24], "簡體選字"),
      mem("simp_term", "軟體", "軟件", "軟體", "軟件", 3, [11, 23], "大陸用詞")]
  };
  const remembered = () => ({ remembered: memory.picks.length + memory.words.length + memory.keys.length,
    rememberedEmails: memory.emails.length, rememberedSimplified: memory.simplified ? memory.simplified.length : null });
  const sys = { ok: true, default_ime: true, language_hk: false };
  const wait = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));
  const demo = new URLSearchParams(location.search).get("update") || "";
  return async (op, args = {}) => {
    switch (op) {
      case "state": return { config, version: "2.1.0", dataDir: "C:\\Users\\you\\AppData\\Roaming\\DualityIME",
        ...remembered(), words: dict.words.length, shortcuts: dict.shortcuts.length, quickFill: quickFill.length, beta: false, canUpdate: true, hasPime: false, dark: false };
      case "setConfig": config[args.key] = args.value; return config;
      case "forgetWords":  // (全部清除: the typing memory, the e-mails when ticked)
        for (const k of ["picks", "words", "keys"]) memory[k] = [];
        if (memory.simplified) memory.simplified = [];
        if (args.emails) memory.emails = [];
        return 0;
      case "system": return wait(400, sys);
      case "setSystem": sys[args.action] = args.on; return wait(300, sys);
      case "dictionary": return dict;
      case "saveDictionary": dict[args.kind] = args.rows; return args.rows.length;
      case "quickFill": return quickFill.map((r) => [...r]);
      case "memory": return wait(150, JSON.parse(JSON.stringify(memory)));
      case "forgetMemory": {
        const gone = new Set(args.items.map((r) => r.join("\u0000")));
        for (const k of Object.keys(memory))
          if (memory[k]) memory[k] = memory[k].filter((r) => !gone.has([r.kind, r.key, r.value].join("\u0000")));
        return wait(200, args.items.length);
      }
      case "pickImage": {  // (a made-up photo: a sunset of two gradients)
        const c = document.createElement("canvas");
        c.width = 600; c.height = 400;
        const g = c.getContext("2d"), sky = g.createLinearGradient(0, 0, 0, 400);
        sky.addColorStop(0, "#7ec8f2"); sky.addColorStop(0.5, "#ffb36b"); sky.addColorStop(1, "#8a5a9e");
        g.fillStyle = sky; g.fillRect(0, 0, 600, 400);
        g.fillStyle = "#fff1b8"; g.beginPath(); g.arc(420, 120, 40, 0, 7); g.fill();
        g.fillStyle = "#3b2f4f"; g.fillRect(0, 300, 600, 100);
        const file = args.slot + ".png";
        images[file] = c.toDataURL();
        return { file, url: images[file], name: "夕陽.png" };
      }
      case "imageUrl": return images[args.file] || null;
      case "removeImage": delete images[args.slot + ".png"]; return true;
      case "saveQuickFill": quickFill = args.rows; return args.rows.length;
      case "hotkeys": return { language: "Ctrl+Shift", layout: "Left Alt+Shift" };
      case "readings": return args.words.map((w) => (w === "台灣" ? "ㄊㄞˊ ㄨㄢ" : ""));
      case "exportBackup": return wait(200, true);
      case "openBackup": return wait(200, { created: "2026-10-27 10:30", memory: 128, words: 3, shortcuts: 2, quickFill: 4, config: true });
      case "restoreBackup": return wait(300, { words: 1, shortcuts: 0 });
      case "checkUpdate": return wait(900, { code: demo === "available" || demo === "fail" ? 10 : 0, version: "2.2.0", stable: "2.2.0", beta: "2.3.0" });
      case "runUpdate": return wait(1500, { code: demo === "fail" ? 301 : 0, version: "2.2.0" });
      default: return wait(100, true);
    }
  };
})();
