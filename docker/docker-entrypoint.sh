#!/bin/bash
set -e

echo "Starting Photo Server initialization..."

# Create necessary directories using environment variables (with defaults)
# For Docker, these should point to /app/data/* which is the mounted volume
UPLOAD_DIR="${UPLOAD_PATH:-/app/data/uploads}"
LOG_DIR="${LOG_PATH:-/app/data/logs}"
CONFIG_DIR="${CONFIG_PATH:-/app/config}"
DB_FILE="${DB_PATH:-/app/data/app.db}"
DB_DIR=$(dirname "$DB_FILE")

# Export these so db_manager.py and server.py pick them up
export UPLOAD_PATH="$UPLOAD_DIR"
export LOG_PATH="$LOG_DIR"
export CONFIG_PATH="$CONFIG_DIR"
export DB_PATH="$DB_FILE"

echo "=== Data Paths ==="
echo "  Upload path: $UPLOAD_DIR"
echo "  Log path: $LOG_DIR"
echo "  Config path: $CONFIG_DIR"
echo "  Database file: $DB_FILE"
echo "  Database directory: $DB_DIR"

# Check if the data volume is mounted (look for .volume_marker or existing data)
if [ -d "/app/data" ]; then
    echo "=== Volume Check ==="
    echo "  /app/data exists"
    
    # Create a marker file to verify volume persistence
    if [ ! -f "/app/data/.volume_marker" ]; then
        echo "  Creating volume marker (first run with this volume)"
        date > /app/data/.volume_marker
    else
        echo "  Volume marker found - volume is persisting correctly"
        echo "  Volume created: $(cat /app/data/.volume_marker)"
    fi
    
    # Show existing data
    if [ -f "$DB_FILE" ]; then
        echo "  Existing database found: $DB_FILE ($(stat -c%s "$DB_FILE" 2>/dev/null || echo "unknown") bytes)"
    else
        echo "  No existing database at $DB_FILE"
    fi
    
    if [ -d "$UPLOAD_DIR" ]; then
        PHOTO_COUNT=$(find "$UPLOAD_DIR" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" -o -name "*.gif" \) 2>/dev/null | wc -l)
        echo "  Existing photos in uploads: $PHOTO_COUNT"
    else
        echo "  No uploads directory yet"
    fi
fi

echo "=== Creating directories ==="
mkdir -p "$UPLOAD_DIR/thumbnails" "$LOG_DIR" "$CONFIG_DIR/credentials" "$DB_DIR/db_backups"

# Create symlinks from legacy paths to volume paths for backward compatibility
# This ensures ANY code using old paths will still write to the persistent volume
echo "=== Setting up legacy path symlinks ==="
# Remove old directories if they exist (but not if they're already symlinks)
for legacy_path in /app/uploads /app/logs /app/db_backups; do
    if [ -d "$legacy_path" ] && [ ! -L "$legacy_path" ]; then
        # Check if directory has files - if so, move them to volume first
        if [ "$(ls -A $legacy_path 2>/dev/null)" ]; then
            echo "  Moving existing files from $legacy_path to volume..."
            cp -rn "$legacy_path"/* "$UPLOAD_DIR/" 2>/dev/null || true
        fi
        rm -rf "$legacy_path"
    fi
done

# Create symlinks to volume paths
ln -sfn "$UPLOAD_DIR" /app/uploads 2>/dev/null || true
ln -sfn "$LOG_DIR" /app/logs 2>/dev/null || true
ln -sfn "$DB_DIR/db_backups" /app/db_backups 2>/dev/null || true

echo "  /app/uploads -> $UPLOAD_DIR"
echo "  /app/logs -> $LOG_DIR"
echo "  /app/db_backups -> $DB_DIR/db_backups"

# Config and credentials stay as regular directories (bind mount for config)
mkdir -p /app/credentials /app/config

# Initialize the database (only creates if doesn't exist)
echo "=== Database Check ==="
echo "  Checking for database at: $DB_FILE"
echo "  DB_PATH env var is: $DB_PATH"
if [ -f "$DB_FILE" ]; then
    DB_SIZE=$(stat -c%s "$DB_FILE" 2>/dev/null || echo "unknown")
    echo "  SUCCESS: Existing database found ($DB_SIZE bytes) - will NOT recreate"
    echo "  Running migration check only..."
    python db_manager.py --migrate || echo "  Migration check completed (or no changes needed)"
else
    echo "  No database found at $DB_FILE - creating new database..."
    python db_manager.py
fi

# Check exit status
if [ $? -ne 0 ]; then
    echo "ERROR: Database initialization failed!"
    exit 1
fi

# Create placeholder config files if they don't exist
echo "Creating placeholder configuration files..."

# Server settings
if [ ! -f config/server_settings.json ]; then
    echo '{
    "server_name": "photo-frame-assistant",
    "timezone": "UTC",
    "cleanup_interval": 24,
    "log_level": "INFO",
    "max_upload_size": 10,
    "discovery_port": 5000,
    "ai_analysis_enabled": false,
    "dark_mode": false
}' > config/server_settings.json
fi

# Immich config
if [ ! -f config/immich_config.json ]; then
    echo '{
    "url": "",
    "api_key": "",
    "auto_import": []
}' > config/immich_config.json
fi

# Metadata config
if [ ! -f config/metadata_config.json ]; then
    echo '{
    "fields": {},
    "background": {
        "enabled": false,
        "color": "#000000",
        "opacity": "50"
    },
    "stack_spacing": "5%",
    "max_text_width": "80%",
    "global_padding": 0
}' > config/metadata_config.json
fi

# MQTT config
if [ ! -f config/mqtt_config.json ]; then
    echo '{
    "enabled": false,
    "broker": "",
    "port": 1883,
    "username": "",
    "password": ""
}' > config/mqtt_config.json
fi

# Network locations
if [ ! -f config/network_locations.json ]; then
    echo '{
    "locations": []
}' > config/network_locations.json
fi

# Photogen settings
if [ ! -f config/photogen_settings.json ]; then
    echo '{
    "dalle_api_key": "",
    "stability_api_key": "",
    "custom_server_api_key": "",
    "dalle_base_url": "",
    "stability_base_url": "",
    "custom_server_base_url": "",
    "default_service": "stability",
    "default_models": {
        "dalle": "dall-e-3",
        "stability": "ultra",
        "custom": ""
    },
    "interval": 0,
    "rotation": "normal",
    "flip": "normal"
}' > config/photogen_settings.json
fi

# Pixabay config
if [ ! -f config/pixabay_config.json ]; then
    echo '{
    "api_key": ""
}' > config/pixabay_config.json
fi

# QR Code config
if [ ! -f config/qrcode_config.json ]; then
    echo '{
    "enabled": false,
    "port": 5000,
    "custom_url": null,
    "size": "medium",
    "position": "bottom-right",
    "link_type": "frame_playlist",
    "bg_opacity": 90,
    "exact_position": {
        "x": 0.2536,
        "y": 0.001
    }
}' > config/qrcode_config.json
fi

# Spotify config
if [ ! -f config/spotify_config.json ]; then
    echo '{
    "client_id": "",
    "client_secret": "",
    "access_token": "",
    "refresh_token": "",
    "token_expiry": "",
    "enabled": false,
    "auto_refresh": true,
    "refresh_interval": 30,
    "frame_mappings": []
}' > config/spotify_config.json
fi

# Unsplash config
if [ ! -f config/unsplash_config.json ]; then
    echo '{
    "api_key": ""
}' > config/unsplash_config.json
fi

# Weather config
if [ ! -f config/weather_config.json ]; then
    echo '{
    "enabled": false,
    "zipcode": "",
    "api_key": "",
    "units": "F",
    "update_interval": 1,
    "style": {
        "position": "top-center",
        "font_family": "Breathingy.ttf",
        "font_size": "2%",
        "margin": "5%",
        "color": "#ffffff",
        "background": {
            "enabled": false,
            "color": "#ffffff",
            "opacity": 30
        },
        "format": "Feels Like {feels_like} \u00b0   {description}"
    }
}' > config/weather_config.json
fi

# Make sure permissions are correct
chmod -R 777 "$UPLOAD_DIR" "$LOG_DIR" "$CONFIG_DIR" "$DB_DIR/db_backups" 2>/dev/null || true
chmod -R 777 /app/credentials /app/config 2>/dev/null || true

# Final verification - show what's actually in the data directory
echo "=== Volume Contents Verification ==="
echo "  Database: $(ls -la $DB_FILE 2>/dev/null || echo 'NOT FOUND')"
echo "  Upload files: $(find $UPLOAD_DIR -maxdepth 1 -type f 2>/dev/null | wc -l) files"
echo "  Thumbnails: $(find $UPLOAD_DIR/thumbnails -type f 2>/dev/null | wc -l) files"
echo "  Symlink check:"
ls -la /app/uploads 2>/dev/null | head -1 || echo "    /app/uploads not found"
ls -la /app/logs 2>/dev/null | head -1 || echo "    /app/logs not found"

echo "Initialization complete. Starting Photo Server..."
exec python server.py 