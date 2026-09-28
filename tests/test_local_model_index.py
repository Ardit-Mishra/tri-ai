"""Contracts for the private, metadata-only local model source."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import local_model_index  # noqa: E402


class LocalModelIndexTests(unittest.TestCase):
    def test_ollama_inventory_becomes_a_valid_private_source_without_endpoint_leaks(self):
        payload = json.dumps({
            "models": [
                {"name": "qwen2.5-coder:7b", "size": 4_000_000_000},
                {"name": "small-research-model:latest", "size": 2_000_000_000},
            ]
        }).encode("utf-8")
        response = mock.MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response

        with mock.patch.object(local_model_index.request, "urlopen", return_value=response):
            index = local_model_index.ollama_inventory("http://private-loopback:11434")

        self.assertEqual(index["status"], "indexed")
        self.assertEqual(index["sources"][0]["id"], "ollama")
        self.assertEqual(index["sources"][0]["node_count"], 3)
        self.assertEqual([item["relative_path"] for item in index["items"]], [".", "models/qwen2.5-coder:7b", "models/small-research-model:latest"])
        self.assertNotIn("private-loopback", json.dumps(index))
        for item in index["items"]:
            datetime.fromisoformat(item["modified_at"].replace("Z", "+00:00"))

    def test_unavailable_ollama_is_an_honest_empty_source(self):
        with mock.patch.object(local_model_index.request, "urlopen", side_effect=OSError("unavailable")):
            index = local_model_index.ollama_inventory("http://private-loopback:11434")

        self.assertEqual(index["status"], "unavailable")
        self.assertEqual(index["item_count"], 0)
        self.assertEqual(index["sources"][0]["id"], "ollama")
        self.assertNotIn("private-loopback", json.dumps(index))


if __name__ == "__main__":
    unittest.main()
