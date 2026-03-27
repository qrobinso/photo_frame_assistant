from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, NonUniqueNameException
import socket
from queue import Queue
from typing import Optional
import uuid
import os
import threading
import time
import logging

logger = logging.getLogger(__name__)

class FrameDiscovery:
    def __init__(self, port: int = 5000):
        self.port = port
        self.zeroconf: Optional[Zeroconf] = None
        self.discovered_frames = {}
        self.service_info: Optional[ServiceInfo] = None
        
        # Use persistent server ID
        self.server_id_file = os.path.join(os.path.dirname(__file__), '.server_id')
        self.server_id = self._get_or_create_server_id()
        
        self._running = False
        self._refresh_thread = None
        self._service_name = None

    def _get_or_create_server_id(self):
        """Get existing server ID or create a new one."""
        try:
            if os.path.exists(self.server_id_file):
                with open(self.server_id_file, 'r') as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"Error reading server ID: {e}")
            
        # Create new ID if none exists
        server_id = str(uuid.uuid4())[:8]
        try:
            with open(self.server_id_file, 'w') as f:
                f.write(server_id)
        except Exception as e:
            logger.warning(f"Error saving server ID: {e}")
        return server_id

    def _refresh_registration(self):
        """Periodically refresh the service registration."""
        while self._running:
            try:
                if self.zeroconf and self.service_info:
                    # Re-register service instead of just updating
                    self.zeroconf.unregister_service(self.service_info)
                    time.sleep(0.1)  # Small delay between unregister/register
                    self.zeroconf.register_service(self.service_info)
                    logger.debug("Re-registered Zeroconf service")
            except Exception as e:
                logger.error(f"Error refreshing registration: {e}")
                # Try to completely restart the service
                try:
                    self.stop()
                    time.sleep(1)
                    self.setup_service()
                except Exception as re_err:
                    logger.error(f"Error re-registering service: {re_err}")
            time.sleep(30)  # Refresh more frequently (every 30 seconds)

    def get_ip_address(self) -> str:
        """Get the non-localhost IP address of the machine."""
        def is_valid(ip):
            return ip and not ip.startswith('127.') and ip != '0.0.0.0'

        # Method 1: UDP routing trick with multiple targets (works even without internet)
        for target in [('8.8.8.8', 80), ('1.1.1.1', 80)]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(target)
                ip = s.getsockname()[0]
                s.close()
                if is_valid(ip):
                    logger.debug(f"Found IP via UDP routing trick: {ip}")
                    return ip
            except Exception:
                pass

        # Method 2: hostname -I (Linux; returns all IPs space-separated)
        try:
            import subprocess
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
            for ip in result.stdout.strip().split():
                if is_valid(ip):
                    logger.debug(f"Found IP via hostname -I: {ip}")
                    return ip
        except Exception:
            pass

        # Method 3: getaddrinfo on hostname
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if is_valid(ip):
                    logger.debug(f"Found IP via getaddrinfo: {ip}")
                    return ip
        except Exception:
            pass

        # Method 4: gethostbyname_ex (returns all addresses for hostname)
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                if is_valid(ip):
                    logger.debug(f"Found IP via gethostbyname_ex: {ip}")
                    return ip
        except Exception:
            pass

        logger.error("Could not determine a valid non-localhost IP address")
        return '0.0.0.0'

    def setup_service(self):
        """Setup and register the Zeroconf service."""
        try:
            local_ip = self.get_ip_address()
            logger.info(f"Registering service with IP: {local_ip}")
            
            # Close any existing Zeroconf instance
            if self.zeroconf:
                try:
                    self.zeroconf.unregister_all_services()
                    self.zeroconf.close()
                except Exception as e:
                    logger.warning(f"Error closing existing Zeroconf: {e}")
            
            # Create new Zeroconf instance
            self.zeroconf = Zeroconf()
            
            # Base service name
            base_name = f"PhotoFrame-Server-{self.server_id}"
            self._service_name = f"{base_name}._photoframe._tcp.local."
            
            properties = {
                'version': '1.0',
                'server_type': 'photo_frame',
                'server_id': self.server_id,
                'server_ip': local_ip,
                'server_port': str(self.port)
            }
            
            properties_bytes = {
                k.encode(): v.encode() 
                for k, v in properties.items()
            }
            
            # Try registering with numbered suffixes if name collision occurs
            max_attempts = 5
            for i in range(max_attempts):
                try:
                    name_with_suffix = self._service_name if i == 0 else f"{base_name}-{i}._photoframe._tcp.local."
                    
                    self.service_info = ServiceInfo(
                        "_photoframe._tcp.local.",
                        name_with_suffix,
                        addresses=[socket.inet_aton(local_ip)],
                        port=self.port,
                        properties=properties_bytes,
                        server=f"photoframe-server-{self.server_id}.local."
                    )
                    
                    self.zeroconf.register_service(self.service_info)
                    self._service_name = name_with_suffix  # Store the successful name
                    logger.info(f"Successfully registered service: {name_with_suffix}")
                    break
                    
                except NonUniqueNameException:
                    if i == max_attempts - 1:
                        raise
                    logger.warning(f"Service name collision, trying alternate name...")
                    time.sleep(1)  # Brief delay before retry
                except Exception as e:
                    logger.error(f"Error registering service: {e}")
                    raise
            
            # Start listening for frames
            self.frame_listener = FrameListener(self.discovered_frames)
            ServiceBrowser(self.zeroconf, "_photoframe._tcp.local.", self.frame_listener)
            
            # Start refresh thread
            if not self._refresh_thread or not self._refresh_thread.is_alive():
                self._running = True
                self._refresh_thread = threading.Thread(target=self._refresh_registration, daemon=True)
                self._refresh_thread.start()
                
        except Exception as e:
            logger.error(f"Error in setup_service: {e}")
            raise

    def start(self):
        """Start the discovery service."""
        if not self._running:
            self.setup_service()

    def stop(self):
        """Stop the discovery service and clean up."""
        self._running = False
        if self._refresh_thread:
            self._refresh_thread.join(timeout=2)
        if self.zeroconf:
            try:
                self.zeroconf.unregister_all_services()
                self.zeroconf.close()
            except Exception as e:
                logger.error(f"Error stopping Zeroconf: {e}")
            finally:
                self.zeroconf = None

    def get_service_info(self):
        """Get current service information for display."""
        if self.service_info:
            return {
                'name': self._service_name,
                'type': "_photoframe._tcp.local.",
                'port': self.port,
                'properties': {
                    k.decode('utf-8'): v.decode('utf-8')
                    for k, v in self.service_info.properties.items()
                }
            }
        return None

    def get_discovered_frames(self):
        """Return a list of currently discovered frames."""
        # Convert the discovered_frames dict to a list of frame data
        return [
            {
                'ip': ip,
                'hostname': frame_data.get('hostname', ''),
                'last_seen': frame_data.get('last_seen', ''),
                'status': frame_data.get('status', 'unknown')
            }
            for ip, frame_data in self.discovered_frames.items()
        ]


class FrameListener:
    def __init__(self, discovered_queue: Queue):
        self.discovered = set()
        self.queue = discovered_queue
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle frame going offline."""
        service_info = zc.get_service_info(type_, name)
        if service_info:
            device_id = service_info.properties.get(b'device_id', b'').decode('utf-8')
            self.discovered.discard(device_id)
    
    def add_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle new frame discovery."""
        service_info = zc.get_service_info(type_, name)
        if service_info:
            device_id = service_info.properties.get(b'device_id', b'').decode('utf-8')
            if device_id:
                frame_info = {
                    'device_id': device_id,
                    'ip_address': socket.inet_ntoa(service_info.addresses[0]),
                    'properties': {
                        k.decode('utf-8'): v.decode('utf-8') 
                        for k, v in service_info.properties.items()
                    }
                }
                self.discovered.add(device_id)
                self.queue.put(frame_info)

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle frame updates."""
        self.add_service(zc, type_, name)
