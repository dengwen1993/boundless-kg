"""Project-wide logging configuration.

Public entry point: :func:`setup_logging`.  One call wires the root logger to
``<project_root>/logs/``：

    logs/debug.log   DEBUG 及以上（含全部细节，排查问题用）
    logs/info.log    INFO  及以上（正常运行流水）
    logs/error.log   ERROR 及以上（异常 / 崩溃）

外加可选的控制台输出（stderr）。全部文件按 10MB × 5 份滚动，不会无限增长。

用法（任意入口，调一次即可，重复调用是 no-op）::

    from src.observability.logging_config import setup_logging
    setup_logging()

之后项目任何模块照常 ``logger = logging.getLogger(__name__)``
再 ``logger.debug/info/warning/error(...)`` 即可，无需其它改动。

Why opt-in (not auto-installed at import)
-----------------------------------------
``src/__init__`` 等模块会被测试 / REPL / 下游工具导入，它们不该被动创建
``logs/`` 目录。所以由运行时入口（``src/cli/main.py``、``src/api/server.py``）
显式调用。
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PREFIX = "kg_log:"
_FMT = "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
# 第三方库噪声：只在它们出问题时才记录
_NOISY = ("httpx", "httpcore", "openai", "anthropic", "urllib3", "asyncio", "watchfiles")

#: (文件名, 级别) —— 增删一行即可增删一类日志文件
_FILES: tuple[tuple[str, int], ...] = (
    ("debug.log", logging.DEBUG),
    ("info.log", logging.INFO),
    ("error.log", logging.ERROR),
)


def _project_root() -> Path:
    """``src/observability/`` → 上溯两级即项目根。"""
    return Path(__file__).resolve().parents[2]


def _resolve_log_dir(log_dir: Path | None) -> Path:
    """显式参数 > ``KG_LOG_DIR`` 环境变量 > ``<project_root>/logs``。

    ``KG_LOG_DIR`` 的存在是为了让测试把日志重定向到 tmp 目录。
    此前集成测试通过 ``create_app()`` 间接调用 ``setup_logging()``，
    把 fixture 的假异常（``domain='d'`` 之类）写进了真实的
    ``logs/error.log``，导致排查线上问题时满屏都是测试噪声。
    """
    if log_dir is not None:
        return Path(log_dir)
    env_dir = os.environ.get("KG_LOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return _project_root() / "logs"


def setup_logging(
    log_dir: Path | None = None,
    console_level: int | str | None = logging.INFO,
    debug: bool = True,
) -> Path:
    """Attach the project file handlers (+ console) to the root logger.

    Parameters
    ----------
    log_dir
        日志目录。默认取 ``KG_LOG_DIR`` 环境变量，未设置则用
        ``<project_root>/logs``。
    console_level
        控制台（stderr）级别；传 ``None`` 表示不输出到控制台。
    debug
        是否写 ``debug.log``。关闭可显著减少磁盘写入。

    Returns
    -------
    Path
        日志目录（已创建）。

    Notes
    -----
    幂等：以 handler 名称去重，重复调用不会重复挂载。
    """
    log_dir = _resolve_log_dir(log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    existing = {getattr(h, "name", None) for h in root.handlers}
    formatter = logging.Formatter(fmt=_FMT, datefmt=_DATEFMT)

    for filename, level in _FILES:
        if level == logging.DEBUG and not debug:
            continue
        name = _PREFIX + filename
        if name in existing:
            continue
        handler = RotatingFileHandler(
            log_dir / filename,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.name = name
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if console_level is not None and _PREFIX + "console" not in existing:
        if isinstance(console_level, str):
            console_level = getattr(logging, console_level.upper(), logging.INFO)
        # Windows 终端默认 GBK，中文日志会乱码 —— 强制 UTF-8
        with contextlib.suppress(AttributeError, ValueError):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        console = logging.StreamHandler(stream=sys.stderr)
        console.name = _PREFIX + "console"
        console.setLevel(console_level)
        console.setFormatter(formatter)
        root.addHandler(console)

    # root 必须放行到最低的 handler 级别，否则 handler 收不到记录。
    # 基于「当前全部 handler」计算，这样重复调用不会把级别意外抬高。
    levels = [h.level for h in root.handlers if h.level] or [logging.INFO]
    root.setLevel(min(levels))
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_dir


def setup_error_logging(log_path: Path | None = None, level: int = logging.ERROR) -> Path:
    """Deprecated：保留旧签名，转发到 :func:`setup_logging`。"""
    log_dir = log_path.parent if log_path is not None else None
    setup_logging(log_dir=log_dir, console_level=None)
    return _resolve_log_dir(log_dir) / "error.log"


__all__ = ["setup_logging", "setup_error_logging"]
