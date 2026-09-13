"""The single execution path: claim → check → run → verify → accept or revert.

Every trigger routes through here, so the three assignment paths cannot drift
apart. That identity is a structural property, not something a test asserts
after the fact.

**Process creation happens in exactly two places** — `git()` and
`spawn_contained()` — and nowhere else in the audited module closure.
`run_agent()` and `run_verify()` both use the latter. `git()` and the agent take
an argv list and never a shell; the verify launcher is deliberately
unconstrained because the operator wrote that command. `tests/test_safety_boundary.py`
enforces the boundary by parsing this module's AST, not by grepping it:
`subprocess.run(["git", "push"])` contains no substring "git push".

What this module does NOT claim: that the agent it launches cannot push. It runs
under `HERMES_YOLO_MODE=1` with every shell approval auto-granted, so a
determined or confused agent can reach anything this user can. The environment
scrubbing below raises the cost of an *accident*; it is not containment. See
`.planning/phases/phase-2-plan.md` for what is and is not claimed, and for the
operator prerequisite (a credential-free account) that would actually close it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Prompt scaffolding, lifted from run_queue.py
# ---------------------------------------------------------------------------
#
# The cd preamble is load-bearing, not decoration. Hermes' terminal starts in
# the user's home directory and `cd` does NOT persist between its commands, so
# an un-prefixed command reports "not a git repository" from the wrong place and
# a perfectly good task looks broken.

CD_PREAMBLE = (
    "Your terminal starts in {home} and `cd` does NOT persist between commands. "
    "Prefix EVERY command with: cd {repo} && \n\n"
)

HARD_RULES = """
NON-NEGOTIABLE:
- Never run: git push, git merge, git rebase, any deploy command.
- Never modify .env, credentials, tokens, or keys.
- Touch ONLY the files this task names.
- If you cannot complete the task, say so plainly. Do NOT partially edit a file
  and report success — a half-finished edit is worse than an untouched one.
