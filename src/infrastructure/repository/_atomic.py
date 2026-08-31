"""Atomic file-write helper used by every repository."""

from __future__ import annotations

from pathlib import Path

import aiofiles


async def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically.

    Strategy: write to ``<path>.tmp`` first, then ``Path.replace()``
    onto the destination. On POSIX this is a single ``rename(2)``
    syscall; readers either see the old file or the new one, never a
    half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    async with aiofiles.open(tmp, "w", encoding=encoding) as f:
        await f.write(text)
    tmp.replace(path)


__all__ = ["atomic_write_text"]