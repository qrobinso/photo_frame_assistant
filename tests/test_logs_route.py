"""
Tests for the server log viewer (helpers/log_helpers.py and the
/api/server/logs routes on the Settings page).

Run with:  ./venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from helpers.log_helpers import read_log_tail
from routes.system import system_bp


def entry(n, level="INFO", msg=None):
    return f"2026-09-23 21:25:{n:02d},000 - services.test - {level} - {msg or f'line {n}'}\n"


TRACEBACK = (
    entry(3, "ERROR", "Error in setup_service: boom")
    + "Traceback (most recent call last):\n"
    + '  File "services/discovery.py", line 287, in _build_service_info\n'
    + "OSError: illegal IP address string passed to inet_aton\n"
)


class LogTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "server.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)


class ReadLogTailTestCase(LogTestCase):
    def test_returns_last_n_lines_in_order(self):
        self.write("".join(entry(i) for i in range(50)))
        lines = read_log_tail(self.path, max_lines=3)
        self.assertEqual([l.split(" - ")[-1] for l in lines],
                         ["line 47", "line 48", "line 49"])

    def test_missing_file_is_empty(self):
        self.assertEqual(read_log_tail(os.path.join(self.dir, "nope.log")), [])

    def test_level_filter_keeps_that_level_and_above(self):
        self.write(entry(1, "DEBUG") + entry(2, "INFO") + entry(3, "WARNING")
                   + entry(4, "ERROR") + entry(5, "CRITICAL"))
        lines = read_log_tail(self.path, min_level="WARNING")
        self.assertEqual([l.split(" - ")[2] for l in lines],
                         ["WARNING", "ERROR", "CRITICAL"])

    def test_traceback_stays_with_its_entry_when_filtering(self):
        self.write(entry(1, "INFO") + TRACEBACK + entry(4, "INFO"))
        lines = read_log_tail(self.path, min_level="ERROR")
        self.assertEqual(len(lines), 4)
        self.assertIn("Error in setup_service", lines[0])
        self.assertIn("inet_aton", lines[-1])

    def test_traceback_of_a_dropped_entry_is_dropped_too(self):
        self.write(entry(1, "INFO", "noise") + "  continuation of noise\n"
                   + entry(2, "ERROR"))
        lines = read_log_tail(self.path, min_level="ERROR")
        self.assertEqual(len(lines), 1)
        self.assertNotIn("continuation", lines[0])

    def test_undecodable_bytes_do_not_break_reading(self):
        with open(self.path, "wb") as f:
            f.write(entry(1).encode() + b"\xff\xfe bad bytes\n" + entry(2).encode())
        self.assertEqual(len(read_log_tail(self.path)), 3)


class LogRoutesTestCase(LogTestCase):
    def setUp(self):
        super().setUp()
        app = Flask(__name__)
        app.register_blueprint(system_bp)
        self.client = app.test_client()
        self.env = mock.patch.dict(os.environ, {"LOG_PATH": self.dir})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_logs_endpoint_returns_tail(self):
        self.write("".join(entry(i) for i in range(10)))
        data = self.client.get("/api/server/logs?lines=200").get_json()
        self.assertEqual(len(data["lines"]), 10)
        self.assertEqual(data["file"], "server.log")
        self.assertGreater(data["size"], 0)

    def test_logs_endpoint_filters_by_level(self):
        self.write(entry(1, "INFO") + entry(2, "WARNING"))
        data = self.client.get("/api/server/logs?level=WARNING").get_json()
        self.assertEqual(len(data["lines"]), 1)

    def test_bad_parameters_are_clamped(self):
        self.write("".join(entry(i % 60) for i in range(2500)))
        r = self.client.get("/api/server/logs?lines=999999&level=BOGUS")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["lines"]), 2000)
        r = self.client.get("/api/server/logs?lines=abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["lines"]), 500)

    def test_missing_log_file_is_empty_not_an_error(self):
        data = self.client.get("/api/server/logs").get_json()
        self.assertEqual(data["lines"], [])
        self.assertEqual(data["size"], 0)

    def test_download_sends_the_file(self):
        self.write(entry(1) + entry(2))
        r = self.client.get("/api/server/logs/download")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(r.data.decode(), entry(1) + entry(2))
        r.close()

    def test_download_missing_file_is_404(self):
        self.assertEqual(self.client.get("/api/server/logs/download").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
