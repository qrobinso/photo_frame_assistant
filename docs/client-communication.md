# Client-Server Communication

This document explains how photo frames (clients) communicate with the Photo Frame Assistant server.

## Communication Architecture

The Photo Frame Assistant uses a RESTful API architecture for communication between the server and client photo frames:

```
┌─────────────────┐                  ┌─────────────────┐
│                 │                  │                 │
│  Photo Frame    │◄────HTTP/REST────►    Server       │
│  (Client)       │                  │                 │
│                 │                  │                 │
└─────────────────┘                  └─────────────────┘
```

All communication happens over HTTP, with JSON as the primary data format for API responses. Binary data (images) are transferred as standard HTTP responses with appropriate content types.

## Typical Client Lifecycle

Photo frames follow a typical wake-sleep cycle to conserve power and update content:

```
┌─────────┐     ┌─────────────┐     ┌───────────┐     ┌─────────────┐     ┌─────────┐
│         │     │             │     │           │     │             │     │         │
│  Wake   │────►│ Get Settings│────►│ Get Photo │────►│Send Diagnostic│───►│ Sleep   │
│         │     │             │     │           │     │    Data      │     │         │
└─────────┘     └─────────────┘     └───────────┘     └─────────────┘     └─────────┘
                                                                              │
                                        ┌───────────────────────────────────┘
                                        │
                                        ▼
                                   ┌─────────┐
                                   │         │
                                   │  Wake   │
                                   │         │
                                   └─────────┘
```

This cycle repeats based on the configured sleep interval, allowing frames to conserve power while periodically refreshing content.

## Detailed Communication Flow

### 1. Wake Up

When a frame wakes up from sleep mode, it:

- Powers on necessary components (WiFi, display, etc.)
- Establishes network connectivity
- Prepares to communicate with the server

### 2. Get Settings

The frame first requests its current settings from the server:

```
┌─────────────┐                                  ┌─────────────┐
│             │                                  │             │
│   Frame     │─────GET /api/settings?id=123────►│   Server    │
│             │                                  │             │
│             │◄────200 OK (Settings JSON)───────│             │
│             │                                  │             │
└─────────────┘                                  └─────────────┘
```

**Request:**
```
GET /api/settings?id=123 HTTP/1.1
Host: server-address:5000
```

**Response:**
```json
{
  "sleep_interval": 300,
  "orientation": "landscape",
  "contrast_factor": 1.0,
  "saturation": 100,
  "blue_adjustment": 0,
  "padding": 0,
  "overlay_preferences": {
    "weather": true,
    "metadata": false
  },
  "shuffle_enabled": false,
  "deep_sleep_enabled": true,
  "deep_sleep_start": 23,
  "deep_sleep_end": 7
}
```

The frame applies these settings, which may affect:
- How long it sleeps between updates
- How images are displayed (orientation, contrast, etc.)
- What overlays are shown on images
- When deep sleep mode is active

### 3. Get Next Photo

After applying settings, the frame requests the next photo to display:

```
┌─────────────┐                                  ┌─────────────┐
│             │                                  │             │
│   Frame     │────GET /api/next_photo?id=123───►│   Server    │
│             │                                  │             │
│             │◄───200 OK (Photo Data + URL)─────│             │
│             │                                  │             │
│             │────GET /photos/{filename}───────►│             │
│             │                                  │             │
│             │◄───200 OK (Image Binary)─────────│             │
│             │                                  │             │
└─────────────┘                                  └─────────────┘
```

**Request for photo metadata:**
```
GET /api/next_photo?id=123 HTTP/1.1
Host: server-address:5000
```

**Response with photo metadata:**
```json
{
  "photo_id": 456,
  "filename": "sunset.jpg",
  "url": "/photos/sunset.jpg",
  "orientation": "landscape",
  "heading": "Beautiful Sunset",
  "exif_metadata": {
    "camera": "Canon EOS R5",
    "date_taken": "2023-06-15T19:30:45",
    "exposure": "1/125",
    "aperture": "f/4.0",
    "iso": 100
  },
  "weather_data": {
    "temperature": 22,
    "condition": "Clear",
    "location": "New York"
  }
}
```

