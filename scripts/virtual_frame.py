#!/usr/bin/env python3
"""
Virtual photo frame — simulates a physical frame device against the server.

Exercises the frame-facing hardware API in routes/frame_client.py:

  1. Discovery   — browse Zeroconf for _photoframe._tcp.local. to find the server
                   (or use --server to skip discovery)
  2. Register    — POST /api/register_frame with device properties/capabilities
  3. Settings    — GET  /api/settings?device_id=...  (sleep interval, orientation)
  4. Photo       — GET  /api/next_photo?device_id=...&type=...  (saved to disk)
  5. Diagnostic  — POST /api/diagnostic with battery + next_wake
  6. Sleep       — wait sleep_interval minutes, repeat from step 3

Examples:
    python scripts/virtual_frame.py --discover-only
    python scripts/virtual_frame.py --server http://localhost:5000 --once
    python scripts/virtual_frame.py --cycles 3 --sleep-override 5
    python scripts/virtual_frame.py --type rgb565 --orientation landscape --epaper
"""
import argparse
import io
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("requests is required:  pip install requests")

SERVICE_TYPE = "_photoframe._tcp.local."
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".virtual_frame_id")


# --------------------------------------------------------------------------- log
def log(step, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {step:<10} {msg}", flush=True)


def fail(step, msg):
    log(step, f"FAIL  {msg}")


# ----------------------------------------------------------------------- identity
def get_device_id(explicit=None, persist=True):
    """Stable device id across runs so the server sees the same frame."""
    if explicit:
        return explicit
    if persist and os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = f.read().strip()
            if saved:
                return saved
    device_id = f"virtual-{uuid.uuid4().hex[:8]}"
    if persist:
        try:
            with open(STATE_FILE, "w") as f:
                f.write(device_id)
        except OSError:
            pass
    return device_id


# ---------------------------------------------------------------------- discovery
def discover_server(timeout=10.0):
    """Browse mDNS for the photo frame server. Returns (url, properties) or (None, None)."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        fail("discover", "zeroconf not installed (pip install zeroconf)")
        return None, None

    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=3000)
            if not info:
                return
            props = {k.decode(): v.decode() for k, v in (info.properties or {}).items()
                     if k and v is not None}
            if props.get("server_type") != "photo_frame":
                log("discover", f"ignoring non-server service: {name}")
                return
            ip = props.get("server_ip") or (
                socket.inet_ntoa(info.addresses[0]) if info.addresses else None)
            port = props.get("server_port") or info.port
            found[name] = (f"http://{ip}:{port}", props)
            log("discover", f"found {name} -> http://{ip}:{port} "
                            f"(server_id={props.get('server_id')}, v{props.get('version')})")

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            log("discover", f"service removed: {name}")

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE_TYPE, Listener())
        log("discover", f"browsing {SERVICE_TYPE} for up to {timeout:.0f}s ...")
        deadline = time.time() + timeout
        while time.time() < deadline and not found:
            time.sleep(0.25)
    finally:
        zc.close()

    if not found:
        fail("discover", "no photo frame server advertised on the network")
        return None, None
    return next(iter(found.values()))


# -------------------------------------------------------------------- advertise
def local_ip_for(server_url):
    """Best-effort local IP the server would see us on."""
    from urllib.parse import urlparse
    host = urlparse(server_url).hostname or "8.8.8.8"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def advertise(device_id, name, resolution, server_url, port=8080):
    """Advertise this virtual frame over mDNS so the server can discover it.

    Returns (zeroconf, service_info) to unregister later, or (None, None).
    """
    try:
        from zeroconf import Zeroconf, ServiceInfo
    except ImportError:
        fail("advertise", "zeroconf not installed (pip install zeroconf)")
        return None, None

    ip = local_ip_for(server_url)
    props = {
        "device_id": device_id,
        "name": name,
        "manufacturer": "VirtualFrames Inc.",
        "model": "VF-1200",
        "screen_resolution": resolution,
        "firmware_rev": "1.0.0",
    }
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{device_id}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={k.encode(): v.encode() for k, v in props.items()},
        server=f"{device_id}.local.",
    )
    zc = Zeroconf()
    try:
        zc.register_service(info)
    except Exception as e:
        fail("advertise", str(e))
        zc.close()
        return None, None
    log("advertise", f"advertising {device_id} at {ip}:{port} on {SERVICE_TYPE}")
    return zc, info


# ------------------------------------------------------------------- API sequence
class VirtualFrame:
    def __init__(self, args, server):
        self.args = args
        self.server = server.rstrip("/")
        self.device_id = args.device_id
        self.orientation = args.orientation
        self.sleep_interval = args.sleep_override or 1.0
        self.battery = 92.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "VirtualPhotoFrame/1.0"
        self.outdir = args.output_dir
        os.makedirs(self.outdir, exist_ok=True)
        self.failures = 0

    def url(self, path):
        return f"{self.server}{path}"

    # -- step 2 ------------------------------------------------------------
    def register(self):
        capabilities = {
            "display_type": "e-paper" if self.args.epaper else "lcd",
            "colors": 7 if self.args.epaper else 16777216,
            "supports_video": not self.args.epaper,
        }
        payload = {
            "device_id": self.device_id,
            "name": self.args.name,
            "properties": {
                "manufacturer": "VirtualFrames Inc.",
                "model": "VF-1200",
                "hardware_rev": "rev-A",
                "firmware_rev": "1.0.0",
                "screen_resolution": self.args.resolution,
                "aspect_ratio": "3:4",
                "os": "virtual-linux",
                "battery_level": self.battery,
                "capabilities": capabilities,
                "diagnostic_info": {"boot_reason": "power_on", "wifi_rssi": -54},
            },
        }
        try:
            r = self.session.post(self.url("/api/register_frame"), json=payload, timeout=15)
        except requests.RequestException as e:
            fail("register", str(e))
            self.failures += 1
            return False
        if r.status_code != 200:
            fail("register", f"HTTP {r.status_code}: {r.text[:200]}")
            self.failures += 1
            return False
        body = r.json()
        log("register", f"ok  device_id={self.device_id}  playlist_count="
                        f"{body.get('playlist_count')}  show_welcome={body.get('show_welcome')}")
        return True

    # -- step 3 ------------------------------------------------------------
    def fetch_settings(self):
        try:
            r = self.session.get(self.url("/api/settings"),
                                 params={"device_id": self.device_id}, timeout=15)
        except requests.RequestException as e:
            fail("settings", str(e))
            self.failures += 1
            return None
        if r.status_code != 200:
            fail("settings", f"HTTP {r.status_code}: {r.text[:200]}")
            self.failures += 1
            return None
        s = r.json()
        self.orientation = s.get("orientation") or self.orientation
        if not self.args.sleep_override:
            self.sleep_interval = float(s.get("sleep_interval") or 1.0)
        log("settings", f"sleep={s.get('sleep_interval')}min ({s.get('sleep_reason')})  "
                        f"orientation={s.get('orientation')}  shuffle={s.get('shuffle_enabled')}  "
                        f"photos={s.get('playlist_count')}  server_time={s.get('server_time')}")
        if self.args.verbose:
            log("settings", json.dumps(s, indent=2))
        return s

    # -- step 4 ------------------------------------------------------------
    def fetch_photo(self, cycle):
        endpoint = "/api/current_photo" if self.args.endpoint == "current" else "/api/next_photo"
        params = {"device_id": self.device_id}
        if self.args.type:
            params["type"] = self.args.type
        t0 = time.time()
        try:
            r = self.session.get(self.url(endpoint), params=params, timeout=60)
        except requests.RequestException as e:
            fail("photo", str(e))
            self.failures += 1
            return None
        elapsed = time.time() - t0
        if r.status_code != 200:
            fail("photo", f"HTTP {r.status_code}: {r.text[:200]}")
            self.failures += 1
            return None

        data = r.content
        ctype = r.headers.get("Content-Type", "")
        photo_id = r.headers.get("X-Photo-ID", "-")
        photo_name = r.headers.get("X-Photo-Filename", "-")

        ext = {"image/jpeg": "jpg", "video/mp4": "mp4"}.get(ctype.split(";")[0], "bin")
        path = os.path.join(self.outdir, f"{self.device_id}_cycle{cycle}.{ext}")
        with open(path, "wb") as f:
            f.write(data)

        detail = ""
        if ext == "jpg":
            detail = self._describe_jpeg(data)
        elif ext == "bin":
            detail = f"raw {self.args.type} payload"
        log("photo", f"{endpoint} -> {len(data)} bytes in {elapsed:.2f}s  "
                     f"[{ctype}] id={photo_id} name={photo_name} {detail}")
        log("photo", f"saved {path}")
        self._sanity_check(data, ctype, ext)
        return data

    def _describe_jpeg(self, data):
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                return f"{img.width}x{img.height} {img.mode}"
        except Exception:
            return "(PIL unavailable or not a decodable image)"

    def _sanity_check(self, data, ctype, ext):
        if not data:
            fail("verify", "empty response body")
            self.failures += 1
            return
        if ext == "jpg" and not data.startswith(b"\xff\xd8"):
            fail("verify", "Content-Type is image/jpeg but body is not a JPEG (missing SOI marker)")
            self.failures += 1
            return
        if self.args.type in ("compressed", "epaper", "epd", "rgb565") and ext != "bin":
            fail("verify", f"requested type={self.args.type} but got {ctype}")
            self.failures += 1
            return
        log("verify", "payload looks valid")

    # -- step 5 ------------------------------------------------------------
    def send_diagnostic(self):
        self.battery = max(1.0, self.battery - 0.7)
        next_wake = datetime.now(timezone.utc) + timedelta(minutes=self.sleep_interval)
        payload = {
            "device_id": self.device_id,
            "battery_level": round(self.battery, 1),
            "next_wake": next_wake.isoformat().replace("+00:00", "Z"),
            "wifi_rssi": -57,
            "uptime_s": int(time.time()) % 100000,
            "free_heap": 148_000,
            "capabilities": {
                "display_type": "e-paper" if self.args.epaper else "lcd",
                "colors": 7 if self.args.epaper else 16777216,
            },
        }
        try:
            r = self.session.post(self.url("/api/diagnostic"), json=payload, timeout=15)
        except requests.RequestException as e:
            fail("diagnostic", str(e))
            self.failures += 1
            return
        if r.status_code != 200:
            fail("diagnostic", f"HTTP {r.status_code}: {r.text[:200]}")
            self.failures += 1
            return
        log("diagnostic", f"ok  battery={payload['battery_level']}%  "
                          f"next_wake={payload['next_wake']}")

    # -- extra -------------------------------------------------------------
    def check_server_time(self):
        try:
            r = self.session.get(self.url("/api/server-time"), timeout=10)
            if r.status_code == 200:
                log("time", json.dumps(r.json()))
            else:
                fail("time", f"HTTP {r.status_code}")
                self.failures += 1
        except requests.RequestException as e:
            fail("time", str(e))
            self.failures += 1

    def check_discovered(self):
        """Confirm the server saw our mDNS advertisement."""
        try:
            r = self.session.get(self.url("/api/discovered_frames"), timeout=10)
        except requests.RequestException as e:
            fail("discovered", str(e))
            self.failures += 1
            return
        if r.status_code != 200:
            fail("discovered", f"HTTP {r.status_code}: {r.text[:200]}")
            self.failures += 1
            return
        frames = r.json()
        mine = [f for f in frames if f.get("device_id") == self.device_id]
        log("discovered", f"server lists {len(frames)} frame(s); ours present: {bool(mine)}")
        if mine:
            log("discovered", json.dumps(mine[0]))
        elif self.args.advertise:
            fail("discovered", "we advertised but the server has not listed us yet")
            self.failures += 1

    # -- loop --------------------------------------------------------------
    def run(self):
        log("server", f"using {self.server}")
        self.check_server_time()
        if not self.register():
            return 1
        if self.args.advertise:
            log("discovered", "waiting 5s for the server's mDNS browser to pick us up ...")
            time.sleep(5)
            self.check_discovered()

        cycle = 0
        while self.args.cycles == 0 or cycle < self.args.cycles:
            cycle += 1
            log("cycle", f"--- wake #{cycle} ---")
            self.fetch_settings()
            self.fetch_photo(cycle)
            self.send_diagnostic()

            if self.args.cycles and cycle >= self.args.cycles:
                break
            wait_s = self.sleep_interval * 60
            if self.args.fast:
                wait_s = min(wait_s, 5)
            log("sleep", f"sleeping {wait_s:.0f}s (server said {self.sleep_interval} min)")
            try:
                time.sleep(wait_s)
            except KeyboardInterrupt:
                break

        log("done", f"{cycle} cycle(s) complete, {self.failures} failure(s)")
        return 1 if self.failures else 0


# ---------------------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", help="Server base URL; skips Zeroconf discovery")
    p.add_argument("--discover-only", action="store_true",
                   help="Only run Zeroconf discovery and exit")
    p.add_argument("--discover-timeout", type=float, default=10.0)
    p.add_argument("--device-id", help="Override device id (default: persisted random id)")
    p.add_argument("--no-persist", action="store_true",
                   help="Do not reuse/save the device id between runs")
    p.add_argument("--name", default="Virtual Test Frame")
    p.add_argument("--resolution", default="1200x1600")
    p.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    p.add_argument("--epaper", action="store_true",
                   help="Advertise as an e-paper display (affects rgb565/epaper output)")
    p.add_argument("--endpoint", default="next", choices=["next", "current"],
                   help="Which photo endpoint to exercise (default: next)")
    p.add_argument("--type", default=None,
                   choices=["compressed", "rgb565", "epaper", "epd"],
                   help="Requested output encoding; omit for plain JPEG")
    p.add_argument("--cycles", type=int, default=1,
                   help="Number of wake cycles; 0 = run forever (default: 1)")
    p.add_argument("--once", action="store_true", help="Alias for --cycles 1")
    p.add_argument("--sleep-override", type=float,
                   help="Ignore the server's sleep interval, use this many minutes")
    p.add_argument("--fast", action="store_true",
                   help="Cap sleeps at 5s so multi-cycle runs finish quickly")
    p.add_argument("--output-dir", default="/tmp/virtual_frame",
                   help="Where fetched photos are written (default: /tmp/virtual_frame)")
    p.add_argument("--advertise", action="store_true",
                   help="Also advertise this frame over mDNS so the server discovers it "
                        "(exercises /api/discovered_frames)")
    p.add_argument("--advertise-port", type=int, default=8080)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.once:
        args.cycles = 1

    if args.discover_only:
        url, props = discover_server(args.discover_timeout)
        if not url:
            return 1
        print(json.dumps({"server": url, "properties": props}, indent=2))
        return 0

    server = args.server
    if not server:
        server, _ = discover_server(args.discover_timeout)
        if not server:
            fail("discover", "use --server http://host:5000 to skip discovery")
            return 1

    args.device_id = get_device_id(args.device_id, persist=not args.no_persist)

    zc = info = None
    if args.advertise:
        zc, info = advertise(args.device_id, args.name, args.resolution,
                             server, args.advertise_port)
    try:
        return VirtualFrame(args, server).run()
    finally:
        if zc and info:
            try:
                zc.unregister_service(info)
            finally:
                zc.close()
            log("advertise", "unregistered mDNS service")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
