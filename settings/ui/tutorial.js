// 使用說明的演示動畫：四課，自動一格一格播放（按鍵、組字、候選字、替換列、補字小框），播完換下一課。
"use strict";

const LESSONS = (() => {
  const typing = (keys, comps) => keys.map((k, i) => ({ key: k, comp: comps[i] }));
  return [
    {
      tab: "連續輸入",
      title: "連續輸入",
      text: "直接照注音把整句話打完，無須逐字選字；系統會根據前後文自動挑選最精準的字詞，按 Enter 即可送出。",
      frames: typing(["s", "u", "3", "c", "l", "3", "a", "8", "7"],
        ["s", "su", "你", "你c", "你cl", "你好", "你好a", "你好a8", "你好嗎"])
        .concat([{ key: "Enter", sent: "你好嗎", comp: "" }])
    },
    {
      tab: "中英混打",
      title: "中英無縫混打",
      text: "無須按 Shift 切換，直接輸入英文單字會自動保留為英文，後方接著打注音仍會正常轉為中文。",
      frames: typing(["j", "i", "3", "m", "/", "4", "g", "o", "o", "g", "l", "e", "n", ".", "空白", "v", "m", "p", "6"],
        ["j", "ji", "我", "我m", "我m/", "我用", "我用g", "我用go", "我用goo", "我用goog", "我用googl", "我用google",
         "我用googlen", "我用googlen.", "我用google搜", "我用google搜v", "我用google搜vm", "我用google搜vmp", "我用google搜尋"])
        .concat([{ key: "Enter", sent: "我用google搜尋", comp: "" }])
    },
    {
      tab: "按 ↓ 快速改字",
      title: "按 ↓ 快速改字",
      text: "按 ↓ 叫出候選字視窗，即可選擇正確字詞；\n按 ↑ 則開啟上方的替換列，可將文字換成注音符號或英文字母。",
      frames: [
        { key: "", comp: "你好" },
        { key: "←", comp: "你好", caret: 1 },
        { key: "←", comp: "你好", caret: 0 },
        { key: "↑", comp: "你好", caret: 0, bar: ["ㄋㄧˇ", "s以", "suˇ", "su3"], sel: 0 },
        { key: "→", comp: "你好", caret: 0, bar: ["ㄋㄧˇ", "s以", "suˇ", "su3"], sel: 3 },
        { key: "Esc", comp: "你好", caret: 0 },
        { key: "↓", comp: "你好", caret: 0, cands: ["你好", "妳好", "你", "妳"], sel: 0 },
        { key: "↓", comp: "你好", caret: 0, cands: ["你好", "妳好", "你", "妳"], sel: 1 },
        { key: "Enter", comp: "妳好" },
        { key: "Enter", sent: "妳好", comp: "" }
      ]
    },
    {
      tab: "Tab 智慧補字",
      title: "Tab 智慧補字",
      text: "游標後方的小框會即時預測下一個字，按一下 Tab 鍵即可直接補上，可連續補字。",
      frames: [
        { key: "w", comp: "w" },
        { key: "9", comp: "w9" },
        { key: "6", comp: "台", hint: "灣" },
        { key: "Tab", comp: "台灣", hint: "人" },
        { key: "Tab", comp: "台灣人" },
        { key: "Enter", sent: "台灣人", comp: "" }
      ]
    }
  ];
})();

function makeTutorial(h) {
  let lesson = 0, frame = 0, rest = 0, timer = null;
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "課程" });
  const stage = h("div", { class: "stage", "aria-live": "off" });
  const keycap = h("div", { class: "keycap" });
  const title = h("div", { class: "lesson-title" });
  const text = h("div", { class: "lesson-text" });
  const bar = h("div");
  const root = h("div", {},
    tabs,
    h("div", { class: "card demo" },
      stage,
      h("div", { class: "keyrow" },
        h("div", { class: "keybox" }, h("span", { class: "row-desc" }, "目前按下的按鍵"), keycap),
        h("div", { class: "row-text" }, title, text)),
      h("div", { class: "keyrow" },
        h("div", { class: "progress" }, bar),
        h("button", { class: "btn", onclick: () => go(lesson) }, "重播"),
        h("button", { class: "btn primary", onclick: () => go((lesson + 1) % LESSONS.length) }, "下一個"))));

  LESSONS.forEach((l, i) => tabs.append(h("button", { class: "tab", role: "tab", onclick: () => go(i) }, `${i + 1}. ${l.tab}`)));

  function draw() {
    const cur = LESSONS[lesson];
    const f = cur.frames[frame];
    [...tabs.children].forEach((t, i) => { t.classList.toggle("on", i === lesson); t.setAttribute("aria-selected", i === lesson); });
    const comp = f.comp || "";
    const caret = f.caret ?? comp.length;
    stage.replaceChildren(
      document.createTextNode(f.sent || ""),
      h("span", { class: "comp" }, comp.slice(0, caret)),
      h("span", { class: "caret" }),
      h("span", { class: "comp" }, comp.slice(caret)));
    if (f.hint) stage.append(h("span", { class: "hint" }, `${f.hint}　Tab`));
    if (f.bar) {
      stage.append(h("div", { class: "bar" }, h("span", { class: "label" }, "替換列"),
        ...f.bar.map((c, i) => h("span", { class: "c" + (i === f.sel ? " sel" : "") }, c))));
    }
    if (f.cands) {
      stage.append(h("div", { class: "cands" }, h("span", { class: "label" }, "候選字"),
        ...f.cands.map((c, i) => h("span", { class: "c" + (i === f.sel ? " sel" : "") }, `${i + 1}　${c}`))));
    }
    keycap.textContent = f.key || "　";
    keycap.classList.remove("press");
    void keycap.offsetWidth;  // (restart the press animation)
    if (f.key) keycap.classList.add("press");
    title.textContent = cur.title;
    text.textContent = cur.text;
    bar.style.width = `${Math.round((frame + 1) / cur.frames.length * 100)}%`;
  }

  function tick() {
    if (!root.isConnected) { clearInterval(timer); return; }  // (the page was left)
    const n = LESSONS[lesson].frames.length;
    if (frame < n - 1) frame++;
    else if (rest < 5) { rest++; return; }
    else { lesson = (lesson + 1) % LESSONS.length; frame = 0; rest = 0; }
    draw();
  }

  function go(i) {
    lesson = i; frame = 0; rest = 0;
    draw();
    clearInterval(timer);
    timer = setInterval(tick, 480);
  }

  go(0);
  return root;
}