**Request for the actual image:**
```
GET /photos/sunset.jpg HTTP/1.1
Host: server-address:5000
```

**Response with image binary:**
```
HTTP/1.1 200 OK
Content-Type: image/jpeg
Content-Length: 2458012

[Binary image data]
```

The server determines which photo to send based on:
- The frame's playlist
- Shuffle settings
- Previously displayed photos
- Dynamic playlist settings (if enabled)

### 4. Send Diagnostic Data

After displaying the new photo, the frame sends diagnostic information back to the server:

```
┌─────────────┐                                  ┌─────────────┐
│             │                                  │             │
│   Frame     │─────POST /api/diagnostic─────────►│   Server    │
│             │     {battery, status, etc}       │             │
│             │                                  │             │
│             │◄────200 OK (Success)─────────────│             │
│             │                                  │             │
└─────────────┘                                  └─────────────┘
```

**Request:**
```
POST /api/diagnostic HTTP/1.1
Host: server-address:5000
Content-Type: application/json

{
  "frame_id": "123",
  "battery_level": 85.5,
  "current_photo_id": 456,
  "last_wake_time": "2023-10-15T08:30:00Z",
  "errors": [],
  "temperature": 32.5,
  "wifi_signal_strength": -67,
  "memory_usage": 42.3,
  "uptime": 3600
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Diagnostic data received"
}
```

This diagnostic data helps the server:
- Monitor frame health
- Track battery levels
- Identify potential issues
- Maintain accurate frame status information

### 5. Sleep

After completing the communication cycle, the frame:

1. Calculates the next wake time based on the sleep interval from settings
2. Powers down non-essential components
3. Enters low-power sleep mode until the next wake time

If deep sleep is enabled and the current time falls within the deep sleep window, the frame may enter an even lower power state and skip some wake cycles.

## Frame Registration Process

New frames go through a registration process before entering the normal wake-sleep cycle:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Discovery  │────►│ Registration│────►│Initial Setup│────►│ Normal Cycle│
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Discovery Phase

The server discovers new frames on the network using Zeroconf/mDNS:

1. Frames advertise themselves on the network with a specific service type
2. The server listens for these advertisements
3. When a new frame is detected, it appears in the "Discovered Frames" section of the web interface

### Registration Phase

Once discovered, frames need to be registered:

**Request:**
```
POST /api/register_frame HTTP/1.1
Host: server-address:5000
Content-Type: application/json

{
  "device_id": "frame123",
  "manufacturer": "PhotoFrame Inc.",
  "model": "E-Ink 10.3",
  "hardware_rev": "v2.1",
  "firmware_rev": "1.0.5",
  "screen_resolution": "1872x1404",
  "aspect_ratio": "4:3",
  "os": "FrameOS 3.2",
  "capabilities": {
    "supports_video": false,
    "supports_weather": true,
    "supports_deep_sleep": true,
    "color_depth": "16-bit",
    "max_image_size": "2048x2048"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "frame_id": "frame123",
  "name": "New Frame",
  "initial_settings": {
    "sleep_interval": 300,
    "orientation": "landscape"
  }
}
```

### Initial Setup Phase

After registration:

1. The server creates a new entry in the database for the frame
2. Default settings are assigned
3. The frame may be automatically added to a default playlist
4. The user can complete setup through the web interface

### Normal Cycle

Once registered and set up, the frame enters the normal wake-sleep cycle described above.

## API Endpoints for Frame Communication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | GET | Retrieve frame-specific settings |
| `/api/current_photo` | GET | Get the currently assigned photo |
| `/api/next_photo` | GET | Get the next photo to display |
| `/api/diagnostic` | POST | Send diagnostic data to server |
| `/api/server-time` | GET | Synchronize time with server |
| `/photos/{filename}` | GET | Download a specific photo file |
| `/api/frames/{frame_id}/force_update` | POST | Force an immediate update |
| `/api/register_frame` | POST | Register a new frame with the server |
| `/api/discovered_frames` | GET | List frames discovered on the network |

## Error Handling

Frames implement error handling for various scenarios:

### Connection Failure

If the server is unreachable:

