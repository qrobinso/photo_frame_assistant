# Photo Frame Assistant — Plugin Development Guide

This document explains how to build plugins for the Photo Frame Assistant app. Plugins generate images (JPEG) that get displayed on connected photo frames via playlists.

## Plugin System Overview

Plugins are discovered automatically at startup from the `plugins/` directory. Each plugin runs on a cron schedule, calls `generate()`, and the resulting image is stored as a `Photo` record that can be added to any playlist.

---

## Creating a New Plugin

### 1. Directory Structure

Create a new directory under `plugins/` with the plugin's snake_case ID:

```
plugins/
└── my_plugin/
    ├── __init__.py     # empty
    ├── manifest.json
    └── plugin.py
```

### 2. manifest.json

Required metadata file used for discovery:

```json
{
  "plugin_id": "my_plugin",
  "display_name": "My Plugin",
  "description": "One or two sentences shown in the plugin catalog UI.",
  "version": "1.0.0",
  "class": "MyPlugin",
  "module": "plugin"
}
```

- `plugin_id` must match the directory name exactly (snake_case)
- `class` is the Python class name inside `module`
- `module` defaults to `"plugin"` (i.e., `plugin.py`)

### 3. plugin.py — Implement the Base Class

```python
from plugins.base_plugin import PluginBase

class MyPlugin(PluginBase):

    plugin_id    = "my_plugin"
    display_name = "My Plugin"
    description  = "Short description for the UI."
    default_cron = "0 * * * *"   # APScheduler 5-part cron (hourly)

    config_schema = {
        "api_key": {
            "type":     "string",
            "label":    "API Key",
            "required": True,
            "secret":   True,       # value encrypted at rest
        },
        "count": {
            "type":    "integer",
            "label":   "Number of Items",
            "default": 5,
        },
        "theme": {
            "type":    "select",
            "label":   "Theme",
            "options": ["dark", "light"],
            "default": "dark",
        },
    }

    def generate(self, config: dict) -> bytes:
        """Return JPEG bytes at 1200x1600 (portrait)."""
        html = f"<html>...</html>"
        return self.render_html_to_image(html)
```

---

## Base Class API (`PluginBase`)

### Required Attributes

| Attribute      | Type   | Description |
|---------------|--------|-------------|
| `plugin_id`   | str    | Snake_case, must match directory name |
| `display_name`| str    | Shown in UI catalog |
| `description` | str    | 1–2 sentences shown in UI |
| `default_cron`| str    | 5-part APScheduler cron string |
| `config_schema`| dict  | Field definitions for the config form |

### Required Method

```python
def generate(self, config: dict) -> bytes:
    ...
```

- `config` contains decrypted user-configured values
- Must return raw JPEG bytes
- Target image size: **1200×1600px** (portrait)

### Provided Helper Methods

```python
# Render an HTML string to JPEG via Playwright/Chromium
self.render_html_to_image(
    html,
    width=1200,
    height=1600,
    wait_until='networkidle',  # or 'load', 'domcontentloaded'
    quality=90,
) -> bytes

# Screenshot a live URL (or a CSS selector within it)
self.render_url_to_image(
    url,
    width=1200,
    height=1600,
    wait_until='networkidle',
    quality=90,
    selector=None,             # CSS selector to crop to
) -> bytes

# Logging (output tagged with plugin_id)
self.log_info("message")
self.log_warning("message")
self.log_error("message")
```

> **Note:** `render_html_to_image` and `render_url_to_image` require Chromium (Playwright).
> Users install it via the Plugins page → "Install Playwright" button.

---

## config_schema Field Types

Each key in `config_schema` is a field rendered in the create/edit instance form.

```python
config_schema = {
    "field_key": {
        "type":     "string" | "integer" | "select",  # required
        "label":    "Human-Readable Label",            # required
        "required": True,                              # default: False
        "secret":   True,                              # default: False — encrypts value at rest
        "default":  "value",                           # shown as placeholder/default
        "options":  ["a", "b", "c"],                   # required when type == "select"
    }
}
```

Secret fields are encrypted with Fernet symmetric encryption. The raw value is never exposed via the API after creation — the UI sends a sentinel value (`__set__`) to signal "keep existing secret."

---

## Execution Pipeline

When a plugin instance runs (on schedule or manually):

1. Plugin class is instantiated; `_upload_folder` is injected
2. Secret config values are decrypted
3. `generate(config)` is called → returns JPEG bytes
4. Image saved to `uploads/plugin_<instance_id>.jpg`
5. Portrait/landscape orientation variants and a 400×400 thumbnail are generated
6. The owned `Photo` record is updated
7. Run result recorded in `PluginRunLog`

The instance owns exactly one `Photo`. That photo can be added to any playlist.

---

## Existing Plugins (Reference Examples)

| Plugin | Key Technique | Cron |
|--------|--------------|------|
| `clock` | Pure HTML/CSS with Jinja-like f-strings, Playwright render | `* * * * *` |
| `news_headlines` | NewsAPI.org fetch → styled HTML → Playwright render | `0 * * * *` |
| `front_pages` | Playwright browser session to scrape image, PIL crop/fit | `0 7 * * *` |

---

## Key Files

| File | Purpose |
|------|---------|
| `plugins/base_plugin.py` | Abstract base class — read this first |
| `plugins/plugin_runner.py` | Discovery, scheduling, and execution logic |
| `plugin_routes.py` | Flask Blueprint — all `/plugins` and `/api/plugins` routes |
| `plugins/config_crypto.py` | Fernet encryption helpers for secret config values |
| `model.py` (`PluginInstance`, `PluginRunLog`) | Database models |
| `templates/plugins.html` | Full plugin management UI |

---

## Quick Checklist

- [ ] `plugins/my_plugin/` directory created
- [ ] `plugins/my_plugin/__init__.py` is empty
- [ ] `manifest.json` present with correct `plugin_id`, `class`, `module`
- [ ] `plugin.py` class extends `PluginBase` and implements `generate()`
- [ ] `generate()` returns JPEG bytes (target 1200×1600)
- [ ] `plugin_id` on class matches directory name and `manifest.json`
- [ ] Any API keys/credentials use `"secret": True` in `config_schema`
- [ ] Reload plugins via UI (`/plugins` → Reload) or `POST /api/plugins/reload`
