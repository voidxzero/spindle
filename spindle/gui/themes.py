"""Skins: Windows XP-style chrome in dark colours, Winamp-Modern flavoured."""
from __future__ import annotations

from .. import system

THEMES: dict[str, dict[str, str]] = {
    # Winamp "Modern", but darker: gunmetal panels, green LCD, orange accents
    "Modern Night": dict(
        bg="#121419", panel_top="#2a2e37", panel_bot="#1b1e24", inset="#0e1014", border="#3b414d",
        hilite="#4a5262", text="#d7dce5", dim="#8a93a3", accent="#f29b30", accent_text="#1a1206",
        lcd_bg="#070a07", lcd_fg="#5cf08c", lcd_dim="#1f5a33", sel="#2e5a87", sel_text="#ffffff",
        title_top="#3a404c", title_bot="#262a32", btn_top="#3a3f4a", btn_bot="#23262d",
        good="#5cf08c", warn="#f2c230", bad="#ff5f56", alt="#16191e"),
    # Windows XP Luna after dark: deep blue chrome, green "Start" accent
    "Luna Midnight": dict(
        bg="#0c1424", panel_top="#1f3b6e", panel_bot="#132649", inset="#0a1120", border="#2f5395",
        hilite="#5d8ad8", text="#e3ebf8", dim="#93a7c8", accent="#3fae49", accent_text="#ffffff",
        lcd_bg="#05101f", lcd_fg="#8fd0ff", lcd_dim="#1d3f63", sel="#316ac5", sel_text="#ffffff",
        title_top="#2c63d4", title_bot="#173c8c", btn_top="#2d5296", btn_bot="#1a3466",
        good="#7ee787", warn="#ffd75e", bad="#ff7b72", alt="#0f192c"),
    # XP Olive Green, darkened
    "Olive Noir": dict(
        bg="#15170f", panel_top="#3a4026", panel_bot="#262a19", inset="#0f110a", border="#4f5733",
        hilite="#7a8650", text="#e6e8d6", dim="#a3a88a", accent="#d9b44a", accent_text="#1c1704",
        lcd_bg="#0a0c05", lcd_fg="#d4ea7a", lcd_dim="#4a5520", sel="#6b7a3a", sel_text="#ffffff",
        title_top="#5a6438", title_bot="#384021", btn_top="#4a5230", btn_bot="#2c311c",
        good="#c8f07a", warn="#f2c230", bad="#ff7058", alt="#1a1d12"),
    # Classic Winamp 2.x: black, green text, steel buttons
    "Amp Classic": dict(
        bg="#0b0b10", panel_top="#33334a", panel_bot="#1d1d2b", inset="#000000", border="#4c4c66",
        hilite="#8080a8", text="#d8d8e8", dim="#8c8ca8", accent="#e8c440", accent_text="#14100a",
        lcd_bg="#000000", lcd_fg="#00e639", lcd_dim="#0a4d18", sel="#2b4f9e", sel_text="#ffffff",
        title_top="#4b4b6e", title_bot="#25253a", btn_top="#4a4a62", btn_bot="#2a2a3a",
        good="#00e639", warn="#e8c440", bad="#ff4040", alt="#121218"),
}

SYSTEM = "System"
FONT = "'Tahoma', 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"
MONO = "'DejaVu Sans Mono', 'Liberation Mono', 'Noto Mono', 'Consolas', monospace"


def theme_names() -> list[str]:
    return [SYSTEM] + list(THEMES)


