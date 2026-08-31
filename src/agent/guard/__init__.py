"""L4 硬拦截中间件 —— 工具层强制校验 + 路径规范化。

包含两个独立的 hard gate：

* :class:`WriteClaimMiddleware` — BUG-005：拦截 AI 的「已写入 X.md」
  伪完成声明。详情见 ``knowledge_bases/.agent_memory/bugs.md`` BUG-005。
* :class:`PathNormalizeMiddleware` — BUG-2026-08-19-003：把
  ``/home/wend/boundless_kg/workspace/`` 前缀剥掉，避免 deepagents
  SDK 的 FilesystemMiddleware 把绝对路径当 cwd 相对路径写到错位目录。
"""

from __future__ import annotations

from .path_normalize import (
    PathNormalizeMiddleware,
    _normalize_path,
)
from .write_claim import (
    WriteClaimMiddleware,
    WriteClaimTracker,
    current_turn_writes,
    get_tracker,
    reset_tracker,
    scan_for_unauthorized_claim,
)

__all__ = [
    "PathNormalizeMiddleware",
    "WriteClaimMiddleware",
    "WriteClaimTracker",
    "_normalize_path",
    "current_turn_writes",
    "get_tracker",
    "reset_tracker",
    "scan_for_unauthorized_claim",
]