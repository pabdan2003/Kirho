# External Kirho themes

This directory contains themes that can be distributed separately from Kirho.
They are not included in the macOS DMG. To install one, copy its `*.json` file
to `~/.kirho/themes/`, then choose **Reload themes** in the app settings.

## Create your own theme

1. Open the themes folder from Kirho Settings; it includes a README and
   `theme-template.json.example`.
2. Copy the template to, for example, `my_theme.json` and edit the colors
   (hex format: `#rrggbb`).
3. Choose **Reload themes** — `My Theme` will appear in the selector.

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

## Development directory

When running Kirho from its source repository, it also reads this directory,
which makes it convenient to develop and share downloadable themes.
