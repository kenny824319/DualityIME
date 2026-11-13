// 自訂外觀 (#22, docs/superpowers/specs/2026-11-13-custom-appearance-design.md): the card of the 外觀 page.
// config.json "appearance": {on, preset, bg, text, select, all: {image, fade, zoom, x, y}, windows: {key: {...}}};
// {} = the default look. The preview draws the photos the way the IME does (cpp/tip/appearance.cpp).

const LOOK_PRESETS = [
  ["奶茶", "#f3e7d9", "#4a3426", "#e08a4c"], ["夜空", "#1d2340", "#e6e9ff", "#6c7cff"],
  ["抹茶", "#e7efe0", "#2f4a2a", "#7aa35c"], ["櫻花", "#fdeef2", "#5a2a3a", "#e66f93"],
  ["墨黑", "#151515", "#f0f0f0", "#3a82f6"]];
// [key, tab name, reference size (DIP) the photo's scale comes from: as cpp/tip/appearance.cpp REFS]
const LOOK_WINDOWS = [
  ["cand", "候選字視窗", 220, 300], ["tip", "補字提示", 200, 44], ["mode", "中英提示", 56, 48],
  ["fill", "快速填入清單", 360, 240], ["keyboard", "符號鍵盤", 600, 280], ["bar", "替換列", 420, 44]];
const LOOK_NEW_PHOTO = { image: "", fade: 45, zoom: 100, x: 0.5, y: 0.5 };

