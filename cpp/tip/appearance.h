// 自訂外觀 (#22, docs/superpowers/specs/2026-11-13-custom-appearance-design.md): the user's colors and photos
// for the IME's windows. config.json "appearance" comes in the engine's cfg with the folder of the photos;
// {} or "on": false is the default look (current_theme() as before, no photo).
#pragma once

#include <windows.h>

#include <map>
#include <string>

#include "json.h"
#include "ui.h"

namespace sz {

// a window's photo: fade 0-85 (the veil's alpha in %), zoom 100-300 (100 = the photo just covers the window's
// reference size), x y the point of the photo at the reference box's center (0-1)
struct Look {
    double fade = 45, zoom = 100, x = 0.5, y = 0.5;
    std::wstring image;      // a file of the appearance folder, "" = no photo
    bool own_image = false;  // (a window's: image set there, else the photo of "all")
};

struct Appearance {
    bool on = false;
    COLORREF bg = RGB(0xf3, 0xe7, 0xd9), text = RGB(0x4a, 0x34, 0x26), select = RGB(0xe0, 0x8a, 0x4c);
    Look all;
    std::map<std::string, Look> windows;  // only the windows adjusted separately: cand tip mode fill keyboard bar
    std::wstring dir;                     // the appearance folder
};

Appearance parse_appearance(const Json* a, const std::wstring& dir);
void set_appearance(Appearance a);
// from the engine's cfg: parsed again only when the object or the folder changed (each reply carries them)
void set_appearance_from(const Json* a, const std::wstring& dir);
const Appearance& appearance();

// the window roles of current_theme() from the three colors
Theme derive_theme(COLORREF bg, COLORREF text, COLORREF select);

// relative luminance 0-1; the veil over a photo is black for light text (> 0.5), else white
double luminance(COLORREF c);

// While the custom look is on and `window` has a photo that loads: draws it (cover-cropped against the window's
// reference size, the veil over it) clipped to the rounded window with its border, and returns true. Else draws
// nothing and returns false: the caller draws its box as before.
bool draw_background(HDC dc, double scale, int w, int h, const char* window, int radius, COLORREF border);

// a rectangle of `color` at `alpha` (0-255) over what is drawn, rounded by `radius` (device pixels)
void glass(HDC dc, int x, int y, int w, int h, COLORREF color, BYTE alpha, int radius);

}  // namespace sz
