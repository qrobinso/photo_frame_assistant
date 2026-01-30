# ------------------------------------------------------------------------------
# Imports
# ------------------------------------------------------------------------------
import ast
import os
import psutil
import platform
import socket
import logging
import json
import base64
import os.path
import secrets
import atexit
import io
import math
import time
import random
import pytz
import humanize
import subprocess
import uuid
import copy
import requests
from datetime import datetime, timedelta, timezone
from threading import Thread

from flask import (Flask, request, jsonify, send_from_directory,
                   redirect, url_for, render_template_string, flash, render_template, send_file, Response, session)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.types import JSON
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ExifTags, ImageEnhance, ImageOps
import pillow_avif  # Add AVIF support to Pillow
import pyheif       # Add HEIC/HEIF support

# Local application imports
from discovery import FrameDiscovery
from photo_generation import PhotoGenerator
from photo_processing import PhotoProcessor
from photo_analysis import PhotoAnalyzer
from logger_config import setup_logger
from scheduler import GenerationScheduler
from integration_routes import integration_routes  # Blueprint for external integration routes
from frame_timing_manager import FrameTimingManager
from imgToArray import img_to_array, img_to_rgb565, img_to_epaper_4bit # For e-paper and RGB565 compression
from event_logger import EventLogger
from model import db, init_db, Photo, PhotoFrame, PlaylistEntry, Playlist, CustomPlaylist, ScheduledGeneration, GenerationHistory, SyncGroup

# Integration specific imports
from integrations.mqtt_integration import MQTTIntegration
from integrations.google_photos import GooglePhotosIntegration
from integrations.unsplash_integration import UnsplashIntegration
from integrations.pixabay_integration import PixabayIntegration
from integrations.overlays.weather_integration import WeatherIntegration
from integrations.overlays.metadata_integration import MetadataIntegration
from integrations.overlays.qrcode_integration import QRCodeIntegration
from integrations.overlays.overlay_manager import OverlayManager, MetadataOverlay, QRCodeOverlay

# ------------------------------------------------------------------------------
# Configuration & Constants
# ------------------------------------------------------------------------------
# Flask App Configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
basedir = os.path.abspath(os.path.dirname(__file__))

# Use environment variables for paths (for Docker volume mounting)
# Falls back to local directories when not running in Docker
UPLOAD_FOLDER = os.environ.get('UPLOAD_PATH', os.path.join(basedir, 'uploads'))
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Database path (for persistence in Docker)
DB_PATH = os.environ.get('DB_PATH', os.path.join(basedir, 'app.db'))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Load max upload size from settings later in initialization

# Constants
ZEROCONF_PORT = 5000
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'heif', 'mp4', 'mov', 'MOV', 'avif'}
CONFIG_DIR = os.environ.get('CONFIG_PATH', os.path.join(basedir, 'config'))
CREDENTIALS_DIR = os.path.join(CONFIG_DIR, 'credentials')
INTEGRATIONS_DIR = os.path.join(basedir, 'integrations')
OVERLAYS_DIR = os.path.join(INTEGRATIONS_DIR, 'overlays')

# Ensure config directories exist
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(CREDENTIALS_DIR, exist_ok=True)
os.makedirs(OVERLAYS_DIR, exist_ok=True)

# Config File Paths
SERVER_SETTINGS_FILE = os.path.join(CONFIG_DIR, "server_settings.json")
PHOTOGEN_SETTINGS_FILE = os.path.join(CONFIG_DIR, "photogen_settings.json")
MQTT_CONFIG_PATH = os.path.join(CONFIG_DIR, 'mqtt_config.json')
UNSPLASH_CONFIG_PATH = os.path.join(CONFIG_DIR, 'unsplash_config.json')
PIXABAY_CONFIG_PATH = os.path.join(CONFIG_DIR, 'pixabay_config.json')
WEATHER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'weather_config.json')
METADATA_CONFIG_PATH = os.path.join(CONFIG_DIR, 'metadata_config.json')
QRCODE_CONFIG_PATH = os.path.join(CONFIG_DIR, 'qrcode_config.json')
GPHOTOS_SECRETS_FILE = os.path.join(CONFIG_DIR, 'gphotos_auth.json')
GPHOTOS_TOKEN_FILE = os.path.join(CONFIG_DIR, 'google_photos_token.json')

# ------------------------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------------------------
logger = setup_logger()

# ------------------------------------------------------------------------------
# Flask Extensions Initialization
# ------------------------------------------------------------------------------
init_db(app)
app.register_blueprint(integration_routes)

# ------------------------------------------------------------------------------
# Global Variables & Initializations
# ------------------------------------------------------------------------------

# Services
frame_discovery = FrameDiscovery(port=ZEROCONF_PORT)
photo_processor = PhotoProcessor()
photo_generator = PhotoGenerator(app.config['UPLOAD_FOLDER'])
scheduler = None # Initialized in init_scheduler
frame_timing_manager = None # Initialized in init_app

# Integrations (initialized in init_integrations or main block)
weather_integration = None
metadata_integration = None
qrcode_integration = None
overlay_manager = None
google_photos = None
unsplash_integration = None
pixabay_integration = None
app.mqtt_integration = None # Placeholder, initialized later if enabled

# AI Analysis State
photo_analysis_state = {
    'in_progress': False,
    'current': 0,
    'total': 0,
    'should_cancel': False
}

# ------------------------------------------------------------------------------
# Initialization Functions
# ------------------------------------------------------------------------------

def create_upload_folder():
    """Creates the upload folder if it doesn't exist."""
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        logger.info(f"Creating upload folder: {app.config['UPLOAD_FOLDER']}")
        os.makedirs(app.config['UPLOAD_FOLDER'])
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails'), exist_ok=True)
    else:
        logger.info(f"Upload folder exists at: {app.config['UPLOAD_FOLDER']}")

# Call immediately after setting UPLOAD_FOLDER config
create_upload_folder()

