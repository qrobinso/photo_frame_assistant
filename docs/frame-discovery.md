# Frame Discovery Process

This document explains how the Photo Frame Assistant server discovers and manages photo frames on the network.

## Overview

The frame discovery process allows the server to automatically detect compatible photo frames on the local network without manual configuration. This is implemented using Zeroconf/mDNS (Multicast DNS), which enables devices to advertise and discover services on a local network without a centralized directory.

```
┌─────────────────┐                  ┌─────────────────┐
│                 │  1. Advertise    │                 │
│  Photo Frame    │─────────────────►│    Network      │
│                 │                  │                 │
└─────────────────┘                  └────────┬────────┘
                                              │
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │                 │
                                     │     Server      │
                                     │  2. Discover    │
                                     │                 │
                                     └─────────────────┘
```

## Discovery Mechanism

### Service Advertisement

Photo frames advertise themselves on the network using the following service type:

```
_photoframe._tcp.local.
```

Each frame includes the following information in its service advertisement:

- **Service Name**: Typically the frame's device ID or name
- **IP Address**: The frame's current IP address
- **Port**: The port on which the frame is listening (usually 80 or 8080)
- **TXT Records**: Additional metadata about the frame

Example TXT records:
- `model=E-Ink-10.3`
- `manufacturer=PhotoFrameInc`
- `id=frame123`
- `version=1.0.5`
- `capabilities=weather,deep-sleep,color`

### Server Discovery Process

The server continuously listens for service advertisements matching the `_photoframe._tcp.local.` service type. When a new frame is detected, the server:

1. Extracts the frame's IP address, port, and metadata from the service advertisement
2. Checks if the frame is already registered in the database
3. If not registered, adds it to the list of discovered frames
4. Makes the frame available for registration in the web interface

## Discovery Implementation

### Server-Side Implementation

The server uses the `zeroconf` Python library to implement service discovery:

```python
from zeroconf import ServiceBrowser, Zeroconf

class FrameDiscovery:
    def __init__(self, port=5000):
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, "_photoframe._tcp.local.", self)
        self.discovered_frames = {}
        self.port = port
        
    def add_service(self, zeroconf, service_type, name):
        """Called when a new service is discovered."""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            # Extract IP address
            addresses = info.parsed_addresses()
            if addresses:
                ip_address = addresses[0]
                
                # Extract metadata from TXT records
                txt_records = {}
                for key, value in info.properties.items():
                    txt_records[key.decode('utf-8')] = value.decode('utf-8')
                
                # Store discovered frame
                frame_id = txt_records.get('id', name.split('.')[0])
                self.discovered_frames[frame_id] = {
                    'ip_address': ip_address,
                    'port': info.port,
                    'name': name.split('.')[0],
                    'metadata': txt_records
                }
                
                print(f"Discovered frame: {frame_id} at {ip_address}:{info.port}")
    
    def remove_service(self, zeroconf, service_type, name):
        """Called when a service is removed."""
        frame_id = name.split('.')[0]
        if frame_id in self.discovered_frames:
            del self.discovered_frames[frame_id]
            print(f"Frame removed: {frame_id}")
    
    def get_discovered_frames(self):
        """Return the list of discovered frames."""
        return self.discovered_frames
    
    def close(self):
        """Close the Zeroconf browser."""
        self.zeroconf.close()
```

### Frame-Side Implementation

Photo frames implement service advertisement using platform-specific libraries:

#### Python Example (for development/testing):

```python
from zeroconf import ServiceInfo, Zeroconf
import socket
import time

def get_ip_address():
    """Get the local IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def advertise_frame(frame_id, model, manufacturer, version, capabilities):
    """Advertise the frame on the network."""
    ip_address = get_ip_address()
    port = 8080
    
    # Prepare TXT records
    properties = {
        'id': frame_id,
        'model': model,
        'manufacturer': manufacturer,
        'version': version,
        'capabilities': ','.join(capabilities)
    }
    
    # Create service info
    service_info = ServiceInfo(
        "_photoframe._tcp.local.",
        f"{frame_id}._photoframe._tcp.local.",
        addresses=[socket.inet_aton(ip_address)],
        port=port,
        properties=properties
    )
    
    # Register service
    zeroconf = Zeroconf()
    zeroconf.register_service(service_info)
    
    print(f"Advertising frame {frame_id} at {ip_address}:{port}")
    
    try:
        # Keep advertising until interrupted
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # Unregister service
        zeroconf.unregister_service(service_info)
        zeroconf.close()

# Example usage
if __name__ == "__main__":
    advertise_frame(
        frame_id="frame123",
        model="E-Ink-10.3",
        manufacturer="PhotoFrameInc",
        version="1.0.5",
        capabilities=["weather", "deep-sleep", "color"]
    )
```

#### ESP32/Arduino Example:

