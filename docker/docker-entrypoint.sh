#!/bin/bash
set -e

echo "Starting Photo Server initialization..."

# Create necessary directories using environment variables (with defaults)
UPLOAD_DIR="${UPLOAD_PATH:-/app/uploads}"
LOG_DIR="${LOG_PATH:-/app/logs}"
CONFIG_DIR="${CONFIG_PATH:-/app/config}"
DB_FILE="${DB_PATH:-/app/app.db}"
DB_DIR=$(dirname "$DB_FILE")

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

# Also create legacy paths for backward compatibility with any hardcoded references
mkdir -p /app/uploads /app/logs /app/credentials /app/db_backups /app/config

# Initialize the database (only creates if doesn't exist)
echo "=== Database Check ==="
if [ -f "$DB_FILE" ]; then
    echo "  Existing database found - will NOT recreate"
    echo "  Running migration check only..."
    python db_manager.py --migrate || echo "  Migration check completed (or no changes needed)"
else
    echo "  No database found - creating new database..."
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
chmod -R 777 /app/uploads /app/logs /app/credentials /app/db_backups /app/config 2>/dev/null || true

echo "Initialization complete. Starting Photo Server..."
exec python server.py 