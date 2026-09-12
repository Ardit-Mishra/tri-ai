"""Proofs for the Part 2/3 dashboard audit: traps, resilience, and semantics.

These cover the client-side contract of the HUD - the parts an AST import
check cannot see. Where a defect was operator-reported, the test names the
defect rather than the fix, so a regression reads as the original complaint.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard import jarvis_web as web  # noqa: E402
from test_dashboard_web import fixture_snapshot  # noqa: E402


class ClientTrapTests(unittest.TestCase):
    def test_only_one_render_function_is_declared(self):
        """The dead duplicate shadowed the live one and touched a removed node.

        Two `function render(data)` declarations shared a scope. The first was
        unreachable, but it called clear(byId('matrix')) against an element the
        HUD no longer has - so if declaration order had ever changed, the SSE
        handler would have thrown a TypeError on every snapshot.
        """
        self.assertEqual(web.HTML.count("function render(data)"), 1)

    def test_the_removed_matrix_element_is_referenced_nowhere(self):
        self.assertNotIn("byId('matrix')", web.HTML)
        self.assertNotIn("#matrix", web.HTML)
        self.assertNotIn("const lanes", web.HTML)

    def test_canvas_backing_store_is_scaled_for_hidpi_and_mobile(self):
        # A CSS-sized canvas on a 3x phone renders at a third of the real
        # resolution; the backing store has to be scaled and the context
        # transformed to match, or every line and label is soft.
        self.assertIn("ratio=window.devicePixelRatio||1", web.HTML)
        self.assertIn("ctx.setTransform(ratio,0,0,ratio,0,0)", web.HTML)
        self.assertIn("#neuralGraph { cursor:crosshair; display:block; height:420px;", web.HTML)
        self.assertIn("width:100%;", web.HTML)


class StreamResilienceTests(unittest.TestCase):
    def test_reconnect_backoff_doubles_and_is_capped(self):
        self.assertIn("Math.min(30000,1000*Math.pow(2,streamState.attempt-1))", web.HTML)
        self.assertIn("streamState.timer=setTimeout(openStream,delay)", web.HTML)
        # The failed source is closed rather than left retrying underneath.
        self.assertIn("source.close();", web.HTML)

    def test_a_successful_snapshot_resets_the_backoff(self):
        self.assertIn("streamState.attempt=0; setStreamBadge(true);", web.HTML)

    def test_connectivity_is_announced_as_an_atomic_status(self):
        # A bare changing value is a poor live region; the badge carries a
        # complete message and announces atomically.
        self.assertIn('id="streamBadge" role="status" aria-atomic="true"', web.HTML)
        self.assertIn("'STREAM LIVE'", web.HTML)
        self.assertIn("RECONNECTING IN ", web.HTML)

    def test_the_stream_badge_survives_a_snapshot_rerender(self):
        """render() clears #services every snapshot; the badge must not live there."""
        head, _, tail = web.HTML.partition('id="streamBadge"')
        self.assertNotIn('<div class="services"', head[head.rfind("<header") :])
        self.assertIn('<div class="services" id="services"', tail)


class RelativeTimeTests(unittest.TestCase):
    def test_ledger_rows_render_relative_time_with_the_absolute_value_retained(self):
        self.assertIn("function timeAgo(ts)", web.HTML)
        self.assertIn("cell.title=absoluteTime(ts)", web.HTML)
        self.assertIn("row.append(timeCell(event.timestamp)", web.HTML)
        self.assertNotIn("monoTime", web.HTML)

    def test_durations_are_humanised_rather_than_raw_seconds(self):
        self.assertIn("function duration(seconds)", web.HTML)
        self.assertIn("make('span',duration(event.seconds))", web.HTML)
        self.assertNotIn("`${event.seconds}s`", web.HTML)

    def test_a_stalled_page_keeps_reporting_how_old_its_view_is(self):
        self.assertIn("last snapshot ${timeAgo(hud.lastSnapshotAt)}", web.HTML)


class SemanticNodeTests(unittest.TestCase):
    def test_the_node_plate_leads_with_intent_and_demotes_the_hex_id(self):
        self.assertIn("function drawNamePlate(node,x,y,bounds,leftward)", web.HTML)
        self.assertIn("truncate(node.detail.title,22)", web.HTML)
        self.assertIn("shortId(node.detail.id)", web.HTML)
        # Title is drawn bold above; the id is the smaller muted line below it.
        self.assertIn("ctx.font='700 11px \"JetBrains Mono\", monospace'; ctx.fillText(title,left+6,top+21)", web.HTML)
        self.assertIn("ctx.fillStyle='rgba(148,163,184,.92)'; ctx.font='9px \"JetBrains Mono\", monospace'; ctx.fillText(id,left+6,top+32)", web.HTML)

    def test_truncation_is_bounded_and_ellipsised(self):
        ellipsis = chr(0x2026)
        self.assertIn(
            "value.length<=limit?value:`${value.slice(0,limit-1).trimEnd()}" + ellipsis + "`",
            web.HTML,
        )
        # 22 characters of title, then a single ellipsis - never a hard cut.
        self.assertIn("truncate(node.detail.title,22)", web.HTML)

    def test_each_workspace_gets_a_stable_tag_and_tint(self):
        self.assertIn("function workspaceTint(path)", web.HTML)
        self.assertIn("const workspaceTag =", web.HTML)
        # Derived from the path, so a project added later is tagged without a
        # palette edit - and the same path always gets the same colour.
        self.assertIn("workspaceTints.set(key,WORKSPACE_TINTS[workspaceTints.size%WORKSPACE_TINTS.length])", web.HTML)

    def test_plates_anchor_outward_so_neighbours_do_not_cover_them(self):
        # Observed live: with three nodes on one orbit and no dependency edges,
        # the left-hand node's plate was drawn underneath its right-hand
        # neighbour, hiding a cancelled task's name entirely.
        self.assertIn("leftward=node.x<coreGeometry(rect).cx", web.HTML)
        self.assertIn("left=leftward?x-width+5:x-5", web.HTML)

    def test_hover_and_tap_both_raise_the_micro_card(self):
        self.assertIn("function drawMicroCard(bounds)", web.HTML)
        self.assertIn("if(next!==hud.hover){hud.hover=next;", web.HTML)
        self.assertIn("hud.selected=node.id;hud.hover=node.id;", web.HTML)

    def test_the_micro_card_states_the_fields_an_operator_asks_for(self):
        for field in ("'TASK'", "'WORKSPACE'", "'BRANCH'", "'PHASE'", "'RUNTIME'"):
            self.assertIn(field, web.HTML)
        # Never fabricated: a missing branch says so rather than rendering blank.
        self.assertIn("no branch recorded", web.HTML)
        self.assertIn("'not started'", web.HTML)


