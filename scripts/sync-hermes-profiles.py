#!/usr/bin/env python3
"""Create one Hermes profile per Tri-AI role, described so the decomposer routes.

`hermes profile create --description` says the text is "used by the kanban
decomposer to route tasks based on role instead of profile name alone." Every
role in capabilities.py / roles_lifecycle.py already carries a mission written
for exactly that purpose, so the two line up without inventing new prose.

Profiles are created lean: --no-alias (29 CLI wrappers would be noise) and
--no-skills (Tri-AI supplies skills through the capability contract, which is
the whole point of capability_catalog.match_resources).

Idempotent: an existing profile is described, not recreated.
"""
import argparse, json, subprocess, sys, pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import capabilities as base            # noqa: E402
import executor                        # noqa: E402
import roles_lifecycle as life         # noqa: E402

# The resolved path, not a bare name. Run over SSH or from a scheduled
# task there is no user PATH, which is the same trap that stopped
# decomposer reaching Hermes from the daemon.
HERMES = str(executor.hermes_bin())

BASE_PHASE = {
    "lead": "build", "researcher": "ideation", "product_strategist": "ideation",
    "designer": "design", "builder": "build", "reviewer": "harden",
    "operator": "ship",
}


def existing() -> set[str]:
    r = subprocess.run([HERMES, "profile", "list"],
                       capture_output=True, text=True, timeout=120)
    names = set()
    for line in r.stdout.splitlines():
        line = line.strip().lstrip("◆").strip()
        if not line or line.startswith(("Profile", "─", "-")):
            continue
        names.add(line.split()[0])
    return names


def description_for(role_id: str) -> str:
    if role_id in life.LIFECYCLE_ROLES:
        lr = life.LIFECYCLE_ROLES[role_id]
        spec, phase = lr.spec, lr.phase
    else:
        spec, phase = base.ROLES[role_id], BASE_PHASE.get(role_id, "build")
    caps = ", ".join(sorted(spec.allowed)[:6])
    return f"{spec.label}. {spec.mission} Phase: {phase}. Capabilities: {caps}."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    roles = list(base.ROLES) + list(life.LIFECYCLE_ROLES)
    have = existing()
    print(f"{len(roles)} roles · {len(have)} profiles already present\n")

    created, described, failed = [], [], []
    for rid in roles:
        desc = description_for(rid)
        action = "describe" if rid in have else "create"
        print(f"  {action:<9} {rid:<24} {desc[:62]}...")
        if not args.apply:
            continue
        try:
            if action == "create":
                subprocess.run(
                    [HERMES, "profile", "create", rid,
                     "--no-alias", "--no-skills", "--description", desc],
                    check=True, capture_output=True, text=True, timeout=240)
                created.append(rid)
            else:
                subprocess.run(
                    [HERMES, "profile", "describe", rid, desc],
                    check=True, capture_output=True, text=True, timeout=120)
                described.append(rid)
        except subprocess.CalledProcessError as e:
            failed.append({"role": rid, "error": (e.stderr or e.stdout or "")[-180:]})
        except subprocess.TimeoutExpired:
            failed.append({"role": rid, "error": "timeout"})

    if not args.apply:
        print("\ndry run. re-run with --apply.")
        return 0

    out = pathlib.Path.home() / ".tri-ai" / "ops" / "hermes-profiles.json"
    out.write_text(json.dumps(
        {"created": created, "described": described, "failed": failed}, indent=2),
        encoding="utf-8")
    print(f"\ncreated {len(created)} · described {len(described)} · failed {len(failed)}")
    for f in failed:
        print(f"   {f['role']}: {f['error'][:120]}")
    print(f"\nwritten to {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
