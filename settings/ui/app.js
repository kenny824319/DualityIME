// 兩儀輸入法 設定工具的畫面。設定一改就存（settings.exe 寫進 config.json），輸入法下一個按鍵就套用。
"use strict";

// ---- small helpers --------------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c);
  return el;
}

const ICONS = {
  general: '<path d="M3 5h7M13 5h2M3 9h2M8 9h7M3 13h9"/><circle cx="11.5" cy="5" r="1.5"/><circle cx="6.5" cy="9" r="1.5"/><circle cx="13.5" cy="13" r="1.5"/>',
  typing: '<rect x="2" y="4.5" width="14" height="9" rx="1.5"/><path d="M5 7.5h1M8.5 7.5h1M12 7.5h1M6 10.5h6"/>',
  complete: '<path d="M9 2.5a4.5 4.5 0 0 1 2.5 8.2V13h-5v-2.3A4.5 4.5 0 0 1 9 2.5zM7 15.5h4"/>',
  dict: '<rect x="4" y="2.5" width="10" height="13" rx="1.5"/><path d="M6.5 6h5M6.5 9h5"/>',
  look: '<circle cx="9" cy="9" r="6.5"/><path d="M9 2.5v13M2.5 9h13"/>',
  help: '<path d="M9 5c-1.6-1.2-3.8-1.6-6.5-1.3v10c2.7-.3 4.9.1 6.5 1.3 1.6-1.2 3.8-1.6 6.5-1.3v-10C12.8 3.4 10.6 3.8 9 5zM9 5v10"/>',
  faq: '<path d="M2.5 4.5a1.5 1.5 0 0 1 1.5-1.5h6a1.5 1.5 0 0 1 1.5 1.5v3.5A1.5 1.5 0 0 1 10 9.5H6.5L4 11.5v-2A1.5 1.5 0 0 1 2.5 8z"/><path d="M13 6.5h1a1.5 1.5 0 0 1 1.5 1.5v3.5A1.5 1.5 0 0 1 14 13v2l-2.5-2H8a1.5 1.5 0 0 1-1.5-1.5v-.5"/>',
  about: '<circle cx="9" cy="9" r="6.5"/><path d="M9 8v4.5M9 5.6v.2"/>'
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 18 18");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name];
  return s;
}

let toastTimer = null;
function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

let S = null;          // what settings.exe told us (config, counts, version...)
let SYS = null;        // Windows' side: {default_ime, language_hk}, once known
const BUSY = new Set();

async function setConfig(key, value) {
  try {
    S.config = await api("setConfig", { key, value });
    toast("設定已儲存並自動套用");
  } catch {
    toast("儲存失敗，請再試一次");
  }
}

// ---- controls ---------------------------------------------------------------------------------
function toggle(on, change, opts = {}) {
  const input = h("input", { type: "checkbox", role: "switch", "aria-label": opts.label || "" });
  input.checked = !!on;
  input.disabled = !!opts.disabled;
  const state = h("span", { class: "state" }, on ? "開啟" : "關閉");
  input.addEventListener("change", () => {
    state.textContent = input.checked ? "開啟" : "關閉";
    change(input.checked, input);
  });
  return h("label", { class: "toggle" }, state, input, h("span", { class: "track" }, h("span", { class: "knob" })));
}

function segments(options, value, change, label) {
  // options: [[value, text], ...]
  const slider = h("span", { class: "slider", "aria-hidden": "true" });
  const box = h("div", { class: "segments", role: "radiogroup", "aria-label": label || "" }, slider);
  const buttons = options.map(([v, text]) => {
    const b = h("button", { role: "radio", type: "button" }, text);
    b.addEventListener("click", () => { select(v); change(v); });
    box.append(b);
    return [v, b];
  });
  function select(v) {
    for (const [bv, b] of buttons) {
      const on = String(bv) === String(v);
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", on);
      if (on) requestAnimationFrame(() => {
        slider.style.width = `${b.offsetWidth}px`;
        slider.style.transform = `translateX(${b.offsetLeft - 3}px)`;
      });
    }
  }
  select(value);
  return box;
}

function row(title, desc, ...controls) {
  return h("div", { class: "row" },
    h("div", { class: "row-text" }, h("div", { class: "row-title" }, title), desc ? h("div", { class: "row-desc" }, desc) : null),
    controls.length ? h("div", { class: "row-control" }, ...controls) : null);
}
const card = (...rows) => h("div", { class: "card" }, ...rows);
const cfgToggle = (key, label) => toggle(S.config[key], (v) => setConfig(key, v), { label });
const cfgSegments = (key, options, label) => segments(options, S.config[key], (v) => setConfig(key, v), label);

function systemToggle(action, label) {
  // Windows' own settings: known after a moment (PowerShell), changed the same way
  const t = toggle(SYS ? SYS[action] : false, async (v, input) => {
    input.disabled = true;
    try {
      SYS = await api("setSystem", { action, on: v });
      if (!SYS.ok || SYS[action] !== v) throw new Error();
      toast(v ? "已開啟" : "已關閉");
    } catch {
      toast("設定失敗，請再試一次");
    }
    input.checked = SYS ? SYS[action] : false;
    input.dispatchEvent(new Event("sync"));
    input.disabled = false;
  }, { label, disabled: !SYS });
  const input = t.querySelector("input");
  input.addEventListener("sync", () => { t.querySelector(".state").textContent = input.checked ? "開啟" : "關閉"; });
  t.dataset.system = action;
  return t;
}

