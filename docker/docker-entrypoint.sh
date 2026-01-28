#!/bin/bash
set -e

echo "Starting Photo Server initialization..."

# Create necessary directories
mkdir -p uploads logs credentials db_backups config

# Initialize the database
echo "Checking database..."
python db_manager.py

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
chmod -R 777 uploads logs credentials db_backups config

echo "Initialization complete. Starting Photo Server..."
exec python server.py 