1. The frame logs the error
2. Implements exponential backoff for retry attempts
3. After several failed attempts, may enter a longer sleep period
4. Continues to display the last successful photo

Example backoff strategy:
- First retry: 30 seconds
- Second retry: 2 minutes
- Third retry: 8 minutes
- Fourth retry: 32 minutes
- Maximum backoff: 60 minutes

### Download Failure

If photo download fails:

1. The frame logs the error
2. Retries the download up to 3 times
3. If still unsuccessful, keeps displaying the current photo
4. Reports the failure in the next successful diagnostic upload

### Invalid Settings

If received settings contain invalid values:

1. The frame logs the issue
2. Applies default values for the problematic settings
3. Continues operation with the valid settings
4. Reports the issue in the next diagnostic upload

## Security Considerations

### Authentication

Frames should implement basic authentication when communicating with the server:

```
GET /api/settings HTTP/1.1
Host: server-address:5000
Authorization: Basic ZnJhbWUxMjM6c2VjcmV0X3Rva2Vu
```

### HTTPS

For secure deployments, all communication should occur over HTTPS to prevent:
- Man-in-the-middle attacks
- Credential theft
- Content tampering

### Firewall Configuration

In production environments, configure firewalls to:
- Allow only known frames to access the server
- Restrict access to the necessary ports
- Implement rate limiting to prevent abuse

## Implementation Examples

### Python Client Example

```python
import requests
import time
import json
import base64

class PhotoFrameClient:
    def __init__(self, server_url, frame_id, auth_token):
        self.server_url = server_url
        self.frame_id = frame_id
        self.auth_headers = {
            'Authorization': f'Basic {base64.b64encode(f"{frame_id}:{auth_token}".encode()).decode()}'
        }
        self.current_photo = None
        self.settings = None
    
    def get_settings(self):
        try:
            response = requests.get(
                f"{self.server_url}/api/settings?id={self.frame_id}",
                headers=self.auth_headers
            )
            if response.status_code == 200:
                self.settings = response.json()
                return self.settings
            else:
                print(f"Error getting settings: {response.status_code}")
                return None
        except Exception as e:
            print(f"Connection error: {e}")
            return None
    
    def get_next_photo(self):
        try:
            response = requests.get(
                f"{self.server_url}/api/next_photo?id={self.frame_id}",
                headers=self.auth_headers
            )
            if response.status_code == 200:
                photo_data = response.json()
                # Download the actual image
                image_response = requests.get(
                    f"{self.server_url}{photo_data['url']}",
                    headers=self.auth_headers
                )
                if image_response.status_code == 200:
                    # Save image to local file
                    with open("current_photo.jpg", "wb") as f:
                        f.write(image_response.content)
                    self.current_photo = photo_data
                    return photo_data
            return None
        except Exception as e:
            print(f"Error getting next photo: {e}")
            return None
    
    def send_diagnostic(self, battery_level, errors=None):
        if errors is None:
            errors = []
        
        diagnostic_data = {
            "frame_id": self.frame_id,
            "battery_level": battery_level,
            "current_photo_id": self.current_photo["photo_id"] if self.current_photo else None,
            "last_wake_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "errors": errors
        }
        
        try:
            response = requests.post(
                f"{self.server_url}/api/diagnostic",
                headers={**self.auth_headers, 'Content-Type': 'application/json'},
                json=diagnostic_data
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Error sending diagnostic data: {e}")
            return False
    
    def run_cycle(self, battery_level):
        # Get settings
        settings = self.get_settings()
        if not settings:
            return False
        
        # Get next photo
        photo = self.get_next_photo()
        if not photo:
            return False
        
        # Send diagnostic data
        success = self.send_diagnostic(battery_level)
        
        # Return sleep interval
        return settings.get("sleep_interval", 300)
```

### ESP32/Arduino Client Example

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* ssid = "YourWiFiSSID";
const char* password = "YourWiFiPassword";
const char* serverUrl = "http://your-server:5000";
const char* frameId = "frame123";
const char* authToken = "secret_token";

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.println("Connecting to WiFi...");
  }
  
  Serial.println("Connected to WiFi");
  
  // Run the communication cycle
  int sleepInterval = runCommunicationCycle();
  
  // Go to sleep
  Serial.printf("Going to sleep for %d seconds\n", sleepInterval);
  ESP.deepSleep(sleepInterval * 1000000); // Convert to microseconds
}

