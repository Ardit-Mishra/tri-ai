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
        self.assertIn("truncate(taskLabel(node.detail),narrowCanvas()?18:22)", web.HTML)
        self.assertIn("shortId(node.detail.id)", web.HTML)
        # Every Telegram task shares the title "Telegram: <alias>"; only the
        # operator's own prompt tells them apart on the canvas.
        self.assertIn("const taskLabel = task => (task && task.prompt && task.prompt.trim())", web.HTML)
        # Title is drawn bold above; the id is the smaller muted line below it.
        self.assertIn("ctx.font='700 11px \"JetBrains Mono\", monospace'; ctx.fillText(title,left+6,top+21)", web.HTML)
        self.assertIn("ctx.fillStyle='rgba(148,163,184,.92)'; ctx.font='9px \"JetBrains Mono\", monospace'; ctx.fillText(id,left+6,top+32)", web.HTML)

    def test_truncation_is_bounded_and_ellipsised(self):
        ellipsis = chr(0x2026)
        self.assertIn(
            "value.length<=limit?value:`${value.slice(0,limit-1).trimEnd()}" + ellipsis + "`",
            web.HTML,
        )
        # Bounded characters of the prompt, then a single ellipsis - never a hard
        # cut, and a tighter bound on a phone-width canvas.
        self.assertIn("truncate(taskLabel(node.detail),narrowCanvas()?18:22)", web.HTML)

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


class LabelCollisionTests(unittest.TestCase):
    """Workspace labels must not draw over the core anchor or each other."""

    def test_labels_are_measured_before_they_are_placed(self):
        self.assertIn("function pillRect(text,x,y,bounds)", web.HTML)
        self.assertIn("function rectsOverlap(a,b,pad)", web.HTML)

    def test_a_placed_label_claims_its_rect_and_later_labels_step_clear(self):
        self.assertIn("function placeClear(text,x,y,bounds,dx,dy)", web.HTML)
        self.assertIn("hud.claimedLabels.some(taken=>rectsOverlap(candidate,taken,3))", web.HTML)
        self.assertIn("hud.claimedLabels.push(finalRect)", web.HTML)

    def test_the_core_anchor_claims_its_label_before_hulls_are_drawn(self):
        # drawCore runs before drawHulls, so the core's rect is already claimed
        # when a workspace label looks for somewhere to sit.
        self.assertIn("hud.claimedLabels.push(pillRect(coreLabel,cx+16,cy,coreBounds))", web.HTML)
        self.assertLess(
            web.HTML.index("hud.claimedLabels.push(pillRect(coreLabel"),
            web.HTML.index("function drawHulls(bounds)"),
        )

    def test_workspace_labels_are_pushed_outward_from_the_core(self):
        # A hull that straddles the middle used to top out beside [TRI-AI CORE].
        # Placement is now radial: outward along centre-minus-core.
        self.assertIn("let dx=centre.x-cx,dy=centre.y-cy;", web.HTML)
        self.assertIn("if(span<1){dx=0;dy=-1;} else {dx/=span;dy/=span;}", web.HTML)
        self.assertNotIn("anchor.y-42-groupIndex*17", web.HTML)

    def test_claims_reset_every_frame(self):
        # Stale claims would push labels further out on each repaint.
        self.assertIn("ctx.clearRect(0,0,rect.width,rect.height);hud.claimedLabels=[];", web.HTML)

    def test_pill_drawing_uses_the_same_placement_it_measured(self):
        # drawLabelPill once clamped only the right edge while pillRect clamped
        # both, so a label measured as fitting was drawn off the left edge.
        self.assertIn("const box=pillRect(text,x,y,bounds);", web.HTML)
        self.assertIn("let left=box.left;", web.HTML)

    def test_a_workspace_label_too_wide_for_the_canvas_is_dropped(self):
        # Each plate already carries its own workspace tag, so a label that
        # would span the canvas is noise, not information.
        self.assertIn("if(labelWidth>canvas.getBoundingClientRect().width*.5)return;", web.HTML)
        self.assertIn("narrowCanvas()?`[ ${leaf} ]`", web.HTML)

    def test_phone_width_shows_plates_only_for_what_is_being_looked_at(self):
        self.assertIn("const crowded=narrowCanvas()||hud.nodes.length>=26;", web.HTML)
        self.assertIn(
            "const wantsPlate=selected||status==='running'||node.id===hud.hover||!crowded;",
            web.HTML,
        )