"""


# ---------------------------------------------------------------------------
# Gateway 1: git — exact argv forms only
# ---------------------------------------------------------------------------
#
# A subcommand-level allowlist is the wrong granularity: `stash` also spells
# `stash clear`, and `branch` also spells `branch -D`. So the COMPLETE argument
# vector is matched against a fixed table. `push`, `merge`, `remote`, `config`
# and `credential` are absent, which makes them unreachable rather than merely
# discouraged.

GIT_ALLOWED_FORMS: frozenset[tuple[str, ...]] = frozenset({
    ("status", "--porcelain"),
    ("branch", "--show-current"),
    ("rev-parse", "--show-toplevel"),
    ("stash", "list"),
})

# The only variable-argument form: the stash message carries task and run ids.
GIT_STASH_PUSH_PREFIX: tuple[str, ...] = (
    "stash", "push", "--include-untracked", "--message",
)


class DisallowedGitCommand(RuntimeError):
    """A git form not on the allowlist. Raised before any process is spawned."""


class WorktreeError(RuntimeError):
    """A linked checkout could not be proven created and isolated."""


def _git_form_allowed(argv: Sequence[str]) -> bool:
    form = tuple(argv)
    if form in GIT_ALLOWED_FORMS:
        return True
    # `stash push --include-untracked --message <msg>` — exactly one free arg.
    return len(form) == 5 and form[:4] == GIT_STASH_PUSH_PREFIX


def git(argv: Sequence[str], *, cwd: Path | str, timeout: int = 120) -> tuple[int, str]:
    """Run one allowlisted git form. Never a shell; never an arbitrary command."""
    if not _git_form_allowed(argv):
        raise DisallowedGitCommand(
            f"git form not allowed: {list(argv)!r}. "
            f"The worker may only run the exact forms in GIT_ALLOWED_FORMS."
        )
    proc = subprocess.run(
        ["git", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def verify_worktree(
    source: Path | str,
    target: Path | str,
    branch_name: str,
    *,
    timeout: int = 120,
) -> bool:
    """Prove ``target`` is this source's linked checkout on this branch."""
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if not source_path.is_dir() or not target_path.is_dir() or not _SAFE_BRANCH.fullmatch(branch_name):
        return False
    proc = subprocess.run(
        ["git", "-C", str(source_path), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        return False
    current_path: Path | None = None
    current_branch: str | None = None
    for line in (proc.stdout or "").splitlines() + [""]:
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
            current_branch = None
        elif line.startswith("branch refs/heads/"):
            current_branch = line.removeprefix("branch refs/heads/")
        elif not line and current_path is not None:
            if current_path == target_path and current_branch == branch_name:
                return True
            current_path = None
            current_branch = None
    return False


def materialize_worktree(
    source: Path | str,
    target: Path | str,
    branch_name: str,
    *,
    timeout: int = 120,
) -> None:
    """Create one linked checkout without deletion or network access.

    Kept separate from ``git()`` so its exact precheck/revert allowlist never
    expands into a generic worktree capability. Failure preserves every
    artifact for inspection; no cleanup command is issued here.
    """
    source_path = Path(source).resolve()
    raw_target = Path(target)
    target_path = raw_target.resolve()
    if not source_path.is_dir() or not (source_path / ".git").exists():
        raise WorktreeError(f"source is not a git checkout: {source_path}")
    if not raw_target.is_absolute() or target_path.exists():
        raise WorktreeError(f"worktree target must be a new absolute path: {target_path}")
    if source_path == target_path or source_path in target_path.parents:
        raise WorktreeError("worktree target may not be inside the source checkout")
    if not _SAFE_BRANCH.fullmatch(branch_name) or branch_name.startswith("-"):
        raise WorktreeError(f"unsafe worktree branch name: {branch_name!r}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "-C", str(source_path), "worktree", "add", "-b", branch_name,
         str(target_path), "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not verify_worktree(source_path, target_path, branch_name):
        raise WorktreeError(
            f"git worktree add failed for {target_path} ({branch_name}): {output.strip()}"
        )


# ---------------------------------------------------------------------------
# Process-tree termination
# ---------------------------------------------------------------------------
#
# subprocess timeout kills the immediate child. Under `shell=True` on Windows
# that is cmd.exe, and its descendants survive — so a verify command that spawns
# a writer can "time out", keep writing, and race the revert and the next claim.
# That corruption looks like nothing went wrong, which is the worst shape a bug
# can take here.

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p,
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD

    _JOB_LIMIT_KILL_ON_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _JobObjectBasicProcessIdList = 3
    _CREATE_SUSPENDED = 0x00000004
    _TH32CS_SNAPTHREAD = 0x00000004
    _THREAD_SUSPEND_RESUME = 0x0002
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32.Thread32First.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32),
    )
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32),
    )
    _kernel32.Thread32Next.restype = wintypes.BOOL

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOB_BASIC_LIMITS(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOB_EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOB_BASIC_LIMITS),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _make_job():
        """A job whose members die with it. None if the OS refuses."""
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JOB_EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = _JOB_LIMIT_KILL_ON_CLOSE
        ok = _kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle),
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            _kernel32.CloseHandle(wintypes.HANDLE(handle))
            return None
        return handle

    def _assign_to_job(job, proc):
        return bool(
            _kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle))
            )
        )

    def _job_pids(job):
        """Pids still assigned to the job.

        Returns ``[-1]`` when the set cannot be enumerated, so an unknown state
        is never mistaken for an empty one.
        """
        capacity = 512
        for _ in range(4):
            size = 8 + capacity * ctypes.sizeof(ctypes.c_size_t)
            buf = (ctypes.c_char * size)()
            ok = _kernel32.QueryInformationJobObject(
                wintypes.HANDLE(job), _JobObjectBasicProcessIdList, buf, size, None
            )
            head = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD * 2)).contents
            assigned, returned = int(head[0]), int(head[1])
            if not ok and assigned > returned:
                capacity = max(assigned * 2, capacity * 2)
                continue
            if not ok:
                return [-1]
            ids = ctypes.cast(
                ctypes.byref(buf, 8), ctypes.POINTER(ctypes.c_size_t * returned)
            ).contents
            return [int(ids[i]) for i in range(returned)]
        return [-1]

    def _terminate_job(job):
        return bool(_kernel32.TerminateJobObject(wintypes.HANDLE(job), 1))

    def _close_job(job):
        return bool(_kernel32.CloseHandle(wintypes.HANDLE(job)))

    def _resume_process(proc) -> bool:
        """Resume the only thread in a root process created suspended.

        Python 3.14's Popen keeps the process handle but no longer exposes the
        primary-thread handle returned by CreateProcess. Because the process is
        still suspended it cannot yet have created another thread, so a Toolhelp
        snapshot identifies its sole thread without reopening a launch race.
        """
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snapshot or int(snapshot) == _INVALID_HANDLE_VALUE:
            return False
        try:
            entry = _THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = _kernel32.OpenThread(
                        _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID,
                    )
                    if not thread:
                        return False
                    try:
                        previous = _kernel32.ResumeThread(thread)
                        return previous != 0xFFFFFFFF
                    finally:
                        _kernel32.CloseHandle(thread)
                entry.dwSize = ctypes.sizeof(entry)
                ok = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            return False
        finally:
            _kernel32.CloseHandle(snapshot)