def _icons(name: str, p: dict) -> dict[str, str]:
    """Write tiny SVG arrows/checkmarks in the skin's colours (Qt style sheets can't draw them)."""
    d = system.cache_dir() / "skin" / name.replace(" ", "_")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}
    shapes = {
        "down": '<path d="M1 3 L5 7 L9 3" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
        "up": '<path d="M1 7 L5 3 L9 7" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
        "check": '<path d="M1.5 5.2 L4 7.7 L8.6 2.4" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "dot": '<circle cx="5" cy="5" r="2.6" fill="{c}"/>',
    }
    out = {}
    for key, color in (("down", p["text"]), ("up", p["text"]), ("down_dim", p["dim"]), ("up_dim", p["dim"])):
        shape = shapes[key.split("_")[0]].format(c=color)
        f = d / f"{key}.svg"
        try:
            f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">{shape}</svg>')
        except OSError:
            continue
        out[key] = str(f).replace("\\", "/")
    # Complete checkbox / radio images (box, border, fill and tick in one picture). Qt draws item-view
    # check indicators from the image alone - background/border rules are ignored there.
    tick = '<path d="M3.6 8.2 L6.6 11.2 L12.4 4.8" fill="none" stroke="{t}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
    box = '<rect x="0.75" y="0.75" width="14.5" height="14.5" rx="3" fill="{f}" stroke="{b}" stroke-width="1.5"/>'
    ring = '<circle cx="8" cy="8" r="7.25" fill="{f}" stroke="{b}" stroke-width="1.5"/>'
    dot = '<circle cx="8" cy="8" r="3.4" fill="{t}"/>'
    states = {
        "cb_off": box.format(f=p["btn_top"], b=p["dim"]),
        "cb_off_hover": box.format(f=p["btn_top"], b=p["accent"]),
        "cb_on": box.format(f=p["accent"], b=p["accent"]) + tick.format(t=p["accent_text"]),
        "cb_off_dis": box.format(f=p["btn_bot"], b=p["border"]),
        "cb_on_dis": box.format(f=p["dim"], b=p["dim"]) + tick.format(t=p["btn_bot"]),
        "rb_off": ring.format(f=p["btn_top"], b=p["dim"]),
        "rb_off_hover": ring.format(f=p["btn_top"], b=p["accent"]),
        "rb_on": ring.format(f=p["btn_top"], b=p["accent"]) + dot.format(t=p["accent"]),
    }
    for key, body in states.items():
        f = d / f"{key}.svg"
        try:
            f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{body}</svg>')
        except OSError:
            continue
        out[key] = str(f).replace("\\", "/")
    return out


SYSTEM_QSS = """
QLabel#lcdBig { font-family: %(MONO)s; font-size: 11pt; font-weight: bold; padding: 0px 10px;
    border: 1px solid palette(mid); border-radius: 4px; background: palette(base); }
QLabel#lcd { font-family: %(MONO)s; padding: 3px 8px; border: 1px solid palette(mid); border-radius: 4px;
    background: palette(base); }
QLabel#railTitle { font-weight: bold; letter-spacing: 2px; color: palette(highlight); }
QLabel#cover { border: 1px solid palette(mid); border-radius: 4px; background: palette(base); color: palette(placeholder-text); }
QLabel#fieldLabel, QLabel#dim { color: palette(placeholder-text); }
QToolButton#railButton { min-width: 92px; max-width: 92px; min-height: 64px; padding: 4px 2px; }
QToolButton#railButton[accent="true"] { font-weight: bold; }
QPlainTextEdit#log { font-family: %(MONO)s; font-size: 9pt; }
""" % dict(MONO=MONO)


