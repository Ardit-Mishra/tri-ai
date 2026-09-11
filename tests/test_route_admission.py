"""Adversarial proofs for verifier-evidence route admission."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import route_admission  # noqa: E402


def policy() -> route_admission.AdmissionPolicy:
    return route_admission.policy_from_mapping({
        "version": "route-admission-v1",
        "task_kinds": {
            "code": {
                "route": "omniroute-local",
                "model": "approved-model",
                "max_resident_models": 1,
                "max_vram_mib": 6000,
            },
        },
    })


def evidence(
    fixture_id: str,
    *,
    exit_code: int = 0,
    route: str = "omniroute-local",
    model: str = "approved-model",
    loaded_models: tuple[str, ...] = ("approved-model",),
    peak_vram_mib: int = 5000,
    report: bool = True,
) -> route_admission.VerificationEvidence:
    return route_admission.VerificationEvidence(
        fixture_id=fixture_id,
        verify_exit=exit_code,
        seconds=1.0,
        resolved_route=route,
        resolved_model=model,
        loaded_models=loaded_models,
        peak_vram_mib=peak_vram_mib,
        agent_reported_success=report,
    )


class RouteAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = (evidence("one"), evidence("two"))
        self.candidate = (evidence("one"), evidence("two"))

    def test_matching_binary_verifier_evidence_is_admitted(self):
        result = route_admission.admit(policy(), "code", self.baseline, self.candidate)
        self.assertTrue(result.admitted)
        self.assertEqual((result.route, result.model), ("omniroute-local", "approved-model"))

    def test_nonzero_verifier_refuses_even_when_the_agent_claims_success(self):
        candidate = (evidence("one"), evidence("two", exit_code=7, report=True))
        result = route_admission.admit(policy(), "code", self.baseline, candidate)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate verifier did not pass every fixture")

    def test_unknown_kind_and_duplicate_fixture_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown task kind"):
            route_admission.admit(policy(), "unknown", self.baseline, self.candidate)
        with self.assertRaisesRegex(ValueError, "unique"):
            route_admission.admit(policy(), "code", self.baseline, (evidence("one"), evidence("one")))

    def test_wrong_route_or_model_or_second_model_cannot_be_admitted(self):
        wrong_route = (evidence("one", route="other"), evidence("two"))
        result = route_admission.admit(policy(), "code", self.baseline, wrong_route)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate resolved an unapproved route")

        wrong_model = (evidence("one", model="other"), evidence("two"))
        result = route_admission.admit(policy(), "code", self.baseline, wrong_model)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate resolved an unapproved model")

        second_model = (evidence("one", loaded_models=("approved-model", "other")), evidence("two"))
        result = route_admission.admit(policy(), "code", self.baseline, second_model)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate loaded an unapproved model")

    def test_residency_cap_and_vram_breach_are_refused(self):
        vram_breach = (evidence("one", peak_vram_mib=6001), evidence("two"))
        result = route_admission.admit(policy(), "code", self.baseline, vram_breach)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate exceeded the VRAM cap")

        two_loaded = (evidence("one", loaded_models=("approved-model", "approved-model")), evidence("two"))
        result = route_admission.admit(policy(), "code", self.baseline, two_loaded)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate exceeded the resident-model cap")

    def test_baseline_mismatch_and_fixture_substitution_are_refused(self):
        failing_baseline = (evidence("one", exit_code=1), evidence("two"))
        result = route_admission.admit(policy(), "code", failing_baseline, self.candidate)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "baseline verifier did not pass every fixture")

        substituted = (evidence("one"), evidence("other"))
        result = route_admission.admit(policy(), "code", self.baseline, substituted)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, "candidate fixtures differ from baseline")

    def test_decision_and_verifier_evidence_round_trip(self):
        result = route_admission.admit(policy(), "code", self.baseline, self.candidate)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "admission.jsonl"
            route_admission.record_evidence(result, target)
            stored = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(stored["admitted"])
        self.assertEqual(stored["policy_version"], "route-admission-v1")
        self.assertEqual([item["resolved_route"] for item in stored["candidate"]], ["omniroute-local", "omniroute-local"])
        self.assertEqual([item["verify_exit"] for item in stored["candidate"]], [0, 0])


class RouteAdmissionBoundaryTests(unittest.TestCase):
    def test_component_cannot_import_execution_or_complete_tasks(self):
        tree = ast.parse(Path(route_admission.__file__).read_text(encoding="utf-8"))
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
        self.assertTrue({"board", "worker", "executor", "subprocess"}.isdisjoint(imported))
        self.assertNotIn(
            "complete_task",
            {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)},
        )


if __name__ == "__main__":
    unittest.main()
