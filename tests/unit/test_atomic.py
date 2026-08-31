"""Atomic write helper — write tmp + rename, no half-files.

Production callers wrap these calls in :func:`graph_lock`; the bare
helper is therefore tested in two ways:

  * Single-writer tests assert basic correctness.
  * Concurrent-writer tests run inside the shared lock to match how
    every repository actually invokes the helper (Windows holds a brief
    lock on the just-closed tmp file that would otherwise make the
    bare Path.replace() race).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from src.infrastructure.lock import graph_lock
from src.infrastructure.repository._atomic import atomic_write_text


class TestAtomicWrite:
    async def test_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "x.json"
        await atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    async def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "x.json"
        target.write_text("old", encoding="utf-8")
        await atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    async def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "x.json"
        await atomic_write_text(target, "abc")
        # The .tmp sibling must be cleaned up after rename.
        siblings = list(tmp_path.iterdir())
        assert siblings == [target]

    @pytest.mark.skipif(
        sys.platform.startswith("win"),
        reason="Windows holds a brief lock on the just-closed tmp file; "
        "production callers run inside graph_lock().",
    )
    async def test_bare_concurrent_writers_no_lock(self, tmp_path: Path) -> None:
        """Without the shared lock, two writers on POSIX can race the
        rename; we still assert no truncation but accept either winner."""
        target = tmp_path / "shared.json"

        async def writer(i: int) -> None:
            await atomic_write_text(target, f"value-{i}")

        await asyncio.gather(*(writer(i) for i in range(10)))
        text = target.read_text(encoding="utf-8")
        assert text.startswith("value-")

    async def test_lock_protected_concurrent_writers(self, tmp_path: Path) -> None:
        """This mirrors how every repository calls the helper — inside the
        shared lock. Final value must be a complete write (no truncation)."""
        target = tmp_path / "shared.json"

        async def writer(i: int) -> None:
            async with graph_lock():
                await atomic_write_text(target, f"value-{i}")

        await asyncio.gather(*(writer(i) for i in range(20)))
        text = target.read_text(encoding="utf-8")
        assert text.startswith("value-")
        assert "\n" not in text  # no half-write residue