// ---- dialogs ----------------------------------------------------------------------------------
function dialog(title, body, buttons) {
  // buttons: [{text, kind, click(close) }]; Esc or a click outside closes it
  return new Promise((resolve) => {
    const close = (v) => { overlay.remove(); document.removeEventListener("keydown", esc); resolve(v); };
    const esc = (e) => { if (e.key === "Escape") close(null); };
    const foot = h("div", { class: "dialog-foot" }, h("span", { class: "spacer" }));
    const overlay = h("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(null); } },
      h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": title },
        h("div", { class: "dialog-head" }, h("h2", { class: "dialog-title" }, title)),
        h("div", { class: "dialog-body" }, body), foot));
    for (const b of buttons) {
      const el = h("button", { class: "btn " + (b.kind || "") }, b.text);
      el.addEventListener("click", () => (b.click ? b.click(close, el) : close(b.value)));
      if (b.left) foot.insertBefore(el, foot.firstChild); else foot.append(el);
    }
    document.addEventListener("keydown", esc);
    document.body.append(overlay);
    (overlay.querySelector("input") || foot.lastChild).focus();
  });
}

// ---- 個人詞庫 editor --------------------------------------------------------------------------
const CJK = /^[㐀-鿿豈-﫿]+$/;
const ENGLISH = /^[A-Za-z][A-Za-z'-]*[A-Za-z]$/;   // an English word (Kazuma, e-sports): no 注音 needed
const ZHUYIN = /^[ㄅ-ㄯˊˇˋ˙]*[ㄅ-ㄯ][ㄅ-ㄯˊˇˋ˙]*$/;  // 注音文 (ㄅㄨㄚ): no 注音 either
function syllables(reading) {
  let n = 0;
  for (const part of reading.replace(/[-ˉ]/g, " ").split(/\s+/).filter(Boolean)) {
    let cur = 0;
    for (const ch of part) { cur++; if ("ˊˇˋ˙".includes(ch)) { n++; cur = 0; } }
    if (cur) n++;
  }
  return n;
}
function checkRow(kind, a, b) {
  if (kind === "words") {
    if (ENGLISH.test(a)) return a.length > 32 ? `「${a}」：英文詞彙最多 32 個字元` : "";
    if (ZHUYIN.test(a)) return [...a].length > 8 ? `「${a}」：注音文最多 8 個符號` : "";
    if (!CJK.test(a)) return `「${a}」：僅支援中文詞彙、英文字母或注音文（英文可包含 - 與 '）`;
    if ([...a].length > 8) return `「${a}」：中文詞彙最多 8 個字`;
    if (!/^[ㄅ-ㄩˊˇˋ˙ˉ\s-]+$/.test(b)) return `「${a}」：注音欄僅能輸入注音符號與聲調`;
    if (syllables(b) !== [...a].length) return `「${a}」：注音數量須與中文字數相同（共 ${[...a].length} 個字）`;
  } else {
    // a shortcut is the keys typed (kfc), or Chinese as shown (肯德基): not both
    if (!/^[A-Za-z0-9]+$/.test(a) && !CJK.test(a)) return `「${a}」：捷徑代碼請用英文字母與數字，或全部用中文`;
    if ([...a].length > 8) return `「${a}」：捷徑最多 8 個字元`;
    if (!b) return `「${a}」：請輸入展開內容`;
    if (b.length > 255) return `「${a}」：展開內容最多 255 個字`;
  }
  return "";
}

// 映射詞彙: a small text box typing a shortcut, ↓, and what it stands for; then the other way round
// (each entry works both ways when one side is Chinese), over and over
const MAP_FRAMES = [
  { key: "k", comp: "k" }, { key: "f", comp: "kf" }, { key: "c", comp: "kfc" },
  { key: "↓", comp: "kfc", cands: ["肯德基"] }, { key: "Enter", sent: "肯德基" }, { sent: "肯德基" },
  { key: "ㄎㄣˇ", comp: "肯" }, { key: "ㄉㄜˊ", comp: "肯德" }, { key: "ㄐㄧ", comp: "肯德基" },
  { key: "↓", comp: "肯德基", cands: ["KFC", "機", "基"] }, { key: "Enter", sent: "KFC" }, { sent: "KFC" }
];

function mapPreview() {
  const stage = h("div", { class: "stage map-stage", "aria-hidden": "true" });
  const keycap = h("div", { class: "keycap small" });
  let i = 0;
  function draw() {
    const f = MAP_FRAMES[i];
    stage.replaceChildren(document.createTextNode(f.sent || ""), h("span", { class: "comp" }, f.comp || ""), h("span", { class: "caret" }));
    if (f.cands) stage.append(h("div", { class: "cands" }, h("span", { class: "label" }, "候選字"),
      ...f.cands.map((c, n) => h("span", { class: "c" + (n === 0 ? " sel" : "") }, `${n + 1}　${c}`))));
    keycap.textContent = f.key || "　";
  }
  draw();
  const timer = setInterval(() => {
    if (!stage.isConnected) { clearInterval(timer); return; }  // (the dialog was closed)
    i = (i + 1) % MAP_FRAMES.length;
    draw();
  }, 700);
  return h("div", { class: "map-preview" }, stage, keycap);
}

async function editList(kind) {
  const words = kind === "words";
  const d = await api("dictionary");
  const tbody = h("tbody");
  const error = h("div", { class: "error-line", role: "alert" });
  function addRow(a = "", b = "") {
    const ia = h("input", { value: a, "aria-label": words ? "詞彙" : "捷徑代碼", maxlength: words ? 32 : 8 });
    const ib = h("input", { value: b, "aria-label": words ? "注音" : "展開內容" });
    if (words) {
      // an English word, or 注音文, has no 注音
      const sync = () => {
        const en = ENGLISH.test(ia.value.trim()), zh = ZHUYIN.test(ia.value.trim());
        ib.disabled = en || zh;
        ib.placeholder = en ? "英文無須注音" : zh ? "注音文無須注音" : "點擊「自動填入注音」即可自動帶入（破音字請手動修正）";
        if (en || zh) ib.value = "";
      };
      ia.addEventListener("input", sync);
      sync();
    }
    const tr = h("tr", {}, h("td", { style: `width:${words ? 38 : 26}%` }, ia), h("td", {}, ib),
      h("td", { style: "width:36px" }, h("button", { class: "icon-btn", "aria-label": "刪除這一行", onclick: () => tr.remove() }, "✕")));
    tbody.append(tr);
    return ia;
  }
  for (const [a, b] of d[kind]) addRow(a, b);
  const rows = () => [...tbody.rows].map((r) => [r.cells[0].firstChild.value.trim(), r.cells[1].firstChild.value.trim()])
    .filter(([a, b]) => a || b);
  const body = h("div", {},
    h("p", { class: "row-desc" }, words ? "輸入法會優先選用此清單中的詞彙。" : "可自定義詞彙映射關係（例：KFC → 肯德基）。中文那一邊打出來後按 ↓，也會出現另一邊（肯德基 → KFC）。"),
    words ? null : mapPreview(),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, words ? "詞彙" : "捷徑代碼"), h("th", {}, words ? "注音" : "展開內容"), h("th"))), tbody),
    h("button", { class: "btn", style: "margin-top:8px", onclick: () => addRow().focus() }, "＋ 新增一筆"),
    error);
  const buttons = [
    { text: "取消", value: false },
    { text: "儲存", kind: "primary", click: async (close) => {
      const list = rows(), seenA = new Set(), seenB = new Set();
      for (const [a, b] of list) {
        let err = checkRow(kind, a, b);
        if (!err && seenA.has(a.toLowerCase())) err = words ? `「${a}」：此詞彙已存在` : `捷徑「${a}」已被使用`;
        if (!err && !words && seenB.has(b)) err = `內容「${b}」已設定過其他捷徑`;
        if (err) { error.textContent = err; return; }
        seenA.add(a.toLowerCase()); seenB.add(b);
      }
      try { await api("saveDictionary", { kind, rows: list }); close(true); }
      catch { error.textContent = "儲存失敗，請再試一次"; }
    } }
  ];
  if (words) buttons.splice(0, 0, { text: "自動填入注音", left: true, click: async () => {
    const empty = [...tbody.rows].filter((r) => CJK.test(r.cells[0].firstChild.value.trim()) && !r.cells[1].firstChild.value.trim());
    const got = await api("readings", { words: empty.map((r) => r.cells[0].firstChild.value.trim()) });
    empty.forEach((r, i) => { if (got[i]) r.cells[1].firstChild.value = got[i]; });
  } });
  const saved = await dialog(words ? "自訂詞彙" : "映射詞彙", body, buttons);
  if (saved) {
    toast("已儲存，將於下次按鍵時套用");
    S = await api("state");
    show("dict");
  }
}

