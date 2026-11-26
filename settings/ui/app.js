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
  // (#36 design: あ in a rounded square, the other icons' line weight - the user 2026-11-24 "日文這一類別你還沒補一個小圖案")
  japanese: '<rect x="2.5" y="2.5" width="13" height="13" rx="2.5"/><text x="9" y="12.6" text-anchor="middle" font-size="9.5" font-weight="600" fill="currentColor" stroke="none" font-family="Yu Gothic UI, Meiryo UI, sans-serif">あ</text>',
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

// 簡體輸出 (#32): 繁體 / 簡體, and its key (off as it comes: the key is then the program's)
function scriptRows() {
  const keyBox = h("button", { class: "text-input key-box", type: "button", "aria-label": "繁簡切換的組合鍵" },
    S.config.simplified_key);
  let listening = false;
  const stop = () => { listening = false; keyBox.textContent = S.config.simplified_key; };
  keyBox.addEventListener("click", () => { listening = true; keyBox.textContent = "請按下組合鍵…"; });
  keyBox.addEventListener("blur", stop);
  keyBox.addEventListener("keydown", async (e) => {
    if (!listening) return;
    e.preventDefault();
    if (e.key === "Escape") return stop();
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;  // (a modifier alone: wait for the key)
    // (by the key's place, e.code: Shift+1 gives e.key "!", review 2026-11-14)
    const code = /^Key([A-Z])$/.exec(e.code) || /^Digit([0-9])$/.exec(e.code) || /^(F(?:[1-9]|1[0-2]))$/.exec(e.code);
    const k = code ? code[1] : null;
    const parts = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win", k || e.key].filter(Boolean);
    try {
      S.config = await api("setConfig", { key: "simplified_key", value: parts.join("+") });
      toast("設定已儲存並自動套用");
    } catch (err) {
      toast(err && err.message ? err.message : "儲存失敗，請再試一次");
    }
    stop();
  });
  return [
    row("繁體／簡體輸出", "注音照打，選擇送出繁體字或簡體字。簡體時候選字第一個是大陸用詞，選過兩次就會記住。",
      cfgSegments("simplified", [[false, "繁體"], [true, "簡體"]], "繁體／簡體輸出")),
    row("繁簡切換快捷鍵", "打字時按這組鍵切換繁體與簡體。開啟後，程式中相同組合的快捷鍵會失效。",
      cfgToggle("simplified_hotkey", "快捷鍵"), keyBox)
  ];
}

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
  box.select = select;  // (a row shown later measures again: 前文長度, #41)
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
    // (Esc closes only the top dialog: 快速填入 opens one over another)
    const esc = (e) => { if (e.key === "Escape" && overlay === [...document.querySelectorAll(".overlay")].pop()) close(null); };
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
// an English word (Kazuma, e-sports, iPhone15): a letter first, - or ' between letters or digits, 2 or more
// (as 框選查詢 takes them); no 注音 needed
const ENGLISH = /^(?=.{2})[A-Za-z][A-Za-z0-9]*(?:['-][A-Za-z0-9]+)*$/;
const HAN = /[㐀-鿿豈-﫿]/g;
// a word mixing Chinese with English letters or digits (PQC技法的ABC功能, PQC 功能): the rules of
// engine/lexicon.py is_custom_mixed; its 注音 is of its Chinese characters alone
function isMixed(a) {
  const han = (a.match(HAN) || []).length;
  return a.length <= 32 && !/^ | $|  /.test(a) && han > 0 && han <= 8 && /[A-Za-z0-9]/.test(a) &&
    /^(?:[㐀-鿿豈-﫿A-Za-z0-9 ]|(?<=[A-Za-z0-9])['-](?=[A-Za-z0-9]))+$/.test(a);
}
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
    if (isMixed(a)) {
      const han = a.match(HAN).length;
      if (!/^[ㄅ-ㄩˊˇˋ˙ˉ\s-]+$/.test(b)) return `「${a}」：注音欄僅能輸入注音符號與聲調`;
      if (syllables(b) !== han) return `「${a}」：注音數量須與中文字數相同（共 ${han} 個中文字）`;
      return "";
    }
    if (!CJK.test(a)) return `「${a}」：僅支援中文、英文字母、數字、空白或注音文（英文可包含 - 與 '）`;
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
        // (short enough for the box: the note on 破音字 is in the line above the table)
        ib.placeholder = en ? "英文無須注音" : zh ? "注音文無須注音" : "可按「自動填入注音」帶入";
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
  // (a word with several spaces in a row keeps one: PQC  功能 is PQC 功能, as 框選查詢 takes it)
  const rows = () => [...tbody.rows].map((r) => [r.cells[0].firstChild.value.trim(), r.cells[1].firstChild.value.trim()])
    .map(([a, b]) => [words ? a.replace(/ {2,}/g, " ") : a, b]).filter(([a, b]) => a || b);
  const body = h("div", {},
    h("p", { class: "row-desc" }, words ? "輸入法會優先選用此清單中的詞彙。注音可按下方「自動填入注音」帶入，破音字請自行確認。" : "可自定義詞彙映射關係（例：KFC → 肯德基）。中文那一邊打出來後按 ↓，也會出現另一邊（肯德基 → KFC）。"),
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
    // (a mixed word too: the 注音 of its Chinese characters)
    const empty = [...tbody.rows].filter((r) => /[㐀-鿿豈-﫿]/.test(r.cells[0].firstChild.value.trim()) && !r.cells[1].firstChild.value.trim());
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
  ["general", "一般"], ["typing", "打字"], ["complete", "補字與記憶"], ["dict", "個人詞庫"], ["japanese", "日文"], ["look", "外觀"], ["help", "使用說明"], ["faq", "常見問題"], ["about", "關於"]
];

function pageGeneral() {
  return [
    h("h1", {}, "一般"),
    card(
      row("設為預設輸入法", "", systemToggle("default_ime", "設為預設輸入法")),
      row("預設輸入模式", "開啟新程式或切換至本輸入法時的初始模式",
        // (#36 design: with 日文 on, its two modes too - the user 2026-11-24)
        // (日文 off with a Japanese mode chosen: the IME starts in 中英混打 - shown so; the setting stays for when 日文 is
        // on again - the user's boundary 2026-11-26, the preview's mistake the user caught 2026-11-26)
        segments(S.config.ja_enabled
          ? [["mixed", "中英混打"], ["ja_mixed", "中日混打"], ["ja_only", "純日文"], ["english", "純英文"]]
          : [["mixed", "中英混打"], ["english", "純英文"]],
          !S.config.ja_enabled && ["ja_mixed", "ja_only"].includes(S.config.start_mode) ? "mixed" : S.config.start_mode,
          (v) => setConfig("start_mode", v), "預設輸入模式"))),
    h("h2", {}, "切換設定"),
    card(
      row("Shift 切換輸入模式", "按一下 Shift 切換至純英文模式，再按一下切回中英混打模式", cfgToggle("shift_toggle", "Shift 切換輸入模式")),
      hotkeyRow(),
      row("遊戲中 Shift 交給遊戲", "在遊戲裡，Shift 和其他按鍵直接交給遊戲（例如衝刺、技能鍵），不會切換輸入模式；關閉後遊戲和一般程式一樣，可以按 Shift 切換打中文",
        cfgToggle("game_keys", "遊戲中 Shift 交給遊戲")))
  ];
}

// From 使用說明 / 常見問題 to the setting itself: that page, the row lit up with a 「在這裡」 tag (nth: the row of that
// title to light, counted from 0 - 個人詞庫 has 自訂詞彙 twice, the Chinese one and the Japanese one)
function goTo(page, title, nth = 0) {
  show(page);
  const t = [...document.querySelectorAll(".row-title")].filter((e) => e.textContent === title)[nth];
  // (no row of that title: a button saying it, its card lit - 關於's 檢查更新 sits in the version card, the user 2026-11-26)
  const b = !t && [...document.querySelectorAll("button")].find((e) => e.textContent === title);
  const r = t ? t.closest(".row") : b && b.closest(".card");
  if (!r) return;
  r.scrollIntoView({ block: "center" });
  r.classList.add("spotlight");
  const tag = h("span", { class: "here" }, "此處");
  if (t) t.append(tag);
  else b.after(tag);
  setTimeout(() => { r.classList.remove("spotlight"); tag.remove(); }, 3200);
}

// A link [page, title, nth, "ja"]: a row usable only while 日文輸入 is on (個人詞庫's Japanese section, the 日文 page's rows) - while it is off
// the button says so and turns it on first (the user 2026-11-26: one button, 「打開日文輸入並前往」)
const goText = (link) => link[3] === "ja" && !S.config.ja_enabled ? "打開日文輸入並前往" : "前往設定";
async function follow([page, title, nth, needs]) {
  if (needs === "ja" && !S.config.ja_enabled) await setConfig("ja_enabled", true);
  goTo(page, title, nth);
}
const goButton = (link) => ({ text: goText(link), left: true, click: (c) => { c(true); follow(link); } });

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
      ...scriptRows(),
      row("Ctrl + 標點打全形", "按住 Ctrl 再按標點鍵即輸出全形標點。開啟後，程式中相同組合的快捷鍵在中英混打時會失效", cfgToggle("ctrl_full_punct", "Ctrl + 標點打全形")),
      row("注音自動校正",
        h("span", {}, "按到旁邊的鍵、注音順序打錯、同一個鍵按兩次、漏打聲調，或 ㄣ／ㄥ、ㄢ／ㄤ 打反（因該→應該）時，看整句自動改成最可能的字。可透過 ↓ 選回原本打的。",
          h("span", { class: "row-warn" }, "注意：偶爾會把打對的字或數字也改掉。")),
        cfgToggle("autocorrect", "注音自動校正")),
      ...longSentenceRows(),
      previewChoice())
  ];
}

// 選字模型 (#41): the tiers released (only theirs go into the installer: installer\build.ps1) and each tier and context
// length's median time a key on the development computer (docs/superpowers/items/41/tier_latency.py, 2026-11-20, measured
// before a release and written here). One tier alone: no 選字模型 row.
const LM_TIERS_SHIPPED = ["light", "plus", "max"];
// (median key, docs/superpowers/items/41/tier_latency.py on this computer, 5 runs in turn, 2026-11-21 with #43/#58
// and the stepping done together: light 1.097, plus 1.215 / 1.219, max 1.222 / 1.221 ms)
const LM_LATENCY_MS = { "light-32": "1.1", "plus-32": "1.2", "plus-64": "1.2", "max-32": "1.2", "max-64": "1.2" };
const LM_TIER_NAMES = { light: "輕量", plus: "加強", max: "最強" };

// 長句選字加強 and, under it, 選字模型, 參考剛送出的字 (#37's 接續前文選字 renamed, #89) and, for 加強／最強, 前文長度
// (#41) - the model's settings in that order (the user 2026-11-26: the model decides, the other two are what it reads):
// greyed while 長句選字加強 is off
function longSentenceRows() {
  const sub = toggle(S.config.lm_context, (v) => setConfig("lm_context", v),
    { label: "參考剛送出的字", disabled: !S.config.long_sentence });
  const subText = (n) => `選字時也看剛送出、游標還緊接在後面的字，最多看 ${n} 個字。`;
  const subRow = row("參考剛送出的字", subText(32), sub);
  subRow.classList.add("row-sub");
  // (#89: its number follows the model and 前文長度 chosen - light 32, plus / max their 32 or 64 - changed at once)
  const model = modelRows((n) => { subRow.querySelector(".row-desc").textContent = subText(n); });
  const grey = (off) => {
    sub.querySelector("input").disabled = off;
    for (const r of [subRow, ...model.rows]) r.classList.toggle("off", off);  // (the whole row greyed: a switch's track
    model.disable(off);                                                         // alone barely showed it)
  };
  const main = toggle(S.config.long_sentence, (v) => { setConfig("long_sentence", v); grey(!v); }, { label: "長句選字加強" });
  grey(!S.config.long_sentence);
  return [row("長句選字加強", "看整句的意思選字，長句更準確。電腦較舊、打字覺得變慢時可以關閉。", main),
    ...model.rows.slice(0, 1), subRow, ...model.rows.slice(1)];
}

// 選字模型 (buttons 輕量｜加強｜最強, the tiers released) and 前文長度 (32 字｜64 字, shown for 加強／最強, each tier its own
// setting); one latency line, in 選字模型's description, for the tier and length chosen. Nothing when one tier alone.
function modelRows(onLength = () => {}) {
  // (onLength(n): the characters the model reads with the tier and length chosen - 參考剛送出的字's words, #89)
  if (LM_TIERS_SHIPPED.length < 2) return { rows: [], disable() {} };
  const lenKey = (t) => (t === "plus" ? "lm_context_len_plus" : "lm_context_len_max");
  const lenOf = (t) => (t !== "light" && Number(S.config[lenKey(t)]) === 64 ? 64 : 32);
  let tier = LM_TIERS_SHIPPED.includes(S.config.lm_model) ? S.config.lm_model : "light";
  const ms = h("span", {});
  const draw = () => {
    ms.textContent = `每鍵約 ${LM_LATENCY_MS[`${tier}-${lenOf(tier)}`] || "—"} 毫秒（開發機量測）`;
    lenRow.style.display = tier === "light" ? "none" : "";
    // (the tier's own length, and measured again now it is shown: while hidden its buttons had no width)
    if (tier !== "light") pickLen.select(lenOf(tier));
    onLength(lenOf(tier));
  };
  const pickTier = segments(LM_TIERS_SHIPPED.map((t) => [t, LM_TIER_NAMES[t]]), tier,
    (v) => { tier = v; setConfig("lm_model", v); draw(); }, "選字模型");
  const tierRow = row("選字模型", "-", pickTier);
  tierRow.querySelector(".row-desc").replaceChildren("越強選字越準，每按一鍵也越慢：", ms);
  tierRow.classList.add("row-sub");
  const pickLen = segments([[32, "32 字"], [64, "64 字"]], lenOf(tier), (v) => {
    S.config[lenKey(tier)] = v;  // (draw reads it before setConfig answers)
    setConfig(lenKey(tier), v);
    draw();
  }, "前文長度");
  const lenRow = row("前文長度", "模型從游標往前看幾個字，含這句前面的字，開著上一項時也含剛送出的字；越多越準也越慢", pickLen);
  lenRow.classList.add("row-sub", "row-sub2");
  draw();
  const disable = (off) => { for (const b of [...pickTier.querySelectorAll("button"), ...pickLen.querySelectorAll("button")]) b.disabled = off; };
  return { rows: [tierRow, lenRow], disable };
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

// 已記憶項目's text: 記憶清單's rows (#65), 簡體 apart once Simplified mode has memory of its own (#32)
function rememberedText() {
  return S.rememberedSimplified == null  // (簡體輸出 never used: as before)
    ? `目前已記住 ${S.remembered} 筆打字習慣、${S.rememberedEmails} 個 Email`
    : `目前已記住 繁體 ${S.remembered} 筆、簡體 ${S.rememberedSimplified} 筆打字習慣，${S.rememberedEmails} 個 Email`;
}

function pageComplete() {
  const remembered = h("span", {}, rememberedText());
  return [
    h("h1", {}, "補字與記憶"),
    card(
      row("Tab 智慧補字", "輸入時於游標後方提示最可能的下一個字", cfgToggle("completion", "Tab 智慧補字")),
      row("自動記憶常用英文與 Email", "自動記憶輸入滿 2 次的英文單字，以及輸入過 1 次的 Email。", cfgToggle("remember_words", "自動記憶常用英文與 Email")),
      row("已記憶項目", remembered, h("button", { class: "btn", onclick: forgetMemory }, "全部清除"))),
    card(memoryPanel(async () => { S = await api("state"); remembered.textContent = rememberedText(); }))
  ];
}

// ---- 記憶清單 (#65 design, approved 2026-11-24): what affects typing, searchable, deletable --------------------
// Rows as settings.exe's memory op gives them (Settings.cs MemoryList, the same as engine/lexicon.py memory_list):
// {kind, key, value, shown, reading, count, day, note}, newest first; count and day null where nothing is kept (the
// older 照打成英文 of user.json): shown as —. A row is (kind, key, value) to delete.
const MEMORY_SHOWN = 300;  // rows drawn at once; the rest reached by search
const memoryCount = (r) => r.count == null ? "—" : `${r.count} 次`;
const memoryDay = (r) => {
  if (r.day == null) return "—";
  const d = new Date(r.day * 86400000);
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
};
const MEMORY_TABS = [
  ["picks", "選字", ["字詞", "注音", "選過", "最近使用"], (r) => [r.shown, r.reading, memoryCount(r), memoryDay(r)]],
  ["words", "英文字", ["英文字", "打過", "最近使用"], (r) => [r.shown, memoryCount(r), memoryDay(r)]],
  ["emails", "Email", ["Email", "打過", "最近使用"], (r) => [r.shown, memoryCount(r), memoryDay(r)]],
  // (count and last use as the other tabs - the user 2026-11-24; 會打成: the 注音 stored, or the keys themselves)
  ["keys", "按鍵", ["按鍵", "會打成", "說明", "選過", "最近使用"], (r) => [r.shown, r.reading || r.shown, r.note, memoryCount(r), memoryDay(r)]],
  // (#83 標點記憶, the user 2026-11-26: a mark swapped twice is what its key types; only the ones in effect listed)
  ["punct", "標點", ["標點", "會打成", "選過", "最近使用"], (r) => [r.shown, r.reading, memoryCount(r), memoryDay(r)]],
  // (#36 plan 7: 日文 - 選字學習 and 常打的詞組, while 日文輸入 is on; the data stays when it is off)
  ["ja", "日文", ["字詞", "讀音", "種類", "選過", "最近使用"], (r) => [r.shown, r.reading, r.note, memoryCount(r), memoryDay(r)]],
  ["simplified", "簡體模式", ["字詞", "對應", "種類", "選過", "最近使用"], (r) => [r.shown, r.reading, r.note, memoryCount(r), memoryDay(r)]]
];
const MEMORY_TABS_ALL = MEMORY_TABS;  // (#36 plan 7: memoryPanel's draw keeps the 日文 tab only while 日文 is on)

function memoryPanel(deleted) {
  // deleted(): after a 刪除, so 已記憶項目 above follows (the settings tool counts the rows the engine is about to remove
  // as gone). (On the page itself, below 已記憶項目: the user 2026-11-24 "我不喜歡要點開才能看 直接列在下面就好")
  let data = { picks: [], words: [], emails: [], keys: [], simplified: null };
  data.punct = [];  // (#83 標點記憶)
  data.ja = [];  // (#36 plan 7)
  let tab = "picks";
  const id = (r) => r.kind + "\u0000" + r.key + "\u0000" + r.value;
  const picked = new Set();  // id() of the rows ticked
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "記憶種類" });
  const search = h("input", { class: "text-input", type: "search", placeholder: "搜尋", "aria-label": "搜尋", style: "margin-bottom:8px" });
  const table = h("table", { class: "list" });
  const empty = h("p", { class: "row-desc", style: "display:none;padding:12px 0" }, "沒有符合的項目");
  const more = h("p", { class: "row-desc", style: "display:none;padding:8px 0 0" });
  const del = h("button", { class: "btn danger", disabled: true }, "刪除選取的項目");
  const cellsOf = (r) => MEMORY_TABS.find(([k]) => k === tab)[3](r);
  function rows() {
    const q = search.value.trim().toLowerCase();
    return (data[tab] || []).filter((r) => !q || cellsOf(r).some((c) => String(c).toLowerCase().includes(q)));
  }
  function draw() {
    // (#36 plan 7: the 日文 tab only while 日文輸入 is on - the tabs below read this one list)
    const MEMORY_TABS = MEMORY_TABS_ALL.filter(([k]) => k !== "ja" || S.config.ja_enabled);
    const shownTabs = MEMORY_TABS.filter(([k]) => k !== "simplified" || (data.simplified || []).length);
    if (!shownTabs.some(([k]) => k === tab)) tab = "picks";  // (簡體模式 emptied by a 刪除: its tab is gone - findings #117)
    tabs.replaceChildren(...shownTabs.map(([k, name]) =>
      h("button", { class: "tab" + (k === tab ? " on" : ""), role: "tab", onclick: () => { tab = k; draw(); } },
        `${name} ${(data[k] || []).length}`)));
    const [, , heads] = MEMORY_TABS.find(([k]) => k === tab);
    const found = rows();
    const shown = found.slice(0, MEMORY_SHOWN);
    const all = h("input", { type: "checkbox", "aria-label": "全選" });
    all.checked = shown.length > 0 && shown.every((r) => picked.has(id(r)));
    all.addEventListener("change", () => { for (const r of shown) all.checked ? picked.add(id(r)) : picked.delete(id(r)); draw(); });
    table.replaceChildren(
      h("thead", {}, h("tr", {}, h("th", { style: "width:32px" }, all), ...heads.map((t) => h("th", {}, t)))),
      h("tbody", {}, ...shown.map((r) => {
        const box = h("input", { type: "checkbox", "aria-label": "選取 " + r.shown });
        box.checked = picked.has(id(r));
        box.addEventListener("change", () => { box.checked ? picked.add(id(r)) : picked.delete(id(r)); draw(); });
        return h("tr", {}, h("td", {}, box), ...cellsOf(r).map((c) => h("td", {}, c)));
      })));
    empty.style.display = found.length ? "none" : "";
    more.style.display = found.length > shown.length ? "" : "none";
    more.textContent = `還有 ${found.length - shown.length} 筆，請用搜尋`;
    del.textContent = picked.size ? `刪除選取的 ${picked.size} 項` : "刪除選取的項目";
    del.disabled = !picked.size;
  }
  search.addEventListener("input", draw);
  del.addEventListener("click", async () => {
    if (!picked.size) return;
    const n = picked.size;
    const ok = await dialog("刪除記憶", h("p", { class: "row-desc", style: "font-size:14px" },
      `確定要刪除選取的 ${n} 項記憶嗎？刪除後下一個按鍵起不再出現，此操作無法復原。`),
      [{ text: "取消", value: false }, { text: "刪除", kind: "danger", value: true }]);
    if (!ok) return;
    await api("forgetMemory", { items: [...picked].map((k) => k.split("\u0000")) });
    for (const t of Object.keys(data)) if (data[t]) data[t] = data[t].filter((r) => !picked.has(id(r)));
    picked.clear();
    toast(`已刪除 ${n} 項`);
    draw();
    await deleted();
  });
  api("memory").then((d) => { data = d; draw(); });
  draw();
  return h("div", { class: "row column" },
    h("div", { class: "row-text" },
      h("div", { class: "row-title" }, "記憶清單"),
      h("div", { class: "row-desc" }, "目前會影響打字的記憶。勾選後按「刪除」，下一個按鍵起就不再出現。")),
    h("div", { style: "width:100%" },
      tabs, search, h("div", { style: "max-height:360px;overflow:auto" }, table), empty, more,
      h("div", { style: "display:flex;justify-content:flex-end;margin-top:10px" }, del)));
}