function lookLuminance(hex) {
  const v = parseInt(hex.slice(1), 16);
  const lin = (c) => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * lin((v >> 16) & 255) + 0.7152 * lin((v >> 8) & 255) + 0.0722 * lin(v & 255);
}
function lookMix(a, b, t) {
  const x = parseInt(a.slice(1), 16), y = parseInt(b.slice(1), 16);
  const ch = (s) => Math.round(((x >> s) & 255) + ((((y >> s) & 255) - ((x >> s) & 255)) * t));
  return "#" + ((ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).padStart(6, "0");
}
// the window roles from the three colors (cpp/tip/appearance.cpp derive_theme)
function lookTheme(a) {
  const shade = (c, t) => lookMix(c, lookLuminance(c) > 0.5 ? "#000000" : "#ffffff", t);
  return { bg: a.bg, text: a.text, select: a.select, selectText: lookLuminance(a.select) > 0.55 ? "#000000" : "#ffffff",
    label: lookMix(a.text, a.bg, 0.45), border: lookMix(a.text, a.bg, 0.75), key: shade(a.bg, 0.06), keyBorder: shade(a.bg, 0.12),
    veil: lookLuminance(a.text) > 0.5 ? "#000000" : "#ffffff" };
}

function lookCard() {
  const urls = {};  // photo file -> data: URL
  const stored = S.config.appearance || {};
  let A = JSON.parse(JSON.stringify(stored));
  let tab = "all";
  const body = h("div", { class: "look-custom" });
  const save = () => setConfig("appearance", A);
  function fresh() {
    const [, bg, text, select] = LOOK_PRESETS[0];
    return { on: true, preset: 0, bg, text, select, all: { ...LOOK_NEW_PHOTO }, windows: {} };
  }
  function filled() {  // (fields missing or bad: as the IME reads them)
    const f = fresh();
    for (const k of ["bg", "text", "select"]) if (!/^#[0-9a-fA-F]{6}$/.test(A[k] || "")) A[k] = f[k];
    if (!Number.isInteger(A.preset)) A.preset = 0;
    A.all = { ...LOOK_NEW_PHOTO, ...(A.all || {}) };
    if (!A.windows || typeof A.windows !== "object") A.windows = {};
  }
  const lookOf = (key) => A.windows[key] || A.all;
  const imageOf = (key) => (A.windows[key] && typeof A.windows[key].image === "string" ? A.windows[key].image : A.all.image);
  async function url(file) {
    if (!file) return null;
    if (!(file in urls)) urls[file] = await api("imageUrl", { file }).catch(() => null);
    return urls[file];
  }
  // the one being adjusted: 全部, or a window's own (made from 全部 the first time)
  function editing() {
    if (tab === "all") return A.all;
    if (!A.windows[tab]) { const { fade, zoom, x, y } = A.all; A.windows[tab] = { fade, zoom, x, y }; }
    return A.windows[tab];
  }

  const mode = segments([[false, "預設"], [true, "自訂"]], !!A.on, (v) => {
    if (v && !A.bg) A = fresh();
    A.on = v;
    filled();
    save();
    draw();
  }, "自訂外觀");

  function swatches() {
    const box = h("div", { class: "look-swatches" });
    LOOK_PRESETS.forEach(([name, bg, text, select], i) => {
      const b = h("button", { class: "look-swatch" + (A.preset === i ? " on" : ""), type: "button", "aria-label": name },
        h("span", { class: "look-swatch-top", style: `background:${bg};color:${text}` },
          h("span", { style: `background:${select};color:${lookLuminance(select) > 0.55 ? "#000" : "#fff"}` }, "你")),
        h("span", { class: "look-swatch-name" }, name));
      b.addEventListener("click", () => { Object.assign(A, { preset: i, bg, text, select }); save(); draw(); });
      box.append(b);
    });
    return box;
  }
  function colorInput(key, label) {
    const input = h("input", { type: "color", value: A[key], "aria-label": label });
    input.addEventListener("input", () => { A[key] = input.value; drawPreview(); });
    input.addEventListener("change", () => { A[key] = input.value; save(); });
    return h("label", { class: "look-color" }, label, input);
  }

  function tabs() {
    const box = h("div", { class: "look-tabs", role: "tablist", "aria-label": "調整哪個視窗" });
    for (const [key, name] of [["all", "全部"], ...LOOK_WINDOWS]) {
      const b = h("button", { class: "look-tab" + (tab === key ? " on" : ""), type: "button", role: "tab", "aria-selected": tab === key },
        name, A.windows[key] ? h("span", { class: "look-dot", "aria-label": "分別調整過" }) : null);
      b.addEventListener("click", () => { tab = key; draw(); });
      box.append(b);
    }
    return box;
  }

  function photoRows() {
    const look = tab === "all" ? A.all : (A.windows[tab] || {});
    const own = tab === "all" ? !!A.all.image : typeof look.image === "string";
    // the photo shown by the name the user's file had (a window without its own: the one of 全部; 移除 tells them apart)
    const shown = (l) => (l.image ? l.name || l.image : "");
    const name = (own ? shown(look) : shown(A.all)) || "未選擇";
    const pick = h("button", { class: "btn", type: "button" }, "選擇圖片");
    pick.addEventListener("click", async () => {
      let r = null;
      try { r = await api("pickImage", { slot: tab }); } catch (e) { toast(e && e.message ? e.message : "無法讀取這張圖片"); }
      if (!r) return;
      urls[r.file] = r.url;
      delete sizes[r.file];  // (the same file name, another photo)
      Object.assign(editing(), { image: r.file, name: r.name || r.file });
      save();
      draw();
    });
    const remove = h("button", { class: "btn", type: "button", disabled: !own }, "移除");
    remove.addEventListener("click", async () => {
      await api("removeImage", { slot: tab }).catch(() => null);
      if (tab === "all") Object.assign(A.all, { image: "", name: "" });
      else { delete A.windows[tab].image; delete A.windows[tab].name; }
      save();
      draw();
    });
    const cur = lookOf(tab === "all" ? "" : tab);
    const slider = (label, min, max, field, unit) => {
      const value = h("span", { class: "look-value" }, `${Math.round(cur[field])}${unit}`);
      const input = h("input", { type: "range", min, max, value: Math.round(cur[field]), "aria-label": label });
      input.addEventListener("input", () => { editing()[field] = +input.value; value.textContent = `${input.value}${unit}`; drawPreview(); drawTabs(); });
      // (the first change of a window's own: drawn again so 跟隨全部 shows at once; else the slider keeps
      // the focus, its arrow keys fire change on every step)
      const had = tab === "all" || !!A.windows[tab];
      input.addEventListener("change", () => { save(); if (!had) draw(); });
      return h("span", { class: "look-slider" }, input, value);
    };
    const reset = [];
    if (tab !== "all" && A.windows[tab]) {
      const b = h("button", { class: "btn", type: "button" }, "跟隨全部");
      b.addEventListener("click", async () => {
        if (typeof A.windows[tab].image === "string") await api("removeImage", { slot: tab }).catch(() => null);
        delete A.windows[tab];
        save();
        draw();
      });
      reset.push(b);
    } else if (tab === "all" && Object.keys(A.windows).length) {
      const b = h("button", { class: "btn", type: "button" }, "全部跟隨");
      b.addEventListener("click", async () => {
        for (const k of Object.keys(A.windows))
          if (typeof A.windows[k].image === "string") await api("removeImage", { slot: k }).catch(() => null);
        A.windows = {};
        save();
        draw();
      });
      reset.push(b);
    }
    return [
      row("照片", name, pick, remove),
      row("照片淡化", null, slider("照片淡化", 0, 85, "fade", "%")),
      row("縮放", null, slider("縮放", 100, 300, "zoom", "%"), ...reset)];
  }

  // ---- the preview: the seven windows, a photo dragged to move it --------------------------------------------
  const preview = h("div", { class: "look-preview" });
  function contents(key, th) {
    const sel = `background:${th.select};color:${th.selectText}`;
    const item = (n, t, on) => `<div class="lp-c"${on ? ` style="${sel}"` : ""}><b style="color:${on ? th.selectText : th.label}">${n}</b>${t}</div>`;
    switch (key) {
      case "cand": return `<div class="lp-title" style="color:${th.label}">候選字</div>` + item(1, "你", true) + item(2, "妳") + item(3, "擬") + item(4, "禰");
      case "tip": return `<div class="lp-c">時間 <span style="color:${th.label}">Tab</span></div>`;
      case "mode": return `<div class="lp-c lp-big">中</div>`;
      case "fill": return `<div class="lp-title" style="color:${th.label}">快速填入</div>` +
        `<div class="lp-c lp-row">sample@example.com<span style="color:${th.label}">[email]</span></div>` +
        `<div class="lp-c lp-row" style="${sel}">王小明<span>[name1]</span></div>` +
        `<div class="lp-c lp-row">John<span style="color:${th.label}">[name2]</span></div>`;
      case "keyboard": {
        const k = (l, s, on) => `<div class="lp-key" style="${on ? sel : ""}"><i>${l}</i>${s}</div>`;
        return `<div class="lp-tabs"><span style="${sel}">一般</span><span>數學</span><span>特殊</span></div>` +
          `<div class="lp-keys">${k("Q", "《")}${k("W", "》")}${k("E", "「", true)}${k("R", "」")}${k("T", "『")}${k("Y", "』")}${k("U", "【")}${k("I", "】")}</div>` +
          `<div class="lp-keys">${k("A", "※")}${k("S", "＊")}${k("D", "○")}${k("F", "●")}${k("G", "，")}${k("H", "、")}${k("J", "。")}${k("K", "；")}</div>`;
      }
      case "bar": return `<div class="lp-bar">${["ㄋㄧˇ", "s以", "suˇ", "su3"].map((t, i) => `<span${i === 0 ? ` style="${sel}"` : ""}>${t}</span>`).join("")}</div>`;
      default: return "";
    }
  }
  // the preview's size of each window (DIP) and how much smaller it is drawn
  const PREVIEW = { cand: [150, 168, 1], tip: [96, 30, 1], mode: [40, 40, 1], fill: [270, 122, 1], keyboard: [600, 170, 0.5], bar: [300, 44, 1] };
  const sizes = {};  // image file -> [w, h]
  async function photoSize(file) {
    if (sizes[file]) return sizes[file];
    const u = await url(file);
    if (!u) return null;
    return new Promise((done) => {
      const img = new Image();
      img.onload = () => done((sizes[file] = [img.naturalWidth, img.naturalHeight]));
      img.onerror = () => done(null);
      img.src = u;
    });
  }
  // where the photo goes in a window w x h (DIP) - cpp/tip/appearance.cpp draw_background
  function place(ref, look, w, h, iw, ih) {
    let k = Math.max(ref[0] / iw, ref[1] / ih) * look.zoom / 100;
    k = Math.max(k, w / iw, h / ih);
    const dw = iw * k, dh = ih * k;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    return { left: clamp(ref[0] / 2 - look.x * dw, w - dw, 0), top: clamp(ref[1] / 2 - look.y * dh, h - dh, 0), dw, dh };
  }
  let drawing = 0;
  async function drawPreview() {
    const n = ++drawing;
    const th = lookTheme(A);
    const boxes = [];
    for (const [key, name, rw, rh] of LOOK_WINDOWS) {
      const [w, h0, f] = PREVIEW[key];
      const look = lookOf(key), file = imageOf(key);
      const size = file ? await photoSize(file) : null;
      if (n !== drawing) return;
      const win = h("div", { class: "lp-win" + (key === "keyboard" ? " lp-keyboard" : "") + (tab === key || (tab === "all" && !A.windows[key]) ? " lp-cur" : ""),
        style: `width:${w * f}px;height:${h0 * f}px;background:${key === "keyboard" ? th.bg : th.bg};color:${th.text};border-color:${th.border}` });
      win.style.setProperty("--key", size ? "rgba(255,255,255," + (lookLuminance(A.text) > 0.5 ? 0.14 : 0.55) + ")" : th.key);
      // (the keys' border over a photo as the IME draws it: grey under light text, white under dark)
      win.style.setProperty("--key-border", size ? (lookLuminance(A.text) > 0.5 ? "#787878" : "#ffffff") : th.keyBorder);
      if (size) {
        const p = place([rw, rh], look, w, h0, size[0], size[1]);
        win.append(h("img", { class: "lp-photo", src: urls[file], alt: "", draggable: "false",
          style: `left:${p.left * f}px;top:${p.top * f}px;width:${p.dw * f}px;height:${p.dh * f}px` }));
        win.append(h("div", { class: "lp-veil", style: `background:${th.veil};opacity:${look.fade / 100}` }));
        win.addEventListener("mousedown", (e) => drag(e, key, [rw, rh], w, h0, f, size));
      }
      const inner = h("div", { class: "lp-in", style: `transform:scale(${f});transform-origin:0 0;width:${w}px` });
      inner.innerHTML = contents(key, th);
      win.append(inner);
      boxes.push(h("div", { class: "lp-box" }, win, h("div", { class: "lp-name" }, name)));
    }
    // the zhuyin tip: one block of the selection color, no photo
    boxes.push(h("div", { class: "lp-box" },
      h("div", { class: "lp-win lp-zhuyin", style: `background:${th.select};color:${th.selectText}` }, "ㄋㄧ"),
      h("div", { class: "lp-name" }, "注音提示")));
    preview.replaceChildren(...boxes);
  }
  function drag(e, key, ref, w, h0, f, size) {
    if (tab !== "all" && tab !== key) return;
    if (tab === "all" && A.windows[key]) return;  // (adjusted separately: on its own tab)
    e.preventDefault();
    let target = null;  // (made on the first move: a click alone adjusts nothing)
    let x = e.clientX, y = e.clientY;
    const move = (m) => {
      target = target || editing();
      const p = place(ref, target, w, h0, size[0], size[1]);
      target.x = Math.min(1, Math.max(0, target.x - (m.clientX - x) / f / p.dw));
      target.y = Math.min(1, Math.max(0, target.y - (m.clientY - y) / f / p.dh));
      x = m.clientX; y = m.clientY;
      drawPreview();
    };
    const up = () => {
      removeEventListener("mousemove", move);
      removeEventListener("mouseup", up);
      if (target) { save(); draw(); }
    };
    addEventListener("mousemove", move);
    addEventListener("mouseup", up);
  }

  let tabsBox = null;
  function drawTabs() { if (tabsBox) { const t = tabs(); tabsBox.replaceWith(t); tabsBox = t; } }
  function draw() {
    // (the page stays where it is: the card is made again, and the page scrolls smoothly - clicking a tab or a
    // swatch made it jump up, 2026-11-13 user)
    // (only while the card is on the page: drawn for a page being opened, main still scrolls the page before it)
    const main = body.isConnected ? document.getElementById("main") : null;
    const top = main ? main.scrollTop : 0;
    const keep = () => { if (main && main.scrollTop !== top) main.scrollTo({ top, behavior: "instant" }); };
    if (!A.on) { body.replaceChildren(); keep(); return; }
    filled();
    tabsBox = tabs();
    const resetColors = h("button", { class: "btn", type: "button" }, "還原顏色");
    resetColors.addEventListener("click", () => {
      const [, bg, text, select] = LOOK_PRESETS[A.preset] || LOOK_PRESETS[0];
      Object.assign(A, { bg, text, select });
      save();
      draw();
    });
    body.replaceChildren(
      row("現成配色", "挑一組再微調下面的顏色", swatches()),
      row("顏色", null, h("span", { class: "look-colors" }, colorInput("bg", "背景"), colorInput("text", "文字"), colorInput("select", "選取框")), resetColors),
      h("div", { class: "look-tabs-row" }, tabsBox),
      ...photoRows(),
      h("div", { class: "look-preview-wrap" },
        // (only with a photo: there is nothing to move without one)
        [A.all.image, ...Object.values(A.windows).map((w) => w.image)].some((f) => f) ?
          h("div", { class: "look-hint" }, "在預覽裡拖曳照片，可以調整照片的位置") : null,
        preview));
    keep();
    drawPreview().then(keep);
  }
  draw();
  return card(
    row("自訂外觀", "候選字視窗、補字提示、中英提示、快速填入清單、符號鍵盤、替換列與注音提示，換成自己的顏色或照片", mode),
    body);
}