// ---- pages --------------------------------------------------------------------------------------
const PAGES = [
  ["general", "一般"], ["typing", "打字"], ["complete", "補字與記憶"], ["dict", "個人詞庫"], ["look", "外觀"], ["help", "使用說明"], ["faq", "常見問題"], ["about", "關於"]
];

function pageGeneral() {
  return [
    h("h1", {}, "一般"),
    card(
      row("設為預設輸入法", "", systemToggle("default_ime", "設為預設輸入法")),
      row("預設輸入模式", "開啟新程式或切換至本輸入法時的初始模式",
        cfgSegments("start_mode", [["mixed", "中英混打"], ["english", "純英文"]], "預設輸入模式"))),
    h("h2", {}, "切換設定"),
    card(
      row("Shift 切換輸入模式", "按一下 Shift 切換至純英文模式，再按一下切回中英混打模式", cfgToggle("shift_toggle", "Shift 切換輸入模式")),
      hotkeyRow(),
      row("遊戲模式", "執行遊戲時將自動切換為全英文輸入模式，按 Shift 即可換回中英混打模式",
        cfgToggle("game_mode", "遊戲模式")))
  ];
}

// From 使用說明 / 常見問題 to the setting itself: that page, the row lit up with a 「在這裡」 tag
function goTo(page, title) {
  show(page);
  const t = [...document.querySelectorAll(".row-title")].find((e) => e.textContent === title);
  const r = t && t.closest(".row");
  if (!r) return;
  r.scrollIntoView({ block: "center" });
  r.classList.add("spotlight");
  t.append(h("span", { class: "here" }, "此處"));
  setTimeout(() => { r.classList.remove("spotlight"); const tag = t.querySelector(".here"); if (tag) tag.remove(); }, 3200);
}

const goButton = ([page, title]) =>
  ({ text: "前往設定", left: true, click: (c) => { c(true); goTo(page, title); } });

// 功能總覽's dialog: a paragraph is its bold lead and the text after it, on one line if they fit,
// else the lead on a line of its own above the text
function topicBody(paragraphs) {
  const body = h("div", { class: "topic-body" }, ...paragraphs.map(([lead, text]) =>
    h("p", {}, h("strong", {}, lead, h("span", { class: "colon" }, "：")), h("span", { class: "text" }, text))));
  requestAnimationFrame(() => {
    for (const p of body.children) {
      const line = parseFloat(getComputedStyle(p).lineHeight);
      if (p.getBoundingClientRect().height > line * 1.5) p.classList.add("stacked");
    }
  });
  return body;
}