```cpp
#include <WiFi.h>
#include <ESPmDNS.h>

const char* ssid = "YourWiFiSSID";
const char* password = "YourWiFiPassword";
const char* frameId = "frame123";
const int port = 8080;

void setup() {
  Serial.begin(115200);
  
  // Connect to WiFi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.println("WiFi connected");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
  
  // Initialize mDNS
  if (!MDNS.begin(frameId)) {
    Serial.println("Error setting up mDNS responder!");
    while(1) {
      delay(1000);
    }
  }
  
  // Add service to mDNS
  MDNS.addService("photoframe", "tcp", port);
  
  // Add TXT records
  MDNS.addServiceTxt("photoframe", "tcp", "id", frameId);
  MDNS.addServiceTxt("photoframe", "tcp", "model", "E-Ink-7.8");
  MDNS.addServiceTxt("photoframe", "tcp", "manufacturer", "PhotoFrameInc");
  MDNS.addServiceTxt("photoframe", "tcp", "version", "1.0.5");
  MDNS.addServiceTxt("photoframe", "tcp", "capabilities", "weather,deep-sleep,color");
  
  Serial.println("mDNS responder started");
  Serial.println("Advertising frame on the network");
  
  // Start a simple web server to respond to discovery
  // ...
}

void loop() {
  // Handle server requests
  // ...
}
```

## Registration Process

After discovery, frames need to be registered with the server to be fully functional:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Discovery  │────►│ Registration│────►│Initial Setup│────►│ Normal Cycle│
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Automatic Registration

The server can be configured to automatically register discovered frames:

1. When a new frame is discovered, the server checks if auto-registration is enabled
2. If enabled, the server automatically creates a new frame entry in the database
3. Default settings are applied based on the frame's capabilities
4. The frame is added to a default playlist

### Manual Registration

If auto-registration is disabled (default), the registration process is:

1. Discovered frames appear in the "Discovered Frames" section of the web interface
2. The administrator clicks "Register" for a specific frame
3. A registration form is displayed with pre-filled information from discovery
4. The administrator can modify the name, settings, and initial playlist
5. After submission, the frame is registered and ready for use

## Web Interface

The web interface provides a dedicated section for frame discovery and registration:

```
┌───────────────────────────────────────────────────────────────┐
│ Discovered Frames                                             │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────┐  ┌─────────────────────────┐     │
│  │ Frame: E-Ink-10.3       │  │ Frame: LCD-8.0          │     │
│  │ ID: frame123            │  │ ID: frame456            │     │
│  │ IP: 192.168.1.101       │  │ IP: 192.168.1.102       │     │
│  │ Manufacturer: PhotoInc  │  │ Manufacturer: FrameCo   │     │
│  │                         │  │                         │     │
│  │      [Register]         │  │      [Register]         │     │
│  └─────────────────────────┘  └─────────────────────────┘     │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## Discovery Configuration

The discovery service can be configured through the server settings:

- **Enable/Disable Discovery**: Turn the discovery service on or off
- **Discovery Port**: The port on which the server listens for advertisements (default: 5000)
- **Auto-Registration**: Enable or disable automatic registration of discovered frames
- **Default Settings**: Configure default settings for automatically registered frames

## Troubleshooting

### Common Discovery Issues

1. **Frames not being discovered**:
   - Ensure the frame and server are on the same network
   - Check if multicast traffic is allowed on the network
   - Verify the frame is correctly advertising its service
   - Check firewall settings that might block mDNS traffic (UDP port 5353)

2. **Discovery works but registration fails**:
   - Check network connectivity between the frame and server
   - Verify the frame's IP address is accessible from the server
   - Ensure the frame is responding to HTTP requests

### Debugging Discovery

To debug discovery issues:

1. **Enable verbose logging**:
   ```
   # In server_settings.json
   {
     "log_level": "DEBUG"
   }
   ```

2. **Use mDNS diagnostic tools**:
   - On Linux/macOS: `avahi-browse -a`
   - On Windows: `dns-sd -B _photoframe._tcp`

3. **Check network traffic**:
   - Use Wireshark to capture and analyze mDNS traffic
   - Filter for UDP port 5353

## Security Considerations

### Network Security

mDNS/Zeroconf is designed for local networks and has limited security features:

- **Network Isolation**: Ensure the discovery service runs on a trusted local network
- **Firewall Rules**: Configure firewalls to restrict mDNS traffic to trusted networks
- **Registration Validation**: Implement additional validation during frame registration

### Registration Security

To prevent unauthorized frames from registering:

1. **Manual Approval**: Require administrator approval for frame registration
2. **Authentication Tokens**: Generate unique tokens for each frame during registration
3. **IP Filtering**: Restrict registration to specific IP ranges

## References

- [Zeroconf Specification](https://datatracker.ietf.org/doc/html/rfc6762)
- [mDNS Specification](https://datatracker.ietf.org/doc/html/rfc6763)
- [Python Zeroconf Library](https://github.com/jstasiak/python-zeroconf)
- [ESP32 mDNS Documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/mdns.html) 