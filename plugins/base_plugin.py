"""
PluginBase — abstract base class for all Photo Frame Assistant plugins.

Plugin authors subclass this and implement the required properties/methods.
The base class provides helpers like render_html_to_image() so plugins don't
need to manage Playwright directly.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class PluginBase(ABC):
    """
    Base class for all PFA plugins.

    Lifecycle:
        1. PluginRunner instantiates the plugin class.
        2. PluginRunner injects _upload_folder before calling generate().
        3. generate() returns raw JPEG bytes.
        4. PluginRunner saves the bytes, regenerates orientation variants,
           and updates the owned Photo record.

    Plugin authors must implement:
        - plugin_id (property)
        - display_name (property)
        - description (property)
        - default_cron (property)
        - config_schema (property)
        - generate(config) -> bytes
    """

    def __init__(self):
        self._upload_folder: str = ''

    # ------------------------------------------------------------------
    # Required properties — implement in every plugin
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Snake_case identifier matching the plugins/ subdirectory name."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the UI."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One or two sentences explaining what this plugin does."""

    @property
    @abstractmethod
    def default_cron(self) -> str:
        """
        Default APScheduler cron string for scheduling.
        Format: 'minute hour day month day_of_week'
        Examples:
          '* * * * *'    — every minute
          '0 * * * *'    — every hour
          '0 9 * * *'    — daily at 09:00
        """

    @property
    @abstractmethod
    def config_schema(self) -> dict:
        """
        Describes the configuration fields this plugin needs.
        Used by plugins.html to render the instance config form dynamically.

        Format:
        {
            "field_key": {
                "type":     "string" | "integer" | "select",
                "label":    "Human Label",
                "required": True | False,        # default False
                "secret":   True | False,         # renders as password input
                "default":  <value>,              # shown as placeholder
                "options":  ["a", "b", ...],      # only for type=select
            },
            ...
        }
        """

    # ------------------------------------------------------------------
    # Required method — implement in every plugin
    # ------------------------------------------------------------------

    @abstractmethod
    def generate(self, config: dict) -> bytes:
        """
        Core plugin logic. Called by the scheduler on each run.

        Args:
            config: The instance's stored config dict (from the DB).

        Returns:
            Raw JPEG bytes for the image. Use render_html_to_image() to
            convert an HTML string to JPEG, or build the bytes directly
            with Pillow if no HTML rendering is needed.

        Raises:
            Any exception will be caught by PluginRunner, logged, and
            recorded in PluginRunLog with success=False.
        """

    # ------------------------------------------------------------------
    # Provided helpers — use freely in generate()
    # ------------------------------------------------------------------

    def render_html_to_image(
        self,
        html: str,
        width: int = 1200,
        height: int = 1600,
        wait_until: str = 'networkidle',
        quality: int = 90,
    ) -> bytes:
        """
        Render an HTML string to a JPEG screenshot using Playwright Chromium.

        Args:
            html:       Full HTML document string (include <!DOCTYPE html> etc.)
            width:      Viewport width in pixels (default 1200)
            height:     Viewport height in pixels (default 1600)
            wait_until: Playwright wait condition — 'load', 'domcontentloaded',
                        'networkidle', or 'commit' (default 'networkidle')
            quality:    JPEG quality 0–100 (default 90)

        Returns:
            Raw JPEG bytes.

        Raises:
            RuntimeError if Playwright or Chromium is not installed.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright"
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    viewport={'width': width, 'height': height},
                    device_scale_factor=2,
                )
                page = ctx.new_page()
                page.set_content(html, wait_until=wait_until)
                data = page.screenshot(type='jpeg', quality=quality, full_page=False)
                browser.close()
            return data
        except Exception as exc:
            msg = str(exc)
            if 'executable' in msg.lower() or 'chromium' in msg.lower() or 'browser' in msg.lower():
                raise RuntimeError(
                    "Playwright Chromium browser not installed. "
                    "Run: playwright install chromium  "
                    "(or use the Install button on the Plugins page)"
                ) from exc
            raise

    def render_url_to_image(
        self,
        url: str,
        width: int = 1200,
        height: int = 1600,
        wait_until: str = 'networkidle',
        quality: int = 90,
        selector: str | None = None,
    ) -> bytes:
        """
        Navigate to a URL with Playwright and return a JPEG screenshot.

        Args:
            url:        The URL to load.
            width:      Viewport width in pixels (default 1200)
            height:     Viewport height in pixels (default 1600)
            wait_until: Playwright wait condition (default 'networkidle')
            quality:    JPEG quality 0–100 (default 90)
            selector:   Optional CSS selector — if given, screenshots just
                        that element instead of the full viewport.

        Returns:
            Raw JPEG bytes.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Playwright is not installed. Run: pip install playwright")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    viewport={'width': width, 'height': height},
                    device_scale_factor=2,
                )
                page = ctx.new_page()
                page.goto(url, wait_until=wait_until, timeout=30000)
                if selector:
                    el = page.query_selector(selector)
                    data = el.screenshot(type='jpeg', quality=quality) if el else \
                           page.screenshot(type='jpeg', quality=quality, full_page=False)
                else:
                    data = page.screenshot(type='jpeg', quality=quality, full_page=False)
                browser.close()
            return data
        except Exception as exc:
            msg = str(exc)
            if 'executable' in msg.lower() or 'chromium' in msg.lower() or 'browser' in msg.lower():
                raise RuntimeError(
                    "Playwright Chromium browser not installed. "
                    "Run: playwright install chromium"
                ) from exc
            raise

    def log_info(self, msg: str) -> None:
        logger.info(f"[plugin:{self.plugin_id}] {msg}")

    def log_warning(self, msg: str) -> None:
        logger.warning(f"[plugin:{self.plugin_id}] {msg}")

    def log_error(self, msg: str) -> None:
        logger.error(f"[plugin:{self.plugin_id}] {msg}")
