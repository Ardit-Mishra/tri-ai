"""The recruiter demo must remain synthetic, self-contained, and read-only."""

from __future__ import annotations

import json
import runpy
import copy
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEMO = runpy.run_path(str(ROOT / "public-demo" / "serve.py"))


class PublicCortexDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DEMO["make_server"]("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_page_and_snapshot_are_sealed(self):
        with urlopen(self.base + "/") as response:
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("connect-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("Kaya Cortex Lab", page)
        self.assertIn('id="demoSnapshot"', page)
        with urlopen(self.base + "/api/snapshot") as response:
            snapshot = json.load(response)
        self.assertIs(snapshot["demo"], True)
        self.assertIs(snapshot["sealed"], True)
        self.assertIs(snapshot["file_graph"]["synthetic"], True)
        self.assertEqual(len(snapshot["tasks"]), 4)
        self.assertTrue(all(item["synthetic"] for item in snapshot["file_graph"]["items"]))

    def test_private_and_action_routes_do_not_exist(self):
        for route in (
            "/api/file-graph/scene", "/api/file-graph/children?source=desktop",
            "/artifact/demo_build/0", "/api/tasks", "/private", "/.env",
        ):
            with self.subTest(route=route), self.assertRaises(HTTPError) as caught:
                urlopen(self.base + route)
            self.assertEqual(caught.exception.code, 404)

    def test_refuses_non_demo_or_non_synthetic_content(self):
        parse = DEMO["sealed_snapshot"]
        original = parse((ROOT / "public-demo" / "index.html").read_bytes())
        def encoded(snapshot):
            return ('<script id="demoSnapshot">' + json.dumps(snapshot) + '</script>').encode()

        wrong_demo = copy.deepcopy(original)
        wrong_demo["demo"] = False
        with self.assertRaises(ValueError):
            parse(encoded(wrong_demo))

        wrong_graph = copy.deepcopy(original)
        wrong_graph["file_graph"]["synthetic"] = False
        with self.assertRaises(ValueError):
            parse(encoded(wrong_graph))

        wrong_item = copy.deepcopy(original)
        wrong_item["file_graph"]["items"][0]["synthetic"] = False
        with self.assertRaises(ValueError):
            parse(encoded(wrong_item))

        wrong_task = copy.deepcopy(original)
        wrong_task["tasks"][0]["id"] = "private-task"
        with self.assertRaises(ValueError):
            parse(encoded(wrong_task))


if __name__ == "__main__":
    unittest.main()