async function forgetMemory() {
  // 全部清除: the engine clears the typing memory; the e-mails only when ticked
  const emails = h("input", { type: "checkbox" });
  const body = h("div", {},
    h("p", { class: "row-desc", style: "font-size:14px" },
      "確定要清除所有打字記憶（選字習慣、記住的英文、自動校正與標點的習慣）嗎？設定與個人詞庫會保留。此操作無法復原。"),
    // (#36 plan 7: with 日文 on, what it clears there too)
    S.config.ja_enabled ? h("p", { class: "row-desc", style: "font-size:14px" }, "日文的選字與常打的詞組也會一起清除。") : null,
    h("label", { class: "check" }, emails, "一併清除記住的 Email"));
  const ok = await dialog("清除所有記憶項目", body, [{ text: "取消", value: false }, { text: "確定清除", kind: "danger", value: true }]);
  if (!ok) return;
  await api("forgetWords", { emails: emails.checked });
  toast("已清除");
  // the counts of 已記憶項目 again: the settings tool counts what the engine clears at its next key as gone
  S = await api("state");
  if (current === "complete") show("complete");
}

// ---- 快速填入 (#21): a table like the ASUS IME's 快捷文字本 ----------------------------------------
function quickFillProblem(text, key, rows, except) {
  if (!text) return "請輸入內容";
  if (text.length > 255) return "內容最多 255 個字";
  if (/[\r\n]/.test(text)) return "內容不能換行";
  if (!/^[A-Za-z0-9]{1,8}$/.test(key)) return "捷徑請用 1 到 8 個英文字母或數字";
  if (rows.some(([, k], i) => i !== except && k.toLowerCase() === key.toLowerCase())) return `捷徑「${key}」已被使用`;
  return null;
}

