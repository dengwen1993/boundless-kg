"""BuildLogger — domain-level ``build.log`` for tracking knowledge-graph build progress.

Every significant pipeline event (intent parsing, hot-keyword collection,
hierarchical expansion, graph synthesis, persistence, etc.) appends a
timestamped line to ``<kb_root>/<domain>/build.log``.

This file is consumed by CLI/debugging tooling (e.g. ``cat build.log``
or the build log API) and is no longer exposed as an agent tool —
agents should prefer ``kg_check_status`` for live task progress and use
shell ``cat`` for full log inspection. Per-node logs are intentionally
NOT stored here; node-specific state lives in
``<domain>/notes/<node>/note.md`` and is only created on demand by the
note generator.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from src.config import get_kb_root


class BuildLogger:
    """Append-only timestamped logger for the domain-level build log.

    Each log line follows the format::

        [2025-07-24 12:34:56] [INFO] [stage_name] Human-readable message

    Files are created lazily on first write; parent directories are
    created as needed.  A size-based rotation prevents the log from
    growing without bound — when the file exceeds ``max_bytes``
    (default 5 MiB) only the last 200 lines are kept.
    """

    #: Maximum file size before rotation kicks in (5 MiB).
    MAX_LOG_BYTES: int = 5 * 1024 * 1024
    #: Number of tail lines to keep after rotation.
    ROTATE_KEEP_LINES: int = 200

    def __init__(self, kb_root: Path | None = None) -> None:
        self._kb_root = Path(kb_root or get_kb_root())

    @staticmethod
    def _safe_domain(domain: str) -> str:
        """Extract the real domain name from a possible 'domain / node' compound."""
        return domain.split(" / ")[0].split(" \\ ")[0].strip()

    def _domain_log_path(self, domain: str) -> Path:
        return self._kb_root / self._safe_domain(domain) / "build.log"

    @staticmethod
    def _format_line(stage: str, message: str, level: str = "INFO") -> str:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] [{level}] [{stage}] {message}\n"

    async def log_domain(
        self,
        domain: str,
        stage: str,
        message: str,
        level: str = "INFO",
    ) -> None:
        """Append an entry to the domain-level ``build.log``."""
        path = self._domain_log_path(domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._rotate_if_needed(path)
        line = self._format_line(stage, message, level)
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(line)

    async def _rotate_if_needed(self, path: Path) -> None:
        """Truncate the log to the last ``ROTATE_KEEP_LINES`` lines if it
        exceeds ``MAX_LOG_BYTES``.

        Uses synchronous file IO because it only runs on the infrequent
        rotation boundary — not on every write.
        """
        try:
            if not path.exists() or path.stat().st_size < self.MAX_LOG_BYTES:
                return
        except OSError:
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            kept = "\n".join(lines[-self.ROTATE_KEEP_LINES:]) + "\n"
            path.write_text(kept, encoding="utf-8")
        except OSError:
            pass

    async def read_domain_log(self, domain: str) -> str:
        """Return the full domain-level build.log, or empty string if missing.

        Tolerant of files that are not strict UTF-8 (e.g. a stray 0x89 byte
        left over from a GBK / mixed-encoding writer).  Non-decodable bytes
        are replaced with U+FFFD instead of raising ``UnicodeDecodeError``.
        """
        path = self._domain_log_path(domain)
        if not path.exists():
            return ""
        try:
            async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
                return await f.read()
        except UnicodeDecodeError:
            # Last-resort fallback: read as latin-1 (every byte is valid)
            # so the caller still gets *something* rather than an exception.
            async with aiofiles.open(path, encoding="latin-1") as f:
                return await f.read()

    @staticmethod
    def _parse_last_entry(content: str) -> dict[str, str]:
        """Extract structured info from the last non-empty log line."""
        lines = [l for l in content.strip().splitlines() if l.strip()]
        if not lines:
            return {"last_line": "", "stage": "", "level": "", "message": ""}
        last = lines[-1]
        # [ts] [level] [stage] message
        m = re.match(
            r"\[([^\]]+)\]\s*\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.*)",
            last,
        )
        if m:
            return {
                "last_line": last,
                "timestamp": m.group(1),
                "level": m.group(2),
                "stage": m.group(3),
                "message": m.group(4),
            }
        return {"last_line": last, "stage": "", "level": "", "message": ""}

    async def scan_domain(self, domain: str) -> dict[str, Any]:
        """Return a summary of the domain-level build.log.

        Returns::

            {
                "domain": "...",
                "domain_log_exists": bool,
                "domain_log_tail": "last 5 lines of domain build.log",
                "log_read_error": str | None,
            }

        ``log_read_error`` is populated when the file is unreadable as UTF-8
        even with the ``errors="replace"`` fallback (very rare — typically
        means the file is a PNG / binary blob at this path).  The tool layer
        surfaces it to the user instead of letting the exception propagate.
        """
        path = self._domain_log_path(domain)
        if not path.exists():
            return {
                "domain": domain,
                "domain_log_exists": False,
                "domain_log_tail": "",
                "log_read_error": None,
            }

        try:
            domain_log = await self.read_domain_log(domain)
        except UnicodeDecodeError as e:
            return {
                "domain": domain,
                "domain_log_exists": True,
                "domain_log_tail": "",
                "log_read_error": (
                    f"build.log is not valid UTF-8 (likely a binary file at "
                    f"this path): {e!r}"
                ),
            }
        except OSError as e:
            return {
                "domain": domain,
                "domain_log_exists": True,
                "domain_log_tail": "",
                "log_read_error": f"failed to read build.log: {e!r}",
            }

        domain_lines = [l for l in domain_log.strip().splitlines() if l.strip()]

        return {
            "domain": domain,
            "domain_log_exists": bool(domain_lines),
            "domain_log_tail": "\n".join(domain_lines[-5:]) if domain_lines else "",
            "log_read_error": None,
        }


__all__ = ["BuildLogger"]