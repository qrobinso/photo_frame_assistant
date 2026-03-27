# Photo Frame Assistant

A Flask web app that manages photo frames — uploading/generating images, organizing them into playlists, and serving them to connected e-ink or digital photo frames. Extensible via a plugin system and integration layer.

## Architecture Overview

```
app.py              ← Flask app factory (create_app), registers blueprints, inits services
server.py           ← Entry point: imports create_app(), runs dev server on port 5000
model.py            ← SQLAlchemy ORM models (Photo, PhotoFrame, Playlist, etc.)
```

### Directory Structure

```
core/               ← Foundational utilities (logging, image format conversion)
helpers/            ← Pure helper functions (EXIF, file validation, frame timing, image pipeline)
services/           ← Stateful services with background threads
settings/           ← JSON-based config persistence (server, MQTT, photo generation)
routes/             ← Flask Blueprints (one per domain: frames, photos, playlists, etc.)
integrations/       ← MQTT + overlay integrations with abstract base classes
plugins/            ← Auto-discovered content generator plugins
templates/          ← Jinja2 templates; partials/ has reusable components
  partials/         ← frames/, gallery/, generate/, integrations/, settings/
static/             ← CSS, JS, assets
config/             ← JSON settings files (server_settings, mqtt_config, weather_config, etc.)
uploads/            ← Photos and plugin-generated images
logs/               ← Rotating log files (server.log)
```

---

## Key Modules

### Services (`services/`)

Services encapsulate business logic and run background threads. Instantiated in `app.py:_init_services()` and attached to the Flask `app` object.

| Service | File | Purpose |
|---------|------|---------|
| PhotoProcessor | `services/photo_processor.py` | Orientation handling, resize, crop, padding, contrast/saturation adjustments |
| PhotoGenerator | `services/photo_generator.py` | AI image generation (DALL-E, Stability AI, custom) |
| FrameDiscovery | `services/discovery.py` | Zeroconf advertisement and frame discovery |
| FrameTimingManager | `services/frame_timing.py` | Wake/sleep scheduling, sync groups, deep sleep windows |
| EventLogger | `services/event_logger.py` | Frame event audit trail |

### Routes (`routes/`)

Nine Flask Blueprints registered at app creation:

| Blueprint | File | Key Endpoints |
|-----------|------|---------------|
| frames_bp | `routes/frames.py` | Frame CRUD, settings, virtual viewer (`/frame/<id>`) |
| photos_bp | `routes/photos.py` | Upload, gallery, thumbnails, EXIF extraction |
| playlists_bp | `routes/playlists.py` | Playlist CRUD, photo ordering, frame assignment |
| generation_bp | `routes/generation.py` | AI image generation prompts and history |
| overlays_bp | `routes/overlays.py` | Weather, metadata, QR code overlay config |
| frame_client_bp | `routes/frame_client.py` | Frame device communication (connect, wake, photo request) |
| integrations_bp | `routes/integrations.py` | MQTT and weather integration settings |
| system_bp | `routes/system.py` | Server info, settings, maintenance |
| plugin_bp | `plugin_routes.py` | Plugin management (see Plugin System below) |

### Integrations (`integrations/`)

Abstract base classes in `integrations/base.py`:
- **Integration** — base for all (initialize, shutdown, config schema, test_connection)
- **SmartHomeIntegration** — MQTT/HA (publish_state, register_frame, handle_command)
- **OverlayIntegration** — overlay providers (get_overlay, get_available_overlays)

Implementations:
- `integrations/mqtt/client.py` — MQTT pub/sub for frame commands and state
- `integrations/overlays/manager.py` — Orchestrates overlays for a photo
- `integrations/overlays/weather.py` — OpenWeatherMap
- `integrations/overlays/metadata.py` — Frame/photo info
- `integrations/overlays/qrcode.py` — QR code generation

### Helpers (`helpers/`)

| File | Purpose |
|------|---------|
| `helpers/file_helpers.py` | EXIF extraction, upload validation, allowed extensions |
| `helpers/frame_helpers.py` | Deep sleep window calc, wake time, photo carousel logic |
| `helpers/image_pipeline.py` | Multi-step image processing (color, contrast, scaling) |
| `helpers/system_helpers.py` | Device capability detection, resource checks |

### Core (`core/`)

| File | Purpose |
|------|---------|
| `core/logging.py` | Rotating file + console logging setup |
| `core/image_conversion.py` | HEIC→JPEG, color space, format conversions |

### Settings (`settings/`)

| File | Purpose |
|------|---------|
| `settings/persistence.py` | Load/save JSON config files from `config/` directory |

Config files: `server_settings.json`, `mqtt_config.json`, `photogen_settings.json`, `weather_config.json`, `metadata_config.json`, `qrcode_config.json`