async function editQuickFillRow(rows, i) {
  // 新增 (i undefined) or 編輯 one row; resolves to the row, or null
  const [t0, k0] = i === undefined ? ["", ""] : rows[i];
  const text = h("input", { class: "text-input", value: t0, maxlength: 255, "aria-label": "內容" });
  const key = h("input", { class: "text-input", value: k0, maxlength: 8, "aria-label": "捷徑" });
  const error = h("div", { class: "error-line", role: "alert" });
  const body = h("div", { class: "fill-edit" },
    h("label", {}, "內容", text), h("label", {}, "捷徑（1 到 8 個英文字母或數字）", key), error);
  setTimeout(() => text.focus(), 0);
  return dialog(i === undefined ? "新增快速填入" : "編輯快速填入", body, [
    { text: "取消", value: null },
    { text: "確定", kind: "primary", click: (close) => {
      const row = [text.value, key.value.trim()];
      const err = quickFillProblem(row[0], row[1], rows, i);
      if (err) { error.textContent = err; return; }
      close(row);
    } }]);
}

async function editQuickFill() {
  let rows = await api("quickFill");
  const checked = new Set();
  let lastShown = 0;
  const tbody = h("tbody");
  const search = h("input", { class: "text-input fill-search", type: "search", placeholder: "搜尋", "aria-label": "搜尋" });
  const all = h("input", { type: "checkbox", "aria-label": "全選" });
  const del = h("button", { class: "btn", disabled: true }, "刪除");
  async function save(next) {
    try { await api("saveQuickFill", { rows: next }); rows = next; }
    catch (e) { toast(e && e.message ? e.message : "儲存失敗，請再試一次"); }
    draw();
  }
  function shownRows() {
    const q = search.value.trim().toLowerCase();
    return rows.map((r, i) => [r, i]).filter(([[t, k]]) => !q || t.toLowerCase().includes(q) || k.toLowerCase().includes(q));
  }
  function draw() {
    const shown = shownRows();
    const seen = new Set(shown.map(([, i]) => i));
    for (const i of [...checked]) if (!seen.has(i)) checked.delete(i);  // (only rows shown stay checked)
    lastShown = shown.length;
    tbody.replaceChildren(...shown.map(([[t, k], i]) => {
      const box = h("input", { type: "checkbox", "aria-label": "選取" });
      box.checked = checked.has(i);
      box.addEventListener("change", () => { box.checked ? checked.add(i) : checked.delete(i); sync(); });
      return h("tr", {},
        h("td", { style: "width:36px" }, box),
        h("td", { class: "fill-text" }, t),
        h("td", { style: "width:28%" }, k),
        h("td", { style: "width:40px" }, h("button", { class: "icon-btn", "aria-label": "編輯", title: "編輯",
          onclick: async () => { const row = await editQuickFillRow(rows, i); if (row) save(rows.map((r, j) => (j === i ? row : r))); } }, "✎")));
    }));
    if (!shown.length)
      tbody.append(h("tr", {}, h("td", { colspan: 4, class: "row-desc" }, rows.length ? "沒有符合的項目" : "尚未新增。打字時按 ` 再按空白，即可叫出這些內容")));
    sync();
  }
  function sync() {
    del.disabled = !checked.size;
    all.checked = lastShown > 0 && checked.size === lastShown;
  }
  // (全選 and 刪除 only ever reach the rows shown: a search clears what was checked)
  all.addEventListener("change", () => { checked.clear(); if (all.checked) shownRows().forEach(([, i]) => checked.add(i)); draw(); });
  search.addEventListener("input", () => { checked.clear(); draw(); });
  del.addEventListener("click", () => { const next = rows.filter((_, i) => !checked.has(i)); checked.clear(); save(next); });
  const add = h("button", { class: "btn", onclick: async () => { const row = await editQuickFillRow(rows); if (row) save([...rows, row]); } }, "＋ 新增");
  const body = h("div", {},
    h("p", { class: "row-desc" }, "打字時按 ` 再按空白叫出清單，用 ↑↓ 選擇、Enter 送出；接著打捷徑的開頭可以縮小清單。"),
    h("div", { class: "fill-bar" }, add, del, h("span", { class: "spacer" }), search),
    h("table", { class: "list fill-table" },
      h("thead", {}, h("tr", {}, h("th", {}, all), h("th", {}, "內容"), h("th", {}, "捷徑"), h("th"))), tbody));
  draw();
  await dialog("快速填入", body, [{ text: "完成", kind: "primary", value: true }]);
  S = await api("state");
  show("dict");
}

