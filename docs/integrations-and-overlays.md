# Integrations and Overlays

This document explains the integration systems and overlay capabilities of the Photo Frame Assistant.

## Overview

The Photo Frame Assistant supports various integrations with external services and provides overlay capabilities to enhance the display of photos on frames. These features allow for dynamic content, real-time information, and customized displays.

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐    │
│  │             │   │             │   │             │    │
│  │  External   │   │   Server    │   │   Photo     │    │
│  │  Services   │◄──┤ Integrations│◄──┤   Frame     │    │
│  │             │   │             │   │             │    │
│  └─────────────┘   └─────────────┘   └─────────────┘    │
│                                                         │
│                    Photo Frame Assistant                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Integrations

### Supported Integration Types

The Photo Frame Assistant supports the following types of integrations:

1. **Content Sources**
   - Google Photos
   - Unsplash
   - Pixabay
   - AI Image Generation (DALL-E, Stability AI)

2. **IoT and Home Automation**
   - MQTT
   - Home Assistant (via MQTT)

3. **Information Services**
   - Weather APIs
   - News Services
   - Calendar Services

4. **AI Services**
   - OpenAI (for image generation and analysis)
   - Stability AI (for image generation)
   - Local AI (for image analysis)

5. **Network Shares**
   - SMB/CIFS network shares
   - Automatic media import

### Integration Architecture

Each integration is implemented as a modular component with a consistent interface:

```
┌─────────────────┐
│                 │
│  Base           │
│  Integration    │
│  Interface      │
│                 │
└───────┬─────────┘
        │
        ├─────────────┬─────────────┬─────────────┐
        │             │             │             │
┌───────▼─────┐ ┌─────▼───────┐ ┌───▼─────────┐ ┌─▼────────────┐
│             │ │             │ │             │ │              │
│  MQTT       │ │  Google     │ │  Weather    │ │  AI          │
│  Integration│ │  Photos     │ │  Integration│ │  Integration │
│             │ │  Integration│ │             │ │              │
└─────────────┘ └─────────────┘ └─────────────┘ └──────────────┘
```

### Integration Configuration

Integrations are configured through the web interface:

1. Navigate to the "Integrations" page
2. Select the integration to configure
3. Enter required credentials and settings
4. Test the connection
5. Save the configuration

## Network Shares Integration

The Network Shares integration allows the Photo Frame Assistant to connect to SMB/CIFS network shares and import photos from them.

### Configuration

```json
{
  "locations": [
    {
      "id": "location1",
      "name": "Family Photos",
      "network_path": "\\\\server\\photos",
      "username": "user",
      "password": "password",
      "autoAddNewMedia": true,
      "autoAddTargetFrameId": "frame123"
    }
  ]
}
```

### Features

- **Browse Network Shares**: Browse available network shares and directories
- **Import Photos**: Import selected photos to the Photo Frame Assistant
- **Automatic Import**: Automatically import new photos from configured network locations
- **Frame Assignment**: Assign imported photos directly to specific frames

### Automatic Import

The Network Shares integration includes an automatic import feature that periodically checks configured network locations for new media files:

1. **Scheduler**: A background job runs every 60 minutes to check for new media
2. **Configuration**: Each network location can be configured with:
   - `autoAddNewMedia`: Enable/disable automatic import
   - `autoAddTargetFrameId`: The frame to which new media should be added
3. **File Tracking**: The system maintains a record of previously imported files to avoid duplicates
4. **Import Process**: New media files are automatically imported and added to the specified frame's playlist

#### How Automatic Import Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│  Scheduler      │────►│  Check Network  │────►│  Find New       │
│  (60 min)       │     │  Locations      │     │  Media Files    │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └───────┬─────────┘
                                                        │
                                                        ▼