class ContainmentError(RuntimeError):
    """A child could not be proved contained before it was allowed to run."""


@dataclass
class Containment:
    """A child process plus everything it spawns, killable and checkable."""

    proc: subprocess.Popen
    job: Optional[int] = None

    def terminate_tree(self, *, grace: float = 30.0) -> tuple[bool, list[int]]:
        """Kill the whole set. Returns ``(proven_gone, surviving_pids)``.

        ``proven_gone`` is False whenever survival cannot be ruled out: a job we
        could not create, could not enumerate, or one that still reports members
        after the grace window. The caller must treat that as a quarantine, never
        as success.

        There is deliberately no "the parent already exited, so we are done"
        shortcut. That shortcut was the defect this replaced: a detached
        grandchild outlives its parent, is absent from any parent-child map, and
        kept writing after a timeout that had reported success.
        """
        if IS_WINDOWS and self.job is not None:
            _terminate_job(self.job)
            deadline = time.time() + grace
            pids = _job_pids(self.job)
            while pids and time.time() < deadline:
                time.sleep(0.05)
                pids = _job_pids(self.job)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return (pids == [], [p for p in pids if p > 0])

        if IS_WINDOWS:
            # Containment was never established, so survival cannot be ruled
            # out. Kill what we can reach and report the failure honestly.
            try:
                self.proc.kill()
                self.proc.wait(timeout=grace)
            except Exception:
                pass
            return (False, [self.proc.pid])

        import signal

        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return (False, [self.proc.pid])
        try:
            os.killpg(os.getpgid(self.proc.pid), 0)
        except (ProcessLookupError, OSError):
            return (True, [])
        return (False, [self.proc.pid])

    def close(self) -> None:
        if IS_WINDOWS and self.job is not None:
            _close_job(self.job)
            self.job = None


def spawn_contained(command, **popen_kwargs) -> Containment:
    """Start a process inside a container that can kill everything it spawns.

    On Windows the root process is created suspended, assigned to a Job Object,
    then resumed. Anything it later spawns joins the job automatically, so there
    is no interval in which a short-lived parent can create an untracked writer.
    If any step fails, the suspended/root process is terminated and no command is
    reported as having run under containment.
    """
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = (
            popen_kwargs.get("creationflags", 0)
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | _CREATE_SUSPENDED
        )
    else:
        popen_kwargs.setdefault("start_new_session", True)

    if IS_WINDOWS:
        job = _make_job()
        if job is None:
            raise ContainmentError("CreateJobObjectW failed")
        proc = None
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
            if not _assign_to_job(job, proc):
                raise ContainmentError("AssignProcessToJobObject failed")
            if not _resume_process(proc):
                raise ContainmentError("ResumeThread failed")
        except BaseException:
            if proc is not None:
                try:
                    proc.kill()
                    proc.communicate(timeout=5)
                except Exception:
                    pass
            _close_job(job)
            raise
        return Containment(proc, job)

    return Containment(subprocess.Popen(command, **popen_kwargs))


# ---------------------------------------------------------------------------
# Gateway 2: the agent
# ---------------------------------------------------------------------------

DEFAULT_HERMES = (
    Path.home() / "AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe"
)