function pageTyping() {
  // 標點符號字元寬度: one choice for the punctuation marks and the space bar
  const width = segments([["half", "半形"], ["full", "全形"]], S.config.punctuation, async (v) => {
    try {
      await api("setConfig", { key: "punctuation", value: v });
      S.config = await api("setConfig", { key: "space", value: v });
      toast("設定已儲存並自動套用");
    } catch {
      toast("儲存失敗，請再試一次");
    }
  }, "標點符號字元寬度");
  return [
    h("h1", {}, "打字"),
    card(
      row("標點符號字元寬度", "", width),
      row("全形英數模式", "英數、標點與空白皆輸出為全形（如 ＡＢＣ１２３）。", cfgToggle("full_width", "全形英數模式")),
      row("Ctrl + 標點打全形", "按住 Ctrl 再按標點鍵即輸出全形標點。開啟後，程式中相同組合的快捷鍵在中英混打時會失效", cfgToggle("ctrl_punct", "Ctrl + 標點打全形")),
      row("注音自動校正",
        h("span", {}, "按到旁邊的鍵、注音順序打錯或同一個鍵按兩次時，自動改成最可能的字。可透過 ↓ 選回原本打的。",
          h("span", { class: "row-warn" }, "注意：偶爾會把打對的字或數字也改掉。")),
        cfgToggle("autocorrect", "注音自動校正")),
      previewChoice())
  ];
}

// 打字過程顯示: what an unfinished syllable shows. Two tiles, each a small text box showing it
// (like Windows' theme picker); pointing at one types 你好 (su3 cl3) in it.
const PREVIEW_KEYS = ["s", "u", "3", "c", "l", "3"];
const PREVIEW_SHOWN = {   // [sent part, unfinished part, zhuyin under it] after each key
  keys_hint: [["", "s", "ㄋ"], ["", "su", "ㄋㄧ"], ["你", ""], ["你", "c", "ㄏ"], ["你", "cl", "ㄏㄠ"], ["你好", ""]],
  keys: [["", "s"], ["", "su"], ["你", ""], ["你", "c"], ["你", "cl"], ["你好", ""]],
  zhuyin: [["", "ㄋ"], ["", "ㄋㄧ"], ["你", ""], ["你", "ㄏ"], ["你", "ㄏㄠ"], ["你好", ""]]
};
const PREVIEW_STILL = 4;   // shown when not pointed at: 你 + cl (+ ㄏㄠ under it) / 你 + ㄏㄠ
const PREVIEW_CHOICES = [["keys_hint", "英文字母＋注音提示"], ["keys", "英文字母"], ["zhuyin", "注音符號"]];

function previewChoice() {
  const tiles = [];
  function pick(v) {
    for (const t of tiles) {
      const on = t.dataset.value === v;
      t.classList.toggle("on", on);
      t.setAttribute("aria-checked", on);
    }
    if (S.config.preview !== v) setConfig("preview", v);
  }
  for (const [v, label] of PREVIEW_CHOICES) {
    const box = h("div", { class: "choice-box" + (v === "keys_hint" ? " with-tip" : ""), "aria-hidden": "true" });
    const key = h("span", { class: "choice-key", "aria-hidden": "true" });
    const draw = (i, k) => {
      const [done, open, tip] = PREVIEW_SHOWN[v][i];
      const comp = h("span", { class: "comp" }, open);
      if (tip) comp.append(h("span", { class: "zhuyin-tip" }, tip));
      box.replaceChildren(document.createTextNode(done), comp, h("span", { class: "caret" }));
      key.textContent = k || "";
      key.classList.toggle("show", !!k);
    };
    let timer = null, frame = 0;
    const play = () => {
      if (timer) return;
      frame = 0;
      draw(0, PREVIEW_KEYS[0]);
      timer = setInterval(() => {
        frame = frame >= PREVIEW_KEYS.length + 2 ? 0 : frame + 1;
        draw(Math.min(frame, PREVIEW_KEYS.length - 1), PREVIEW_KEYS[frame]);
      }, 520);
    };
    const stop = () => { clearInterval(timer); timer = null; draw(PREVIEW_STILL); };
    const tile = h("button", { class: "choice", type: "button", role: "radio", "data-value": v,
      onclick: () => pick(v), onmouseenter: play, onmouseleave: stop, onfocus: play, onblur: stop },
      h("div", { class: "choice-preview" }, box, key),
      h("div", { class: "choice-label" }, h("span", { class: "radio", "aria-hidden": "true" }),
        h("span", { class: "row-text" }, h("span", { class: "row-title" }, label))));
    draw(PREVIEW_STILL);
    tiles.push(tile);
  }
  pick(PREVIEW_CHOICES.some(([v]) => v === S.config.preview) ? S.config.preview : "keys_hint");
  return h("div", { class: "row column" },
    h("div", { class: "row-text" },
      h("div", { class: "row-title" }, "打字過程顯示"),
      h("div", { class: "row-desc" }, "尚未輸入完整時，顯示的樣式")),
    h("div", { class: "choices", role: "radiogroup", "aria-label": "打字過程顯示" }, ...tiles));
}

function hotkeyRow() {
  // Windows' 輸入語言快速鍵: changed in Windows' own window (the IME never changes them)
  return row("系統切換快速鍵設定", "", h("button", { class: "btn", onclick: () => api("openHotkeys") }, "前往 Windows 設定"));
}

