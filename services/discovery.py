from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, NonUniqueNameException
import socket
from typing import Optional
import uuid
import os
import threading
import time
import logging

import psutil

logger = logging.getLogger(__name__)

# How often the maintenance loop checks that the advertisement is still
# correct (address changes are picked up within this window).
DEFAULT_REFRESH_INTERVAL = 30
# How often to re-announce an unchanged service. This is a plain announcement,
# never an unregister/register pair, so frame caches are refreshed rather than
# purged.
DEFAULT_REANNOUNCE_INTERVAL = 300

# Interface name prefixes whose addresses are never reachable from a frame on
# the LAN: container bridges, VPN tunnels and hypervisor networks. A Docker
# host (this app ships with network_mode: host) always has docker0 and one
# br-<id> per user-defined network, so every generic "what is my IP" lookup
# returns 172.x addresses alongside the real one.
VIRTUAL_IFACE_PREFIXES = (
    'docker', 'br-', 'veth', 'virbr', 'vmnet', 'vboxnet',
    'tun', 'tap', 'utun', 'wg', 'zt', 'tailscale', 'lo',
)


class FrameDiscovery:
    def __init__(self, port: int = 5000,
                 refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
                 reannounce_interval: float = DEFAULT_REANNOUNCE_INTERVAL,
                 advertise_ips: Optional[list] = None):
        self.port = port
        # Explicit override for hosts whose addressing we cannot infer
        # (advertise_ips in server_settings.json). Empty/None means autodetect.
        self.advertise_ips = [ip for ip in (advertise_ips or []) if ip]
        self.refresh_interval = refresh_interval
        self.reannounce_interval = reannounce_interval
        self._last_announce = 0.0
        self._consecutive_failures = 0
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

    def _maintenance_loop(self):
        """Keep the advertisement correct. This loop must never exit on error."""
        while self._running:
            try:
                self._maintenance_tick()
            except Exception as e:
                # A tick should handle its own errors; this is the last line of
                # defence so the thread can never die the way it used to.
                logger.error(f"Unhandled error in discovery maintenance: {e}")
            time.sleep(self.refresh_interval)
        logger.info("Discovery maintenance loop exited (service stopped).")

    def _maintenance_tick(self):
        """One maintenance pass. Swallows its own errors by design."""
        # Health check: if the stack collapsed, rebuild it from scratch.
        if self.zeroconf is None or self.service_info is None:
            logger.warning("Zeroconf stack is gone — rebuilding discovery service.")
            try:
                self.setup_service()
                self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(f"Failed to rebuild discovery service: {e}")
            return

        try:
            current = set(self.get_ip_addresses())
            advertised = {socket.inet_ntoa(a) for a in self.service_info.addresses}

            if current and current != advertised:
                # The host moved (DHCP lease, interface up/down, new subnet).
                logger.info(
                    f"Server address changed {sorted(advertised)} -> {sorted(current)}; "
                    "republishing mDNS advertisement."
                )
                new_info = self._build_service_info(sorted(current))
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.register_service(new_info)
                # Only commit after a successful register, so a failure here is
                # retried on the next tick instead of being silently lost.
                self.service_info = new_info
                self._last_announce = time.monotonic()
                self._consecutive_failures = 0
                return

            if not current:
                logger.warning("No usable local IP address found; keeping last advertisement.")

            # Steady state: re-announce periodically WITHOUT a goodbye packet.
            if (time.monotonic() - self._last_announce) >= self.reannounce_interval:
                self.zeroconf.update_service(self.service_info)
                self._last_announce = time.monotonic()
                logger.debug("Re-announced Zeroconf service")

            self._consecutive_failures = 0

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(
                f"Error maintaining Zeroconf registration "
                f"(failure {self._consecutive_failures}): {e}"
            )
            # Repeated failures mean the stack itself is wedged. Drop it and let
            # the next tick rebuild; never tear down from inside this thread the
            # way the old code did.
            if self._consecutive_failures >= 3:
                logger.warning("Discovery unhealthy — tearing down for rebuild.")
                self._teardown_zeroconf()

    def _teardown_zeroconf(self):
        """Close the Zeroconf stack. Safe to call from the maintenance thread."""
        if self.zeroconf:
            try:
                self.zeroconf.unregister_all_services()
                self.zeroconf.close()
            except Exception as e:
                logger.error(f"Error closing Zeroconf: {e}")
            finally:
                self.zeroconf = None
                self.service_info = None

    def get_ip_addresses(self) -> list:
        """Return the addresses to advertise, best route first.

        A multi-homed host (Wi-Fi + Ethernet) must advertise all of its real
        addresses: publishing only one means a frame on the other interface —
        or one that outlives the chosen interface — can never reach us.

        But "every local address" is too broad. On a Docker host it includes
        the bridge gateways (172.17.0.1, 172.18.0.1), which no frame can reach
        and which mean something different on every host. Publishing them as A
        records is worse than useless: the responder does not preserve the
        order we register, and a client that takes the first A record it sees
        (the ESP32 firmware calls MDNS.address(0)) then talks to nothing.
        """
        if self.advertise_ips:
            logger.debug(f"Using configured advertise_ips: {self.advertise_ips}")
            return list(self.advertise_ips)

        candidates = self._candidate_ip_addresses()
        virtual = self._virtual_interface_ips()
        usable = [ip for ip in candidates if ip not in virtual]

        if not usable:
            # Better to advertise something questionable than nothing at all.
            if candidates:
                logger.warning(
                    "Every local address looks virtual "
                    f"({candidates}); advertising them unfiltered."
                )
            return candidates

        if len(usable) != len(candidates):
            logger.debug(
                "Excluded virtual-interface addresses from advertisement: "
                f"{[ip for ip in candidates if ip in virtual]}"
            )
        return usable

    @staticmethod
    def _virtual_interface_ips() -> set:
        """IPv4 addresses bound to container/VPN/virtual interfaces."""
        skip = set()
        try:
            for name, addrs in psutil.net_if_addrs().items():
                if not name.startswith(VIRTUAL_IFACE_PREFIXES):
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address:
                        skip.add(addr.address)
        except Exception as e:
            # Without the interface map we cannot filter; advertising a
            # superset still beats advertising nothing.
            logger.debug(f"Could not enumerate interfaces: {e}")
        return skip

    def _candidate_ip_addresses(self) -> list:
        """Every usable non-loopback IPv4 address, best route first."""
        def is_valid(ip):
            return bool(ip) and not ip.startswith('127.') and ip != '0.0.0.0'

        found = []

        def add(ip, how):
            if is_valid(ip) and ip not in found:
                found.append(ip)
                logger.debug(f"Found IP via {how}: {ip}")

        # Method 1: UDP routing trick — gives the interface with the default
        # route, so it goes first and stays the primary advertised address.
        for target in [('8.8.8.8', 80), ('1.1.1.1', 80)]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(target)
                add(s.getsockname()[0], 'UDP routing trick')
                s.close()
            except Exception:
                pass

        # Method 2: every address bound to a local interface.
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                add(info[4][0], 'getaddrinfo')
        except Exception:
            pass

        # Method 3: hostname -I (Linux; returns all IPs space-separated).
        try:
            import subprocess
            result = subprocess.run(['hostname', '-I'], capture_output=True,
                                    text=True, timeout=2)
            for ip in result.stdout.strip().split():
                add(ip, 'hostname -I')
        except Exception:
            pass

        # Method 4: gethostbyname_ex (all addresses for hostname).
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                add(ip, 'gethostbyname_ex')
        except Exception:
            pass

        if not found:
            logger.error("Could not determine any valid non-localhost IP address")
        return found

    def get_ip_address(self) -> str:
        """Primary address (kept for callers that want a single IP)."""
        addresses = self.get_ip_addresses()
        return addresses[0] if addresses else '0.0.0.0'

    def _build_service_info(self, addresses):
        """Build a ServiceInfo advertising every given address."""
        properties = {
            'version': '1.0',
            'server_type': 'photo_frame',
            'server_id': self.server_id,
            # Primary address, kept for frames that read the TXT record
            # instead of the A record.
            'server_ip': addresses[0],
            'server_port': str(self.port),
            # All addresses, comma separated, for clients that can use them.
            'server_ips': ','.join(addresses),
        }
        properties_bytes = {k.encode(): v.encode() for k, v in properties.items()}

        return ServiceInfo(
            "_photoframe._tcp.local.",
            self._service_name,
            addresses=[socket.inet_aton(ip) for ip in addresses],
            port=self.port,
            properties=properties_bytes,
            server=f"photoframe-server-{self.server_id}.local."
        )

    def setup_service(self):
        """Setup and register the Zeroconf service."""
        try:
            addresses = self.get_ip_addresses()
            if not addresses:
                raise RuntimeError("No usable local IP address to advertise")
            logger.info(f"Registering service with addresses: {addresses}")

            # Close any existing Zeroconf instance (does not touch threads).
            self._teardown_zeroconf()

            self.zeroconf = Zeroconf()

            base_name = f"PhotoFrame-Server-{self.server_id}"

            # Try registering with numbered suffixes if a name collision occurs
            max_attempts = 5
            for i in range(max_attempts):
                try:
                    self._service_name = (
                        f"{base_name}._photoframe._tcp.local." if i == 0
                        else f"{base_name}-{i}._photoframe._tcp.local."
                    )
                    self.service_info = self._build_service_info(addresses)
                    self.zeroconf.register_service(self.service_info)
                    logger.info(f"Successfully registered service: {self._service_name}")
                    break

                except NonUniqueNameException:
                    if i == max_attempts - 1:
                        raise
                    logger.warning("Service name collision, trying alternate name...")
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"Error registering service: {e}")
                    raise

            self._last_announce = time.monotonic()
            self._consecutive_failures = 0

            # Start listening for frames
            self.frame_listener = FrameListener(self.discovered_frames)
            ServiceBrowser(self.zeroconf, "_photoframe._tcp.local.", self.frame_listener)

            # Start the maintenance thread (only if one isn't already running)
            self._running = True
            if not self._refresh_thread or not self._refresh_thread.is_alive():
                self._refresh_thread = threading.Thread(
                    target=self._maintenance_loop, daemon=True,
                    name="frame-discovery-maintenance"
                )
                self._refresh_thread.start()

        except Exception as e:
            logger.error(f"Error in setup_service: {e}")
            raise

    def start(self):
        """Start the discovery service."""
        if not self._running:
            self.setup_service()

    def stop(self):
        """Stop the discovery service and clean up.

        Safe to call from the maintenance thread itself. Joining the current
        thread raises RuntimeError, which previously aborted cleanup partway
        and left discovery permanently dead.
        """
        self._running = False
        thread = self._refresh_thread
        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=2)
        self._teardown_zeroconf()

    def is_healthy(self) -> bool:
        """True when the advertisement is live and the maintenance loop is up."""
        return bool(
            self._running
            and self.zeroconf is not None
            and self.service_info is not None
            and self._refresh_thread is not None
            and self._refresh_thread.is_alive()
        )

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
        # discovered_frames is keyed by device_id; expose a stable list shape
        return [
            {
                'device_id': device_id,
                'ip': frame_data.get('ip', ''),
                'hostname': frame_data.get('hostname', ''),
                'port': frame_data.get('port'),
                'last_seen': frame_data.get('last_seen', ''),
                'status': frame_data.get('status', 'unknown'),
                'properties': frame_data.get('properties', {}),
            }
            for device_id, frame_data in self.discovered_frames.items()
        ]


