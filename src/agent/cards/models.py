"""Card data model + lightweight YAML-front-matter parser.

A card is a single Markdown file with a YAML front-matter block. The front-matter
describes *when* to inject the card (triggers, applies_to_tools); the body is the
actual LLM guidance rendered into the SystemMessage at selection time.

Why a custom parser instead of pulling in PyYAML:
  - Cards are tiny key:value blocks; PyYAML adds a dependency for almost no win.
  - We control the syntax — keeping it deliberately narrow lets us reject malformed
    files with a useful error message rather than yaml's generic ``ScannerError``.

Card file shape
---------------

::

    ---
    id: plans
    title: 学习计划原子拆分
    triggers: ["计划", "学习计划", "添加计划"]
    applies_to_tools: ["kg_add_plan"]
    priority: 10
    ---

    # Body (Markdown)

    详细规则正文……

If front-matter is missing entirely, the file is skipped (logged). If a required
key is missing or has a wrong type, :class:`CardParseError` is raised so the
loader can fail loud at boot, not silently at chat time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Card:
    """A single prompt-injection card.

    Attributes:
        id: Stable identifier (used in logs + debug payloads). Defaults to
            file stem if front-matter omits it.
        title: Short human-readable title (rendered as section header when injected).
        triggers: User-message keyword tokens; ANY match activates the card.
        applies_to_tools: Tool names; if any tool in the history matches,
            the card is activated. ``()`` means "no tool gating".
        priority: Rendering order (lower first). Cards with the same priority
            are sorted by id for stability.
        body: Markdown body verbatim; rendered into the SystemMessage when active.
        source_path: Absolute path to the source file (for error messages only).
    """

    id: str
    title: str
    triggers: tuple[str, ...] = field(default_factory=tuple)
    applies_to_tools: tuple[str, ...] = field(default_factory=tuple)
    priority: int = 100
    body: str = ""
    source_path: str = ""

    def matches_user_message(self, user_message: str) -> bool:
        """True if any trigger substring appears in *user_message* (case-sensitive).

        Empty triggers ⇒ never match (semantic: card is tool-only).
        """
        if not self.triggers or not user_message:
            return False
        return any(tok in user_message for tok in self.triggers)

    def matches_used_tools(self, used_tools: tuple[str, ...]) -> bool:
        """True if any of *used_tools* appears in ``applies_to_tools``.

        Empty ``applies_to_tools`` ⇒ never match (user-message-only card).
        Empty ``used_tools`` ⇒ no overlap possible → False.
        """
        if not self.applies_to_tools or not used_tools:
            return False
        return any(t in used_tools for t in self.applies_to_tools)

    def to_markdown(self) -> str:
        """Serialize back to the ``.md`` file format (front-matter + body).

        Round-trips with :func:`parse_card` — a card written by this method
        can be re-parsed losslessly. Used by ``kg_add_card`` / CLI ``cards
        add`` to persist agent-created cards.
        """
        lines: list[str] = ["---"]
        lines.append(f"id: {self.id}")
        lines.append(f"title: {self.title}")
        if self.triggers:
            items = ", ".join(f'"{t}"' for t in self.triggers)
            lines.append(f"triggers: [{items}]")
        else:
            lines.append("triggers: []")
        if self.applies_to_tools:
            items = ", ".join(f'"{t}"' for t in self.applies_to_tools)
            lines.append(f"applies_to_tools: [{items}]")
        else:
            lines.append("applies_to_tools: []")
        lines.append(f"priority: {self.priority}")
        lines.append("---")
        lines.append("")
        lines.append(self.body)
        return "\n".join(lines)


class CardParseError(ValueError):
    """Raised when a card file has malformed front-matter.

    The loader catches this and logs + skips the offending file, so one bad
    card never breaks the whole library.
    """


_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<meta>.*?)\n---[ \t]*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_card(path: Path) -> Card:
    """Read a single ``.md`` file and return its :class:`Card`.

    Raises :class:`CardParseError` on bad front-matter. The file must contain
    a YAML front-matter block delimited by ``---`` lines; without it the
    function raises as well — no-card-with-defaults ambiguity is worse than a
    loud failure during boot.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise CardParseError(f"missing YAML front-matter (expected '---' delimiters) in {path}")
    meta_raw = m.group("meta")
    body = m.group("body").rstrip("\n")

    meta = _parse_kv_block(meta_raw)
    cid = str(meta.get("id") or path.stem)
    title = str(meta.get("title") or cid)
    triggers = _as_str_list(meta.get("triggers"))
    applies = _as_str_list(meta.get("applies_to_tools"))
    priority = _as_int(meta.get("priority"), default=100)

    if not title.strip():
        raise CardParseError(f"{path}: 'title' is empty")

    return Card(
        id=cid,
        title=title,
        triggers=tuple(triggers),
        applies_to_tools=tuple(applies),
        priority=priority,
        body=body,
        source_path=str(path.resolve()),
    )


def _parse_kv_block(text: str) -> dict[str, object]:
    """Parse a tiny subset of YAML: ``key: value`` plus inline ``[a, b, c]`` lists.

    Supports the four keys we actually use (``id``, ``title``, ``triggers``,
    ``applies_to_tools``, ``priority``). Anything more complex → use real YAML.
    """
    out: dict[str, object] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise CardParseError(f"unparseable front-matter line: {raw!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                out[key] = []
            else:
                items = [_strip_quotes(p.strip()) for p in inner.split(",")]
                out[key] = [it for it in items if it]
        else:
            out[key] = _strip_quotes(value)
    return out


def _strip_quotes(s: str) -> str:
    """Strip matched surrounding single/double quotes."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _as_str_list(v: object) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(v)]


def _as_int(v: object, *, default: int) -> int:
    if v is None or v == "":
        return default
    try:
        return int(str(v).strip())
    except (TypeError, ValueError) as exc:
        raise CardParseError(f"priority must be integer, got {v!r}") from exc


__all__ = ["Card", "CardParseError", "parse_card"]