def stylesheet(name: str) -> str:
    if name == SYSTEM or name not in THEMES:
        return SYSTEM_QSS
    p = dict(THEMES[name])
    icons = _icons(name, p)
    p.update({k: icons.get(k, "") for k in ("down", "up", "down_dim", "up_dim", "cb_off", "cb_off_hover", "cb_on",
                                            "cb_off_dis", "cb_on_dis", "rb_off", "rb_off_hover", "rb_on")})
    return """
* { font-family: %(FONT)s; font-size: 9pt; color: %(text)s; }
*:disabled { color: %(dim)s; }
QMainWindow, QDialog { background: %(bg)s; }
QWidget#central { background: %(bg)s; }
QToolTip { background: %(inset)s; color: %(text)s; border: 1px solid %(accent)s; padding: 4px; }

/* panels: raised XP-style bevel */
QFrame#panel, QFrame#rail, QFrame#header {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(panel_top)s, stop:1 %(panel_bot)s);
    border: 1px solid %(border)s; border-top-color: %(hilite)s; border-radius: 4px; }
QFrame#rail { border-radius: 6px; }
QLabel#railTitle { color: %(accent)s; font-weight: bold; letter-spacing: 1px; font-size: 8pt; }

/* the LCD readout */
QLabel#lcd, QLabel#lcdBig {
    background: %(lcd_bg)s; color: %(lcd_fg)s; font-family: %(MONO)s;
    border: 2px solid %(inset)s; border-bottom-color: %(hilite)s; border-right-color: %(hilite)s;
    border-radius: 3px; padding: 0px 10px; }
QLabel#lcdBig { font-size: 11pt; font-weight: bold; }
QLabel#lcdDim { color: %(lcd_dim)s; background: transparent; font-family: %(MONO)s; }

QLabel#cover { background: %(inset)s; border: 2px solid %(inset)s; border-bottom-color: %(hilite)s;
    border-right-color: %(hilite)s; color: %(dim)s; }
QLabel#cover:hover { border-color: %(accent)s; }
QLabel#fieldLabel { color: %(dim)s; }
QLabel#dim { color: %(dim)s; }

/* group boxes with XP task-pane title strips */
QGroupBox { background: %(panel_bot)s; border: 1px solid %(border)s; border-radius: 4px;
    margin-top: 16px; padding: 14px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 6px; padding: 1px 8px;
    color: %(text)s; font-weight: bold; border-radius: 3px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %(title_top)s, stop:1 %(title_bot)s);
    border: 1px solid %(border)s; }

/* inputs: one consistent height for every single-line control */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QListWidget {
    background: %(inset)s; border: 1px solid %(border)s; border-radius: 3px;
    selection-background-color: %(sel)s; selection-color: %(sel_text)s; }
QLineEdit, QSpinBox, QComboBox { min-height: 20px; padding: 0px 6px; color: %(text)s; }
QSpinBox { selection-background-color: %(sel)s; selection-color: %(sel_text)s; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QListWidget:focus { border-color: %(accent)s; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: %(dim)s; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 20px;
    border: none; border-left: 1px solid %(border)s; }
QComboBox::down-arrow { image: url("%(down)s"); width: 10px; height: 10px; }
QComboBox::down-arrow:disabled { image: url("%(down_dim)s"); }
QComboBox QAbstractItemView { background: %(inset)s; border: 1px solid %(accent)s; padding: 2px;
    selection-background-color: %(sel)s; outline: none; }
QSpinBox { padding-right: 20px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 18px;
    background: %(btn_bot)s; border: none; border-left: 1px solid %(border)s; }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 3px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 3px;
    border-top: 1px solid %(border)s; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: %(btn_top)s; }
QSpinBox::up-arrow { image: url("%(up)s"); width: 7px; height: 7px; }
QSpinBox::down-arrow { image: url("%(down)s"); width: 7px; height: 7px; }
QPlainTextEdit#log { font-family: %(MONO)s; font-size: 8pt; color: %(lcd_fg)s; background: %(lcd_bg)s;
    padding: 4px; }

/* glossy XP buttons */
QPushButton, QToolButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(btn_top)s, stop:0.5 %(btn_bot)s, stop:1 %(btn_bot)s);
    border: 1px solid %(border)s; border-top-color: %(hilite)s; border-radius: 3px; padding: 1px 12px;
    min-height: 22px; }
QToolButton#transport { min-width: 28px; max-width: 28px; padding: 1px; }
QToolButton#iconButton { padding: 0px; }
QPushButton:hover, QToolButton:hover { border-color: %(accent)s; }
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {
    background: %(inset)s; border-top-color: %(inset)s; border-bottom-color: %(hilite)s; }
QPushButton:disabled, QToolButton:disabled { color: %(dim)s; border-color: %(border)s; }
QPushButton#primary, QToolButton#primary {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(accent)s, stop:1 %(btn_bot)s);
    color: %(accent_text)s; font-weight: bold; border-color: %(accent)s; }
QPushButton#primary:disabled, QToolButton#primary:disabled { background: %(btn_bot)s; color: %(dim)s; }
QToolButton#railButton { min-width: 92px; max-width: 92px; min-height: 64px; padding: 4px; }
QToolButton#railButton[accent="true"] {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(accent)s, stop:1 %(btn_bot)s);
    color: %(accent_text)s; font-weight: bold; border-color: %(accent)s; }
QToolButton#railButton[accent="true"]:disabled { background: %(btn_bot)s; color: %(dim)s; border-color: %(border)s; }
QToolButton::menu-indicator { image: url("%(down)s"); subcontrol-position: right center; width: 10px; right: 6px; }
QToolButton#formatButton { padding-right: 24px; }

QCheckBox, QRadioButton { spacing: 6px; }
QCheckBox::indicator, QTableView::indicator, QTableWidget::indicator, QListView::indicator, QListWidget::indicator, QTreeView::indicator {
    width: 15px; height: 15px; background: transparent; border: none; }
QCheckBox::indicator:unchecked, QTableView::indicator:unchecked, QTableWidget::indicator:unchecked, QListView::indicator:unchecked, QListWidget::indicator:unchecked, QTreeView::indicator:unchecked { image: url("%(cb_off)s"); }
QCheckBox::indicator:unchecked:hover, QTableView::indicator:unchecked:hover, QTableWidget::indicator:unchecked:hover, QListView::indicator:unchecked:hover, QListWidget::indicator:unchecked:hover, QTreeView::indicator:unchecked:hover { image: url("%(cb_off_hover)s"); }
QCheckBox::indicator:unchecked:disabled, QTableView::indicator:unchecked:disabled, QTableWidget::indicator:unchecked:disabled, QListView::indicator:unchecked:disabled, QListWidget::indicator:unchecked:disabled, QTreeView::indicator:unchecked:disabled { image: url("%(cb_off_dis)s"); }
QCheckBox::indicator:checked, QTableView::indicator:checked, QTableWidget::indicator:checked, QListView::indicator:checked, QListWidget::indicator:checked, QTreeView::indicator:checked { image: url("%(cb_on)s"); }
QCheckBox::indicator:checked:disabled, QTableView::indicator:checked:disabled, QTableWidget::indicator:checked:disabled, QListView::indicator:checked:disabled, QListWidget::indicator:checked:disabled, QTreeView::indicator:checked:disabled { image: url("%(cb_on_dis)s"); }
QRadioButton::indicator { width: 15px; height: 15px; background: transparent; border: none; }
QRadioButton::indicator:unchecked { image: url("%(rb_off)s"); }
QRadioButton::indicator:unchecked:hover { image: url("%(rb_off_hover)s"); }
QRadioButton::indicator:checked { image: url("%(rb_on)s"); }
QSlider::groove:horizontal { height: 4px; background: %(inset)s; border: 1px solid %(border)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -6px 0; border-radius: 6px; border: 1px solid %(border)s;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(btn_top)s, stop:1 %(btn_bot)s); }
QSlider::handle:horizontal:hover { border-color: %(accent)s; }

/* track list */
QTableWidget, QTableView { background: %(inset)s; alternate-background-color: %(alt)s;
    gridline-color: %(panel_bot)s; border: 1px solid %(border)s; border-radius: 3px;
    selection-background-color: %(sel)s; selection-color: %(sel_text)s; }
QHeaderView::section { color: %(text)s; padding: 4px 6px; border: none;
    border-right: 1px solid %(border)s; border-bottom: 2px solid %(accent)s;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(title_top)s, stop:1 %(title_bot)s); }
QTableCornerButton::section { background: %(title_bot)s; border: none; }

/* XP tabs: the selected tab gets the orange (accent) top line */
QTabWidget::pane { border: 1px solid %(border)s; border-radius: 3px; background: %(panel_bot)s; top: -1px; }
QTabBar::tab { background: %(btn_bot)s; border: 1px solid %(border)s; border-bottom: none;
    padding: 5px 12px; margin-right: 2px; border-top-left-radius: 3px; border-top-right-radius: 3px;
    color: %(dim)s; }
QTabBar::tab:selected { background: %(panel_bot)s; color: %(text)s; border-top: 3px solid %(accent)s; }
QTabBar::tab:hover:!selected { color: %(text)s; }

QProgressBar { background: %(lcd_bg)s; border: 1px solid %(inset)s; border-bottom-color: %(hilite)s;
    border-radius: 3px; text-align: center; color: %(lcd_fg)s; font-family: %(MONO)s; height: 16px; }
QProgressBar::chunk { border-radius: 2px;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(lcd_fg)s, stop:1 %(lcd_dim)s); }

QMenuBar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 %(panel_top)s, stop:1 %(panel_bot)s);
    border-bottom: 1px solid %(border)s; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: %(sel)s; border-radius: 3px; }
QMenu { background: %(panel_bot)s; border: 1px solid %(border)s; padding: 3px; }
QMenu::item { padding: 5px 24px 5px 20px; border-radius: 2px; }
QMenu::item:selected { background: %(sel)s; color: %(sel_text)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }
QStatusBar { background: %(panel_bot)s; border-top: 1px solid %(border)s; color: %(dim)s; }
QStatusBar QLabel { color: %(dim)s; padding: 0 8px; }
QSplitter::handle { background: %(bg)s; }
QSplitter::handle:hover { background: %(accent)s; }

QScrollBar:vertical { background: %(inset)s; width: 12px; margin: 0; }
QScrollBar:horizontal { background: %(inset)s; height: 12px; margin: 0; }
QScrollBar::handle { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %(btn_top)s, stop:1 %(btn_bot)s);
    border: 1px solid %(border)s; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { border-color: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
""" % dict(p, FONT=FONT, MONO=MONO)


def colors(name: str) -> dict[str, str]:
    if name in THEMES:
        return THEMES[name]
    # System theme: derive from the desktop palette so it fits light and dark setups
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    pal = QApplication.palette()
    c = lambda role: pal.color(role).name()  # noqa: E731
    dark = pal.color(QPalette.Window).lightness() < 128
    return dict(sel=c(QPalette.Highlight), sel_text=c(QPalette.HighlightedText), text=c(QPalette.Text),
                lcd_bg=c(QPalette.Base), lcd_fg=c(QPalette.Highlight), lcd_dim=c(QPalette.Mid),
                border=c(QPalette.Mid), good="#3fb950" if dark else "#1a7f37",
                warn="#d29922" if dark else "#9a6700", bad="#f85149" if dark else "#cf222e",
                accent=c(QPalette.Highlight), dim=c(QPalette.PlaceholderText))
