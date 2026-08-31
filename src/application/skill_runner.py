"""SkillRunner — subprocess wrapper for the bundled external skills.

The four skills shipped under ``src/skills/`` are:
  * knowledge-digest  — pure scenario guide (no subprocess; tells the agent
                        when to call which sibling skill)
  * minimax-pdf       — bash scripts/make.sh (Node.js + playwright + reportlab)
  * pptx-generator    — Node.js + pptxgenjs (called by writing JS + running node)
  * minimax-docx      — ``dotnet run --project scripts/dotnet/...`` (.NET SDK)

This module is used internally by generation flows that need to render an
artifact synchronously (e.g. the graph-generation pipeline writing a PDF
summary).  The agent itself does NOT call these runners directly — when
the user asks for a PDF/PPTX/DOCX the agent reads the corresponding
skill's SKILL.md and invokes the bundled script through deepagents'
SkillsMiddleware.  ``SkillRunner`` exists so non-agent code paths
(GenerationPipeline, tests, one-off scripts) share the same subprocess
harness instead of re-implementing availability checks and timeout
handling.

Why a class per skill (instead of free functions)?
    * Health-check state is computed once and reused across calls in the
      same process — avoids re-running ``which node`` per tool invocation.
    * Each skill has a different surface (bash script vs node CLI vs dotnet),
      so per-skill subclasses keep the call sites readable.
    * Subprocess timeouts and stdout/stderr capture live in the base class.

Run model
---------
Subprocess calls are *blocking* on purpose: the actual artifact production
is genuinely CPU/IO bound (Node.js rendering, Playwright headless browser,
.NET build).  Long-running jobs (>timeout) are surfaced as a timeout error
so the caller can decide whether to retry.

Windows note
------------
On Windows, ``bash`` resolves to Git Bash (ships with Git for Windows).
If Git Bash is not on PATH, minimax-pdf cannot run; the runner detects
this via ``shutil.which("bash")``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from src.config.settings import (
    get_skill_docx_path,
    get_skill_pdf_path,
    get_skill_pptx_path,
    get_skill_timeout_sec,
)

logger = logging.getLogger(__name__)


# ── Result type ─────────────────────────────────────────────────────


@dataclass
class SkillRunResult:
    """Outcome of a SkillRunner.run_* call.

    Attributes:
        ok: True iff the subprocess exited 0 AND the expected artifact
            exists on disk.
        exit_code: Subprocess exit code (None when not executed).
        stdout: Captured stdout (decoded UTF-8, errors='replace').
        stderr: Captured stderr.
        duration_sec: Wall-clock duration.
        artifact_path: Resolved absolute path of the produced artifact
            (when known).
        error: Human-readable error message (set when ``ok`` is False).
    """

    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    artifact_path: Path | None = None
    error: str | None = None

    def to_user_message(self) -> str:
        """Format for the caller to surface to the LLM / user."""
        if self.ok:
            return (
                f"✅ 生成成功：{self.artifact_path}\n"
                f"   耗时 {self.duration_sec:.1f}s"
            )
        lines = [f"❌ 生成失败：{self.error or 'unknown error'}"]
        if self.exit_code is not None:
            lines.append(f"   exit code: {self.exit_code}")
        # Surface BOTH streams.  Bash pipelines (minimax-pdf/make.sh) print
        # progress to stdout and the real traceback may land on either
        # stream depending on which sub-step died — showing only stderr is
        # how BUG-010 became an undiagnosable "unknown error".
        if self.stderr.strip():
            # Keep at most the last 30 lines so we don't blow the LLM context.
            tail = "\n".join(self.stderr.strip().splitlines()[-30:])
            lines.append(f"--- stderr (tail) ---\n{tail}")
        if self.stdout.strip():
            tail = "\n".join(self.stdout.strip().splitlines()[-30:])
            lines.append(f"--- stdout (tail) ---\n{tail}")
        if not self.stderr.strip() and not self.stdout.strip():
            lines.append(
                "   （子进程未输出任何内容——通常意味着解释器/依赖缺失，"
                "请先跑一次依赖体检）"
            )
        return "\n".join(lines)


# ── Base runner ─────────────────────────────────────────────────────


class SkillRunner:
    """Subprocess wrapper for an external skill.

    Construct via the factory methods (:meth:`for_pdf`, :meth:`for_pptx`,
    :meth:`for_docx`); do not instantiate directly.  Each subclass defines
    :meth:`_entry_script` (the command to invoke) and the public run_*
    methods shaped to that skill's CLI.
    """

    skill_name: str = "<base>"

    def __init__(self) -> None:
        self._available: bool | None = None
        self._missing: list[str] | None = None

    # ── Public introspection ────────────────────────────────────────

    def is_available(self) -> bool:
        """Cached availability check."""
        if self._available is None:
            self._available, self._missing = self._check_availability()
        return self._available

    def missing_dependencies(self) -> list[str]:
        """Human-readable list of missing binaries / paths."""
        if self._missing is None:
            self._available, self._missing = self._check_availability()
        return list(self._missing)

    # ── Internal API ────────────────────────────────────────────────

    def _check_availability(self) -> tuple[bool, list[str]]:
        """Return ``(available, missing)`` for this skill."""
        raise NotImplementedError

    def _run_subprocess(
        self,
        cmd: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> SkillRunResult:
        """Run *cmd* with shared timeout/env handling."""
        timeout = timeout_sec if timeout_sec is not None else get_skill_timeout_sec()
        import time

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                list(cmd),
                cwd=str(cwd) if cwd else None,
                env={**os.environ, **(env or {})},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration = time.monotonic() - t0
            return SkillRunResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_sec=duration,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - t0
            return SkillRunResult(
                ok=False,
                stderr=(e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                duration_sec=duration,
                error=f"超时（>{timeout:.0f}s）",
            )
        except FileNotFoundError as e:
            return SkillRunResult(ok=False, error=f"找不到可执行文件：{e}")
        except Exception as e:
            return SkillRunResult(ok=False, error=f"{type(e).__name__}: {e}")

    # ── Factories ───────────────────────────────────────────────────

    @classmethod
    def for_pdf(cls) -> "PdfSkillRunner":
        return PdfSkillRunner()

    @classmethod
    def for_pptx(cls) -> "PptxSkillRunner":
        return PptxSkillRunner()

    @classmethod
    def for_docx(cls) -> "DocxSkillRunner":
        return DocxSkillRunner()


# ── minimax-pdf runner ──────────────────────────────────────────────


class PdfSkillRunner(SkillRunner):
    """Wraps ``bash src/skills/minimax-pdf/scripts/make.sh``.

    The script supports ``run`` / ``reformat`` / ``fill`` subcommands with
    CLI flags documented in the SKILL.md.  We always invoke via ``bash`` so
    the .sh shebang line doesn't have to be executable on Windows.
    """

    skill_name = "minimax-pdf"

    def __init__(self) -> None:
        super().__init__()
        self._skill_root = get_skill_pdf_path()
        self._entry = self._skill_root / "scripts" / "make.sh"

    @staticmethod
    def _python_for_skill() -> str:
        """Interpreter handed to make.sh via ``$PDF_PYTHON``.

        ``sys.executable`` is the interpreter already running this server,
        so reportlab/pypdf resolve against the same environment the app was
        installed into.  Relying on the script's own ``python3`` lookup is
        what caused BUG-010 on Windows (Microsoft Store stub).
        """
        import sys

        return sys.executable or "python"

    def _check_availability(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if shutil.which("bash") is None:
            missing.append("bash（请安装 Git for Windows 并加入 PATH）")
        if shutil.which("node") is None:
            missing.append("node（minimax-pdf 的封面渲染需要 Node.js 18+）")
        if not self._entry.exists():
            missing.append(f"入口脚本不存在：{self._entry}")

        # Python-side rendering deps.  These are hard requirements: without
        # them make.sh dies mid-pipeline with a stack trace instead of a
        # clean "dependency missing" signal.
        py = self._python_for_skill()
        for mod, hint in (
            ("reportlab", "reportlab（正文渲染）"),
            ("pypdf", "pypdf（封面/正文合并）"),
        ):
            try:
                probe = subprocess.run(
                    [py, "-c", f"import {mod}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if probe.returncode != 0:
                    missing.append(f"{hint} — 安装：{py} -m pip install {mod}")
            except (OSError, subprocess.SubprocessError):
                missing.append(f"无法用 {py} 探测 {mod}")

        # Playwright renders the cover page; make.sh exits 2 without it.
        node = shutil.which("node")
        if node is not None:
            try:
                # Resolve project root to check local node_modules/playwright too
                _this_file = Path(__file__).resolve()
                _proj_root = _this_file.parents[2]  # src/application -> project root
                _local_pw = _proj_root / "node_modules" / "playwright"
                from src.application.skill_runner import PptxSkillRunner
                env = PptxSkillRunner._node_env()
                found = False
                for pw_src in [
                    f"require({json.dumps(str(_local_pw))})",
                    "require('playwright')",
                ]:
                    probe = subprocess.run(
                        [node, "-e", pw_src],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                        env={**os.environ, **env},
                    )
                    if probe.returncode == 0:
                        found = True
                        break
                if not found:
                    missing.append(
                        "playwright（封面渲染）— 安装："
                        "npm install && npx playwright install chromium"
                    )
            except (OSError, subprocess.SubprocessError):
                missing.append("无法探测 playwright（node 调用失败）")

        return (not missing, missing)

    def run(
        self,
        *,
        subcommand: str,
        cwd: Path | None = None,
        extra_args: Sequence[str] = (),
        content_json: dict[str, Any] | None = None,
        content_json_path: Path | None = None,
        timeout_sec: float | None = None,
    ) -> SkillRunResult:
        """Invoke ``bash make.sh <subcommand> [extra_args]``.

        Either ``content_json`` (dict) or ``content_json_path`` must be
        provided so the script can read structured input.  When ``content_json``
        is a dict, we serialise it to a temporary file under the skill root
        so the script can read a stable path.
        """
        if not self.is_available():
            return SkillRunResult(
                ok=False,
                error="外部依赖缺失：" + "; ".join(self.missing_dependencies()),
            )

        # If content_json is a dict, dump to a temp file inside the skill root.
        tmp_content_path: Path | None = None
        if content_json is not None:
            import tempfile

            tmpdir = self._skill_root / "_tmp"
            tmpdir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="content_", suffix=".json", dir=tmpdir)
            os.close(fd)
            tmp_content_path = Path(name)
            tmp_content_path.write_text(
                json.dumps(content_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        effective_content_path = content_json_path or tmp_content_path

        cmd: list[str] = ["bash", str(self._entry), subcommand]
        if effective_content_path:
            cmd.extend(["--content", str(effective_content_path)])
        cmd.extend(extra_args)

        run_cwd = cwd or self._skill_root
        result = self._run_subprocess(
            cmd,
            cwd=run_cwd,
            env={"PDF_PYTHON": self._python_for_skill()},
            timeout_sec=timeout_sec,
        )

        # Cleanup tmp file (best-effort).
        if tmp_content_path and tmp_content_path.exists():
            try:
                tmp_content_path.unlink()
            except OSError:
                pass

        return result


# ── pptx-generator runner ───────────────────────────────────────────


class PptxSkillRunner(SkillRunner):
    """Wraps Node.js + pptxgenjs (slide modules + compile.js)."""

    skill_name = "pptx-generator"

    #: BUG-011：成功编译至少 1s；<0.5s 几乎可以确定上层 wrapper 短路了
    #: pipeline 但仍返回 success。暴露为类常量供上层 wrapper 复用同一
    #: 启发式（避免在多处硬编码 0.5）。
    SUSPICIOUS_DURATION_SEC = 0.5

    def __init__(self) -> None:
        super().__init__()
        self._skill_root = get_skill_pptx_path()

    def _check_availability(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if shutil.which("node") is None:
            missing.append("node（pptx-generator 需要 Node.js 18+）")
        if shutil.which("npm") is None:
            missing.append("npm（pptxgenjs 安装需要）")
        # pptxgenjs is a HARD requirement: the generated compile.js does
        # ``require("pptxgenjs")``.  Previously this check was a no-op, so
        # the runner reported "available" and the failure surfaced as a raw
        # MODULE_NOT_FOUND at compile time — that was BUG-009.
        if shutil.which("node") is not None and not self._pptxgenjs_resolvable():
            missing.append(
                "pptxgenjs（PPTX 渲染库）— 安装：npm install -g pptxgenjs"
            )
        return (not missing, missing)

    def _pptxgenjs_resolvable(self) -> bool:
        """True when ``require("pptxgenjs")`` succeeds from the build dir.

        Checks the same resolution path compile.js will use, including the
        global npm root (which is not on Node's default search path for
        scripts run from a temp directory — hence NODE_PATH below).
        """
        node = shutil.which("node")
        if not node:
            return False
        try:
            probe = subprocess.run(
                [node, "-e", "require.resolve('pptxgenjs')"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env={**os.environ, **self._node_env()},
            )
            return probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _node_env() -> dict[str, str]:
        """NODE_PATH pointing at the global npm root.

        The generated ``compile.js`` lives in a throwaway ``_pptx_build``
        directory next to the output file, so Node's usual
        walk-up-the-tree ``node_modules`` lookup never reaches a globally
        installed pptxgenjs.  Adding the global root to NODE_PATH makes a
        ``npm install -g pptxgenjs`` install actually usable.

        Also used by :meth:`PdfSkillRunner._check_availability` so a
        globally-installed ``playwright`` is detected.
        """
        # Resolve npm via ``which``: on Windows it is ``npm.CMD``, and
        # CreateProcess does not apply PATHEXT lookup, so passing the bare
        # name "npm" raises FileNotFoundError.
        npm = shutil.which("npm")
        if not npm:
            return {}
        try:
            probe = subprocess.run(
                [npm, "root", "-g"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            global_root = probe.stdout.strip() if probe.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            global_root = ""
        if not global_root:
            return {}
        existing = os.environ.get("NODE_PATH", "")
        joined = (
            f"{global_root}{os.pathsep}{existing}" if existing else global_root
        )
        return {"NODE_PATH": joined}

    def build_slide_modules(
        self,
        *,
        out_dir: Path,
        topic: str,
        slides_content: list[dict[str, Any]],
        theme: dict[str, str] | None = None,
    ) -> tuple[Path, list[Path]]:
        """Generate ``slide-XX.js`` modules + ``compile.js`` from LLM output.

        Returns ``(compile_js_path, [slide_path, ...])``.  Caller invokes
        ``run_compile`` next.

        This function does NOT itself invoke an LLM — the slide content must
        already be shaped by the caller (the agent feeds ``slides_content``
        via the pptx-generator skill's SKILL.md).  The runner just writes
        the JS files.
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        theme = theme or {
            "primary": "22223b",
            "secondary": "4a4e69",
            "accent": "9a8c98",
            "light": "c9ada7",
            "bg": "f2e9e4",
        }

        slide_paths: list[Path] = []
        for i, slide in enumerate(slides_content, start=1):
            path = out_dir / f"slide-{i:02d}.js"
            path.write_text(_render_slide_js(slide, theme=theme, index=i), encoding="utf-8")
            slide_paths.append(path)

        compile_path = out_dir / "compile.js"
        compile_path.write_text(_render_compile_js(len(slide_paths), theme=theme), encoding="utf-8")
        return compile_path, slide_paths

    def run_compile(
        self,
        compile_js: Path,
        *,
        output_path: Path,
        timeout_sec: float | None = None,
    ) -> SkillRunResult:
        """Invoke ``node compile.js <output_path>``."""
        if not self.is_available():
            return SkillRunResult(
                ok=False,
                error="外部依赖缺失：" + "; ".join(self.missing_dependencies()),
            )

        node = shutil.which("node") or "node"
        # Use a path *relative to cwd* for compile.js: on Windows, an absolute
        # path containing spaces (e.g. Chinese knowledge-base names with
        # "面向架构师的 AI 知识体系") is mishandled by Node 24's CJS loader
        # — it concatenates ``cwd`` with the script path and emits a
        # ``MODULE_NOT_FOUND`` against a doubled path.  ``cwd`` already
        # points at ``compile_js.parent`` so a bare filename resolves
        # cleanly.  Output path stays absolute so pptxgenjs's writeFile
        # lands at the expected location.
        logger.info(
            "PptxSkillRunner.run_compile start: compile_js=%s output=%s timeout=%s",
            compile_js, output_path, timeout_sec,
        )
        result = self._run_subprocess(
            [node, compile_js.name, str(output_path.resolve())],
            cwd=compile_js.parent,
            env=self._node_env(),
            timeout_sec=timeout_sec,
        )
        result.artifact_path = output_path if output_path.exists() else None
        if result.ok and not result.artifact_path:
            result.ok = False
            result.error = f"node compile.js 退出 0 但产物不存在：{output_path}"
            logger.error(
                "PptxSkillRunner.run_compile: node 退出 0 但产物缺失 "
                "(duration=%.2fs output=%s) — BUG-011 症状",
                result.duration_sec or 0.0, output_path,
            )
        elif result.ok:
            logger.info(
                "PptxSkillRunner.run_compile ok: duration=%.2fs artifact=%s",
                result.duration_sec or 0.0, result.artifact_path,
            )
        else:
            # BUG-011 调查用：异常短耗时 + ok=False 强烈暗示上层 wrapper
            # 吞掉了真实错误并把 result 标成"成功"
            if self._is_suspicious_duration(result.duration_sec):
                logger.warning(
                    "PptxSkillRunner.run_compile SUSPICIOUSLY FAST: "
                    "duration=%.2fs ok=%s exit_code=%s error=%s "
                    "(BUG-011 信号 — 上层 wrapper 可能吞错)",
                    result.duration_sec or 0.0, result.ok,
                    result.exit_code, result.error,
                )
            logger.warning(
                "PptxSkillRunner.run_compile failed: "
                "duration=%.2fs exit_code=%s stderr_tail=%s",
                result.duration_sec or 0.0, result.exit_code,
                (result.stderr or "").strip().splitlines()[-5:],
            )
        return result

    @classmethod
    def _is_suspicious_duration(cls, duration_sec: float | None) -> bool:
        """BUG-011 启发式：duration < :attr:`SUSPICIOUS_DURATION_SEC` 即视为
        「上层 wrapper 短路了 pipeline 但返回 success」的强信号。

        暴露为类方法供上层 ``kg_make_pptx`` / ``kg_digest_notes`` 等
        wrapper 复用，避免在多处硬编码 0.5。
        """
        return (duration_sec or 0.0) < cls.SUSPICIOUS_DURATION_SEC


# ── minimax-docx runner ─────────────────────────────────────────────


class DocxSkillRunner(SkillRunner):
    """Wraps ``dotnet run --project ...`` for the OpenXML SDK CLI."""

    skill_name = "minimax-docx"

    def __init__(self) -> None:
        super().__init__()
        self._skill_root = get_skill_docx_path()
        self._cli_project = (
            self._skill_root / "scripts" / "dotnet" / "MiniMaxAIDocx.Cli"
        )

    def _check_availability(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if shutil.which("dotnet") is None:
            missing.append("dotnet（minimax-docx 需要 .NET SDK）")
        if not self._cli_project.exists():
            missing.append(f"CLI 工程不存在：{self._cli_project}")
        return (not missing, missing)

    def run_cli(
        self,
        *,
        args: Sequence[str],
        timeout_sec: float | None = None,
    ) -> SkillRunResult:
        """Invoke ``dotnet run --project ... -- <args>``.

        ``args`` is forwarded verbatim after the ``--`` separator.
        """
        if not self.is_available():
            return SkillRunResult(
                ok=False,
                error="外部依赖缺失：" + "; ".join(self.missing_dependencies()),
            )

        cmd = ["dotnet", "run", "--project", str(self._cli_project), "--", *args]
        return self._run_subprocess(cmd, cwd=self._skill_root, timeout_sec=timeout_sec)


# ── JS templates (single source of truth for slide rendering) ──────


def _render_slide_js(slide: dict[str, Any], *, theme: dict[str, str], index: int) -> str:
    """Render one slide-XX.js module (cover / toc / content / section / summary)."""
    stype = slide.get("type", "content")
    title = (slide.get("title") or "").replace('"', '\\"')

    body_lines: list[str] = [
        f'  slide.background = {{ color: theme.bg }};',
        f'  slide.addText("{title}", {{',
        f'    x: 0.5, y: 0.4, w: 9, h: 0.8,',
        f'    fontSize: 32, fontFace: "Microsoft YaHei",',
        f'    color: theme.primary, bold: true',
        f'  }});',
    ]

    if stype == "cover":
        body_lines = [
            f'  slide.background = {{ color: theme.bg }};',
            f'  slide.addText("{title}", {{',
            f'    x: 0.5, y: 2, w: 9, h: 1.4,',
            f'    fontSize: 48, fontFace: "Microsoft YaHei",',
            f'    color: theme.primary, bold: true, align: "center"',
            f'  }});',
        ]
        page_badge = ""
    elif stype == "toc":
        body_lines = [
            f'  slide.background = {{ color: theme.bg }};',
            f'  slide.addText("目录", {{',
            f'    x: 0.5, y: 0.4, w: 9, h: 0.8,',
            f'    fontSize: 32, fontFace: "Microsoft YaHei",',
            f'    color: theme.primary, bold: true',
            f'  }});',
        ]
        for i, item in enumerate(slide.get("items") or [], start=1):
            body_lines.append(
                f'  slide.addText("{i}. {(item or "").replace(chr(34), chr(92)+chr(34))}", {{\n'
                f'    x: 1.0, y: {1.3 + (i - 1) * 0.5}, w: 8.5, h: 0.5,\n'
                f'    fontSize: 20, fontFace: "Microsoft YaHei",\n'
                f'    color: theme.secondary\n'
                f'  }});'
            )
        page_badge = _PAGE_BADGE_JS
    elif stype == "content":
        bullets = slide.get("bullets") or slide.get("body") or []
        if isinstance(bullets, str):
            bullets = [bullets]
        for i, b in enumerate(bullets[:6], start=1):
            body_lines.append(
                f'  slide.addText("• {(b or "").replace(chr(34), chr(92)+chr(34))}", {{\n'
                f'    x: 0.9, y: {1.4 + (i - 1) * 0.5}, w: 8.5, h: 0.5,\n'
                f'    fontSize: 20, fontFace: "Microsoft YaHei",\n'
                f'    color: theme.secondary\n'
                f'  }});'
            )
        if slide.get("callout"):
            body_lines.append(
                f'  slide.addText("{(slide["callout"] or "").replace(chr(34), chr(92)+chr(34))}", {{\n'
                f'    x: 0.5, y: 4.7, w: 9, h: 0.5,\n'
                f'    fontSize: 14, fontFace: "Microsoft YaHei", italic: true,\n'
                f'    color: theme.accent\n'
                f'  }});'
            )
        page_badge = _PAGE_BADGE_JS
    elif stype == "summary":
        takeaways = slide.get("takeaways") or []
        for i, t in enumerate(takeaways[:5], start=1):
            body_lines.append(
                f'  slide.addText("{i}. {(t or "").replace(chr(34), chr(92)+chr(34))}", {{\n'
                f'    x: 0.9, y: {1.4 + (i - 1) * 0.6}, w: 8.5, h: 0.55,\n'
                f'    fontSize: 20, fontFace: "Microsoft YaHei",\n'
                f'    color: theme.secondary\n'
                f'  }});'
            )
        page_badge = _PAGE_BADGE_JS
    else:
        # Unknown type — graceful degradation
        page_badge = _PAGE_BADGE_JS

    body = "\n".join(body_lines)
    # Use plain str.replace — the template contains literal {x: 9.3}
    # which str.format would mistake for a placeholder.
    badge = "" if page_badge == "" else _PAGE_BADGE_JS.replace("{index}", str(index))
    return (
        f'const pptxgen = require("pptxgenjs");\n\n'
        f'function createSlide(pres, theme) {{\n'
        f'  const slide = pres.addSlide();\n{body}\n{badge}\n'
        f'  return slide;\n'
        f'}}\n\n'
        f'module.exports = {{ createSlide }};\n'
    )


_PAGE_BADGE_JS = """  slide.addShape(pres.shapes.OVAL, {
    x: 9.3, y: 5.1, w: 0.4, h: 0.4,
    fill: { color: theme.accent }
  });
  slide.addText("{index}", {
    x: 9.3, y: 5.1, w: 0.4, h: 0.4,
    fontSize: 12, fontFace: "Arial",
    color: "FFFFFF", bold: true, align: "center", valign: "middle"
  });"""


def _render_compile_js(slide_count: int, *, theme: dict[str, str]) -> str:
    return (
        f"const pptxgen = require('pptxgenjs');\n"
        f"const path = require('path');\n\n"
        f"const pres = new pptxgen();\n"
        f"pres.layout = 'LAYOUT_16x9';\n"
        f"const theme = {json.dumps(theme, ensure_ascii=False)};\n\n"
        f"for (let i = 1; i <= {slide_count}; i++) {{\n"
        f"  const num = String(i).padStart(2, '0');\n"
        f"  const mod = require(path.join(__dirname, `slide-${{num}}.js`));\n"
        f"  mod.createSlide(pres, theme);\n"
        f"}}\n\n"
        f"const outPath = process.argv[2] || 'output.pptx';\n"
        # writeFile() is async — await it so a write failure becomes a
        # non-zero exit with a real message instead of a silent exit 0
        # plus a missing artifact.
        f"pres.writeFile({{ fileName: outPath }})\n"
        f"  .then(() => {{ console.log('wrote ' + outPath); }})\n"
        f"  .catch((err) => {{\n"
        f"    console.error('pptx write failed: ' + (err && err.stack || err));\n"
        f"    process.exit(1);\n"
        f"  }});\n"
    )


__all__ = [
    "SkillRunner",
    "PdfSkillRunner",
    "PptxSkillRunner",
    "DocxSkillRunner",
    "SkillRunResult",
]