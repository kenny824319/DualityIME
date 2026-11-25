// 自訂外觀 (#35): the frame each window's photo is scaled to and centred on (DIP), and where the photo goes - the
// same sums as cpp/tip/photo_frame.h (both checked against tests/photo_frames.json and tests/photo_places.json): the
// candidate list and the quick fill list a whole page with these settings, the symbol keyboard its size, the others
// (their size follows what they show) a fixed one. The photo is measured from the window's top-left corner, so a
// window smaller than its frame shows the top-left of the same picture.
function lookFrame(key, keys, perLine, fontSize) {
  const f = Math.min(48, Math.max(10, fontSize));
  const lineH = Math.floor(f * 1.28 + 0.5);  // (a character's height in the font, as cpp/tip/ui.cpp measures it)
  if (key === "keyboard") return [918, 292];
  if (key === "tip") return [200, 44];
  if (key === "mode") return [56, 48];
  if (key === "notice") return [140, 32];  // (#73 design: "Insert 沒有放開" at 16 px)
  if (key === "bar") return [420, 44];
  if (key === "fill") return [332, 12 + 30 + 9 * (lineH + 10)];
  const n = Math.max(1, keys), p = Math.max(1, perLine);
  const label = Math.floor(Math.max(11, Math.floor(f * 2 / 3)) * 0.55 + 0.5);
  const cell = label + 6 + f + 20, row = lineH + 6;  // (one character to a cell)
  let w, h;
  if (p > 1 && p >= n) {  // one row: each cell as wide as it is
    w = 12 + n * cell + (n - 1) * 2;
    h = 36 + row + 6;
  } else {  // a column (1 to a row) or a grid: cells 64 wide at least
    const rows = Math.ceil(n / p);
    w = 12 + p * Math.max(64, cell) + (p - 1) * 4;
    h = 36 + rows * (row + 2) - 2 + 6;
  }
  return [Math.max(w, 110), h];
}

// where the photo (iw x ih) goes in a window w x h with this frame: [left, top, width, height] (DIP). Scaled to
// cover the frame (times zoom %), and the window if it is bigger; its point (x, y) at the frame's centre, clamped
// against the larger of window and frame (a window shorter than its frame, the last page, shows the same picture's
// top; review 2026-11-14)
function lookPlace(frame, zoom, x, y, w, h, iw, ih) {
  const [fw, fh] = frame;
  let k = Math.max(fw / iw, fh / ih) * zoom / 100;
  k = Math.max(k, w / iw, h / ih);
  const dw = iw * k, dh = ih * k;
  const left = Math.min(0, Math.max(Math.max(w, fw) - dw, fw / 2 - x * dw));
  const top = Math.min(0, Math.max(Math.max(h, fh) - dh, fh / 2 - y * dh));
  return [left, top, dw, dh];
}