function pageComplete() {
  return [
    h("h1", {}, "補字與記憶"),
    card(
      row("Tab 智慧補字", "輸入時於游標後方提示最可能的下一個字", cfgToggle("completion", "Tab 智慧補字")),
      row("自動記憶常用英文與 Email", "自動記憶輸入滿 2 次的英文單字，以及輸入過 1 次的 Email。", cfgToggle("remember_words", "自動記憶常用英文與 Email")),
      row("已記憶項目", `目前已記住 ${S.remembered} 筆打字習慣、${S.rememberedEmails} 個 Email`,
        h("button", { class: "btn", onclick: forgetMemory }, "全部清除")))
  ];
}

async function forgetMemory() {
  // 全部清除: the engine clears the typing memory; the e-mails only when ticked
  const emails = h("input", { type: "checkbox" });
  const body = h("div", {},
    h("p", { class: "row-desc", style: "font-size:14px" },
      "確定要清除所有打字記憶（選字習慣、記住的英文與自動校正的習慣）嗎？設定與個人詞庫會保留。此操作無法復原。"),
    h("label", { class: "check" }, emails, "一併清除記住的 Email"));
  const ok = await dialog("清除所有記憶項目", body, [{ text: "取消", value: false }, { text: "確定清除", kind: "danger", value: true }]);
  if (!ok) return;
  await api("forgetWords", { emails: emails.checked });
  toast("已清除");
  // the counts of 已記憶項目 again: the settings tool counts what the engine clears at its next key as gone
  S = await api("state");
  if (current === "complete") show("complete");
}

function pageDict() {
  return [
    h("h1", {}, "個人詞庫"),
    card(
      row("自訂詞彙", `打字時優先選用設定的詞彙。目前已設定 ${S.words} 筆`, h("button", { class: "btn", onclick: () => editList("words") }, "編輯")),
      row("映射詞彙", `可自定義詞彙映射關係。目前已設定 ${S.shortcuts} 筆`, h("button", { class: "btn", onclick: () => editList("shortcuts") }, "編輯"))),
    h("h2", {}, "框選查詢"),
    card(
      row("連按 Ctrl 框選查詢", "選取文字後連按兩次 Ctrl，即可查看注音、語音朗讀，或快速加入「自訂詞彙」與「映射詞彙」", cfgToggle("lookup", "連按 Ctrl 框選查詢")),
      row("查詢視窗大小", "", cfgSegments("lookup_size", [["small", "小"], ["medium", "中"], ["large", "大"]], "查詢視窗大小")))
  ];
}

function pageLook() {
  const sizes = [[14, "小"], [18, "中"], [22, "大"], [26, "特大"]];
  const near = sizes.reduce((a, b) => (Math.abs(b[0] - S.config.cand_font_size) < Math.abs(a[0] - S.config.cand_font_size) ? b : a))[0];
  return [
    h("h1", {}, "外觀"),
    card(
      row("候選字視窗大小", "調整按 ↓ 選字時，候選字視窗與字體的顯示尺寸", segments(sizes, near, (v) => setConfig("cand_font_size", v), "候選字視窗大小")),
      row("候選字排列方向", "選擇垂直單欄列表，或水平單排列表（每排 9 字）", cfgSegments("cand_per_row", [[1, "垂直"], [9, "水平"]], "候選字排列方向")),
      row("符號鍵盤位置", "按「~」叫出的符號鍵盤可自由拖曳並記憶位置",
        h("button", { class: "btn", onclick: async () => { await api("resetKeyboard"); toast("已重設，符號鍵盤下次將跟隨游標顯示"); } }, "重設為跟隨游標"))),
    h("p", { class: "foot" }, "設定視窗、候選字視窗、符號鍵盤與補字提示框，皆會自動跟隨 Windows 系統的主題模式。")
  ];
}

