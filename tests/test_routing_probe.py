"""Adversarial proofs for the evidence-only local routing probe."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import routing_probe  # noqa: E402


def policy() -> routing_probe.RoutePolicy:
    return routing_probe.policy_from_mapping({
        "version": "test-policy-v1",
        "routes": {
            "code": {
                "primary": {
                    "name": "omniroute",
                    "endpoint": "http://127.0.0.1:20128/v1",
                    "model": "primary-model",
                },
                "fallback": {
                    "name": "ollama",
                    "endpoint": "http://127.0.0.1:11434/v1",
                    "model": "fallback-model",
                },
            },
        },
    })


class RoutingProbeTests(unittest.TestCase):
    def test_unknown_task_kind_is_refused_before_transport(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "unknown task kind"):
            routing_probe.probe(policy(), "unknown", lambda route: calls.append(route) or 200)
        self.assertEqual(calls, [])

    def test_rate_limit_timeout_and_connection_error_each_fall_back_once(self):
        for error in (429, TimeoutError(), OSError("offline")):
            with self.subTest(error=type(error).__name__):
                calls = []

                def transport(route):
                    calls.append(route.name)
                    if len(calls) == 1:
                        if isinstance(error, BaseException):
                            raise error
                        return error
                    return 200

                result = routing_probe.probe(policy(), "code", transport)
                self.assertEqual(calls, ["omniroute", "ollama"])
                self.assertEqual(len(result.attempts), 2)
                self.assertIn(result.attempts[0].result, routing_probe.FALLBACK_REASONS)
                self.assertEqual(result.attempts[1].fallback_reason, result.attempts[0].result)
                self.assertEqual(result.attempts[1].result, "responded")

    def test_non_environment_http_failure_does_not_invent_a_fallback(self):
        calls = []
        result = routing_probe.probe(
            policy(), "code", lambda route: calls.append(route.name) or 503
        )
        self.assertEqual(calls, ["omniroute"])
        self.assertEqual([attempt.result for attempt in result.attempts], ["http_error"])

    def test_policy_rejects_nonlocal_non_v1_and_credential_bearing_endpoints(self):
        for endpoint in (
            "https://example.com/v1",
            "http://127.0.0.1:20128/not-v1",
            "http://token@127.0.0.1:20128/v1",
        ):
            with self.subTest(endpoint=endpoint):
                raw = {
                    "version": "test",
                    "routes": {
                        "code": {
                            "primary": {"name": "bad", "endpoint": endpoint, "model": "m"},
                            "fallback": {
                                "name": "local",
                                "endpoint": "http://127.0.0.1:11434/v1",
                                "model": "m",
                            },
                        },
                    },
                }
                with self.assertRaises(ValueError):
                    routing_probe.policy_from_mapping(raw)

    def test_policy_refuses_a_fallback_that_is_the_primary_in_disguise(self):
        raw = {
            "version": "test",
            "routes": {
                "code": {
                    "primary": {
                        "name": "primary",
                        "endpoint": "http://127.0.0.1:20128/v1",
                        "model": "one",
                    },
                    "fallback": {
                        "name": "fallback",
                        "endpoint": "http://127.0.0.1:20128/v1",
                        "model": "two",
                    },
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "differ"):
            routing_probe.policy_from_mapping(raw)

    def test_policy_version_and_both_attempts_are_durable_diagnostic_evidence(self):
        result = routing_probe.probe(policy(), "code", lambda route: 429 if route.name == "omniroute" else 200)
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "routing.jsonl"
            routing_probe.record_evidence(result, evidence)
            recorded = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(recorded["kind"], "routing_probe")
        self.assertFalse(recorded["accepted"])
        self.assertEqual(recorded["policy_version"], "test-policy-v1")
        self.assertEqual([item["route"] for item in recorded["attempts"]], ["omniroute", "ollama"])


class ProbeBoundaryTests(unittest.TestCase):
    def test_probe_has_no_board_worker_or_executor_import_or_completion_call(self):
        source = Path(routing_probe.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue({"board", "worker", "executor"}.isdisjoint(imported))
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertNotIn("complete_task", attributes)


if __name__ == "__main__":
    unittest.main()