function pageDict() {
  return [
    h("h1", {}, "個人詞庫"),
    // (#36 design: sections 中文 / 日文 / 通用, the user 2026-11-24 - 映射詞彙 is Chinese words and codes, 快速填入 works in every mode)
    h("h2", {}, "中文"),
    card(
      row("自訂詞彙", `打字時優先選用設定的詞彙。目前已設定 ${S.words} 筆`, h("button", { class: "btn", onclick: () => editList("words") }, "編輯")),
      row("映射詞彙", `可自定義詞彙映射關係。目前已設定 ${S.shortcuts} 筆`, h("button", { class: "btn", onclick: () => editList("shortcuts") }, "編輯"))),
    // (日文 while 日文 is on)
    ...(S.config.ja_enabled ? [h("h2", {}, "日文"), card(
      row("自訂詞彙", `打日文時優先選用設定的詞彙（人名、專有名詞等）。目前已設定 ${S.jaWords || 0} 筆`,
        h("button", { class: "btn", onclick: () => editJaWords() }, "編輯")))] : []),
    h("h2", {}, "通用"),
    card(
      row("快速填入", `打字時按 \` 再按空白，叫出常用的 Email、地址等內容。目前已設定 ${S.quickFill || 0} 筆`,
        h("button", { class: "btn", onclick: () => editQuickFill() }, "編輯"))),
    h("h2", {}, "框選查詢"),
    card(
      row("連按 Ctrl 框選查詢", "選取文字後連按兩次 Ctrl，即可查看注音、語音朗讀，或快速加入「自訂詞彙」與「映射詞彙」", cfgToggle("lookup", "連按 Ctrl 框選查詢")),
      row("查詢視窗大小", "", cfgSegments("lookup_size", [["small", "小"], ["medium", "中"], ["large", "大"]], "查詢視窗大小")))
  ];
}

// ---- 候選字排列與選字鍵 (#23; the IME's side: engine/candlayout.py) ----------------------------------
const CAND_KEY_SETS = [["123456789", 3], ["1234567890", 5], ["1234qweras", 4], ["qwerasdf", 4],
  ["asdfghjkl;", 5], ["asdfzxcv89", 4], ["asdfjkl789", 4], ["aoeuhtn789", 4]];
const CAND_LAYOUTS = [["column", "垂直單欄"], ["row", "水平單排"], ["grid", "格子"]];
const CAND_SAMPLE = "你妳擬禰祢昵薿抳旎坭";

function candKeys() {
  return CAND_KEY_SETS.some(([k]) => k === S.config.cand_keys) ? S.config.cand_keys : "123456789";
}
function candLayout() {
  if (CAND_LAYOUTS.some(([v]) => v === S.config.cand_layout)) return S.config.cand_layout;
  return S.config.cand_per_row === 9 ? "row" : "column";  // (a config.json of before: cand_per_row only)
}
function candPerLine(layout, keys) {
  if (layout === "column") return 1;
  if (layout === "row") return keys.length;
  return [3, 4, 5].includes(S.config.cand_columns) ? S.config.cand_columns : CAND_KEY_SETS.find(([k]) => k === keys)[1];
}

function candLayoutRows() {
  const tiles = [];
  const columnsRow = row("格子每排", "排成格子時每排幾個；「自動」照選字鍵排整齊（例如 1234qweras 排成 1234／qwer／as）",
    segments([[0, "自動"], [3, "3"], [4, "4"], [5, "5"]], [3, 4, 5].includes(S.config.cand_columns) ? S.config.cand_columns : 0,
      (v) => { setConfig("cand_columns", v); draw(); }, "格子每排"));
  function preview(layout) {
    const keys = candKeys(), per = candPerLine(layout, keys);
    const shown = layout === "grid" ? keys.length : 4;  // (a column or a row: its first four, to fit the tile)
    const box = h("div", { class: "cand-mini", style: `grid-template-columns: repeat(${Math.min(per, shown)}, auto)` });
    for (let i = 0; i < shown; i++)
      box.append(h("span", { class: "cand-mini-item" + (i === 0 ? " on" : "") }, h("b", {}, keys[i]), CAND_SAMPLE[i]));
    return box;
  }
  function draw() {
    const layout = candLayout();
    for (const t of tiles) {
      const on = t.dataset.value === layout;
      t.classList.toggle("on", on);
      t.setAttribute("aria-checked", on);
      t.querySelector(".choice-preview").replaceChildren(preview(t.dataset.value));
    }
    columnsRow.style.display = layout === "grid" ? "" : "none";  // (only for a grid)
  }
  for (const [v, label] of CAND_LAYOUTS) {
    tiles.push(h("button", { class: "choice", type: "button", role: "radio", "data-value": v,
      onclick: () => {
        if (candLayout() !== v || S.config.cand_layout !== v) {
          setConfig("cand_layout", v);
          setConfig("cand_per_row", v === "row" ? 9 : 1);  // (what versions before #23 read)
        }
        draw();
      } },
      h("div", { class: "choice-preview cand-preview" }),
      h("div", { class: "choice-label" }, h("span", { class: "radio", "aria-hidden": "true" }),
        h("span", { class: "row-text" }, h("span", { class: "row-title" }, label)))));
  }
  const keys = h("select", { class: "select", "aria-label": "選字鍵",
    onchange: (e) => { setConfig("cand_keys", e.target.value); draw(); } },
    ...CAND_KEY_SETS.map(([k]) => h("option", { value: k }, k)));
  keys.value = candKeys();
  draw();
  return [
    h("div", { class: "row column" },
      h("div", { class: "row-text" },
        h("div", { class: "row-title" }, "候選字排列"),
        h("div", { class: "row-desc" }, "按 ↓ 選字時，候選字的排列方式")),
      h("div", { class: "choices", role: "radiogroup", "aria-label": "候選字排列" }, ...tiles)),
    row("選字鍵", "候選字視窗開著時，按這些鍵選字（一頁的字數跟著選字鍵的數量）", keys),
    columnsRow
  ];
}

// ---- 符號鍵盤內容 (#69): symbols per key, drag to swap, categories; symbols.json through settings.exe ------------------------------
const SK_ROWS = ["`1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./"];
const SK_OFFSETS = [0, 1.5, 1.75, 2.25];  // (engine/symbols.py ROW_OFFSETS, in key widths)
const SK_DEFAULT = [
  ["一般", "` 1 2 3 4 5 6 7 8 9 0 - = 《 》 〈 〉 “ ” 【 】 （ ） 〔 〕 ＼ ※ ＊ ○ ● 「 」 『 』 ： ； 、 ！ ？ … ‥ — ｛ ｝ ， 。 ／"],
  ["數學", "≦ ≧ ＜ ＞ ∈ ∉ ∃ ° ℃ ℉ ± － ＝ √ ∞ ∩ ∪ ∂ ∀ ∇ Δ × ÷ ＋ ≠ ≒ μ α β γ δ Ω ∵ ∴ ∑ π Φ ∫ ∬ ∭ § ρ ∧ ∨ λ ′ ″"],
  ["特殊", "〰 ┌ ┬ ┐ ¿ ¡ ☑ ☒ ✓ ✗ © ® ™ ├ ┼ ┤ ↔ ↕ ↖ ↗ ↙ ↘ ← → ↑ ↓ └ ┴ ┘ ♫ ◉ ♀ ♂ ★ ■ ● ▲ ₩ ¥ £ € ☺ ☹ ♥ ♦ ♠ ♣"]
].map(([name, s], i) => ({ name, builtin: i, hidden: false, keys: s.split(" ") }));
const SK_MAX = 10;     // categories shown on the keyboard at once (hidden built-ins do not count)
const SK_NAME_MAX = 8; // a name's width in half-width cells - Chinese and emoji 2, letters and digits 1: the tabs fit
// what a reader sees as one character each - the rule settings.exe and both engines hold (engine/symbols.py _characters:
// a letter with its marks, an emoji with its joiners and modifiers, a flag's two halves), not the browser's: a key or a
// name the page allowed and the tool refused left the editor unable to save anything (findings #133)
const SK_EXTEND = [[0x0300, 0x036F], [0x1AB0, 0x1AFF], [0x1DC0, 0x1DFF], [0x200D, 0x200D], [0x20D0, 0x20FF], [0x3099, 0x309A],
  [0xFE00, 0xFE0F], [0xFE20, 0xFE2F], [0x1F3FB, 0x1F3FF], [0xE0020, 0xE007F]];
function skCharacters(s) {
  const out = [];
  let joined = false, flag = false;
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (SK_EXTEND.some(([a, b]) => cp >= a && cp <= b)) {
      if (out.length) out[out.length - 1] += ch; else out.push(ch);
      joined = cp === 0x200D;
      continue;
    }
    const ri = cp >= 0x1F1E6 && cp <= 0x1F1FF;
    if (out.length && (joined || (ri && flag))) { out[out.length - 1] += ch; flag = false; }
    else { out.push(ch); flag = ri; }
    joined = false;
  }
  return out;
}
const nameWidth = (parts) => parts.reduce((w, g) => w + (/^[\x20-\x7E]$/.test(g) ? 1 : 2), 0);
const graphemes = (s) => skCharacters(s).length;