// [title on the card, one line on the card, the paragraphs in its dialog ([bold lead, text]),
//  the setting it is about ([page, row title]), the dialog's title if not the card's]
const TOPICS = [
  ["中英混打模式", "中英文直接混輸，免按 Shift 鍵切換", [
    ["免切換直接輸入", "無法組成注音的字母組合（如 hello、Python）會自動識別為英文，按空白鍵即可直接送出。"]],
   null, "中英混打"],
  ["選字與改字", "整句智慧選字，按 ↓ 快速修正", [
    ["修改文字", "系統會依語意自動挑選最適合的字詞。若需修改，移至該字後按 ↓ 叫出候選字視窗並選字。"],
    ["替換文字", "按 ↑ 可開啟替換文字視窗，將文字一鍵轉為注音符號、英文字母，修正的詞彙會被系統記住。"]]],
  ["Tab 智慧補字", "即時預測字詞，按 Tab 一鍵補齊", [
    ["中文字詞預測", "打字時游標後方會浮現預測小框，按一下 Tab 即可採用，並支援連續按 Tab 補字。"],
    ["英文與 Email 補完", "輸入英文單字、自訂詞彙裡的英文或記憶過的 Email 前 3 個字元，同樣可按 Tab 補齊。"]],
   ["complete", "Tab 智慧補字"]],
  ["符號鍵盤", "按 ~ 鍵快速叫出各類特殊符號", [
    ["快速輸入特殊符號", "按鍵盤左上角的 ` 或 ~ 即可叫出符號鍵盤。"],
    ["視窗位置", "按住視窗空白處可自由拖曳，系統會自動記住您偏好的位置。"]],
   ["look", "符號鍵盤位置"]],
  ["數字與符號", "自動判斷數字與標點符號情境", [
    ["符號直接輸出", "非注音或英文按鍵的標點符號，會直接輸出該符號（例：[、]）。"],
    ["智慧區分數字與聲調", "連續輸入 2026、100 會自動識別為數字；若有打聲調（如 18 加空白鍵）則視為注音。\n接在中文字後方的 , . ; 會當成標點符號。"]],
   null, "數字與標點"],
  ["全英模式", "按 Shift 可切換純英文模式", [
    ["切換純英文模式", "按 Shift 可切換至純英文模式，再按一次即可切回中英混打。"]],
   ["general", "Shift 切換輸入模式"], "全英模式切換"],
  ["注音順序不限", "聲韻母順序打反也能正確辨識", [
    ["不限輸入順序", "輸入注音時，聲母、介音、韻母順序顛倒也能正確辨識\n（例如輸入 us3 與 su3 都能打出「你」）。"]]],
  ["隱私與記憶", "輸入習慣僅存於本機，絕不外傳", [
    ["100% 本機端儲存", "您選過的字詞、常用英文與 Email 紀錄皆僅儲存在這台電腦中，絕不上傳雲端，保障隱私安全。"],
    ["自動淘汰舊紀錄", "長期未使用的字詞權重會自然降低；您也可以隨時至「補字與記憶」頁面手動清除所有紀錄。"]],
   ["complete", "已記憶項目"]],
  ["遊戲模式", "遊戲中自動暫停組字，防按鍵衝突", [
    ["解決遊戲按鍵衝突", "避免玩遊戲時造成按鍵衝突，以及避免輸入法的視窗容易擋住畫面，嚴重時甚至讓畫面閃爍。您可至「一般」頁面開啟此功能（預設為關閉）。"],
    ["全自動偵測遊戲", "開啟後，只要 Windows 遊戲列識別的遊戲或全螢幕獨佔程式，系統就會自動切換為全英模式。"],
    ["遊戲內打字切換", "在遊戲聊天室需要打中文時，只需按一下 Shift 即可正常打字。"]],
   ["general", "遊戲模式"]],
];
const FAQ = [
  ["打出來都是英文字母，打不出中文？", "請檢查工作列的輸入法圖示。若目前處於英文模式，請按一下 Shift 鍵（或用滑鼠左鍵點擊圖示）即可切回中文模式。"],
  ["系統自動選錯字了，該怎麼修改？", "在按 Enter 送出文字前，用方向鍵 ← → 將游標移到錯字上，按 ↓ 叫出候選字視窗並按數字鍵選字。系統會記住您的選擇，下次就會優先出現。"],
  ["想打數字「94」，卻變成中文字「愛」？", "有打聲調的數字會被當成注音。此時只需在該字上按 ↑，從上方替換列選擇原本的數字即可；或者直接使用鍵盤右側的數字鍵盤，輸出就一定會是數字。"],
  ["Email 要打幾次才會被記住？", "只要在中英混打模式下完整輸入並送出 1 次就會自動記住。下次只需打出開頭前 3 個字元，後方就會浮現完整提示，按 Tab 鍵即可補完。", ["complete", "自動記憶常用英文與 Email"]],
  ["英文單字要打幾次才會被記住？", "4 個字母以上的英文單字，在中英混打模式下輸入並送出滿 2 次就會記住，下次輸入前 3 個字母即可按 Tab 補完。（註：若按 Shift 切換至「純英文模式」下打字，則不會納入記憶。）", ["complete", "自動記憶常用英文與 Email"]],
  ["玩遊戲時被輸入法攔截怎麼辦？", "請至「一般」頁面開啟「遊戲模式」。開啟後在遊戲內會自動切換為全英文模式；若需在遊戲聊天室打中文，按一下 Shift 即可臨時切換。", ["general", "遊戲模式"]]
];

let helpTab = "lessons";

function pageHelp() {
  const tabs = h("div", { class: "pivot", role: "tablist", "aria-label": "使用說明" });
  const body = h("div", { class: "pivot-body" });
  function select(tab) {
    helpTab = tab;
    for (const b of tabs.children) {
      const on = b.dataset.tab === tab;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on);
    }
    body.replaceChildren(tab === "lessons" ? makeTutorial(h) : h("div", { class: "topics" }, ...TOPICS.map(([t, lead, text, link, title]) =>
      h("button", { class: "topic", type: "button", onclick: () =>
        dialog(title || t, topicBody(text),
          [link ? goButton(link) : null, { text: "知道了", kind: link ? "" : "primary", value: true }].filter(Boolean)) },
        h("span", { class: "topic-title" }, t), h("span", { class: "topic-lead" }, lead), h("span", { class: "topic-more" }, "了解詳情 ›")))));
  }
  for (const [tab, text] of [["lessons", "演示動畫"], ["topics", "功能總覽"]])
    tabs.append(h("button", { class: "pivot-tab", role: "tab", type: "button", "data-tab": tab, onclick: () => select(tab) }, text));
  select(helpTab);
  return [h("h1", {}, "使用說明"), tabs, body];
}

function pageFaq() {
  return [
    h("h1", {}, "常見問題"),
    h("div", { class: "card" }, ...FAQ.map(([q, a, link]) => h("details", { class: "faq" }, h("summary", {}, q), h("p", {}, a),
      link ? h("div", { class: "faq-go" }, h("button", { class: "btn", onclick: () => goTo(...link) }, goButton(link).text)) : null))),
    h("p", { class: "foot" }, "想了解詳細的打字技巧與功能操作，請參閱左側選單的「使用說明」。")
  ];
}

