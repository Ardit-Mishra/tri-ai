"""Proofs for exact, local-only route selection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model_routes  # noqa: E402


class ModelRoutingPolicyTest(unittest.TestCase):
    def test_role_route_uses_the_resolved_model_identity(self):
        policy = model_routes.policy_from_mapping({
            "version": "test-routes-v1",
            "default_route": "local-coder",
            "role_routes": {"designer": "local-vision"},
            "routes": {
                "local-coder": {
                    "model": "qwen2.5-coder:7b",
                    "provider": None,
                    "admitted": True,
                },
                "local-vision": {
                    "model": "gemma3:12b",
                    "provider": None,
                    "admitted": True,
                },
            },
        })

        self.assertEqual(
            model_routes.select(policy, "designer").model,
            "gemma3:12b",
        )
        self.assertEqual(
            model_routes.select(policy, "builder").model,
            "qwen2.5-coder:7b",
        )

