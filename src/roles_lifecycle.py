"""Full product-lifecycle roles, from ideation through monetisation and marketing.

`capabilities.py` defines seven roles that cover research, design, build, review
and release. That is enough to change a codebase and not enough to ship a
product: nothing in it prices anything, takes a payment, measures a funnel,
writes a launch post, or answers whether the thing is legal to collect data
with.

This module adds the missing phases. It is additive by design -- it mutates
nothing and returns new mappings, so a caller merges it explicitly and the
original seven roles keep working untouched.

Two rules carried over from `capabilities.py`, because they are the point:

  * A capability is an *instruction plus named local skills*. It is never
    ambient authority. Nothing here grants a credential, relaxes a sandbox, or
    weakens the verifier.
  * A role that can send, spend or publish is marked `external=True`. Those
    never auto-accept -- the operator approves them, which is the operator's
    own third rule of thumb.

Every skill named below was verified present in this machine's skill estate at
the time of writing. A name that later disappears surfaces as an unmatched
skill in `capability_catalog.match_resources`, not as a silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from capabilities import CapabilitySpec, RoleSpec


@dataclass(frozen=True)
class LifecycleRole:
    """A role plus the scheduling facts the planner needs.

    `phase` orders the build. `lane` is the default routing alias -- a *hint*
    for the model selector, never a guarantee; the selector may override it
    from measured ledger outcomes. `external` marks a role whose output leaves
    the machine.
    """
    spec: RoleSpec
    phase: str
    lane: str
    external: bool = False


PHASES = (
    "ideation", "design", "build", "harden",
    "ship", "monetise", "market", "govern",
)

# --------------------------------------------------------------------------
# Capabilities. Instruction first, then the local skills that teach it.
# --------------------------------------------------------------------------

LIFECYCLE_CAPABILITIES: dict[str, CapabilitySpec] = {
    "user_research": CapabilitySpec(
        "Talk to or model the actual user before proposing features. Produce jobs-to-be-done, "
        "not personas made of adjectives. Name the pain, how often it occurs, and what the user "
        "does about it today.",
        ("marketing-customer-research", "market-research"),
    ),
    "market_analysis": CapabilitySpec(
        "Size the market and locate the product in it. State who already serves this audience, "
        "what they charge, and the specific gap being entered. No unsourced claims.",
        ("marketing-competitor-profiling", "market-research", "marketing-product-marketing"),
    ),
    "brand_identity": CapabilitySpec(
        "Establish name, voice, palette and typographic identity specific to this product and "
        "audience. Reject the generic default; state why each choice suits this subject.",
        ("ui-ux-pro-max", "marketing-content-strategy"),
    ),
    "ux_writing": CapabilitySpec(
        "Write the words in the interface: labels, empty states, errors, confirmations. Name "
        "things as the user recognises them. An error says what went wrong and how to fix it.",
        ("marketing-copywriting", "writing-skills"),
    ),
    "data_modelling": CapabilitySpec(
        "Design the schema and its migrations. Every migration applies and rolls back cleanly "
        "against production-shaped data before it counts as done.",
        ("database-security",),
    ),
    "integrations": CapabilitySpec(
        "Wire third-party APIs and webhooks. Assume every external call fails, arrives twice, or "
        "arrives out of order, and prove the handler is idempotent.",
        ("api-security",),
    ),
    "test_engineering": CapabilitySpec(
        "Write the checks that gate the work. A test that cannot fail proves nothing -- show it "
        "failing against the bug before it passes against the fix.",
        ("test-driven-development", "e2e-testing", "ai-regression-testing", "browser-testing-with-devtools"),
    ),
    "accessibility": CapabilitySpec(
        "Hold the interface to WCAG. Keyboard reachable, focus visible, contrast >= 4.5:1, "
        "headings nested, every control labelled.",
        ("a11y-debugging", "frontend-a11y", "accessibility"),
    ),
    "performance": CapabilitySpec(
        "Measure before optimising and report the delta. Set a byte and latency budget, then "
        "prove the change stays inside it.",
        ("react-performance",),
    ),
    "threat_modelling": CapabilitySpec(
        "Enumerate the trust boundaries and what crosses them before reviewing code. Name the "
        "attacker, the asset and the path.",
        ("security-performing-threat-modeling-with-owasp-threat-dragon", "supply-chain-security", "llm-security"),
    ),
    "infrastructure": CapabilitySpec(
        "Build reproducible deploys with a tested rollback. A rollback that is written down but "
        "never executed is not a rollback.",
        ("deployment-patterns", "cloud-k8s", "security-auditing-terraform-infrastructure-for-security"),
    ),
    "observability": CapabilitySpec(
        "Make failure visible before a user reports it. A forced 500 must reach a human, and "
        "every alert must name the thing to do about it.",
        ("cloud-k8s",),
    ),
    "pricing_strategy": CapabilitySpec(
        "Decide packaging and price from willingness-to-pay evidence, not from a competitor's "
        "screenshot. The price is visible without booking a call.",
        ("htmlx-pricing-page", "marketing-product-marketing", "business-growth-skills"),
    ),
    "payments_engineering": CapabilitySpec(
        "Implement checkout, entitlement, tax and dunning. Prove a free account cannot reach a "
        "paid route and a replayed webhook grants access exactly once.",
        ("agent-payment-x402", "api-security"),
    ),
    "growth_analytics": CapabilitySpec(
        "Instrument the funnel and measure time-to-first-value. Report what a real visit does, "
        "not what the model assumes it does.",
        ("marketing-analytics", "ab-testing", "growth-log", "business-growth-skills"),
    ),
    "seo": CapabilitySpec(
        "Earn the traffic technically and editorially: crawlable, titled, described, fast, and "
        "answering a question someone actually types.",
        ("seo", "marketing-seo-audit", "ai-seo", "90-day-seo-sprint", "seo-ahrefs"),
    ),
    "content_marketing": CapabilitySpec(
        "Produce launch and ongoing content from what the product genuinely does. No claim "
        "without a source; no superlative without a measurement.",
        ("marketing-content-strategy", "content-engine", "content-idea-generator", "article-writing"),
    ),
    "social_distribution": CapabilitySpec(
        "Draft platform-native posts and schedule them. Drafts are shown before anything is "
        "published -- publishing is always the operator's action.",
        ("linkedin-post-writer", "linkedin-content-planner", "linkedin-hook-extractor",
         "linkedin-repurposer", "linkedin-humanizer"),
    ),
    "launch_media": CapabilitySpec(
        "Turn the shipped product into a short demo that shows the thing working, from the real "
        "build rather than a mockup.",
        ("brag", "brag-slim", "htmlx-video-hyperframes"),
    ),
    "documentation": CapabilitySpec(
        "Write task-oriented docs with progressive disclosure. Every command shown must have "
        "been run.",
        ("docs-generator", "spec-writer", "ko-tech-writer"),
    ),
    "privacy_compliance": CapabilitySpec(
        "Say plainly what personal data is collected, where it goes, and on what basis. A third "
        "party receiving user data is disclosed in words a reader understands.",
        ("api-security",),
    ),
}


# --------------------------------------------------------------------------
# Roles. Phase decides ordering; lane is the default routing hint.
# --------------------------------------------------------------------------

def _role(label: str, mission: str, caps: set[str], phase: str,
          lane: str, external: bool = False) -> LifecycleRole:
    return LifecycleRole(RoleSpec(label, mission, frozenset(caps)), phase, lane, external)


LIFECYCLE_ROLES: dict[str, LifecycleRole] = {
    # ---- ideation -------------------------------------------------------
    "user_researcher": _role(
        "User Researcher",
        "Establish who this is for and what specifically hurts, before anyone proposes a feature.",
        {"user_research", "web_research", "competitor_research"},
        "ideation", "auto/best-free"),
    "market_analyst": _role(
        "Market Analyst",
        "Locate the product against what already exists and name the gap being entered.",
        {"market_analysis", "competitor_research", "web_research"},
        "ideation", "auto/best-free"),

    # ---- design ---------------------------------------------------------
    "brand_designer": _role(
        "Brand Designer",
        "Give the product an identity specific to its subject and audience, not a template.",
        {"brand_identity", "taste", "media_generation"},
        "design", "auto/best-coding"),
    "ux_writer": _role(
        "UX Writer",
        "Write every word the interface says, so the product explains itself without a manual.",
        {"ux_writing", "taste"},
        "design", "auto/best-free"),

    # ---- build ----------------------------------------------------------
    "frontend_builder": _role(
        "Frontend Builder",
        "Build the part people touch, to the design's states and the accessibility contract.",
        {"frontend_engineering", "motion_design", "accessibility", "codebase_memory"},
        "build", "deskollama/qwen2.5-coder:7b"),
    "backend_builder": _role(
        "Backend Builder",
        "Build the data paths, APIs and jobs, and make every one of them fail safely.",
        {"backend_engineering", "codebase_memory", "integrations"},
        "build", "deskollama/qwen2.5-coder:14b"),
    "data_engineer": _role(
        "Data Engineer",
        "Own the schema and its migrations, forwards and backwards.",
        {"data_modelling", "backend_engineering", "codebase_memory"},
        "build", "deskollama/qwen2.5-coder:14b"),
    "integrations_engineer": _role(
        "Integrations Engineer",
        "Connect third-party services so that duplicate, late and failed events are all survivable.",
        {"integrations", "backend_engineering", "security_review"},
        "build", "deskollama/qwen2.5-coder:14b"),

    # ---- harden ---------------------------------------------------------
    "test_engineer": _role(
        "Test Engineer",
        "Write the checks that decide whether each step is accepted, and prove they can fail.",
        {"test_engineering", "browser_qa", "codebase_memory"},
        "harden", "deskollama/qwen2.5-coder:14b"),
    "security_auditor": _role(
        "Security Auditor",
        "Find the path an attacker takes before a user finds it by accident.",
        {"threat_modelling", "security_review", "codebase_memory"},
        "harden", "auto/reasoning"),
    "accessibility_auditor": _role(
        "Accessibility Auditor",
        "Prove the product is operable by someone not using a mouse or a screen.",
        {"accessibility", "browser_qa"},
        "harden", "auto/best-free"),
    "performance_engineer": _role(
        "Performance Engineer",
        "Hold the product to a measured budget rather than a feeling of speed.",
        {"performance", "browser_qa", "codebase_memory"},
        "harden", "auto/best-coding"),

    # ---- ship -----------------------------------------------------------
    "devops_engineer": _role(
        "DevOps Engineer",
        "Make the deploy reproducible and the rollback proven, on a clean clone.",
        {"infrastructure", "deployment_prepare", "cloud_infrastructure"},
        "ship", "auto/best-coding"),
    "observability_engineer": _role(
        "Observability Engineer",
        "Ensure a failure in production reaches a human with the thing to do about it.",
        {"observability", "cloud_infrastructure", "backend_engineering"},
        "ship", "auto/best-coding"),

    # ---- monetise -------------------------------------------------------
    "pricing_strategist": _role(
        "Pricing Strategist",
        "Decide what is sold, in what packages, at what price, from evidence of willingness to pay.",
        {"pricing_strategy", "market_analysis", "marketing"},
        "monetise", "auto/reasoning"),
    "payments_engineer": _role(
        "Payments Engineer",
        "Take money correctly: checkout, entitlement, tax, refunds and dunning.",
        {"payments_engineering", "backend_engineering", "security_review", "test_engineering"},
        "monetise", "auto/best-coding", external=True),

    # ---- market ---------------------------------------------------------
    "growth_engineer": _role(
        "Growth Engineer",
        "Instrument activation and retention so growth is measured rather than assumed.",
        {"growth_analytics", "frontend_engineering", "marketing"},
        "market", "auto/best-coding"),
    "content_marketer": _role(
        "Content Marketer",
        "Explain the product in public, in its own terms, with every claim sourced.",
        {"content_marketing", "seo", "marketing"},
        "market", "auto/best-free"),
    "social_marketer": _role(
        "Social Marketer",
        "Draft platform-native posts for the operator to approve. Never posts unprompted.",
        {"social_distribution", "content_marketing", "marketing"},
        "market", "auto/best-free", external=True),
    "launch_producer": _role(
        "Launch Producer",
        "Turn the shipped build into a demo that shows it actually working.",
        {"launch_media", "media_generation", "browser_qa"},
        "market", "auto/best-coding", external=True),

    # ---- govern ---------------------------------------------------------
    "docs_writer": _role(
        "Documentation Writer",
        "Write the docs a new reader can act on, with every command verified.",
        {"documentation", "codebase_memory", "diagram_design"},
        "govern", "auto/best-free"),
    "privacy_officer": _role(
        "Privacy Officer",
        "State what personal data is collected, where it goes, and on what basis.",
        {"privacy_compliance", "security_review", "web_research"},
        "govern", "auto/reasoning"),
}


def merged_capabilities(base: Mapping[str, CapabilitySpec]) -> dict[str, CapabilitySpec]:
    """Base capabilities plus the lifecycle ones. Never mutates `base`."""
    out = dict(base)
    out.update(LIFECYCLE_CAPABILITIES)
    return out


def merged_roles(base: Mapping[str, RoleSpec]) -> dict[str, RoleSpec]:
    """Base roles plus the lifecycle ones, flattened to plain RoleSpecs."""
    out = dict(base)
    out.update({name: r.spec for name, r in LIFECYCLE_ROLES.items()})
    return out


def roles_in_phase(phase: str) -> tuple[str, ...]:
    return tuple(n for n, r in LIFECYCLE_ROLES.items() if r.phase == phase)


def external_roles() -> frozenset[str]:
    """Roles whose output leaves the machine. These never auto-accept."""
    return frozenset(n for n, r in LIFECYCLE_ROLES.items() if r.external)


def default_lane(role: str) -> str | None:
    r = LIFECYCLE_ROLES.get(role)
    return r.lane if r else None


def unknown_capabilities() -> dict[str, tuple[str, ...]]:
    """Lifecycle roles referencing a capability neither module defines.

    Called by the test suite: a role granting a capability that does not exist
    would silently widen to nothing, which is the quiet kind of bug.
    """
    import capabilities as base
    known = set(base.CAPABILITIES) | set(LIFECYCLE_CAPABILITIES)
    bad: dict[str, tuple[str, ...]] = {}
    for name, role in LIFECYCLE_ROLES.items():
        missing = tuple(sorted(c for c in role.spec.allowed if c not in known))
        if missing:
            bad[name] = missing
    return bad