// update: idle | checking | latest | available | updating | done | failed | cancelled
const UPDATE = { state: "idle", version: "", code: 0, stable: "", beta: "" };
// bumped when the block is reset: a reply that was asked for before that is dropped
let updateGen = 0;

function updateBlock() {
  const u = UPDATE;
  const status = h("div", { class: "update-status" });
  let action = null;
  if (u.state === "checking" || u.state === "updating") {
    status.append(h("span", { class: "spinner", "aria-hidden": "true" }), u.state === "checking" ? "正在檢查更新…" : "正在下載並更新，請稍候…");
  } else if (u.state === "latest") {
    status.classList.add("ok");
    status.append("✓ 目前已是最新版本");
  } else if (u.state === "available") {
    status.append(`發現新版本 ${u.version} 可供安裝`);
    action = h("button", { class: "btn primary", onclick: runUpdate }, "立即更新");
  } else if (u.state === "done") {
    status.classList.add("ok");
    status.append(`✓ 已成功更新至 ${u.version}（已開啟的應用程式需重新啟動才會套用新版）。`);
  } else if (u.state === "cancelled") {
    status.append("已取消更新。");
  }
  if (!action && u.state !== "checking" && u.state !== "updating") {
    action = h("button", { class: "btn", onclick: checkUpdate }, "檢查更新");
  }
  // the two remote lines show only once a check has run (nothing is fetched when the page opens)
  const checked = u.state !== "idle" && u.state !== "checking" && u.state !== "failed" && (u.stable || u.beta);
  const head = h("div", { class: "card about-head" },
    h("img", { class: "app-mark", src: "logo.svg", alt: "" }),
    h("div", { class: "row-text" }, h("div", { class: "version" }, `兩儀輸入法 ${S.version || ""}`),
      h("div", { class: "row-desc" }, `當前版本：${S.version || ""}`),
      checked && u.stable ? h("div", { class: "row-desc" }, `正式版：${u.stable}`) : null,
      checked && u.beta ? h("div", { class: "row-desc" }, `beta版：${u.beta}`) : null,
      S.canUpdate ? status : null),
    S.canUpdate ? action : null);
  const banner = u.state === "failed"
    ? h("div", { class: "banner", role: "alert" },
        h("div", { class: "row-text" },
          h("div", { class: "banner-title" }, "更新失敗"),
          h("div", { class: "row-desc" }, "更新失敗，請稍後可以再試一次。"),
          h("div", { class: "banner-actions" },
            h("button", { class: "btn primary", onclick: checkUpdate }, "再試一次"),
            h("button", { class: "btn", onclick: () => api("report", { code: `U-${u.code}` }) }, "回報問題"),
            h("span", { class: "code" }, `錯誤代碼：U-${u.code}`))))
    : null;
  return [banner, head];
}

async function checkUpdate() {
  if (UPDATE.state === "checking" || UPDATE.state === "updating") return;
  const gen = updateGen;
  Object.assign(UPDATE, { state: "checking" });
  show("about");
  const r = await api("checkUpdate").catch(() => ({ code: 101 }));
  if (gen !== updateGen) return;
  const lines = { stable: r.stable || "", beta: r.beta || "" };
  if (r.code === 0) Object.assign(UPDATE, { state: "latest" }, lines);
  else if (r.code === 10) Object.assign(UPDATE, { state: "available", version: r.version }, lines);
  else Object.assign(UPDATE, { state: "failed", code: r.code });
  show("about");
}

async function runUpdate() {
  if (UPDATE.state === "checking" || UPDATE.state === "updating") return;
  const gen = updateGen;
  Object.assign(UPDATE, { state: "updating" });
  show("about");
  const r = await api("runUpdate").catch(() => ({ code: 301 }));
  if (gen !== updateGen) return;
  // 0 = installed and verified (r.version is what was installed); 30 = nothing newer was left to install
  if (r.code === 0) Object.assign(UPDATE, { state: "done", version: r.version });
  else if (r.code === 30) Object.assign(UPDATE, { state: "latest" });
  else if (r.code === 20) Object.assign(UPDATE, { state: "cancelled" });
  else Object.assign(UPDATE, { state: "failed", code: r.code });
  S = await api("state");
  show("about");
}

// 匯出與匯入: 記憶, 個人詞庫 and the settings in one file; importing merges it into what is here
async function backup() {
  try {
    if (await api("exportBackup")) toast("已匯出");
  } catch {
    toast("匯出失敗，請再試一次");
  }
}

async function restore() {
  let info;
  try { info = await api("openBackup"); } catch { info = { error: "無法讀取這個檔案" }; }
  if (!info) return;  // (cancelled)
  if (info.error) {
    await dialog("匯入資料", h("p", { class: "row-desc", style: "font-size:14px" }, info.error), [{ text: "知道了", kind: "primary", value: true }]);
    return;
  }
  const parts = [["memory", `記憶（${info.memory} 筆）`, info.memory > 0], ["words", `自訂詞彙（${info.words} 筆）`, info.words > 0],
    ["shortcuts", `映射詞彙（${info.shortcuts} 筆）`, info.shortcuts > 0], ["config", "設定", info.config]];
  const boxes = {};
  const body = h("div", {},
    h("p", { class: "row-desc", style: "font-size:14px" }, `匯出時間：${info.created}。會與目前的資料合併。`),
    ...parts.filter(([, , has]) => has).map(([key, label]) => {
      boxes[key] = h("input", { type: "checkbox" });
      boxes[key].checked = true;
      return h("label", { class: "check" }, boxes[key], label);
    }));
  const ok = await dialog("匯入資料", body, [{ text: "取消", value: false }, { text: "匯入", kind: "primary", value: true }]);
  if (!ok) return;
  const pick = {};
  for (const [key] of parts) pick[key] = !!(boxes[key] && boxes[key].checked);
  try {
    await api("restoreBackup", pick);
    toast(pick.memory ? "已匯入，下次打字時套用記憶" : "已匯入");
    S = await api("state");
    show("about");
  } catch {
    toast("匯入失敗，請再試一次");
  }
}

