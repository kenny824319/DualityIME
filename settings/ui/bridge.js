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
    autocorrect: false, long_sentence: true, punctuation: "half", space: "half", full_width: false, ctrl_full_punct: true,
    preview: "keys_hint", shift_toggle: true, start_mode: "mixed", completion: true, remember_words: true,
    forget_words: 0, forget_emails: 0, lookup: true, lookup_size: "small", game_keys: true,
    cand_font: "Microsoft JhengHei UI", cand_font_size: 18, cand_per_row: 1,
    cand_layout: "", cand_keys: "123456789", cand_columns: 0, appearance: {}
  };
  const dict = { words: [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"]], shortcuts: [["addr", "台北市信義區…"]] };
  const images = {};  // 自訂外觀's photos: file -> data: URL
  let quickFill = [["sample@example.com", "email"], ["王小明", "name1"], ["0912345678", "phone"], ["西元_年_月_日", "birth"]];
  const sys = { ok: true, default_ime: true, language_hk: false };
  const wait = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));
  const demo = new URLSearchParams(location.search).get("update") || "";
  return async (op, args = {}) => {
    switch (op) {
      case "state": return { config, version: "2.1.0", dataDir: "C:\\Users\\you\\AppData\\Roaming\\DualityIME",
        remembered: 12, rememberedEmails: 2, words: dict.words.length, shortcuts: dict.shortcuts.length, quickFill: quickFill.length, beta: false, canUpdate: true, hasPime: false, dark: false };
      case "setConfig": config[args.key] = args.value; return config;
      case "forgetWords": return 0;
      case "system": return wait(400, sys);
      case "setSystem": sys[args.action] = args.on; return wait(300, sys);
      case "dictionary": return dict;
      case "saveDictionary": dict[args.kind] = args.rows; return args.rows.length;
      case "quickFill": return quickFill.map((r) => [...r]);
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