function symbolsEditor() {
  let cats = SK_DEFAULT.map((c) => ({ ...c, keys: [...c.keys] }));
  let cur = 0, sel = -1, dragKey = -1, dragCat = -1, selOrig = "";
  const tabs = h("div", { class: "sk-tabs", role: "tablist", "aria-label": "符號鍵盤類別" });
  const bar = h("div", { class: "sk-bar" });
  const board = h("div", { class: "sk-board" });
  const edit = h("div", { class: "sk-edit" });
  const shown = () => cats.filter((c) => !c.hidden).length;
  // (settings.exe writes symbols.json, the IME reads it at its next key; a table it refuses: its message, the file
  // as it was)
  // (refused: the table it keeps is read back - the editor never shows what is not saved, findings #132 #133)
  const save = () => api("saveSymbols", { pages: cats }).then((r) => { if (r && r.error) { toast(r.error); load(); } });
  const load = () => api("symbols").then((d) => {
    cats = d.pages.map((c) => ({ ...c, keys: [...c.keys] }));
    cur = Math.min(cur, cats.length - 1);
    sel = -1;
    draw();
  });
  function draw() {
    const firstShown = cats.findIndex((c) => !c.hidden);
    tabs.replaceChildren(...cats.map((c, i) => {
      const chip = h("button", { class: "sk-chip" + (i === cur ? " on" : "") + (c.hidden ? " off" : ""), role: "tab", draggable: "true",
        title: c.hidden ? "已隱藏：符號鍵盤上不顯示" : "拖曳可調整順序", onclick: () => { cur = i; sel = -1; draw(); } },
        c.name || "（未命名）", c.hidden ? "（已隱藏）" : "", i === firstShown ? h("span", { class: "sk-first" }, "打開時") : "");
      chip.addEventListener("dragstart", (e) => { dragCat = i; e.dataTransfer.effectAllowed = "move"; });
      chip.addEventListener("dragover", (e) => { if (dragCat >= 0) { e.preventDefault(); chip.classList.add("drop"); } });
      chip.addEventListener("dragleave", () => chip.classList.remove("drop"));
      chip.addEventListener("drop", (e) => {
        e.preventDefault();
        if (dragCat < 0 || dragCat === i) return;
        const moved = cats.splice(dragCat, 1)[0];
        cats.splice(i, 0, moved);
        cur = i; dragCat = -1; save(); draw();
      });
      chip.addEventListener("dragend", () => { dragCat = -1; });
      return chip;
    }), h("button", { class: "sk-chip sk-add", disabled: shown() >= SK_MAX, title: shown() >= SK_MAX ? `最多顯示 ${SK_MAX} 個類別` : "",
      onclick: () => {
        cats.push({ name: "自訂", builtin: null, hidden: false, keys: Array(47).fill("") });
        cur = cats.length - 1; sel = -1; save(); draw();
      } }, "＋ 新增類別"));
    const c = cats[cur];
    // (a built-in's name stays: 一般／數學／特殊 always recognisable, 恢復預設 never surprising - the user 2026-11-24)
    let naming;
    if (c.builtin != null) {
      naming = [h("span", { class: "row-title" }, c.name)];
    } else {
      const name = h("input", { value: c.name, "aria-label": "類別名稱" });
      const note = h("span", { class: "row-desc" }, "最多 4 個中文字或 8 個英文字母");
      name.addEventListener("input", (e) => {
        // (counted as seen: an emoji is one; only this chip's label changes, so typing - and the IME's
        // composition - goes on uninterrupted; nothing cut while a syllable is still being composed)
        if (e.isComposing) return;
        let parts = skCharacters(name.value);
        if (nameWidth(parts) > SK_NAME_MAX) {
          while (nameWidth(parts) > SK_NAME_MAX) parts.pop();  // (cut what does not fit the tab)
          name.value = parts.join("");
          note.style.color = "var(--danger)";
        } else note.style.color = "";
        c.name = name.value.trim();
        if (c.name) save();  // (emptied while retyping: not saved - leaving the box puts the old name back)
        tabs.children[cur].firstChild.textContent = c.name || "（未命名）";
      });
      name.addEventListener("compositionend", () => name.dispatchEvent(new Event("input")));
      // (never empty: the keyboard's tab would show nothing - left empty, the name it had comes back; the user
      // 2026-11-24 asked whether an empty name is allowed)
      const before = c.name || "自訂";
      name.addEventListener("blur", () => {
        if (name.value.trim()) return;
        name.value = c.name = before;
        save();
        tabs.children[cur].firstChild.textContent = before;
        note.textContent = "名稱不能空白，已改回「" + before + "」";
        note.style.color = "var(--danger)";
      });
      naming = [h("span", { class: "row-desc" }, "名稱"), name, note];
    }
    bar.replaceChildren(...naming,
      h("span", { class: "spacer", style: "flex:1" }),
      c.builtin != null
        ? h("button", { class: "btn", disabled: !c.hidden && shown() <= 1, onclick: () => {
            if (c.hidden && shown() >= SK_MAX) { toast(`最多顯示 ${SK_MAX} 個類別`); return; }
            c.hidden = !c.hidden; save(); draw();
          } }, c.hidden ? "顯示這個類別" : "隱藏這個類別")
        : h("button", { class: "btn danger", disabled: shown() <= 1, onclick: async () => {  // (the last shown: #132)
            const ok = await dialog("刪除類別", h("p", { class: "row-desc", style: "font-size:14px" }, `確定要刪除「${c.name || "未命名"}」嗎？此操作無法復原。`),
              [{ text: "取消", value: false }, { text: "刪除", kind: "danger", value: true }]);
            if (!ok) return;
            cats.splice(cur, 1); cur = Math.max(0, cur - 1); sel = -1; save(); draw();
          } }, "刪除這個類別"));
    let k = 0;
    board.replaceChildren(...SK_ROWS.map((row, r) => h("div", { class: "sk-row", style: `padding-left:${SK_OFFSETS[r] * 52}px` },
      ...[...row].map((keyName) => {
        const idx = k++;
        const sym = c.keys[idx];
        const key = h("div", { class: "sk-key" + (idx === sel ? " sel" : "") + (sym ? "" : " empty"), draggable: "true",
          role: "button", tabindex: "0", "aria-label": `${keyName.toUpperCase()} 鍵：${sym || "空白"}` },
          h("i", {}, keyName.toUpperCase()), sym || "空");
        key.addEventListener("click", () => {
          if (sel !== idx) selOrig = c.keys[idx];  // (what it had when picked: 還原 until another key is picked)
          sel = idx; draw();
          const box = edit.querySelector("input");  // (the symbol there selected: what is typed replaces it)
          if (box) { box.focus(); box.select(); }
        });
        key.addEventListener("dragstart", (e) => { dragKey = idx; e.dataTransfer.effectAllowed = "move"; });
        key.addEventListener("dragover", (e) => { if (dragKey >= 0) { e.preventDefault(); key.classList.add("drop"); } });
        key.addEventListener("dragleave", () => key.classList.remove("drop"));
        key.addEventListener("drop", (e) => {
          e.preventDefault();
          if (dragKey < 0 || dragKey === idx) return;
          [c.keys[dragKey], c.keys[idx]] = [c.keys[idx], c.keys[dragKey]];
          sel = idx; selOrig = c.keys[idx]; dragKey = -1; save(); draw();
        });
        key.addEventListener("dragend", () => { dragKey = -1; });
        return key;
      }))));
    if (sel < 0) {
      edit.replaceChildren(h("span", { class: "row-desc" }, "點一下鍵可以改符號；把一個鍵拖到另一個鍵上可以對調。"));
    } else {
      const keyName = SK_ROWS.join("")[sel].toUpperCase();
      const input = h("input", { value: c.keys[sel], "aria-label": `${keyName} 鍵的符號` });
      input.addEventListener("focus", () => input.select());
      const msg = h("span", { class: "row-desc" }, "一個字或一個 emoji；清空表示這個鍵不打字");
      const undo = h("button", { class: "btn", disabled: c.keys[sel] === selOrig,
        title: `還原成「${selOrig || "空"}」` }, "還原");
      const put = (v) => {
        c.keys[sel] = v; save();
        const el = board.querySelectorAll(".sk-key")[sel];
        el.lastChild.textContent = v || "空"; el.classList.toggle("empty", !v);
        undo.disabled = v === selOrig;
      };
      input.addEventListener("input", () => {
        const v = input.value.trim();
        if (graphemes(v) > 1) { msg.textContent = "只能放一個字或一個 emoji（較長的內容請用「快速填入」）"; msg.style.color = "var(--danger)"; return; }
        msg.textContent = "一個字或一個 emoji；清空表示這個鍵不打字"; msg.style.color = "";
        put(v);
      });
      // (改錯了: back to what the key had when it was picked, the button only - the user 2026-11-24, Esc not)
      undo.addEventListener("click", () => {
        input.value = selOrig; put(selOrig);
        msg.textContent = "一個字或一個 emoji；清空表示這個鍵不打字"; msg.style.color = "";
        input.focus(); input.select();
      });
      edit.replaceChildren(h("span", { class: "row-title" }, `${keyName} 鍵`), input, undo, msg);
    }
  }
  draw();
  load();
  return h("div", { class: "row column" },
    h("div", { class: "row-text" },
      h("div", { class: "row-title" }, "符號鍵盤內容"),
      h("div", { class: "row-desc" }, "第一個顯示的類別是按「`」打開時看到的那頁，Tab 照順序切換。拖曳類別可以調整順序。")),
    h("div", { style: "width:100%" }, tabs, bar, board, edit,
      h("div", { class: "sk-foot" }, h("span", { class: "row-desc" }, `符號鍵盤上最多顯示 ${SK_MAX} 個類別；內建的類別只能隱藏，不會刪掉`),
        h("button", { class: "btn", onclick: async () => {
          const ok = await dialog("恢復預設", h("p", { class: "row-desc", style: "font-size:14px" },
            "確定要把符號鍵盤恢復成原本的「一般、數學、特殊」三頁嗎？自訂的類別與改過的符號都會清除。"),
            [{ text: "取消", value: false }, { text: "恢復預設", kind: "danger", value: true }]);
          if (!ok) return;
          await api("resetSymbols");  // (symbols.json removed: the built-in pages again)
          cur = 0;
          await load();
          toast("已恢復預設");
        } }, "恢復預設"))));
}