async function uninstall() {
  const pime = h("input", { type: "checkbox" });
  const data = h("input", { type: "checkbox" });
  const body = h("div", {},
    h("p", { class: "row-desc", style: "font-size:14px" }, "確定要從這台電腦移除「兩儀輸入法」嗎？"),
    h("label", { class: "check" }, data, "一併刪除個人學習紀錄、詞庫與設定檔"),
    // (only where PIME, which versions before 2.0 needed, is still installed)
    S.hasPime ? h("label", { class: "check" }, pime, "同時移除舊版殘留的 PIME 元件") : null);
  const ok = await dialog("解除安裝兩儀輸入法", body, [{ text: "取消", value: false }, { text: "確認移除", kind: "danger", value: true }]);
  if (!ok) return;
  const done = await api("uninstall", { removePime: !!S.hasPime && pime.checked, removeData: data.checked }).catch(() => false);
  if (!done) toast("無法啟動解除安裝程式，請到「設定 > 應用程式」移除兩儀輸入法");
}

function pageAbout() {
  return [
    h("h1", {}, "關於"),
    ...updateBlock(),
    S.canUpdate ? card(row("搶先體驗測試版（Beta）", "提早體驗開發中的最新功能，但穩定性可能不如正式版",
      toggle(S.beta, async (v) => {
        await api("setBeta", { on: v }); S.beta = v;
        // a result fetched for the other setting must not be acted on: back to not checked
        updateGen++;
        Object.assign(UPDATE, { state: "idle", version: "", code: 0, stable: "", beta: "" });
        show("about");
        toast(v ? "已加入搶先體驗" : "已退出搶先體驗");
      }, { label: "搶先體驗測試版（Beta）", disabled: UPDATE.state === "checking" || UPDATE.state === "updating" }))) : null,
    card(
      row("本機學習紀錄與設定", "所有個人詞庫與打字習慣皆儲存在本機，絕不上傳雲端", h("button", { class: "btn", onclick: () => api("openDataFolder") }, "開啟儲存位置")),
      row("匯出與匯入", "將選字記憶、個人詞庫與設定匯出，換電腦時可直接匯入還原",
        h("button", { class: "btn", onclick: backup }, "匯出…"), h("button", { class: "btn", onclick: restore }, "匯入…")),
      row("解除安裝兩儀輸入法", "將輸入法從電腦中移除（可選擇是否保留個人學習紀錄）", h("button", { class: "btn danger", onclick: uninstall }, "解除安裝"))),
    h("p", { class: "foot" }, "開源致謝：基礎詞庫採用「小麥注音」（MIT 授權）；前後文統計取自維基百科與公開網頁文字。")
  ];
}

const BUILD = { general: pageGeneral, typing: pageTyping, complete: pageComplete, dict: pageDict, look: pageLook, help: pageHelp, faq: pageFaq, about: pageAbout };
let current = "general";

function show(name) {
  if (!BUILD[name]) name = "general";
  const same = name === current && document.querySelector(".page");
  current = name;
  const main = document.getElementById("main");
  const keep = same ? main.scrollTop : 0;
  const page = h("div", { class: "page" }, ...BUILD[name]().filter(Boolean));
  if (same) page.style.animation = "none";
  main.replaceChildren(page);
  main.scrollTop = keep;
  document.querySelectorAll(".nav-item").forEach((b) => {
    const on = b.dataset.page === name;
    b.classList.toggle("active", on);
    if (on) {
      b.setAttribute("aria-current", "page");
      const ind = document.getElementById("nav-indicator");
      ind.style.transform = `translateY(${b.offsetTop + b.offsetHeight / 2 - 8}px)`;
    } else b.removeAttribute("aria-current");
  });
  if (!same) history.replaceState(null, "", "#" + name);
}

// The focus ring shows while the keyboard moves around the page, not after a click (see app.css)
const NAV_KEYS = new Set(["Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"]);
document.addEventListener("keydown", (e) => { if (NAV_KEYS.has(e.key)) document.documentElement.dataset.keys = ""; }, true);
document.addEventListener("pointerdown", () => { delete document.documentElement.dataset.keys; }, true);

async function main() {
  const nav = document.getElementById("nav-items");
  for (const [name, text] of PAGES) {
    nav.append(h("button", { class: "nav-item", "data-page": name, onclick: () => show(name) }, icon(name), text));
  }
  S = await api("state");
  // (browser preview only: ?show=failed|available|latest|updating|done shows that update state)
  const preview = !(window.chrome && window.chrome.webview) && new URLSearchParams(location.search).get("show");
  if (preview) Object.assign(UPDATE, { state: preview, version: "2.2.0", code: 301, stable: "2.2.0", beta: "2.3.0" });
  show(location.hash.slice(1) || "general");
  // Windows' own settings take a moment (PowerShell): the toggles come alive then
  api("system").then((r) => {
    SYS = r;
    document.querySelectorAll("[data-system]").forEach((t) => {
      const input = t.querySelector("input");
      input.checked = !!SYS[t.dataset.system];
      input.disabled = false;
      input.dispatchEvent(new Event("sync"));
    });
  }).catch(() => {});
}

main();
