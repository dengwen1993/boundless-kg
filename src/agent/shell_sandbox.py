"""Shell sandbox backend for deepagents.

Exposes a sandboxed shell-execution tool (``kg_shell_exec``) that lets
the agent run arbitrary commands (``bash make.sh ...``, ``python -m
pytest tests/``, ``git status``, etc.) inside the agent's working
directory.

Why we don't just call ``LocalShellBackend.execute`` directly
-------------------------------------------------------------
deepagents ships a ``LocalShellBackend`` with an ``execute(command,
timeout)`` method that's exactly what we need, **but** it calls
``subprocess.run(..., text=True)`` without an explicit ``encoding`` —
on Windows the default codec is the active code page (typically GBK /
cp936), which throws ``UnicodeDecodeError`` on any non-cp936 byte and
unrecoverably corrupts build output that contains UTF-8 / CJK text.

To get portable behaviour we keep ``LocalShellBackend`` (it owns the
path / virtual-mode plumbing and the timeout / max-output limits) but
route execution through :func:`_run_subprocess_async`, which drives
``subprocess.run`` ourselves with ``encoding="utf-8", errors="replace"``.
The result is wrapped in the same dict shape ``LocalShellBackend`` would
have produced so downstream code keeps a single contract.

Why async, not sync
-------------------
The wrapper calls blocking ``subprocess.run``.  Exposing it as a
synchronous ``@tool`` would freeze the LangGraph event loop for the
full command duration — that pauses the SSE stream for the user and
prevents the agent from streaming text while a long-running build
runs.  We dispatch through ``anyio.to_thread.run_sync`` so the loop
stays responsive.

Why a separate module
---------------------
The agent already has three other middlewares with their own backends
(memory / skills / FilesystemMiddleware).  Mixing a fourth into one of
those would conflate "agent's file sandbox" with "agent's OS sandbox"
and break permissions.  This module owns the fourth backend and exposes
a single async ``@tool`` rather than a whole middleware.

Security
--------
The shell is **unrestricted** — commands run with the agent's user
permissions and can read / write / spawn anything on the host.  This
is appropriate for the local KG-curation CLI use-case (the user is
the agent operator, no untrusted input) but should be replaced with a
real sandbox if this code ever powers a multi-tenant web service.

Activation
----------
``KG_AGENT_SHELL_ENABLED`` (default ``True``).  Set to ``False`` to
disable the shell tool without removing the module — useful for
auditing, demos, or production deployments that shouldn't expose an
arbitrary exec surface.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import anyio
from langchain_core.tools import tool

from deepagents.backends import LocalShellBackend

from src.config import get_kb_root, get_workspace_dir
from src.config.settings import get_agent_shell_timeout_sec
from src.observability.logged_tool import logged_tool

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_shell_backend() -> LocalShellBackend:
    """Return the shared ``LocalShellBackend`` rooted at the workspace.

    The shell's working directory is the agent workspace — the parent
    of both the curated knowledge tree (``knowledge_bases/``) and the
    runtime operational data (``.agent_memory/``, ``_pipeline/``,
    ``_staging/``).  From this root the agent can ``cd knowledge_bases``
    or ``cat .agent_memory/AGENTS.md`` with plain shell commands and
    never needs to know the absolute paths.

    The backend is used for its path / virtual-mode plumbing only —
    :func:`_run_subprocess_async` runs subprocesses directly.  Kept as
    a sibling object so the backend's ``max_output_bytes`` /
    ``virtual_mode`` / future ``env`` overrides remain a single
    config surface instead of two.
    """
    root = Path(get_workspace_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # ``kb_root`` is also created lazily so the very first invocation
    # of the agent doesn't crash before any data is written there.
    kb_root = Path(get_kb_root()).resolve()
    kb_root.mkdir(parents=True, exist_ok=True)
    return LocalShellBackend(
        root_dir=str(root),
        virtual_mode=False,           # shell sees the real FS, not virtual
        timeout=get_agent_shell_timeout_sec(),
        max_output_bytes=200_000,     # ~200 KB — large enough for build logs
        inherit_env=True,
    )


def _resolve_command(command: str) -> tuple[str, str]:
    """Pick the right interpreter for the first token on Windows.

    On Windows + Git Bash, ``bash`` (and friends) sometimes resolve via
    ``CreateProcess`` to a stub (Python / WindowsApps) that prints an
    unrelated error and exits non-zero.  ``shutil.which`` always returns
    a real binary if one exists on PATH.

    Returns ``(command, hint)``.  ``hint`` is a short string the tool
    result prepends when we had to substitute; empty string otherwise.
    """
    head = command.lstrip().split(maxsplit=1)[0] if command.strip() else ""
    hint = ""
    if head and not Path(head).is_absolute() and "/" not in head and "\\" not in head:
        resolved = shutil.which(head)
        if resolved and resolved != head:
            tail = command[len(head):]
            command = f"{resolved}{tail}"
            hint = f"(resolved `{head}` → `{resolved}`)"
    return command, hint


@dataclass
class _ShellResult:
    """Lightweight drop-in for ``ExecuteResponse`` from LocalShellBackend.

    We keep just the fields our wrapper actually consumes; anything
    else (truncated, etc.) is computed inline.
    """

    output: str
    exit_code: int
    truncated: bool = False


async def _run_subprocess_async(
    command: str,
    *,
    cwd: str,
    timeout: int | None,
    max_output_bytes: int,
) -> _ShellResult:
    """Run *command* via ``subprocess.run`` with explicit UTF-8 decoding.

    Why not ``LocalShellBackend.execute``
    ------------------------------------
    ``subprocess.run(text=True)`` defaults to the active code page on
    Windows, which throws ``UnicodeDecodeError`` on any non-cp936 byte.
    Forcing UTF-8 + ``errors="replace"`` gives us lossless output for
    English / CJK / emoji without the user having to ``chcp 65001``.
    """
    def _call() -> _ShellResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,    # never block on input
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                env=os.environ.copy(),
            )
            duration = time.monotonic() - t0
        except subprocess.TimeoutExpired:
            return _ShellResult(
                output=f"[timeout] command exceeded {timeout}s",
                exit_code=124,                # GNU timeout convention
                truncated=False,
            )
        except Exception as e:
            return _ShellResult(
                output=f"[error] {type(e).__name__}: {e}",
                exit_code=1,
                truncated=False,
            )

        # Mirror LocalShellBackend's "[stderr]" line attribution so
        # agents can tell streams apart in tool output.
        parts: list[str] = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            for line in proc.stderr.strip().splitlines():
                parts.append(f"[stderr] {line}")
        output = "\n".join(parts) if parts else "<no output>"
        truncated = False
        if len(output) > max_output_bytes:
            output = output[:max_output_bytes] + (
                f"\n\n... Output truncated at {max_output_bytes} bytes."
            )
            truncated = True
        return _ShellResult(output=output, exit_code=proc.returncode,
                            truncated=truncated)

    return await anyio.to_thread.run_sync(_call)


@tool
@logged_tool
async def kg_shell_exec(
    command: str,
    timeout: int | None = None,
) -> str:
    """Run a shell command in the agent's working directory.

    Args:
        command: Shell command string.  Examples: ``ls``, ``bash
            scripts/make.sh check``, ``python -m pytest tests/``.
            Multiple commands chain with ``;`` or ``&&``.
        timeout: Maximum runtime in seconds.  Defaults to
            ``KG_AGENT_SHELL_TIMEOUT_SEC`` (300s).  Pass ``0`` for
            no cap (use sparingly for genuinely long-running commands).

    Returns:
        Plain-text dump of the command, its output, and the exit code.
        Non-zero exits append a ``[exit N — non-zero]`` footer so the
        agent can grep for failures.
    """
    backend = get_shell_backend()
    cmd, hint = _resolve_command(command)
    effective_timeout = timeout if timeout not in (None, 0) else None

    try:
        result = await _run_subprocess_async(
            cmd,
            cwd=str(backend.cwd),
            timeout=effective_timeout,
            max_output_bytes=int(getattr(backend, "max_output_bytes", 200_000)),
        )
    except Exception as e:
        logger.warning("[shell_exec] command raised: %s", e)
        return f"❌ execute() raised: {type(e).__name__}: {e}"

    hint_line = (hint + "\n") if hint else ""
    header = f"$ {cmd}\n"
    if result.exit_code == 0:
        return f"{hint_line}{header}{result.output}\n[exit 0]"
    return (
        f"{hint_line}{header}{result.output}\n"
        f"[exit {result.exit_code} — non-zero; fix the command before retrying]"
    )


__all__: list[str] = ["kg_shell_exec", "get_shell_backend"]