void loop() {
  // This won't run with deep sleep
}

int runCommunicationCycle() {
  // Get settings
  DynamicJsonDocument settings(1024);
  bool settingsSuccess = getSettings(settings);
  if (!settingsSuccess) {
    return 300; // Default sleep interval
  }
  
  // Get next photo
  String photoUrl;
  bool photoSuccess = getNextPhoto(photoUrl);
  if (!photoSuccess) {
    return settings["sleep_interval"] | 300;
  }
  
  // Send diagnostic data
  float batteryLevel = getBatteryLevel();
  sendDiagnostic(batteryLevel);
  
  // Return sleep interval
  return settings["sleep_interval"] | 300;
}

bool getSettings(DynamicJsonDocument &settings) {
  HTTPClient http;
  String url = String(serverUrl) + "/api/settings?id=" + frameId;
  
  http.begin(url);
  http.addHeader("Authorization", "Basic " + base64Encode(String(frameId) + ":" + String(authToken)));
  
  int httpCode = http.GET();
  if (httpCode == 200) {
    String payload = http.getString();
    DeserializationError error = deserializeJson(settings, payload);
    http.end();
    return !error;
  } else {
    Serial.printf("Settings request failed, code: %d\n", httpCode);
    http.end();
    return false;
  }
}

bool getNextPhoto(String &photoUrl) {
  HTTPClient http;
  String url = String(serverUrl) + "/api/next_photo?id=" + frameId;
  
  http.begin(url);
  http.addHeader("Authorization", "Basic " + base64Encode(String(frameId) + ":" + String(authToken)));
  
  int httpCode = http.GET();
  if (httpCode == 200) {
    String payload = http.getString();
    DynamicJsonDocument photoData(1024);
    DeserializationError error = deserializeJson(photoData, payload);
    
    if (!error) {
      photoUrl = photoData["url"].as<String>();
      // Download the actual image...
      // Display the image...
      http.end();
      return true;
    }
  }
  
  http.end();
  return false;
}

void sendDiagnostic(float batteryLevel) {
  HTTPClient http;
  String url = String(serverUrl) + "/api/diagnostic";
  
  http.begin(url);
  http.addHeader("Authorization", "Basic " + base64Encode(String(frameId) + ":" + String(authToken)));
  http.addHeader("Content-Type", "application/json");
  
  DynamicJsonDocument diagnosticData(1024);
  diagnosticData["frame_id"] = frameId;
  diagnosticData["battery_level"] = batteryLevel;
  diagnosticData["last_wake_time"] = "2023-10-15T08:30:00Z"; // Use actual time
  
  String requestBody;
  serializeJson(diagnosticData, requestBody);
  
  int httpCode = http.POST(requestBody);
  http.end();
}

float getBatteryLevel() {
  // Read battery level from hardware
  return 85.5; // Example value
}

String base64Encode(String input) {
  // Base64 encoding implementation
  // This is a placeholder - use a proper base64 library
  return input;
}
```

## Troubleshooting

### Common Issues

1. **Frame not connecting to server**
   - Check network connectivity
   - Verify server address is correct
   - Ensure firewall allows connections

2. **Authentication failures**
   - Verify frame ID and auth token
   - Check if frame is properly registered

3. **Photo download issues**
   - Check available storage on frame
   - Verify image format is supported
   - Check for network bandwidth limitations

4. **Inconsistent wake times**
   - Synchronize frame clock with server time
   - Check for deep sleep configuration issues
   - Verify sleep interval settings

### Debugging

For debugging communication issues:

1. Enable verbose logging on the frame
2. Check server logs for API request errors
3. Use network monitoring tools to inspect HTTP traffic
4. Verify JSON payloads are properly formatted

## References

- [RESTful API Best Practices](https://restfulapi.net/)
- [HTTP Status Codes](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status)
- [JSON Data Format](https://www.json.org/) 