┌─────────────────┐     ┌─────────────────┐     ┌───────────────────┐
│                 │     │                 │     │                   │
│  Update         │◄────┤  Add to Frame   │◄────┤  Import Each      │
│  Imported List  │     │  Playlist       │     │  New File         │
│                 │     │                 │     │                   │
└─────────────────┘     └─────────────────┘     └───────────────────┘
```

### Network Share Discovery

The system can automatically discover available network shares:

1. **SMB Server Discovery**: Find SMB servers on the local network
2. **Share Enumeration**: List available shares on discovered servers
3. **Connection Testing**: Test connection to shares with provided credentials

## MQTT Integration

The MQTT integration allows the Photo Frame Assistant to communicate with MQTT brokers, enabling IoT and home automation integration.

### Configuration

```json
{
  "enabled": true,
  "broker": "mqtt.example.com",
  "port": 1883,
  "username": "user",
  "password": "password",
  "client_id": "photo-frame-assistant",
  "topics": {
    "frame_status": "photoframe/status/{frame_id}",
    "frame_command": "photoframe/command/{frame_id}",
    "server_status": "photoframe/server/status"
  }
}
```

### Published Topics

The server publishes to the following topics:

- **Frame Status**: `photoframe/status/{frame_id}`
  ```json
  {
    "frame_id": "frame123",
    "status": "active",
    "battery_level": 85.5,
    "current_photo": "sunset.jpg",
    "last_update": "2023-10-15T08:30:00Z"
  }
  ```

- **Server Status**: `photoframe/server/status`
  ```json
  {
    "status": "online",
    "frames_count": 3,
    "photos_count": 256,
    "uptime": 86400
  }
  ```

### Subscribed Topics

The server subscribes to the following topics:

- **Frame Command**: `photoframe/command/{frame_id}`
  ```json
  {
    "command": "update",
    "params": {
      "photo_id": 123
    }
  }
  ```

  Supported commands:
  - `update`: Force a frame to update its display
  - `sleep`: Put a frame to sleep
  - `wake`: Wake up a frame
  - `set_photo`: Display a specific photo
  - `set_setting`: Change a frame setting

## Google Photos Integration

The Google Photos integration allows importing photos from Google Photos libraries.

### Authentication Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Request    │────►│  Google     │────►│  Redirect   │
│  Auth URL   │     │  Auth Page  │     │  to Server  │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                                ▼
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│             │     │             │     │              │
│  Store      │◄────┤  Exchange   │◄────┤  Receive     │
│  Tokens     │     │  Code       │     │  Auth Code   │
│             │     │             │     │              │
└─────────────┘     └─────────────┘     └──────────────┘
```

### Features

- **Album Browsing**: Browse and select albums from Google Photos
- **Photo Search**: Search photos by description, location, or date
- **Selective Import**: Import selected photos to the Photo Frame Assistant
- **Metadata Preservation**: Preserve photo metadata during import

## Weather Integration

The Weather Integration provides current weather data for display on photo frames.

### Supported Weather Providers

- **OpenWeatherMap**: Free and paid API options
- **WeatherAPI.com**: Comprehensive weather data
- **Local Weather Station**: For users with local weather stations

### Configuration

```json
{
  "provider": "openweathermap",
  "api_key": "your_api_key",
  "location": {
    "city": "New York",
    "country": "US",
    "lat": 40.7128,
    "lon": -74.0060
  },
  "units": "metric",
  "update_interval": 3600,
  "display_options": {
    "show_temperature": true,
    "show_condition": true,
    "show_humidity": true,
    "show_wind": false,
    "show_forecast": false
  }
}
```

### Weather Data Format

