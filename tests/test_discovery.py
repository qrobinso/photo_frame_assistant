"""
Regression tests for mDNS service advertisement (services/discovery.py).

These cover the two failure modes that made frames lose the server after a
day or two:

  1. A single transient network error killed the refresh thread permanently.
  2. The advertised IP was frozen at boot, so a DHCP move or an interface
     change left the server advertising an address it no longer owned.

Run with:  ./venv/bin/python -m unittest discover -s tests -v
"""
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.discovery as disc
from services.discovery import FrameDiscovery


class FakeZeroconf:
    """Records calls so tests can assert on announce/goodbye behaviour."""

    def __init__(self, fail_register=False):
        self.calls = []
        self.fail_register = fail_register
        self.closed = False

    def register_service(self, info, *a, **kw):
        self.calls.append(("register", info))
        if self.fail_register:
            raise OSError(65, "No route to host")

    def unregister_service(self, info):
        self.calls.append(("unregister", info))

    def update_service(self, info):
        self.calls.append(("update", info))

    def unregister_all_services(self):
        self.calls.append(("unregister_all", None))

    def close(self):
        self.closed = True
        self.calls.append(("close", None))

    def names(self):
        return [c[0] for c in self.calls]


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        # Never touch the real network from tests.
        self._real_zc, self._real_browser = disc.Zeroconf, disc.ServiceBrowser
        self.fake = FakeZeroconf()
        disc.Zeroconf = lambda *a, **kw: self.fake
        disc.ServiceBrowser = lambda *a, **kw: None

        self.d = FrameDiscovery(port=5000)
        self.ips = ["192.168.4.184"]
        self.d.get_ip_addresses = lambda: list(self.ips)

    def tearDown(self):
        self.d._running = False
        disc.Zeroconf, disc.ServiceBrowser = self._real_zc, self._real_browser

    def advertised_addresses(self):
        return [socket.inet_ntoa(a) for a in self.d.service_info.addresses]

    # --- Root cause 1: transient error must not kill discovery -------------

    def test_transient_register_error_does_not_kill_the_loop(self):
        """A failed re-register must not terminate maintenance permanently."""
        self.d.setup_service()
        self.fake.fail_register = True
        self.ips = ["192.168.4.207"]          # force a re-register attempt

        self.d._maintenance_tick()            # must swallow the error

        self.assertTrue(self.d._running, "_running must stay True after a blip")

        # Recovery: once the network is back, the next tick re-registers.
        self.fake.fail_register = False
        self.d._maintenance_tick()
        self.assertEqual(self.advertised_addresses(), ["192.168.4.207"])

    def test_stop_from_inside_maintenance_thread_does_not_raise(self):
        """stop() called on the maintenance thread must not self-join."""
        self.d.setup_service()
        error = {}

        def call_stop():
            try:
                self.d.stop()
            except Exception as e:      # RuntimeError: cannot join current thread
                error["err"] = e

        self.d._refresh_thread = threading.Thread(target=call_stop)
        self.d._refresh_thread.start()
        self.d._refresh_thread.join(timeout=5)

        self.assertNotIn("err", error, f"stop() raised: {error.get('err')}")
        self.assertTrue(self.fake.closed, "zeroconf.close() must still run")

    def test_maintenance_thread_survives_repeated_failures(self):
        """The real loop must still be alive after several failed cycles."""
        self.d.refresh_interval = 0.05
        self.d.setup_service()
        self.fake.fail_register = True
        self.ips = ["192.168.4.207"]

        time.sleep(0.6)                       # several cycles elapse
        self.assertTrue(self.d._refresh_thread.is_alive(),
                        "maintenance thread died after transient errors")
        self.assertTrue(self.d._running)

    # --- Root cause 2: advertised address must track the host --------------

    def test_address_change_is_republished(self):
        """A DHCP move must update both the A record and the TXT server_ip."""
        self.d.setup_service()
        self.assertEqual(self.advertised_addresses(), ["192.168.4.184"])

        self.ips = ["192.168.4.207"]          # router hands out a new lease
        self.d._maintenance_tick()

        self.assertEqual(self.advertised_addresses(), ["192.168.4.207"])
        self.assertEqual(
            self.d.service_info.properties[b"server_ip"].decode(), "192.168.4.207"
        )

    def test_all_local_addresses_are_advertised(self):
        """A multi-homed host must advertise every usable address."""
        self.ips = ["192.168.4.184", "192.168.4.50"]
        self.d.setup_service()
        self.assertEqual(
            set(self.advertised_addresses()), {"192.168.4.184", "192.168.4.50"}
        )

    # --- Contributing factor: no goodbye churn -----------------------------

    def test_no_goodbye_packets_when_nothing_changed(self):
        """Steady state must not unregister/re-register (purges frame caches)."""
        self.d.setup_service()
        self.fake.calls.clear()

        for _ in range(5):
            self.d._maintenance_tick()

        self.assertNotIn("unregister", self.fake.names(),
                         "sent mDNS goodbye while the address was unchanged")

    def test_reannounce_happens_without_a_goodbye(self):
        """Periodic re-announce must use update_service, not unregister."""
        self.d.reannounce_interval = 0
        self.d.setup_service()
        self.fake.calls.clear()

        self.d._maintenance_tick()

        self.assertIn("update", self.fake.names(), "expected a re-announce")
        self.assertNotIn("unregister", self.fake.names())

    # --- Health: a dead stack must be rebuilt ------------------------------

    def test_dead_zeroconf_stack_is_rebuilt(self):
        """If the zeroconf instance is gone, maintenance must rebuild it."""
        self.d.setup_service()
        self.d.zeroconf = None                # simulate a collapsed stack

        self.d._maintenance_tick()

        self.assertIsNotNone(self.d.zeroconf, "stack was not rebuilt")
        self.assertTrue(self.d._running)