class TouchErgonomicsTests(unittest.TestCase):
    def test_the_bottom_sheet_handle_meets_the_touch_target_minimum(self):
        self.assertIn("min-height:48px", web.HTML)

    def test_tap_delay_is_removed_without_disabling_pinch_zoom(self):
        self.assertIn("touch-action:manipulation;", web.HTML)
        self.assertNotIn("user-scalable=no", web.HTML)
        self.assertNotIn("maximum-scale=1", web.HTML)

    def test_the_inspector_becomes_a_bottom_sheet_on_phones(self):
        self.assertIn("@media (max-width:767px)", web.HTML)
        self.assertIn(".hud-aside.open { transform:translateY(0); }", web.HTML)


class NetworkBindingTests(unittest.TestCase):
    def test_loopback_remains_the_default(self):
        server = web.create_server(port=0, snapshot_fn=fixture_snapshot)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_a_wider_binding_requires_an_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            web.create_server(host="0.0.0.0", port=0, snapshot_fn=fixture_snapshot)
        with self.assertRaisesRegex(ValueError, "loopback"):
            web.create_server(host="::", port=0, snapshot_fn=fixture_snapshot)

    def test_an_explicitly_allowed_host_binds_and_still_serves_read_only(self):
        server = web.create_server(
            host="0.0.0.0", port=0, snapshot_fn=fixture_snapshot,
            event_interval=0.01, allow_non_loopback=True,
        )
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "0.0.0.0")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/snapshot", timeout=5
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("metrics", payload)
        # Widening the bind does not add a control plane.
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/task", timeout=5
            )
        self.assertEqual(raised.exception.code, 404)

    def test_several_interfaces_can_be_bound_without_widening_to_every_network(self):
        """0.0.0.0 would also publish the HUD on the home Wi-Fi.

        Binding loopback plus one named interface keeps the reachable surface
        exactly the list the operator gave, which a wildcard bind cannot do.
        """
        loopback = web.create_server(port=0, snapshot_fn=fixture_snapshot)
        self.addCleanup(loopback.server_close)
        self.assertEqual(loopback.server_address[0], "127.0.0.1")
        # A second interface is a second server, not a wider single bind.
        named = web.create_server(
            host="127.0.0.2", port=0, snapshot_fn=fixture_snapshot, allow_non_loopback=True,
        )
        self.addCleanup(named.server_close)
        self.assertEqual(named.server_address[0], "127.0.0.2")
        self.assertNotEqual(loopback.server_address[0], "0.0.0.0")

    def test_serve_refuses_an_empty_server_set(self):
        with self.assertRaisesRegex(ValueError, "no servers"):
            web.serve([])

    def test_an_empty_host_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "host"):
            web.create_server(
                host="  ", port=0, snapshot_fn=fixture_snapshot, allow_non_loopback=True,
            )

    def test_the_cli_exposes_the_opt_in_as_a_separate_flag(self):
        parser_flags = set()
        import argparse as _argparse

        real_parse = _argparse.ArgumentParser.parse_args

        def capture(self, args=None, namespace=None):
            parser_flags.update(
                option for action in self._actions for option in action.option_strings
            )
            raise SystemExit(0)

        _argparse.ArgumentParser.parse_args = capture
        try:
            with self.assertRaises(SystemExit):
                web.main([])
        finally:
            _argparse.ArgumentParser.parse_args = real_parse
        self.assertIn("--host", parser_flags)
        self.assertIn("--allow-non-loopback", parser_flags)
        # --host repeats, so loopback and a Tailscale address can both be named
        # without falling back to a wildcard bind.
        self.assertIn('action="append"', Path(web.__file__).read_text(encoding="utf-8").replace("'", '"'))

    def test_the_warning_names_what_becomes_reachable(self):
        source = Path(web.__file__).read_text(encoding="utf-8")
        self.assertIn("reachable beyond this machine", source)
        self.assertIn("live log tails", source)


if __name__ == "__main__":
    unittest.main()