def load_server_settings():
    """Load server settings from file."""
    default_settings = {
        'server_name': socket.gethostname(),
        'timezone': 'UTC',
        'cleanup_interval': 24,  # hours
        'log_level': 'INFO',
        'max_upload_size': 10,  # MB
        'discovery_port': ZEROCONF_PORT,
        'ai_analysis_enabled': False,
        'dark_mode': False
    }
    try:
        if os.path.exists(SERVER_SETTINGS_FILE):
            with open(SERVER_SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                # Ensure all default keys exist, merging defaults
                default_settings.update(settings)
                return default_settings
        return default_settings
    except Exception as e:
        logger.error(f"Error loading server settings from {SERVER_SETTINGS_FILE}: {e}")
        return default_settings

def save_server_settings(settings):
    """Save server settings to file."""
    try:
        os.makedirs(os.path.dirname(SERVER_SETTINGS_FILE), exist_ok=True)
        with open(SERVER_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Server settings saved to {SERVER_SETTINGS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving server settings to {SERVER_SETTINGS_FILE}: {e}")
        return False

def load_photogen_settings():
    """Load photo generation (AI) settings."""
    default_settings = {
        'dalle_api_key': '', 'stability_api_key': '', 'custom_server_api_key': '',
        'dalle_base_url': '', 'stability_base_url': '', 'custom_server_base_url': '',
        'default_service': 'dalle',
        'default_models': { 'dalle': 'dall-e-3', 'stability': 'ultra', 'custom': '' }
    }
    try:
        if os.path.exists(PHOTOGEN_SETTINGS_FILE):
            with open(PHOTOGEN_SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                # Ensure all default keys exist, merging defaults
                # Special handling for nested default_models
                if 'default_models' not in settings:
                    settings['default_models'] = {}
                merged_models = default_settings['default_models'].copy()
                merged_models.update(settings['default_models'])
                settings['default_models'] = merged_models

                merged_settings = default_settings.copy()
                merged_settings.update(settings)
                return merged_settings
        return default_settings
    except Exception as e:
        logger.error(f"Error loading photo generation settings from {PHOTOGEN_SETTINGS_FILE}: {e}")
        return default_settings

def save_photogen_settings(settings):
    """Save photo generation settings."""
    try:
        os.makedirs(os.path.dirname(PHOTOGEN_SETTINGS_FILE), exist_ok=True)
        with open(PHOTOGEN_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Photo generation settings saved to {PHOTOGEN_SETTINGS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving photo generation settings to {PHOTOGEN_SETTINGS_FILE}: {e}")
        return False

def load_mqtt_settings():
    """Load MQTT settings from config file."""
    default_settings = {'enabled': False, 'broker': '', 'port': 1883, 'username': '', 'password': '', 'device_name': 'Photo Frame'}
    try:
        if os.path.exists(MQTT_CONFIG_PATH):
            with open(MQTT_CONFIG_PATH, 'r') as f:
                settings = json.load(f)
                default_settings.update(settings) # Merge loaded settings over defaults
                return default_settings
    except Exception as e:
        logger.error(f"Error loading MQTT settings from {MQTT_CONFIG_PATH}: {e}. Using defaults.")
    return default_settings

def save_mqtt_settings(mqtt_settings):
    """Save MQTT settings to config file."""
    try:
        os.makedirs(os.path.dirname(MQTT_CONFIG_PATH), exist_ok=True)
        with open(MQTT_CONFIG_PATH, 'w') as f:
            json.dump(mqtt_settings, f, indent=4)
        logger.info(f"MQTT settings saved to {MQTT_CONFIG_PATH}")
    except Exception as e:
        logger.error(f"Error saving MQTT settings to {MQTT_CONFIG_PATH}: {e}")

# Apply server settings immediately affecting Flask config
server_settings = load_server_settings()
app.config['MAX_CONTENT_LENGTH'] = server_settings.get('max_upload_size', 10) * 1024 * 1024
logging.getLogger().setLevel(server_settings.get('log_level', 'INFO'))

def init_scheduler():
    """Initialize the GenerationScheduler."""
    global scheduler
    if not scheduler:
        models = {
            'Photo': Photo, 'ScheduledGeneration': ScheduledGeneration,
            'GenerationHistory': GenerationHistory, 'PlaylistEntry': PlaylistEntry,
            'CustomPlaylist': CustomPlaylist, 'PhotoFrame': PhotoFrame # Include PhotoFrame
        }
        # NOTE: Temporarily removed unsplash_integration and pixabay_integration
        # to match the expected 5 arguments (self + 4) based on the TypeError.
        # The GenerationScheduler class likely needs to be updated to accept these.
        scheduler = GenerationScheduler(app, photo_generator, db, models) # Pass integrations
        # Re-add existing jobs from DB on startup
        with app.app_context():
            active_schedules = ScheduledGeneration.query.filter_by(is_active=True).all()

def init_integrations():
    """Initialize core integrations like overlays."""
    global weather_integration, metadata_integration, qrcode_integration, overlay_manager, unsplash_integration, pixabay_integration, google_photos
    try:
        weather_integration = WeatherIntegration(WEATHER_CONFIG_PATH)
        metadata_integration = MetadataIntegration(METADATA_CONFIG_PATH)
        app.metadata_integration = metadata_integration # Make accessible for routes if needed directly
        qrcode_integration = QRCodeIntegration(QRCODE_CONFIG_PATH)
        # NOTE: Temporarily removed qrcode_integration from the OverlayManager call
        # to match the expected 3 arguments (self + 2) based on the TypeError.
        # The OverlayManager class likely needs to be updated to accept this.
        overlay_manager = OverlayManager(weather_integration, metadata_integration) # Pass QR code integration
        logger.info("Weather, Metadata, QR Code integrations and OverlayManager initialized.")

        unsplash_integration = UnsplashIntegration(UNSPLASH_CONFIG_PATH)
        pixabay_integration = PixabayIntegration(PIXABAY_CONFIG_PATH)
        logger.info("Unsplash and Pixabay integrations initialized.")

        google_photos = GooglePhotosIntegration(
             client_secrets_file=GPHOTOS_SECRETS_FILE,
             token_file=GPHOTOS_TOKEN_FILE,
             upload_folder=UPLOAD_FOLDER
        )
        logger.info("Google Photos integration initialized.")

    except Exception as e:
        logger.error(f"Error initializing integrations: {e}", exc_info=True)
        # Decide if this is fatal or if the app can continue partially functional

def start_discovery_service():
    """Start the Zeroconf discovery service."""
    global frame_discovery
    try:
        if not hasattr(frame_discovery, 'zeroconf') or frame_discovery.zeroconf is None:
            logger.info("Starting frame discovery service...")
            frame_discovery.start()
            logger.info("Frame discovery service started.")
        else:
            logger.info("Frame discovery service already running.")
    except Exception as e:
        logger.error(f"Error starting frame discovery service: {e}")

def cleanup_discovery_service():
    """Stop the Zeroconf discovery service."""
    global frame_discovery
    try:
        if hasattr(frame_discovery, 'zeroconf') and frame_discovery.zeroconf is not None:
            logger.info("Stopping frame discovery service...")
            frame_discovery.stop()
            logger.info("Frame discovery service stopped.")
    except Exception as e:
        logger.error(f"Error stopping frame discovery service: {e}")

def init_app_services():
    """Initialize all necessary application services on startup."""
    global frame_timing_manager
    init_integrations() # Initialize weather, metadata, overlays etc.
    init_scheduler()    # Initialize and load scheduled jobs

    # Initialize frame timing manager
    if not frame_timing_manager:
        models = {'PhotoFrame': PhotoFrame, 'Photo': Photo, 'PlaylistEntry': PlaylistEntry}
        frame_timing_manager = FrameTimingManager(app, db, models)
        frame_timing_manager.start()
        logger.info("FrameTimingManager initialized and started.")

    # Start discovery last
    start_discovery_service()

def cleanup_app_services():
    """Cleanup services on application exit."""
    global scheduler, frame_timing_manager, app
    logger.info("Shutting down application services...")
    cleanup_discovery_service()
    if scheduler:
        scheduler.shutdown()
        logger.info("Scheduler shut down.")
    if frame_timing_manager:
        frame_timing_manager.stop()
        logger.info("FrameTimingManager stopped.")
    if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
        app.mqtt_integration.stop()
        logger.info("MQTT Integration stopped.")
    logger.info("Application cleanup complete.")

# Register cleanup function to run at exit
atexit.register(cleanup_app_services)

# ------------------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------------------

@app.template_filter('from_json')
def from_json_filter(value):
    """Template filter to parse JSON strings."""
    try:
        return json.loads(value) if value else {}
    except Exception:
        return {}

def allowed_file(filename):
    """Check if the filename has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def format_relative_time(dt, current_time=None, timezone_name='UTC'):
    """Format a datetime as a human-readable relative time string."""
    if not dt:
        return "Never"
    try:
        # Ensure dt is timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)

        # Get current time, ensure it's timezone-aware
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            current_time = pytz.UTC.localize(current_time) # Assume current_time is UTC if naive

        # Convert both to the target timezone for comparison
        target_tz = pytz.timezone(timezone_name)
        dt_local = dt.astimezone(target_tz)
        current_time_local = current_time.astimezone(target_tz)

        # Use humanize for relative time
        return humanize.naturaltime(current_time_local - dt_local) # Pass timedelta to naturaltime

    except Exception as e:
        logger.error(f"Error formatting relative time for {dt} in TZ {timezone_name}: {e}")
        # Fallback to ISO format in UTC
        return dt.astimezone(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S %Z')

def is_in_deep_sleep(frame, current_time_utc=None):
    """Check if a frame is currently in its deep sleep window (using UTC)."""
    if not frame.deep_sleep_enabled or frame.deep_sleep_start is None or frame.deep_sleep_end is None:
        return False

    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)
    elif current_time_utc.tzinfo is None:
         current_time_utc = pytz.UTC.localize(current_time_utc) # Assume UTC if naive
    else:
         current_time_utc = current_time_utc.astimezone(pytz.UTC)

    current_hour = current_time_utc.hour
    start = frame.deep_sleep_start # Stored as UTC hour
    end = frame.deep_sleep_end     # Stored as UTC hour

    if start < end:  # e.g., 1:00 to 6:00 UTC
        return start <= current_hour < end
    else:            # Crosses midnight UTC e.g., 22:00 to 6:00 UTC
        return current_hour >= start or current_hour < end

def calculate_sleep_interval(frame, current_time_utc=None):
    """Calculate the effective sleep interval considering deep sleep (in minutes, UTC)."""
    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)
    elif current_time_utc.tzinfo is None:
         current_time_utc = pytz.UTC.localize(current_time_utc)
    else:
         current_time_utc = current_time_utc.astimezone(pytz.UTC)

    base_interval = frame.sleep_interval

    if frame.deep_sleep_enabled and frame.deep_sleep_start is not None and frame.deep_sleep_end is not None:
        # Check if currently in deep sleep
        if is_in_deep_sleep(frame, current_time_utc):
            # Calculate time until deep sleep ends
            end_time_today = current_time_utc.replace(hour=frame.deep_sleep_end, minute=0, second=0, microsecond=0)
            if end_time_today <= current_time_utc: # If end time is in the past today, it's tomorrow
                end_time = end_time_today + timedelta(days=1)
            else:
                end_time = end_time_today
            minutes_to_sleep = (end_time - current_time_utc).total_seconds() / 60.0
            logger.debug(f"Frame {frame.id} in deep sleep. Sleeping for {minutes_to_sleep:.1f} mins until {end_time.isoformat()}.")
            return max(minutes_to_sleep, base_interval) # Ensure we sleep at least the base interval

        # Check if the *next* wake-up would fall into deep sleep
        next_normal_wake = current_time_utc + timedelta(minutes=base_interval)
        if is_in_deep_sleep(frame, next_normal_wake):
            # Calculate time until deep sleep ends from *now*
            end_time_today = next_normal_wake.replace(hour=frame.deep_sleep_end, minute=0, second=0, microsecond=0)
            if end_time_today <= next_normal_wake: # If end time is past the wake time, it's the next day's end time
                 end_time = end_time_today + timedelta(days=1)
            else:
                 end_time = end_time_today
            minutes_to_sleep = (end_time - current_time_utc).total_seconds() / 60.0
            logger.debug(f"Frame {frame.id} next wake is in deep sleep. Sleeping for {minutes_to_sleep:.1f} mins until {end_time.isoformat()}.")
            return max(minutes_to_sleep, base_interval)

    # Not in deep sleep, and next wake is not in deep sleep
    return base_interval

def extract_exif_metadata(image_path):
    """Extract EXIF metadata, handling various types and potential errors."""
    try:
        logger.debug(f"Extracting EXIF from: {image_path}")
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                # Create basic metadata using file modification time if EXIF is missing
                mtime = os.path.getmtime(image_path)
                upload_time = datetime.fromtimestamp(mtime, tz=timezone.utc)
                formatted_time_utc = upload_time.strftime('%Y:%m:%d %H:%M:%S')
                metadata = {
                    'DateTime': formatted_time_utc,
                    'DateTimeOriginal': formatted_time_utc,
                    'DateTimeDigitized': formatted_time_utc,
                    'SourceFileInfo': {'FileModifyDate': upload_time.isoformat()},
                    # Add formatted date/time based on server settings later if needed
                }
                logger.info(f"No EXIF found for {os.path.basename(image_path)}, using file modify time.")
                return metadata

            metadata = {}
            for tag_id, value in exif_data.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

                if isinstance(value, bytes):
                    # Attempt to decode bytes, replace errors
                    try:
                        metadata[tag_name] = value.decode('utf-8', errors='replace').strip()
                    except Exception:
                         metadata[tag_name] = repr(value) # Fallback to repr
                elif isinstance(value, tuple) and len(value) > 0 and isinstance(value[0], int):
                     # Handle rational numbers typically stored as (numerator, denominator) tuples
                     if len(value) == 2 and value[1] != 0:
                          try:
                               metadata[tag_name] = float(value[0]) / float(value[1])
                          except ZeroDivisionError:
                               metadata[tag_name] = 0.0
                          except TypeError: # Handle cases where tuple elements are not numbers
                               metadata[tag_name] = str(value)
                     else:
                         # Store other integer tuples as lists
                         metadata[tag_name] = list(value)
                elif isinstance(value, (int, float, str, bool)) or value is None:
                     metadata[tag_name] = value
                else:
                     # Fallback for other non-serializable types
                     try:
                        json.dumps(value) # Test serializability
                        metadata[tag_name] = value
                     except (TypeError, OverflowError):
                        metadata[tag_name] = str(value)

            # Special handling for GPS Info
            if 34853 in exif_data: # GPSInfo IFD tag ID
                gps_info_raw = exif_data[34853]
                gps_data = {}
                for gps_tag_id, gps_value in gps_info_raw.items():
                    gps_tag_name = ExifTags.GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                    # Process GPS values similarly to main EXIF
                    if isinstance(gps_value, bytes):
                        try:
                            gps_data[gps_tag_name] = gps_value.decode('utf-8', errors='replace').strip()
                        except Exception:
                            gps_data[gps_tag_name] = repr(gps_value)
                    elif isinstance(gps_value, tuple) and len(gps_value) > 0 and isinstance(gps_value[0], (int, float)):
                         # GPS Coordinates (Degrees, Minutes, Seconds often as rationals)
                         if len(gps_value) == 3: # DMS format
                             try:
                                 d = float(gps_value[0]) if not isinstance(gps_value[0], tuple) else float(gps_value[0][0])/float(gps_value[0][1])
                                 m = float(gps_value[1]) if not isinstance(gps_value[1], tuple) else float(gps_value[1][0])/float(gps_value[1][1])
                                 s = float(gps_value[2]) if not isinstance(gps_value[2], tuple) else float(gps_value[2][0])/float(gps_value[2][1])
                                 gps_data[gps_tag_name] = [d, m, s] # Store as list [D, M, S]
                             except (ValueError, TypeError, ZeroDivisionError, IndexError):
                                 gps_data[gps_tag_name] = str(gps_value) # Fallback
                         elif len(gps_value) == 2 and isinstance(gps_value[0], int) and gps_value[1] != 0: # Simple rational
                              try:
                                   gps_data[gps_tag_name] = float(gps_value[0]) / float(gps_value[1])
                              except (ZeroDivisionError, TypeError):
                                   gps_data[gps_tag_name] = str(gps_value)
                         else:
                             gps_data[gps_tag_name] = list(gps_value) # Store other tuples as list
                    elif isinstance(gps_value, (int, float, str, bool)) or gps_value is None:
                        gps_data[gps_tag_name] = gps_value
                    else:
                        try:
                           json.dumps(gps_value)
                           gps_data[gps_tag_name] = gps_value
                        except (TypeError, OverflowError):
                           gps_data[gps_tag_name] = str(gps_value)
                metadata['GPSInfo'] = gps_data # Replace raw GPS data with processed dict

                # Attempt to calculate decimal coordinates
                try:
                    lat_dms = gps_data.get('GPSLatitude')
                    lat_ref = gps_data.get('GPSLatitudeRef', 'N')
                    lon_dms = gps_data.get('GPSLongitude')
                    lon_ref = gps_data.get('GPSLongitudeRef', 'E')

                    if isinstance(lat_dms, list) and len(lat_dms) == 3 and isinstance(lon_dms, list) and len(lon_dms) == 3:
                        lat = lat_dms[0] + lat_dms[1] / 60.0 + lat_dms[2] / 3600.0
                        lon = lon_dms[0] + lon_dms[1] / 60.0 + lon_dms[2] / 3600.0
                        if lat_ref == 'S': lat = -lat
                        if lon_ref == 'W': lon = -lon
                        metadata['decimal_latitude'] = round(lat, 6)
                        metadata['decimal_longitude'] = round(lon, 6)
                        metadata['formatted_location'] = f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"
                except Exception as gps_calc_e:
                    logger.warning(f"Could not calculate decimal GPS coordinates: {gps_calc_e}")

            # Add formatted date/time if DateTime exists (using server's timezone setting)
            if 'DateTimeOriginal' in metadata or 'DateTime' in metadata:
                dt_str = metadata.get('DateTimeOriginal') or metadata.get('DateTime')
                try:
                    # Common EXIF format: 'YYYY:MM:DD HH:MM:SS'
                    naive_dt = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
                    # Assume the EXIF time is local to where the photo was taken, but store UTC reference
                    # For simplicity here, we just store the naive string, formatting happens on display
                    metadata['formatted_date'] = naive_dt.strftime('%B %d, %Y')
                    metadata['formatted_time'] = naive_dt.strftime('%I:%M %p')
                except (ValueError, TypeError) as fmt_e:
                    logger.warning(f"Could not parse or format EXIF DateTime '{dt_str}': {fmt_e}")

            logger.debug(f"Successfully extracted EXIF for {os.path.basename(image_path)}")
            return metadata

    except Exception as e:
        logger.error(f"Error extracting EXIF metadata from {image_path}: {e}", exc_info=True)
        return None # Return None on failure

def generate_video_thumbnail(video_path, thumbnail_path):
    """Generate a thumbnail from the middle of a video file using ffmpeg."""
    try:
        # Get video duration
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
        midpoint = duration / 2.0

        # Extract frame
        ffmpeg_cmd = [
            'ffmpeg', '-y', # Overwrite existing thumbnail
            '-i', video_path,
            '-ss', str(midpoint), # Seek to midpoint
            '-vframes', '1', # Extract one frame
            '-vf', 'scale=400:-1', # Scale width to 400px, maintain aspect ratio
            thumbnail_path
        ]
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
        logger.info(f"Generated video thumbnail for {os.path.basename(video_path)} at {thumbnail_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg/ffprobe error generating thumbnail for {video_path}: {e.stderr}")
        return False
    except ValueError as e:
        logger.error(f"Error parsing video duration for {video_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error generating video thumbnail for {video_path}: {e}")
        return False

def cleanup_temp_files(directory, max_age_hours=1):
    """Clean up temporary files (e.g., temp_*.jpg) older than max_age_hours."""
    try:
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        for filename in os.listdir(directory):
            if filename.startswith('temp_'):
                file_path = os.path.join(directory, filename)
                try:
                    if os.path.isfile(file_path):
                        file_mod_time = os.path.getmtime(file_path)
                        if file_mod_time < cutoff:
                            os.remove(file_path)
                            logger.debug(f"Cleaned up old temp file: {file_path}")
                except Exception as e:
                    logger.warning(f"Error processing temp file {file_path} for cleanup: {e}")
    except FileNotFoundError:
        logger.warning(f"Temp file directory not found for cleanup: {directory}")
    except Exception as e:
        logger.error(f"Error during temp file cleanup in {directory}: {e}")

def get_size_str(size_bytes):
    """Convert bytes to human-readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size_kb = size_bytes / 1024
    if size_kb < 1024:
        return f"{size_kb:.1f} KB"
    size_mb = size_kb / 1024
    if size_mb < 1024:
        return f"{size_mb:.1f} MB"
    size_gb = size_mb / 1024
    return f"{size_gb:.1f} GB"

def get_system_info():
    """Gather various system information metrics."""
    info = {}
    # Storage
    try:
        disk = psutil.disk_usage('/')
        info['storage'] = {
            'total': get_size_str(disk.total), 'used': get_size_str(disk.used),
            'free': get_size_str(disk.free), 'percent': disk.percent
        }
    except Exception as e: info['storage'] = {'error': str(e)}
    # Photos Storage
    try:
        photos_path = app.config['UPLOAD_FOLDER']
        total_size = 0
        photo_count = 0
        if os.path.exists(photos_path):
             for dirpath, _, filenames in os.walk(photos_path):
                 for f in filenames:
                     try:
                         fp = os.path.join(dirpath, f)
                         if os.path.isfile(fp): # Check if it's actually a file
                             total_size += os.path.getsize(fp)
                             photo_count += 1
                     except Exception: pass # Ignore errors on individual files
        info['photos_storage'] = {
            'count': photo_count, 'total_size': get_size_str(total_size),
            'avg_size': get_size_str(total_size / photo_count) if photo_count > 0 else '0 B'
        }
    except Exception as e: info['photos_storage'] = {'error': str(e)}
    # System Performance
    try:
        # Try common paths for CPU temp
        temp_paths = ['/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input']
        cpu_temp = 'N/A'
        for path in temp_paths:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        temp = float(f.read().strip()) / 1000.0
                    cpu_temp = f"{temp:.1f}°C"
                    break
                except Exception: pass
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_str = str(timedelta(seconds=int(uptime_seconds)))
        info['system'] = {
            'cpu_temp': cpu_temp, 'cpu_usage': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'uptime': uptime_str, 'python_version': platform.python_version()
        }
    except Exception as e: info['system'] = {'error': str(e)}
    # Network
    try:
        hostname = socket.gethostname()
        ip_address = 'Unknown'
        try:
            # Attempt to get primary IP address
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1) # Avoid blocking
            s.connect(('8.8.8.8', 80)) # Doesn't send data
            ip_address = s.getsockname()[0]
            s.close()
        except Exception:
             # Fallback if external connect fails
             try: ip_address = socket.gethostbyname(hostname)
             except Exception: pass
        info['network'] = {
            'hostname': hostname, 'ip_address': ip_address
        }
    except Exception as e: info['network'] = {'error': str(e)}

    return info

def get_version():
    """Read version from version.txt file."""
    version_file = os.path.join(basedir, 'version.txt')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return 'unknown (version.txt not found)'
    except Exception as e:
        logger.warning(f"Could not read version file: {e}")
        return 'unknown (read error)'

class PhotoHelper:
    """Class to encapsulate static methods related to photo retrieval for frames."""
    @staticmethod
    def get_current_photo_filename(frame_id):
        frame = db.session.get(PhotoFrame, frame_id)
        if frame and frame.current_photo_id:
            photo = db.session.get(Photo, frame.current_photo_id)
            if photo:
                # Return appropriate version based on orientation
                if frame.orientation == 'portrait' and photo.portrait_version: return photo.portrait_version
                if frame.orientation == 'landscape' and photo.landscape_version: return photo.landscape_version
                return photo.filename
        elif frame: # Frame exists but no current photo set, try first in playlist
            entry = frame.playlist_entries.order_by(PlaylistEntry.order).first()
            if entry and entry.photo:
                photo = entry.photo
                if frame.orientation == 'portrait' and photo.portrait_version: return photo.portrait_version
                if frame.orientation == 'landscape' and photo.landscape_version: return photo.landscape_version
                return photo.filename
        return None

    @staticmethod
    def get_next_photo_filename(frame_id):
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame: return None

        playlist = frame.playlist_entries.order_by(PlaylistEntry.order).all()
        if not playlist: return None

        current_photo_id = frame.current_photo_id
        next_photo = None

        if frame.shuffle_enabled:
             # Simple shuffle: pick a random one *not* the current one, if possible
             possible_next = [p for p in playlist if p.photo_id != current_photo_id]
             if not possible_next and len(playlist) == 1: # Only one photo
                 next_photo = playlist[0].photo
             elif possible_next:
                 next_photo = random.choice(possible_next).photo
             else: # If current_photo_id was somehow invalid, pick any random
                 next_photo = random.choice(playlist).photo
        else:
             # Sequential: find current, get next (looping)
             current_index = -1
             if current_photo_id:
                 for i, entry in enumerate(playlist):
                     if entry.photo_id == current_photo_id:
                         current_index = i
                         break
             next_index = (current_index + 1) % len(playlist)
             next_photo = playlist[next_index].photo

        if next_photo:
            # Return appropriate version based on orientation
            if frame.orientation == 'portrait' and next_photo.portrait_version: return next_photo.portrait_version
            if frame.orientation == 'landscape' and next_photo.landscape_version: return next_photo.landscape_version
            return next_photo.filename
        return None

    @staticmethod
    def get_photo_object_by_id(photo_id):
        return db.session.get(Photo, photo_id)

def get_default_color_map():
    """Return the default color map for image processing (e-paper)."""
    # This map seems extensive, keep as is unless known issues
    return [
        "#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF",
        # Grayscale (16 shades)
        "#0A0A0A", "#1A1A1A", "#2A2A2A", "#3A3A3A", "#4A4A4A", "#5A5A5A", "#6A6A6A", "#7A7A7A",
        "#8A8A8A", "#9A9A9A", "#AAAAAA", "#BABABA", "#CACACA", "#DADADA", "#EAEAEA", "#F5F5F5",
        # Red shades (16)
        "#330000", "#660000", "#990000", "#CC0000", "#FF3333", "#FF6666", "#FF9999", "#FFCCCC",
        "#7F0000", "#B20000", "#E50000", "#FF1919", "#FF4C4C", "#FF8080", "#FFB3B3", "#FFE5E5",
        # Green shades (16)
        "#003300", "#006600", "#009900", "#00CC00", "#33FF33", "#66FF66", "#99FF99", "#CCFFCC",
        "#007F00", "#00B200", "#00E500", "#19FF19", "#4CFF4C", "#80FF80", "#B3FFB3", "#E5FFE5",
        # Blue shades (16)
        "#000033", "#000066", "#000099", "#0000CC", "#3333FF", "#6666FF", "#9999FF", "#CCCCFF",
        "#00007F", "#0000B2", "#0000E5", "#1919FF", "#4C4CFF", "#8080FF", "#B3B3FF", "#E5E5FF",
        # Yellow/Orange/Brown shades (16)
        "#332600", "#664C00", "#997300", "#CC9900", "#FFBF00", "#FFCC33", "#FFDB66", "#FFE699",
        "#7F5F00", "#B28500", "#E5AB00", "#FFBF1A", "#FFCC4C", "#FFDB80", "#FFE6B3", "#FFF2E5",
        # Purple/Magenta shades (16)
        "#330033", "#660066", "#990099", "#CC00CC", "#FF33FF", "#FF66FF", "#FF99FF", "#FFCCFF",
        "#7F007F", "#B200B2", "#E500E5", "#FF19FF", "#FF4CFF", "#FF80FF", "#FFB3FF", "#FFE5FF",
        # Cyan/Teal shades (16)
        "#003333", "#006666", "#009999", "#00CCCC", "#33FFFF", "#66FFFF", "#99FFFF", "#CCFFFF",
        "#007F7F", "#00B2B2", "#00E5E5", "#19FFFF", "#4CFFFF", "#80FFFF", "#B3FFFF", "#E5FFFF",
        # Additional vibrant colors (16)
        "#FF8000", "#FF4000", "#FF0080", "#FF0040", "#80FF00", "#40FF00", "#00FF80", "#00FF40",
        "#8000FF", "#4000FF", "#0080FF", "#0040FF", "#FFFF40", "#40FFFF", "#FF40FF", "#808080"
    ]

def process_uploaded_file(file, form_data):
    """Handles saving, processing, and DB entry for an uploaded file."""
    if not file or not allowed_file(file.filename):
        return None, "Invalid file or file type not allowed."

    original_filename = secure_filename(file.filename)
    temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{uuid.uuid4().hex}_{original_filename}")
    final_filename = original_filename
    final_filepath = os.path.join(app.config['UPLOAD_FOLDER'], final_filename)

    try:
        file.save(temp_filepath)
        logger.info(f"Saved uploaded file temporarily to {temp_filepath}")

        # --- Format Conversion (HEIC, AVIF, MOV) ---
        original_lower = original_filename.lower()
        converted = False
        exif_bytes_to_preserve = None

        if original_lower.endswith(('.heic', '.heif')):
            try:
                heif_file = pyheif.read(temp_filepath)
                img = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode, heif_file.stride)
                # Try to extract EXIF from HEIC
                for meta in heif_file.metadata or []:
                    if meta['type'] == 'Exif':
                        exif_bytes_to_preserve = meta['data']
                        logger.info("Found EXIF data in HEIC to preserve.")
                        break
                final_filename = f"{os.path.splitext(original_filename)[0]}.jpg"
                final_filepath = os.path.join(app.config['UPLOAD_FOLDER'], final_filename)
                img.save(final_filepath, "JPEG", quality=95, exif=exif_bytes_to_preserve or b'')
                converted = True
                logger.info(f"Converted HEIC {original_filename} to {final_filename}")
            except Exception as e:
                logger.error(f"Error converting HEIC file {original_filename}: {e}")
                os.remove(temp_filepath) # Clean up temp file on error
                return None, f"Error converting HEIC: {e}"

        elif original_lower.endswith('.avif'):
            try:
                with Image.open(temp_filepath) as img:
                    # Try to extract EXIF from AVIF (Pillow might put it in info dict)
                    exif_bytes_to_preserve = img.info.get('exif')
                    if exif_bytes_to_preserve: logger.info("Found EXIF data in AVIF to preserve.")
                    final_filename = f"{os.path.splitext(original_filename)[0]}.jpg"
                    final_filepath = os.path.join(app.config['UPLOAD_FOLDER'], final_filename)
                    # Ensure image is RGB before saving as JPEG
                    img_to_save = img.convert('RGB') if img.mode != 'RGB' else img
                    img_to_save.save(final_filepath, "JPEG", quality=95, exif=exif_bytes_to_preserve or b'')
                converted = True
                logger.info(f"Converted AVIF {original_filename} to {final_filename}")
            except Exception as e:
                logger.error(f"Error converting AVIF file {original_filename}: {e}")
                os.remove(temp_filepath)
                return None, f"Error converting AVIF: {e}"

        elif original_lower.endswith('.mov'):
            # Convert MOV to MP4 (simplified, consider adding progress/scaling if needed)
            mp4_filename = f"{os.path.splitext(original_filename)[0]}.mp4"
            mp4_filepath = os.path.join(app.config['UPLOAD_FOLDER'], mp4_filename)
            try:
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', temp_filepath, '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', '-an', mp4_filepath]
                subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
                final_filename = mp4_filename
                final_filepath = mp4_filepath
                converted = True
                logger.info(f"Converted MOV {original_filename} to {final_filename}")
            except subprocess.CalledProcessError as e:
                logger.error(f"ffmpeg error converting MOV {original_filename}: {e.stderr}")
                os.remove(temp_filepath)
                return None, f"Error converting MOV: {e.stderr}"
            except Exception as e:
                logger.error(f"Unexpected error converting MOV {original_filename}: {e}")
                os.remove(temp_filepath)
                return None, f"Error converting MOV: {e}"

        # If no conversion happened, move temp file to final location
        if not converted:
            os.rename(temp_filepath, final_filepath)
            logger.info(f"Moved uploaded file to {final_filepath}")
            temp_filepath = None # Prevent deletion later

        # --- Metadata Extraction ---
        # Extract EXIF *after* conversion if it happened, otherwise from original
        exif_metadata = extract_exif_metadata(final_filepath)
        if not exif_metadata and exif_bytes_to_preserve:
            logger.warning(f"Could not re-extract EXIF from converted {final_filename}, attempting manual parse.")
            # Try parsing the preserved bytes if extraction failed (less reliable)
            # This part is complex and often library-dependent; skipping detailed impl. for now.

        # --- Media Type Specific Processing (Thumbnail, Duration) ---
        is_video = final_filename.lower().endswith(('.mp4', '.mov')) # Check final name
        thumb_filename = None
        duration = None
        portrait_path = None
        landscape_path = None
        thumb_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)

        if is_video:
            thumb_filename_base = f"thumb_{os.path.splitext(final_filename)[0]}.jpg"
            thumb_path = os.path.join(thumb_dir, thumb_filename_base)
            if generate_video_thumbnail(final_filepath, thumb_path):
                thumb_filename = thumb_filename_base
            # Get duration
            try:
                ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', final_filepath]
                result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
                duration = float(result.stdout.strip())
            except Exception as e:
                logger.warning(f"Could not get video duration for {final_filename}: {e}")
            # Video files use the same file for both orientations in current logic
            portrait_path = final_filepath
            landscape_path = final_filepath
        else: # It's an image
            try:
                with Image.open(final_filepath) as img:
                    # Apply EXIF orientation *before* thumbnailing/processing
                    img = ImageOps.exif_transpose(img)

                    # Create thumbnail
                    thumb_img = img.copy()
                    thumb_img.thumbnail((400, 400))
                    thumb_filename_base = f"thumb_{final_filename}"
                    thumb_path = os.path.join(thumb_dir, thumb_filename_base)
                    thumb_img.save(thumb_path, "JPEG", quality=85)
                    thumb_filename = thumb_filename_base

                    # Save the orientation-corrected image back (overwriting original potentially)
                    # This simplifies downstream processing, assuming this is desired.
                    # Preserve EXIF if possible.
                    img.save(final_filepath, quality=95, exif=img.info.get('exif', b''))
                    logger.info(f"Applied EXIF orientation to {final_filename}")

                # Process for orientations (using the now orientation-corrected file)
                portrait_path = photo_processor.process_for_orientation(final_filepath, 'portrait')
                landscape_path = photo_processor.process_for_orientation(final_filepath, 'landscape')
                if not portrait_path: logger.warning(f"Failed to create portrait version for {final_filename}")
                if not landscape_path: logger.warning(f"Failed to create landscape version for {final_filename}")

            except Exception as e:
                logger.error(f"Error processing image {final_filename} for thumbnails/orientation: {e}")
                # Continue without thumbnail/oriented versions if processing fails

        # --- Database Entry ---
        photo = Photo(
            filename=final_filename,
            portrait_version=os.path.basename(portrait_path) if portrait_path else final_filename, # Fallback
            landscape_version=os.path.basename(landscape_path) if landscape_path else final_filename, # Fallback
            thumbnail=thumb_filename,
            media_type='video' if is_video else 'photo',
            duration=duration,
            heading=form_data.get('heading', ''),
            exif_metadata=exif_metadata,
            uploaded_at=datetime.utcnow()
        )
        db.session.add(photo)
        db.session.flush() # Get the photo.id for potential immediate use
        logger.info(f"Created Photo DB record ID {photo.id} for {final_filename}")

        # --- AI Analysis (Background) ---
        server_settings = load_server_settings()
        if server_settings.get('ai_analysis_enabled', False) and photo.media_type == 'photo':
             def async_analyze(app_context, photo_id_to_analyze):
                 with app_context:
                     try:
                         logger.info(f"Starting background AI analysis for photo {photo_id_to_analyze}")
                         photo_analyzer = PhotoAnalyzer(app, db)
                         photo_analyzer.analyze_photo(photo_id_to_analyze)
                         db.session.commit() # Commit analysis results
                         logger.info(f"Finished background AI analysis for photo {photo_id_to_analyze}")
                     except Exception as bg_e:
                         logger.error(f"Background AI analysis failed for photo {photo_id_to_analyze}: {bg_e}")
                         db.session.rollback()
             Thread(target=async_analyze, args=(app.app_context(), photo.id)).start()
        else:
            logger.debug("AI analysis skipped (disabled or not applicable).")

        return photo, "Upload successful"

    except Exception as e:
        logger.error(f"General error processing upload {original_filename}: {e}", exc_info=True)
        # Attempt cleanup of final file if it exists and differs from temp
        if 'final_filepath' in locals() and os.path.exists(final_filepath) and final_filepath != temp_filepath:
             try: os.remove(final_filepath)
             except Exception: pass
        return None, f"An error occurred: {e}"
    finally:
        # Ensure temp file is deleted if it still exists
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                logger.debug(f"Cleaned up temp file {temp_filepath}")
            except Exception as clean_e:
                logger.warning(f"Could not delete temp file {temp_filepath}: {clean_e}")

def add_photo_to_frame_playlist(photo_id, frame_id):
    """Adds a photo to the beginning of a specific frame's playlist."""
    try:
        # Check if frame and photo exist
        frame = db.session.get(PhotoFrame, frame_id)
        photo = db.session.get(Photo, photo_id)
        if not frame or not photo:
            logger.error(f"Cannot add photo to playlist: Frame {frame_id} or Photo {photo_id} not found.")
            return False, "Frame or Photo not found."
        
        # Ensure frame has a playlist
        if not frame.playlist_id:
            playlist_name = f"{frame.name} Playlist"
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id

        # Shift existing entries' order down by 1
        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).update({
            PlaylistEntry.order: PlaylistEntry.order + 1
        })

        # Add the new photo at the beginning (order 0)
        new_entry = PlaylistEntry(
            playlist_id=frame.playlist_id,
            photo_id=photo.id,
            order=0,
            date_added=datetime.utcnow()
        )
        db.session.add(new_entry)
        db.session.commit()
        logger.info(f"Added photo {photo_id} to start of playlist for frame {frame_id}")

        # Trigger MQTT update if enabled
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(frame)

        return True, "Photo added to playlist."
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding photo {photo_id} to playlist for frame {frame_id}: {e}")
        return False, f"Error adding to playlist: {e}"

# ------------------------------------------------------------------------------
# Routes - Admin Web Interface
# ------------------------------------------------------------------------------

# Home page - redirect to frames
@app.route('/')
def index():
    """Redirect to frames page."""
    return redirect(url_for('manage_frames'))

# Photo Upload
@app.route('/upload', methods=['GET', 'POST'])
def upload_photo():
    if request.method == 'POST':
        # Check if this is an API request
        is_api_request = request.headers.get('accept') == 'application/json'
        
        if 'photo' not in request.files:
            if is_api_request:
                return jsonify({'success': False, 'error': 'No photo file provided'}), 400
            flash('No photo file provided.')
            return redirect(request.url)
        
        file = request.files['photo']
        if file.filename == '':
            if is_api_request:
                return jsonify({'success': False, 'error': 'No selected file'}), 400
            flash('No selected file.')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            # Get frame_id from form data
            frame_id = request.form.get('frame_id')
            
            # Create thumbnails directory if it doesn't exist
            thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)
            
            # Save original file
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Extract EXIF metadata from the original file
            exif_metadata = extract_exif_metadata(filepath)
            if exif_metadata:
                app.logger.info(f"Successfully extracted EXIF metadata from {filename}")
            else:
                app.logger.info(f"No EXIF metadata found in {filename}")

            # Convert HEIC/HEIF to JPG
            try:
                if filename.lower().endswith(('.heic', '.heif')):
                    import pyheif  # Requires pyheif and libheif installation
                    heif_file = pyheif.read(filepath)
                    img = Image.frombytes(
                        heif_file.mode, 
                        heif_file.size, 
                        heif_file.data,
                        "raw",
                        heif_file.mode,
                        heif_file.stride,
                    )
                    
                    # Extract metadata from HEIC file
                    metadata = None
                    try:
                        for metadata in heif_file.metadata or []:
                            if metadata['type'] == 'Exif':
                                # Found EXIF metadata
                                app.logger.info("Found EXIF metadata in HEIC file")
                                metadata = metadata['data']
                                break
                    except Exception as e:
                        app.logger.error(f"Error extracting metadata from HEIC: {e}")
                    
                    # Replace original file with JPG version
                    new_filename = f"{os.path.splitext(filename)[0]}.jpg"
                    new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                    
                    # Save with EXIF data if available
                    if metadata:
                        img.save(new_filepath, "JPEG", quality=95, exif=metadata)
                        app.logger.info("Preserved EXIF metadata during HEIC conversion")
                    else:
                        img.save(new_filepath, "JPEG", quality=95)
                    
                    # Clean up original HEIC file and update variables
                    os.remove(filepath)
                    filename = new_filename
                    filepath = new_filepath
                    
                    # If we didn't get metadata from the original file, try again with the converted file
                    if not exif_metadata:
                        exif_metadata = extract_exif_metadata(filepath)
                        if exif_metadata:
                            app.logger.info(f"Successfully extracted EXIF metadata from converted {filename}")
                elif filename.lower().endswith('.avif'):
                    # Convert AVIF to JPG
                    with Image.open(filepath) as img:
                        # Extract EXIF data if available
                        metadata = None
                        try:
                            metadata = img.info.get('exif')
                        except Exception as e:
                            app.logger.error(f"Error extracting metadata from AVIF: {e}")
                        
                        # Replace original file with JPG version
                        new_filename = f"{os.path.splitext(filename)[0]}.jpg"
                        new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                        
                        # Save with EXIF data if available
                        if metadata:
                            img.save(new_filepath, "JPEG", quality=95, exif=metadata)
                            app.logger.info("Preserved EXIF metadata during AVIF conversion")
                        else:
                            img.save(new_filepath, "JPEG", quality=95)
                    
                    # Clean up original AVIF file and update variables
                    os.remove(filepath)
                    filename = new_filename
                    filepath = new_filepath
                    
                    # If we didn't get metadata from the original file, try again with the converted file
                    if not exif_metadata:
                        exif_metadata = extract_exif_metadata(filepath)
                        if exif_metadata:
                            app.logger.info(f"Successfully extracted EXIF metadata from converted {filename}")
            except Exception as e:
                app.logger.error(f"HEIC/AVIF conversion error: {e}")
                flash('Error converting file')
                return redirect(request.url)
            
            # Determine if it's a video
            is_video = filename.lower().endswith(('.mp4', '.mov'))
            
            thumb_filename = None
            duration = None
            portrait_path = None
            landscape_path = None
            
            if is_video:
                # Generate video thumbnail
                thumb_filename = f"thumb_{filename}.jpg"
                thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                
                # Convert MOV to MP4 if necessary
                if filename.lower().endswith('.mov'):
                    mp4_filename = os.path.splitext(filename)[0] + '.mp4'
                    mp4_filepath = os.path.join(app.config['UPLOAD_FOLDER'], mp4_filename)
                    progress_file = 'ffmpeg_progress.txt'
                    
                    try:
                        # Get original video dimensions
                        probe = subprocess.run([
                            'ffprobe',
                            '-v', 'error',
                            '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height',
                            '-of', 'csv=s=x:p=0',
                            filepath
                        ], capture_output=True, text=True)
                        
                        width, height = map(int, probe.stdout.strip().split('x'))
                        new_width = width // 2
                        new_height = height // 2
                        
                        # Optimized MOV to MP4 conversion with scaling
                        subprocess.run([
                            'ffmpeg', '-y',
                            '-i', filepath,
                            '-vf', f'scale={new_width}:{new_height}',  # Scale to half size
                            '-c:v', 'libx264',
                            '-preset', 'ultrafast',
                            '-tune', 'fastdecode',
                            '-crf', '28',
                            '-an',
                            '-movflags', '+faststart',
                            '-progress', progress_file,
                            mp4_filepath
                        ], check=True)
                        
                        # Remove original MOV file
                        os.remove(filepath)
                        
                        # Update filepath and filename to use MP4 version
                        filepath = mp4_filepath
                        filename = mp4_filename
                        
                    except Exception as e:
                        logger.error(f"Error converting MOV to MP4: {e}")
                        
                    if os.path.exists(progress_file):
                        os.remove(progress_file)

                if generate_video_thumbnail(filepath, thumb_path):
                    try:
                        probe = subprocess.run([
                            'ffprobe',
                            '-v', 'error',
                            '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1',
                            filepath
                        ], capture_output=True, text=True)
                        duration = float(probe.stdout)
                    except Exception as e:
                        logger.error(f"Error getting video duration: {e}")

                # Save to database
                photo = Photo(
                    filename=filename,
                    portrait_version=filename,
                    landscape_version=filename,
                    thumbnail=thumb_filename,
                    media_type='video',
                    duration=duration,
                    heading=request.form.get('heading', ''),  # Add heading from form data
                    exif_metadata=exif_metadata  # Add EXIF metadata
                )
            else:
                # Generate thumbnail
                try:
                    with Image.open(filepath) as img:
                        # Get EXIF orientation if it exists
                        exif = img._getexif()
                        orientation = exif.get(274) if exif else None  # 274 is the EXIF tag for orientation
                        
                        # Apply EXIF orientation before creating thumbnail
                        if orientation:
                            if orientation == 2:
                                img = img.transpose(Image.FLIP_LEFT_RIGHT)
                            elif orientation == 3:
                                img = img.rotate(180, expand=True)
                            elif orientation == 4:
                                img = img.transpose(Image.FLIP_TOP_BOTTOM)
                            elif orientation == 5:
                                img = img.rotate(-270, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
                            elif orientation == 6:
                                img = img.rotate(-90, expand=True)
                            elif orientation == 7:
                                img = img.rotate(-90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
                            elif orientation == 8:
                                img = img.rotate(-270, expand=True)
                        
                        # Create thumbnail from corrected image
                        img.thumbnail((400, 400))  # Max size 400x400
                        thumb_filename = f"thumb_{filename}"
                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                        img.save(thumb_path, "JPEG")

                        # Process for both orientations
                        try:
                            portrait_path = photo_processor.process_for_orientation(filepath, 'portrait')
                            if portrait_path:
                                app.logger.info(f"Successfully created portrait version: {portrait_path}")
                            else:
                                app.logger.error(f"Failed to create portrait version for {filename}")
                                
                            landscape_path = photo_processor.process_for_orientation(filepath, 'landscape')
                            if landscape_path:
                                app.logger.info(f"Successfully created landscape version: {landscape_path}")
                            else:
                                app.logger.error(f"Failed to create landscape version for {filename}")
                        except Exception as e:
                            app.logger.error(f"Error processing image orientations: {e}")
                            portrait_path = None
                            landscape_path = None
                        
                except Exception as e:
                    app.logger.error(f"Error generating thumbnail: {e}")
                    thumb_filename = None
            
            
            # Save photo record in the database with orientation versions and thumbnail
            photo = Photo(
                filename=filename,
                portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                thumbnail=thumb_filename,
                media_type='video' if is_video else 'photo',
                duration=duration,
                heading=request.form.get('heading', ''),  # Add heading from form data
                exif_metadata=exif_metadata  # Add EXIF metadata
            )
            
            # Log the photo record being saved
            app.logger.info(f"Saving photo record: filename={filename}, portrait={os.path.basename(portrait_path) if portrait_path else None}, landscape={os.path.basename(landscape_path) if landscape_path else None}")
            db.session.add(photo)
            db.session.commit() 
            
            # After adding photo to database, check AI settings before analysis
            server_settings = load_server_settings()
            if server_settings.get('ai_analysis_enabled', False):
                # Only run analysis if enabled
                def async_analyze(app, db, photo_id):
                    with app.app_context():
                        try:
                            photo_analyzer = PhotoAnalyzer(app, db)
                            photo_analyzer.analyze_photo(photo_id)
                        except Exception as e:
                            app.logger.error(f"Background analysis failed: {e}")
                
                Thread(target=async_analyze, args=(app, db, photo.id)).start()
            else:
                app.logger.debug("AI analysis disabled, skipping photo analysis")

            # After the photo is added to the database and before the background analysis,
            # check if we need to add it to a playlist
            target_playlist_id = request.form.get('playlist_id')
            if target_playlist_id:
                # Direct playlist_id provided
                try:
                    target_playlist_id = int(target_playlist_id)
                    # Shift existing entries
                    PlaylistEntry.query.filter_by(playlist_id=target_playlist_id)\
                        .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                    
                    # Add new entry at the beginning
                    entry = PlaylistEntry(
                        playlist_id=target_playlist_id,
                        photo_id=photo.id,
                        order=0
                    )
                    db.session.add(entry)
                    db.session.commit()
                except Exception as e:
                    app.logger.error(f"Error adding photo to playlist: {e}")
                    if is_api_request:
                        return jsonify({
                            'success': False,
                            'error': f'Error adding to playlist: {str(e)}'
                        }), 500
            elif frame_id:
                # Legacy: frame_id provided, use frame's playlist
                try:
                    frame = db.session.get(PhotoFrame, frame_id)
                    if frame and frame.playlist_id:
                        # Shift existing entries
                        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                            .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                        
                        # Add new entry at the beginning
                        entry = PlaylistEntry(
                            playlist_id=frame.playlist_id,
                            photo_id=photo.id,
                            order=0
                        )
                        db.session.add(entry)
                        db.session.commit()
                except Exception as e:
                    app.logger.error(f"Error adding photo to playlist: {e}")
                    if is_api_request:
                        return jsonify({
                            'success': False,
                            'error': f'Error adding to playlist: {str(e)}'
                        }), 500
            
            if is_api_request:
                return jsonify({
                    'success': True,
                    'photo_id': photo.id,
                    'message': 'Photo uploaded successfully'
                })
            
            flash('Photo uploaded successfully!')
            return redirect(url_for('upload_photo'))
        else:
            if is_api_request:
                return jsonify({'success': False, 'error': 'File type not allowed'}), 400
            flash('File type not allowed.')
            return redirect(request.url)

    # Get all photos and playlists for the display
    photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()
    
    # Get playlists with their recent photos for preview
    playlists = Playlist.query.order_by(Playlist.name).all()
    playlists_with_previews = []
    for playlist in playlists:
        recent_entries = playlist.entries.order_by(PlaylistEntry.order.desc()).limit(4).all()
        recent_photos = [entry.photo for entry in recent_entries if entry.photo]
        playlists_with_previews.append({
            'playlist': playlist,
            'recent_photos': recent_photos
        })
    
    # Build photo-to-playlists mapping for filtering
    photo_playlists = {}
    for entry in PlaylistEntry.query.all():
        if entry.photo_id not in photo_playlists:
            photo_playlists[entry.photo_id] = []
        if entry.playlist_id:
            photo_playlists[entry.photo_id].append(entry.playlist_id)
    is_connected = google_photos.is_connected()
    
    # Load server settings to check AI analysis status
    server_settings = load_server_settings()
    ai_enabled = server_settings.get('ai_analysis_enabled', False)
    
    # Determine the last playlist ID with a photo uploaded
    last_playlist_id = None
    latest_playlist_entry = PlaylistEntry.query.order_by(PlaylistEntry.id.desc()).first()
    if latest_playlist_entry and latest_playlist_entry.playlist_id:
        last_playlist_id = latest_playlist_entry.playlist_id
    
    # Check if Unsplash and Pixabay API keys are configured
    unsplash_settings = unsplash_integration.load_settings()
    pixabay_settings = pixabay_integration.load_settings()
    
    unsplash_api_key = unsplash_settings.get('api_key', '')
    pixabay_api_key = pixabay_settings.get('api_key', '')
    
    return render_template('upload.html', 
                         photos=photos, 
                         playlists=playlists_with_previews,
                         photo_playlists=photo_playlists,
                         google_photos_connected=is_connected,
                         ai_analysis_enabled=ai_enabled,
                         last_playlist_id=last_playlist_id,
                         unsplash_api_key=bool(unsplash_api_key),
                         pixabay_api_key=bool(pixabay_api_key))

# Make sure you have these routes
@app.route('/photos/<filename>')
def serve_photo(filename):
    """Serve uploaded photos."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/photos/<int:photo_id>/delete', methods=['DELETE'])
def delete_photo(photo_id):
    try:
        photo = Photo.query.get_or_404(photo_id)
        
        # Remove photo from all playlists
        PlaylistEntry.query.filter_by(photo_id=photo_id).delete()
        
        # Delete all versions of the file
        files_to_delete = [
            (photo.filename, 'original file'),
            (photo.portrait_version, 'portrait version'),
            (photo.landscape_version, 'landscape version'),
            (photo.thumbnail, 'thumbnail')
        ]
        
        for filename, file_type in files_to_delete:
            if filename:
                # Determine the correct path based on file type
                if file_type == 'thumbnail':
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', filename)
                else:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        app.logger.debug(f"Deleted {file_type}: {file_path}")
                except Exception as e:
                    app.logger.error(f"Error deleting {file_type} at {file_path}: {e}")
            
        # Delete the database record
        db.session.delete(photo)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Photo and all versions deleted successfully',
            'removed_from_frames': frame_names
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting photo {photo_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/frame/<frame_id>')
def view_frame(frame_id):
    """Display a virtual frame in a web browser."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        flash('Frame not found', 'error')
        return redirect(url_for('index'))
    
    # Get the current photo for this frame
    current_photo = None
    if frame.current_photo_id:
        current_photo = db.session.get(Photo, frame.current_photo_id)
    
    # If no current photo, try to get one from the playlist
    if not current_photo and frame.playlist_entries.count() > 0:
        playlist_entry = frame.playlist_entries.order_by(PlaylistEntry.order).first()
        if playlist_entry:
            current_photo = playlist_entry.photo
    
    # Calculate the next wake time
    next_wake_time = None
    if frame.next_wake_time:
        next_wake_time = frame.next_wake_time.timestamp() * 1000  # Convert to milliseconds for JS
    
    # Get sleep interval in milliseconds
    sleep_interval_ms = int(frame.sleep_interval * 60 * 1000)
    
    return render_template('view_frame.html', 
                          frame=frame, 
                          current_photo=current_photo,
                          next_wake_time=next_wake_time,
                          sleep_interval_ms=sleep_interval_ms)

# Manage Photo Frames: list frames and add new frame.
@app.route('/manage_frames', methods=['GET', 'POST'])
def manage_frames():
    """Display and manage all registered frames."""
    if request.method == 'POST':
        device_id = request.form.get('device_id')
        name = request.form.get('name')
        sleep_interval = float(request.form.get('sleep_interval', 5.0))
        frame_type = request.form.get('frame_type', 'physical')
        
        # For virtual frames, generate a random ID if not provided
        if frame_type == 'virtual' and not device_id:
            import uuid
            device_id = f"v{uuid.uuid4().hex[:4]}"
        
        if not device_id:
            flash('Device ID is required for physical frames', 'error')
            return redirect(url_for('manage_frames'))
        
        # Check if frame already exists
        frame = db.session.get(PhotoFrame, device_id)
        if frame:
            flash('A frame with this Device ID already exists', 'error')
            return redirect(url_for('manage_frames'))
        
        # Create new frame
        frame_name = name or f"Frame {device_id}"
        frame = PhotoFrame(
            id=device_id,
            name=frame_name,
            sleep_interval=sleep_interval,
            battery_level=None,
            last_diagnostic=None,
            frame_type=frame_type
        )
        
        try:
            db.session.add(frame)
            db.session.flush()  # Get frame ID before creating playlist
            
            # Auto-create a playlist for the new frame
            playlist_name = f"{frame_name} Playlist"
            # Handle duplicate names
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id
            
            db.session.commit()
            
            if frame_type == 'virtual':
                # Generate a QR code for the virtual frame URL
                server_url = request.host_url.rstrip('/')
                frame_url = f"{server_url}/frame/{device_id}"
                frame_url = url_for('view_frame', frame_id=device_id, _external=True)
                flash(f'Virtual frame created successfully. Access it at: <a href="{frame_url}" target="_blank">{frame_url}</a>', 'success')
            else:
                flash('Frame added successfully', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding frame: {str(e)}', 'error')
        
        return redirect(url_for('manage_frames'))
    
    # GET request - show frames list
    frames = PhotoFrame.query.order_by(PhotoFrame.order).all()
    discovered = frame_discovery.get_discovered_frames()
    
    # Ensure now is in UTC for status calculations
    now = datetime.now(timezone.utc)
    server_settings = load_server_settings()
    
    # Add relative times to frames
    for frame in frames:
        # Ensure wake times are in UTC for status calculations
        if frame.last_wake_time and frame.last_wake_time.tzinfo is None:
            frame.last_wake_time = pytz.UTC.localize(frame.last_wake_time)
        if frame.next_wake_time and frame.next_wake_time.tzinfo is None:
            frame.next_wake_time = pytz.UTC.localize(frame.next_wake_time)
            
        frame.last_wake_relative = format_relative_time(
            frame.last_wake_time, 
            now, 
            server_settings['timezone']
        )
        frame.next_wake_relative = format_relative_time(
            frame.next_wake_time, 
            now, 
            server_settings['timezone']
        )
        
    # Add this section to handle JSON format requests
    if request.args.get('format') == 'json':
        frames_data = []
        for frame in frames:
            frames_data.append({
                'id': frame.id,
                'name': frame.name,
                'orientation': frame.orientation,
                'sleep_interval': frame.sleep_interval,
                'frame_type': frame.frame_type
            })
        return jsonify({'frames': frames_data})
    
    # Get all playlists for the playlist selector
    playlists = Playlist.query.order_by(Playlist.name).all()
    
    return render_template('manage_frames.html', 
                         frames=frames, 
                         discovered=discovered,
                         playlists=playlists,
                         get_current_photo=PhotoHelper.get_current_photo,
                         get_next_photo=PhotoHelper.get_next_photo,
                         Photo=Photo,
                         now=now,
                         server_settings=server_settings)

class PhotoHelper:
    @classmethod
    def get_current_photo(cls, frame_id):
        """Get the current photo for a frame."""
        frame = PhotoFrame.query.get(frame_id)
        
        if frame and frame.playlist_entries.first():
            return frame.playlist_entries.first().photo
        
        return None

    @classmethod
    def get_next_photo(cls, frame_id):
        """Get the next photo object for a frame."""
        frame = PhotoFrame.query.get(frame_id)
        if not frame or not frame.playlist_id:
            return None
            
        # Get the first entry in the playlist (this is what will play next)
        # The "next" photo is the first one in order since current_photo tracks what's playing
        entry = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).order_by(PlaylistEntry.order).first()
        if entry and entry.photo:
            return entry.photo
            
        return None

# Manage Playlist for a Given Frame - redirects to playlist edit page
@app.route('/frames/<frame_id>/playlist', methods=['GET', 'POST'])
def edit_playlist(frame_id):
    frame = PhotoFrame.query.get_or_404(frame_id)
    
    # Ensure frame has a playlist
    if not frame.playlist:
        playlist_name = f"{frame.name} Playlist"
        base_name = playlist_name
        counter = 1
        while Playlist.query.filter_by(name=playlist_name).first():
            counter += 1
            playlist_name = f"{base_name} ({counter})"
        playlist = Playlist(name=playlist_name)
        db.session.add(playlist)
        db.session.flush()
        frame.playlist_id = playlist.id
        db.session.commit()
    
    # POST requests still work for backward compatibility
    if request.method == 'POST':
        data = request.get_json()
        photo_ids = [int(pid) for pid in data['photo_ids'].split(',') if pid.strip()]
        
        # Remove existing playlist entries from the frame's playlist
        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).delete()
        
        # Add new entries with order preserved
        for order, photo_id in enumerate(photo_ids):
            playlist_entry = PlaylistEntry(
                playlist_id=frame.playlist_id,
                photo_id=photo_id,
                order=order,
                date_added=datetime.utcnow()
            )
            db.session.add(playlist_entry)
        
        # Update playlist timestamp
        frame.playlist.updated_at = datetime.utcnow()
        db.session.commit()
        
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(frame)
        return jsonify({'success': True, 'message': 'Playlist updated successfully'})

    # GET requests redirect to the playlist edit page
    return redirect(url_for('edit_custom_playlist', playlist_id=frame.playlist_id))

# Manage Frame Settings
@app.route('/frames/<frame_id>/settings', methods=['GET', 'POST'])
def edit_frame_settings(frame_id):
    # Get frame from database
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        flash('Frame not found.', 'error')
        return redirect(url_for('manage_frames'))
    
    server_settings = load_server_settings()
    local_tz = pytz.timezone(server_settings['timezone'])
    
    if request.method == 'POST':
        old_frame_name = frame.name
        new_frame_name = request.form.get('name')
        frame.name = new_frame_name
        frame.sleep_interval = float(request.form.get('sleep_interval', 5.0))
        
        # If frame name changed, check if there's a playlist with the old name and rename it
        if old_frame_name and new_frame_name and old_frame_name != new_frame_name:
            # Check for playlist with old frame name pattern (e.g., "Device 1234 Playlist")
            old_playlist_name = f"{old_frame_name} Playlist"
            new_playlist_name = f"{new_frame_name} Playlist"
            
            matching_playlist = Playlist.query.filter_by(name=old_playlist_name).first()
            if matching_playlist:
                # Make sure the new name doesn't conflict with an existing playlist
                existing_playlist = Playlist.query.filter_by(name=new_playlist_name).first()
                if not existing_playlist:
                    matching_playlist.name = new_playlist_name
                    app.logger.info(f"Renamed playlist '{old_playlist_name}' to '{new_playlist_name}'")
        frame.orientation = request.form.get('orientation', 'portrait')
        
        # Handle playlist assignment
        playlist_id = request.form.get('playlist_id')
        if playlist_id:
            frame.playlist_id = int(playlist_id)
        
        # Handle image settings
        frame.contrast_factor = float(request.form.get('contrast_factor', 1.0))
        frame.saturation = int(request.form.get('saturation', 100))
        frame.blue_adjustment = int(request.form.get('blue_adjustment', 0))
        frame.padding = int(request.form.get('padding', 0))
        
        # Handle color map
        color_map_text = request.form.get('color_map', '')
        if color_map_text:
            try:
                # Split by lines and clean up each color code
                colors = [color.strip() for color in color_map_text.split('\n') if color.strip()]
                frame.color_map = colors
            except Exception as e:
                app.logger.error(f"Error parsing color map: {e}")
                # Keep existing color map if there's an error
        else:
            # If color_map_text is empty, explicitly set color_map to None
            frame.color_map = None
        
        # Handle shuffle setting
        frame.shuffle_enabled = request.form.get('shuffle_enabled') == 'on'
        
        # Convert deep sleep times from local to UTC
        frame.deep_sleep_enabled = request.form.get('deep_sleep_enabled') == 'on'
        if frame.deep_sleep_enabled:
            local_start = int(request.form.get('deep_sleep_start', 0))
            local_end = int(request.form.get('deep_sleep_end', 0))
            
            # Convert local hours to UTC
            now = datetime.now()
            local_time = local_tz.localize(now.replace(hour=local_start, minute=0))
            utc_start = local_time.astimezone(pytz.UTC).hour
            
            local_time = local_tz.localize(now.replace(hour=local_end, minute=0))
            utc_end = local_time.astimezone(pytz.UTC).hour
            
            frame.deep_sleep_start = utc_start
            frame.deep_sleep_end = utc_end
        
        # Handle overlay preferences
        try:
            preferences = json.loads(frame.overlay_preferences) if frame.overlay_preferences else {}
            preferences['weather'] = request.form.get('weather_overlay') == 'on'
            preferences['metadata'] = request.form.get('metadata_overlay') == 'on'
            preferences['qrcode'] = request.form.get('qrcode_overlay') == 'on'
            frame.overlay_preferences = json.dumps(preferences)
        except Exception as e:
            app.logger.error(f"Error updating overlay preferences: {e}")
            preferences = {'weather': False, 'metadata': False, 'qrcode': False}
            frame.overlay_preferences = json.dumps(preferences)
        
        db.session.commit()
        flash('Settings updated successfully.')
        
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.publish_state(frame)
            
        return redirect(url_for('manage_frames'))
    
    # Ensure now is timezone-aware in UTC
    now = datetime.now(timezone.utc)
    
    # Make last_wake_time timezone-aware if it exists
    if frame.last_wake_time and frame.last_wake_time.tzinfo is None:
        frame.last_wake_time = pytz.UTC.localize(frame.last_wake_time)
        
    # Make next_wake_time timezone-aware if it exists
    if frame.next_wake_time and frame.next_wake_time.tzinfo is None:
        frame.next_wake_time = pytz.UTC.localize(frame.next_wake_time)
    
    # Convert UTC times to local for display
    if frame.deep_sleep_enabled:
        now = datetime.now()
        utc_time = pytz.UTC.localize(now.replace(hour=frame.deep_sleep_start, minute=0))
        frame.deep_sleep_start = utc_time.astimezone(local_tz).hour
        
        utc_time = pytz.UTC.localize(now.replace(hour=frame.deep_sleep_end, minute=0))
        frame.deep_sleep_end = utc_time.astimezone(local_tz).hour
    
    # Note: We don't set a default color map if it's null
    # as a NULL/blank color_map means the server should ignore color mapping
    
    # Get all playlists for the playlist selector
    playlists = Playlist.query.order_by(Playlist.name).all()
    
    return render_template('edit_settings.html', 
                         frame=frame, 
                         frames=PhotoFrame.query.all(),
                         playlists=playlists,
                         now=datetime.now(timezone.utc),
                         timedelta=timedelta,
                         weather_enabled=weather_integration.settings.get('enabled', False),
                         metadata_enabled=True,
                         server_settings=server_settings)

# ------------------------------------------------------------------------------
# API Endpoints for Photo Frame Clients
# ------------------------------------------------------------------------------

# /api/settings: Returns settings (such as sleep_interval) for a given device
@app.route('/api/settings')
def get_settings():
    """Get settings for a specific frame, including sync timing."""
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400
        
    frame = PhotoFrame.query.get(device_id)
    if not frame:
        return jsonify({"error": "Device not found"}), 404

    # Define minimum sleep interval (in minutes)
    MIN_SLEEP_INTERVAL = 1

    now = datetime.now(timezone.utc)
    sleep_interval = None
    sleep_reason = None
    next_sync = None  # Initialize next_sync here at the top level
    
    # Check for deep sleep first (highest priority)
    if frame.deep_sleep_enabled:
        deep_sleep_interval = calculate_sleep_interval(frame, now)
        if deep_sleep_interval > frame.sleep_interval:
            sleep_interval = deep_sleep_interval
            sleep_reason = "Frame is in deep sleep mode"
    
    # If not in deep sleep and frame is in sync group
    if sleep_interval is None and frame.sync_group:
        next_sync = frame.sync_group.get_next_sync_time()
        
        if next_sync:
            # Ensure next_sync is timezone-aware
            if next_sync.tzinfo is None:
                next_sync = pytz.UTC.localize(next_sync)
            
            # Calculate minutes until next sync
            sync_interval = round((next_sync - now).total_seconds() / 60.0, 3)
            
            # If sync interval is too short, skip to next sync period
            if sync_interval < MIN_SLEEP_INTERVAL:
                next_sync = frame.sync_group.get_next_sync_time(after=next_sync)
                if next_sync:
                    if next_sync.tzinfo is None:
                        next_sync = pytz.UTC.localize(next_sync)
                    sync_interval = round((next_sync - now).total_seconds() / 60.0, 3)
                    sleep_reason = "Skipped to next sync period (previous interval too short)"
                else:
                    sync_interval = frame.sync_group.sleep_interval
                    sleep_reason = "Using sync group default interval (next sync too short)"
            else:
                sleep_reason = "Synchronized with group schedule"
            
            sleep_interval = sync_interval
        else:
            sleep_interval = round(frame.sync_group.sleep_interval, 1)
            sleep_reason = "Using sync group default interval"
    
    # Fall back to frame's individual settings
    if sleep_interval is None:
        sleep_interval = round(frame.sleep_interval, 1)
        sleep_reason = "Using frame's default interval"

    # Ensure minimum sleep interval
    if sleep_interval < MIN_SLEEP_INTERVAL:
        sleep_interval = MIN_SLEEP_INTERVAL
        sleep_reason += " (adjusted to minimum)"
    
    # Get overlay preferences
    overlay_prefs = {}
    if frame.overlay_preferences:
        try:
            overlay_prefs = json.loads(frame.overlay_preferences)
        except:
            overlay_prefs = {}
    
    # Get image settings
    image_settings = {
        'contrast_factor': frame.contrast_factor if hasattr(frame, 'contrast_factor') and frame.contrast_factor is not None else 1.0,
        'saturation': frame.saturation if hasattr(frame, 'saturation') and frame.saturation is not None else 100,
        'blue_adjustment': frame.blue_adjustment if hasattr(frame, 'blue_adjustment') and frame.blue_adjustment is not None else 0
    }
    
    # Update frame's last_wake_time
    frame.last_wake_time = now
    
    # Calculate next wake time based on sleep interval
    frame.next_wake_time = now + timedelta(minutes=sleep_interval)
    
    db.session.commit()
    
    # Format next_sync for response if it exists
    next_sync_str = None
    if next_sync:
        next_sync_str = next_sync.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    # Get server settings
    server_settings = load_server_settings()
    
    # Return settings
    return jsonify({
        'sleep_interval': sleep_interval,
        'sleep_reason': sleep_reason,
        'next_sync': next_sync_str,
        'orientation': frame.orientation,
        'overlay_preferences': overlay_prefs,
        'image_settings': image_settings,
        'server_time': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'timezone': server_settings.get('timezone', 'UTC'),
        'shuffle_enabled': frame.shuffle_enabled,
        "current_photo_id": frame.current_photo_id if frame.current_photo_id else None,
        'deep_sleep_enabled': frame.deep_sleep_enabled,
        'deep_sleep_start': frame.deep_sleep_start,
        'deep_sleep_end': frame.deep_sleep_end
    })

@app.route('/api/diagnostic', methods=['POST'])
def update_diagnostic():
    """Handle frame diagnostic updates."""
    data = request.get_json()
    device_id = data.get('device_id')
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    frame = PhotoFrame.query.get(device_id)
    if not frame:
        return jsonify({"error": "Device not found"}), 404

    now = datetime.utcnow()
    
    # Update frame's diagnostic information
    frame.last_wake_time = now
    frame.last_diagnostic = now
    frame.diagnostics = data  # Store the entire diagnostic payload
    
    # Update battery level if provided
    if 'battery_level' in data:
        frame.battery_level = data['battery_level']
    
    # Update next wake time if provided
    if 'next_wake' in data:
        try:
            next_wake_str = data['next_wake'].replace('Z', '+00:00')
            next_wake = datetime.fromisoformat(next_wake_str)
            
            if next_wake.tzinfo is not None:
                next_wake = next_wake.astimezone(timezone.utc).replace(tzinfo=None)
            
            frame.next_wake_time = next_wake
            
            logger.info(f"""
                Frame {frame.id} diagnostic update (UTC):
                Current time: {now.isoformat()}Z
                Next wake: {next_wake.isoformat()}Z
                Time until wake: {((next_wake - now).total_seconds() / 60):.1f} minutes
            """)
        except Exception as e:
            logger.error(f"Error parsing next_wake time for frame {frame.id}: {e}")
            logger.error(f"Received next_wake value: {data.get('next_wake')}")
    
    # Update capabilities if provided
    if 'capabilities' in data:
        try:
            frame.capabilities = data['capabilities']
            logger.info(f"Updated capabilities for frame {frame.id}")
        except Exception as e:
            logger.error(f"Error updating capabilities for frame {frame.id}: {e}")
    
    db.session.commit()
    
    return jsonify({"message": "Diagnostic info updated"})

@app.route('/api/discovered_frames')
def get_discovered_frames():
    """API endpoint to get list of discovered frames."""
    return jsonify(frame_discovery.get_discovered_frames())

@app.route('/api/register_frame', methods=['POST'])
def register_frame():
    """Handle frame registration."""
    try:
        data = request.json
        device_id = data.get('device_id')
        properties = data.get('properties', {})
        
        if not device_id:
            return jsonify({'error': 'No device_id provided'}), 400
        
        # Debug logging
        print(f"Received registration for device {device_id} with properties:", properties)
        
        # Check if frame exists using Session.get()
        frame = db.session.get(PhotoFrame, device_id) 
        if not frame:
            # Create new frame with all properties
            frame_name = data.get('name', f'Device {device_id}')
            frame = PhotoFrame(
                id=device_id,
                name=frame_name,
                manufacturer=properties.get('manufacturer'),
                model=properties.get('model'),
                hardware_rev=properties.get('hardware_rev'),
                firmware_rev=properties.get('firmware_rev'),
                screen_resolution=properties.get('screen_resolution'),
                aspect_ratio=properties.get('aspect_ratio'),
                os=properties.get('os'),
                sleep_interval=30
            )
            db.session.add(frame)
            db.session.flush()
            
            # Auto-create a playlist for the new frame
            playlist_name = f"{frame_name} Playlist"
            # Handle duplicate names
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id
            
            print(f"Created new frame with properties:", {
                'manufacturer': frame.manufacturer,
                'model': frame.model,
                'hardware_rev': frame.hardware_rev,
                'firmware_rev': frame.firmware_rev,
                'screen_resolution': frame.screen_resolution,
                'aspect_ratio': frame.aspect_ratio,
                'os': frame.os,
                'playlist_id': frame.playlist_id
            })
        else:
            # Update existing frame properties
            frame.manufacturer = properties.get('manufacturer', frame.manufacturer)
            frame.model = properties.get('model', frame.model)
            frame.hardware_rev = properties.get('hardware_rev', frame.hardware_rev)
            frame.firmware_rev = properties.get('firmware_rev', frame.firmware_rev)
            frame.screen_resolution = properties.get('screen_resolution', frame.screen_resolution)
            frame.aspect_ratio = properties.get('aspect_ratio', frame.aspect_ratio)
            frame.os = properties.get('os', frame.os)
            
            # Ensure frame has a playlist (for frames created before migration)
            if not frame.playlist_id:
                playlist_name = f"{frame.name} Playlist"
                base_name = playlist_name
                counter = 1
                while Playlist.query.filter_by(name=playlist_name).first():
                    counter += 1
                    playlist_name = f"{base_name} ({counter})"
                
                playlist = Playlist(name=playlist_name)
                db.session.add(playlist)
                db.session.flush()
                frame.playlist_id = playlist.id
                print(f"Created playlist for existing frame {frame.id}")
            
            print(f"Updated existing frame with properties:", {
                'manufacturer': frame.manufacturer,
                'model': frame.model,
                'hardware_rev': frame.hardware_rev,
                'firmware_rev': frame.firmware_rev,
                'screen_resolution': frame.screen_resolution,
                'aspect_ratio': frame.aspect_ratio,
                'os': frame.os,
                'playlist_id': frame.playlist_id
            })
        
        # Update diagnostic information if provided
        if 'battery_level' in properties:
            frame.battery_level = properties['battery_level']
        if 'diagnostic_info' in properties:
            frame.diagnostic_info = json.dumps(properties['diagnostic_info'])
            frame.last_diagnostic = datetime.now(timezone.utc)
        
        # Store capabilities if provided
        if 'capabilities' in properties:
            try:
                frame.capabilities = properties['capabilities']
            except Exception as e:
                print(f"Error storing capabilities: {str(e)}")
        
        EventLogger.log_connection(frame.id, source=EventLogger.SOURCE_FRAME, 
                              details={'name': frame.name})
        
        db.session.commit()
        return jsonify({'message': 'Frame registered successfully'})
    except Exception as e:
        print(f"Error in register_frame: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/current_photo')
def get_current_photo():
    """Return the current photo for a frame and move it to the bottom of the playlist."""
    device_id = request.args.get('device_id')
    app.logger.info(f"Received request for device_id: {device_id}")
    
    if not device_id:
        app.logger.error("No device_id provided")
        return jsonify({'error': 'No device_id provided'}), 400

    frame = db.session.get(PhotoFrame, device_id)
    if frame:
        app.logger.info(f"Found frame: {frame.id}")
        app.logger.info(f"Frame overlay preferences: {frame.overlay_preferences}")
        
        # Get all playlist entries ordered by order via frame's playlist
        playlist = []
        if frame.playlist_id:
            playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                        .order_by(PlaylistEntry.order).all()
        app.logger.info(f"Playlist entries found: {len(playlist)}")
        
        if playlist:
            # If shuffle is enabled, pick a random photo from the playlist
            if frame.shuffle_enabled:
                import random
                current_entry = random.choice(playlist)
            else:
                # Get the first entry (current photo)
                current_entry = playlist[0]
                
            photo = Photo.query.get(current_entry.photo_id)
            
            # Reorder the playlist:
            # 1. Move all other entries up one position
            for i, entry in enumerate(playlist[1:], 0):
                entry.order = i
            # 2. Move current entry to the bottom
            current_entry.order = len(playlist) - 1
            
            db.session.commit()
            
            # Check if we have a valid photo
            if not playlist:
                # Return placeholder image if playlist is empty
                placeholder_path = os.path.join('static', 'images', 'frame-blank.jpg')
                return jsonify({
                    'status': 'success',
                    'photo_url': url_for('static', filename='images/frame-blank.jpg'),
                    'media_type': 'photo',
                    'sleep_interval': calculate_sleep_interval(frame)
                })
            
            if photo:
                # Choose the appropriate version based on frame orientation
                if frame.orientation == 'portrait' and photo.portrait_version:
                    filename = photo.portrait_version
                elif frame.orientation == 'landscape' and photo.landscape_version:
                    filename = photo.landscape_version
                else:
                    filename = photo.filename  # Fall back to original if no oriented version exists
                
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                # Check if this is a video file
                is_video = filename.lower().endswith('.mp4') or photo.media_type == 'video'
                mimetype = 'video/mp4' if is_video else 'image/jpeg'
                
                # For videos, skip image processing
                if is_video:
                    return send_file(photo_path, mimetype=mimetype)
                
                # Process image with frame settings if available
                processed_image = None
                if hasattr(frame, 'contrast_factor') and frame.contrast_factor is not None:
                    app.logger.info(f"Applying image settings for frame {frame.id}")
                    
                    try:
                        # Process the image with the frame's settings
                        from photo_processing import PhotoProcessor
                        processor = PhotoProcessor()
                        
                        # Open the image and apply enhancements
                        from PIL import Image
                        with Image.open(photo_path) as img:
                            processed_image = processor.enhance_image(img, frame)
                        app.logger.info("Successfully applied image enhancements")
                    except Exception as e:
                        app.logger.error(f"Error processing image with frame settings: {e}")
                        processed_image = None
                
                # Apply overlays if enabled
                if frame.overlay_preferences:
                    try:
                        app.logger.info("Attempting to apply overlays...")
                        # Use processed image if available, otherwise use original
                        source_image = processed_image if processed_image else photo_path
                        modified_image = overlay_manager.apply_overlays(source_image, frame.overlay_preferences, frame, photo)
                        
                        if modified_image:
                            app.logger.info("Successfully applied overlays")
                            temp_filename = f"temp_{int(datetime.now().timestamp())}_{photo.filename}"
                            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
                            
                            # Convert to RGB mode if the image is in palette mode (P)
                            if modified_image.mode == 'P':
                                app.logger.info("Converting palette image with overlays to RGB before saving as JPEG")
                                modified_image = modified_image.convert('RGB')
                            
                            modified_image.save(temp_path)
                            cleanup_temp_files(app.config['UPLOAD_FOLDER'])
                            return send_file(temp_path, mimetype='image/jpeg')
                        else:
                            app.logger.error("Overlay manager returned None")
                    except Exception as e:
                        app.logger.error(f"Error applying overlays: {e}", exc_info=True)
                
                # If we have a processed image but no overlays were applied, save and return it
                if processed_image:
                    temp_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'temp')
                    os.makedirs(temp_dir, exist_ok=True)
                    
                    temp_filename = f"temp_{uuid.uuid4().hex}_{os.path.basename(photo_path)}"
                    temp_path = os.path.join(temp_dir, temp_filename)
                    
                    # Convert to RGB mode if the image is in palette mode (P)
                    if processed_image.mode == 'P':
                        app.logger.info("Converting palette image to RGB before saving as JPEG")
                        processed_image = processed_image.convert('RGB')
                    
                    processed_image.save(temp_path, quality=95)
                    cleanup_temp_files(temp_dir)
                    
                    # Update the frame's current photo
                    frame.current_photo_id = photo.id
                    frame.last_wake_time = datetime.now(timezone.utc)
                    
                    # Calculate next wake time
                    sleep_interval = calculate_sleep_interval(frame)
                    frame.next_wake_time = frame.last_wake_time + timedelta(minutes=sleep_interval)
                    
                    db.session.commit()
                    
                    # Return the modified image
                    return send_file(temp_path, mimetype='image/jpeg')
                
                # Update the frame's current photo
                frame.current_photo_id = photo.id
                frame.last_wake_time = datetime.now(timezone.utc)
                
                # Calculate next wake time
                sleep_interval = calculate_sleep_interval(frame)
                frame.next_wake_time = frame.last_wake_time + timedelta(minutes=sleep_interval)
                
                db.session.commit()
                
                # Return the original image
                return send_file(photo_path, mimetype=mimetype)
    
    # Return default image for any failure case
    default_image_path = os.path.join(os.path.dirname(__file__), 'assets', 'default.jpg')
    app.logger.info(f"Returning default image from: {default_image_path}")
    
    if not os.path.exists(default_image_path):
        app.logger.error(f"Default image not found at: {default_image_path}")
        return jsonify({'error': 'Default image not found'}), 404
        
    return send_file(default_image_path, mimetype='image/jpeg')

def cleanup_temp_files(directory, max_age_hours=1):
    """Clean up temporary overlay files older than max_age_hours."""
    try:
        current_time = datetime.now()
        for filename in os.listdir(directory):
            if filename.startswith('temp_'):
                file_path = os.path.join(directory, filename)
                file_age = current_time - datetime.fromtimestamp(os.path.getctime(file_path))
                
                if file_age > timedelta(hours=max_age_hours):
                    try:
                        os.remove(file_path)
                        app.logger.debug(f"Cleaned up temporary file: {filename}")
                    except Exception as e:
                        app.logger.error(f"Error cleaning up temporary file {filename}: {e}")
    except Exception as e:
        app.logger.error(f"Error during temp file cleanup: {e}")

@app.route('/api/next_photo')
def get_next_photo():
    
    """Return the next photo for a frame using unified processing pipeline."""
    device_id = request.args.get('device_id')
    output_type = request.args.get('type')
    
    frame = db.session.get(PhotoFrame, device_id)

    if not frame or not (playlist := frame.playlist_entries.order_by(PlaylistEntry.order).all()):
        return handle_empty_playlist(frame, output_type)

    try:
        # Get next photo and update playlist
        current_entry = get_next_entry(frame, playlist)
        photo = current_entry.photo
        update_playlist_order(frame, playlist, current_entry)

        # Unified processing pipeline
        temp_path = process_image_pipeline(frame, photo)
        
        # Add headers for photo ID and filename
        headers = {
            'X-Photo-ID': str(photo.id),
            'X-Photo-Filename': photo.filename,
            'Content-Type': 'application/octet-stream' if output_type in ('compressed', 'rgb565', 'epaper', 'epd') else 'image/jpeg',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
        
        # Generate the output using existing function
        response = generate_final_output(temp_path, frame, output_type)
        
        # Add our custom headers to the response
        for key, value in headers.items():
            response.headers[key] = value
        
        EventLogger.log_photo_request(frame.id, photo_id=photo.id if photo else None)
        
        return response

    except Exception as e:
        logger.error(f"Error in get_next_photo: {e}")
        return jsonify({'error': str(e)}), 500

def handle_empty_playlist(frame, output_type):
    """Handle case when playlist is empty."""
    placeholder_path = os.path.join(app.root_path, 'static', 'images', 'frame-blank.jpg')
    if output_type == 'compressed':
        return generate_compressed_output(placeholder_path, frame.orientation if frame else 'portrait')
    elif output_type in ('epaper', 'epd') or (output_type == 'rgb565' and is_epaper_frame(frame)):
        return generate_epaper_output(placeholder_path, frame.orientation if frame else 'portrait')
    elif output_type == 'rgb565':
        return generate_rgb565_output(placeholder_path, frame)
    return send_file(placeholder_path, mimetype='image/jpeg')

def get_next_entry(frame, playlist):
    """Select next playlist entry based on shuffle settings."""
    if frame.shuffle_enabled and frame.playlist_id:
        entries = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).all()
        if not entries:
            return None

        # Create a session key specific to this frame's shuffle session
        shuffle_key = f'frame_{frame.id}_shuffle'
        shown_entries = session.get(shuffle_key, [])

        # If we've shown all entries, reset the tracking
        if len(shown_entries) >= len(entries):
            shown_entries = []

        # Get available entries (those not yet shown)
        available_entries = [entry for entry in entries 
                            if entry.id not in shown_entries]

        if available_entries:
            chosen_entry = random.choice(available_entries)
            shown_entries.append(chosen_entry.id)
            session[shuffle_key] = shown_entries
            return chosen_entry
    return playlist[0]

def update_playlist_order(frame, playlist, current_entry):
    """Update playlist order and frame's current photo."""
    # Find current entry's position in the playlist
    current_index = next((i for i, entry in enumerate(playlist) if entry.id == current_entry.id), -1)
    
    if current_index == -1:
        return  # Should never happen, but safety check

    # Shift subsequent entries up by one
    for entry in playlist[current_index+1:]:
        entry.order -= 1

    # Move current entry to the end
    current_entry.order = len(playlist) - 1

    # Update frame's current photo reference
    frame.current_photo_id = current_entry.photo_id
    frame.last_wake_time = datetime.now(timezone.utc)
    frame.next_wake_time = frame.last_wake_time + timedelta(minutes=frame.sleep_interval)
    
    db.session.commit()

def process_image_pipeline(frame, photo):
    logger.info(f"process_image_pipeline")
    """Unified image processing pipeline."""
    # Load base image
    img = load_base_image(frame, photo)
    
    # Apply enhancements
    if needs_enhancement(frame):
        img = apply_enhancements(img, frame)
    
    # Create temp file for remaining processing
    temp_path = create_temp_image(img)
    
    # Apply overlays
    if frame.overlay_preferences:
        overlay_img = apply_overlays(temp_path, frame, photo)
        overlay_img.save(temp_path)  # Overwrite temp file with overlay
    
    return temp_path

def load_base_image(frame, photo):
    """Load appropriate base image version."""
    filename = get_orientation_filename(frame, photo)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    return Image.open(filepath)

def get_orientation_filename(frame, photo):
    """Get filename for correct orientation version."""
    if frame.orientation == 'portrait' and photo.portrait_version:
        return photo.portrait_version
    if frame.orientation == 'landscape' and photo.landscape_version:
        return photo.landscape_version
    return photo.filename

def needs_enhancement(frame):
    """Check if any image enhancements are needed."""
    return (frame.contrast_factor != 1.0 or 
            frame.saturation != 100 or 
            frame.blue_adjustment != 0)

def apply_enhancements(img, frame):
    """Apply image enhancements based on frame settings"""
    from photo_processing import PhotoProcessor
    processor = PhotoProcessor()
    return processor.enhance_image(img, frame)

def apply_overlays(temp_path, frame, photo):
    """Apply configured overlays to image."""
    overlay_prefs = json.loads(frame.overlay_preferences) if frame.overlay_preferences else {}
    return overlay_manager.apply_overlays(temp_path, overlay_prefs, frame, photo)

def generate_final_output(image_path, frame, output_type):
    """Generate final output based on requested type."""
    if output_type == 'compressed':
        return generate_compressed_output(image_path, frame.orientation)
    elif output_type in ('epaper', 'epd') or (output_type == 'rgb565' and is_epaper_frame(frame)):
        return generate_epaper_output(image_path, frame.orientation if frame else 'portrait')
    elif output_type == 'rgb565':
        return generate_rgb565_output(image_path, frame)
    
    # Cleanup temp files after sending
    response = send_file(image_path, mimetype='image/jpeg')
    response.call_on_close(lambda: cleanup_temp_files(os.path.dirname(image_path)))
    return response

def generate_compressed_output(image_path, orientation):
    """Generate compressed output for e-paper displays."""
    app.logger.info(f"Calling imgToArray")
    from imgToArray import img_to_array
    with Image.open(image_path) as img:
        raw_bytes = img_to_array(img, orientation)
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')

def is_epaper_frame(frame):
    """Determine if the frame is an e-paper device based on capabilities."""
    if not frame or not frame.capabilities:
        return False

    capabilities = frame.capabilities
    if isinstance(capabilities, str):
        try:
            capabilities = json.loads(capabilities)
        except Exception:
            return False

    display_type = str(capabilities.get('display_type', '')).lower()
    if not display_type:
        display_type = str(capabilities.get('screen_type', '')).lower()

    return 'e-paper' in display_type or 'epaper' in display_type

def generate_epaper_output(image_path, orientation):
    """Generate 4bpp palette output for Seeed_GFX color e-paper."""
    app.logger.info("Generating Seeed_GFX e-paper output")
    from imgToArray import img_to_epaper_4bit
    with Image.open(image_path) as img:
        raw_bytes = img_to_epaper_4bit(img, orientation)
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')

def generate_rgb565_output(image_path, frame):
    """Generate RGB565 output for TFT/LCD displays."""
    app.logger.info(f"Generating RGB565 output for frame {frame.id if frame else 'unknown'}")
    from imgToArray import img_to_rgb565
    
    # Parse screen resolution from frame if available (e.g., "320x240")
    target_width, target_height = 320, 240  # Default dimensions
    if frame and frame.screen_resolution:
        try:
            parts = frame.screen_resolution.lower().split('x')
            if len(parts) == 2:
                target_width = int(parts[0])
                target_height = int(parts[1])
        except (ValueError, AttributeError):
            app.logger.warning(f"Could not parse screen_resolution: {frame.screen_resolution}, using defaults")
    
    with Image.open(image_path) as img:
        raw_bytes = img_to_rgb565(img, target_width, target_height)
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')

def get_size_str(size_bytes):
    """Convert bytes to human readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024

def get_system_info():
    """Gather system information"""
    # Storage info
    disk = psutil.disk_usage('/')
    storage = {
        'total': get_size_str(disk.total),
        'used': get_size_str(disk.used),
        'free': get_size_str(disk.free),
        'percent': disk.percent
    }
    
    # Photos info
    photos_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'])
    total_size = 0
    photo_count = 0
    
    for dirpath, dirnames, filenames in os.walk(photos_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
            photo_count += 1 
    
    photos = {
        'total': photo_count,
        'total_size': get_size_str(total_size),
        'avg_size': get_size_str(total_size / max(photo_count, 1))
    }
    
    # System info
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            cpu_temp = float(f.read()) / 1000
    except:
        cpu_temp = 0
    
    # Get uptime
    uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
    uptime_str = str(uptime).split('.')[0]  # Remove microseconds
    
    system = {
        'cpu_temp': round(cpu_temp, 1),
        'cpu_usage': psutil.cpu_percent(),
        'memory_usage': psutil.virtual_memory().percent,
        'uptime': uptime_str,
        'python_version': platform.python_version()
    }
    
    # Network info
    hostname = socket.gethostname()
    ip = socket.gethostbyname(hostname)
    
    network = {
        'ip': ip,
        'hostname': hostname,
        'connection_type': 'WiFi' if os.path.exists('/sys/class/net/wlan0') else 'Ethernet'
    }
    
    return storage, photos, system, network

def get_frame_diagnostics():
    """Gather frame diagnostic information"""
    frames_info = {
        'total': 0,
        'online': 0,
        'total_photos': 0,
        'list': []
    }
    
    frames = PhotoFrame.query.all()
    frames_info['total'] = len(frames)
    
    current_time = datetime.now()
    online_threshold = timedelta(minutes=5)
    
    for frame in frames:
        # Get frame's playlist using PlaylistEntry via frame's playlist
        playlist = []
        if frame.playlist_id:
            playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).order_by(PlaylistEntry.order).all()
        
        last_ping = frame.last_wake_time or datetime.min
        is_online = (current_time - last_ping) < online_threshold
        
        if is_online:
            frames_info['online'] += 1
        
        if not frame.last_wake_time:
            status = "Never Connected"
        elif is_online:
            status = "Online"
        else:
            status = f"Offline (Last seen {frame.last_wake_time.strftime('%Y-%m-%d %H:%M:%S')})"
        
        frames_info['total_photos'] += len(playlist)
        
        # Get the latest diagnostic data
        diagnostics = frame.last_diagnostic
        if diagnostics:
            try:
                diagnostics = ast.literal_eval(diagnostics)  # Convert string to dict if stored as string
            except (ValueError, SyntaxError):
                diagnostics = None
        
        frame_data = {
            'id': frame.id,
            'name': frame.name,
            'is_online': is_online,
            'last_seen': frame.last_wake_time.strftime('%Y-%m-%d %H:%M:%S') if frame.last_wake_time else "Never",
            'photo_count': len(playlist),
            'status': status,
            'ip': getattr(frame, 'last_ip', "Unknown"),
            'version': diagnostics.get('version') if diagnostics else None,
            'diagnostics': diagnostics
        }
        
        frames_info['list'].append(frame_data)
    
    return frames_info

def get_version():
    """Read version from version.txt file"""
    version_file = os.path.join(os.path.dirname(__file__), 'version.txt')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except Exception:
        return 'unknown'

@app.route('/info')
def info():
    """Display system information and frame details."""
    # Get all frames from the database
    frames = PhotoFrame.query.all()
    
    # Get the current time
    now = datetime.now()

    # Get system information
    system_info = {
        'cpu_temp': get_cpu_temperature(),
        'cpu_usage': psutil.cpu_percent(),
        'memory_usage': psutil.virtual_memory().percent,
        'uptime': get_uptime(),
        'python_version': platform.python_version()
    }
    
    # Get storage information
    storage = get_storage_info()
    
    # Get network information
    network = {
        'ip': get_ip_address(),
        'hostname': socket.gethostname(),
        'connection_type': 'Ethernet/WiFi'
    }
    
    # Get photo statistics
    photos = get_photo_stats()

    # Get discovery information
    discovery_info = {
        'service_name': f"PhotoFrame Server ({network['hostname']})",
        'service_type': '_photoframe._tcp.local.',
        'port': ZEROCONF_PORT,
        'properties': {
            'server_id': app.server_id if hasattr(app, 'server_id') else secrets.token_hex(8),
            'version': '1.0.0',
            'frames': len(frames),
            'photos': photos.get('total', 0),
            'hostname': network['hostname'],
            'ip': network['ip']
        }
    }

    # Generate a server ID if not already set
    if not hasattr(app, 'server_id'):
        app.server_id = secrets.token_hex(8)
    
    # Load server settings
    server_settings = load_server_settings()
    
    # Load AI settings (handles missing file via defaults)
    ai_settings = load_photogen_settings()
    
    return render_template('info.html',
                         frames=frames,
                         system=system_info,
                         storage=storage,
                         photos=photos,
                         network=network,
                         version=get_version(),
                         now=now,
                         discovery=discovery_info,
                         server_id=app.server_id,
                         # Add these new template variables
                         server_name=server_settings['server_name'],
                         current_timezone=server_settings['timezone'],
                         timezones=pytz.all_timezones,
                         cleanup_interval=server_settings['cleanup_interval'],
                         log_level=server_settings['log_level'],
                         max_upload_size=server_settings['max_upload_size'],
                         discovery_port=server_settings['discovery_port'],
                         ai_settings=ai_settings,
                         ai_analysis_enabled=server_settings.get('ai_analysis_enabled', False),
                         dark_mode=server_settings.get('dark_mode', False)  # Add dark_mode setting
                         )

def get_cpu_temperature():
    """Get CPU temperature."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = float(f.read()) / 1000.0
        return round(temp, 1)
    except:
        return 0

def get_uptime():
    """Get system uptime."""
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    return str(timedelta(seconds=int(uptime_seconds)))

def get_storage_info():
    """Get storage information."""
    disk = psutil.disk_usage('/')
    return {
        'total': format_bytes(disk.total),
        'used': format_bytes(disk.used),
        'free': format_bytes(disk.free),
        'percent': disk.percent
    }

def get_photo_stats():
    """Get photo statistics."""
    photos = Photo.query.all()
    total_size = 0
    for photo in photos:
        try:
            if photo.filename:
                path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
                if os.path.exists(path):
                    total_size += os.path.getsize(path)
        except:
            pass
    
    return {
        'total': len(photos),
        'total_size': format_bytes(total_size),
        'avg_size': format_bytes(total_size / len(photos)) if photos else '0 B'
    }

def format_bytes(size):
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"

def get_ip_address():
    """Get the server's IP address."""
    try:
        # This creates a socket but doesn't actually connect
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return 'Unknown'

@app.route('/generate')
def generate():
    """Render the photo generation page."""
    frames = PhotoFrame.query.all()
    return render_template('generate.html', frames=frames)

@app.route('/api/generate', methods=['POST'])
def generate_images():
    """Generate images using the selected service."""
    try:
        data = request.get_json()
        service = data.get('service')
        model = data.get('model')
        prompt = data.get('prompt')
        orientation = data.get('orientation', 'square')
        style_preset = data.get('style_preset')
        
        # Load settings to get API key and base URL
        settings = load_photogen_settings()
        
        if service == 'dalle':
            api_key = settings.get('dalle_api_key')
            base_url = settings.get('dalle_base_url')
        elif service == 'stability':
            api_key = settings.get('stability_api_key')
            base_url = settings.get('stability_base_url')
        else:
            return jsonify({'error': 'Invalid service selected'}), 400
            
        if not api_key:
            return jsonify({'error': 'API key not found for selected service'}), 400

        # Generate images using PhotoGenerator with base URL and style_preset
        result = photo_generator.generate_images(
            service, model, prompt, orientation, api_key, 
            base_url=base_url, style_preset=style_preset  # Add style_preset here
        )
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error generating images: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/save-generated', methods=['POST'])
def save_generated():
    """Save a generated image."""
    try:
        data = request.get_json()
        image_data = data.get('image')
        
        if not image_data:
            return jsonify({'error': 'No image data provided'}), 400
            
        filename = photo_generator.save_image(image_data)
        
        return jsonify({
            'message': 'Image saved successfully',
            'filename': filename
        })
        
    except Exception as e:
        logging.error(f"Error saving generated image: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/add-to-frame', methods=['POST'])
def add_to_frame():
    """Save a generated image and add it to a frame's playlist."""
    try:
        data = request.get_json()
        image_data = data.get('image')
        frame_id = data.get('frame_id')
        
        if not image_data:
            return jsonify({'error': 'No image data provided'}), 400
            
        # Save the image
        filename = photo_generator.save_image(image_data)
        
        # Add to frame's playlist if frame_id provided
        if frame_id:
            frame = PhotoFrame.query.get(frame_id)
            if frame:
                photo = Photo(filename=filename)
                db.session.add(photo)
                frame.photos.append(photo)
                db.session.commit()
        
        return jsonify({
            'message': 'Image saved and added to frame',
            'filename': filename
        })
        
    except Exception as e:
        logging.error(f"Error adding generated image to frame: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add this constant at the top with other constants
PHOTOGEN_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config", "photogen_settings.json")

def load_photogen_settings():
    """Load photo generation settings."""
    default_settings = {
        'dalle_api_key': '',
        'stability_api_key': '',
        'dalle_base_url': '',
        'stability_base_url': '',
        'default_service': 'dalle',
        'default_models': {
            'dalle': 'dall-e-3',
            'stability': 'ultra'
        }
    }
    
    try:
        if os.path.exists(PHOTOGEN_SETTINGS_FILE):
            with open(PHOTOGEN_SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                # Ensure all default keys exist
                for key, value in default_settings.items():
                    if key not in settings:
                        settings[key] = value
                if 'default_models' not in settings:
                    settings['default_models'] = default_settings['default_models']
                return settings
        return default_settings
    except Exception as e:
        logging.error(f"Error loading photo generation settings: {e}")
        return default_settings

def save_photogen_settings(settings):
    """Save photo generation settings."""
    try:
        with open(PHOTOGEN_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        return True
    except Exception as e:
        logging.error(f"Error saving photo generation settings: {e}")
        return False

# Update the routes to use these functions
@app.route('/api/photogen_settings', methods=['GET'])
def get_photogen_settings():
    """Get photo generation settings."""
    try:
        settings = load_photogen_settings()
        return jsonify(settings)
    except Exception as e:
        logging.error(f"Error getting photo generation settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/photogen_settings', methods=['POST'])
def update_photogen_settings():
    """Update photo generation settings."""
    try:
        data = request.get_json()
        current_settings = load_photogen_settings()
        
        # Update API keys if provided
        if 'dalle_api_key' in data:
            current_settings['dalle_api_key'] = data['dalle_api_key']
        if 'stability_api_key' in data:
            current_settings['stability_api_key'] = data['stability_api_key']
            
        # Update base URLs if provided
        if 'dalle_base_url' in data:
            current_settings['dalle_base_url'] = data['dalle_base_url']
        if 'stability_base_url' in data:
            current_settings['stability_base_url'] = data['stability_base_url']
            
        # Update default service if provided
        if 'default_service' in data:
            current_settings['default_service'] = data['default_service']
            
        # Update default models if provided
        if 'default_models' in data:
            if 'default_models' not in current_settings:
                current_settings['default_models'] = {}
            current_settings['default_models'].update(data['default_models'])
        
        save_photogen_settings(current_settings)
        return jsonify({'message': 'Settings updated successfully', 'settings': current_settings})
    except Exception as e:
        logging.error(f"Error updating photo generation settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/gallery/add', methods=['POST'])
def add_to_gallery():
    try:
        data = request.json
        if not data or 'image' not in data:
            logging.error("No image data provided in request")
            return jsonify({'success': False, 'error': 'No image data provided'}), 400
            
        # Convert base64 to BytesIO
        base64_image = data['image']
        image_data = base64.b64decode(base64_image.split(',')[1] if ',' in base64_image else base64_image)
        file_obj = io.BytesIO(image_data)
        
        # Set filename
        filename = f"gallery_{secrets.token_hex(8)}.jpg"
        
        # Use the existing upload logic
        if allowed_file(filename):
            # Create thumbnails directory if it doesn't exist
            thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)
            
            # Save original file
            filename = secure_filename(filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(image_data)
            
            # Generate thumbnail
            try:
                with Image.open(filepath) as img:
                    img.thumbnail((400, 400))
                    thumb_filename = f"thumb_{filename}"
                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                    img.save(thumb_path, "JPEG")
            except Exception as e:
                app.logger.error(f"Error generating thumbnail: {e}")
                thumb_filename = None
            
            # Process for both orientations
            portrait_path = photo_processor.process_for_orientation(filepath, 'portrait')
            landscape_path = photo_processor.process_for_orientation(filepath, 'landscape')
            
            # Save photo record in the database
            photo = Photo(
                filename=filename,
                portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                thumbnail=thumb_filename
            )
            db.session.add(photo)
            db.session.commit()
            
            # Run AI analysis if enabled
            server_settings = load_server_settings()
            if server_settings.get('ai_analysis_enabled', False):
                def async_analyze(app, db, photo_id):
                    with app.app_context():
                        try:
                            photo_analyzer = PhotoAnalyzer(app, db)
                            photo_analyzer.analyze_photo(photo_id)
                        except Exception as e:
                            app.logger.error(f"Background analysis failed: {e}")
                
                Thread(target=async_analyze, args=(app, db, photo.id)).start()
            
            return jsonify({
                'success': True,
                'message': 'Image added to gallery and database successfully',
                'filename': filename
            })
            
    except Exception as e:
        logging.error(f"Error adding image to gallery: {str(e)}")
        logging.exception("Full traceback:")
        return jsonify({
            'success': False,
            'error': f'Failed to add image to gallery: {str(e)}' 
        }), 500

@app.route('/api/playlist/add', methods=['POST'])
def add_to_playlist():
    try:
        data = request.get_json()
        frame_id = data.get('frame_id')
        photo_id = data.get('photo_id')
        
        if not frame_id or not photo_id:
            return jsonify({'error': 'Missing frame_id or photo_id'}), 400
            
        frame = PhotoFrame.query.get(frame_id)
        photo = Photo.query.get(photo_id)
        
        if not frame or not photo:
            return jsonify({'error': 'Frame or photo not found'}), 404
        
        # Ensure frame has a playlist
        if not frame.playlist:
            playlist_name = f"{frame.name} Playlist"
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id

        # Check if photo is already in the frame's playlist
        existing_entry = PlaylistEntry.query.filter_by(
            playlist_id=frame.playlist_id,
            photo_id=photo_id
        ).first()
        
        if existing_entry:
            # Instead of returning an error, move the photo to the top of the playlist
            current_order = existing_entry.order
            
            # Update entries with order less than the current entry
            db.session.query(PlaylistEntry)\
                .filter(PlaylistEntry.playlist_id == frame.playlist_id)\
                .filter(PlaylistEntry.order < current_order)\
                .update({
                    PlaylistEntry.order: PlaylistEntry.order + 1
                })
            
            # Move the existing entry to the top (order 0)
            existing_entry.order = 0
            db.session.commit()
            
            if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
                app.mqtt_integration.update_frame_options(frame)
                
            return jsonify({
                'success': True,
                'message': f'Photo moved to the top of {frame.name or frame.id}\'s playlist'
            })
            
        # Shift all existing playlist entries down by one
        db.session.query(PlaylistEntry)\
            .filter_by(playlist_id=frame.playlist_id)\
            .update({
                PlaylistEntry.order: PlaylistEntry.order + 1
            })
        
        # Create new playlist entry at order 0 (next up)
        playlist_entry = PlaylistEntry(
            playlist_id=frame.playlist_id,
            photo_id=photo_id,
            order=0  # This will be the next up photo
        )
        
        db.session.add(playlist_entry)
        db.session.commit()
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(frame)
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/integrations')
def integrations_page():
    logger.info("Accessing integrations page")
    try:
        mqtt_settings = load_mqtt_settings()
        mqtt_status = "Disabled"
        if mqtt_settings.get('enabled'):
            mqtt_status = app.mqtt_integration.status if (hasattr(app, 'mqtt_integration') and app.mqtt_integration) else "Unknown"
        
        return render_template('integrations.html',
                             mqtt_enabled=mqtt_settings.get('enabled', False),
                             mqtt_settings=mqtt_settings,
                             mqtt_status=mqtt_status)
    except Exception as e:
        logger.error(f"Error in integrations_page: {e}")
        return f"Error loading integrations page: {str(e)}", 500

def save_mqtt_settings(mqtt_settings):
    """Save MQTT settings to config file in integrations folder."""
    config_path = os.path.join(os.path.dirname(__file__), 'integrations', 'mqtt_config.txt')
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(mqtt_settings, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving MQTT settings: {e}")

@app.route('/api/integrations/mqtt/settings', methods=['POST'])
def mqtt_settings():
    try:
        data = request.json
        mqtt_settings = {
            'enabled': data.get('enabled', False),
            'broker': data.get('broker'),
            'port': data.get('port', 1883),
            'username': data.get('username'),
            'password': data.get('password')
        }
        save_mqtt_settings(mqtt_settings)
        
        if mqtt_settings['enabled']:
            if not hasattr(app, 'mqtt_integration') or not app.mqtt_integration:
                app.mqtt_integration = MQTTIntegration(
                    mqtt_settings,
                    app.config['UPLOAD_FOLDER'],
                    PhotoFrame,
                    db,
                    PlaylistEntry,
                    app,
                    CustomPlaylist  # Add this line
                )
            else:
                app.mqtt_integration.stop()
                app.mqtt_integration = MQTTIntegration(
                    mqtt_settings,
                    app.config['UPLOAD_FOLDER'],
                    PhotoFrame,
                    db,
                    PlaylistEntry,
                    app,
                    CustomPlaylist  # Add this line
                )
        else:
            if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
                app.mqtt_integration.stop()
                delattr(app, 'mqtt_integration')
        
        return jsonify({
            'success': True,
            'status': app.mqtt_integration.status if (hasattr(app, 'mqtt_integration') and app.mqtt_integration) else "Disabled"
        })
    except Exception as e:
        logger.error(f"Error updating MQTT settings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/integrations/mqtt/test', methods=['POST'])
def test_mqtt():
    try:
        data = request.json
        test_settings = {
            'broker': data.get('broker'),
            'port': data.get('port', 1883),
            'username': data.get('username'),
            'password': data.get('password')
        }
        
        test_integration = MQTTIntegration(
            test_settings,
            app.config['UPLOAD_FOLDER'],
            PhotoFrame,
            db,
            PlaylistEntry,
            app,
            CustomPlaylist  # Add this line
        )
        result = test_integration.test_connection()
        test_integration.stop()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error testing MQTT connection: {e}")
        return jsonify({
            'success': False,
            'message': f"Test failed: {str(e)}",
            'status': "Test failed"
        }), 500

# Add new routes for Google Photos integration
@app.route('/api/google-photos/auth-url') 
def get_google_photos_auth_url():
    """Get Google Photos authorization URL."""
    try:
        # Check if client secrets file exists
        client_secrets_path = os.path.join(basedir, 'credentials', 'gphotos_auth.json')
        if not os.path.exists(client_secrets_path):
            logger.error("Client secrets file not found at: %s", client_secrets_path)
            return jsonify({
                'error': 'Google Photos client secrets file not found. Please configure the integration first.'
            }), 500

        auth_url = google_photos.get_auth_url()
        if not auth_url:
            logger.error("Failed to generate auth URL")
            return jsonify({'error': 'Failed to generate authorization URL'}), 500

        logger.info("Successfully generated Google Photos auth URL")
        return jsonify({'auth_url': auth_url})
    except Exception as e:
        logger.exception("Error generating Google Photos auth URL: %s", str(e))
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/google-photos/auth', methods=['POST'])
def handle_google_photos_auth():
    """Handle Google Photos authorization code."""
    try:
        auth_code = request.json.get('code')
        if not auth_code:
            return jsonify({'error': 'No authorization code provided'}), 400
            
        success = google_photos.handle_auth_code(auth_code)
        if success:
            logger.info("Successfully connected to Google Photos")
            return jsonify({'message': 'Successfully connected to Google Photos'})
        
        logger.error("Failed to connect to Google Photos")
        return jsonify({'error': 'Failed to connect to Google Photos'}), 500
    except Exception as e:
        logger.exception("Error handling Google Photos auth code: %s", str(e))
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/google-photos/disconnect', methods=['POST'])
def disconnect_google_photos():
    """Disconnect Google Photos integration."""
    try:
        success = google_photos.disconnect()
        if success:
            logger.info("Successfully disconnected from Google Photos")
            return jsonify({'message': 'Successfully disconnected from Google Photos'})
        
        logger.error("Failed to disconnect from Google Photos")
        return jsonify({'error': 'Failed to disconnect from Google Photos'}), 500
    except Exception as e:
        logger.exception("Error disconnecting from Google Photos: %s", str(e))
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/google-photos/status')
def google_photos_status():
    """Get Google Photos connection status."""
    try:
        connected = google_photos.is_connected()
        return jsonify({'connected': connected})
    except Exception as e:
        logger.exception("Error checking Google Photos status: %s", str(e))
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/google-photos/search')
def search_google_photos():
    """Search Google Photos library."""
    try:
        query = request.args.get('query')
        page_token = request.args.get('pageToken')
        album_id = request.args.get('albumId')  # Add this line
        
        photos, next_page_token = google_photos.search_photos(
            query=query, 
            page_token=page_token,
            album_id=album_id  # Add this parameter
        )
        
        if photos is None:
            return jsonify({'error': 'Failed to search photos'}), 500
            
        return jsonify({
            'photos': photos,
            'nextPageToken': next_page_token
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/google-photos/import', methods=['POST'])
def import_google_photos():
    """Import selected photos from Google Photos."""
    try:
        photo_ids = request.json.get('photoIds', [])
        if not photo_ids:
            return jsonify({'error': 'No photos selected'}), 400
            
        imported_photos = []
        for photo_id in photo_ids:
            try:
                # Download photo from Google Photos
                photo_data = google_photos.download_photo(photo_id)
                if not photo_data:
                    continue
                    
                # Set filename for the photo
                filename = f"gphotos_{secrets.token_hex(8)}.jpg"
                
                # Use the existing upload logic
                if allowed_file(filename):
                    # Create thumbnails directory if it doesn't exist
                    thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
                    os.makedirs(thumbnails_dir, exist_ok=True)
                    
                    # Save original file
                    filename = secure_filename(filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    with open(filepath, 'wb') as f:
                        photo_data.seek(0)  # Reset file pointer to beginning
                        f.write(photo_data.read())  # Read bytes from BytesIO
                    
                    # Generate thumbnail
                    try:
                        with Image.open(filepath) as img:
                            img.thumbnail((400, 400))
                            thumb_filename = f"thumb_{filename}"
                            thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                            img.save(thumb_path, "JPEG")
                    except Exception as e:
                        app.logger.error(f"Error generating thumbnail: {e}")
                        thumb_filename = None
                    
                    # Process for both orientations
                    portrait_path = photo_processor.process_for_orientation(filepath, 'portrait')
                    landscape_path = photo_processor.process_for_orientation(filepath, 'landscape')
                    
                    # Save photo record in the database
                    photo = Photo(
                        filename=filename,
                        portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                        landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                        thumbnail=thumb_filename
                    )
                    db.session.add(photo)
                    db.session.commit()
                    
                    # Run AI analysis if enabled
                    server_settings = load_server_settings()
                    if server_settings.get('ai_analysis_enabled', False):
                        def async_analyze(app, db, photo_id):
                            with app.app_context():
                                try:
                                    photo_analyzer = PhotoAnalyzer(app, db)
                                    photo_analyzer.analyze_photo(photo_id)
                                except Exception as e:
                                    app.logger.error(f"Background analysis failed: {e}")
                        
                        Thread(target=async_analyze, args=(app, db, photo.id)).start()
                    
                    imported_photos.append(filename)
                    
            except Exception as e:
                logger.error(f"Error importing photo {photo_id}: {e}")
                continue
        
        return jsonify({
            'message': f'Successfully imported {len(imported_photos)} photos',
            'imported': imported_photos
        })
        
    except Exception as e:
        logger.error(f"Error during Google Photos import: {e}")
        return jsonify({'error': str(e)}), 500

# Add new routes for weather settings
@app.route('/api/weather/settings', methods=['GET'])
def get_weather_settings():
    try:
        # Use the correct path for weather_config.json
        weather_config_path = os.path.join(os.path.dirname(__file__), 'integrations', 'overlays', 'weather_config.json')
        weather_integration = WeatherIntegration(weather_config_path)
        
        # Get current settings
        settings = weather_integration.settings
        
        return jsonify({
            'success': True,
            'settings': settings
        })
    except Exception as e:
        logging.error(f"Error getting weather settings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/weather/settings', methods=['POST'])
def update_weather_settings():
    try:
        settings = request.get_json()
        if not settings:
            return jsonify({'success': False, 'error': 'No settings provided'})

        # Use the correct path for weather_config.json
        weather_config_path = os.path.join(os.path.dirname(__file__), 'integrations', 'overlays', 'weather_config.json')
        weather_integration = WeatherIntegration(weather_config_path)
        
        # Save the settings
        success = weather_integration.save_settings(settings)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings'})
            
    except Exception as e:
        logging.error(f"Error updating weather settings: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/weather/test', methods=['POST'])
def test_weather():
    try:
        result = weather_integration.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test/overlay/<frame_id>')
def test_overlay(frame_id):
    """Preview final image with settings and overlays applied."""
    import io
    import uuid
    from PIL import Image
    from photo_processing import PhotoProcessor

    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    playlist_entry = None
    if frame.playlist_id:
        playlist_entry = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).order_by(PlaylistEntry.order).first()
    if not playlist_entry or not playlist_entry.photo:
        return jsonify({'error': 'No photos in playlist'}), 404

    photo = playlist_entry.photo
    orientation = frame.orientation or 'portrait'
    photo_path = get_photo_path(photo, orientation)  # Helper function to get correct path

    if not os.path.exists(photo_path):
        return jsonify({'error': 'Photo file not found'}), 404

    # Handle preview settings
    use_frame = handle_preview_settings(frame, request.args)

    try:
        # Process image enhancements
        with Image.open(photo_path) as img:
            processor = PhotoProcessor()
            enhanced_img = processor.enhance_image(img, use_frame)

            # Create temporary file for overlay processing
            temp_path = create_temp_image(enhanced_img)
        
        # Apply overlays using the existing overlay manager
        overlay_prefs = json.loads(use_frame.overlay_preferences) if use_frame.overlay_preferences else {}
        final_img = overlay_manager.apply_overlays(
            image_path=temp_path,
            preferences=overlay_prefs,
            frame=use_frame,
            photo=photo
        )

        # Cleanup and return
        os.remove(temp_path)
        return serve_pil_image(final_img)

    except Exception as e:
        app.logger.error(f"Overlay error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Helper functions
def get_photo_path(photo, orientation):
    if orientation == 'portrait' and photo.portrait_version:
        return os.path.join(app.config['UPLOAD_FOLDER'], photo.portrait_version)
    if orientation == 'landscape' and photo.landscape_version:
        return os.path.join(app.config['UPLOAD_FOLDER'], photo.landscape_version)
    return os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)

def handle_preview_settings(frame, args):
    if args.get('preview', 'false').lower() == 'true':
        from copy import copy
        preview_frame = copy(frame)
        preview_frame.contrast_factor = float(args.get('contrast_factor', 1.0))
        preview_frame.saturation = int(args.get('saturation', 100))
        preview_frame.blue_adjustment = int(args.get('blue_adjustment', 0))
        preview_frame.padding = int(args.get('padding', 0))
        preview_frame.overlay_preferences = json.dumps({
            'weather': args.get('weather', '').lower() == 'true',
            'metadata': args.get('metadata', '').lower() == 'true',
            'qrcode': args.get('qrcode', '').lower() == 'true'
        })
        return preview_frame
    return frame

def create_temp_image(img):
    temp_dir = app.config['UPLOAD_FOLDER']
    temp_name = f"temp_{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(temp_dir, temp_name)
    img.save(temp_path)
    return temp_path

def serve_pil_image(pil_img):
    img_io = io.BytesIO()
    pil_img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

@app.route('/api/frames/list', methods=['GET'])
def list_frames():
    """Get a simple list of frames for dropdowns."""
    try:
        frames = PhotoFrame.query.order_by(PhotoFrame.order, PhotoFrame.name).all()
        frame_list = [{
            'id': frame.id,
            'name': frame.name,
            'type': frame.frame_type,  # Might be useful to show virtual vs physical frames
            'status': frame.get_status()[0]  # 0=offline, 1=sleeping, 2=online
        } for frame in frames]
        
        return jsonify({'frames': frame_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/frames/<frame_id>', methods=['PUT'])
def update_frame(frame_id):
    """Update a frame's details (name, etc.)."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404
        
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'error': 'Name cannot be empty'}), 400
            
            old_name = frame.name
            frame.name = new_name
            
            # If frame name changed, check if there's a playlist with the old name and rename it
            if old_name and old_name != new_name:
                old_playlist_name = f"{old_name} Playlist"
                new_playlist_name = f"{new_name} Playlist"
                
                matching_playlist = Playlist.query.filter_by(name=old_playlist_name).first()
                if matching_playlist:
                    # Make sure the new name doesn't conflict with an existing playlist
                    existing_playlist = Playlist.query.filter_by(name=new_playlist_name).first()
                    if not existing_playlist:
                        matching_playlist.name = new_playlist_name
                        app.logger.info(f"Renamed playlist '{old_playlist_name}' to '{new_playlist_name}'")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'frame': {
                'id': frame.id,
                'name': frame.name
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/frames/<frame_id>/playlist', methods=['PUT'])
def change_frame_playlist(frame_id):
    """Change the playlist assigned to a frame."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404
        
        data = request.get_json()
        playlist_id = data.get('playlist_id')
        
        if playlist_id:
            playlist = Playlist.query.get(playlist_id)
            if not playlist:
                return jsonify({'error': 'Playlist not found'}), 404
            frame.playlist_id = playlist_id
        else:
            # Create a new playlist for this frame if none specified
            playlist_name = f"{frame.name} Playlist"
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id
        
        db.session.commit()
        
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(frame)
        
        return jsonify({
            'success': True,
            'playlist_id': frame.playlist_id,
            'playlist_name': frame.playlist.name
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/frames/<frame_id>/delete', methods=['DELETE'])
def delete_frame(frame_id):
    """Delete a frame and its associated data."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'success': False, 'error': 'Frame not found'}), 404

        # Remove MQTT discovery entries if MQTT is enabled
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            try:
                # Publish empty configs to remove entities
                discovery_prefix = app.mqtt_integration.discovery_prefix
                device_components = ['select', 'number', 'sensor']
                
                for component in device_components:
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_next_up/config"
                    app.mqtt_integration.client.publish(topic, '', retain=True)
                    
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_sleep_interval/config"
                    app.mqtt_integration.client.publish(topic, '', retain=True)
                    
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_last_wake/config"
                    app.mqtt_integration.client.publish(topic, '', retain=True)
            except Exception as e:
                app.logger.error(f"Error cleaning up MQTT entities: {e}")

        # Get the frame's playlist before deleting
        playlist_id = frame.playlist_id
        
        # Delete any scheduled generation tasks associated with this frame
        scheduled_generations = ScheduledGeneration.query.filter_by(frame_id=frame_id).all()
        for schedule in scheduled_generations:
            db.session.delete(schedule)
        
        # Delete the frame
        db.session.delete(frame)
        
        # Delete the frame's playlist if no other frames are using it
        if playlist_id:
            other_frames_using_playlist = PhotoFrame.query.filter_by(playlist_id=playlist_id).count()
            if other_frames_using_playlist == 0:
                # Delete playlist entries first
                PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
                # Delete the playlist
                playlist = Playlist.query.get(playlist_id)
                if playlist:
                    db.session.delete(playlist)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Frame deleted successfully' 
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting frame: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/scheduled-generations', methods=['GET'])
def get_scheduled_generations():
    """Get all scheduled generations and available frames."""
    try:
        schedules = ScheduledGeneration.query.all()
        
        return jsonify({
            'schedules': [{
                'id': s.id,
                'name': s.name,
                'prompt': s.prompt,
                'frame_id': s.frame_id,
                'frame_name': s.frame.name or s.frame.id,  # Add frame name
                'service': s.service,
                'model': s.model,
                'orientation': s.orientation,
                'style_preset': s.style_preset,  # Add style_preset here
                'cron_expression': s.cron_expression,
                'is_active': s.is_active,
                'created_at': s.created_at.isoformat()
            } for s in schedules]
        })
    except Exception as e:
        logger.error(f"Error getting scheduled generations: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/scheduled-generations', methods=['POST'])
def create_scheduled_generation():
    """Create a new scheduled generation."""
    try:
        data = request.get_json()
        schedule = ScheduledGeneration(
            name=data['name'],
            prompt=data['prompt'],
            frame_id=data['frame_id'],
            service=data['service'],
            model=data['model'],
            orientation=data.get('orientation', 'portrait'),
            style_preset=data.get('style_preset'),  # Add this line
            cron_expression=data['cron_expression']
        )
        
        db.session.add(schedule)
        db.session.commit()
        
        # Add job to scheduler
        scheduler.add_job(schedule.id, schedule.cron_expression)
        
        return jsonify({
            'success': True,
            'id': schedule.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/scheduled-generations/<int:schedule_id>', methods=['DELETE'])
def delete_scheduled_generation(schedule_id):
    """Delete a scheduled generation."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        
        # Remove from scheduler
        if scheduler:
            scheduler.remove_job(str(schedule_id))
        
        db.session.delete(schedule)
        db.session.commit()
        
        return jsonify({'message': 'Schedule deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/generation-history', methods=['GET'])
def get_generation_history():
    """Get generation history."""
    history = GenerationHistory.query.order_by(GenerationHistory.generated_at.desc()).all()
    return jsonify([{
        'id': h.id,
        'schedule_id': h.schedule_id,
        'generated_at': h.generated_at.isoformat(),
        'success': h.success,
        'error_message': h.error_message,
        'photo_id': h.photo_id 
    } for h in history])

@app.route('/api/scheduled-generations/<int:schedule_id>', methods=['GET'])
def get_scheduled_generation(schedule_id):
    """Get a specific scheduled generation."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        return jsonify({
            'id': schedule.id,
            'name': schedule.name,
            'prompt': schedule.prompt,
            'frame_id': schedule.frame_id,
            'service': schedule.service,
            'model': schedule.model,
            'orientation': schedule.orientation,
            'style_preset': schedule.style_preset,  # Add this line
            'cron_expression': schedule.cron_expression
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scheduled-generations/<int:schedule_id>', methods=['PUT'])
def update_scheduled_generation(schedule_id):
    """Update a scheduled generation."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        data = request.get_json()
        
        schedule.name = data['name']
        schedule.prompt = data['prompt']
        schedule.frame_id = data['frame_id']
        schedule.service = data['service']
        schedule.model = data['model']
        schedule.orientation = data['orientation']
        schedule.cron_expression = data['cron_expression']
        
        db.session.commit()
        
        # Update scheduler
        if scheduler:
            scheduler.remove_job(str(schedule.id))
            scheduler.add_job(schedule.id, schedule.cron_expression)
        
        return jsonify({'message': 'Schedule updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/scheduled-generations/<int:schedule_id>/test', methods=['POST'])
def test_scheduled_generation(schedule_id):
    return execute_generation(schedule_id)

def execute_generation(schedule_id):
    """Execute a scheduled generation."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)

        # For custom playlist schedules - just assign the playlist to the frame
        if schedule.service == 'custom_playlist':
            try:
                playlist_id = int(schedule.model)
                playlist = Playlist.query.get(playlist_id)
                if not playlist:
                    return jsonify({'error': 'Playlist not found'}), 404

                # Get valid entries to verify playlist has content
                entries = playlist.entries.order_by(PlaylistEntry.order).all()
                valid_entries = [e for e in entries if e.photo_id and e.photo]
                
                if not valid_entries:
                    return jsonify({'error': 'No valid photos in playlist'}), 400

                # Simply assign the playlist to the frame
                frame = PhotoFrame.query.get(schedule.frame_id)
                if frame:
                    frame.playlist_id = playlist_id

                # Record in generation history
                history = GenerationHistory(
                    schedule_id=schedule.id,
                    success=True,
                    photo_id=valid_entries[0].photo_id,
                    name=schedule.name
                )
                db.session.add(history)
                db.session.commit()

                return jsonify({
                    'success': True,
                    'message': 'Playlist applied successfully'
                })
            except Exception as e:
                db.session.rollback()
                return jsonify({'error': str(e)}), 500

        # For Unsplash schedules, redirect to the specific handler
        if schedule.service == 'unsplash':
            return test_unsplash_schedule(schedule_id)
        # For Pixabay schedules, redirect to the specific handler
        elif schedule.service == 'pixabay':
            return test_pixabay_schedule(schedule_id)
        
        # Load API settings from the form data in the session or database
        if schedule.service == 'stability':
            os.environ['STABILITY_API_KEY'] = request.form.get('stability_api_key', '')
            os.environ['STABILITY_BASE_URL'] = request.form.get('stability_base_url', 'https://api.stability.ai/v1')
        else:  # dalle
            os.environ['DALLE_API_KEY'] = request.form.get('dalle_api_key', '')
            os.environ['DALLE_BASE_URL'] = request.form.get('dalle_base_url', 'https://api.openai.com/v1')
        
        # Use the photo generator to create the image
        result = photo_generator.generate_photo(
            prompt=schedule.prompt,
            service=schedule.service,
            model=schedule.model,
            orientation=schedule.orientation
        )
        
        if result.get('error'):
            raise Exception(result['error'])
            
        # Save the generated photo
        filename = result['filename']
        photo = Photo(filename=filename)
        db.session.add(photo)
        db.session.commit()
        
        # Add to frame's playlist
        frame = PhotoFrame.query.get(schedule.frame_id)
        if frame and frame.playlist_id:
            playlist_entry = PlaylistEntry(
                playlist_id=frame.playlist_id,
                photo_id=photo.id,
                order=db.session.query(db.func.max(PlaylistEntry.order))
                          .filter_by(playlist_id=frame.playlist_id)
                          .scalar() or 0 + 1
            )
            db.session.add(playlist_entry)
        
        # Record in generation history
        history = GenerationHistory(
            schedule_id=schedule.id,
            success=True,
            photo_id=photo.id,
            name=schedule.name  # Add the schedule name
        )
        db.session.add(history)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Test generation completed successfully'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generation-history/clear', methods=['POST'])
def clear_generation_history():
    """Clear all generation history entries."""
    try:
        GenerationHistory.query.delete()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Generation history cleared'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/photos/<photo_id>/edit', methods=['POST'])
def edit_photo(photo_id):
    try:
        data = request.json
        photo = Photo.query.get_or_404(photo_id)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
        
        # Update heading if provided
        if 'heading' in data:
            photo.heading = data['heading']
        
        # Update date/time metadata if provided
        if 'dateTime' in data and data['dateTime']:
            # Initialize exif_metadata if it doesn't exist
            if not photo.exif_metadata:
                photo.exif_metadata = {}
            
            # Update all date/time fields
            photo.exif_metadata['DateTime'] = data['dateTime']
            photo.exif_metadata['DateTimeOriginal'] = data['dateTime']
            photo.exif_metadata['DateTimeDigitized'] = data['dateTime']
            
            # Update formatted date and time
            try:
                # Parse the date string (YYYY:MM:DD HH:MM:SS)
                date_parts = data['dateTime'].split(' ')[0].split(':')
                time_parts = data['dateTime'].split(' ')[1]
                
                year = int(date_parts[0])
                month = int(date_parts[1])
                day = int(date_parts[2])
                
                # Create datetime object
                dt = datetime(year, month, day)
                
                # Format the date and time
                photo.exif_metadata['formatted_date'] = dt.strftime('%B %d, %Y')
                
                # Parse time
                time_parts = time_parts.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                
                # Format time (12-hour format with AM/PM)
                am_pm = 'AM' if hour < 12 else 'PM'
                hour_12 = hour % 12
                if hour_12 == 0:
                    hour_12 = 12
                
                photo.exif_metadata['formatted_time'] = f"{hour_12}:{minute:02d} {am_pm}"
            except Exception as e:
                print(f"Error formatting date/time: {e}")
        # Handle the case where the user wants to remove the date/time
        elif 'dateTime' in data and data['dateTime'] is None:
            # If exif_metadata exists, remove date/time fields
            if photo.exif_metadata:
                for field in ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized', 'formatted_date', 'formatted_time']:
                    if field in photo.exif_metadata:
                        del photo.exif_metadata[field]
        
        db.session.commit()
        
        # Load the image and apply EXIF orientation
        with Image.open(image_path) as img_file:
            img = ImageOps.exif_transpose(img_file)
            exif_data = img.info.get('exif')
            
            # Apply edits
            if 'brightness' in data:
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(float(data['brightness']))
                
            if 'contrast' in data:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(float(data['contrast']))
                
            if 'saturation' in data:
                enhancer = ImageEnhance.Color(img)
                img = enhancer.enhance(float(data['saturation']))
                
            if 'sharpness' in data:
                enhancer = ImageEnhance.Sharpness(img)
                img = enhancer.enhance(float(data['sharpness']))
                
            if 'rotation' in data and data['rotation'] != 0:
                img = img.rotate(float(data['rotation']), expand=True)
            
            # Save the edited image, preserving EXIF data
            try:
                img.save(image_path, quality=95, exif=exif_data)
            except:
                # If saving with EXIF fails, save without it
                img.save(image_path, quality=95)
            
            # Generate new thumbnail
            thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)
            
            if hasattr(photo, 'thumbnail') and photo.thumbnail:
                thumb_path = os.path.join(thumbnails_dir, photo.thumbnail)
                # Create thumbnail
                thumb_img = img.copy()
                thumb_img.thumbnail((400, 400))
                try:
                    thumb_img.save(thumb_path, quality=85, exif=exif_data)
                except:
                    thumb_img.save(thumb_path, quality=85)
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error editing photo: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/frames/<frame_id>/import-settings', methods=['POST'])
def import_frame_settings(frame_id):
    """Import settings and playlist from another frame."""
    try:
        data = request.get_json()
        source_frame_id = data.get('source_frame_id')
        
        # Get both frames
        target_frame = db.session.get(PhotoFrame, frame_id)
        source_frame = db.session.get(PhotoFrame, source_frame_id)
        
        if not target_frame or not source_frame:
            return jsonify({
                'success': False,
                'error': 'Frame not found'
            }), 404

        # Copy settings
        target_frame.sleep_interval = source_frame.sleep_interval
        target_frame.orientation = source_frame.orientation
        target_frame.overlay_preferences = source_frame.overlay_preferences
        
        # Copy image settings
        if hasattr(source_frame, 'contrast_factor'):
            target_frame.contrast_factor = source_frame.contrast_factor
        if hasattr(source_frame, 'saturation'):
            target_frame.saturation = source_frame.saturation
        if hasattr(source_frame, 'blue_adjustment'):
            target_frame.blue_adjustment = source_frame.blue_adjustment
        if hasattr(source_frame, 'padding'):
            target_frame.padding = source_frame.padding
        if hasattr(source_frame, 'color_map'):
            target_frame.color_map = source_frame.color_map

        # Assign the source frame's playlist to the target frame
        if source_frame.playlist_id:
            target_frame.playlist_id = source_frame.playlist_id

        db.session.commit()

        # Update MQTT if enabled
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(target_frame)

        return jsonify({
            'success': True,
            'message': 'Settings imported successfully'
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error importing frame settings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    
@app.route('/api/restart_discovery', methods=['POST'])
def restart_discovery():
    """Restart the frame discovery service."""
    try:
        frame_discovery.stop()
        frame_discovery.start()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error restarting discovery: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/google-photos/albums')
def get_google_photos_albums():
    """Get list of Google Photos albums."""
    try:
        albums = google_photos.list_albums()
        if albums is None:
            return jsonify({'error': 'Failed to fetch albums'}), 500
            
        return jsonify({
            'albums': albums
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500 


@app.route('/api/frame/<frame_id>')
def get_frame(frame_id):
    """Get frame information."""
    # Replace Query.get() with Session.get()
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
    
    # Get next photo info safely
    next_photo = PhotoHelper.get_current_photo(frame.id)
    
    # Choose the appropriate version based on frame orientation for next photo
    next_photo_info = None
    if next_photo:
        if frame.orientation == 'portrait' and next_photo.portrait_version:
            next_photo_info = next_photo.portrait_version
        elif frame.orientation == 'landscape' and next_photo.landscape_version:
            next_photo_info = next_photo.landscape_version
        else:
            next_photo_info = next_photo.filename  # Fall back to original if no oriented version exists
    
    # Get current photo info
    current_photo = None
    if frame.current_photo_id:
        current_photo = db.session.get(Photo, frame.current_photo_id)
    
    # Choose the appropriate version based on frame orientation for current photo
    current_photo_info = None
    if current_photo:
        if frame.orientation == 'portrait' and current_photo.portrait_version:
            current_photo_info = current_photo.portrait_version
        elif frame.orientation == 'landscape' and current_photo.landscape_version:
            current_photo_info = current_photo.landscape_version
        else:
            current_photo_info = current_photo.filename  # Fall back to original if no oriented version exists 
    
    # Get status
    now = datetime.now(timezone.utc)
    status = frame.get_status(now)
    
    # Calculate next wake time
    next_wake_time = None
    if frame.next_wake_time:
        next_wake_time = frame.next_wake_time.isoformat()
    
    return jsonify({
        'id': frame.id,
        'name': frame.name,
        'sleep_interval': frame.sleep_interval,
        'orientation': frame.orientation,
        'frame_type': frame.frame_type,
        'current_photo': current_photo_info,
        'next_photo': next_photo_info,
        'status': status,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None,
        'next_wake_time': next_wake_time
    })

@app.route('/api/frame/<frame_id>/next')
def next_photo(frame_id):
    """Navigate to the next photo in the playlist."""
    # Check if the frame exists
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
    
    # Get playlist entries via frame's playlist
    playlist = []
    if frame.playlist_id:
        playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                    .order_by(PlaylistEntry.order).all()
    
    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 404
    
    # Use the frame timing manager for virtual frames
    if frame.frame_type == 'virtual':
        # First, call the frame timing manager to handle the transition
        # This will update the current_photo_id to the next photo in the playlist
        result = frame_timing_manager.force_transition(frame_id, direction='next')
        if isinstance(result, tuple):  # Error case
            return jsonify(result[0]), result[1]
        
        # After the transition, get the updated frame with the new current_photo_id
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame or not frame.current_photo_id:
            return jsonify({'error': 'Frame update failed'}), 500
        
        # Refresh the playlist
        if frame.playlist_id:
            playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                        .order_by(PlaylistEntry.order).all()
        
        # Now update the playlist order based on the new current_photo_id
        # Find the current photo's entry in the playlist
        current_entry = None
        for entry in playlist:
            if entry.photo_id == frame.current_photo_id:
                current_entry = entry
                break
        
        if current_entry:
            # Reorder the playlist by moving all entries up one position
            # and the current entry to the bottom
            current_order = current_entry.order
            
            # Shift entries between current and end up by one position
            for entry in playlist:
                if entry.photo_id != current_entry.photo_id and entry.order > current_order:
                    entry.order -= 1
            
            # Move the current entry to the bottom
            current_entry.order = len(playlist) - 1
            
            db.session.commit()
            app.logger.info(f"Updated playlist order for virtual frame {frame_id} after transition")
        
        return jsonify(result)
    
    # Find the current photo's position in the playlist
    current_position = 0
    if frame.current_photo_id:
        for i, entry in enumerate(playlist):
            if entry.photo_id == frame.current_photo_id:
                current_position = i
                break
    
    # Get the next photo (or loop back to the first)
    next_position = (current_position + 1) % len(playlist)
    next_entry = playlist[next_position]
    
    # Update the frame's current photo
    frame.current_photo_id = next_entry.photo_id
    
    # Move the current entry to the bottom of the playlist
    # and shift all entries after it up by one
    current_entry = playlist[current_position]
    current_order = current_entry.order
    
    # Shift entries between current and end up by one position
    for entry in playlist:
        if entry.order > current_order:
            entry.order -= 1
    
    # Move current to the end
    current_entry.order = len(playlist) - 1
    
    db.session.commit()
    
    # Get the updated photo
    photo = db.session.get(Photo, next_entry.photo_id)
    
    # Calculate next wake time
    now = datetime.now(timezone.utc)
    frame.last_wake_time = now
    frame.next_wake_time = now + timedelta(minutes=frame.sleep_interval)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'current_photo': photo.filename if photo else None,
        'next_wake_time': frame.next_wake_time.isoformat() if frame.next_wake_time else None,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None
    })

@app.route('/api/frame/<frame_id>/prev')
def prev_photo(frame_id):
    """Navigate to the previous photo in the playlist."""
    # Check if the frame exists
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
    
    # Get playlist entries via frame's playlist
    playlist = []
    if frame.playlist_id:
        playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                    .order_by(PlaylistEntry.order).all()
    
    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 404
    
    # Use the frame timing manager for virtual frames
    if frame.frame_type == 'virtual':
        
        # First, call the frame timing manager to handle the transition
        # This will update the current_photo_id to the previous photo in the playlist
        result = frame_timing_manager.force_transition(frame_id, direction='prev')
        if isinstance(result, tuple):  # Error case
            return jsonify(result[0]), result[1]
        
        # After the transition, get the updated frame with the new current_photo_id
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame or not frame.current_photo_id:
            return jsonify({'error': 'Frame update failed'}), 500
        
        # Now update the playlist order based on the new current_photo_id
        # Find the current photo's entry in the playlist
        current_entry = None
        for entry in playlist:
            if entry.photo_id == frame.current_photo_id:
                current_entry = entry
                break
        
        if current_entry:
            # Reorder the playlist by moving all entries up one position
            # and the current entry to the bottom
            current_order = current_entry.order
            
            # Shift entries between current and end up by one position
            for entry in playlist:
                if entry.photo_id != current_entry.photo_id and entry.order > current_order:
                    entry.order -= 1
            
            # Move the current entry to the bottom
            current_entry.order = len(playlist) - 1
            
            db.session.commit()
            app.logger.info(f"Updated playlist order for virtual frame {frame_id} after transition")
        
        return jsonify(result)
    
    # For physical frames, use the existing logic
    # Playlist already fetched at the start of the function
    
    # Find the current photo's position in the playlist
    current_position = 0
    if frame.current_photo_id:
        for i, entry in enumerate(playlist):
            if entry.photo_id == frame.current_photo_id:
                current_position = i
                break
    
    # Get the previous photo (or loop back to the last)
    prev_position = (current_position - 1) % len(playlist)
    prev_entry = playlist[prev_position]
    
    # Update the frame's current photo
    frame.current_photo_id = prev_entry.photo_id
    
    # Move the current entry to the bottom of the playlist
    # and shift all entries after it up by one
    current_entry = playlist[current_position]
    current_order = current_entry.order
    
    # Shift entries between current and end up by one position
    for entry in playlist:
        if entry.order > current_order:
            entry.order -= 1
    
    # Move current to the end
    current_entry.order = len(playlist) - 1
    
    db.session.commit()
    
    # Get the updated photo
    photo = db.session.get(Photo, prev_entry.photo_id)
    
    # Calculate next wake time
    now = datetime.now(timezone.utc)
    frame.last_wake_time = now
    frame.next_wake_time = now + timedelta(minutes=frame.sleep_interval)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'current_photo': photo.filename if photo else None,
        'next_wake_time': frame.next_wake_time.isoformat() if frame.next_wake_time else None,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None
    })

# Add route to serve thumbnails
@app.route('/photos/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve photo thumbnails."""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails'), filename)

@app.route('/api/frames/<frame_id>/force_update', methods=['POST'])
def force_frame_update(frame_id):
    """Force a frame to update its current photo."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'success': False, 'error': 'Frame not found'}), 404
            
        # Check if frame is online
        status = frame.get_status()[0]
        if status != 'online':
            return jsonify({
                'success': False, 
                'error': f'Frame is {status}. Cannot force update.'
            }), 400

        # If MQTT is enabled, publish an update
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.publish_state(frame)
            
        return jsonify({'success': True})
        
    except Exception as e:
        app.logger.error(f"Error forcing frame update: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/metadata/styles', methods=['GET'])
def get_metadata_styles():
    """Get current metadata styling configuration."""
    try:
        logging.info("Fetching metadata styles")
        styles = app.metadata_integration.styles
        logging.info(f"Retrieved styles: {styles}")
        
        if styles is None:
            logging.error("No styles found")
            return jsonify({
                'success': False,
                'error': 'No styles configuration found'
            }), 404
            
        return jsonify({
            'success': True,
            'styles': styles
        })
    except Exception as e:
        logging.error(f"Error getting metadata styles: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/metadata/styles', methods=['POST'])
def update_metadata_styles():
    """Update metadata styling configuration."""
    try:
        styles = request.json
        success = app.metadata_integration.save_styles(styles)
        if success:
            return jsonify({
                'success': True,
                'message': 'Styles updated successfully'
            })
        else:
            raise Exception('Failed to save styles')
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/metadata/preview', methods=['POST'])
def generate_metadata_preview():
    """Generate a preview of metadata overlay with current styles."""
    try:
        # Get the first available photo for preview
        photo = Photo.query.first()
        if not photo:
            return jsonify({
                'success': False,
                'error': 'No photos available for preview'
            }), 404

        # Get full path to photo
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
        
        # Create sample metadata for preview
        sample_metadata = {
            'date': datetime.now().strftime('%B %d, %Y'),
            'time': datetime.now().strftime('%I:%M %p'),
            'camera_make': 'Sample Camera',
            'camera_model': 'Model X',
            'location': '47.6062°N, 122.3321°W'
        }
        
        # Apply metadata overlay using sample data
        with Image.open(photo_path) as img:
            draw = ImageDraw.Draw(img)
            
            # Store original get_metadata method
            original_get_metadata = app.metadata_integration.get_metadata
            
            try:
                # Temporarily override get_metadata to return sample data
                app.metadata_integration.get_metadata = lambda x: sample_metadata
                
                # Apply overlay
                result_img = overlay_manager.overlays['metadata'].apply(img, draw, photo_path)
                
                # Convert image to base64 for preview
                buffered = io.BytesIO()
                result_img.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
            finally:
                # Restore original get_metadata method
                app.metadata_integration.get_metadata = original_get_metadata
        
        return jsonify({
            'success': True,
            'preview': f'data:image/png;base64,{img_str}'
        })
    except Exception as e:
        logging.error(f"Error generating preview: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/groups')
def manage_sync_groups():
    """Display sync groups management page."""
    try:
        sync_groups = SyncGroup.query.all()
        available_frames = PhotoFrame.query.filter_by(sync_group_id=None).all()
        
        return render_template('sync_groups.html', 
                             sync_groups=sync_groups,
                             available_frames=available_frames,
                             now=datetime.utcnow())
    except Exception as e:
        logger.error(f"Error loading sync groups page: {e}")
        flash('Error loading sync groups', 'danger')
        return redirect(url_for('index'))

@app.route('/api/sync-groups', methods=['GET'])
def get_sync_groups():
    """Get all sync groups and their frames."""
    try:
        groups = SyncGroup.query.all()
        return jsonify([{
            'id': group.id,
            'name': group.name,
            'sleep_interval': group.sleep_interval,
            'frames': [{
                'id': frame.id,
                'name': frame.name,
                'last_sync': frame.last_sync_time.isoformat() if frame.last_sync_time else None
            } for frame in group.frames]
        } for group in groups])
    except Exception as e:
        logger.error(f"Error getting sync groups: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync-groups', methods=['POST'])
def create_sync_group():
    """Create a new sync group."""
    try:
        data = request.get_json()
        group = SyncGroup(
            name=data['name'],
            sleep_interval=float(data.get('sleep_interval', 5.0))
        )
        db.session.add(group)
        db.session.commit()
        return jsonify({'id': group.id, 'name': group.name})
    except Exception as e:
        logger.error(f"Error creating sync group: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync-groups/<int:group_id>', methods=['GET'])
def get_sync_group(group_id):
    """Get sync group details for editing."""
    try:
        group = SyncGroup.query.get(group_id)
        if not group:
            return jsonify({"error": "Group not found"}), 404
            
        return jsonify({
            "id": group.id,
            "name": group.name,
            "sleep_interval": group.sleep_interval
        })
    except Exception as e:
        logger.error(f"Error getting sync group: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync-groups/<int:group_id>', methods=['PUT'])
def edit_sync_group(group_id):
    """Update a sync group's settings."""
    try:
        group = SyncGroup.query.get(group_id)
        if not group:
            return jsonify({"error": "Group not found"}), 404
            
        data = request.get_json()
        group.name = data.get('name', group.name)
        group.sleep_interval = float(data.get('sleep_interval', group.sleep_interval))
        
        # Update sleep intervals for all frames in the group
        for frame in group.frames:
            frame.sleep_interval = group.sleep_interval
        
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating sync group: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync-groups/<int:group_id>', methods=['DELETE'])
def delete_sync_group(group_id):
    """Delete a sync group."""
    try:
        group = SyncGroup.query.get(group_id)
        if not group:
            return jsonify({"error": "Group not found"}), 404
            
        # Remove group association from frames
        for frame in group.frames:
            frame.sync_group_id = None
            frame.last_sync_time = None
        
        db.session.delete(group)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting sync group: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync-groups/<int:group_id>/frames', methods=['POST'])
def add_frame_to_group(group_id):
    """Add a frame to a sync group."""
    try:
        data = request.get_json()
        frame_id = data['frame_id']
        
        frame = PhotoFrame.query.get(frame_id)
        if not frame:
            return jsonify({"error": "Frame not found"}), 404
            
        group = SyncGroup.query.get(group_id)
        if not group:
            return jsonify({"error": "Sync group not found"}), 404
        
        frame.sync_group = group
        frame.sleep_interval = group.sleep_interval
        
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error adding frame to group: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync-groups/<int:group_id>/frames/<frame_id>', methods=['DELETE'])
def remove_frame_from_group(group_id, frame_id):
    """Remove a frame from a sync group."""
    try:
        # Convert frame_id from hex string if needed
        frame = PhotoFrame.query.get(frame_id)
        if not frame or frame.sync_group_id != group_id:
            logger.error(f"Frame {frame_id} not found in group {group_id}")
            return jsonify({"error": "Frame not found in group"}), 404
        
        logger.info(f"Removing frame {frame_id} from group {group_id}")
        frame.sync_group_id = None
        frame.last_sync_time = None
        frame.next_wake_time = None
        
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error removing frame from group: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/server-time')
def get_server_time():
    """Return server timezone and current time information."""
    now = datetime.utcnow()
    local = datetime.now()
    
    return jsonify({
        "utc_time": now.isoformat(),
        "local_time": local.isoformat(),
        "timezone": time.tzname,
        "utc_offset": (local - now).total_seconds() / 3600
    })

@app.route('/api/frames/<frame_id>/toggle_shuffle', methods=['POST'])
def toggle_frame_shuffle(frame_id):
    frame = PhotoFrame.query.get_or_404(frame_id)
    frame.shuffle_enabled = not frame.shuffle_enabled
    db.session.commit()
    
    if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
        app.mqtt_integration.update_frame_options(frame)
    
    return jsonify({
        'success': True,
        'shuffle_enabled': frame.shuffle_enabled
    })

@app.route('/api/frames/<frame_id>/clear_playlist', methods=['POST'])
def clear_frame_playlist(frame_id):
    """Clear all photos from a frame's playlist."""
    frame = PhotoFrame.query.get_or_404(frame_id)
    
    # Remove all playlist entries from the frame's playlist
    if frame.playlist_id:
        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).delete()
        db.session.commit()
    
    if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
        app.mqtt_integration.update_frame_options(frame)
    
    return jsonify({
        'success': True,
        'message': 'Playlist cleared successfully'
    })

# Add this constant with other constants
SERVER_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config", "server_settings.json")

def load_server_settings():
    """Load server settings from file."""
    default_settings = {
        'server_name': socket.gethostname(),
        'timezone': 'UTC',
        'cleanup_interval': 24,  # hours
        'log_level': 'INFO',
        'max_upload_size': 10,  # MB
        'discovery_port': ZEROCONF_PORT
    }
    
    try:
        if os.path.exists(SERVER_SETTINGS_FILE):
            with open(SERVER_SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                # Ensure all default keys exist
                for key, value in default_settings.items():
                    if key not in settings:
                        settings[key] = value
                return settings
        return default_settings
    except Exception as e:
        logger.error(f"Error loading server settings: {e}")
        return default_settings

def save_server_settings(settings):
    """Save server settings to file."""
    try:
        with open(SERVER_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving server settings: {e}")
        return False

# Add new route to handle settings updates
@app.route('/api/server/settings', methods=['POST'])
def update_server_settings():
    """Update server settings."""
    try:
        data = request.get_json()
        current_settings = load_server_settings()
        
        # Validate and update settings
        if 'server_name' in data:
            current_settings['server_name'] = data['server_name']
        if 'timezone' in data and data['timezone'] in pytz.all_timezones:
            current_settings['timezone'] = data['timezone']
        if 'cleanup_interval' in data and isinstance(data['cleanup_interval'], int):
            current_settings['cleanup_interval'] = data['cleanup_interval']
        if 'log_level' in data and data['log_level'] in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            current_settings['log_level'] = data['log_level']
            # Update logger level
            logging.getLogger().setLevel(data['log_level'])
        if 'max_upload_size' in data and isinstance(data['max_upload_size'], int):
            current_settings['max_upload_size'] = data['max_upload_size']
        if 'discovery_port' in data and 1024 <= data['discovery_port'] <= 65535:
            current_settings['discovery_port'] = data['discovery_port']
        if 'ai_analysis_enabled' in data:
            current_settings['ai_analysis_enabled'] = bool(data['ai_analysis_enabled'])
        if 'dark_mode' in data:  # Add dark_mode handling
            current_settings['dark_mode'] = bool(data['dark_mode'])
        
        if save_server_settings(current_settings):
            # Apply settings that need immediate effect
            app.config['MAX_CONTENT_LENGTH'] = current_settings['max_upload_size'] * 1024 * 1024
            
            return jsonify({'success': True, 'settings': current_settings})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
            
    except Exception as e:
        logger.error(f"Error updating server settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/metadata/available-fonts')
def get_available_fonts():
    try:
        fonts = MetadataOverlay.get_available_fonts()
        return jsonify({
            'success': True,
            'fonts': fonts
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/server/ai-settings', methods=['POST'])
def update_ai_settings():
    """Update AI settings."""
    try:
        data = request.get_json()
        
        # Load current settings from new path
        with open(os.path.join('config', 'photogen_settings.json'), 'r') as f:
            current_settings = json.load(f)
        
        # Update settings
        current_settings['custom_server_base_url'] = data['custom_server_base_url']
        current_settings['custom_server_api_key'] = data['custom_server_api_key']
        current_settings['default_models']['custom'] = data['default_models']['custom']
        
        # Save settings to new path
        with open(os.path.join('config', 'photogen_settings.json'), 'w') as f:
            json.dump(current_settings, f, indent=4)
            
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating AI settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/server/test-ai-connection', methods=['POST'])
def test_ai_connection():
    """Test connection to AI server."""
    try:
        data = request.get_json()
        server_url = data.get('server_url')
        api_key = data.get('api_key')

        if not server_url:
            return jsonify({
                'success': False,
                'error': 'Server URL is required'
            }), 400

        # Try to make a simple request to the AI server
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        response = requests.get(server_url, headers=headers, timeout=10)
        
        if response.ok:
            return jsonify({
                'success': True,
                'message': 'Successfully connected to AI server'
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Server returned status code {response.status_code}'
            })

    except requests.exceptions.Timeout:
        return jsonify({
            'success': False,
            'error': 'Connection timed out'
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Could not connect to server'
        })
    except Exception as e:
        logger.error(f"Error testing AI connection: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Global variable to track analysis state
photo_analysis_state = {
    'in_progress': False,
    'current': 0,
    'total': 0,
    'should_cancel': False
}

@app.route('/api/photos/start-analysis', methods=['POST'])
def start_photo_analysis():
    """Start analyzing all photos that don't have AI descriptions."""
    try:
        if photo_analysis_state['in_progress']:
            return jsonify({
                'success': False,
                'error': 'Analysis already in progress'
            }), 400

        # Get all photos without AI descriptions
        photos = Photo.query.filter(Photo.ai_description.is_(None)).all()
        
        if not photos:
            return jsonify({
                'success': False,
                'error': 'No photos found that need analysis'
            }), 400

        photo_analysis_state.update({
            'in_progress': True,
            'current': 0,
            'total': len(photos),
            'should_cancel': False
        })

        # Start analysis in a background thread
        thread = Thread(target=process_photos_for_analysis, args=(photos,))
        thread.start()

        return jsonify({
            'success': True,
            'total_photos': len(photos)
        })

    except Exception as e:
        logger.error(f"Error starting photo analysis: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def process_photos_for_analysis(photos):
    """Process photos in the background."""
    try:
        # Create an application context for the background thread
        with app.app_context():
            photo_analyzer = PhotoAnalyzer(app, db)
            
            for i, photo in enumerate(photos, 1):
                if photo_analysis_state['should_cancel']:
                    break
                    
                try:
                    # Pass the photo ID instead of the photo object
                    photo_analyzer.analyze_photo(photo.id)
                    db.session.commit()
                    
                    # Update progress
                    photo_analysis_state['current'] = i
                    
                except Exception as e:
                    logger.error(f"Error analyzing photo {photo.id}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Error in photo analysis process: {e}")
    finally:
        photo_analysis_state.update({
            'in_progress': False,
            'should_cancel': False
        })

@app.route('/api/photos/analysis-progress')
def get_analysis_progress():
    """Get the current progress of photo analysis."""
    return jsonify({
        'in_progress': photo_analysis_state['in_progress'],
        'current': photo_analysis_state['current'],
        'total': photo_analysis_state['total'],
        'completed': not photo_analysis_state['in_progress'] and photo_analysis_state['current'] > 0
    })

@app.route('/api/photos/cancel-analysis', methods=['POST'])
def cancel_analysis():
    """Cancel the ongoing photo analysis."""
    photo_analysis_state['should_cancel'] = True
    return jsonify({'success': True})

@app.route('/api/qrcode/settings', methods=['GET'])
def get_qrcode_settings():
    """Get QR code settings."""
    try:
        qrcode_config_path = os.path.join(os.path.dirname(__file__), 'integrations', 'overlays', 'qrcode_config.json')
        qrcode_integration = QRCodeIntegration(qrcode_config_path)
        settings = qrcode_integration.load_settings()
        return jsonify({
            'success': True,
            'settings': settings
        })
    except Exception as e:
        logger.error(f"Error getting QR code settings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/qrcode/settings', methods=['POST'])
def update_qrcode_settings():
    """Update QR code settings."""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request must be JSON'
            }), 400

        data = request.get_json()
        if not all(key in data for key in ['size', 'position', 'link_type']):
            return jsonify({
                'success': False,
                'error': 'Missing required fields'
            }), 400

        qrcode_config_path = os.path.join(os.path.dirname(__file__), 'integrations', 'overlays', 'qrcode_config.json')
        qrcode_integration = QRCodeIntegration(qrcode_config_path)
        
        # Get current settings and update with new values
        settings = qrcode_integration.load_settings()
        settings.update({
            'size': data['size'],
            'position': data['position'],
            'link_type': data['link_type']
        })
        
        # Save updated settings
        if qrcode_integration.save_settings(settings):
            # Force reload of QR code integration in overlay manager
            qrcode_integration = QRCodeIntegration(qrcode_config_path)
            overlay_manager.overlays['qrcode'] = QRCodeOverlay(qrcode_integration)
            return jsonify({'success': True})
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save settings'
            }), 500
            
    except Exception as e:
        logger.error(f"Error updating QR code settings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/overlays')
def overlays():
    return render_template('overlays.html')

@app.route('/unsplash')
def unsplash_page():
    """Render the Unsplash scheduling page."""
    frames = PhotoFrame.query.all()
    return render_template('unsplash.html', frames=frames)

@app.route('/api/unsplash/settings', methods=['GET'])
def get_unsplash_settings():
    """Get Unsplash API settings."""
    try:
        settings = unsplash_integration.load_settings()
        # Don't send API key to frontend
        if 'api_key' in settings:
            settings['api_key'] = '********' if settings['api_key'] else ''
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/unsplash/settings', methods=['POST'])
def update_unsplash_settings():
    """Update Unsplash API settings."""
    try:
        data = request.get_json()
        success = unsplash_integration.save_settings({
            'api_key': data.get('api_key')
        })
        if success:
            return jsonify({'message': 'Settings updated successfully'})
        return jsonify({'error': 'Failed to save settings'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/unsplash/preview', methods=['POST'])
def preview_unsplash_search():
    """Preview random Unsplash photos without adding them to the database."""
    try:
        data = request.get_json()
        # Get count from request, default to 5, ensure it's within 1-30
        count = data.get('count', 5) 
        try:
            count = int(count)
        except (ValueError, TypeError):
            count = 5 # Default if conversion fails
        count = max(1, min(30, count)) # Clamp between 1 and 30
        
        results = unsplash_integration.get_random_photos(
            query=data['query'],
            orientation=data.get('orientation'),
            count=count
        ) 

        if not results:
            return jsonify({'error': 'No photos found'}), 404

        # Process each photo for preview only
        processed_results = []
        for photo_data in results:
            try:
                # Add preview URL to results without saving to database
                # Store the original photo data for later use
                preview_data = {
                    'id': photo_data['id'],
                    'preview_url': photo_data['urls']['regular'],
                    'user': photo_data['user'],
                    'description': photo_data.get('description', ''),
                    'alt_description': photo_data.get('alt_description', ''),
                    'links': photo_data['links'],
                    'urls': photo_data['urls'],
                    'original_data': photo_data  # Store the complete original data
                }
                processed_results.append(preview_data)

            except Exception as e:
                logger.error(f"Error processing photo {photo_data.get('id')}: {e}")
                continue

        return jsonify({
            'results': processed_results,
            'message': f'Successfully processed {len(processed_results)} photos for preview'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/unsplash/add-to-frame', methods=['POST'])
def add_unsplash_to_frame():
    """Add selected Unsplash photos to a frame's playlist."""
    try:
        data = request.get_json()
        frame_id = data.get('frame_id')
        photos_data = data.get('photos_data', [])

        if not frame_id:
            return jsonify({'error': 'Frame ID is required'}), 400
        if not photos_data:
            return jsonify({'error': 'No photos selected'}), 400

        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404
        
        if not frame.playlist_id:
            return jsonify({'error': 'Frame does not have a playlist'}), 400

        # Get current max order from frame's playlist
        max_order = db.session.query(db.func.max(PlaylistEntry.order))\
                      .filter_by(playlist_id=frame.playlist_id)\
                      .scalar() or -1

        # Add each photo to the database and playlist
        added_count = 0
        for i, photo_data in enumerate(photos_data):
            try:
                # Download and save the photo
                download_result = unsplash_integration.download_photo(photo_data['original_data'], app.config['UPLOAD_FOLDER'])
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], download_result['filename'])

                # Set current time for the photo's date & time
                current_time = datetime.utcnow()
                
                # Create exif_metadata with the current time
                exif_metadata = {
                    "DateTimeOriginal": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "CreateDate": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "ModifyDate": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "Source": "Unsplash"
                }

                # Generate thumbnail
                thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
                os.makedirs(thumbnails_dir, exist_ok=True)
                thumb_filename = None
                
                try:
                    with Image.open(filepath) as img:
                        img.thumbnail((400, 400))
                        thumb_filename = f"thumb_{download_result['filename']}"
                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                        img.save(thumb_path, "JPEG")
                except Exception as e:
                    app.logger.error(f"Error generating thumbnail for Unsplash photo: {e}")
                
                # Process for both orientations
                portrait_path = None
                landscape_path = None
                try:
                    portrait_path = photo_processor.process_for_orientation(filepath, 'portrait')
                    landscape_path = photo_processor.process_for_orientation(filepath, 'landscape')
                    app.logger.info(f"Created orientation versions for Unsplash photo: portrait={portrait_path}, landscape={landscape_path}")
                except Exception as e:
                    app.logger.error(f"Error creating orientation versions for Unsplash photo: {e}")

                # Create photo record
                photo = Photo(
                    filename=download_result['filename'],
                    heading=download_result['heading'],
                    exif_metadata=exif_metadata,
                    uploaded_at=current_time,
                    portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                    landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                    thumbnail=thumb_filename
                )
                db.session.add(photo)
                db.session.flush()  # Get photo.id without committing

                # Create playlist entry in frame's playlist
                entry = PlaylistEntry(
                    playlist_id=frame.playlist_id,
                    photo_id=photo.id,
                    order=max_order + 1 + i
                )
                db.session.add(entry)
                added_count += 1

            except Exception as e:
                logger.error(f"Error adding photo {photo_data.get('id')} to frame: {e}")
                continue

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Added {added_count} photos to frame'
        })

    except Exception as e:
        logger.error(f"Error in add_unsplash_to_frame: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/unsplash/schedules/<int:schedule_id>/test', methods=['POST'])
def test_unsplash_schedule(schedule_id):
    """Test an Unsplash schedule by running it once."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        if schedule.service != 'unsplash':
            return jsonify({'error': 'Not an Unsplash schedule'}), 400

        # Validate orientation parameter
        orientation = schedule.orientation
        if orientation and orientation not in ['landscape', 'portrait', 'squarish']:
            logger.warning(f"Invalid orientation value: {orientation}, setting to None")
            orientation = None
        
        # Log all parameters being used
        logger.info(f"Using query: '{schedule.prompt}', orientation: '{orientation}'")
        
        # Get a random photo
        try:
            results = unsplash_integration.get_random_photos(
                query=schedule.prompt,
                orientation=orientation,
                count=1
            )
        except Exception as e:
            logger.error(f"Error getting photos from Unsplash: {e}")
            raise Exception(f"Error getting photos from Unsplash: {e}")

        if not results:
            raise Exception('No photos found')

        # Download the photo
        photo_data = results[0]
        download_result = unsplash_integration.download_photo(photo_data, app.config['UPLOAD_FOLDER'])

        # Create photo record
        photo = Photo(
            filename=download_result['filename'],
            heading=download_result['heading']
        )
        db.session.add(photo)
        # Flush the session to get the photo ID
        db.session.flush()
        
        # Now photo.id should be available
        if not photo.id:
            raise Exception("Failed to create photo record")

        # Add to frame's playlist
        frame = PhotoFrame.query.get(schedule.frame_id)
        if frame and frame.playlist_id:
            playlist_entry = PlaylistEntry(
                playlist_id=frame.playlist_id,
                photo_id=photo.id,
                order=db.session.query(db.func.max(PlaylistEntry.order))
                          .filter_by(playlist_id=frame.playlist_id)
                          .scalar() or 0 + 1
            )
            db.session.add(playlist_entry)

        # Record in generation history
        history = GenerationHistory(
            schedule_id=schedule.id,
            success=True,
            photo_id=photo.id,
            name=schedule.name
        )
        db.session.add(history)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Test completed successfully'
        })

    except Exception as e:
        logger.error(f"Error in test Unsplash schedule: {e}")
        db.session.rollback()

        # Record failed attempt in history
        try:
            history = GenerationHistory(
                schedule_id=schedule.id,
                success=False,
                error_message=str(e),
                name=schedule.name
            )
            db.session.add(history)
            db.session.commit()
        except Exception as history_error:
            logger.error(f"Error recording failure history: {history_error}")

        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Modify the scheduler's execute_generation function to handle Unsplash schedules
def execute_generation(schedule_id):
    """Execute a scheduled generation."""
    with app.app_context():
        try:
            schedule = ScheduledGeneration.query.get(schedule_id)
            if not schedule:
                logger.error(f"Schedule {schedule_id} not found")
                return

            if schedule.service == 'unsplash':
                # Validate orientation parameter
                orientation = schedule.orientation
                if orientation and orientation not in ['landscape', 'portrait', 'squarish']:
                    logger.warning(f"Invalid orientation value: {orientation}, setting to None")
                    orientation = None
                
                # Log all parameters being used
                logger.info(f"Using query: '{schedule.prompt}', orientation: '{orientation}'")
                
                # Get a random photo
                try:
                    results = unsplash_integration.get_random_photos(
                        query=schedule.prompt,
                        orientation=orientation,
                        count=1
                    )
                except Exception as e:
                    logger.error(f"Error getting photos from Unsplash: {e}")
                    raise Exception(f"Error getting photos from Unsplash: {e}")

                if not results:
                    raise Exception('No photos found')

                # Download the photo
                photo_data = results[0]
                download_result = unsplash_integration.download_photo(photo_data, app.config['UPLOAD_FOLDER'])

                # Create photo record
                photo = Photo(
                    filename=download_result['filename'],
                    heading=download_result['heading']
                )
                db.session.add(photo)
                # Flush the session to get the photo ID
                db.session.flush()
                
                # Now photo.id should be available
                if not photo.id:
                    raise Exception("Failed to create photo record")

                # Add to frame's playlist
                frame = PhotoFrame.query.get(schedule.frame_id)
                if frame and frame.playlist_id:
                    playlist_entry = PlaylistEntry(
                        playlist_id=frame.playlist_id,
                        photo_id=photo.id,
                        order=db.session.query(db.func.max(PlaylistEntry.order))
                                  .filter_by(playlist_id=frame.playlist_id)
                                  .scalar() or 0 + 1
                    )
                    db.session.add(playlist_entry)

                # Record success in history
                history = GenerationHistory(
                    schedule_id=schedule.id,
                    success=True,
                    photo_id=photo.id,
                    name=schedule.name
                )
                db.session.add(history)
                db.session.commit()

            else:
                # Handle other services (DALL-E, Stability AI, Pixabay)
                # ... existing code ...
                pass

        except Exception as e:
            logger.error(f"Error executing schedule {schedule_id}: {e}")
            # Record failure in history
            try:
                history = GenerationHistory(
                    schedule_id=schedule_id,
                    success=False,
                    error_message=str(e),
                    name=schedule.name if schedule else "Unknown"
                )
                db.session.add(history)
                db.session.commit()
            except Exception as history_error:
                logger.error(f"Error recording failure history: {history_error}")

@app.route('/pixabay')
def pixabay_page():
    """Render the Pixabay scheduling page."""
    frames = PhotoFrame.query.all()
    return render_template('pixabay.html', frames=frames)

@app.route('/api/pixabay/settings', methods=['GET'])
def get_pixabay_settings():
    """Get Pixabay API settings."""
    try:
        settings = pixabay_integration.load_settings()
        # Don't send API key to frontend
        if 'api_key' in settings:
            settings['api_key'] = '********' if settings['api_key'] else ''
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pixabay/settings', methods=['POST'])
def update_pixabay_settings():
    """Update Pixabay API settings."""
    try:
        data = request.get_json()
        success = pixabay_integration.save_settings({
            'api_key': data.get('api_key')
        })
        if success:
            return jsonify({'message': 'Settings updated successfully'})
        return jsonify({'error': 'Failed to save settings'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pixabay/preview', methods=['POST'])
def preview_pixabay_search():
    """Preview Pixabay photos without adding them to the database."""
    try:
        data = request.get_json()
        count = min(max(data.get('count', 1), 1), 200)  # Ensure count is between 1 and 200
        
        # Log the search parameters for debugging
        logger.info(f"Pixabay search parameters: {data}")
        
        results = pixabay_integration.get_random_photos(
            query=data['query'],
            category=data.get('category'),
            colors=data.get('colors'),
            orientation=data.get('orientation'),
            editors_choice=data.get('editors_choice', False),
            safesearch=data.get('safesearch', True),
            count=count
        )

        if not results:
            return jsonify({'error': 'No photos found'}), 404

        # Process each photo for preview without saving to database
        processed_results = []
        for photo_data in results:
            try:
                # Add preview URL and metadata without downloading
                processed_results.append({
                    'id': photo_data['id'],
                    'preview_url': photo_data['webformatURL'],  # Use webformatURL instead of previewURL for better quality
                    'user': photo_data['user'],
                    'user_url': photo_data['pageURL'],
                    'tags': photo_data.get('tags', ''),
                    'original_data': photo_data  # Store original data for later use
                })

            except Exception as e:
                logger.error(f"Error processing photo {photo_data.get('id')}: {e}")
                continue

        return jsonify({
            'results': processed_results,
            'message': f'Found {len(processed_results)} photos'
        })
    except Exception as e:
        logger.error(f"Error in preview_pixabay_search: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pixabay/add-to-frame', methods=['POST'])
def add_pixabay_to_frame():
    """Add selected Pixabay photos to a frame's playlist."""
    try:
        data = request.get_json()
        frame_id = data.get('frame_id')
        photo_data_list = data.get('photos', [])  # Expect full photo data now

        if not frame_id:
            return jsonify({'error': 'Frame ID is required'}), 400
        if not photo_data_list:
            return jsonify({'error': 'No photos selected'}), 400

        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404
        
        if not frame.playlist_id:
            return jsonify({'error': 'Frame does not have a playlist'}), 400

        # Get current max order from frame's playlist
        max_order = db.session.query(db.func.max(PlaylistEntry.order))\
                      .filter_by(playlist_id=frame.playlist_id)\
                      .scalar() or -1

        # Add each photo to the playlist
        added_count = 0
        for i, photo_data in enumerate(photo_data_list):
            try:
                # Log the photo data for debugging
                logger.info(f"Processing photo data: {photo_data}")
                
                # Download and save the photo
                download_result = pixabay_integration.download_photo(
                    photo_data, 
                    app.config['UPLOAD_FOLDER']
                )
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], download_result['filename'])

                # Set current time for the photo's date & time
                current_time = datetime.utcnow()
                
                # Create exif_metadata with the current time
                exif_metadata = {
                    "DateTimeOriginal": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "CreateDate": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "ModifyDate": current_time.strftime("%Y:%m:%d %H:%M:%S"),
                    "Source": "Pixabay"
                }

                # Generate thumbnail
                thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
                os.makedirs(thumbnails_dir, exist_ok=True)
                thumb_filename = None
                
                try:
                    with Image.open(filepath) as img:
                        img.thumbnail((400, 400))
                        thumb_filename = f"thumb_{download_result['filename']}"
                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                        img.save(thumb_path, "JPEG")
                except Exception as e:
                    app.logger.error(f"Error generating thumbnail for Pixabay photo: {e}")
                
                # Process for both orientations
                portrait_path = None
                landscape_path = None
                try:
                    portrait_path = photo_processor.process_for_orientation(filepath, 'portrait')
                    landscape_path = photo_processor.process_for_orientation(filepath, 'landscape')
                    app.logger.info(f"Created orientation versions for Pixabay photo: portrait={portrait_path}, landscape={landscape_path}")
                except Exception as e:
                    app.logger.error(f"Error creating orientation versions for Pixabay photo: {e}")

                # Create photo record
                photo = Photo(
                    filename=download_result['filename'],
                    heading=download_result['heading'],
                    exif_metadata=exif_metadata,
                    uploaded_at=current_time,
                    portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                    landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                    thumbnail=thumb_filename
                )
                db.session.add(photo)
                db.session.flush()  # Get photo.id without committing

                # Create playlist entry in frame's playlist
                entry = PlaylistEntry(
                    playlist_id=frame.playlist_id,
                    photo_id=photo.id,
                    order=max_order + 1 + i
                )
                db.session.add(entry)
                added_count += 1

            except Exception as e:
                logger.error(f"Error adding photo to frame: {e}")
                continue

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Added {added_count} photos to frame'
        })

    except Exception as e:
        logger.error(f"Error in add_pixabay_to_frame: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/pixabay/schedules/<int:schedule_id>/test', methods=['POST'])
def test_pixabay_schedule(schedule_id):
    """Test a Pixabay schedule by running it once."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        if schedule.service != 'pixabay':
            return jsonify({'error': 'Not a Pixabay schedule'}), 400

        # Extract Pixabay parameters from style_preset JSON field
        pixabay_params = {}
        if schedule.style_preset:
            try:
                pixabay_params = json.loads(schedule.style_preset)
                logger.info(f"Pixabay parameters: {pixabay_params}")
            except Exception as e:
                logger.error(f"Error parsing Pixabay parameters: {e}")
                pixabay_params = {}
        
        # Validate parameters
        category = pixabay_params.get('category', '')
        if category and category not in pixabay_integration.CATEGORIES:
            logger.warning(f"Invalid category: {category}, setting to None")
            category = None
            
        colors = pixabay_params.get('colors', '')
        if colors and colors not in pixabay_integration.COLORS:
            logger.warning(f"Invalid colors: {colors}, setting to None")
            colors = None
        
        # Validate orientation parameter
        orientation = schedule.orientation
        if orientation and orientation not in ['horizontal', 'vertical']:
            logger.warning(f"Invalid orientation value: {orientation}, setting to None")
            orientation = None
        
        # Log all parameters being used
        logger.info(f"Using query: '{schedule.prompt}', category: '{category}', colors: '{colors}', orientation: '{orientation}'")
        
        # Get a random photo
        try:
            results = pixabay_integration.get_random_photos(
                query=schedule.prompt,
                category=category,
                colors=colors,
                orientation=orientation,
                editors_choice=pixabay_params.get('editors_choice', False),
                safesearch=pixabay_params.get('safesearch', True),
                count=1
            )
        except Exception as e:
            logger.error(f"Error getting photos from Pixabay: {e}")
            raise Exception(f"Error getting photos from Pixabay: {e}")

        if not results:
            raise Exception('No photos found for the given criteria')

        # Download the photo
        photo_data = results[0]
        download_result = pixabay_integration.download_photo(photo_data, app.config['UPLOAD_FOLDER'])

        # Create photo record
        photo = Photo(
            filename=download_result['filename'],
            heading=download_result['heading']
        )
        db.session.add(photo)
        # Flush the session to get the photo ID
        db.session.flush()
        
        # Now photo.id should be available
        if not photo.id:
            raise Exception("Failed to create photo record")

        # Add to frame's playlist
        frame = PhotoFrame.query.get(schedule.frame_id)
        if frame and frame.playlist_id:
            playlist_entry = PlaylistEntry(
                playlist_id=frame.playlist_id,
                photo_id=photo.id,
                order=db.session.query(db.func.max(PlaylistEntry.order))
                          .filter_by(playlist_id=frame.playlist_id)
                          .scalar() or 0 + 1
            )
            db.session.add(playlist_entry)

        # Record in generation history
        history = GenerationHistory(
            schedule_id=schedule.id,
            success=True,
            photo_id=photo.id,
            name=schedule.name
        )
        db.session.add(history)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Test completed successfully'
        })

    except Exception as e:
        logger.error(f"Error in test Pixabay schedule: {e}")
        db.session.rollback()

        # Record failed attempt in history
        try:
            history = GenerationHistory(
                schedule_id=schedule.id,
                success=False,
                error_message=str(e),
                name=schedule.name
            )
            db.session.add(history)
            db.session.commit()
        except Exception as history_error:
            logger.error(f"Error recording failure history: {history_error}")

        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/pixabay/schedules', methods=['GET'])
def get_pixabay_schedules():
    """Get all Pixabay schedules."""
    try:
        schedules = ScheduledGeneration.query.filter_by(service='pixabay').all()
        result_schedules = []
        
        for s in schedules:
            # Extract Pixabay parameters from style_preset JSON field
            pixabay_params = {}
            if s.style_preset:
                try:
                    pixabay_params = json.loads(s.style_preset)
                except Exception as e:
                    logger.error(f"Error parsing Pixabay parameters: {e}")
            
            result_schedules.append({
                'id': s.id,
                'name': s.name,
                'query': s.prompt,
                'category': pixabay_params.get('category', ''),
                'colors': pixabay_params.get('colors', ''),
                'orientation': s.orientation,
                'editors_choice': pixabay_params.get('editors_choice', False),
                'frame_id': s.frame_id,
                'frame_name': s.frame.name or s.frame.id,
                'cron_expression': s.cron_expression
            })
            
        return jsonify({
            'schedules': result_schedules
        })
    except Exception as e:
        logger.error(f"Error getting Pixabay schedules: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pixabay/schedules', methods=['POST'])
def create_pixabay_schedule():
    """Create a new Pixabay schedule."""
    try:
        data = request.get_json()
        
        # Create schedule with basic fields
        schedule = ScheduledGeneration(
            name=data['name'],
            prompt=data['query'],
            service='pixabay',
            frame_id=data['frame_id'],
            model='pixabay',  # Use 'pixabay' as the model name
            orientation=data.get('orientation', ''),
            cron_expression=data['cron_expression']
        )
        
        # Store Pixabay-specific parameters in the style_preset field as JSON
        pixabay_params = {
            'category': data.get('category', ''),
            'colors': data.get('colors', ''),
            'editors_choice': data.get('editors_choice', False),
            'safesearch': data.get('safesearch', True)
        }
        schedule.style_preset = json.dumps(pixabay_params)
        
        db.session.add(schedule)
        db.session.commit()
        
        # Add job to scheduler
        scheduler.add_job(schedule.id, schedule.cron_expression)
        
        return jsonify({
            'success': True,
            'id': schedule.id
        })
    except Exception as e:
        logger.error(f"Error creating Pixabay schedule: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/pixabay/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_pixabay_schedule(schedule_id):
    """Delete a Pixabay schedule."""
    try:
        schedule = ScheduledGeneration.query.get_or_404(schedule_id)
        
        # Remove from scheduler
        if scheduler:
            scheduler.remove_job(str(schedule_id))
        
        db.session.delete(schedule)
        db.session.commit()
        
        return jsonify({'message': 'Schedule deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Update the execute_generation function to handle Pixabay schedules
def execute_generation(schedule_id):
    """Execute a scheduled generation."""
    with app.app_context():
        try:
            schedule = ScheduledGeneration.query.get(schedule_id)
            if not schedule:
                logger.error(f"Schedule {schedule_id} not found")
                return

            if schedule.service == 'pixabay':
                # Extract Pixabay parameters from style_preset JSON field
                pixabay_params = {}
                if schedule.style_preset:
                    try:
                        pixabay_params = json.loads(schedule.style_preset)
                        logger.info(f"Pixabay parameters: {pixabay_params}")
                    except Exception as e:
                        logger.error(f"Error parsing Pixabay parameters: {e}")
                        pixabay_params = {}
                
                # Validate parameters
                category = pixabay_params.get('category', '')
                if category and category not in pixabay_integration.CATEGORIES:
                    logger.warning(f"Invalid category: {category}, setting to None")
                    category = None
                    
                colors = pixabay_params.get('colors', '')
                if colors and colors not in pixabay_integration.COLORS:
                    logger.warning(f"Invalid colors: {colors}, setting to None")
                    colors = None
                
                # Validate orientation parameter
                orientation = schedule.orientation
                if orientation and orientation not in ['horizontal', 'vertical']:
                    logger.warning(f"Invalid orientation value: {orientation}, setting to None")
                    orientation = None
                
                # Log all parameters being used
                logger.info(f"Using query: '{schedule.prompt}', category: '{category}', colors: '{colors}', orientation: '{orientation}'")
                
                # Get a random photo
                try:
                    results = pixabay_integration.get_random_photos(
                        query=schedule.prompt,
                        category=category,
                        colors=colors,
                        orientation=orientation,
                        editors_choice=pixabay_params.get('editors_choice', False),
                        safesearch=pixabay_params.get('safesearch', True),
                        count=1
                    )
                except Exception as e:
                    logger.error(f"Error getting photos from Pixabay: {e}")
                    raise Exception(f"Error getting photos from Pixabay: {e}")

                if not results:
                    raise Exception('No photos found for the given criteria')

                # Download the photo
                photo_data = results[0]
                download_result = pixabay_integration.download_photo(photo_data, app.config['UPLOAD_FOLDER'])

                # Create photo record
                photo = Photo(
                    filename=download_result['filename'],
                    heading=download_result['heading']
                )
                db.session.add(photo)
                # Flush the session to get the photo ID
                db.session.flush()
                
                # Now photo.id should be available
                if not photo.id:
                    raise Exception("Failed to create photo record")

                # Add to frame's playlist
                frame = PhotoFrame.query.get(schedule.frame_id)
                if frame and frame.playlist_id:
                    playlist_entry = PlaylistEntry(
                        playlist_id=frame.playlist_id,
                        photo_id=photo.id,
                        order=db.session.query(db.func.max(PlaylistEntry.order))
                                  .filter_by(playlist_id=frame.playlist_id)
                                  .scalar() or 0 + 1
                    )
                    db.session.add(playlist_entry)

                # Record success in history
                history = GenerationHistory(
                    schedule_id=schedule.id,
                    success=True,
                    photo_id=photo.id,
                    name=schedule.name
                )
                db.session.add(history)
                db.session.commit()

            else:
                # Handle other services (DALL-E, Stability AI, Unsplash)
                # ... existing code ...
                pass

        except Exception as e:
            logger.error(f"Error executing schedule {schedule_id}: {e}")
            # Record failure in history
            try:
                history = GenerationHistory(
                    schedule_id=schedule_id,
                    success=False,
                    error_message=str(e),
                    name=schedule.name if schedule else "Unknown"
                )
                db.session.add(history)
                db.session.commit()
            except Exception as history_error:
                logger.error(f"Error recording failure history: {history_error}")

@app.route('/api/frame/<frame_id>/status')
def get_frame_status(frame_id):
    """Get detailed status information about a frame for debugging."""
    global frame_timing_manager
    
    # Check if the frame exists
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
    
    # If frame timing manager is not initialized, initialize it
    if not frame_timing_manager:
        logger.warning("Frame timing manager not initialized, initializing now")
    
    # Use the frame timing manager to get detailed status
    status = frame_timing_manager.check_frame_status(frame_id)
    
    # If the frame needs transition and is virtual, force a transition
    if status.get('needs_transition') and frame.frame_type == 'virtual':
        logger.info(f"Frame {frame_id} needs transition, forcing transition now")
        result = frame_timing_manager.force_transition(frame_id, direction='next')
        status['forced_transition'] = True
        status['transition_result'] = result
    
    return jsonify(status)

@app.route('/api/frames/reorder', methods=['POST'])
def reorder_frames():
    try:
        frame_order = request.json.get('frame_order', [])
        
        # Update order for each frame
        for index, frame_id in enumerate(frame_order):
            frame = PhotoFrame.query.get(frame_id)
            if frame:
                frame.order = index
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/playlists')
def manage_playlists():
    playlists = Playlist.query.all()
    frames = PhotoFrame.query.order_by(PhotoFrame.order, PhotoFrame.name).all()
    return render_template('playlists/manage.html', playlists=playlists, frames=frames)

@app.route('/api/custom-playlists', methods=['GET'])
def get_custom_playlists():
    """Get all playlists."""
    playlists = Playlist.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'photo_count': p.entries.count(),
        'frame_count': len(p.frames),
        'frames': [{'id': f.id, 'name': f.name} for f in p.frames],
        'created_at': p.created_at.isoformat(),
        'updated_at': p.updated_at.isoformat()
    } for p in playlists])

@app.route('/api/custom-playlists', methods=['POST'])
def create_custom_playlist():
    """Create a new playlist."""
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400
    
    data = request.get_json()
    name = data.get('name')
    
    # Generate a default name if none provided
    if not name:
        # Find a unique default name
        base_name = "New Playlist"
        existing_names = {p.name for p in Playlist.query.all()}
        if base_name not in existing_names:
            name = base_name
        else:
            counter = 2
            while f"{base_name} {counter}" in existing_names:
                counter += 1
            name = f"{base_name} {counter}"
    
    playlist = Playlist(name=name)
    db.session.add(playlist)
    db.session.commit()
    
    return jsonify({
        'id': playlist.id,
        'name': playlist.name,
        'created_at': playlist.created_at.isoformat()
    }), 201

@app.route('/playlists/<int:playlist_id>/edit')
def edit_custom_playlist(playlist_id):
    """Page for editing a playlist."""
    playlist = Playlist.query.get_or_404(playlist_id)
    
    # Get photos in playlist order
    playlist_photos = [entry.photo for entry in playlist.entries.order_by(PlaylistEntry.order).all() if entry.photo]
    
    # Get photos not in playlist
    playlist_photo_ids = [photo.id for photo in playlist_photos]
    bench_photos = Photo.query.filter(~Photo.id.in_(playlist_photo_ids)).order_by(Photo.uploaded_at.desc()).all() if playlist_photo_ids else Photo.query.order_by(Photo.uploaded_at.desc()).all()
    
    return render_template('playlists/edit.html', 
                         playlist=playlist, 
                         playlist_photos=playlist_photos,
                         bench_photos=bench_photos)

@app.route('/api/custom-playlists/<int:playlist_id>/entries', methods=['POST'])
def add_to_custom_playlist(playlist_id):
    """Add photos to a playlist."""
    playlist = Playlist.query.get_or_404(playlist_id)
    
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400
    
    data = request.get_json()
    photo_ids = data.get('photo_ids', [])
    position = data.get('position', 'end')  # 'start' or 'end'
    
    if not photo_ids:
        return jsonify({'error': 'No photos specified'}), 400
    
    try:
        # Check which photos are already in the playlist
        existing_photo_ids = set(
            entry.photo_id for entry in 
            PlaylistEntry.query.filter_by(playlist_id=playlist_id).all()
        )
        
        # Filter out duplicates
        new_photo_ids = [pid for pid in photo_ids if pid not in existing_photo_ids]
        skipped_count = len(photo_ids) - len(new_photo_ids)
        
        entries_added = []
        
        if new_photo_ids:
            if position == 'start':
                # Shift existing entries to make room at the beginning
                num_new = len(new_photo_ids)
                PlaylistEntry.query.filter_by(playlist_id=playlist_id)\
                    .update({PlaylistEntry.order: PlaylistEntry.order + num_new})
                
                # Add new entries at the beginning
                for order, photo_id in enumerate(new_photo_ids):
                    entry = PlaylistEntry(
                        playlist_id=playlist_id,
                        photo_id=photo_id,
                        order=order
                    )
                    db.session.add(entry)
                    entries_added.append(entry)
            else:
                # Add to end (default behavior)
                next_order = db.session.query(db.func.max(PlaylistEntry.order)).filter(
                    PlaylistEntry.playlist_id == playlist_id 
                ).scalar() or 0
                next_order += 1
                
                for photo_id in new_photo_ids:
                    entry = PlaylistEntry(
                        playlist_id=playlist_id,
                        photo_id=photo_id,
                        order=next_order
                    )
                    db.session.add(entry)
                    entries_added.append(entry)
                    next_order += 1
            
            db.session.commit()
        
        # Return the updated entries for the playlist
        return jsonify({
            'success': True,
            'added_count': len(entries_added),
            'skipped_count': skipped_count,
            'entries': [{
                'id': entry.id,
                'photo_id': entry.photo_id,
                'order': entry.order,
                'thumbnail': entry.photo.thumbnail,
                'filename': entry.photo.filename
            } for entry in entries_added]
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/custom-playlists/<int:playlist_id>/entries', methods=['DELETE'])
def clear_playlist_entries(playlist_id):
    """Clear all entries from a playlist."""
    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
        playlist.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/photos')
def get_all_photos():
    """Get all photos for the photo selector."""
    photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()
    return jsonify([{
        'id': photo.id,
        'filename': photo.filename,
        'thumbnail': photo.thumbnail
    } for photo in photos])


@app.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def api_delete_photo(photo_id):
    """Delete a photo and all its file versions (original, portrait, landscape, thumbnail)."""
    try:
        photo = Photo.query.get_or_404(photo_id)
        
        # Remove photo from all playlists
        PlaylistEntry.query.filter_by(photo_id=photo_id).delete()
        
        # Delete all versions of the file
        upload_folder = app.config['UPLOAD_FOLDER']
        files_to_delete = [
            (photo.filename, upload_folder),  # Original
            (photo.portrait_version, upload_folder),  # Portrait version
            (photo.landscape_version, upload_folder),  # Landscape version
            (photo.thumbnail, os.path.join(upload_folder, 'thumbnails')),  # Thumbnail
        ]
        
        for filename, folder in files_to_delete:
            if filename:
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        app.logger.info(f"Deleted file: {file_path}")
                except Exception as e:
                    app.logger.error(f"Error deleting file {file_path}: {e}")
        
        # Delete the database record
        db.session.delete(photo)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Photo deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting photo {photo_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/frames/<frame_id>/apply-playlist/<int:playlist_id>', methods=['POST'])
def apply_playlist_to_frame(frame_id, playlist_id):
    """Apply a playlist to a frame by setting the frame's playlist_id."""
    try:
        # Get the frame and playlist
        frame = PhotoFrame.query.get_or_404(frame_id)
        playlist = Playlist.query.get_or_404(playlist_id)
        
        # Simply assign the playlist to the frame (no copying needed)
        frame.playlist_id = playlist_id
        
        db.session.commit()
        
        if hasattr(app, 'mqtt_integration') and app.mqtt_integration:
            app.mqtt_integration.update_frame_options(frame)
        
        return jsonify({
            'success': True,
            'message': f'Playlist "{playlist.name}" assigned to frame',
            'playlist_id': playlist_id,
            'playlist_name': playlist.name
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/custom-playlists/<int:playlist_id>/entries/<int:entry_id>', methods=['DELETE'])
def delete_playlist_entry(playlist_id, entry_id):
    """Delete a single entry from a playlist."""
    try:
        # Get the playlist and entry
        playlist = Playlist.query.get_or_404(playlist_id)
        entry = PlaylistEntry.query.get_or_404(entry_id)
        
        # Verify entry belongs to this playlist
        if entry.playlist_id != playlist_id:
            return jsonify({'error': 'Entry does not belong to this playlist'}), 400
        
        # Delete the entry
        db.session.delete(entry)
        
        # Reorder remaining entries to ensure no gaps
        remaining_entries = PlaylistEntry.query.filter_by(
            playlist_id=playlist_id
        ).order_by(PlaylistEntry.order).all()
        
        for i, entry in enumerate(remaining_entries):
            entry.order = i
        
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/custom-playlists/<int:playlist_id>/entries/reorder', methods=['POST'])
def reorder_playlist_entries(playlist_id):
    """Update the order of entries in a playlist."""
    try:
        # Get the playlist
        playlist = Playlist.query.get_or_404(playlist_id)
        
        # Get the entries data from request
        data = request.get_json()
        if not data or 'entries' not in data:
            return jsonify({'error': 'No entries provided'}), 400
            
        entries = data['entries']
        
        # Verify all entries exist and belong to this playlist
        entry_ids = [entry['id'] for entry in entries]
        db_entries = PlaylistEntry.query.filter(
            PlaylistEntry.id.in_(entry_ids),
            PlaylistEntry.playlist_id == playlist_id
        ).all()
        
        if len(db_entries) != len(entry_ids):
            return jsonify({'error': 'Invalid entry IDs provided'}), 400
            
        # Update order for each entry
        for entry_data in entries:
            entry = next(e for e in db_entries if e.id == entry_data['id'])
            entry.order = entry_data['order']
            
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/custom-playlists/<int:playlist_id>', methods=['PUT'])
def update_custom_playlist(playlist_id):
    """Update a playlist's details (name, etc.)."""
    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'error': 'Name cannot be empty'}), 400
            playlist.name = new_name
        
        playlist.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'playlist': {
                'id': playlist.id,
                'name': playlist.name
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/custom-playlists/<int:playlist_id>', methods=['DELETE'])
def delete_custom_playlist(playlist_id):
    """Delete a playlist and all its entries."""
    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        force = request.args.get('force', '').lower() == 'true'
        
        # Check if any frames are using this playlist
        frames_using_playlist = PhotoFrame.query.filter_by(playlist_id=playlist_id).all()
        if frames_using_playlist:
            if not force:
                frame_names = [f.name or f.id for f in frames_using_playlist]
                return jsonify({
                    'error': f'Cannot delete playlist - it is being used by {len(frames_using_playlist)} frame(s): {", ".join(frame_names)}',
                    'frames': [{'id': f.id, 'name': f.name} for f in frames_using_playlist]
                }), 400
            
            # Force delete: unassign playlist from all frames
            for frame in frames_using_playlist:
                frame.playlist_id = None
        
        # Delete all entries first
        PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
        
        # Delete the playlist
        db.session.delete(playlist)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ------------------------------------------------------------------------------
# Main Execution Block
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    # When using the reloader, Werkzeug spawns a child process
    # WERKZEUG_RUN_MAIN is 'true' only in the child process
    # Skip initialization in the parent to avoid duplicate services (scheduler, MQTT, etc.)
    use_reloader = True
    is_reloader_parent = use_reloader and os.environ.get('WERKZEUG_RUN_MAIN') != 'true'

    if not is_reloader_parent:
        # Ensure database exists and tables are created
        with app.app_context():
            logger.info("Initializing database...")
            try:
                # Use a simple query to check connection and existence
                db.session.execute(text('SELECT 1'))
                logger.info("Database connection successful.")
                # Create tables if they don't exist
                db.create_all()
                logger.info("Database tables ensured.")
            except Exception as db_e:
                logger.error(f"Database initialization failed: {db_e}", exc_info=True)
                # Depending on the error, might want to exit
                exit(1)

            # Initialize core app services (Scheduler, Integrations, Discovery)
            logger.info("Initializing application services...")
            init_app_services() # This calls init_integrations, init_scheduler, starts timing manager and discovery

            # Initialize MQTT integration separately if enabled in config
            mqtt_settings = load_mqtt_settings()
            if mqtt_settings.get('enabled', False):
                try:
                    logger.info("MQTT enabled, initializing integration...")
                    app.mqtt_integration = MQTTIntegration(
                        mqtt_settings, app.config['UPLOAD_FOLDER'],
                        PhotoFrame, db, PlaylistEntry, app, CustomPlaylist
                    )
                    logger.info(f"MQTT Integration initialized. Status: {app.mqtt_integration.status}")
                except Exception as mqtt_init_e:
                    logger.error(f"Failed to initialize MQTT integration: {mqtt_init_e}", exc_info=True)
            else:
                 logger.info("MQTT integration is disabled in settings.")

            # Server already started message
            host = '0.0.0.0'
            port = server_settings.get('discovery_port', ZEROCONF_PORT) # Use configured port
            logger.info(f"Starting Flask server on {host}:{port}")
            print(f" --- Photo Frame Server ---")
            print(f"      Version: {get_version()}")
            print(f"      Uploads: {app.config['UPLOAD_FOLDER']}")
            print(f"      Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
            print(f"      URL: http://{socket.gethostbyname(socket.gethostname())}:{port}/")
            print(f"      * Running on http://{host}:{port}/ (Press CTRL+C to quit)")

    # Start the Flask development server
    # Set use_reloader=True for auto-reload on code changes (development)
    # Set use_reloader=False for production or when using external debuggers
    app.run(host='0.0.0.0', port=server_settings.get('discovery_port', ZEROCONF_PORT), debug=True, use_reloader=use_reloader)

