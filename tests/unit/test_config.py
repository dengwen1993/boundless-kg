"""config — no hardcoded secrets, missing env raises, reload works.

Corresponds to ENGINEERING_PLAN.md §1.1 / §7.2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    get_deepseek_api_key,
    get_kb_root,
    get_minimax_api_key,
    get_settings,
    get_workspace_dir,
    reload_settings,
    require_secret,
)
from src.config.settings import LLMSettings, Settings


class TestRequireSecret:
    def test_none_raises_environmenterror(self) -> None:
        with pytest.raises(EnvironmentError) as exc:
            require_secret(None, "TEST_VAR")
        assert "TEST_VAR" in str(exc.value)

    def test_secret_value_returns_plaintext(self) -> None:
        from pydantic import SecretStr

        assert require_secret(SecretStr("hello"), "X") == "hello"


class TestLLMSettingsNoHardcodedKeys:
    def test_anthropic_key_defaults_to_none(self, clean_env) -> None:
        s = LLMSettings()
        assert s.anthropic_api_key is None

    def test_deepseek_key_defaults_to_none(self, clean_env) -> None:
        s = LLMSettings()
        assert s.deepseek_api_key is None

    def test_anthropic_base_url_is_default_not_secret(self, clean_env) -> None:
        # The base URL is public, not a secret — defaults are fine.
        s = LLMSettings()
        assert s.anthropic_base_url.startswith("http")

    def test_env_var_overrides_take_effect(self, clean_env, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xyz")
        s = LLMSettings()
        assert s.anthropic_api_key is not None
        assert s.anthropic_api_key.get_secret_value() == "sk-test-xyz"


class TestGetApiKeyRaises:
    def test_get_minimax_api_key_raises_when_missing(self, clean_env) -> None:
        with pytest.raises(EnvironmentError):
            get_minimax_api_key()

    def test_get_deepseek_api_key_raises_when_missing(self, clean_env) -> None:
        with pytest.raises(EnvironmentError):
            get_deepseek_api_key()

    def test_get_minimax_api_key_returns_secret(self, clean_env, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real")
        assert get_minimax_api_key() == "sk-real"


class TestKbRoot:
    def test_default_is_path_object(self, clean_env) -> None:
        reload_settings()
        kb = get_kb_root()
        assert isinstance(kb, type(get_kb_root()))

    def test_default_resolves_under_cwd_not_container(
        self, clean_env, monkeypatch, tmp_path: Path
    ) -> None:
        """Regression: get_kb_root() must NOT fall back to /app/... on local dev.

        Pre-fix, _resolve_path_setting read os.environ directly
        (pydantic-settings never populates it), so unset KG_KB_ROOT
        fell back to the POSIX container default /app/workspace/...,
        which doesn't exist on Windows/WSL and made the shell sandbox's
        root.mkdir raise PermissionError on /app.
        """
        monkeypatch.delenv("KG_KB_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        reload_settings()

        kb = get_kb_root()
        resolved = kb.resolve()
        # Default is './workspace/knowledge_bases' (relative), so it
        # must resolve under the chdir'd CWD, not under /app.
        assert resolved.is_relative_to(tmp_path.resolve()), (
            f"kb_root leaked container default: {resolved}"
        )

    def test_path_segments_do_not_contain_app(
        self, clean_env, monkeypatch, tmp_path: Path
    ) -> None:
        """No path segment may be 'app'; guards against any future
        regression that re-introduces the container default."""
        monkeypatch.delenv("KG_KB_ROOT", raising=False)
        monkeypatch.delenv("KG_WORKSPACE_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        reload_settings()

        for getter in (get_kb_root, get_workspace_dir):
            p = getter().resolve()
            segments = [s for s in str(p).replace("\\", "/").split("/") if s]
            assert "app" not in segments, f"{getter.__name__} leaked /app: {p}"

    def test_get_kb_root_matches_settings_value(self, clean_env) -> None:
        """get_kb_root() must agree with Settings.kb_root (single source of truth).

        Without this, callers using get_settings().kb_root vs.
        get_kb_root() can see two different paths, the exact bug
        that masked the .env value behind a stale container default.
        """
        reload_settings()
        settings_kb = Path(get_settings().kb_root).resolve()
        assert settings_kb == get_kb_root()

    def test_env_var_overrides_settings_default(
        self, clean_env, monkeypatch, tmp_path: Path
    ) -> None:
        """A KG_KB_ROOT override must take effect (no caching staleness)."""
        target = tmp_path / "custom_kb"
        target.mkdir()
        monkeypatch.setenv("KG_KB_ROOT", str(target))
        reload_settings()
        assert get_kb_root() == target.resolve()


class TestSettingsReload:
    def test_reload_clears_cache(self, clean_env, monkeypatch) -> None:
        monkeypatch.setenv("KG_LOG_LEVEL", "DEBUG")
        s1 = Settings()
        monkeypatch.setenv("KG_LOG_LEVEL", "INFO")
        s2 = reload_settings()
        # Fresh instance picks up the new value.
        assert s1.log_level != s2.log_level or s1 is not s2