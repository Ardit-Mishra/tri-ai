"""Build the README illustrations in docs/img/ (dark and light variants).

Each visual is one function that draws with palette tokens, so the two themes
can never drift apart. Run from the repository root:

    python docs/img/build_art.py

Output is plain SVG for GitHub's <img> context: no scripts, no external
fonts or links, no foreignObject. Text is system-ui for captions and
ui-monospace for real identifiers. Colors come from the Kaya console's
black-and-green theme (src/dashboard/kaya_console.css). Green means
accepted, crimson means reverted, amber means escalating, and nothing else.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent

PALETTES = {
    "dark": {
        "bg": "#050a07", "plate": "rgba(120,220,160,0.22)",
        "s1": "#0b130e", "s2": "#101b14", "line": "rgba(120,220,160,0.28)",
        "text": "#e2ece5", "text2": "#a9bcae", "muted": "#7f9787",
        "ok": "#5fe0a0", "okSoft": "rgba(95,224,160,0.13)",
        "bad": "#e5675f", "badSoft": "rgba(229,103,95,0.13)",
        "warn": "#dcaa4f", "warnSoft": "rgba(220,170,79,0.13)",
        "ink": "#e2ece5",
    },
    "light": {
        "bg": "#f6f9f5", "plate": "#cbd9cf",
        "s1": "#ffffff", "s2": "#e9f0ea", "line": "#a9bdb0",
        "text": "#0e1f15", "text2": "#3b5145", "muted": "#566a5e",
        "ok": "#11764a", "okSoft": "rgba(17,118,74,0.10)",
        "bad": "#b3362d", "badSoft": "rgba(179,54,45,0.08)",
        "warn": "#8a5a0b", "warnSoft": "rgba(138,90,11,0.10)",
        "ink": "#0e1f15",
    },
}

SANS = 'system-ui,-apple-system,&quot;Segoe UI&quot;,Roboto,&quot;Helvetica Neue&quot;,Arial,sans-serif'
MONO = 'ui-monospace,SFMono-Regular,&quot;SF Mono&quot;,Menlo,Consolas,&quot;Liberation Mono&quot;,monospace'


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def head(slug: str, w: int, h: int, title: str, desc: str, p: dict, extra_css: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-labelledby="{slug}-title {slug}-desc">\n'
        f'<title id="{slug}-title">{esc(title)}</title>\n'
        f'<desc id="{slug}-desc">{esc(desc)}</desc>\n'
        f'<style>\n'
        f'.s{{font-family:{SANS}}}\n'
        f'.m{{font-family:{MONO}}}\n'
        f'{extra_css}'
        f'</style>\n'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" fill="{p["bg"]}" stroke="{p["plate"]}"/>\n'
    )


def t(x, y, s, size, fill, cls="s", anchor="start", weight=400, extra=""):
    return (f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{extra}>{esc(s)}</text>\n')


def check(x, y, color, size=16, width=2.2):
    """A drawn check mark whose bounding box starts at (x, y)."""
    k = size / 16
    return (f'<path d="M{x + 2 * k:.1f} {y + 8.5 * k:.1f} L{x + 6.5 * k:.1f} {y + 13 * k:.1f} '
            f'L{x + 14 * k:.1f} {y + 3.5 * k:.1f}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>\n')


def cross(x, y, color, size=16, width=2.2):
    k = size / 16
    return (f'<path d="M{x + 3 * k:.1f} {y + 3 * k:.1f} L{x + 13 * k:.1f} {y + 13 * k:.1f} '
            f'M{x + 13 * k:.1f} {y + 3 * k:.1f} L{x + 3 * k:.1f} {y + 13 * k:.1f}" fill="none" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>\n')


def arrow_defs(p: dict) -> str:
    out = "<defs>\n"
    for name, col in (("a", p["text2"]), ("ok", p["ok"]), ("bad", p["bad"]), ("warn", p["warn"])):
        out += (f'<marker id="m-{name}" markerWidth="10" markerHeight="8" refX="9" refY="4" '
                f'orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0 L10 4 L0 8 Z" fill="{col}"/></marker>\n')
    return out + "</defs>\n"


# Motifs, drawn the same way everywhere ---------------------------------------

def phone(x, y, w, h, p):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(10, w // 5)}" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
            f'<line x1="{x + w / 2 - 7}" y1="{y + h - 8}" x2="{x + w / 2 + 7}" y2="{y + h - 8}" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n')


def gate(cx, top, bottom, p, bar_y=None, bar_id=None):
    """Two posts and a bar across the path between them."""
    bar_y = bar_y if bar_y is not None else (top + bottom) / 2 - 4
    s = (f'<rect x="{cx - 32}" y="{top}" width="8" height="{bottom - top}" rx="2" fill="{p["text"]}"/>\n'
         f'<rect x="{cx + 24}" y="{top}" width="8" height="{bottom - top}" rx="2" fill="{p["text"]}"/>\n')
    for px in (cx - 28, cx + 28):
        s += f'<circle cx="{px}" cy="{top}" r="6" fill="{p["text"]}"/>\n'
        s += f'<line x1="{px - 10}" y1="{bottom}" x2="{px + 10}" y2="{bottom}" stroke="{p["text"]}" stroke-width="3" stroke-linecap="round"/>\n'
    idattr = f' id="{bar_id}"' if bar_id else ""
    s += f'<rect{idattr} x="{cx - 26}" y="{bar_y}" width="52" height="8" rx="2" fill="{p["text"]}"/>\n'
    return s


def ledger_glyph(x, y, w, h, p, rows=4, marks=("ok", "bad", "ok", "ok")):
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
    step = h / (rows + 1)
    for i in range(rows):
        ry = y + step * (i + 1)
        mark = marks[i % len(marks)]
        s += f'<circle cx="{x + 16}" cy="{ry:.1f}" r="4" fill="{p[mark]}"/>\n'
        s += f'<line x1="{x + 28}" y1="{ry:.1f}" x2="{x + w - 14}" y2="{ry:.1f}" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
    return s


# 1. Hero ----------------------------------------------------------------------

def hero(p, slug):
    W, H = 1280, 640
    s = head(slug, W, H, "Tri-AI: free AI models do the work, a test decides whether it counts",
             "A request from a phone runs on a free AI model; a verification command then decides: "
             "exit 0 keeps the work, any other exit code reverts it.", p)
    s += arrow_defs(p)
    # Wordmark: a small gate glyph and the name in mono.
    s += (f'<circle cx="76" cy="66" r="4" fill="{p["ok"]}"/><circle cx="100" cy="66" r="4" fill="{p["ok"]}"/>'
          f'<rect x="74" y="66" width="4" height="28" fill="{p["ok"]}"/><rect x="98" y="66" width="4" height="28" fill="{p["ok"]}"/>'
          f'<rect x="76" y="78" width="24" height="4" rx="1" fill="{p["ok"]}"/>'
          f'<line x1="68" y1="94" x2="108" y2="94" stroke="{p["ok"]}" stroke-width="3" stroke-linecap="round"/>\n')
    s += t(118, 92, "tri-ai", 28, p["text"], "m", weight=600)
    s += t(72, 196, "Free AI models do the work.", 60, p["text"], weight=650, extra=' letter-spacing="-0.5"')
    s += t(72, 268, "A test decides whether it counts.", 60, p["text2"], weight=650, extra=' letter-spacing="-0.5"')
    s += t(72, 320, "Multi-agent build system  ·  requests from a phone  ·  $0 metered model spend", 24, p["text2"])
    # Hairline under the text block.
    s += f'<line x1="72" y1="356" x2="1208" y2="356" stroke="{p["line"]}" stroke-width="1"/>\n'

    # Column A: phone + message bubble.
    s += phone(72, 404, 64, 120, p)
    s += f'<line x1="84" y1="428" x2="124" y2="428" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
    s += f'<line x1="84" y1="444" x2="112" y2="444" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
    s += f'<path d="M136 452 L152 444 L152 460 Z" fill="{p["s2"]}"/>\n'
    s += f'<rect x="152" y="424" width="300" height="56" rx="10" fill="{p["s2"]}" stroke="{p["line"]}"/>\n'
    s += t(172, 460, "add dark mode to the site", 22, p["text"])
    # A -> B
    s += f'<line x1="452" y1="452" x2="512" y2="452" stroke="{p["text2"]}" stroke-width="1.6" marker-end="url(#m-a)"/>\n'

    # Column B: three model chips (cheapest first), joined by a bracket.
    chips = [("local", "Ornith 9B"), ("free hosted", "Kimi K3"), ("subscription", "last")]
    for i, (k, v) in enumerate(chips):
        y = 396 + i * 56
        s += f'<rect x="520" y="{y}" width="244" height="44" rx="8" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
        s += t(540, y + 29, k, 19, p["text"])
        s += t(744, y + 29, v, 17, p["text2"], "m", anchor="end")
    s += f'<path d="M764 418 H788 V530 H764 M788 452 H836" fill="none" stroke="{p["text2"]}" stroke-width="1.6" marker-end="url(#m-a)"/>\n'

    # Column C: the gate on the path.
    s += f'<line x1="836" y1="452" x2="932" y2="452" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
    s += gate(884, 404, 524, p, bar_y=448)

    # Column D: outcomes.
    s += f'<path d="M932 452 H952 V428 H988 M952 452 V500 H988" fill="none" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
    s += f'<rect x="988" y="404" width="220" height="48" rx="8" fill="{p["okSoft"]}" stroke="{p["ok"]}"/>\n'
    s += check(1004, 418, p["ok"], 20, 2.6)
    s += t(1036, 435, "exit 0  accepted", 20, p["ok"], "m", weight=600)
    s += f'<rect x="988" y="476" width="220" height="48" rx="8" fill="{p["badSoft"]}" stroke="{p["bad"]}"/>\n'
    s += cross(1004, 490, p["bad"], 20, 2.6)
    s += t(1036, 507, "exit 1  reverted", 20, p["bad"], "m", weight=600)

    # Two-layer captions under each column.
    caps = [(72, "A request from my phone", "telegram"), (520, "Runs on a free model", "worker"),
            (836, "A test checks it", "verify_command"), (988, "Kept, or undone", "ledger.jsonl")]
    for x, c, m in caps:
        s += t(x, 576, c, 20, p["text"], weight=600)
        s += t(x, 604, m, 17, p["text2"], "m")
    return s + "</svg>\n"


# 2. How a request flows -----------------------------------

FLOW = [
    ("Send a request", "from your phone", "telegram_daemon"),
    ("Split into", "specialist tasks", "decomposer"),
    ("Each task runs", "on a free model", "worker"),
    ("A test command", "checks the work", "verify_command"),
    ("Pass: kept", "Fail: retried", "ledger.jsonl"),
]


def flow_icon(i, cx, cy, p):
    """Icons sized to a 64x64 box centred on (cx, cy)."""
    s = ""
    if i == 0:
        s += phone(cx - 18, cy - 32, 36, 64, p)
        s += f'<line x1="{cx - 9}" y1="{cy - 16}" x2="{cx + 9}" y2="{cy - 16}" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
        s += f'<line x1="{cx - 9}" y1="{cy - 6}" x2="{cx + 3}" y2="{cy - 6}" stroke="{p["text2"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
    elif i == 1:
        s += f'<path d="M{cx} {cy - 14} V{cy} M{cx - 26} {cy + 12} V{cy} H{cx + 26} V{cy + 12} M{cx} {cy} V{cy + 12}" fill="none" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
        s += f'<rect x="{cx - 14}" y="{cy - 32}" width="28" height="18" rx="4" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
        for dx in (-26, 0, 26):
            s += f'<rect x="{cx + dx - 10}" y="{cy + 12}" width="20" height="18" rx="4" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
    elif i == 2:
        for d in (-14, 0, 14):
            s += f'<line x1="{cx - 32}" y1="{cy + d}" x2="{cx + 32}" y2="{cy + d}" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
            s += f'<line x1="{cx + d}" y1="{cy - 32}" x2="{cx + d}" y2="{cy + 32}" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
        s += f'<rect x="{cx - 24}" y="{cy - 24}" width="48" height="48" rx="6" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
        s += f'<rect x="{cx - 12}" y="{cy - 12}" width="24" height="24" rx="3" fill="none" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
    elif i == 3:
        s += f'<line x1="{cx - 36}" y1="{cy + 4}" x2="{cx + 36}" y2="{cy + 4}" stroke="{p["text2"]}" stroke-width="1.6"/>\n'
        s += gate(cx, cy - 30, cy + 32, p, bar_y=cy)
    else:
        s += ledger_glyph(cx - 28, cy - 32, 56, 64, p, rows=3, marks=("ok", "bad", "ok"))
    return s


def flow_wide(p, slug):
    W, H = 900, 336
    s = head(slug, W, H, "How a request flows through Tri-AI",
             "Five steps: a request is sent from a phone, split into specialist tasks, each task runs on a "
             "free model, a test command checks the work, and passing work is kept while failing work is retried.", p)
    s += arrow_defs(p)
    pw, gap, x0 = 160, 14, 22
    for i, (c1, c2, m) in enumerate(FLOW):
        x = x0 + i * (pw + gap)
        cx = x + pw / 2
        s += f'<rect x="{x}" y="20" width="{pw}" height="296" rx="10" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
        s += f'<circle cx="{x + 24}" cy="44" r="13" fill="none" stroke="{p["text2"]}" stroke-width="1.4"/>\n'
        s += t(x + 24, 49, str(i + 1), 14, p["text2"], "m", anchor="middle", weight=600)
        s += flow_icon(i, cx, 120, p)
        if i == 4:
            s += check(x + 14, 205, p["ok"], 16, 2.2)
            s += t(x + 36, 220, c1, 17, p["ok"], weight=600)
            s += cross(x + 14, 233, p["bad"], 16, 2.2)
            s += t(x + 36, 248, c2, 17, p["bad"], weight=600)
        else:
            s += t(x + 14, 220, c1, 17, p["text"], weight=600)
            s += t(x + 14, 244, c2, 17, p["text"], weight=600)
        s += f'<line x1="{x + 14}" y1="268" x2="{x + pw - 14}" y2="268" stroke="{p["line"]}" stroke-width="1"/>\n'
        s += t(x + 14, 294, m, 14, p["text2"], "m")
        if i < 4:
            ax = x + pw
            s += f'<line x1="{ax + 1}" y1="120" x2="{ax + gap - 1}" y2="120" stroke="{p["text2"]}" stroke-width="1.6" marker-end="url(#m-a)"/>\n'
    return s + "</svg>\n"


# 3. The verify gate, animated --------------------------------------------------

def gate_anim(p, slug):
    W, H = 900, 400
    css = """
