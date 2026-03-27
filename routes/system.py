"""
System Blueprint — /, /info, and /api/server/* routes.
"""
import json
import logging
import os
import platform
import socket
import secrets
from datetime import datetime, timedelta

import psutil
import pytz
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from helpers.system_helpers import get_system_info, get_version
from settings.persistence import load_server_settings, save_server_settings

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__)

# ---------------------------------------------------------------------------
# Internal helpers (private to this module)
# ---------------------------------------------------------------------------

def _get_cpu_temperature():
    """Get CPU temperature."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = float(f.read()) / 1000.0
        return round(temp, 1)
    except Exception:
        return 0


def _get_uptime():
    """Get system uptime."""
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    return str(timedelta(seconds=int(uptime_seconds)))


def _get_storage_info():
    """Get storage information."""
    disk = psutil.disk_usage('/')

    def _fmt(size):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    return {
        'total':   _fmt(disk.total),
        'used':    _fmt(disk.used),
        'free':    _fmt(disk.free),
        'percent': disk.percent,
    }


def _get_photo_stats():
    """Get photo statistics."""
    from model import Photo

    def _fmt(size):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    upload_folder = current_app.config['UPLOAD_FOLDER']
    photos = Photo.query.all()
    total_size = 0
    for photo in photos:
        try:
            if photo.filename:
                path = os.path.join(upload_folder, photo.filename)
                if os.path.exists(path):
                    total_size += os.path.getsize(path)
        except Exception:
            pass
    return {
        'total':      len(photos),
        'total_size': _fmt(total_size),
        'avg_size':   _fmt(total_size / len(photos)) if photos else '0 B',
    }


def _get_ip_address():
    """Get the server's IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'Unknown'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@system_bp.route('/')
def index():
    """Redirect to frames page."""
    return redirect(url_for('frames.manage_frames'))


@system_bp.route('/info')
def info():
    """Display system information and frame details."""
    from model import PhotoFrame

    ZEROCONF_PORT = 5000

    frames = PhotoFrame.query.all()
    now = datetime.now()

    system_info = {
        'cpu_temp':      _get_cpu_temperature(),
        'cpu_usage':     psutil.cpu_percent(),
        'memory_usage':  psutil.virtual_memory().percent,
        'uptime':        _get_uptime(),
        'python_version': platform.python_version(),
    }

    storage = _get_storage_info()

    network = {
        'ip':               _get_ip_address(),
        'hostname':         socket.gethostname(),
        'connection_type':  'Ethernet/WiFi',
    }

    photos = _get_photo_stats()

    app = current_app._get_current_object()
    if not hasattr(app, 'server_id'):
        app.server_id = secrets.token_hex(8)

    discovery_info = {
        'service_name': f"PhotoFrame Server ({network['hostname']})",
        'service_type': '_photoframe._tcp.local.',
        'port':         ZEROCONF_PORT,
        'properties': {
            'server_id': app.server_id,
            'version':   '1.0.0',
            'frames':    len(frames),
            'photos':    photos.get('total', 0),
            'hostname':  network['hostname'],
            'ip':        network['ip'],
        },
    }

    server_settings = load_server_settings()

    return render_template(
        'info.html',
        frames=frames,
        system=system_info,
        storage=storage,
        photos=photos,
        network=network,
        version=get_version(),
        now=now,
        discovery=discovery_info,
        server_id=app.server_id,
        server_name=server_settings['server_name'],
        current_timezone=server_settings['timezone'],
        timezones=pytz.all_timezones,
        cleanup_interval=server_settings['cleanup_interval'],
        log_level=server_settings['log_level'],
        max_upload_size=server_settings['max_upload_size'],
        discovery_port=server_settings['discovery_port'],
        dark_mode=server_settings.get('dark_mode', False),
        discovery_enabled=server_settings.get('discovery_enabled', True),
    )


@system_bp.route('/api/server/settings', methods=['POST'])
def update_server_settings():
    """Update server settings."""
    try:
        data = request.get_json()
        current_settings = load_server_settings()

        if 'server_name' in data:
            current_settings['server_name'] = data['server_name']
        if 'timezone' in data and data['timezone'] in pytz.all_timezones:
            current_settings['timezone'] = data['timezone']
        if 'cleanup_interval' in data and isinstance(data['cleanup_interval'], int):
            current_settings['cleanup_interval'] = data['cleanup_interval']
        if 'log_level' in data and data['log_level'] in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            current_settings['log_level'] = data['log_level']
            logging.getLogger().setLevel(data['log_level'])
        if 'max_upload_size' in data and isinstance(data['max_upload_size'], int):
            current_settings['max_upload_size'] = data['max_upload_size']
        if 'discovery_port' in data and 1024 <= data['discovery_port'] <= 65535:
            current_settings['discovery_port'] = data['discovery_port']
        if 'discovery_enabled' in data:
            current_settings['discovery_enabled'] = bool(data['discovery_enabled'])
        if 'dark_mode' in data:
            current_settings['dark_mode'] = bool(data['dark_mode'])

        if save_server_settings(current_settings):
            current_app.config['MAX_CONTENT_LENGTH'] = current_settings['max_upload_size'] * 1024 * 1024

            if 'discovery_enabled' in data:
                frame_discovery = current_app.frame_discovery
                if current_settings['discovery_enabled']:
                    if not frame_discovery._running:
                        frame_discovery.start()
                        logger.info("Frame discovery enabled via settings.")
                else:
                    if frame_discovery._running:
                        frame_discovery.stop()
                        logger.info("Frame discovery disabled via settings.")

            return jsonify({'success': True, 'settings': current_settings})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500

    except Exception as e:
        logger.error(f"Error updating server settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


