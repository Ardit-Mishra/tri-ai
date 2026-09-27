# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The operator uses Tri-AI from a desktop or phone to inspect delegated work,
understand which model and tools are involved, and decide whether evidence is
sufficient to release a result. A recruiter or collaborator may inspect the
sealed public demonstration without gaining access to operator data.

## Product Purpose

Tri-AI turns a scoped request into a reviewable delivery through research,
planning, specialist execution, verification, retained evidence, and a human
release decision.

## Positioning

Tri-AI treats an agent's report as a claim rather than evidence: a defined
verification command determines whether a delegated task is accepted.

## Operating Context

The private system will connect user-authorized work sources such as local
machines, Obsidian, Drive, GitHub, Vercel, Render, conversation systems, and
inboxes. The public dashboard remains a sealed demonstration that never reads
those sources.

## Capabilities and Constraints

- The dashboard is read-only and public mode is fixed to demonstration data.
- The operator needs visible model routing, specialist activity, retained
  memory, source provenance, and release gates.
- Visual motion must communicate real state in private mode and be explicitly
  labeled when it illustrates a demonstration scenario.
- No public surface may expose credentials, private paths, task logs,
  artifacts, live workspaces, or private-source content.

## Brand Commitments

The product should feel like a personal intelligence environment rather than a
generic AI chat app or a decorative cyberpunk dashboard. It may use an animated
neural core, but clarity, evidence, and operator control take priority.

## Evidence on Hand

- `src/dashboard/kaya_web.py`: read-only dashboard and sealed demo server.
- `src/dashboard/tri_space.js`: interactive three-dimensional neural topology.
- `docs/SHOWCASE.md`: public demonstration boundary.
- A user-supplied screen recording establishes the desired spatial, animated
  intelligence-workspace reference; it is inspiration, not a source of
  functionality claims.

## Product Principles

1. Evidence is visible and attributable.
2. The system's center is an understandable work graph, not a chatbot avatar.
3. Motion reveals activity, routing, or state; it is never a substitute for it.
4. Public demonstrations are compelling but structurally unable to leak
   private operator data.
5. The interface makes human approval and model responsibility legible.

## Accessibility & Inclusion

The dashboard supports keyboard navigation, visible focus, readable contrast,
and `prefers-reduced-motion`. Its responsive view preserves access to the
system map and inspection detail on a phone.