# Names stripped from the child environment. This raises the cost of an
# accidental push; it is NOT containment — Windows Credential Manager, an
# inherited SSH agent, the system and repo git configs, `ssh` itself and the
# GitHub API all remain reachable to a process running as this user.
CREDENTIAL_ENV_NAMES = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_USERNAME", "GIT_PASSWORD",
)


def agent_env() -> dict[str, str]:
    """The child environment: inherited, minus credentials, plus hard defaults."""
    env = dict(os.environ)
    for name in CREDENTIAL_ENV_NAMES:
        env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def hermes_bin() -> Path:
    override = os.environ.get("TRIAI_HERMES_BIN", "").strip()
    return Path(override) if override else DEFAULT_HERMES


def build_prompt(repo: Path | str, prompt: str) -> str:
    home = str(Path.home()).replace("\\", "/")
    return CD_PREAMBLE.format(home=home, repo=repo) + prompt + HARD_RULES


@dataclass
class AgentResult:
    exit_code: int
    output: str
    seconds: float
    model: Optional[str] = None
    provider: Optional[str] = None
    model_source: str = "unavailable"
    tree_survived: bool = False       # True -> quarantine the workspace
    survivors: list[int] = field(default_factory=list)
    # The runtime's own record of whether its turn completed, read from the
    # usage file. None when no usage file was requested or it carried no such
    # field — absence of a record is not a record of success.
    runtime_failed: Optional[bool] = None


def run_agent(
    repo: Path | str,
    prompt: str,
    *,
    timeout: int,
    usage_path: Optional[Path] = None,
) -> AgentResult:
    """Launch Hermes one-shot. argv list, never a shell.

    Model provenance comes from ``--usage-file``, which records the model and
    provider of the run that actually executed (hermes_cli/oneshot.py). A
    configured model name would be an assertion, and the ledger is the one file
    that must contain none — so when the usage file is absent or lacks the
    field, ``model`` stays None and ``model_source`` says "unavailable".
    """
    argv = [str(hermes_bin()), "-z", build_prompt(repo, prompt)]
    if usage_path is not None:
        argv += ["--usage-file", str(usage_path)]

    # Contained on the same terms as the verify command. A timed-out Hermes run
    # is not a bare `subprocess.run(timeout=...)`: the agent spawns tool
    # processes of its own, and those would otherwise keep writing to the
    # workspace while verification and revert proceed — the same orphan defect
    # the verify path had, in a place that edits files by design.
    started = time.time()
    tree_survived = False
    survivors: list[int] = []
    try:
        contained = spawn_contained(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=agent_env(),
        )
    except (OSError, ContainmentError) as exc:
        return AgentResult(1, f"{type(exc).__name__}: {exc}", 0.0)

    try:
        out, _ = contained.proc.communicate(timeout=timeout)
        code = contained.proc.returncode
        gone, survivors = contained.terminate_tree(grace=5)
        tree_survived = not gone
        if not gone:
            out = (out or "") + f"\nagent process tree SURVIVED (pids {survivors})"
    except subprocess.TimeoutExpired:
        gone, survivors = contained.terminate_tree()
        tree_survived = not gone
        try:
            out, _ = contained.proc.communicate(timeout=15)
        except Exception:
            out = ""
        out = (out or "") + (
            f"\nagent TIMEOUT after {timeout}s; process tree "
            + ("terminated" if gone else f"SURVIVED (pids {survivors})")
        )
        code = 124
    finally:
        contained.close()
    elapsed = round(time.time() - started, 2)

    model = provider = None
    source = "unavailable"
    runtime_failed: Optional[bool] = None
    if usage_path is not None and usage_path.exists():
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            model = usage.get("model")
            provider = usage.get("provider")
            if model:
                source = "usage_file"
            # `failed` is written by the runtime itself (hermes_cli/oneshot.py
            # `_write_usage_file`), not asserted by the agent about its work.
            # It is the only signal that separates "the turn did not happen"
            # from "the turn happened", because a provider error printed as the
            # final response still exits 0.
            if "failed" in usage:
                runtime_failed = bool(usage.get("failed"))
        except (json.JSONDecodeError, OSError):
            pass

    return AgentResult(
        code, out, elapsed, model, provider, source,
        tree_survived=tree_survived, survivors=survivors,
        runtime_failed=runtime_failed,
    )


