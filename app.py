"""
app.py — Application factory for Photo Frame Assistant
"""
import os
import json
import secrets
import atexit
import logging

from flask import Flask
from core.logging import setup_logger
from model import db, init_db, Photo, PhotoFrame, PlaylistEntry, Playlist, CustomPlaylist, EventLog, PluginInstance, PluginRunLog
from routes.plugins import plugin_bp
from routes.integrations import integrations_bp
from routes.system import system_bp
from routes.playlists import playlists_bp
from routes.overlays import overlays_bp
from routes.photos import photos_bp
from routes.frames import frames_bp
from routes.frame_client import frame_client_bp
from settings.persistence import (
    load_server_settings, load_mqtt_settings, ZEROCONF_PORT
)

logger = setup_logger()


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
    basedir = os.path.abspath(os.path.dirname(__file__))

    UPLOAD_FOLDER = os.environ.get('UPLOAD_PATH', os.path.join(basedir, 'uploads'))
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

    DB_PATH = os.environ.get('DB_PATH', os.path.join(basedir, 'app.db'))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    CONFIG_DIR = os.environ.get('CONFIG_PATH', os.path.join(basedir, 'config'))
    app.config['CONFIG_DIR'] = CONFIG_DIR

    # ------------------------------------------------------------------
    # Bootstrap directories
    # ------------------------------------------------------------------
    CREDENTIALS_DIR = os.path.join(CONFIG_DIR, 'credentials')
    INTEGRATIONS_DIR = os.path.join(basedir, 'integrations')
    OVERLAYS_DIR = os.path.join(INTEGRATIONS_DIR, 'overlays')

    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    os.makedirs(OVERLAYS_DIR, exist_ok=True)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'thumbnails'), exist_ok=True)

    # ------------------------------------------------------------------
    # Load server settings to configure Flask limits and log level
    # ------------------------------------------------------------------
    server_settings = load_server_settings()
    app.config['MAX_CONTENT_LENGTH'] = server_settings.get('max_upload_size', 10) * 1024 * 1024
    logging.getLogger().setLevel(server_settings.get('log_level', 'INFO'))

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    init_db(app)
    with app.app_context():
        db.create_all()

    # ------------------------------------------------------------------
    # Template filter
    # ------------------------------------------------------------------
    @app.template_filter('from_json')
    def from_json_filter(value):
        """Template filter to parse JSON strings."""
        try:
            return json.loads(value) if value else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    # ------------------------------------------------------------------
    # Generic error handler (hides internals in production)
    # ------------------------------------------------------------------
    @app.errorhandler(500)
    def handle_500(e):
        logger.exception("Internal server error")
        if app.debug:
            raise e
        return {'error': 'Internal server error'}, 500

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    app.register_blueprint(plugin_bp)
    app.register_blueprint(integrations_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(playlists_bp)
    app.register_blueprint(overlays_bp)
    app.register_blueprint(photos_bp)
    app.register_blueprint(frames_bp)
    app.register_blueprint(frame_client_bp)

    # ------------------------------------------------------------------
    # Services (requires app context)
    # ------------------------------------------------------------------
    with app.app_context():
        _init_services(app)

    # ------------------------------------------------------------------
    # Cleanup on exit
    # ------------------------------------------------------------------
    atexit.register(_cleanup_services, app)

    return app


def _init_services(app):
    """Instantiate and attach all application services to the app object."""
    cfg = app.config
    basedir = os.path.abspath(os.path.dirname(__file__))
    CONFIG_DIR = cfg.get('CONFIG_DIR', os.path.join(basedir, 'config'))

    # Config file paths
    WEATHER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'weather_config.json')
    METADATA_CONFIG_PATH = os.path.join(CONFIG_DIR, 'metadata_config.json')
    QRCODE_CONFIG_PATH = os.path.join(CONFIG_DIR, 'qrcode_config.json')

    # Lazy-import services here to avoid circular imports
    from services.photo_processor import PhotoProcessor
    from services.discovery import FrameDiscovery
    from services.frame_timing import FrameTimingManager
    from integrations.overlays.weather import WeatherIntegration
    from integrations.overlays.metadata import MetadataIntegration
    from integrations.overlays.qrcode import QRCodeIntegration
    from integrations.overlays.manager import OverlayManager
    from integrations.mqtt import MQTTIntegration
    from plugins.runner import PluginRunner

    # Core services
    app.photo_processor = PhotoProcessor()
    app.frame_discovery = FrameDiscovery(port=ZEROCONF_PORT)

    # Integrations (overlays)
    try:
        app.weather_integration = WeatherIntegration(WEATHER_CONFIG_PATH)
        app.metadata_integration = MetadataIntegration(METADATA_CONFIG_PATH)
        app.qrcode_integration = QRCodeIntegration(QRCODE_CONFIG_PATH)
        app.overlay_manager = OverlayManager(app.weather_integration, app.metadata_integration)
        logger.debug("Weather, Metadata, QR Code integrations and OverlayManager initialized.")
    except Exception as e:
        logger.error(f"Error initializing integrations: {e}", exc_info=True)

    # Frame timing manager
    ftm_models = {'PhotoFrame': PhotoFrame, 'Photo': Photo, 'PlaylistEntry': PlaylistEntry}
    app.frame_timing_manager = FrameTimingManager(app, db, ftm_models)
    app.frame_timing_manager.start()
    logger.debug("FrameTimingManager initialized and started.")

    # Plugin scheduler (APScheduler for cron-based plugin jobs)
    from apscheduler.schedulers.background import BackgroundScheduler
    app.plugin_scheduler = BackgroundScheduler()
    app.plugin_scheduler.start()
    logger.debug("Plugin scheduler started.")

    # Plugin runner
    app.plugin_runner = PluginRunner(app, db, cfg['UPLOAD_FOLDER'])
    app.plugin_runner.discover_and_register()
    app.plugin_runner.set_scheduler(app.plugin_scheduler)
    app.plugin_runner.load_active_plugin_jobs()
    logger.info("Plugin runner initialized.")

    # MQTT (conditional)
    app.mqtt_integration = None
    mqtt_settings = load_mqtt_settings()
    if mqtt_settings.get('enabled', False):
        try:
            logger.info("MQTT enabled, initializing integration...")
            app.mqtt_integration = MQTTIntegration(
                mqtt_settings, cfg['UPLOAD_FOLDER'],
                PhotoFrame, db, PlaylistEntry, app, CustomPlaylist
            )
            logger.info(f"MQTT Integration initialized. Status: {app.mqtt_integration.status}")
        except Exception as e:
            logger.error(f"Failed to initialize MQTT integration: {e}", exc_info=True)
    else:
        logger.debug("MQTT integration is disabled in settings.")

    # Discovery (conditional)
    startup_settings = load_server_settings()
    if startup_settings.get('discovery_enabled', True):
        try:
            if not app.frame_discovery._running:
                logger.info("Starting frame discovery service...")
                app.frame_discovery.start()
                logger.info("Frame discovery service started.")
        except Exception as e:
            logger.error(f"Error starting frame discovery service: {e}")
    else:
        logger.debug("Frame discovery is disabled in settings — skipping startup.")


def _cleanup_services(app):
    """Stop all services cleanly on application exit."""
    logger.debug("Shutting down application services...")
    try:
        if hasattr(app, 'frame_discovery') and app.frame_discovery._running:
            logger.info("Stopping frame discovery service...")
            app.frame_discovery.stop()
            logger.info("Frame discovery service stopped.")
    except Exception as e:
        logger.error(f"Error stopping frame discovery service: {e}")

    if hasattr(app, 'frame_timing_manager') and app.frame_timing_manager:
        app.frame_timing_manager.stop()
        logger.debug("FrameTimingManager stopped.")

    if hasattr(app, 'plugin_scheduler') and app.plugin_scheduler.running:
        app.plugin_scheduler.shutdown()
        logger.debug("Plugin scheduler shut down.")

    if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
        app.mqtt_integration.stop()
        logger.info("MQTT Integration stopped.")

    logger.debug("Application cleanup complete.")
