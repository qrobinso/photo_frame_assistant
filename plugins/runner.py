"""
PluginRunner — discovers plugin classes, schedules jobs, and executes plugin instances.

One instance is created at server startup and stored as app.plugin_runner.
"""

import importlib
import json
import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_playwright_cache: dict = {'result': None, 'checked_at': 0, 'detail': ''}
_PLAYWRIGHT_CACHE_TTL = 60  # seconds


class PluginRunner:
    def __init__(self, app, db, upload_folder: str):
        self.app = app
        self.db = db
        self.upload_folder = upload_folder
        self._registry: dict[str, type] = {}   # plugin_id -> class
        self._manifests: dict[str, dict] = {}  # plugin_id -> manifest dict
        self._scheduler = None
        self._plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_and_register(self):
        """Scan the plugins/ directory and load all plugin classes."""
        self._registry.clear()
        self._manifests.clear()

        for entry in os.scandir(self._plugins_dir):
            if not entry.is_dir():
                continue
            manifest_path = os.path.join(entry.path, 'manifest.json')
            if not os.path.exists(manifest_path):
                continue

            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)

                plugin_id = manifest['plugin_id']
                module_name = manifest.get('module', 'plugin')
                class_name = manifest['class']

                # Dynamic import: plugins.<plugin_id>.<module>
                mod = importlib.import_module(f'plugins.{plugin_id}.{module_name}')
                cls = getattr(mod, class_name)

                self._registry[plugin_id] = cls
                self._manifests[plugin_id] = manifest
                logger.debug(f"Registered plugin: {plugin_id} ({class_name})")

            except Exception as exc:
                logger.error(f"Failed to load plugin from {entry.path}: {exc}", exc_info=True)

    def list_available_plugins(self) -> list[dict]:
        """Return metadata for each registered plugin class (for the UI catalog)."""
        result = []
        for plugin_id, cls in self._registry.items():
            try:
                instance = cls()
                result.append({
                    'plugin_id':    plugin_id,
                    'display_name': instance.display_name,
                    'description':  instance.description,
                    'default_cron': instance.default_cron,
                    'config_schema': instance.config_schema,
                    'version':      self._manifests[plugin_id].get('version', '1.0.0'),
                })
            except Exception as exc:
                logger.warning(f"Could not introspect plugin {plugin_id}: {exc}")
        return result

    def get_plugin_class(self, plugin_id: str):
        return self._registry.get(plugin_id)

    # ------------------------------------------------------------------
    # APScheduler wiring
    # ------------------------------------------------------------------

    def set_scheduler(self, scheduler):
        """Pass the raw APScheduler BackgroundScheduler instance."""
        self._scheduler = scheduler

    def load_active_plugin_jobs(self):
        """Add APScheduler jobs for all enabled plugin instances at startup."""
        from model import PluginInstance
        with self.app.app_context():
            instances = PluginInstance.query.filter_by(enabled=True).all()
            for inst in instances:
                try:
                    self._add_job(inst)
                except Exception as exc:
                    logger.error(f"Failed to schedule plugin instance {inst.id}: {exc}")
        logger.info(f"Plugins: {len(self._registry)} registered, {len(instances)} scheduled.")

    def _add_job(self, instance):
        """Register or replace the APScheduler cron job for a plugin instance."""
        if self._scheduler is None:
            logger.warning("PluginRunner has no scheduler — cannot schedule job.")
            return

        job_id = f'plugin_{instance.id}'
        parts = instance.cron.split()
        if len(parts) != 5:
            logger.error(f"Invalid cron '{instance.cron}' for instance {instance.id}")
            return

        minute, hour, day, month, day_of_week = parts

        self._scheduler.add_job(
            func=self.execute_instance,
            trigger='cron',
            args=[instance.id],
            id=job_id,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            replace_existing=True,
        )
        logger.debug(f"Scheduled plugin job {job_id} with cron '{instance.cron}'")

    def _remove_job(self, instance_id: int):
        """Remove the APScheduler job for a plugin instance."""
        if self._scheduler is None:
            return
        job_id = f'plugin_{instance_id}'
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed plugin job {job_id}")
        except Exception:
            pass  # Job may not exist (e.g. was disabled)

    def trigger_now(self, instance_id: int):
        """Run a plugin instance immediately in a background thread."""
        t = threading.Thread(target=self.execute_instance, args=[instance_id], daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_instance(self, instance_id: int):
        """
        Execute a plugin instance: generate an image and update the owned Photo.
        Called by APScheduler or directly for manual runs.
        """
        from model import PluginInstance, PluginRunLog, Photo
        from services.photo_processor import PhotoProcessor

        start = time.time()
        with self.app.app_context():
            instance = PluginInstance.query.get(instance_id)
            if instance is None:
                logger.error(f"Plugin instance {instance_id} not found")
                return

            if not instance.enabled:
                logger.info(f"Plugin instance {instance_id} is disabled — skipping")
                return

            cls = self.get_plugin_class(instance.plugin_id)
            if cls is None:
                err = f"Plugin class '{instance.plugin_id}' not registered"
                logger.error(err)
                self._record_run(instance, False, err, 0)
                return

            logger.info(f"Running plugin instance {instance_id} ({instance.plugin_id}: {instance.name})")
            error_msg = None
            success = False

            try:
                plugin = cls()
                plugin._upload_folder = self.upload_folder

                # Decrypt any encrypted secret config values before passing to the plugin
                from plugins.crypto import decrypt_secrets
                run_config = decrypt_secrets(instance.config or {}, plugin.config_schema)

                # Call the plugin
                image_bytes = plugin.generate(run_config)

                if not image_bytes:
                    raise ValueError("Plugin returned empty bytes")

                # Determine stable filenames
                base_name = f'plugin_{instance.id}.jpg'
                raw_path = os.path.join(self.upload_folder, base_name)

                # Write raw image
                with open(raw_path, 'wb') as f:
                    f.write(image_bytes)

                # Generate orientation variants
                processor = PhotoProcessor()
                portrait_path = processor.process_for_orientation(raw_path, 'portrait', crop_anchor='top')
                landscape_path = processor.process_for_orientation(raw_path, 'landscape', crop_anchor='top')

                portrait_name = os.path.basename(portrait_path) if portrait_path else base_name
                landscape_name = os.path.basename(landscape_path) if landscape_path else base_name

                # Generate thumbnail
                thumb_name = f'thumb_plugin_{instance.id}.jpg'
                thumb_path = os.path.join(self.upload_folder, 'thumbnails', thumb_name)
                os.makedirs(os.path.join(self.upload_folder, 'thumbnails'), exist_ok=True)

                from PIL import Image
                with Image.open(raw_path) as img:
                    img.thumbnail((400, 400))
                    img.save(thumb_path, 'JPEG', quality=85)

                # Update the Photo record
                photo = Photo.query.get(instance.photo_id)
                if photo is None:
                    raise ValueError(f"Owned Photo {instance.photo_id} not found in DB")

                photo.filename = base_name
                photo.portrait_version = portrait_name
                photo.landscape_version = landscape_name
                photo.thumbnail = thumb_name
                photo.uploaded_at = datetime.utcnow()

                instance.last_run_at = datetime.utcnow()
                instance.last_run_ok = True
                instance.last_error = None
                success = True

            except Exception as exc:
                error_msg = str(exc)
                logger.error(f"Plugin instance {instance_id} failed: {exc}", exc_info=True)
                instance.last_run_at = datetime.utcnow()
                instance.last_run_ok = False
                instance.last_error = error_msg

            duration_ms = int((time.time() - start) * 1000)
            self._record_run(instance, success, error_msg, duration_ms)
            self.db.session.commit()

    def _record_run(self, instance, success: bool, error: str | None, duration_ms: int):
        """Append a PluginRunLog entry and trim old ones."""
        from model import PluginRunLog

        log = PluginRunLog(
            instance_id=instance.id,
            ran_at=datetime.utcnow(),
            success=success,
            error=error,
            duration_ms=duration_ms,
        )
        self.db.session.add(log)
        self.db.session.flush()

        # Keep only the last 50 log entries per instance
        logs = (
            PluginRunLog.query
            .filter_by(instance_id=instance.id)
            .order_by(PluginRunLog.ran_at.desc())
            .all()
        )
        for old in logs[50:]:
            self.db.session.delete(old)

    # ------------------------------------------------------------------
    # Placeholder image
    # ------------------------------------------------------------------

    def create_placeholder_photo(self, instance_id: int) -> tuple[str, str, str, str]:
        """
        Write a dark grey placeholder JPEG for a newly created plugin instance.
        Returns (filename, portrait_version, landscape_version, thumbnail).
        """
        from PIL import Image, ImageDraw

        base_name = f'plugin_{instance_id}.jpg'
        raw_path = os.path.join(self.upload_folder, base_name)

        # 1200×1600 dark slate placeholder with centred label
        img = Image.new('RGB', (1200, 1600), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)
        draw.text((600, 800), 'Plugin — not yet run', fill=(100, 100, 120), anchor='mm')
        img.save(raw_path, 'JPEG', quality=85)

        # Orientation variants
        from services.photo_processor import PhotoProcessor
        processor = PhotoProcessor()
        portrait_path = processor.process_for_orientation(raw_path, 'portrait', crop_anchor='top')
        landscape_path = processor.process_for_orientation(raw_path, 'landscape', crop_anchor='top')

        portrait_name = os.path.basename(portrait_path) if portrait_path else base_name
        landscape_name = os.path.basename(landscape_path) if landscape_path else base_name

        # Thumbnail
        thumb_name = f'thumb_plugin_{instance_id}.jpg'
        thumb_path = os.path.join(self.upload_folder, 'thumbnails', thumb_name)
        os.makedirs(os.path.join(self.upload_folder, 'thumbnails'), exist_ok=True)
        with Image.open(raw_path) as thumb_img:
            thumb_img.thumbnail((400, 400))
            thumb_img.save(thumb_path, 'JPEG', quality=85)

        return base_name, portrait_name, landscape_name, thumb_name

    # ------------------------------------------------------------------
    # Playwright status
    # ------------------------------------------------------------------

    @staticmethod
    def check_playwright() -> dict:
        """
        Check Playwright Chromium status. Returns dict:
          {'ok': bool, 'detail': str}
        detail values: 'ok', 'missing_browser', 'missing_system_libs', 'unknown'
        Result cached for 60s.
        """
        global _playwright_cache
        now = time.time()
        if _playwright_cache['result'] is not None and (now - _playwright_cache['checked_at']) < _PLAYWRIGHT_CACHE_TTL:
            return {'ok': _playwright_cache['result'], 'detail': _playwright_cache['detail']}

        detail = 'unknown'
        result = False
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            result = True
            detail = 'ok'
        except Exception as exc:
            msg = str(exc)
            # Distinguish: binary exists but missing system libs vs. browser not downloaded
            if 'shared libraries' in msg or 'cannot open shared object' in msg or 'No such file or directory' in msg.lower():
                detail = 'missing_system_libs'
            elif 'executable' in msg.lower() or 'Executable' in msg:
                detail = 'missing_browser'
            else:
                detail = 'missing_system_libs' if _chromium_binary_exists() else 'missing_browser'

        _playwright_cache['result'] = result
        _playwright_cache['detail'] = detail
        _playwright_cache['checked_at'] = now
        return {'ok': result, 'detail': detail}


def _chromium_binary_exists() -> bool:
    """Check if the Chromium binary has been downloaded."""
    import glob
    patterns = [
        os.path.expanduser('~/.cache/ms-playwright/chromium*/chrome-headless-shell-linux64/chrome-headless-shell'),
        os.path.expanduser('~/.cache/ms-playwright/chromium*/chrome-linux/chrome'),
    ]
    return any(glob.glob(p) for p in patterns)