# ---------------------------------------------------------------------------
# Gateway 3: the verify command — deliberately unconstrained
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    """A discriminated outcome. `exit_code` is None for the non-exit cases.

    run_queue.py mapped a timeout to exit 124, but 124 is a value a real verify
    command may legitimately return, so the two were indistinguishable. Only
    ``passed`` accepts; everything else reverts.
    """
    outcome: str                      # passed | failed | timeout | spawn_error
    exit_code: Optional[int]
    output: str
    seconds: float
    tree_survived: bool = False       # True → quarantine the workspace
    survivors: list[int] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.outcome == "passed"


def run_verify(command: str, *, cwd: Path | str, timeout: int) -> VerifyResult:
    """Run the operator's verify command. THE single unconstrained gateway.

    `shell=True` is intentional: the operator authored this command and it is
    the oracle the whole system rests on. What is constrained is everything
    else — see `git()` above.
    """
    popen_kwargs: dict[str, Any] = dict(
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    started = time.time()
    try:
        contained = spawn_contained(command, **popen_kwargs)
    except (OSError, ContainmentError) as exc:
        return VerifyResult(
            "spawn_error", None, f"{type(exc).__name__}: {exc}",
            round(time.time() - started, 2),
        )

    try:
        out, _ = contained.proc.communicate(timeout=timeout)
        elapsed = round(time.time() - started, 2)
        code = contained.proc.returncode
        gone, survivors = contained.terminate_tree(grace=5)
        if not gone:
            return VerifyResult(
                "failed", code,
                (out or "") + f"\nverify process tree SURVIVED (pids {survivors})",
                elapsed, tree_survived=True, survivors=survivors,
            )
        return VerifyResult(
            "passed" if code == 0 else "failed", code, out or "", elapsed,
        )
    except subprocess.TimeoutExpired:
        gone, survivors = contained.terminate_tree()
        try:
            out, _ = contained.proc.communicate(timeout=15)
        except Exception:
            out = ""
        elapsed = round(time.time() - started, 2)
        note = (
            f"verify TIMEOUT after {timeout}s; process tree "
            + ("terminated" if gone else f"SURVIVED (pids {survivors})")
        )
        return VerifyResult(
            "timeout", None, (out or "") + "\n" + note, elapsed,
            tree_survived=not gone, survivors=survivors,
        )
    finally:
        contained.close()


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

@dataclass
class Precheck:
    ok: bool
    reason: str = ""
    branch: Optional[str] = None


def precheck(
    conn,
    repo: Path | str,
    *,
    expected_branch: Optional[str] = None,
) -> Precheck:
    """Refuse to run unless the repo is in a state the revert can undo.

    Three gates, in order of severity:

    1. **Quarantine.** A workspace stopped by a surviving process tree stays
       refused until an operator clears it — by every worker, not just the one
       that set it. Checked first because it outranks everything else.
    2. **Branch.** Working on the wrong branch is editing the wrong thing.
    3. **Clean tree.** The revert stashes whatever is present at failure time.
       If the tree was already dirty at claim time, that stash would capture
       work the task never touched. A dirty tree is a skip, not something to
       tidy: the worker never touches a repo it did not dirty.
    """
    import board  # local import keeps the module closure explicit

    quarantined = board.is_quarantined(conn, repo)
    if quarantined:
        return Precheck(
            False,
            f"workspace quarantined ({quarantined['reason']}, "
            f"task {quarantined['task_id']}) — operator must clear it",
        )

    code, out = git(["branch", "--show-current"], cwd=repo)
    if code != 0:
        return Precheck(False, f"cannot read branch: {out.strip()}")
    branch = out.strip()
    if expected_branch and branch != expected_branch:
        return Precheck(False, f"branch is {branch!r}, expected {expected_branch!r}", branch)

    code, out = git(["status", "--porcelain"], cwd=repo)
    if code != 0:
        return Precheck(False, f"cannot read status: {out.strip()}", branch)
    if out.strip():
        n = len(out.strip().splitlines())
        return Precheck(False, f"working tree not clean ({n} entries) — skipping", branch)

    return Precheck(True, branch=branch)


# ---------------------------------------------------------------------------
# Upstream artifacts (VERIFY-04)
# ---------------------------------------------------------------------------

class ArtifactEscape(ValueError):
    """A declared artifact path points outside its task's workspace."""


def resolve_artifact(workspace: Path | str, declared: str) -> Path:
    """Resolve a declared artifact path, refusing anything outside ``workspace``.

    Existence and non-emptiness are not sufficient on their own. An absolute
    path, a ``..`` traversal, or a symlink could point at an unrelated non-empty
    file and satisfy the check while proving nothing about the upstream task —
    a gate that cannot fail. Both sides are realpath'd before comparison so a
    symlink cannot straddle the boundary.
    """
    drive, _tail = os.path.splitdrive(declared)
    if drive or os.path.isabs(declared):
        raise ArtifactEscape(f"artifact path must be relative: {declared!r}")
    root = Path(os.path.realpath(str(workspace)))
    candidate = Path(os.path.realpath(str(root / declared)))
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ArtifactEscape(
            f"artifact {declared!r} resolves outside its workspace "
            f"({candidate} not under {root})"
        ) from None
    return candidate


def check_upstream_artifacts(conn, task_id: str) -> tuple[bool, str]:
    """Every parent's declared artifacts must exist, be non-empty, and be inside.

    Runs BEFORE the agent is invoked at all: a fabricated upstream result must
    not get a chance to propagate. Needs no git — it is a filesystem check.
    """
    import board

    kb = board.kanban()
    problems: list[str] = []
    for parent_id in kb.parent_ids(conn, task_id):
        spec = board.verify_spec(conn, parent_id)
        if spec is None:
            problems.append(f"parent {parent_id} not found")
            continue
        workspace = spec.get("repo")
        for declared in spec.get("expected_artifacts") or []:
            if not workspace:
                problems.append(f"{parent_id}: no workspace for {declared!r}")
                continue
            try:
                path = resolve_artifact(workspace, declared)
            except ArtifactEscape as exc:
                problems.append(f"{parent_id}: {exc}")
                continue
            if not path.exists():
                problems.append(f"{parent_id}: missing artifact {declared!r}")
            elif path.stat().st_size == 0:
                problems.append(f"{parent_id}: empty artifact {declared!r}")
    return (not problems), "; ".join(problems)


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

@dataclass
class RevertResult:
    outcome: str                       # stashed | no_changes_to_revert | failed
    stash_message: Optional[str] = None
    output: str = ""

    @property
    def clean(self) -> bool:
        return self.outcome in ("stashed", "no_changes_to_revert")


def stash_message(task_id: str, run_id: Optional[int]) -> str:
    """Per-attempt-unique, so a retry cannot match its predecessor's stash."""
    return f"triai-revert:{task_id}:{run_id if run_id is not None else 'norun'}"


def revert(repo: Path | str, *, task_id: str, run_id: Optional[int]) -> RevertResult:
    """Undo a failed task's changes without deleting anything.

    `git clean -fd` would be the obvious tool and it is the wrong one: the
    clean-tree precondition proves the tree was clean at CLAIM time and says
    nothing about who wrote to it afterwards — an editor, a watcher, a formatter
    or a child that outlived the agent's timeout all produce files
    indistinguishable from the task's own. So the revert stashes instead, and
    everything it takes is recoverable with `git stash pop`.

    Two limits, both measured rather than assumed: ignored files are untouched
    (`--include-untracked` is not `--all`), and an empty untracked directory is
    removed but cannot be restored, because git does not track directories.
    """
    message = stash_message(task_id, run_id)
    code, out = git(
        ["stash", "push", "--include-untracked", "--message", message],
        cwd=repo,
    )
    if code != 0:
        return RevertResult("failed", None, out)

    # A no-op stash prints "No local changes to save" and exits 0 WITHOUT
    # creating an entry. Recording a stash reference that does not exist would
    # be a fabricated recovery path in the one file that must contain none.
    listed_code, listed = git(["stash", "list"], cwd=repo)
    created = listed_code == 0 and message in listed
    if created:
        return RevertResult("stashed", message, out)
    return RevertResult("no_changes_to_revert", None, out)
