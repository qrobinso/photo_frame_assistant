"""
Regression tests for frame-side discovery (FrameListener in services/discovery.py).

The original listener was constructed with FrameDiscovery.discovered_frames — a
dict — but called self.queue.put() on it. Every frame that advertised itself
raised AttributeError inside the Zeroconf callback thread, so
/api/discovered_frames always returned an empty list.

Run with:  ./venv/bin/python -m unittest discover -s tests -v
"""
import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.discovery import FrameDiscovery, FrameListener

SERVICE_TYPE = "_photoframe._tcp.local."


class FakeServiceInfo:
    def __init__(self, properties, addresses=None, port=8080, server="frame.local."):
        self.properties = properties
        self.addresses = addresses if addresses is not None else [socket.inet_aton("192.168.4.77")]
        self.port = port
        self.server = server


class FakeZeroconf:
    """Serves ServiceInfo by name, the way zeroconf does during a browse."""

    def __init__(self, records=None):
        self.records = records or {}

    def get_service_info(self, type_, name, *a, **kw):
        return self.records.get(name)


def frame_props(device_id="frame-abc", **extra):
    props = {b"device_id": device_id.encode(), b"model": b"VF-1200"}
    props.update(extra)
    return props


class FrameListenerTestCase(unittest.TestCase):
    def setUp(self):
        self.frames = {}
        self.listener = FrameListener(self.frames)

    def add(self, name, info):
        zc = FakeZeroconf({name: info})
        self.listener.add_service(zc, SERVICE_TYPE, name)

    # --- Root cause: the callback used to raise on every advertisement -----

    def test_advertised_frame_is_recorded_by_device_id(self):
        """A frame's advertisement must land in the shared dict, not raise."""
        self.add("frame-abc." + SERVICE_TYPE, FakeServiceInfo(frame_props()))

        self.assertIn("frame-abc", self.frames)
        entry = self.frames["frame-abc"]
        self.assertEqual(entry["ip"], "192.168.4.77")
        self.assertEqual(entry["port"], 8080)
        self.assertEqual(entry["hostname"], "frame.local.")
        self.assertEqual(entry["status"], "online")
        self.assertEqual(entry["properties"]["model"], "VF-1200")

    def test_get_discovered_frames_returns_the_advertised_frame(self):
        """The API shape /api/discovered_frames serves must include the frame."""
        d = FrameDiscovery(port=5000)
        listener = FrameListener(d.discovered_frames)
        zc = FakeZeroconf({"frame-abc." + SERVICE_TYPE: FakeServiceInfo(frame_props())})
        listener.add_service(zc, SERVICE_TYPE, "frame-abc." + SERVICE_TYPE)

        result = d.get_discovered_frames()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["device_id"], "frame-abc")
        self.assertEqual(result[0]["ip"], "192.168.4.77")

    # --- The server browses the type it advertises on ----------------------

    def test_servers_own_record_is_ignored(self):
        """The server must not list itself as a discovered frame."""
        server_props = {
            b"server_type": b"photo_frame",
            b"server_id": b"0b971666",
            b"server_ip": b"192.168.4.184",
        }
        self.add("PhotoFrame-Server-0b971666." + SERVICE_TYPE,
                 FakeServiceInfo(server_props))

        self.assertEqual(self.frames, {})

    def test_record_without_device_id_is_ignored(self):
        """Some other _photoframe service must not create a junk entry."""
        self.add("mystery." + SERVICE_TYPE, FakeServiceInfo({b"model": b"VF-1200"}))
        self.assertEqual(self.frames, {})

    def test_missing_service_info_is_ignored(self):
        """A record that vanished before we resolved it must not raise."""
        self.listener.add_service(FakeZeroconf(), SERVICE_TYPE, "gone." + SERVICE_TYPE)
        self.assertEqual(self.frames, {})

    # --- A callback must never raise into the Zeroconf thread --------------

    def test_undecodable_properties_do_not_kill_the_callback(self):
        """Non-UTF8 TXT values must be tolerated, not propagated."""
        props = frame_props()
        props[b"junk"] = b"\xff\xfe"
        props[b"empty"] = None
        self.add("frame-abc." + SERVICE_TYPE, FakeServiceInfo(props))

        self.assertIn("frame-abc", self.frames)
        self.assertEqual(self.frames["frame-abc"]["properties"]["junk"], "")
        self.assertEqual(self.frames["frame-abc"]["properties"]["empty"], "")

    def test_frame_with_no_addresses_is_still_recorded(self):
        """An A-record-less advertisement must not crash the callback."""
        self.add("frame-abc." + SERVICE_TYPE,
                 FakeServiceInfo(frame_props(), addresses=[]))
        self.assertEqual(self.frames["frame-abc"]["ip"], "")

    # --- Updates and removals ---------------------------------------------

    def test_update_refreshes_the_existing_entry(self):
        """A frame that moves must update in place, not duplicate."""
        name = "frame-abc." + SERVICE_TYPE
        self.add(name, FakeServiceInfo(frame_props()))
        zc = FakeZeroconf({name: FakeServiceInfo(
            frame_props(), addresses=[socket.inet_aton("192.168.4.99")])})
        self.listener.update_service(zc, SERVICE_TYPE, name)

        self.assertEqual(len(self.frames), 1)
        self.assertEqual(self.frames["frame-abc"]["ip"], "192.168.4.99")

    def test_remove_service_drops_the_matching_frame(self):
        """Going offline must remove that frame and leave the others alone."""
        self.add("frame-abc." + SERVICE_TYPE, FakeServiceInfo(frame_props()))
        self.add("frame-xyz." + SERVICE_TYPE, FakeServiceInfo(frame_props("frame-xyz")))

        self.listener.remove_service(FakeZeroconf(), SERVICE_TYPE,
                                     "frame-abc." + SERVICE_TYPE)

        self.assertNotIn("frame-abc", self.frames)
        self.assertIn("frame-xyz", self.frames)

    def test_remove_of_unknown_service_is_a_noop(self):
        """A goodbye for something we never recorded must not raise."""
        self.add("frame-abc." + SERVICE_TYPE, FakeServiceInfo(frame_props()))
        self.listener.remove_service(FakeZeroconf(), SERVICE_TYPE,
                                     "never-seen." + SERVICE_TYPE)
        self.assertIn("frame-abc", self.frames)


if __name__ == "__main__":
    unittest.main(verbosity=2)
