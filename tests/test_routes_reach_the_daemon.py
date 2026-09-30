"""A route registry nothing loads is a route registry nobody has.

`model_routes` parses and validates an operator-owned registry, `board`
writes `model_override`/`provider_override` from a resolved route, and
`telegram_control.TelegramControl` accepts a `model_routes_config`. All
three are built and tested.

Nothing supplies the config. No daemon declares a flag for it, so
`TelegramControl` is constructed as

    TelegramControl(policy, dashboard_url=..., completer=...)

and `self._model_routing` is None on every run. The laptop has carried
`~/.tri-ai/model-routes.json` since 2026-09-26 and the running system has
never read one byte of it. The desktop has no such file at all.

That is the fifth instance in this repository of written, tested, called
by nothing - and the consequence here is specific: every task falls
through to whatever Hermes decides, so a paid subscription can be spent
on work the operator pinned to a free lane.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model_routes  # noqa: E402
from interfaces import telegram_daemon  # noqa: E402


REAL = Path(__file__).resolve().parents[1] / "config" / "model-routes.tri-ai.json"


def _write(tmp: Path, payload: dict) -> Path:
    path = tmp / "model-routes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TheDaemonAcceptsARouteRegistryTest(unittest.TestCase):
    def test_the_flag_exists(self):
        parser = telegram_daemon.build_parser()
        options = {action.dest for action in parser._actions}
        self.assertIn("model_routes", options,
                      "no daemon flag supplies a route registry, so the "
                      "policy is never loaded and every task falls through "
                      "to whatever Hermes decides")

    def test_a_valid_registry_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), {
                "version": "t", "default_route": "local",
                "role_routes": {"builder": "local"},
                "routes": {"local": {"model": "qwen2.5-coder:14b",
                                     "provider": "custom", "admitted": True}},
            })
            config = telegram_daemon.load_model_routes(str(path))
        policy = model_routes.policy_from_mapping(config)
        self.assertEqual(model_routes.select(policy, "builder").model,
                         "qwen2.5-coder:14b")

    def test_no_flag_means_no_routing_rather_than_a_guess(self):
        self.assertIsNone(telegram_daemon.load_model_routes(None))

    def test_a_registry_that_does_not_parse_is_refused_at_load(self):
        """Fail closed and loudly, at start, not per task at 3am."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), {"version": "t", "routes": {}})
            with self.assertRaises(ValueError):
                telegram_daemon.load_model_routes(str(path))

    def test_a_role_pinned_to_an_unadmitted_route_is_refused_at_load(self):
        """The admission flag has to bite before a run, not during one."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), {
                "version": "t",
                "role_routes": {"builder": "unproven"},
                "routes": {"unproven": {"model": "gemma4:31b",
                                        "provider": "custom", "admitted": False}},
            })
            with self.assertRaises(ValueError):
                telegram_daemon.load_model_routes(str(path))


class TheShippedRegistryIsCoherentTest(unittest.TestCase):
    """The file this repository ships must satisfy its own parser."""

    def setUp(self):
        self.raw = json.loads(REAL.read_text(encoding="utf-8"))
        self.policy = model_routes.policy_from_mapping(self.raw)

    def test_every_role_resolves_to_an_admitted_route(self):
        for role in self.policy.role_routes:
            route = model_routes.select(self.policy, role)
            self.assertIsNotNone(route, f"{role} resolves to nothing")
            self.assertTrue(route.admitted)

    def test_every_capability_role_has_a_route(self):
        """A role with no pin silently falls back to Hermes."""
        import capabilities
        missing = sorted(set(capabilities.ROLES) - set(self.policy.role_routes))
        self.assertEqual(missing, [],
                         f"these roles are unpinned and would run unrouted: {missing}")

    def test_nothing_escalates_to_a_subscription(self):
        """Operator decision, 2026-09-30: nothing escalates yet.

        Encoded as a test because the whole point of the file is that a
        lane cannot be spent by accident.
        """
        for role, name in self.policy.role_routes.items():
            self.assertFalse(
                name.startswith("subscription-"),
                f"{role} is pinned to {name}; nothing should escalate yet")
        for name in ("subscription-claude", "subscription-codex"):
            self.assertFalse(self.raw["routes"][name]["admitted"],
                             f"{name} is admitted but was not measured")

    def test_the_lanes_that_failed_the_bench_are_refused(self):
        """0/3 correct is not a lane. 2/3 is not one either."""
        for name in ("local-qwen3-14b", "local-gemma4-31b", "freellmapi-auto"):
            self.assertFalse(
                self.raw["routes"][name]["admitted"],
                f"{name} did not pass the bench and must not be admitted")

    def test_every_admitted_route_carries_its_evidence(self):
        for name, route in self.raw["routes"].items():
            self.assertTrue(
                str(route.get("_evidence", "")).strip(),
                f"{name} is declared with no measurement behind it")

    def test_the_default_is_a_free_lane(self):
        route = self.policy.registry.routes[self.policy.default_route]
        self.assertFalse(route.name.startswith("subscription-"))


class TheLoadedRegistryActuallyReachesTheControlTest(unittest.TestCase):
    """Loading a file and using it are two different bugs.

    Every other test here proves the flag parses and the registry
    validates. None of them would fail if `main` loaded the file and then
    constructed `TelegramControl` without it - which is exactly the shape
    of the defect this module exists to close, one layer up.
    """

    def test_main_constructs_the_control_with_the_registry(self):
        import ast, inspect
        tree = ast.parse(inspect.getsource(telegram_daemon))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "TelegramControl"]
        self.assertEqual(len(calls), 1, "expected exactly one construction")
        kwargs = {kw.arg for kw in calls[0].keywords}
        self.assertIn(
            "model_routes_config", kwargs,
            "the daemon loads a route registry and then builds the control "
            "without it, so the pins are parsed and discarded")

    def test_the_registry_passed_is_the_one_the_flag_named(self):
        import ast, inspect
        tree = ast.parse(inspect.getsource(telegram_daemon))
        call = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "TelegramControl"][0]
        passed = [kw.value for kw in call.keywords if kw.arg == "model_routes_config"][0]
        self.assertIsInstance(passed, ast.Call,
                              "expected load_model_routes(args.model_routes)")
        self.assertEqual(passed.func.id, "load_model_routes")


class TheSupervisorPassesTheRegistryDownTest(unittest.TestCase):
    """The supervisor spawns the Telegram child, so it owns the flag.

    Adding `--model-routes` to the daemon changes nothing in production if
    the process that launches it never passes one. `commands()` is the only
    place the child argv is built, and the test asserts on that argv rather
    than on the supervisor's intent.
    """

    def _telegram_argv(self, **kwargs):
        import daemon_supervisor
        from pathlib import Path as P
        return daemon_supervisor.commands(
            root=P("/repo"), board_path=P("/b.db"), ledger_path=P("/l.jsonl"),
            runs_root=P("/runs"), intake_policy=None, **kwargs).telegram

    def test_a_registry_path_reaches_the_child(self):
        from pathlib import Path as P
        argv = self._telegram_argv(model_routes=P("/routes.json"))
        self.assertIn("--model-routes", argv,
                      "the supervisor never hands the child a route "
                      "registry, so the pins stay on disk")
        self.assertEqual(argv[argv.index("--model-routes") + 1], str(P("/routes.json")))

    def test_no_registry_means_no_flag(self):
        self.assertNotIn("--model-routes", self._telegram_argv(model_routes=None))

    def test_the_default_path_is_used_when_it_exists(self):
        """Same shape as the intake policy: present on disk means in use."""
        import daemon_supervisor, tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            path = P(tmp) / "model-routes.json"
            self.assertIsNone(daemon_supervisor.resolve_model_routes(
                None, default_path=path))
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(
                daemon_supervisor.resolve_model_routes(None, default_path=path), path)

    def test_an_explicit_path_stays_authoritative(self):
        """A typo must fail loudly, not be replaced by the default."""
        import daemon_supervisor
        from pathlib import Path as P
        asked = P("/nope/routes.json")
        self.assertEqual(
            daemon_supervisor.resolve_model_routes(asked, default_path=P("/other.json")),
            asked)


class TheConsoleReadsTheRegistryItDescribesTest(unittest.TestCase):
    """The lane panel described a policy it had never read.

    `AGENT_LENSES` is a constant in the template, so the three lanes were
    described identically whatever the registry held - including when
    there was no registry at all, which was true on both machines until
    today. A console that reports a wish is worse than one that reports
    nothing, because you stop checking.
    """

    def test_the_snapshot_carries_the_registry(self):
        from dashboard import kaya_web
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-routes.json"
            path.write_text(json.dumps({
                "version": "t", "default_route": "l",
                "role_routes": {"builder": "l"},
                "routes": {"l": {"model": "qwen2.5-coder:14b", "provider": "custom",
                                 "admitted": True, "_evidence": "bench 3/3"}},
            }), encoding="utf-8")
            view = kaya_web.routing_policy_view(path)
        self.assertEqual(view["status"], "loaded")
        self.assertEqual(view["default_route"], "l")
        self.assertEqual(view["roles"], {"builder": "l"})
        self.assertEqual(view["routes"][0]["model"], "qwen2.5-coder:14b")
        self.assertTrue(view["routes"][0]["admitted"])

    def test_a_missing_registry_says_absent_rather_than_inventing_one(self):
        from dashboard import kaya_web
        view = kaya_web.routing_policy_view(Path("/no/such/model-routes.json"))
        self.assertEqual(view["status"], "absent")
        self.assertEqual(view["routes"], [])

    def test_a_corrupt_registry_does_not_take_the_console_down(self):
        """The dashboard is a read-only view; bad operator JSON is not an outage."""
        from dashboard import kaya_web
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-routes.json"
            path.write_text("{ not json", encoding="utf-8")
            view = kaya_web.routing_policy_view(path)
        self.assertEqual(view["status"], "absent")

    def test_the_shipped_registry_reports_what_it_admits(self):
        from dashboard import kaya_web
        view = kaya_web.routing_policy_view(REAL)
        admitted = {r["name"] for r in view["routes"] if r["admitted"]}
        self.assertIn("local-qwen-coder-14b", admitted)
        self.assertNotIn("subscription-claude", admitted)
        self.assertNotIn("freellmapi-auto", admitted)

    def test_subscription_routes_are_classified_to_their_own_lanes(self):
        """So the Claude tab reports on Claude, not on everything."""
        from dashboard import kaya_web
        view = kaya_web.routing_policy_view(REAL)
        lanes = {r["name"]: r["lane"] for r in view["routes"]}
        self.assertEqual(lanes["subscription-claude"], "claude")
        self.assertEqual(lanes["subscription-codex"], "codex")
        self.assertEqual(lanes["local-qwen-coder-14b"], "local")
        self.assertEqual(lanes["omniroute-best-coding"], "local")


if __name__ == "__main__":
    unittest.main()