class AdvertisedAddressFilterTestCase(unittest.TestCase):
    """Root cause 3: container/VPN addresses must never be advertised.

    Observed on a live homelab (Docker host, network_mode: host). Browsing the
    service from the LAN returned three A records:

        photoframe-server-d1593d15.local.  172.17.0.1
        photoframe-server-d1593d15.local.  172.18.0.1
        photoframe-server-d1593d15.local.  192.168.6.235

    172.17.0.1 is docker0 and 172.18.0.1 is a user-defined bridge. Neither is
    reachable from a frame, and the responder does not preserve the order we
    published, so a client that takes the first A record it sees (the ESP32
    firmware calls MDNS.address(0)) usually gets an unroutable address and can
    never reach the server. A laptop has no docker bridges, which is why the
    same build works there.
    """

    class FakeAddr:
        def __init__(self, address, family=socket.AF_INET):
            self.address = address
            self.family = family

    def make(self, candidates, interfaces, **kwargs):
        d = FrameDiscovery(port=5000, **kwargs)
        d._candidate_ip_addresses = lambda: list(candidates)
        disc.psutil = type("psutil", (), {
            "net_if_addrs": staticmethod(lambda: {
                name: [self.FakeAddr(a) for a in addrs]
                for name, addrs in interfaces.items()
            })
        })
        return d

    def setUp(self):
        self._real_psutil = disc.psutil

    def tearDown(self):
        disc.psutil = self._real_psutil

    def test_docker_bridge_addresses_are_not_advertised(self):
        d = self.make(
            candidates=["192.168.6.235", "172.18.0.1", "172.17.0.1"],
            interfaces={
                "eth0": ["192.168.6.235"],
                "docker0": ["172.17.0.1"],
                "br-9f3c1a2b4d5e": ["172.18.0.1"],
            },
        )
        self.assertEqual(d.get_ip_addresses(), ["192.168.6.235"])

    def test_vpn_and_virtual_interfaces_are_not_advertised(self):
        d = self.make(
            candidates=["192.168.6.235", "10.8.0.2", "100.64.1.5"],
            interfaces={
                "eth0": ["192.168.6.235"],
                "tun0": ["10.8.0.2"],
                "tailscale0": ["100.64.1.5"],
            },
        )
        self.assertEqual(d.get_ip_addresses(), ["192.168.6.235"])

    def test_a_genuine_second_nic_is_still_advertised(self):
        """Filtering must not undo multi-homed support."""
        d = self.make(
            candidates=["192.168.6.235", "192.168.9.10", "172.17.0.1"],
            interfaces={
                "eth0": ["192.168.6.235"],
                "wlan0": ["192.168.9.10"],
                "docker0": ["172.17.0.1"],
            },
        )
        self.assertEqual(d.get_ip_addresses(),
                         ["192.168.6.235", "192.168.9.10"])

    def test_filtering_never_leaves_nothing_to_advertise(self):
        """If every address looks virtual, advertise them rather than vanish."""
        d = self.make(
            candidates=["172.17.0.1"],
            interfaces={"docker0": ["172.17.0.1"]},
        )
        self.assertEqual(d.get_ip_addresses(), ["172.17.0.1"])

    def test_interface_lookup_failure_falls_back_to_all_candidates(self):
        d = self.make(candidates=["192.168.6.235"], interfaces={})

        def boom():
            raise OSError("no /proc/net")

        disc.psutil.net_if_addrs = staticmethod(boom)
        self.assertEqual(d.get_ip_addresses(), ["192.168.6.235"])

    def test_explicit_override_replaces_autodetection(self):
        """The escape hatch for hosts we cannot reason about."""
        d = self.make(
            candidates=["172.17.0.1"],
            interfaces={"docker0": ["172.17.0.1"]},
            advertise_ips=["192.168.6.235"],
        )
        self.assertEqual(d.get_ip_addresses(), ["192.168.6.235"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
