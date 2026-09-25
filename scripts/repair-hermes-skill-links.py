#!/usr/bin/env python3
r"""Repair the dangling skill junctions that abort `hermes profile list`.

39 entries in the Hermes skills directory are Windows **directory junctions**
pointing into ~/.agents/skills/, which still exists but now holds only four
entries. One dangling entry raises FileNotFoundError during the profile scan
and takes the whole listing with it, so profiles cannot be managed at all.

These are junctions, not symlinks: `Path.is_symlink()` returns False, `lstat`
reports a directory, `os.readlink()` succeeds and returns a `\\?\` path, and
they are removed with `os.rmdir`, not `unlink`. Git Bash reports them as
symlinks, which is what made the first version of this script see nothing.

Nothing is deleted. A skill that still exists somewhere is re-junctioned to
that copy; one with no copy anywhere has its target string recorded and the
dead junction removed, so it can be restored by hand from the mapping.

Run with --apply; the default is a dry run.
"""
import argparse, json, os, pathlib, subprocess, sys
from datetime import datetime, timezone

HOME = pathlib.Path.home()
HERMES_SKILLS = HOME / "AppData" / "Local" / "hermes" / "skills"
ACTIVE = HOME / ".claude" / "skills"
ARCHIVE = HOME / ".claude" / "skills-archive"
RECORD = HOME / ".tri-ai" / "ops" / "hermes-skill-link-repair.json"


def link_target(p: pathlib.Path):
    """The junction's target, or None if p is not a reparse point."""
    try:
        return os.readlink(p)
    except OSError:
        return None


def dangling():
    out = []
    for p in sorted(HERMES_SKILLS.iterdir()):
        tgt = link_target(p)
        if tgt is not None and not p.exists():
            out.append((p, tgt))
    return out


def copy_of(name: str):
    for root in (ACTIVE, ARCHIVE):
        cand = root / name
        if cand.is_dir():
            return cand
    return None


def make_junction(link: pathlib.Path, target: pathlib.Path) -> None:
    """mklink /J - a junction needs no privilege, unlike a real symlink."""
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True, capture_output=True, text=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not HERMES_SKILLS.is_dir():
        print(f"not found: {HERMES_SKILLS}")
        return 1

    links = dangling()
    print(f"{len(links)} dangling junction(s) in {HERMES_SKILLS}\n")
    if not links:
        return 0

    repoint = [(p, t, copy_of(p.name)) for p, t in links if copy_of(p.name)]
    orphan = [(p, t) for p, t in links if not copy_of(p.name)]

    for p, _, tgt in repoint:
        where = "active" if str(tgt).startswith(str(ACTIVE)) else "archive"
        print(f"  REPOINT  {p.name:<32} -> {where}")
    for p, _ in orphan:
        print(f"  RECORD   {p.name:<32} (no copy anywhere; junction removed)")

    print(f"\nrepoint {len(repoint)} · record-and-remove {len(orphan)}")
    if not args.apply:
        print("\ndry run. re-run with --apply.")
        return 0

    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "hermes_skills": str(HERMES_SKILLS),
        "note": "entries are Windows directory junctions, removed with rmdir",
        "repointed": [], "removed_no_copy": [], "failed": [],
    }

    for p, was, tgt in repoint:
        try:
            os.rmdir(p)                      # a junction, not a file
            make_junction(p, tgt)
            record["repointed"].append({"name": p.name, "was": was, "now": str(tgt)})
        except (OSError, subprocess.CalledProcessError) as e:
            detail = getattr(e, "stderr", "") or str(e)
            try:                              # put the dead junction back
                if not p.exists() and link_target(p) is None:
                    make_junction(p, pathlib.Path(was))
            except Exception:
                pass
            record["failed"].append({"name": p.name, "error": str(detail)[:200]})

    for p, was in orphan:
        try:
            os.rmdir(p)
            record["removed_no_copy"].append({"name": p.name, "was": was})
        except OSError as e:
            record["failed"].append({"name": p.name, "error": f"{type(e).__name__}: {e}"})

    RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nrepointed        {len(record['repointed'])}")
    print(f"removed (no copy) {len(record['removed_no_copy'])}")
    print(f"failed            {len(record['failed'])}")
    for f in record["failed"]:
        print(f"   {f['name']}: {f['error']}")
    print(f"\nmapping written to {RECORD}")
    print("every original target string is in that file; restore with mklink /J")
    return 0 if not record["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
