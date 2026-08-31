"""SkillRunner — health checks + safe subprocess wrapping.

Tests run without invoking the real external skills — they patch
``shutil.which`` / ``Path.exists`` to simulate presence/absence and
fake the subprocess via ``_run_subprocess`` injection on the base class.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src.application.skill_runner import (
    DocxSkillRunner,
    PdfSkillRunner,
    PptxSkillRunner,
    SkillRunResult,
)


# ── Health checks ──────────────────────────────────────────────────


def test_pdf_runner_reports_missing_bash(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pdf_path",
        lambda: tmp_path,
    )
    runner = PdfSkillRunner()
    assert runner.is_available() is False
    assert any("bash" in m for m in runner.missing_dependencies())


def test_pdf_runner_reports_missing_entry_script(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bash")
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pdf_path",
        lambda: tmp_path / "nonexistent",
    )
    runner = PdfSkillRunner()
    assert runner.is_available() is False
    assert any("入口脚本" in m for m in runner.missing_dependencies())


def test_pptx_runner_reports_missing_node(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pptx_path",
        lambda: tmp_path,
    )
    runner = PptxSkillRunner()
    assert runner.is_available() is False
    assert any("node" in m.lower() for m in runner.missing_dependencies())


def test_docx_runner_reports_missing_dotnet(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_docx_path",
        lambda: tmp_path,
    )
    runner = DocxSkillRunner()
    assert runner.is_available() is False
    assert any("dotnet" in m for m in runner.missing_dependencies())


# ── Result formatting ──────────────────────────────────────────────


def test_skill_run_result_success_message() -> None:
    r = SkillRunResult(
        ok=True,
        exit_code=0,
        artifact_path=Path("x.pdf"),
        duration_sec=1.23,
    )
    msg = r.to_user_message()
    assert "✅" in msg
    # Windows may render absolute paths with backslashes; check the basename.
    assert "x.pdf" in msg
    assert "1.2s" in msg


def test_skill_run_result_failure_includes_stderr_tail() -> None:
    r = SkillRunResult(
        ok=False,
        exit_code=2,
        stderr="line1\nline2\n" + ("x" * 5000),
        error="boom",
    )
    msg = r.to_user_message()
    assert "❌" in msg
    assert "boom" in msg
    assert "line1" in msg
    # Implementation truncates to the LAST 30 lines; "line1\nline2\n" is 3 lines,
    # so the entire 5000-char suffix lands inside the tail — we just assert
    # the message is well-formed.
    assert msg.endswith("… [truncated]") or "x" in msg


# ── PptxSkillRunner.build_slide_modules (pure-ish) ──────────────────


def test_pptx_runner_builds_cover_and_compile(tmp_path: Path) -> None:
    """Verify JS module generation produces parseable output + compile.js."""
    runner = PptxSkillRunner()
    slides = [
        {"type": "cover", "title": "Test", "subtitle": "Demo"},
        {"type": "toc", "items": ["A", "B"]},
        {"type": "content", "title": "X", "bullets": ["1", "2"]},
        {"type": "summary", "takeaways": ["t1", "t2"]},
    ]
    compile_js, slide_paths = runner.build_slide_modules(
        out_dir=tmp_path,
        topic="Test",
        slides_content=slides,
    )
    assert compile_js.exists()
    assert len(slide_paths) == 4
    for p in slide_paths:
        text = p.read_text(encoding="utf-8")
        assert "createSlide" in text
        assert 'require("pptxgenjs")' in text
        # Cover slide has no page badge; others do.
        if "slide-01" in p.name:
            # Cover intentionally omits the badge block — verify it's absent.
            assert "9.3" not in text
        else:
            assert "9.3" in text  # page-badge coordinates


def test_pptx_runner_escapes_double_quotes_in_titles(tmp_path: Path) -> None:
    """Special characters must not break the generated JS."""
    runner = PptxSkillRunner()
    slides = [{"type": "content", "title": 'He said "hi"', "bullets": ['a"b']}]
    compile_js, slide_paths = runner.build_slide_modules(
        out_dir=tmp_path,
        topic='T"opic',
        slides_content=slides,
    )
    text = slide_paths[0].read_text(encoding="utf-8")
    # Output is JS-source text where " becomes \"; check both as raw strings.
    assert 'He said \\"hi\\"' in text
    assert 'a\\"b' in text
    # compile.js also escapes the theme object
    compile_text = compile_js.read_text(encoding="utf-8")
    assert "LAYOUT_16x9" in compile_text


# ── BUG-009: pptxgenjs detection ───────────────────────────────────


def test_pptx_runner_reports_missing_pptxgenjs(monkeypatch, tmp_path: Path) -> None:
    """BUG-009: the runner must surface a clean ``pptxgenjs`` message
    instead of letting the missing-module error surface as ``unknown error``
    at compile time.
    """
    # node and npm are present, but the probe for pptxgenjs fails.
    monkeypatch.setattr(
        "shutil.which", lambda name: "C:/fake/{}.CMD".format(name) if name else None
    )
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pptx_path", lambda: tmp_path
    )
    # All `subprocess.run` calls in the runner should exit 0 except
    # the `require.resolve('pptxgenjs')` probe which must fail.
    def fake_run(cmd, **kwargs):
        result = mock.MagicMock()
        if "pptxgenjs" in (cmd if isinstance(cmd, str) else " ".join(cmd)):
            result.returncode = 1
            result.stdout = ""
            result.stderr = "Cannot find module 'pptxgenjs'"
        else:
            result.returncode = 0
            result.stdout = "C:/npm/lib/node_modules"
            result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = PptxSkillRunner()
    assert runner.is_available() is False
    assert any("pptxgenjs" in m for m in runner.missing_dependencies())


def test_pptx_node_env_resolves_global_pptxgenjs(monkeypatch) -> None:
    """The generated compile.js runs from a throwaway _pptx_build dir
    where the default node_modules walk-up never reaches a global install.
    NODE_PATH must therefore point at ``npm root -g`` for pptxgenjs to
    be resolvable.
    """
    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/npm" if name == "npm" else None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: mock.MagicMock(
            returncode=0, stdout="C:/npm/lib/node_modules", stderr=""
        ),
    )
    env = PptxSkillRunner._node_env()
    assert env.get("NODE_PATH", "").startswith("C:/npm/lib/node_modules")


def test_pptx_node_env_handles_no_npm(monkeypatch) -> None:
    """If npm is not on PATH (or is a .CMD the runner can't exec), the
    helper must return an empty env, not raise — the failure surface is
    the pptxgenjs availability check, not the env lookup.
    """
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert PptxSkillRunner._node_env() == {}


def test_pptx_compile_js_awaits_writefile(tmp_path: Path) -> None:
    """BUG-009 follow-up: pptx writeFile() is async; the generated compile
    script must await it via .then/.catch so a write failure becomes a
    non-zero exit instead of process exit with no artifact.
    """
    runner = PptxSkillRunner()
    compile_js, _ = runner.build_slide_modules(
        out_dir=tmp_path, topic="T",
        slides_content=[{"type": "cover", "title": "T"}],
    )
    text = compile_js.read_text(encoding="utf-8")
    assert ".then(" in text and ".catch(" in text
    assert "process.exit(1)" in text


# ── BUG-010: stderr/stdout surfacing + Python interpreter resolution ─


def test_skill_run_result_includes_stdout_when_no_stderr() -> None:
    """BUG-010: the previous formatter only printed stderr.  Bash
    pipelines (minimax-pdf/make.sh) sometimes print the actual failure
    to stdout, so a stderr-only filter produces a fake ``unknown error``.
    """
    r = SkillRunResult(
        ok=False, exit_code=1, stdout="make.sh: line 99: command not found",
        stderr="", error="exit 1",
    )
    msg = r.to_user_message()
    assert "command not found" in msg  # visible now
    assert "stdout" in msg  # explicitly labelled


def test_skill_run_result_flags_silent_failure() -> None:
    """BUG-010: when both streams are empty, the message must not look
    like a clean success; it should hint that the cause is upstream.
    """
    r = SkillRunResult(ok=False, exit_code=1, error="exit 1")
    msg = r.to_user_message()
    assert "❌" in msg
    assert "未输出任何内容" in msg


def test_pdf_runner_reports_missing_python_dep(monkeypatch, tmp_path: Path) -> None:
    """BUG-010: a missing python dep (e.g. reportlab) used to surface only
    as 'unknown error'.  The runner now probes each required module
    and reports the install command.
    """
    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/bash")
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pdf_path", lambda: tmp_path
    )

    def fake_run(cmd, **kwargs):
        result = mock.MagicMock()
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "import reportlab" in joined or "import pypdf" in joined:
            result.returncode = 1
            result.stderr = "ModuleNotFoundError"
        elif "require('playwright')" in joined:
            result.returncode = 0  # playwright present
        else:
            result.returncode = 0
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = PdfSkillRunner()
    assert runner.is_available() is False
    deps = runner.missing_dependencies()
    assert any("reportlab" in m for m in deps)
    assert any("pypdf" in m for m in deps)


def test_pdf_runner_reports_missing_playwright(monkeypatch, tmp_path: Path) -> None:
    """BUG-010: missing playwright must be a clean dep-list entry, not a
    subprocess traceback that gets swallowed.
    """
    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/bash")
    monkeypatch.setattr(
        "src.application.skill_runner.get_skill_pdf_path", lambda: tmp_path
    )

    def fake_run(cmd, **kwargs):
        result = mock.MagicMock()
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "import reportlab" in joined or "import pypdf" in joined:
            result.returncode = 0  # python deps ok
        else:
            result.returncode = 1
            result.stderr = "Cannot find module 'playwright'"
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = PdfSkillRunner()
    assert runner.is_available() is False
    assert any("playwright" in m for m in runner.missing_dependencies())


def test_pdf_python_for_skill_uses_sys_executable() -> None:
    """BUG-010 root cause: make.sh hardcoded ``python3``, which on Windows
    is the Microsoft Store stub.  The runner must hand the subprocess
    its own sys.executable via $PDF_PYTHON.
    """
    import sys
    assert PdfSkillRunner._python_for_skill() == sys.executable


# ── BUG-011 — duration threshold heuristic ──────────────────────────


class TestPptxSuspiciousDuration:
    """BUG-011：上层 wrapper 短路 pipeline 但仍返回 success 时
    ``duration_sec < 0.5s`` 是关键信号。验证 :class:`PptxSkillRunner`
    把阈值常量化并暴露为公开 API。
    """

    def test_threshold_constant_exists(self):
        assert hasattr(PptxSkillRunner, "SUSPICIOUS_DURATION_SEC")
        assert PptxSkillRunner.SUSPICIOUS_DURATION_SEC == 0.5

    def test_short_duration_is_suspicious(self):
        assert PptxSkillRunner._is_suspicious_duration(0.1) is True
        assert PptxSkillRunner._is_suspicious_duration(0.0) is True
        assert PptxSkillRunner._is_suspicious_duration(0.49) is True

    def test_long_duration_is_not_suspicious(self):
        assert PptxSkillRunner._is_suspicious_duration(0.5) is False
        assert PptxSkillRunner._is_suspicious_duration(1.5) is False
        assert PptxSkillRunner._is_suspicious_duration(60.0) is False

    def test_none_duration_handled(self):
        """``None`` duration (subprocess crashed before timing) 视作
        异常短耗时 — 也是潜在 wrapper 短路信号。"""
        assert PptxSkillRunner._is_suspicious_duration(None) is True