.tA{animation:tA 8s infinite}
.tB{animation:tB 8s infinite}
.okA{animation:okA 8s infinite}
.badB{animation:badB 8s infinite}
.bar{transform-box:view-box;transform-origin:480px 182px;animation:bar 8s infinite}
.r0{animation:r0 8s infinite}.r1{animation:r1 8s infinite}.r2{animation:r2 8s infinite}
@keyframes tA{0%{transform:translate(0px,0px);opacity:1;animation-timing-function:ease-in-out}
15%{transform:translate(212px,0px);opacity:1}
27%{transform:translate(212px,0px);opacity:1;animation-timing-function:ease-in-out}
40%{transform:translate(520px,0px);opacity:1}
44%{transform:translate(520px,0px);opacity:0}
99.5%{transform:translate(0px,0px);opacity:0}
100%{transform:translate(0px,0px);opacity:1}}
@keyframes okA{0%,25%{opacity:0}27%,100%{opacity:1}}
@keyframes tB{0%,47%{transform:translate(0px,0px);opacity:0}
50%{transform:translate(0px,0px);opacity:1;animation-timing-function:ease-in-out}
63%{transform:translate(212px,0px);opacity:1}
72%{transform:translate(212px,0px);opacity:1;animation-timing-function:ease-in}
77%{transform:translate(212px,136px);opacity:1}
94%{transform:translate(-108px,136px);opacity:1;animation-timing-function:ease-out}
97%{transform:translate(-108px,64px);opacity:1}
99%{transform:translate(-108px,64px);opacity:0}
100%{transform:translate(0px,0px);opacity:0}}
@keyframes badB{0%,70%{opacity:0}72%,100%{opacity:1}}
@keyframes bar{0%,25%{transform:rotate(0deg)}29%,38%{transform:rotate(-78deg)}43%,100%{transform:rotate(0deg)}}
@keyframes r0{0%,22%{opacity:1}23%,45%{opacity:0}46%,68%{opacity:1}69%,95%{opacity:0}96%,100%{opacity:1}}
@keyframes r1{0%,22%{opacity:0}23%,45%{opacity:1}46%,100%{opacity:0}}
@keyframes r2{0%,68%{opacity:0}69%,95%{opacity:1}96%,100%{opacity:0}}
@media (prefers-reduced-motion:reduce){.tA,.tB,.okA,.badB,.bar,.r0,.r1,.r2{animation:none}.tB,.r1,.r2{opacity:0}}
"""
    s = head(slug, W, H, "The verify gate",
             "A task leaves the board and stops at a gate. The agent's own report is ignored. A verification "
             "command runs: exit 0 lifts the bar and the work is kept in the ledger; exit 1 keeps the bar down, "
             "and the task is reverted and returned to the board for the next model.", p, css)
    s += arrow_defs(p)
    # Board.
    s += t(116, 100, "Task board", 17, p["text"], anchor="middle", weight=600)
    s += t(116, 122, "board.py", 14, p["text2"], "m", anchor="middle")
    s += f'<rect x="40" y="136" width="152" height="100" rx="8" fill="{p["s1"]}" stroke="{p["text"]}" stroke-width="1.6"/>\n'
    for ry in (160, 180, 200, 220):
        s += f'<line x1="56" y1="{ry}" x2="176" y2="{ry}" stroke="{p["line"]}" stroke-width="1.6" stroke-linecap="round"/>\n'
    # Track and paths (drawn before nodes).
    s += f'<line x1="192" y1="184" x2="700" y2="184" stroke="{p["text2"]}" stroke-width="1.6" marker-end="url(#m-a)"/>\n'
    s += (f'<path d="M436 200 V320 H116 V242" fill="none" stroke="{p["bad"]}" stroke-width="1.6" '
          f'stroke-dasharray="6 5" marker-end="url(#m-bad)"/>\n')
    s += t(276, 354, "Fail: undo the change, try the next model", 15, p["bad"], anchor="middle", weight=600)
    s += t(622, 172, "accepted", 15, p["ok"], anchor="middle", weight=600)
    # Agent's report: shown, struck out.
    s += t(326, 100, "the agent's own report", 14, p["text2"], anchor="middle")
    s += f'<rect x="246" y="112" width="160" height="34" rx="6" fill="{p["s2"]}" stroke="{p["line"]}"/>\n'
    s += t(326, 134, 'agent: "done"', 15, p["muted"], "m", anchor="middle")
    s += f'<line x1="262" y1="129" x2="390" y2="129" stroke="{p["muted"]}" stroke-width="1.6"/>\n'
    s += t(326, 162, "not counted", 14, p["muted"], anchor="middle")
    # Readout above the gate.
    s += f'<line x1="504" y1="100" x2="504" y2="144" stroke="{p["line"]}" stroke-width="1.4" stroke-dasharray="3 4"/>\n'
    s += f'<rect x="420" y="40" width="168" height="60" rx="8" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
    s += t(504, 63, "verify_command", 14, p["text2"], "m", anchor="middle")
    s += t(504, 87, "running...", 16, p["text2"], "m r0", anchor="middle", weight=600, extra='')
    s += t(504, 87, "exit 0  pass", 16, p["ok"], "m r1", anchor="middle", weight=600, extra=' opacity="0"')
    s += t(504, 87, "exit 1  fail", 16, p["bad"], "m r2", anchor="middle", weight=600, extra=' opacity="0"')
    # Gate posts and animated bar.
    s += f'<rect x="472" y="144" width="8" height="88" rx="2" fill="{p["text"]}"/>\n'
    s += f'<rect x="528" y="144" width="8" height="88" rx="2" fill="{p["text"]}"/>\n'
    for px in (476, 532):
        s += f'<circle cx="{px}" cy="144" r="6" fill="{p["text"]}"/>\n'
        s += f'<line x1="{px - 10}" y1="232" x2="{px + 10}" y2="232" stroke="{p["text"]}" stroke-width="3" stroke-linecap="round"/>\n'
    s += f'<rect class="bar" x="476" y="178" width="56" height="8" rx="2" fill="{p["text"]}"/>\n'
    # Ledger.
    s += t(780, 100, "Ledger", 17, p["text"], anchor="middle", weight=600)
    s += t(780, 122, "ledger.jsonl", 14, p["text2"], "m", anchor="middle")
    s += ledger_glyph(704, 136, 152, 100, p, rows=4, marks=("ok", "bad", "ok", "ok"))
    # Tokens.
    def token(cls, overlay_cls, overlay_col, overlay_soft, mark):
        g = f'<g class="{cls}">\n'
        g += f'<rect x="196" y="170" width="56" height="28" rx="6" fill="{p["s2"]}" stroke="{p["text2"]}" stroke-width="1.4"/>\n'
        g += f'<g class="{overlay_cls}" opacity="0"><rect x="196" y="170" width="56" height="28" rx="6" fill="{p["bg"]}"/>'
        g += f'<rect x="196" y="170" width="56" height="28" rx="6" fill="{overlay_soft}" stroke="{overlay_col}" stroke-width="1.6"/></g>\n'
        g += t(224, 189, "task", 14, p["text"], "m", anchor="middle")
        return g + "</g>\n"
    s += token("tA", "okA", p["ok"], p["okSoft"], "ok")
    s += token("tB", "badB", p["bad"], p["badSoft"], "bad").replace('<g class="tB">', '<g class="tB" opacity="0">', 1)
    s += t(450, 388, "The model's own “done” is ignored. Only the exit code counts.", 16, p["text"], anchor="middle", weight=600)
    return s + "</svg>\n"


# 4. Escalation ladder ---------------------------------------------------------

def ladder(p, slug):
    W, H = 900, 520
    s = head(slug, W, H, "Escalation ladder: cheapest model first",
             "A failed or timed-out task moves down one rung: a local Ollama model, then free hosted models, "
             "then a flat-rate Google AI Pro subscription, then the Claude subscription last. Nothing billed "
             "per token is ever called, and a route is admitted only after an agentic probe and a served-model match.", p)
    s += arrow_defs(p)
    rows = [
        (32, 80, "Local model, on my own GPU", ["Ollama: Ornith 9B"], "free"),
        (144, 124, "Free hosted models", ["NVIDIA: Nemotron 3 Super, Kimi K3,", "        Laguna XS 2.1",
                                           "OpenRouter: Qwen 3.8 27B  ·  Mistral: Mistral Code",
                                           "Google AI Studio: Gemini 3.5 Flash Lite"], "free tier"),
        (300, 80, "Flat-rate subscription", ["Google AI Pro, through Antigravity"], "flat fee"),
        (412, 80, "Claude subscription, last", ["claude-subscription"], "flat fee"),
    ]
    rows[1] = (144, 124, rows[1][2], ["NVIDIA: Nemotron 3 Super, Kimi K3, Laguna XS 2.1",
                                      "OpenRouter: Qwen 3.8 27B  ·  Mistral: Mistral Code",
                                      "Google AI Studio: Gemini 3.5 Flash Lite"], rows[1][4])
    # Rails.
    s += f'<line x1="44" y1="24" x2="44" y2="500" stroke="{p["text"]}" stroke-width="3" stroke-linecap="round"/>\n'
    s += f'<line x1="96" y1="24" x2="96" y2="500" stroke="{p["text"]}" stroke-width="3" stroke-linecap="round"/>\n'
    for (y, h, cap, monos, tag) in rows:
        cy = y + 30
        s += f'<line x1="44" y1="{cy}" x2="96" y2="{cy}" stroke="{p["text"]}" stroke-width="3" stroke-linecap="round"/>\n'
        s += f'<line x1="96" y1="{cy}" x2="128" y2="{cy}" stroke="{p["line"]}" stroke-width="1.4"/>\n'
        s += f'<rect x="128" y="{y}" width="480" height="{h}" rx="10" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
        s += t(148, y + 36, cap, 19, p["text"], weight=600)
        s += f'<rect x="{592 - 12 - len(tag) * 8.4 - 16:.0f}" y="{y + 16}" width="{len(tag) * 8.4 + 16:.0f}" height="26" rx="13" fill="none" stroke="{p["line"]}"/>\n'
        s += t(592 - 12 - 8, y + 34, tag, 14, p["text2"], "m", anchor="end")
        for j, mline in enumerate(monos):
            s += t(148, y + 62 + j * 22, mline, 14, p["text2"], "m")
    # Amber escalation arrows in the gaps.
    gaps = [(112, 144), (268, 300), (380, 412)]
    for k, (a, b) in enumerate(gaps):
        s += f'<line x1="368" y1="{a + 2}" x2="368" y2="{b - 2}" stroke="{p["warn"]}" stroke-width="2" marker-end="url(#m-warn)"/>\n'
        if k == 0:
            s += t(380, a + 22, "fails or times out: next rung", 14, p["warn"], weight=600)
    # Right column notes.
    s += f'<rect x="632" y="32" width="244" height="168" rx="10" fill="none" stroke="{p["line"]}"/>\n'
    s += t(652, 92, "$0", 48, p["text"], weight=650)
    s += t(652, 124, "metered spend.", 17, p["text"], weight=600)
    s += t(652, 152, "Nothing billed per token", 16, p["text2"])
    s += t(652, 174, "is ever called.", 16, p["text2"])
    s += f'<rect x="632" y="216" width="244" height="216" rx="10" fill="none" stroke="{p["line"]}"/>\n'
    s += t(652, 250, "A model gets a rung", 17, p["text"], weight=600)
    s += t(652, 274, "only after it proves itself:", 17, p["text"], weight=600)
    s += check(652, 296, p["text2"], 16, 2)
    s += t(676, 310, "agentic probe passes", 14, p["text2"], "m")
    s += check(652, 324, p["text2"], 16, 2)
    s += t(676, 338, "served model ==", 14, p["text2"], "m")
    s += t(676, 358, "requested model", 14, p["text2"], "m")
    s += t(652, 392, "(probe: a throwaway repo,", 14, p["muted"])
    s += t(652, 410, "a real file write, a check)", 14, p["muted"])
    return s + "</svg>\n"


# 5. Agent said done vs verifier proved it --------------------------------------

def compare(p, slug):
    W, H = 900, 436
    s = head(slug, W, H, "Agent said done versus verifier proved it",
             "Same free model, same day. A prose rewrite with no check deleted 387 of 389 lines and was reported "
             "as a success. A task whose result grep could check was correct, and exit 0 proved it.", p)
    s += t(24, 46, "Same model, same day. The difference was a test.", 22, p["text"], weight=650)
    cards = [
        (24, "No check", [("Task", "Rewrite three prose strings", "s"), ("Check", "none", "m"),
                          ("Agent said", "success", "m"), ("Reality", "\u2212387 of 389 lines", "m")],
         "bad", "Wrong, and reported as done"),
        (460, "With a check", [("Task", "Delete two project entries", "s"), ("Check", "grep returns empty", "m"),
                               ("Agent said", "success", "m"), ("Verifier", "exit 0", "m")],
         "ok", "Proved by a command"),
    ]
    for x, hdr, rows, tone, foot in cards:
        s += f'<rect x="{x}" y="72" width="416" height="316" rx="10" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
        s += f'<path d="M{x} 118 V82 a10 10 0 0 1 10 -10 H{x + 406} a10 10 0 0 1 10 10 V118 Z" fill="{p["s2"]}"/>\n'
        s += f'<line x1="{x}" y1="118" x2="{x + 416}" y2="118" stroke="{p["line"]}"/>\n'
        s += t(x + 20, 101, hdr, 16, p["text"], weight=600)
        for j, (k, v, cls) in enumerate(rows):
            y = 156 + j * 44
            s += t(x + 20, y, k, 16, p["text2"])
            col = p["text"]
            if j == 3:
                col = p[tone]
            s += t(x + 132, y, v, 18 if cls == "s" else 17, col, cls, weight=600 if j == 3 else 400)
        s += f'<path d="M{x} 332 H{x + 416} V378 a10 10 0 0 1 -10 10 H{x + 10} a10 10 0 0 1 -10 -10 Z" fill="{p[tone + "Soft"]}"/>\n'
        s += f'<line x1="{x}" y1="332" x2="{x + 416}" y2="332" stroke="{p[tone]}" stroke-opacity="0.6"/>\n'
        s += (check if tone == "ok" else cross)(x + 20, 351, p[tone], 18, 2.4)
        s += t(x + 48, 367, foot, 19, p[tone], weight=600)
    s += t(24, 420, "If no command can prove a task worked, the task does not go in the queue.", 16, p["text2"])
    return s + "</svg>\n"


# 6. Proof strip ---------------------------------------------------------------

def proof(p, slug):
    W, H = 900, 212
    s = head(slug, W, H, "What is proven",
             "Nine of nine queued tasks passed their own test; zero dollars of metered spend; 574 models listed "
             "but only 20 answered as themselves; 1,497 tests in this repository.", p)
    cells = [
        ("9 / 9", "ok", "tasks passed", "their own test", "ledger.jsonl"),
        ("$0", "text", "metered model", "spend", "ledger.jsonl"),
        ("574 → 20", "text", "models listed vs.", "served as asked", "MODEL_SWEEP.md"),
        ("1,497", "text", "tests in", "this repository", "tests/run.py"),
    ]
    cw, gap, x0 = 204, 12, 24
    for i, (big, tone, l1, l2, src) in enumerate(cells):
        x = x0 + i * (cw + gap)
        s += f'<rect x="{x}" y="20" width="{cw}" height="172" rx="10" fill="{p["s1"]}" stroke="{p["line"]}"/>\n'
        s += t(x + 18, 76, big, 40, p[tone], weight=650, extra=' letter-spacing="-0.5"')
        s += t(x + 18, 110, l1, 17, p["text"])
        s += t(x + 18, 132, l2, 17, p["text"])
        s += f'<line x1="{x + 18}" y1="150" x2="{x + cw - 18}" y2="150" stroke="{p["line"]}"/>\n'
        s += t(x + 18, 174, src, 14, p["text2"], "m")
    return s + "</svg>\n"


VISUALS = {
    "hero": hero,
    "flow": flow_wide,
    "verify-gate-anim": gate_anim,
    "escalation-ladder": ladder,
    "said-vs-proved": compare,
    "proof-strip": proof,
}


def main() -> None:
    for name, fn in VISUALS.items():
        for theme, pal in PALETTES.items():
            slug = f"{name}-{theme}"
            (OUT / f"{slug}.svg").write_text(fn(pal, slug), encoding="utf-8", newline="\n")
            print(f"wrote docs/img/{slug}.svg")


if __name__ == "__main__":
    main()