class NeuralLatticeTests(unittest.TestCase):
    """Axons, action potentials, cluster fields, and the soma's three states."""

    def test_axons_are_curved_filaments_not_straight_lines(self):
        self.assertIn("function axonCurve(a,b)", web.HTML)
        self.assertIn("ctx.quadraticCurveTo(control.cx,control.cy,b.x,b.y)", web.HTML)
        # The bow is perpendicular to the run, so parallel edges stay apart.
        self.assertIn("cx:(a.x+b.x)/2 - (dy/span)*bow", web.HTML)

    def test_action_potentials_travel_only_from_a_running_task(self):
        # A pulse is a readout of live execution, not ambient decoration.
        self.assertIn("function isFiring(node)", web.HTML)
        self.assertIn("node.detail.status==='running'", web.HTML)
        self.assertIn("if(!isFiring(a))return;", web.HTML)
        self.assertIn("function axonPoint(a,b,control,t)", web.HTML)

    def test_an_axon_lights_when_connected_to_what_is_being_inspected(self):
        self.assertIn(
            "const lit=[hud.hover,hud.selected].some(id=>id&&(id===edge.source||id===edge.target));",
            web.HTML,
        )

    def test_clusters_render_as_layered_fields_rather_than_one_outline(self):
        self.assertIn("const firing=group.members.some(isFiring);", web.HTML)
        self.assertIn("[[74,.030],[62,.045],[48,.060]].forEach", web.HTML)

    def test_the_soma_reads_its_state_from_the_board(self):
        # Running keeps the reactor core and phase ring; done settles; a
        # cancelled task dims to a dormant trace with an amber fringe.
        self.assertIn("if(status==='running'){drawReactorCore(node,radius);drawPhaseRing(node,radius);}", web.HTML)
        self.assertIn("ctx.strokeStyle='rgba(255,183,3,.42)';", web.HTML)
        self.assertIn("ctx.strokeStyle='rgba(0,255,157,.20)'; ctx.lineWidth=1;", web.HTML)


class TouchAndPreviewTests(unittest.TestCase):
    def test_the_canvas_backing_buffer_is_scaled_by_device_pixel_ratio(self):
        self.assertIn("ratio=window.devicePixelRatio||1", web.HTML)
        self.assertIn("ctx.setTransform(ratio,0,0,ratio,0,0)", web.HTML)
        self.assertIn("touch-action:none", web.HTML)

    def test_a_second_finger_becomes_a_pinch_rather_than_a_fight(self):
        self.assertIn("function pinchState()", web.HTML)
        self.assertIn("hud.dragging=null; hud.panning=null; hud.pinch=pinchState();", web.HTML)
        self.assertIn("zoomAt(next.midX,next.midY,hud.view.k*(next.distance/hud.pinch.distance));", web.HTML)

    def test_lifting_a_finger_ends_the_pinch(self):
        # Two handlers clear it, so a cancelled gesture cannot strand the state.
        self.assertEqual(web.HTML.count("if(hud.pointers.size<2)hud.pinch=null;"), 2)

    def test_the_micro_card_leads_with_intent_and_names_firing_duration(self):
        self.assertIn("taskLabel(node.detail):node.detail.rule_id,44", web.HTML)
        self.assertIn("`firing for ${runtime}`", web.HTML)

    def test_artifacts_preview_in_a_sandboxed_frame_that_stops_on_close(self):
        self.assertIn('sandbox="allow-scripts"', web.HTML)
        self.assertIn("function openPreview(url,label)", web.HTML)
        self.assertIn("frame.setAttribute('src','about:blank');", web.HTML)
        # Dismissable three ways, and a modified click keeps its normal meaning.
        self.assertIn("if(event.key==='Escape')closePreview();", web.HTML)
        self.assertIn("event.metaKey||event.ctrlKey||event.shiftKey||event.button!==0", web.HTML)
