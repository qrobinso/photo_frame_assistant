# Photo Frame Assistant

A powerful and feature-rich server application for managing digital photo frames, photo collections, and automated image generation.

![Photo Frame Assistant](https://via.placeholder.com/800x400?text=Photo+Frame+Assistant)

A powerful self-hosted platform for managing digital photo frames, collections, and AI-generated imagery—all with privacy at its core. Easily deploy on a Raspi or your local server.

## Overview
Photo Frame Assistant (PFA) is the digital photo frame solution that puts you in control. It transforms how you display cherished memories throughout your home by seamlessly orchestrating multiple frames—from energy-efficient e-ink displays to vibrant screens with video support—all while keeping your photos private and secure on your own network.

PFA bridges the convenience gap between proprietary cloud solutions and DIY setups, providing automated content management, intelligent frame coordination, and deep smart home integration without compromising on privacy. Connect with your existing photo libraries, generate custom AI imagery, or automatically sync from network folders—then let PFA handle the rest.

## Why Photo Frame Assistant?
Today's digital frame market forces unnecessary compromises:

Privacy concerns: Commercial products often lock your memories behind proprietary clouds
Limited control: Most frames offer minimal customization and lack advanced scheduling
Disconnected experiences: Frames operate in isolation rather than as coordinated displays
Management headaches: Adding new content becomes a tedious, manual process
Smart home isolation: Frames rarely integrate with your existing home automation

PFA delivers the complete solution by combining:
Self-hosted privacy: Keep your photos on your own server, not someone else's cloud
Unified management: Control all your frames from a single dashboard
Smart synchronization: Coordinate frames into groups for consistent viewing experiences
Effortless content updates: Automatically import from network folders, cloud services, or AI generation
Deep integration: Connect with Home Assistant via MQTT for true smart home experiences

```
┌───────────────────────────────────────────────────────────────────────────┐
│                        PHOTO FRAME ASSISTANT                              │
│                                                                           │
│  ┌─────────────────┐                              ┌─────────────────────┐ │
│  │  PHOTO SOURCES  │                              │   PHOTO FRAMES      │ │
│  │                 │                              │                     │ │
│  │  ┌───────────┐  │                              │   ┌─────────────┐   │ │
│  │  │ Manual    │  │                              │   │ E-Ink       │   │ │
│  │  │ Upload    │──┤                              │   │ Frames      │   │ │
│  │  └───────────┘  │                              │   └─────────────┘   │ │
│  │                 │                              │                     │ │
│  │  ┌───────────┐  │    ┌───────────────────┐    │   ┌─────────────┐    │ │
│  │  │ Google    │  │    │                   │    │   │ OLED/LCD    │    │ │
│  │  │ Photos    │──┼────┤                   │────┼───│ Frames      │    │ │
│  │  └───────────┘  │    │                   │    │   └─────────────┘    │ │
│  │                 │    │  • Photo Library  │    │                      │ │
│  │  ┌───────────┐  │    │  • Frame Mgmt     │    │   ┌─────────────┐    │ │
│  │  │ Immich    │  │    │  • Sleep System   │    │   │ Smart TVs   │    │ │
│  │  │           │──┼────┤  • Sync Groups    │────┼───│ (Frame Mode)│    │ │
│  │  └───────────┘  │    │  • Overlays       │    │   └─────────────┘    │ │
│  │                 │    │  • AI Analysis    │    │                      │ │
│  │  ┌───────────┐  │    │  • Home Assistant │    │   ┌─────────────┐    │ │
│  │  │ Unsplash/ │  │    │                   │    │   │ Battery     │    │ │
│  │  │ Pixabay   │──┼────┤                   │────┼───│ Operated    │    │ │
│  │  └───────────┘  │    └───────────────────┘    │   └─────────────┘    │ │
│  │                 │                              │                     │ │
│  │  ┌───────────┐  │                              │   ┌─────────────┐   │ │
│  │  │ AI Image  │  │                              │   │ Cellular    │   │ │
│  │  │ Generation│──┤                              │   │ Connected   │   │ │
│  │  └───────────┘  │                              │   └─────────────┘   │ │
│  │                 │                              │                     │ │
│  │  ┌───────────┐  │                              │                     │ │
│  │  │ Network   │  │                              │                     │ │
│  │  │ Shares    │──┤                              │                     │ │
│  │  └───────────┘  │                              │                     │ │
│  │                 │                              │                     │ │
│  └─────────────────┘                              └─────────────────────┘ │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Features

### Photo Frame Management
- **Multi-Frame Support**: Manage multiple photo frames with different settings and playlists
- **Frame Discovery**: Automatically discover compatible photo frames on your network
- **Sync Groups**: Group frames together to display the same content simultaneously
- **Sleep Scheduling**: Configure deep sleep periods to save power
- **Remote Control**: Update frame content and settings remotely

### Photo Management
- **Upload Interface**: Easy-to-use web interface for uploading photos
- **Video Support**: Upload and display video content on compatible frames
- **Metadata Extraction**: Automatically extract and display EXIF metadata

### Custom Playlists
- **Playlist Templates**: Create reusable playlist templates that can be applied to multiple frames
- **Playlist Scheduling**: Schedule automatic playlist changes using cron expressions

### Content Sources
- **Immich Integration**: Connect to your self-hosted Immich photo library
- **Network Shares Integration**: Import photos directly from SMB/CIFS network shares with automatic import
- **Google Photos Integration**: Connect to your Google Photos library
- **AI Image Generation**: Generate custom images using Stability AI

### Integrations
- **MQTT Support**: Connect to MQTT brokers for IoT integration
- **Weather Services**: Integrate with weather APIs for local conditions
- **AI Services**: Connect to OpenAI and Stability AI for image generation

### Automation
- **Scheduled Generation**: Set up recurring image generation based on prompts
- **Content Rotation**: Automatically rotate content based on schedules
- **Weather Integration**: Display current weather conditions on frames
- **Automatic Import**: Configure network shares to automatically import new photos hourly

## Documentation

Detailed documentation is available in the [docs](docs/) directory:

### Core Concepts
- [Photo Workflow](docs/photo-workflow.md) - Complete lifecycle of photos from upload to display
- [Client-Server Communication](docs/client-communication.md) - How frames communicate with the server
- [Frame Discovery](docs/frame-discovery.md) - How frames are discovered on the network

### Integrations
- [Integrations and Overlays](docs/integrations-and-overlays.md) - External service integrations and display overlays
- [Local AI Integration](docs/local-ai-integration.md) - Using local AI servers for photo analysis and generation

### Deployment
- [Docker Setup](README.docker.md) - Running the Photo Frame Assistant in Docker

## Quick Start Installation

1. Download the docker-compose.yml file:
   ```bash
   # Create a directory for Photo Frame Assistant
   mkdir photo-frame-assistant && cd photo-frame-assistant
   
   # Download the docker-compose file
   curl -O https://raw.githubusercontent.com/qrobinso/photo_frame_assistant_server/main/docker/docker-compose.yml
   ```

2. Start the application:
   ```bash
   # Start Photo Frame Assistant
   docker compose up -d
   ```

3. Access the web interface:
   - Open your web browser
   - Visit `http://localhost:5000` (or use your device's IP address)

That's it! The application will automatically:
- Download the latest version
- Create all necessary directories
- Set up the database
- Start the server

### Updating

To update to the latest version:
```bash
docker compose pull
docker compose up -d
```

### Where is my data stored?

All data is automatically stored in a Docker volume named `photo_data` with this structure:
- `/app/data/uploads`: Your photos and videos
- `/app/data/config`: Configuration files
- `/app/data/logs`: Application logs

To backup your data, simply backup the Docker volume.

## Usage

### Managing Frames

- **Frame Status**: View the status of all frames including battery level, current photo, and next wake time
- **Frame Settings**: Configure sleep intervals, display orientation, and image processing settings
- **Playlist Management**: Create and edit playlists for each frame

### Photo Operations

- **Upload**: Upload photos from your device
- **Import**: Import photos from Google Photos, Unsplash, or Pixabay
- **Generate**: Create new images using AI services
- **Edit**: Adjust photo metadata and settings

### Automation

- **Scheduled Generation**: Set up recurring tasks to generate new images
- **Dynamic Playlists**: Create AI-powered playlists that update automatically
- **Sync Groups**: Manage groups of frames that display the same content

## Advanced Features

### AI Image Generation

1. Navigate to the "Generate" page
2. Enter a prompt describing the image you want
3. Select the service (DALL-E or Stability AI)
4. Choose orientation and style options
5. Generate and preview the image
6. Save to your library and/or add to frames

### Weather Overlays

1. Go to a frame's settings
2. Enable weather overlay
3. Configure your location and display preferences
4. The frame will display current weather conditions

### Dynamic Playlists

1. Select a frame and go to its settings
2. Enable "Dynamic Playlist"
3. Enter a theme or description
4. The system will automatically curate photos matching the theme

### Network Shares Integration

1. Navigate to the "Integrations" page
2. Find the "Network Integration" section
3. Add a new network location with the path to your shared folder (e.g., \\server\photos)
4. Optionally provide username and password if required
5. Test the connection to ensure access
6. Browse the network share and select photos to import
7. Assign imported photos to frames directly during import
8. Optionally enable automatic import by checking "Auto-add new media" and selecting a target frame
9. New photos added to the network share will be automatically imported every hour

## API Reference

The Photo Frame Assistant provides a comprehensive API for integration with other systems:

- `/api/frames` - Manage frames
- `/api/photos` - Manage photos
- `/api/generate` - Generate images
- `/api/unsplash` - Interact with Unsplash
- `/api/pixabay` - Interact with Pixabay
- `/api/google-photos` - Interact with Google Photos
- `/api/network` - Manage network shares and import photos
- `/api/weather` - Access weather data
- `/api/server` - Server management

For detailed API documentation, see the [API Reference](API.md).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Frequently Asked Questions (FAQ)

### General Questions

#### How can I build my own photo frame for Photo Frame Assistant?
You can build your own photo frame using various hardware options:
1. **Raspberry Pi + Display**: Use a Raspberry Pi (3B+, 4, or Zero 2W) with an e-ink, LCD, or OLED display.
2. **Repurposed Tablet**: Convert an old tablet into a dedicated photo frame by installing our client app.
3. **ESP32 + Display**: For battery-operated frames, an ESP32 with an e-ink display offers excellent power efficiency.
4. **Smart TVs (Browser-supported Devices)**: Frame TVs and smart speakers can support Photo Frame Assistant using our local client frame.

#### Can Photo Frame Assistant work with a device that is not on my network?
Yes. Use services like Cloudflare Tunnel or Ngrok to securely expose your Photo Frame Assistant server to the internet. For security, we recommend using a reverse proxy with SSL/TLS encryption when exposing your server to the internet.

#### How does the battery life work for battery-operated frames?
Battery life depends on several factors:
- **Display Technology**: E-ink displays consume power only when changing images, offering weeks of battery life.
- **Sleep Settings**: Deep sleep mode significantly extends battery life by putting the frame to sleep during specified hours.
- **Wake Frequency**: Less frequent wake cycles (longer sleep intervals) preserve battery life.
- **Hardware**: Different microcontrollers and displays have varying power requirements.

#### Can I use Photo Frame Assistant with commercial photo frames?
Yes, in several ways:
1. **API Integration**: Some commercial frames offer APIs that Photo Frame Assistant can use.
2. **Email Integration**: For frames that accept photos via email, you can configure automatic email forwarding.
3. **Custom Firmware**: For some models, we provide custom firmware that enables direct integration.

#### What's the difference between sync groups and individual frame management?
- **Individual Management**: Each frame has its own playlist, settings, and wake schedule.
- **Sync Groups**: Frames in a sync group share the same content and wake at synchronized times, ensuring consistent displays across multiple frames.

Use sync groups when you want multiple frames to show the same photos (like in different rooms of a house), and individual management when each frame should display different content.

#### How do I add photos from my phone automatically?
Several options are available:
1. **Mobile App**: Use the Photo Frame Assistant webapp to upload photos directly.
2. **Cloud Integration**: Connect Google Photos or Immich to automatically import photos.
3. **Network Folder**: Set up a watched folder that automatically imports new photos.
4. **Auto-Import from Network Shares**: Configure a network share with auto-import enabled, then save photos from your phone to that network location.

#### Can I display videos on my photo frames?
Yes, Photo Frame Assistant supports video playback on compatible frames. Videos can be:
- Uploaded directly through the web interface
- Imported from Google Photos
- Displayed on frames with video playback capabilities

#### Can I control my photo frames through Home Assistant?
Yes, using the MQTT integration you can connect PFA directly to Home Assistant. Once connected, you are able to control what photos are up next on the display, see diagnostic information, add frames as part of scences or automations. For example, when a specific homehold member walks into a room, frames can change to pictures of loved ones. When your favorite album is played on a music source, the frames can change to photos of the band.

#### Can I connect Photo Frame Assistant to my Smart TV or (multimodal) Smart Speaker?
Yes. Simply add a local frame client (under Frame settings) and go to the local frame client website on your TV. When adding a local client you can define the client's capabilities (resolution, video support, etc). Once you create a client you are no longer able to edit its capabilities.

## 📜 License

This project is licensed under the [Apache License 2.0](LICENSE) for non-commercial use.

**Commercial use (e.g., resale, SaaS, hardware integration)** requires a separate license.  
Please see [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for details.