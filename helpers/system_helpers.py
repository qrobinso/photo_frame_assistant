import os
import time
import socket
import platform
import logging
import psutil
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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


def get_system_info(upload_folder):
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
        photos_path = upload_folder
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
    _basedir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_file = os.path.join(_basedir, 'version.txt')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return 'unknown (version.txt not found)'
    except Exception as e:
        logger.warning(f"Could not read version file: {e}")
        return 'unknown (read error)'


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