```json
{
  "location": {
    "name": "New York",
    "country": "US",
    "lat": 40.7128,
    "lon": -74.0060
  },
  "current": {
    "temperature": 22,
    "feels_like": 23,
    "humidity": 65,
    "condition": "Clear",
    "condition_code": 800,
    "wind_speed": 5.1,
    "wind_direction": "NE",
    "pressure": 1012,
    "uv_index": 5,
    "visibility": 10,
    "icon": "01d"
  },
  "forecast": [
    {
      "date": "2023-10-16",
      "temp_min": 18,
      "temp_max": 25,
      "condition": "Partly cloudy",
      "condition_code": 802,
      "icon": "02d"
    },
    {
      "date": "2023-10-17",
      "temp_min": 17,
      "temp_max": 24,
      "condition": "Rain",
      "condition_code": 500,
      "icon": "10d"
    }
  ],
  "last_updated": "2023-10-15T08:30:00Z"
}
```

## AI Integration

The AI Integration connects to AI services for image generation and analysis.

### Supported AI Services

- **OpenAI DALL-E**: For image generation
- **Stability AI**: For image generation
- **Local AI**: For image analysis and description

### Image Generation

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  User       │────►│  Server     │────►│  AI Service │
│  Prompt     │     │  Processing │     │  API        │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                                ▼
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│             │     │             │     │              │
│  Display    │◄────┤  Save to    │◄────┤  Generated   │
│  Image      │     │  Library    │     │  Image       │
│             │     │             │     │              │
└─────────────┘     └─────────────┘     └──────────────┘
```

### Image Analysis

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Photo      │────►│  Extract    │────►│  AI Service │
│  Upload     │     │  Features   │     │  API        │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                                                ▼
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│             │     │             │     │              │
│  Update     │◄────┤  Process    │◄────┤  AI          │
│  Metadata   │     │  Response   │     │  Description │
│             │     │             │     │              │
└─────────────┘     └─────────────┘     └──────────────┘
```

### Configuration

```json
{
  "openai": {
    "api_key": "your_openai_api_key",
    "model": "dall-e-3",
    "image_size": "1024x1024",
    "quality": "standard"
  },
  "stability": {
    "api_key": "your_stability_api_key",
    "model": "stable-diffusion-xl-1024-v1-0",
    "steps": 30,
    "cfg_scale": 7.5
  },
  "local_ai": {
    "enabled": false,
    "url": "http://localhost:8080",
    "model": "clip-vit-base-patch32"
  }
}
```

## Overlay System

The overlay system allows displaying additional information on top of photos shown on frames.

### Overlay Architecture

```
┌─────────────────┐
│                 │
│  Base           │
│  Overlay        │
│  Interface      │
│                 │
└───────┬─────────┘
        │
        ├─────────────┬─────────────┬─────────────┐
        │             │             │             │
┌───────▼─────┐ ┌─────▼───────┐ ┌───▼─────────┐ ┌─▼────────────┐
│             │ │             │ │             │ │              │
│  Weather    │ │  Metadata   │ │  Clock      │ │  QR Code     │
│  Overlay    │ │  Overlay    │ │  Overlay    │ │  Overlay     │
│             │ │             │ │             │ │              │
└─────────────┘ └─────────────┘ └─────────────┘ └──────────────┘
```

### Overlay Configuration

Overlays are configured per frame through the frame settings:

```json
{
  "overlay_preferences": {
    "weather": true,
    "metadata": true,
    "clock": false,
    "qrcode": false
  },
  "overlay_styles": {
    "weather": {
      "position": "bottom",
      "font_size": 36,
      "background_opacity": 0.5
    },
    "metadata": {
      "position": "top",
      "font_size": 24,
      "background_opacity": 0.5
    }
  }
}
```

## Customizing Overlays

Users can customize overlays through the web interface:

1. Navigate to the "Overlays" page
2. Select an overlay type to customize
3. Configure appearance settings (position, size, colors, etc.)
4. Preview the overlay on sample photos
5. Save the configuration

## References

- [OpenWeatherMap API](https://openweathermap.org/api)
- [Google Photos API](https://developers.google.com/photos)
- [MQTT Protocol](https://mqtt.org/)
- [OpenAI API](https://platform.openai.com/docs/api-reference)
- [Stability AI API](https://stability.ai/api)
- [QR Code Python Library](https://pypi.org/project/qrcode/) 