---

## Database Models (`model.py`)

| Model | Purpose |
|-------|---------|
| Photo | Uploaded/generated images with portrait, landscape, thumbnail variants |
| PhotoFrame | Physical/virtual frames with display settings, battery, overlay prefs |
| Playlist | Ordered photo collections; frames reference one playlist |
| PlaylistEntry | Joins photos to playlists with ordering |
| SyncGroup | Groups of frames that wake simultaneously |
| EventLog | Frame connection and activity audit trail |
| PluginInstance | Configured plugin instance (config, cron, enabled) |
| PluginRunLog | Plugin execution history (success, error, duration) |

---

## Data Flow: Photo Display on Frame

1. Frame wakes → connects to server via `frame_client_bp`
2. FrameTimingManager updates wake times
3. Frame requests photo → route fetches from assigned Playlist
4. PhotoProcessor converts to frame's orientation with padding/cropping
5. OverlayManager applies enabled overlays (weather, metadata, QR)
6. JPEG bytes sent to frame

---

## Plugin System

Plugins generate images (JPEG) displayed on frames via playlists. Auto-discovered from `plugins/` at startup.

### Key Plugin Files

| File | Purpose |
|------|---------|
| `plugins/base.py` | Abstract base class — read this first |
| `plugins/runner.py` | Discovery, scheduling, execution |
| `plugins/crypto.py` | Fernet encryption for secret config values |
| `plugin_routes.py` | Flask Blueprint — `/plugins` and `/api/plugins` routes |

### Creating a New Plugin

Create `plugins/my_plugin/` with three files:

**`__init__.py`** — empty

**`manifest.json`**:
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

**`plugin.py`**:
```python
from plugins.base import PluginBase

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

### PluginBase API

**Required attributes:** `plugin_id`, `display_name`, `description`, `default_cron`, `config_schema`

**Required method:** `generate(self, config: dict) -> bytes` — returns JPEG bytes (target 1200×1600)

**Helper methods:**
```python
self.render_html_to_image(html, width=1200, height=1600, wait_until='networkidle', quality=90) -> bytes
self.render_url_to_image(url, width=1200, height=1600, wait_until='networkidle', quality=90, selector=None) -> bytes
self.log_info("message")
self.log_warning("message")
self.log_error("message")
```

> `render_html_to_image` and `render_url_to_image` require Playwright Chromium, installed via the Plugins page UI.

### config_schema Field Types

```python
{
    "field_key": {
        "type":     "string" | "integer" | "select",  # required
        "label":    "Human-Readable Label",            # required
        "required": True,                              # default: False
        "secret":   True,                              # default: False — encrypted at rest
        "default":  "value",                           # placeholder/default
        "options":  ["a", "b", "c"],                   # required for type "select"
    }
}
```

Secret fields use Fernet encryption. The UI sends sentinel `__set__` to preserve existing secrets.

### Plugin Execution Pipeline

1. APScheduler triggers cron → PluginRunner instantiates class with `_upload_folder`
2. Secret config decrypted → `generate(config)` called → JPEG bytes returned
3. Image saved to `uploads/plugin_<instance_id>.jpg`
4. Portrait/landscape variants and 400×400 thumbnail generated
5. Owned `Photo` record updated → run result recorded in `PluginRunLog`

Each instance owns exactly one `Photo` that can be added to any playlist.

### Existing Plugins

| Plugin | Key Technique | Cron |
|--------|--------------|------|
| `clock` | Pure HTML/CSS, Playwright render | `* * * * *` |
| `news_headlines` | NewsAPI fetch → styled HTML → Playwright | `0 * * * *` |
| `front_pages` | Playwright scrape → PIL crop/fit | `0 7 * * *` |
| `weather_dashboard` | Weather API → HTML dashboard | configurable |
| `sports_scores` | Sports data API → styled HTML | configurable |
| `nasa_apod` | NASA APOD API → image fetch | `0 8 * * *` |
| `unsplash` | Unsplash API → random photo | configurable |
| `immich` | Immich photo library integration | configurable |
| `product_hunt` | Product Hunt trending → HTML | configurable |
| `this_day_in_history` | Historical events → HTML | configurable |
| `crypto` | Cryptocurrency prices → HTML | configurable |

### Plugin Checklist

- [ ] `plugins/my_plugin/` directory with empty `__init__.py`
- [ ] `manifest.json` with correct `plugin_id`, `class`, `module`
- [ ] `plugin.py` extends `PluginBase`, implements `generate()` returning JPEG bytes (1200×1600)
- [ ] `plugin_id` matches directory name and `manifest.json`
- [ ] API keys/credentials use `"secret": True` in `config_schema`
- [ ] Reload via UI (`/plugins` → Reload) or `POST /api/plugins/reload`