function pageLook() {
  const sizes = [[14, "小"], [18, "中"], [22, "大"], [26, "特大"]];
  const near = sizes.reduce((a, b) => (Math.abs(b[0] - S.config.cand_font_size) < Math.abs(a[0] - S.config.cand_font_size) ? b : a))[0];
  return [
    h("h1", {}, "外觀"),
    card(
      row("候選字視窗大小", "調整按 ↓ 選字時，候選字視窗與字體的顯示尺寸", segments(sizes, near, (v) => setConfig("cand_font_size", v), "候選字視窗大小")),
      ...candLayoutRows(),
      row("符號鍵盤位置", "按「`」叫出的符號鍵盤可自由拖曳並記憶位置",
        h("button", { class: "btn", onclick: async () => { await api("resetKeyboard"); toast("已重設，符號鍵盤下次將跟隨游標顯示"); } }, "重設為跟隨游標")),
      row("符號鍵盤選完不關", "開著時可以連續選符號，按 Esc 或 × 才關", cfgToggle("symbols_stay_open", "符號鍵盤選完不關"))),
    card(symbolsEditor()),
    lookCard(),
    h("p", { class: "foot" }, "預設外觀會自動跟隨 Windows 系統的主題模式；選「自訂」後改用自己的顏色與照片。")
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
  ["長句選字加強", "看整句意思選字，長句更準確", [
    ["整句選字", "用看整句意思的小型語言模型幫忙選字（預設開啟）；電腦較舊、打字覺得變慢時可以關閉。"],
    ["選字模型", "輕量、加強、最強三種，越強選字越準，每按一鍵也越慢；加強和最強可以選參考前面 32 或 64 個字。"],
    ["參考剛送出的字", "選字時也看剛送出、游標還緊接在後面的字，接著打的句子選得更準；最多看幾個字照前文長度。"]],
   ["typing", "長句選字加強"]],
  ["注音自動校正", "按錯鍵、漏打聲調也能打對", [
    ["自動改正", "開啟後（預設關閉），按到旁邊的鍵、漏打聲調、注音順序打錯、同一個鍵按兩次、ㄣ／ㄥ 或 ㄢ／ㄤ 打反，都會改成整句最通順的字（例如 si3cl3 是「你好」）。英文和個人詞庫的詞不會被改。"],
    ["改錯時", "在那個字按 ↓，裡面有您原本打的音的字；同樣的按鍵改回原樣 2 次，以後就照您打的讀。"]],
   ["typing", "注音自動校正"]],
  ["注音順序不限", "聲韻母順序打反也能正確辨識", [
    ["不限輸入順序", "輸入注音時，聲母、介音、韻母順序顛倒也能正確辨識，不用開啟注音自動校正\n（例如輸入 us3 與 su3 都能打出「你」）。"],
    ["要開注音自動校正的", "打反後開頭是 , . ; 這三個會打成標點的鍵，或整個音都是數字鍵的（例如 ;e 要打「剛」、824 要打「大」），要開啟「注音自動校正」才會重排。"]],
   ["typing", "注音自動校正"]],
  ["Tab 智慧補字", "即時預測字詞，按 Tab 一鍵補齊", [
    ["中文字詞預測", "打字時游標後方會浮現預測小框，按一下 Tab 即可採用，並支援連續按 Tab 補字。"],
    ["英文與 Email 補完", "輸入英文單字、自訂詞彙裡的英文或記憶過的 Email 前 3 個字元，同樣可按 Tab 補齊。"]],
   ["complete", "Tab 智慧補字"]],
  ["個人詞庫", "自訂詞彙與映射詞彙", [
    ["自訂詞彙", "人名、專有名詞等，打字時優先出現；也可以加注音文、英文字或中英夾雜的詞。打到詞的開頭就會提示整個詞，按 Tab 一次補完。"],
    ["映射詞彙", "替一段文字（地址、常用回覆）取個捷徑，打出捷徑後按 ↓ 就出現那段文字。"]],
   ["dict", "自訂詞彙"]],
  ["快速填入", "常用的 Email、電話一鍵填入", [
    ["叫出清單", "在「個人詞庫」頁存好常用的 Email、姓名、電話、地址，各取一個捷徑；打字時按 ` 再按空白叫出清單，↑ ↓ 選擇、Enter 送出。"],
    ["縮小清單", "叫出清單後接著打捷徑的開頭，就只列出符合的項目。"]],
   ["dict", "快速填入"]],
  ["符號鍵盤", "按 ` 鍵快速叫出各類特殊符號", [
    ["快速輸入特殊符號", "按鍵盤左上角的 ` 即可叫出符號鍵盤（Shift + ` 打出 ~）。"],
    ["視窗位置", "按住視窗空白處可自由拖曳，系統會自動記住您偏好的位置。"],
    ["連續選符號", "開啟「符號鍵盤選完不關」後，選完符號鍵盤照樣開著，按 Esc 或 × 才關。"]],
   ["look", "符號鍵盤位置"]],
  ["數字與符號", "自動判斷數字與標點符號情境", [
    ["符號直接輸出", "非注音或英文按鍵的標點符號，會直接輸出該符號（例：[、]）。"],
    ["智慧區分數字與聲調", "連續輸入 2026、100 會自動識別為數字；若有打聲調（如 18 加空白鍵）則視為注音。\n接在中文字後方的 , . ; 會當成標點符號。"]],
   null, "數字與標點"],
  ["全形與全形標點", "Shift + 空白切換全形，Ctrl + 標點打全形標點", [
    ["全形模式", "按 Shift + 空白鍵切換，英文字母、數字、標點和空白都打成全形（ＡＢＣ１２３），游標旁會顯示「全形」或「半形」。"],
    ["Ctrl + 標點", "中英混打時按住 Ctrl 再按 , . ; ? [ 這類標點鍵，打出 ，。；？「（預設開啟）；打字打到一半時，標點會接在字後面，前面的字還能回去改。"]],
   ["typing", "Ctrl + 標點打全形"]],
  ["簡體輸出", "注音照打，送出簡體字", [
    ["切換簡體", "在「打字」頁的「繁體／簡體輸出」切換成簡體，注音照打、送出簡體字；候選字第一個是大陸用詞（软体 → 软件），選過兩次就會記住。"],
    ["快捷鍵切換", "開啟「繁簡切換快捷鍵」後，打字時按 Ctrl + F1（可自訂）就能在繁體與簡體之間切換，游標旁會顯示「繁」或「簡」。"]],
   ["typing", "繁體／簡體輸出"]],
  ["全英模式", "按 Shift 可切換純英文模式", [
    ["切換純英文模式", "按 Shift 可切換至純英文模式，再按一次即可切回中英混打。"]],
   ["general", "Shift 切換輸入模式"], "全英模式切換"],
  ["框選查詢", "選取文字連按兩次 Ctrl 查注音", [
    ["查注音", "選取一段文字後連按兩次 Ctrl，跳出小視窗顯示注音（依詞判斷破音字），可以朗讀。"],
    ["加進詞庫", "查到的詞可以直接加進自訂詞彙或映射詞彙；不會動到剪貼簿。"]],
   ["dict", "連按 Ctrl 框選查詢"]],
  ["自訂外觀", "自訂顏色與照片", [
    ["換顏色", "在「外觀」頁的「自訂外觀」選「自訂」，挑一組現成配色，再微調背景、文字、選取框的顏色。"],
    ["放照片", "選一張照片當背景，每個視窗可以用不同的照片；「照片淡化」可以讓字更清楚，在預覽裡拖曳照片可以移動位置。"],
    ["分別調整", "在「全部」調整時，沒有分別調整過的視窗會一起變；在某個視窗的分頁調整後，它就不再跟著「全部」，可按「跟隨全部」恢復。"]],
   ["look", "自訂外觀"]],
  ["隱私與記憶", "輸入習慣僅存於本機，絕不外傳", [
    ["100% 本機端儲存", "您選過的字詞、常用英文與 Email 紀錄皆僅儲存在這台電腦中，絕不上傳雲端，保障隱私安全。"],
    ["自動淘汰舊紀錄", "長期未使用的字詞權重會自然降低；您也可以隨時至「補字與記憶」頁面逐筆刪除或清除所有紀錄。"]],
   ["complete", "已記憶項目"]],
  ["遊戲中 Shift 交給遊戲", "遊戲按鍵不被輸入法攔截", [
    ["按鍵直接給遊戲", "許多遊戲把 Shift 當成衝刺或技能鍵。在遊戲裡，Shift 和其他按鍵都直接交給遊戲，不會切換輸入模式，也不會被拿去組字（預設開啟）。"],
    ["自動偵測遊戲", "Windows 遊戲列識別的遊戲，或全螢幕獨佔的程式，都會自動套用。"],
    ["在遊戲裡打中文", "遊戲的輸入框若是 Windows 認得的一般文字框，點進去後可照常按 Shift 切換；大部分遊戲的聊天框是遊戲自己畫的，要在那裡打中文，請到「一般」頁關閉此功能。"]],
   ["general", "遊戲中 Shift 交給遊戲"]],
];
const FAQ = [
  ["打出來都是英文字母，打不出中文？", "請檢查工作列的輸入法圖示。若目前處於英文模式，請按一下 Shift 鍵（或用滑鼠左鍵點擊圖示）即可切回中文模式。", ["general", "Shift 切換輸入模式"]],
  ["系統自動選錯字了，該怎麼修改？", "在按 Enter 送出文字前，用方向鍵 ← → 將游標移到錯字上，按 ↓ 叫出候選字視窗並按數字鍵選字。系統會記住您的選擇，下次就會優先出現。"],
  ["想打數字「94」，卻變成中文字「愛」？", "有打聲調的數字會被當成注音。此時只需在該字上按 ↑，從上方替換列選擇原本的數字即可；或者直接使用鍵盤右側的數字鍵盤，輸出就一定會是數字。"],
  ["想打英文縮寫（如 pm、dj），卻被拼成中文？", "拼得成注音的小寫英文會被當成注音。可以用大寫打（PM），或按 Shift 切到純英文模式；已經變成中文的，在那個字前面按 ↑，從替換列選回原本的英文字母，選過的下次會記住。"],
  ["打字變慢、按鍵有延遲怎麼辦？", "選字模型越強，每按一鍵也越慢，較舊或省電的電腦較明顯。請到「打字」頁把「選字模型」改成「加強」或「輕量」；仍覺得慢，可以關閉「長句選字加強」。", ["typing", "選字模型"]],
  ["注音打錯（按到旁邊的鍵、漏打聲調）能自動改正嗎？", "可以。到「打字」頁開啟「注音自動校正」（預設關閉），按到旁邊的鍵、漏打聲調、注音順序打錯、同一個鍵按兩次、ㄣ／ㄥ 或 ㄢ／ㄤ 打反，都會改成整句最通順的字。英文和個人詞庫的詞不會被改；改錯了在那個字按 ↓ 選回原本的音，同樣的按鍵改回 2 次後就照您打的讀。", ["typing", "注音自動校正"]],
  ["前面剛打的句子，會影響接下來的選字嗎？", "會。輸入法本來就會參考剛送出的最後一個詞；「參考剛送出的字」開啟時，還會參考剛送出、游標還緊接在後面的更多字，接著打的句子選得更準。不需要的話可以在「打字」頁關閉「參考剛送出的字」。", ["typing", "參考剛送出的字"]],
  ["人名、專有名詞老是選錯？", "請到「個人詞庫」頁的「自訂詞彙」加入這個詞，打字時就會優先出現。注音可以按「自動填入注音」帶入，破音字再自己修改。", ["dict", "自訂詞彙"]],
  ["選錯的字或打錯的英文一直被記住、一直出現？", "選錯的字：在那個字按 ↓ 選對的字，系統會改記您選的。要刪掉某一筆記住的選字、英文字或 Email，請到「補字與記憶」頁的「記憶清單」搜尋、勾選後按「刪除」，下一個按鍵起就不再出現。很久沒用的紀錄會慢慢淡掉；要清掉所有打字記憶，按同一頁的「全部清除」（個人詞庫與設定會保留；記住的 Email 預設留著，勾選才一起清除）。", ["complete", "記憶清單"]],
  ["Email 要打幾次才會被記住？", "只要在中英混打模式下完整輸入並送出 1 次就會自動記住。下次只需打出開頭前 3 個字元，後方就會浮現完整提示，按 Tab 鍵即可補完。", ["complete", "自動記憶常用英文與 Email"]],
  ["英文單字要打幾次才會被記住？", "4 個字母以上的英文單字，在中英混打模式下輸入並送出滿 2 次就會記住，下次輸入前 3 個字母即可按 Tab 補完。（註：若按 Shift 切換至「純英文模式」下打字，則不會納入記憶。）", ["complete", "自動記憶常用英文與 Email"]],
  ["換電腦時，怎麼帶走打字習慣和個人詞庫？", "到「關於」頁的「匯出與匯入」，把選字記憶、個人詞庫和設定存成一個檔案，在新電腦匯入即可。匯入時可以勾選要匯入哪幾項：選字記憶和個人詞庫是合併，新電腦上已有的不會被刪除；勾選「設定」時，設定會換成檔案裡的。", ["about", "匯出與匯入"]],
  ["我打的字會被上傳嗎？", "不會。選字記憶、記住的英文字與 Email、個人詞庫和設定都只存在這台電腦；輸入法只有在您按「檢查更新」或「立即更新」時才會連網。", ["about", "本機學習紀錄與設定"]],
  ["怎麼打出簡體字？", "到「打字」頁的「繁體／簡體輸出」切換成簡體，注音照打、送出簡體字；也可以開啟「繁簡切換快捷鍵」，打字時按 Ctrl + F1（可自訂）隨時切換。", ["typing", "繁體／簡體輸出"]],
  ["怎麼快速輸入 Email、電話、地址？", "使用「個人詞庫」頁的「快速填入」：存好常用的資料並各取一個捷徑，打字時按 ` 再按空白叫出清單，↑ ↓ 選擇、Enter 送出。整段常用回覆也可以用「映射詞彙」取個捷徑，打出捷徑後按 ↓。", ["dict", "快速填入"]],
  ["怎麼打出 ★ ※ ← 這類符號？", "按 ` 叫出符號鍵盤（一般／數學／特殊三頁，Tab 換頁），按鍵盤上對應的鍵或用滑鼠點選即可。預設連按兩下 ` 打出 `；改過第一頁 ` 鍵的話，打出改後的符號。每個鍵的符號、類別與順序可以在「外觀」頁的「符號鍵盤內容」自訂，emoji 也行。", ["look", "符號鍵盤內容"]],
  ["程式裡的 Ctrl + , 這類快捷鍵沒反應？", "「Ctrl + 標點打全形」開啟時，中英混打模式下的 Ctrl + , . ; 等組合會打出全形標點，程式用這些組合的快捷鍵就會失效。需要的話請到「打字」頁關閉。", ["typing", "Ctrl + 標點打全形"]],
  ["輸入法的視窗太大或太小，想換顏色或照片？", "「外觀」頁可以調整候選字視窗大小，排成一欄、一排或格子；「自訂外觀」可以換顏色或放照片，每個視窗都能分別調整。", ["look", "候選字視窗大小"]],
  ["怎麼更新到新版？", "到「關於」頁按「檢查更新」，有新版時按「立即更新」，就會下載並更新。", ["about", "檢查更新"]],
  ["玩遊戲時按鍵被輸入法攔截怎麼辦？", "「一般」頁的「遊戲中 Shift 交給遊戲」預設開啟，遊戲裡的按鍵會直接交給遊戲。若仍被攔截，可能是 Windows 沒有把它認成遊戲（請改用全螢幕，或讓 Windows 遊戲列記住它）。", ["general", "遊戲中 Shift 交給遊戲"]],
  ["遊戲聊天框打不出中文？", "大部分遊戲的聊天框是遊戲自己畫的，輸入法分辨不出那是輸入框。請到「一般」頁關閉「遊戲中 Shift 交給遊戲」，遊戲就和一般程式一樣可以按 Shift 切換打中文。", ["general", "遊戲中 Shift 交給遊戲"]],
  // (#36 plan 7: the Japanese topics the user picked, 2026-11-26)
  ["怎麼打日文？", "到「日文」頁打開「日文輸入」，之後按 Ctrl + F2 在中文和日文之間切換。在日文時單按 Shift，可以在「中日混打」（注音照樣打中文，羅馬字轉成日文）和「純日文」之間切換。直接打羅馬字，邊打邊轉成漢字與假名，按 Enter 送出。", ["japanese", "日文輸入"]],
  ["日文選錯漢字怎麼改？會記住嗎？", "把游標移到那個詞前面按 ↓，選對的寫法。選過的會記住，下次同樣的讀音直接出現；但前後文明顯要另一種寫法時，會照前後文（例如選過「箸」，打「橋を渡る」還是「橋」）。要不管前後文都出現某個寫法，請加進「個人詞庫」日文區的「自訂詞彙」。", ["dict", "自訂詞彙", 1, "ja"]],
  ["怎麼打成片假名或平假名？", "平假名、片假名本來就照詞自動決定。要換某個詞，把游標移到那個詞上按 F6（平假名）、F7（全形片假名）或 F8（半形片假名）；要整段只打平假名或片假名，按 Ctrl + F6 或 Ctrl + F7，按 Ctrl + F8 回到自動判斷。這些熱鍵都可以在「日文」頁更改。", ["japanese", "F6／F7／F8 轉換", 0, "ja"]],
  ["中日混打時打不出英文？", "中日混打模式下，英文字母都當成羅馬字轉成日文，不會打出英文。要打英文，請按 Ctrl + F2 切回中文的中英混打。", ["japanese", "中文／日文切換", 0, "ja"]],
  ["日文的人名、專有名詞老是選錯？", "打開日文輸入後，到「個人詞庫」頁日文區的「自訂詞彙」加入這個詞和它的讀音（平假名），打出讀音就是這個詞；同一個讀音可以加好幾個詞。也可以選取文字後連按兩次 Ctrl，在框選查詢按「加入自訂詞彙（日文）」直接加入。", ["dict", "自訂詞彙", 1, "ja"]],
  ["打日文覺得變慢？", "「日文」頁的「長句選字加強」可以單獨關掉，只影響日文，中文不受影響。關掉後選字比較不看整句的意思，可能比較常需要按 ↓ 改字。", ["japanese", "長句選字加強", 0, "ja"]]
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

function faqOne(e) {  // (toggle does not bubble: each question has it)
  const d = e.currentTarget;
  if (!d.open) return;
  for (const o of d.parentNode.querySelectorAll("details.faq[open]")) if (o !== d) o.open = false;
}

function pageFaq() {
  return [
    h("h1", {}, "常見問題"),
    // (one open at a time: opening one closes the one open before, the user 2026-11-26)
    h("div", { class: "card" }, ...FAQ.map(([q, a, link]) => h("details", { class: "faq", ontoggle: faqOne }, h("summary", {}, q), h("p", {}, a),
      link ? h("div", { class: "faq-go" }, h("button", { class: "btn", onclick: () => follow(link) }, goText(link))) : null))),
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
    ["shortcuts", `映射詞彙（${info.shortcuts} 筆）`, info.shortcuts > 0], ["quickFill", `快速填入（${info.quickFill || 0} 筆）`, info.quickFill > 0],
    // (#77: the look and the symbol keyboard of their own - 設定 no longer replaces them)
    ["appearance", info.photos ? `自訂外觀（含 ${info.photos} 張照片）` : "自訂外觀", info.appearance],
    ["symbols", "符號鍵盤", info.symbols],
    ["config", "設定", info.config]];
  const boxes = {};
  const body = h("div", {},
    h("p", { class: "row-desc", style: "font-size:14px" }, `匯出時間：${info.created}。記憶與詞庫會和目前的資料合併；勾選的自訂外觀、符號鍵盤與設定會換成檔案裡的。`),
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

// ---- 日文 (#36 design, being discussed 2026-11-24): a page of its own - the user "日文要獨立一個頁面" ----------
function keyBoxFor(key, label) {
  // a combination key read from the keyboard, as 繁簡切換快捷鍵 (scriptRows)
  const box = h("button", { class: "text-input key-box", type: "button", "aria-label": label }, S.config[key]);
  let listening = false;
  const stop = () => { listening = false; box.textContent = S.config[key]; };
  box.addEventListener("click", () => { listening = true; box.textContent = "請按下組合鍵…"; });
  box.addEventListener("blur", stop);
  box.addEventListener("keydown", async (e) => {
    if (!listening) return;
    e.preventDefault();
    if (e.key === "Escape") return stop();
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
    const code = /^Key([A-Z])$/.exec(e.code) || /^Digit([0-9])$/.exec(e.code) || /^(F(?:[1-9]|1[0-2]))$/.exec(e.code);
    const parts = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win", code ? code[1] : e.key].filter(Boolean);
    try {  // (a failed save says so and puts the box back, as scriptRows - findings #226)
      S.config = await api("setConfig", { key, value: parts.join("+") });
      toast("設定已儲存並自動套用");
    } catch (err) {
      toast(err && err.message ? err.message : "儲存失敗，請再試一次");
    } finally {
      stop();
    }
  });
  return box;
}

function pageJapanese() {
  const on = S.config.ja_enabled;
  const master = toggle(on, async (v) => { await setConfig("ja_enabled", v); show("japanese"); }, { label: "日文輸入" });
  // (off as it comes - existing users unchanged, the user 2026-11-24: the rest is shown but can't be used until it is on)
  // (...and not by the keyboard either: its switches, boxes and buttons disabled - findings #227)
  const rest = (...nodes) => {
    const box = h("div", { class: on ? "" : "ja-off", "aria-disabled": on ? "false" : "true",
      style: on ? "" : "opacity:.45;pointer-events:none" }, ...nodes);
    if (!on) for (const el of box.querySelectorAll("input, button, select, textarea")) el.disabled = true;
    return box;
  };
  return [
    h("h1", {}, "日文"),
    card(row("日文輸入", "用羅馬字打日文，邊打邊轉成漢字與假名。開啟後可以用熱鍵切換到中日混打與純日文。", master)),
    rest(
      h("h2", {}, "切換設定"),
      // (one key between Chinese and Japanese, Shift as in Chinese: 中日混打 <-> 純日文 - the user 2026-11-25, after the
      // two direct keys of 2026-11-24; one key each for 平／片／自動, a hint by the caret that fades, as 繁／簡)
      card(
        row("中文／日文切換", "按一下在中文和日文之間切換，回到那邊上次的模式。在日文時，Shift 在中日混打和純日文之間切換，就像中文的中英混打和純英文。開啟後，程式中相同組合的快捷鍵會失效。",
          keyBoxFor("ja_switch_key", "中文／日文切換的組合鍵"))),
      h("h2", {}, "平假名與片假名"),
      card(
        row("只打平假名", "在日文時，之後打的日文都出平假名，直到按「自動判斷」。切換時游標旁會短暫顯示「あ」。", keyBoxFor("ja_hira_key", "只打平假名的組合鍵")),
        row("只打片假名", "之後打的日文都出片假名，直到按「自動判斷」；工作列的圖示也會變成片假名。切換時游標旁會短暫顯示「ア」。", keyBoxFor("ja_kata_key", "只打片假名的組合鍵")),
        row("自動判斷", "平假名、片假名照詞自動決定（預設）。切回中文時也會回到自動判斷。切換時游標旁會短暫顯示「自動」。", keyBoxFor("ja_auto_key", "自動判斷的組合鍵")),
        row("F6／F7／F8 轉換", "按 F6、F7、F8 把游標所在的詞換成平假名、全形片假名、半形片假名。開啟後，程式中的 F6、F7、F8 在打日文時會失效。",
          cfgToggle("ja_fkeys", "F6／F7／F8 轉換"))),
      h("h2", {}, "純日文"),
      card(
        row("空白鍵轉換", "按空白鍵列出游標所在這個詞的候選，再按一下換下一個。關閉時空白鍵打出空格。",
          cfgToggle("ja_space_convert", "空白鍵轉換"))),
      // (one hint after the caret, Tab completes it, as 中文 Tab 智慧補字; another one: caret before it, ↓ - the user 2026-11-24)
      h("h2", {}, "預測"),
      card(
        row("預測提示", "邊打邊在游標後方提示最可能接下來的詞或句子，按 Tab 補完；要換別的，把游標移到詞前面按 ↓。",
          cfgToggle("ja_predict", "預測提示"))),
      // (#36 plan 7, #89: the Japanese model's switch and, under it, 參考剛送出的字 - as the Chinese 輕量: one model, its
      // context fixed at 32; 選字模型 and 前文長度 come with more tiers, #85)
      h("h2", {}, "選字"),
      card(...jaLongSentenceRows()))
  ];
}

function jaLongSentenceRows() {
  const sub = toggle(S.config.ja_lm_context, (v) => setConfig("ja_lm_context", v),
    { label: "參考剛送出的字", disabled: !S.config.ja_lm });
  const subRow = row("參考剛送出的字", "選字時也看剛送出、游標還緊接在後面的日文，最多看 32 個字。", sub);  // (#85: more tiers, as the Chinese one)
  subRow.classList.add("row-sub");
  const grey = (off) => { sub.querySelector("input").disabled = off; subRow.classList.toggle("off", off); };
  const main = toggle(S.config.ja_lm, (v) => { setConfig("ja_lm", v); grey(!v); }, { label: "長句選字加強" });
  grey(!S.config.ja_lm);
  return [row("長句選字加強", "看整句意思挑日文的寫法，長句更準確。只影響日文；中文的在「打字」頁。", main), subRow];
}

// 日文詞彙 (#36 design, 単語登録): a word and its reading in hiragana
async function editJaWords() {
  // (#36 plan 7: ja_dictionary.json through jaWords / saveJaWords, checked as Settings.cs checks them)
  const saved = await api("jaWords");
  const tbody = h("tbody");
  const error = h("div", { class: "error-line", role: "alert" });
  const HIRAGANA = /^[ぁ-ゖー]+$/;
  function addRow(a = "", b = "") {
    const ia = h("input", { value: a, "aria-label": "詞彙", maxlength: 30 });
    const ib = h("input", { value: b, "aria-label": "讀音（平假名）", placeholder: "例：すずきいちろう", maxlength: 30 });
    const tr = h("tr", {}, h("td", { style: "width:42%" }, ia), h("td", {}, ib),
      h("td", { style: "width:36px" }, h("button", { class: "icon-btn", "aria-label": "刪除這一行", onclick: () => tr.remove() }, "✕")));
    tbody.append(tr);
    return ia;
  }
  for (const [a, b] of saved || []) addRow(a, b);
  const rows = () => [...tbody.rows].map((r) => [r.cells[0].firstChild.value.trim(), r.cells[1].firstChild.value.trim()])
    .filter(([a, b]) => a || b);
  const body = h("div", {},
    h("p", { class: "row-desc" }, "打日文時，打出讀音就是這個詞。同一個讀音可以有好幾個詞，打出排在前面的，其他的按 ↓ 選，選過的下次直接出現。讀音請用平假名（長音用「ー」）。"),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "詞彙"), h("th", {}, "讀音（平假名）"), h("th"))), tbody),
    h("button", { class: "btn", style: "margin-top:8px", onclick: () => addRow().focus() }, "＋ 新增一筆"),
    error);
  await dialog("自訂詞彙（日文）", body, [
    { text: "取消", value: false },
    { text: "儲存", kind: "primary", click: async (close) => {
      const list = rows();
      const bad = list.find(([a, b]) => !a || !HIRAGANA.test(b));
      if (bad) { error.textContent = !bad[0] ? "有一行沒有填詞彙" : `「${bad[0]}」的讀音只能用平假名（長音用ー）`; return; }
      const r = await api("saveJaWords", { rows: list });
      if (r && r.error) { error.textContent = r.error; return; }
      S.jaWords = r;
      toast("已儲存");
      close(true);
      show("dict");
    } }
  ]);
}

const BUILD = { general: pageGeneral, typing: pageTyping, complete: pageComplete, dict: pageDict, japanese: pageJapanese, look: pageLook, help: pageHelp, faq: pageFaq, about: pageAbout };
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
