# External OhmPy themes

This directory contains **separately installable themes**. Every `*.json`
file placed here will automatically appear in the app's **Theme** menu the
next time you open it.

## Create your own theme

1. Copy `solarized_dark.json` to, for example, `my_theme.json`.
2. Edit the colors (hex format: `#rrggbb`).
3. Restart the app — `My Theme` will appear in the selector.

## Format

```json
{
    "name":        "Display name",
    "description": "Optional description",
  "colors": {
    "bg":         "#…",
    "grid":       "#…",
    "grid_line":  "#…",
    "component":  "#…",
    "comp_body":  "#…",
    "comp_sel":   "#…",
    "wire":       "#…",
    "wire_sel":   "#…",
    "node_dot":   "#…",
    "text":       "#…",
    "text_dim":   "#…",
    "pin":        "#…",
    "gnd":        "#…",
    "toolbar":    "#…",
    "panel":      "#…",
    "panel_brd":  "#…",
    "voltage":    "#…",
    "current":    "#…"
  }
}
```

All `colors` keys are required; if any are missing, the theme is silently
ignored.

## Alternative directories

OhmPy also reads themes from `~/.ohmpy/themes/`, which is useful when you
want to share a theme between installations.
