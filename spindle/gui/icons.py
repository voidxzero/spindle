"""Spindle's own minimal line icons, tinted to the current theme's text colour."""
from __future__ import annotations

import math as _m

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

S = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
F = 'fill="{c}" stroke="none"'

PATHS = {
    "disc": f'<circle cx="12" cy="12" r="9" {S}/><circle cx="12" cy="12" r="2.5" {S}/>'
            f'<path d="M12 6.5a5.5 5.5 0 0 1 5.5 5.5" {S}/>',
    "search": f'<circle cx="10.5" cy="10.5" r="6" {S}/><path d="M15 15l5 5" {S}/>',
    "image": f'<rect x="3.5" y="5" width="17" height="14" rx="2" {S}/><circle cx="9" cy="10" r="1.6" {S}/>'
             f'<path d="M20 16l-5-5-8.5 8" {S}/>',
    "rip": f'<circle cx="12" cy="12" r="9" {S}/><circle cx="12" cy="12" r="4" {F}/>',
    "imagefile": f'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" {S}/>'
                 f'<path d="M14 3v5h5" {S}/><circle cx="12" cy="14.5" r="3.2" {S}/>',
    "eject": f'<path d="M12 5l7 8H5z" {S}/><path d="M5 18h14" {S}/>',
    "settings": f'<path d="M4 7h16M4 12h16M4 17h16" {S}/><circle cx="9" cy="7" r="2" fill="{{bg}}" stroke="{{c}}" '
                f'stroke-width="1.8"/><circle cx="15" cy="12" r="2" fill="{{bg}}" stroke="{{c}}" stroke-width="1.8"/>'
                f'<circle cx="8" cy="17" r="2" fill="{{bg}}" stroke="{{c}}" stroke-width="1.8"/>',
    "folder": f'<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" {S}/>',
    "prev": f'<path d="M7 6v12" {S}/><path d="M18 6l-8 6 8 6z" {F}/>',
    "next": f'<path d="M17 6v12" {S}/><path d="M6 6l8 6-8 6z" {F}/>',
    "play": f'<path d="M8 5l11 7-11 7z" {F}/>',
    "pause": f'<rect x="6.5" y="5" width="4" height="14" rx="1" {F}/><rect x="13.5" y="5" width="4" height="14" rx="1" {F}/>',
    "stop": f'<rect x="6.5" y="6.5" width="11" height="11" rx="1.5" {F}/>',
    "volume": f'<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" {S}/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" {S}/>',
}


def _pixmap(name: str, color: str, bg: str, px: int) -> QPixmap | None:
    body = PATHS[name].replace("{c}", color).replace("{bg}", bg)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{px}" height="{px}">{body}</svg>'
    pm = QPixmap()
    if not pm.loadFromData(QByteArray(svg.encode()), "SVG"):
        return None
    return pm


def _faded(pm: QPixmap, alpha: float) -> QPixmap:
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setOpacity(alpha)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def make(name: str, color: str, bg: str = "#000000") -> QIcon | None:
    """Icon in ``color`` at several sizes, with a faded disabled state; None if SVG can't load."""
    icon = QIcon()
    for px in (16, 24, 32, 48, 64):
        pm = _pixmap(name, QColor(color).name(), QColor(bg).name(), px)
        if pm is None:
            return None
        icon.addPixmap(pm, QIcon.Normal)
        icon.addPixmap(_faded(pm, 0.35), QIcon.Disabled)
    return icon


# ----------------------------------------------------------------------------------------------
# Glossy, full-colour icons for the Y2K / Windows XP skins. Only gradients, paths and opacity are
# used (Qt's SVG renderer has no filters or clipping), so shadows and gloss are drawn as shapes.
# ----------------------------------------------------------------------------------------------
def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(c: str, other: str, t: float) -> str:
    a, b = _hex(c), _hex(other)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _lin(i, *stops, x2=0, y2=1):
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{i}" x1="0" y1="0" x2="{x2}" y2="{y2}">{s}</linearGradient>'


def _rad(i, cx, cy, r, *stops):
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<radialGradient id="{i}" cx="{cx}" cy="{cy}" r="{r}">{s}</radialGradient>'


