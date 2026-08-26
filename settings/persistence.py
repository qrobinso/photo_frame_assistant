import os
import json
import socket
import logging

logger = logging.getLogger(__name__)

# Config file paths (derived same way as server.py)
_basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))  # parent of settings/
CONFIG_DIR = os.environ.get('CONFIG_PATH', os.path.join(_basedir, 'config'))
SERVER_SETTINGS_FILE = os.path.join(CONFIG_DIR, 'server_settings.json')
MQTT_CONFIG_PATH = os.path.join(CONFIG_DIR, 'mqtt_config.json')
ZEROCONF_PORT = 5000


def load_server_settings():
    """Load server settings from file."""
    default_settings = {
        'server_name': socket.gethostname(),
        'timezone': 'UTC',
        'cleanup_interval': 24,  # hours
        'log_level': 'INFO',
        'max_upload_size': 10,  # MB
        'discovery_port': ZEROCONF_PORT,
        'discovery_enabled': True,
        # Optional override: the exact IPs to advertise over mDNS. Leave empty
        # to autodetect. Set it when the host's addressing cannot be inferred
        # (unusual bridges, multiple LANs) and frames get an unreachable IP.
        'advertise_ips': [],
        'dark_mode': False
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
        os.makedirs(os.path.dirname(SERVER_SETTINGS_FILE), exist_ok=True)
        with open(SERVER_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Server settings saved to {SERVER_SETTINGS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving server settings to {SERVER_SETTINGS_FILE}: {e}")
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