class FrameListener:
    """Zeroconf listener that records frames advertising themselves.

    `frames` is the shared dict owned by FrameDiscovery, keyed by device_id.
    """

    def __init__(self, frames: dict):
        self.frames = frames

    @staticmethod
    def _decode_properties(service_info):
        props = {}
        for k, v in (service_info.properties or {}).items():
            if k is None:
                continue
            try:
                key = k.decode('utf-8')
            except (AttributeError, UnicodeDecodeError):
                continue
            if v is None:
                props[key] = ''
                continue
            try:
                props[key] = v.decode('utf-8')
            except (AttributeError, UnicodeDecodeError):
                props[key] = ''
        return props

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle frame going offline."""
        try:
            # The record is already gone from the cache, so match on the stored name.
            for device_id, data in list(self.frames.items()):
                if data.get('service_name') == name:
                    self.frames.pop(device_id, None)
                    logger.info(f"Frame {device_id} went offline ({name})")
        except Exception as e:
            logger.error(f"Error handling removed service {name}: {e}")

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle new frame discovery."""
        try:
            service_info = zc.get_service_info(type_, name)
            if not service_info:
                return

            props = self._decode_properties(service_info)

            # The server advertises on the same service type; ignore its own record.
            if props.get('server_type') == 'photo_frame':
                return

            device_id = props.get('device_id')
            if not device_id:
                return

            ip = ''
            if service_info.addresses:
                ip = socket.inet_ntoa(service_info.addresses[0])

            self.frames[device_id] = {
                'device_id': device_id,
                'ip': ip,
                'hostname': service_info.server or '',
                'port': service_info.port,
                'service_name': name,
                'last_seen': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'status': 'online',
                'properties': props,
            }
            logger.info(f"Discovered frame {device_id} at {ip}:{service_info.port}")
        except Exception as e:
            logger.error(f"Error handling service {name}: {e}")

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        """Handle frame updates."""
        self.add_service(zc, type_, name)