def _shadow(shape: str) -> str:
    return f'<g transform="translate(0.7,1.0)" opacity="0.35">{shape}</g>'


def _gear_path(cx, cy, r_out, r_in, teeth=8):
    pts = []
    for i in range(teeth * 4):
        a = 2 * _m.pi * i / (teeth * 4) - _m.pi / 2
        r = r_out if (i % 4) in (1, 2) else r_in
        pts.append(f"{cx + r * _m.cos(a):.2f},{cy + r * _m.sin(a):.2f}")
    return "M" + " L".join(pts) + " Z"


def _glossy_body(name: str, accent: str) -> str:
    hi, mid, lo = _mix(accent, "#ffffff", 0.55), accent, _mix(accent, "#000000", 0.35)
    edge = _mix(accent, "#000000", 0.55)
    acc = _lin("acc", (0, hi), (0.5, mid), (1, lo))
    if name == "disc":
        sheen = ""
        cols = ["#ff7a7a", "#ffd36b", "#9dff9d", "#7fdcff", "#a18bff", "#ff8de0"]
        for k in range(12):
            a0, a1 = 2 * _m.pi * k / 12, 2 * _m.pi * (k + 1) / 12
            x0, y0 = 12 + 9 * _m.cos(a0), 12 + 9 * _m.sin(a0)
            x1, y1 = 12 + 9 * _m.cos(a1), 12 + 9 * _m.sin(a1)
            op = 0.10 + 0.28 * (0.5 + 0.5 * _m.cos(2 * a0 + 0.8)) ** 2
            sheen += (f'<path d="M12 12 L{x0:.2f} {y0:.2f} A9 9 0 0 1 {x1:.2f} {y1:.2f} Z" '
                      f'fill="{cols[k % 6]}" opacity="{op:.2f}"/>')
        return ("<defs>" + _rad("s", 0.4, 0.35, 0.75, (0, "#ffffff"), (0.55, "#c8d0dc"), (1, "#8794a8")) + "</defs>"
                + _shadow('<circle cx="12" cy="12" r="9.3" fill="#000"/>')
                + '<circle cx="12" cy="12" r="9.3" fill="url(#s)" stroke="#556075" stroke-width="0.9"/>' + sheen
                + '<ellipse cx="9" cy="7.6" rx="5.6" ry="2.8" fill="#ffffff" opacity="0.45"/>'
                + '<circle cx="12" cy="12" r="3.3" fill="#eef1f6" stroke="#7b869a" stroke-width="0.8"/>'
                + '<circle cx="12" cy="12" r="1.4" fill="#2b3038"/>')
    if name == "search":
        return ("<defs>" + _rad("g", 0.35, 0.3, 0.8, (0, "#f4fbff"), (0.5, "#8fcaf5"), (1, "#2f74c0"))
                + _lin("rim", (0, "#f2f4f7"), (1, "#6b7482")) + "</defs>"
                + _shadow('<circle cx="10" cy="10" r="6.6" fill="#000"/><path d="M15 15 L20.5 20.5" stroke="#000" stroke-width="4" stroke-linecap="round"/>')
                + '<path d="M15 15 L20.6 20.6" stroke="#2c1f15" stroke-width="4.2" stroke-linecap="round"/>'
                + '<path d="M15.2 14.6 L20.4 19.8" stroke="#9b7650" stroke-width="1.3" stroke-linecap="round"/>'
                + '<circle cx="10" cy="10" r="6.4" fill="url(#g)" stroke="url(#rim)" stroke-width="2"/>'
                + '<circle cx="10" cy="10" r="7.4" fill="none" stroke="#4a525e" stroke-width="0.7"/>'
                + '<ellipse cx="8.2" cy="7.6" rx="2.9" ry="1.7" fill="#ffffff" opacity="0.75"/>')
    if name == "image":
        return ("<defs>" + _lin("f", (0, "#fbe3a0"), (1, "#b07d26")) + _lin("sky", (0, "#7fc8ff"), (1, "#e8f6ff"))
                + _lin("hill", (0, "#86dd71"), (1, "#2c7f26")) + "</defs>"
                + _shadow('<rect x="3" y="4.5" width="18" height="15" rx="1.6" fill="#000"/>')
                + '<rect x="3" y="4.5" width="18" height="15" rx="1.6" fill="url(#f)" stroke="#6e4c14" stroke-width="0.9"/>'
                + '<rect x="5.2" y="6.7" width="13.6" height="10.6" fill="url(#sky)" stroke="#8a6420" stroke-width="0.6"/>'
                + '<path d="M5.2 17.3 L5.2 14 Q8.5 10.6 11.5 13.4 Q14.2 11.4 18.8 14.6 L18.8 17.3 Z" fill="url(#hill)"/>'
                + '<circle cx="15.3" cy="9.6" r="1.7" fill="#ffe25c" stroke="#e0a800" stroke-width="0.5"/>')
    if name == "rip":
        return ("<defs>" + _rad("r", 0.38, 0.32, 0.75, (0, "#ffb3a6"), (0.45, "#e8291c"), (1, "#7e0c07")) + "</defs>"
                + _shadow('<circle cx="12" cy="12" r="8.4" fill="#000"/>')
                + '<circle cx="12" cy="12" r="8.4" fill="url(#r)" stroke="#5e0a06" stroke-width="0.9"/>'
                + '<ellipse cx="12" cy="7.9" rx="5.2" ry="2.9" fill="#ffffff" opacity="0.55"/>')
    if name == "imagefile":
        return ("<defs>" + _lin("p", (0, "#ffffff"), (1, "#d6dde8")) + _rad("s", 0.4, 0.35, 0.8, (0, "#ffffff"), (0.6, "#c3ccd9"), (1, "#8592a6")) + "</defs>"
                + _shadow('<path d="M5.5 2.5 H14.5 L19 7 V21.5 H5.5 Z" fill="#000"/>')
                + '<path d="M5.5 2.5 H14.5 L19 7 V21.5 H5.5 Z" fill="url(#p)" stroke="#7d889a" stroke-width="0.9" stroke-linejoin="round"/>'
                + '<path d="M14.5 2.5 V7 H19 Z" fill="#c2cad6" stroke="#7d889a" stroke-width="0.7" stroke-linejoin="round"/>'
                + '<circle cx="12.2" cy="14.4" r="4.6" fill="url(#s)" stroke="#5f6a7e" stroke-width="0.7"/>'
                + '<path d="M12.2 9.8 A4.6 4.6 0 0 1 16.8 14.4 L12.2 14.4 Z" fill="#ffd36b" opacity="0.35"/>'
                + '<circle cx="12.2" cy="14.4" r="1.5" fill="#eef1f6" stroke="#7b869a" stroke-width="0.5"/>')
    if name == "eject":
        return ("<defs>" + acc + "</defs>"
                + _shadow('<path d="M12 4 L20 13 H4 Z M4 15.5 H20 V19 H4 Z" fill="#000"/>')
                + f'<path d="M12 4 L20 13 H4 Z" fill="url(#acc)" stroke="{edge}" stroke-width="0.9" stroke-linejoin="round"/>'
                + f'<rect x="4" y="15.5" width="16" height="3.6" rx="1" fill="url(#acc)" stroke="{edge}" stroke-width="0.9"/>'
                + '<path d="M12 5.6 L16.4 10.6 H7.6 Z" fill="#ffffff" opacity="0.30"/>')
    if name == "settings":
        gear = _gear_path(12, 12, 9.4, 7.0, 8)
        return ("<defs>" + _lin("m", (0, "#f3f5f8"), (0.5, "#b9c0cb"), (1, "#7b8494")) + "</defs>"
                + _shadow(f'<path d="{gear}" fill="#000"/>')
                + f'<path d="{gear}" fill="url(#m)" stroke="#4c5463" stroke-width="0.9" stroke-linejoin="round"/>'
                + '<circle cx="12" cy="12" r="3.4" fill="#3b414b" stroke="#2a2f37" stroke-width="0.7"/>'
                + '<circle cx="12" cy="12" r="5.4" fill="none" stroke="#ffffff" stroke-width="0.7" opacity="0.6"/>'
                + '<ellipse cx="10" cy="7.4" rx="4" ry="1.6" fill="#ffffff" opacity="0.45"/>')
    if name == "folder":
        return ("<defs>" + _lin("b", (0, "#f4c75a"), (1, "#c98a12")) + _lin("fr", (0, "#ffe596"), (1, "#f0ae2a")) + "</defs>"
                + _shadow('<path d="M2.5 6.5 Q2.5 5 4 5 H9 L11 7 H20 Q21.5 7 21.5 8.5 V18.5 Q21.5 20 20 20 H4 Q2.5 20 2.5 18.5 Z" fill="#000"/>')
                + '<path d="M2.5 6.5 Q2.5 5 4 5 H9 L11 7 H20 Q21.5 7 21.5 8.5 V18.5 Q21.5 20 20 20 H4 Q2.5 20 2.5 18.5 Z" fill="url(#b)" stroke="#8f610c" stroke-width="0.8" stroke-linejoin="round"/>'
                + '<path d="M3.2 10 H21.4 L20.4 19.2 Q20.3 20 19.4 20 H4.6 Q3.7 20 3.6 19.2 Z" fill="url(#fr)" stroke="#9c6b10" stroke-width="0.8" stroke-linejoin="round"/>'
                + '<path d="M4 11 H20.4 L20.3 12 H4.1 Z" fill="#ffffff" opacity="0.5"/>')
    if name in ("prev", "next", "play", "pause", "stop"):
        shape = {
            "play": '<path d="M7 4.5 L19.5 12 L7 19.5 Z"/>',
            "pause": '<rect x="5.5" y="4.5" width="5" height="15" rx="1.2"/><rect x="13.5" y="4.5" width="5" height="15" rx="1.2"/>',
            "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
            "prev": '<rect x="4.5" y="5" width="3.2" height="14" rx="1"/><path d="M19.5 5 L8.5 12 L19.5 19 Z"/>',
            "next": '<rect x="16.3" y="5" width="3.2" height="14" rx="1"/><path d="M4.5 5 L15.5 12 L4.5 19 Z"/>',
        }[name]
        return ("<defs>" + acc + "</defs>"
                + _shadow(f'<g fill="#000">{shape}</g>')
                + f'<g fill="url(#acc)" stroke="{edge}" stroke-width="0.9" stroke-linejoin="round">{shape}</g>'
                + f'<g fill="#ffffff" opacity="0.30" transform="translate(12,9) scale(0.8,0.45) translate(-12,-12)">{shape}</g>')
    if name == "volume":
        return ("<defs>" + _lin("m", (0, "#f1f3f6"), (1, "#818a99")) + "</defs>"
                + _shadow('<path d="M3.5 9 H7.5 L12.5 4.5 V19.5 L7.5 15 H3.5 Z" fill="#000"/>')
                + '<path d="M3.5 9 H7.5 L12.5 4.5 V19.5 L7.5 15 H3.5 Z" fill="url(#m)" stroke="#4a5262" stroke-width="0.9" stroke-linejoin="round"/>'
                + '<path d="M15.3 8.6 A4.6 4.6 0 0 1 15.3 15.4" fill="none" stroke="#2f7fd6" stroke-width="2" stroke-linecap="round"/>'
                + '<path d="M17.8 6 A8.2 8.2 0 0 1 17.8 18" fill="none" stroke="#2f7fd6" stroke-width="2" stroke-linecap="round"/>'
                + '<path d="M15.6 9.4 A3.8 3.8 0 0 1 16.2 11" fill="none" stroke="#bfe0ff" stroke-width="0.8" stroke-linecap="round"/>')
    return ""


def glossy_svg(name: str, accent: str, px: int = 24) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{px}" height="{px}">'
            f'{_glossy_body(name, accent)}</svg>')


def make_glossy(name: str, accent: str) -> QIcon | None:
    icon = QIcon()
    for px in (16, 24, 32, 48, 64):
        pm = QPixmap()
        if not pm.loadFromData(QByteArray(glossy_svg(name, QColor(accent).name(), px).encode()), "SVG"):
            return None
        icon.addPixmap(pm, QIcon.Normal)
        icon.addPixmap(_faded(pm, 0.35), QIcon.Disabled)
    return icon
