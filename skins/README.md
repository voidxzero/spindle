# Spindle skins

Every `.json` file in this folder is a skin that ships with Spindle (*Options ▸ Skin*). To use a skin
someone sent you, choose **Settings ▸ Appearance ▸ Import skin…**. A skin is just colours and a few
settings, never code, so skins are safe to share.

## Making a skin

The easiest start: pick the skin closest to what you want, then **Settings ▸ Appearance ▸ Save a copy
to edit…**. That writes a file with every colour spelled out. Edit it, then **Import skin…** it (import
it again after each change). `vapor-dusk.json` here is a complete example.

```json
{
  "spindle_skin": 1,
  "name": "My Skin",
  "author": "you",
  "base": "Modern Night",
  "colors": { "bg": "#140b24", "accent": "#ff4fb8" },
  "corners": { "r": 2, "r_panel": 3 },
  "font": "Tahoma, sans-serif",
  "font_size": 9,
  "caption": "#3a1d5c",
  "style": "classic98"
}
```

Only `name` is required. Anything you leave out comes from the `base` skin (Modern Night if none).

| Setting | What it is |
|---|---|
| `name` | Shown in the Skin menu (up to 40 characters) |
| `base` | A skin to start from: any built-in or installed skin |
| `colors` | Any of the colours below, as `#rrggbb` |
| `corners` | Corner radius in pixels (0-20): `r` buttons and fields, `r_panel` panels, `r_rail` the left button column, `r_small` small parts, `r_knob` slider knobs |
| `font`, `font_size` | Font names (comma-separated fallbacks) and size in points |
| `caption` | Windows title bar colour (Windows 11); otherwise `title_top` is used |
| `style` | `"classic98"` adds Windows 98 bevels, icons and controls (see Classic 98) |

### Colours

| Colour | Used for |
|---|---|
| `bg` | Window background |
| `panel_top`, `panel_bot` | Panel gradient (top, bottom) |
| `inset` | Text fields, lists, the track table |
| `border`, `hilite` | Edges; the light top edge of raised things |
| `text`, `dim` | Text; labels and disabled text |
| `accent`, `accent_text` | Highlights, the Rip button and its text |
| `lcd_bg`, `lcd_fg`, `lcd_dim` | The LCD readouts, log and progress bar |
| `sel`, `sel_text` | Selected rows and their text |
| `title_top`, `title_bot` | Column headers and title strips |
| `btn_top`, `btn_bot` | Button gradient |
| `good`, `warn`, `bad` | Success, warning and failure colours |
| `alt` | Alternate table rows |
