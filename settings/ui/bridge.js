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
    autocorrect: false, punctuation: "half", space: "half", full_width: false, ctrl_punct: false,
    preview: "keys_hint", shift_toggle: true, start_mode: "mixed", completion: true, remember_words: true,
    forget_words: 0, forget_emails: 0, lookup: true, lookup_size: "small", game_mode: false,
    cand_font: "Microsoft JhengHei UI", cand_font_size: 18, cand_per_row: 1
  };
  const dict = { words: [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"]], shortcuts: [["addr", "台北市信義區…"]] };
  const sys = { ok: true, default_ime: true, language_hk: false };
  const wait = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));
  const demo = new URLSearchParams(location.search).get("update") || "";
  return async (op, args = {}) => {
    switch (op) {
      case "state": return { config, version: "2.1.0", dataDir: "C:\\Users\\you\\AppData\\Roaming\\DualityIME",
        remembered: 12, rememberedEmails: 2, words: dict.words.length, shortcuts: dict.shortcuts.length, beta: false, canUpdate: true, hasPime: false, dark: false };
      case "setConfig": config[args.key] = args.value; return config;
      case "forgetWords": return 0;
      case "system": return wait(400, sys);
      case "setSystem": sys[args.action] = args.on; return wait(300, sys);
      case "dictionary": return dict;
      case "saveDictionary": dict[args.kind] = args.rows; return args.rows.length;
      case "hotkeys": return { language: "Ctrl+Shift", layout: "Left Alt+Shift" };
      case "readings": return args.words.map((w) => (w === "台灣" ? "ㄊㄞˊ ㄨㄢ" : ""));
      case "exportBackup": return wait(200, true);
      case "openBackup": return wait(200, { created: "2026-10-27 10:30", memory: 128, words: 3, shortcuts: 2, config: true });
      case "restoreBackup": return wait(300, { words: 1, shortcuts: 0 });
      case "checkUpdate": return wait(900, { code: demo === "available" || demo === "fail" ? 10 : 0, version: "2.2.0", stable: "2.2.0", beta: "2.3.0" });
      case "runUpdate": return wait(1500, { code: demo === "fail" ? 301 : 0, version: "2.2.0" });
      default: return wait(100, true);
    }
  };
})();
