"""Configuration — refactored to load all secrets from environment.

ENGINEERING_PLAN.md §1.1 / §7.2. Three rules:

  1. No hardcoded secrets in source — ever.
  2. Missing required secrets raise ``EnvironmentError`` with a clear
     pointer to which env var is missing.
  3. ``Settings`` is the single source of truth; module-level helpers
     (``get_minimax_api_key()`` etc.) are thin accessors.

Public surface (stable):

  * ``Settings`` / ``LLMSettings`` / ``SearchSettings`` /
    ``APISettings`` / ``AgentSettings``
  * ``get_settings()`` (cached; ``reload_settings()`` clears cache)
  * ``get_minimax_api_key()`` / ``get_minimax_base_url()`` /
    ``get_minimax_model()``
  * ``get_deepseek_api_key()`` / ``get_deepseek_base_url()`` /
    ``get_deepseek_chat_model()`` / ``get_deepseek_v4_model()``
  * ``get_kb_root()``
  * ``get_api_cors_origins()``
  * ``get_agent_recursion_limit()``
  * ``require_secret(value, var_name)`` — raise-or-return helper.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _looks_absolute(raw: str | Path) -> bool:
    """Return ``True`` when *raw* is already anchored (no CWD joining).

    Cross-platform correctness matters here because the documented
    defaults for ``KG_KB_ROOT`` and ``KG_WORKSPACE_DIR`` are POSIX-style
    (e.g. ``/app/workspace``) — what the container image expects. On
    Windows ``Path.is_absolute()`` reports ``False`` for those, which
    would silently let downstream code resolve them against the
    developer's CWD. We therefore accept either:

      * a leading POSIX slash (``/app/workspace``); or
      * a Windows drive letter (``C:\\…``, ``D:/…``).

    as 「already absolute」 and pass the value through unchanged.
    """
    s = str(raw)
    if s.startswith("/") or s.startswith("\\"):
        return True
    try:
        win = PureWindowsPath(s)
    except Exception:
        return False
    return bool(win.drive)


def _resolve_host_path(raw: str | Path) -> Path:
    """Normalise a config-supplied path string to a ``Path`` instance.

    The result is just ``Path(raw)`` — what matters for callers is
    whether to CWD-join or not, which :func:`_looks_absolute` answers.
    This helper exists so the call sites stay uniform: every config
    accessor does ``return _resolve_host_path(get_settings().xxx)``
    and downstream code only has to consult ``_looks_absolute`` to
    decide on CWD joining.
    """
    return Path(raw) if not isinstance(raw, Path) else raw


_BASE_ENV_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)


class LLMSettings(BaseSettings):
    """Per-provider LLM settings. All keys optional at type level;
    the ``get_*_api_key()`` helpers raise when actually missing."""

    model_config = _BASE_ENV_CONFIG

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default="https://api.minimaxi.com/anthropic", alias="ANTHROPIC_BASE_URL"
    )
    anthropic_model: str = Field(default="MiniMax-M3-512k", alias="ANTHROPIC_MODEL")

    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_chat_model: str = Field(
        default="deepseek-chat", alias="DEEPSEEK_MODEL_CHAT"
    )
    deepseek_v4_model: str = Field(
        default="deepseek-v4-pro", alias="DEEPSEEK_MODEL_V4"
    )


class SearchSettings(BaseSettings):
    """mmx-cli / DuckDuckGo / Bocha AI search backend configuration."""

    model_config = _BASE_ENV_CONFIG

    mmx_api_key: SecretStr | None = Field(default=None, alias="MMX_API_KEY")
    mmx_region: str = Field(default="cn", alias="MMX_REGION")
    mmx_proxy: str = Field(default="", alias="MMX_PROXY")
    # Explicit proxy for DuckDuckGo (e.g. ``http://127.0.0.1:7897``).
    # When empty, the system proxy is auto-detected at startup via
    # ``detect_proxy()`` — env vars, Windows registry, or port probe.
    ddg_proxy: str = Field(default="", alias="DDG_PROXY")
    # Bocha AI Search (博查搜索) — see src/infrastructure/search/bocha.py.
    # Empty ``bocha_api_key`` keeps Bocha out of the fallback chain
    # silently (no 401 noise in logs).
    bocha_api_key: SecretStr | None = Field(default=None, alias="BOCHA_API_KEY")
    bocha_endpoint: str = Field(
        default="https://api.bochaai.com/v1/web-search", alias="BOCHA_ENDPOINT"
    )
    bocha_proxy: str = Field(default="", alias="BOCHA_PROXY")
    bocha_timeout_sec: float = Field(default=15.0, alias="BOCHA_TIMEOUT_SEC")
    bocha_count: int = Field(default=10, alias="BOCHA_COUNT")
    max_concurrency: int = Field(default=5, alias="KG_SEARCH_MAX_CONCURRENCY")
    rate_limit_ms: int = Field(default=300, alias="KG_SEARCH_RATE_LIMIT_MS")
    # Adaptive backend ordering (see src/infrastructure/search/preference.py).
    # ``True`` → DualSearchClient promotes whichever backend last returned
    # results, and quarantines backends that raise network / auth errors
    # so the next query skips them. ``False`` → fall back to the legacy
    # fixed chain (DDG → mmx → Bocha).
    adaptive: bool = Field(default=True, alias="KG_SEARCH_ADAPTIVE")
    # How long to quarantine a failed backend before probing it again
    # (seconds). Default 6 h. Each failure also appends to the on-disk
    # history so ops can grep
    # ``<KG_AGENT_MEMORY_DIR>/search_preference.json``.
    quarantine_sec: int = Field(default=6 * 3600, alias="KG_SEARCH_QUARANTINE_SEC")


class APISettings(BaseSettings):
    """FastAPI server configuration."""

    model_config = _BASE_ENV_CONFIG

    host: str = Field(default="0.0.0.0", alias="KG_API_HOST")
    port: int = Field(default=8888, alias="KG_API_PORT")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5175"],
        alias="KG_API_CORS_ORIGINS",
    )
    debug: bool = Field(default=False, alias="KG_API_DEBUG")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class AgentSettings(BaseSettings):
    """deepagents orchestration knobs."""

    model_config = _BASE_ENV_CONFIG

    recursion_limit: int = Field(default=250, alias="KG_AGENT_RECURSION_LIMIT")
    thread_id: str = Field(default="default", alias="KG_AGENT_THREAD_ID")
    interrupt_on_delete: bool = Field(default=True, alias="KG_AGENT_INTERRUPT_ON_DELETE")
    # Agent memory subsystem directory (relative to ``KG_WORKSPACE_DIR``
    # or absolute). Stores AGENTS.md + ``conversations/`` session logs +
    # ``tmp/`` transient files. Resolved against the workspace — not
    # against the curated ``kb_root`` — so agent scratch data never
    # pollutes the per-domain namespace.
    memory_dir: str = Field(default=".agent_memory", alias="KG_AGENT_MEMORY_DIR")
    # Intent-driven prompt-card injection. When ``True``, the cards middleware
    # filters ``src/agent/cards/data/*.md`` against the current turn's user
    # message and tool stack, then appends matching cards to the SystemMessage.
    # Disabled by default (Phase-1 rollout) — flip via ``KG_AGENT_CARDS_ENABLED``.
    cards_enabled: bool = Field(default=False, alias="KG_AGENT_CARDS_ENABLED")
    # Override the bundled card library location. Leave empty to use
    # ``src/agent/cards/data/`` (the package default). Relative paths resolve
    # against ``KG_KB_ROOT``; absolute paths are taken as-is.
    cards_dir: str = Field(default="", alias="KG_AGENT_CARDS_DIR")
    # When true, the agent route logs the per-turn active-cards list (no
    # prompt injection impact — purely a debug aid for Phase 2 evaluation).
    cards_debug: bool = Field(default=False, alias="KG_AGENT_CARDS_DEBUG")
    # When True, the agent exposes ``kg_shell_exec`` — a sandboxed shell
    # command tool backed by ``deepagents.backends.LocalShellBackend``
    # rooted at ``KG_KB_ROOT``.  Set False for audits / demos / prod
    # deployments where arbitrary exec isn't acceptable.  Default True
    # because the KG-curation CLI is a single-user local tool today.
    shell_enabled: bool = Field(default=True, alias="KG_AGENT_SHELL_ENABLED")
    # Default per-command timeout (seconds).  Individual calls can
    # override via the ``timeout`` argument on ``kg_shell_exec``.
    shell_timeout_sec: int = Field(default=300, alias="KG_AGENT_SHELL_TIMEOUT_SEC")


class FalkorDBSettings(BaseSettings):
    """FalkorDB graph database connection settings."""

    model_config = _BASE_ENV_CONFIG

    host: str = Field(default="localhost", alias="KG_FALKORDB_HOST")
    port: int = Field(default=6379, alias="KG_FALKORDB_PORT")
    password: SecretStr | None = Field(default=None, alias="KG_FALKORDB_PASSWORD")
    graph_prefix: str = Field(default="kg_", alias="KG_FALKORDB_GRAPH_PREFIX")
    # When False, graph store operations are skipped (graceful degradation).
    enabled: bool = Field(default=True, alias="KG_FALKORDB_ENABLED")
    # Where the associations-API endpoints read from.
    # ``auto``      → FalkorDB when reachable, otherwise ``associations.json``
    # ``falkordb``  → force FalkorDB (returns empty shell on failure)
    # ``json``      → always read from the on-disk ``associations.json``
    source: str = Field(default="auto", alias="KG_ASSOCIATIONS_SOURCE")

    @field_validator("source", mode="before")
    @classmethod
    def _validate_source(cls, v: Any) -> str:
        if v is None:
            return "auto"
        s = str(v).strip().lower()
        if s not in {"auto", "falkordb", "json"}:
            return "auto"
        return s


class EmbeddingSettings(BaseSettings):
    """Embedding API client settings."""

    model_config = _BASE_ENV_CONFIG

    provider: str = Field(default="api", alias="KG_EMBEDDING_PROVIDER")
    api_key: SecretStr | None = Field(default=None, alias="KG_EMBEDDING_API_KEY")
    base_url: str = Field(
        default="https://api.deepseek.com", alias="KG_EMBEDDING_BASE_URL"
    )
    model: str = Field(default="text-embedding-v1", alias="KG_EMBEDDING_MODEL")
    dim: int = Field(default=1024, alias="KG_EMBEDDING_DIM")
    batch_size: int = Field(default=32, alias="KG_EMBEDDING_BATCH_SIZE")
    # Hybrid search weights
    bm25_weight: float = Field(default=0.4, alias="KG_SEARCH_BM25_WEIGHT")
    vector_weight: float = Field(default=0.6, alias="KG_SEARCH_VECTOR_WEIGHT")
    default_top_k: int = Field(default=10, alias="KG_SEARCH_DEFAULT_TOP_K")


class Settings(BaseSettings):
    """Top-level settings — composes per-section settings."""

    model_config = _BASE_ENV_CONFIG

    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    api: APISettings = Field(default_factory=APISettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    falkordb: FalkorDBSettings = Field(default_factory=FalkorDBSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)

    llm_provider: str = Field(default="mock", alias="KG_LLM_PROVIDER")
    # Provider for background business flows (graph generation pipeline,
    # note generation, resource classifier, etc.). Falls back to
    # ``KG_LLM_PROVIDER`` when unset so existing setups keep working.
    # Set this to ``deepseek`` while leaving ``KG_LLM_PROVIDER=minimax``
    # to keep the agent chat on MiniMax but switch graph generation
    # over to deepseek-v4-pro (see src/infrastructure/llm/factory.py).
    llm_provider_generation: str = Field(
        default="", alias="KG_GENERATION_LLM_PROVIDER"
    )
    # Provider used ONLY by note generation. Falls back to
    # ``KG_GENERATION_LLM_PROVIDER`` → ``KG_LLM_PROVIDER``. Use this to
    # pin notes to a faster model (e.g. ``deepseek-chat`` → flash)
    # without changing the graph-generation provider.
    llm_provider_note: str = Field(default="", alias="KG_NOTE_LLM_PROVIDER")
    # Per-request HTTP timeout in seconds for LLM calls (graph / note /
    # resource generation). The default 300 s is generous; reasoning
    # models with very long chains may need more.
    llm_timeout_sec: float = Field(default=300.0, alias="KG_LLM_TIMEOUT_SEC")
    # Reasoning effort level for reasoning-capable providers
    # (deepseek-v4-*). Maps to the API's ``reasoning_effort`` param.
    # Accepts: ``low`` | ``medium`` | ``high`` | ``max`` | ``xhigh``.
    # Empty (default) = provider default, i.e. the param is NOT sent.
    # Note: cannot fully disable reasoning on reasoning-mandatory models
    # (e.g. deepseek-v4-flash) — the architecture itself forces a
    # reasoning trace; this knob only adjusts its depth.
    reasoning_effort: str = Field(default="", alias="KG_LLM_REASONING_EFFORT")
    kb_root: Path = Field(
        default=Path("./workspace/knowledge_bases"), alias="KG_KB_ROOT"
    )
    # Agent workspace directory — sits OUTSIDE ``kb_root`` so that
    # operational artefacts (pipeline state, file staging, the agent
    # memory tree, etc.) don't pollute the knowledge-base namespace.
    # The new directory layout is::
    #
    #     <workspace_dir>/
    #     ├── AGENTS.md                # persistent memory map
    #     ├── bugs.md / lessons.md / … # curated memory
    #     ├── .agent_memory/           # agent runtime data
    #     │   ├── conversations/       # permanent session logs
    #     │   └── tmp/                 # transient files (auto-cleaned)
    #     ├── _pipeline/               # pipeline state (state.json)
    #     ├── _staging/                # file upload staging area
    #     └── knowledge_bases/         # only domain data lives here
    #         └── <domain>/…
    #
    # Relative paths resolve against the current working directory;
    # absolute paths are taken as-is.
    workspace_dir: Path = Field(default=Path("./workspace"), alias="KG_WORKSPACE_DIR")
    log_level: str = Field(default="INFO", alias="KG_LOG_LEVEL")
    log_format: str = Field(default="json", alias="KG_LOG_FORMAT")

    # External skill configuration. Each external skill is a CLI tool
    # shipped under ``example/<skill>/`` — SkillRunner wraps them as
    # subprocess calls. Path defaults point to the bundled skills so the
    # project works out of the box on a fresh checkout.
    #
    # ``enabled`` lets ops disable a skill (e.g. license / availability
    # concerns) without removing code paths. ``path`` overrides the
    # default location — useful for relocated deployments.
    skill_minimax_pdf_enabled: bool = Field(default=True, alias="KG_SKILL_PDF_ENABLED")
    skill_minimax_pdf_path: str = Field(default="", alias="KG_SKILL_PDF_PATH")
    skill_pptx_generator_enabled: bool = Field(default=True, alias="KG_SKILL_PPTX_ENABLED")
    skill_pptx_generator_path: str = Field(default="", alias="KG_SKILL_PPTX_PATH")
    skill_minimax_docx_enabled: bool = Field(default=True, alias="KG_SKILL_DOCX_ENABLED")
    skill_minimax_docx_path: str = Field(default="", alias="KG_SKILL_DOCX_PATH")
    # Per-skill subprocess timeout (seconds). CREATE routes that involve
    # Chromium (playwright) can be slow on first run.
    skill_subprocess_timeout_sec: float = Field(default=600.0, alias="KG_SKILL_TIMEOUT_SEC")
    # Path to the bundled skills directory (skills/*.md each in its own
    # sub-folder).  Defaults to ``<project>/src/skills`` so a fresh
    # checkout works out of the box; ops may relocate by setting
    # ``KG_SKILLS_DIR`` to an absolute path or a path relative to
    # ``KG_KB_ROOT``.
    skills_dir: str = Field(default="", alias="KG_SKILLS_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Tests that mutate env vars should call :func:`reload_settings` first
    so the new values take effect.
    """
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached Settings instance and return a fresh one."""
    get_settings.cache_clear()
    return get_settings()


def require_secret(value: SecretStr | None, var_name: str) -> str:
    """Reject a missing secret with a clear, actionable error.

    Used by every ``get_*_api_key()`` helper so the contract is uniform:
    callers either get the real secret or a loud EnvironmentError. The
    P0 silent-fallback smell is impossible by construction.
    """
    if value is None:
        raise EnvironmentError(
            f"Missing required environment variable {var_name!r}. "
            "Set it in .env or pass it via the process environment. "
            "(ENGINEERING_PLAN.md §1.1: secrets must never be hardcoded in source.)"
        )
    return value.get_secret_value()


def _fresh_llm() -> LLMSettings:
    """Build a fresh ``LLMSettings`` so env mutations take effect immediately."""
    try:
        return LLMSettings()
    except Exception:
        return get_settings().llm


def get_minimax_api_key() -> str:
    return require_secret(_fresh_llm().anthropic_api_key, "ANTHROPIC_API_KEY")


def get_minimax_base_url() -> str:
    return _fresh_llm().anthropic_base_url


def get_minimax_model() -> str:
    return _fresh_llm().anthropic_model


def get_deepseek_api_key() -> str:
    return require_secret(_fresh_llm().deepseek_api_key, "DEEPSEEK_API_KEY")


def get_deepseek_base_url() -> str:
    return _fresh_llm().deepseek_base_url


def get_deepseek_chat_model() -> str:
    return _fresh_llm().deepseek_chat_model


def get_deepseek_v4_model() -> str:
    return _fresh_llm().deepseek_v4_model


def _read_env_str(env_var: str, default: str) -> str:
    """Read *env_var* as a raw string, bypassing pydantic's ``Path`` coercion.

    ``pydantic`` happily parses a ``KG_KB_ROOT`` value like
    ``/app/workspace`` into a ``Path`` object — but on Windows that
    ``Path`` silently drops the leading slash (becomes
    ``\\app\\workspace``), which is exactly the information we need to
    detect "this is already absolute, do not join with CWD". So we
    re-read the environment variable as a plain string and only feed it
    to :class:`Path` once we know which branch to take.

    Falls back to the literal *default* when *env_var* is unset — this
    matches the pydantic ``Field(default=…)`` contract that the field
    definitions below declare.
    """
    import os

    return os.environ.get(env_var, default)


_SETTING_ATTR_BY_ENV: dict[str, str] = {
    "KG_KB_ROOT": "kb_root",
    "KG_WORKSPACE_DIR": "workspace_dir",
}


def _resolve_path_setting(env_var: str, default: str) -> Path:
    """Resolve a POSIX-style-or-relative path setting.

    Behaviour matrix:

    * ``/app/workspace`` (POSIX absolute) → returned unchanged — even
      when running on Windows, where ``Path.is_absolute()`` would lie;
    * ``./workspace`` (relative) → resolved against CWD;
    * ``C:\\workspace`` (Windows absolute) → returned unchanged.

    Source of truth is :func:`get_settings` — pydantic-settings has
    already parsed ``.env`` into the Settings object. Falling back to
    ``os.environ`` covers shell-exported overrides (e.g. CI runners
    that pass via env vars without a ``.env`` file). The literal
    *default* is the last resort when neither source provides a value.
    """
    # 1) Prefer the parsed Settings value (single source of truth).
    #    pydantic-settings reads .env into Settings but does NOT export
    #    to os.environ, so reading os.environ directly would silently
    #    fall back to the POSIX container default on local dev.
    attr = _SETTING_ATTR_BY_ENV.get(env_var)
    if attr:
        try:
            raw = str(getattr(get_settings(), attr)).strip()
            if raw:
                if _looks_absolute(raw):
                    return Path(raw.replace("\\", "/"))
                return (Path.cwd() / raw).resolve()
        except Exception:
            pass

    # 2) Fall back to process env (shell-exported overrides / no .env).
    s = _read_env_str(env_var, default).strip()
    if _looks_absolute(s):
        # Round-trip through ``Path`` to normalise separators; the
        # leading slash is preserved because we never go through
        # ``Path.is_absolute()`` at the host level.
        return Path(s.replace("\\", "/"))
    return (Path.cwd() / s).resolve()


def get_kb_root() -> Path:
    """Return the knowledge-base root, normalised for the host platform.

    POSIX-style absolute paths (``/data/knowledge_bases`` — the
    container default) and Windows-absolute paths (``C:\\data\\…``) are
    returned unchanged; relative paths are resolved against the current
    working directory. The cross-platform check goes through
    :func:`_looks_absolute` instead of ``Path.is_absolute()`` because
    the latter lies on Windows about POSIX-style leading slashes.
    """
    return _resolve_path_setting("KG_KB_ROOT", "./workspace/knowledge_bases")


def get_workspace_dir() -> Path:
    """Return the agent workspace directory (absolute path).

    Reads ``KG_WORKSPACE_DIR`` (default ``./workspace``). Relative paths
    resolve against the current working directory; absolute paths are
    taken as-is. The returned path is **not** created on read — callers
    that need the directory on disk must ``mkdir(parents=True,
    exist_ok=True)`` themselves (this matches the behaviour of
    :func:`get_kb_root`).

    See :class:`Settings` for the canonical layout contract.
    """
    return _resolve_path_setting("KG_WORKSPACE_DIR", "./workspace")


def get_skills_dir() -> Path:
    """Return the bundled skills directory used by SkillsMiddleware.

    Resolution order:
      1. ``KG_SKILLS_DIR`` (absolute path → use as-is)
      2. ``KG_SKILLS_DIR`` (relative → resolve against ``KG_KB_ROOT``)
      3. Default ``<project>/src/skills`` (the directory shipped with the repo)

    The path is returned resolved but not validated — SkillsMiddleware
    silently skips missing sources, so a stale override just disables skills.
    """
    raw = get_settings().skills_dir.strip()
    if raw:
        if _looks_absolute(raw):
            return Path(raw.replace("\\", "/")).resolve()
        return (get_kb_root() / raw).resolve()
    # Default: <repo>/src/skills (this file lives at src/config/settings.py)
    return (Path(__file__).parent.parent / "skills").resolve()


def get_skill_timeout_sec() -> float:
    return float(get_settings().skill_subprocess_timeout_sec)


def get_skill_pdf_path() -> Path:
    """Override path for the bundled ``minimax-pdf`` skill (CLI scripts)."""
    raw = get_settings().skill_minimax_pdf_path.strip()
    if raw:
        return Path(raw).resolve()
    return get_skills_dir() / "minimax-pdf"


def get_skill_pptx_path() -> Path:
    raw = get_settings().skill_pptx_generator_path.strip()
    if raw:
        return Path(raw).resolve()
    return get_skills_dir() / "pptx-generator"


def get_skill_docx_path() -> Path:
    raw = get_settings().skill_minimax_docx_path.strip()
    if raw:
        return Path(raw).resolve()
    return get_skills_dir() / "minimax-docx"


def get_llm_provider() -> str:
    """Provider for the agent chat layer (drives ``_build_model``).

    Reads ``KG_LLM_PROVIDER`` (default ``"mock"``).
    """
    return get_settings().llm_provider


def get_generation_llm_provider() -> str:
    """Provider for business flows (graph generation, notes, resources).

    Reads ``KG_GENERATION_LLM_PROVIDER``; falls back to
    ``KG_LLM_PROVIDER`` so existing setups keep working out of the box.
    Set them to different values to keep agent chat on one model and
    background generation on another.
    """
    s = get_settings()
    return (s.llm_provider_generation or s.llm_provider or "mock").lower()


def get_note_llm_provider() -> str:
    """Provider for note generation specifically.

    Reads ``KG_NOTE_LLM_PROVIDER``; falls back through
    ``KG_GENERATION_LLM_PROVIDER`` → ``KG_LLM_PROVIDER``. Useful when
    notes want a faster/cheaper model than graph generation.
    """
    s = get_settings()
    return (
        s.llm_provider_note
        or s.llm_provider_generation
        or s.llm_provider
        or "mock"
    ).lower()


def get_llm_timeout_sec() -> float:
    """HTTP timeout in seconds for LLM calls. Defaults to 300 s."""
    return float(get_settings().llm_timeout_sec or 300.0)


def get_bocha_api_key() -> str | None:
    """Return the Bocha API key, or ``None`` when unset.

    Unlike :func:`get_minimax_api_key`, Bocha is optional — when no key
    is configured the search backend is skipped silently and the rest
    of the chain (DuckDuckGo / mmx) keeps working. Returning ``None``
    keeps the call site self-documenting.
    """
    raw = _fresh_search().bocha_api_key
    return raw.get_secret_value() if raw is not None else None


def get_bocha_endpoint() -> str:
    return _fresh_search().bocha_endpoint


def get_bocha_proxy() -> str:
    return _fresh_search().bocha_proxy


def get_bocha_timeout_sec() -> float:
    return float(_fresh_search().bocha_timeout_sec or 15.0)


def get_bocha_count() -> int:
    return int(_fresh_search().bocha_count or 10)


def get_search_adaptive() -> bool:
    """Whether :class:`DualSearchClient` uses learned preferences.

    Reads ``KG_SEARCH_ADAPTIVE`` (default ``True``). When ``False``,
    the orchestrator falls back to the legacy fixed chain
    (DDG → mmx → Bocha) with no learning and no quarantine.
    """
    return bool(_fresh_search().adaptive)


def get_search_quarantine_sec() -> int:
    """Quarantine window for failed backends, in seconds.

    Reads ``KG_SEARCH_QUARANTINE_SEC`` (default 21600 = 6 h).
    """
    return max(0, int(_fresh_search().quarantine_sec or 6 * 3600))


def _fresh_search() -> SearchSettings:
    """Build a fresh ``SearchSettings`` so env mutations take effect immediately."""
    try:
        return SearchSettings()
    except Exception:
        return get_settings().search


def get_llm_reasoning_effort() -> str:
    """Reasoning effort level for reasoning-capable providers.

    Reads ``KG_LLM_REASONING_EFFORT`` (default ``""``). Empty means
    "don't send the parameter at all" — the OpenAICompatClient only
    includes ``reasoning_effort`` in the payload when this is non-empty,
    so unset = provider default behaviour.
    """
    return (get_settings().reasoning_effort or "").lower().strip()


def get_api_cors_origins() -> list[str]:
    return list(get_settings().api.cors_origins)


def get_agent_recursion_limit() -> int:
    return get_settings().agent.recursion_limit


def get_agent_memory_dir() -> Path:
    """Return the agent memory directory (absolute path).

    Reads ``KG_AGENT_MEMORY_DIR`` (default ``.agent_memory``).
    Relative paths are resolved against :func:`get_workspace_dir` so
    that ``.agent_memory`` lives next to ``knowledge_bases`` rather
    than inside the curated domain tree. Absolute paths (POSIX or
    Windows) are returned unchanged.
    """
    raw = (get_settings().agent.memory_dir or "").strip()
    if not raw:
        raw = ".agent_memory"
    if _looks_absolute(raw):
        return Path(raw.replace("\\", "/"))
    return get_workspace_dir() / raw


def get_agent_cards_enabled() -> bool:
    """Whether intent-driven prompt-card injection is active.

    Reads ``KG_AGENT_CARDS_ENABLED`` (default ``False``). When ``False``,
    :class:`src.agent.cards.CardsMiddleware` is still registered (so the
    agent build path is identical) but its hooks short-circuit and the
    SystemMessage is left untouched. Ops flips this on after the Phase-2
    migration lands.
    """
    return bool(get_settings().agent.cards_enabled)


def get_agent_cards_dir() -> Path:
    """Return the cards directory (absolute path).

    Reads ``KG_AGENT_CARDS_DIR`` (default ``""``). When empty, the cards
    middleware uses its bundled default at ``src/agent/cards/data/``.
    Relative non-empty paths resolve against ``KG_KB_ROOT``; absolute
    paths are taken as-is.
    """
    raw = (get_settings().agent.cards_dir or "").strip()
    if not raw:
        # Defer import to keep this module purely configuration-shaped.
        from src.agent.cards.middleware import _default_cards_dir

        return _default_cards_dir()
    if _looks_absolute(raw):
        return Path(raw.replace("\\", "/"))
    return get_kb_root() / raw


def get_agent_cards_debug() -> bool:
    """Whether to surface active-cards info on the agent SSE stream.

    Reads ``KG_AGENT_CARDS_DEBUG`` (default ``False``). Used by the SSE
    route to optionally append a debug frame — purely an observability aid.
    """
    return bool(get_settings().agent.cards_debug)


def get_agent_shell_enabled() -> bool:
    """Whether the agent exposes ``kg_shell_exec``.

    Reads ``KG_AGENT_SHELL_ENABLED`` (default ``True``).  Local dev /
    CLI workflows want this on; multi-tenant / demo / audit deployments
    should flip it off.
    """
    return bool(get_settings().agent.shell_enabled)


def get_agent_shell_timeout_sec() -> int:
    """Default per-command timeout (seconds) for ``kg_shell_exec``.

    Reads ``KG_AGENT_SHELL_TIMEOUT_SEC`` (default ``300``).  Individual
    calls can override via the ``timeout`` argument.
    """
    v = int(get_settings().agent.shell_timeout_sec)
    return v if v > 0 else 300


# ----------------------------------------------------------------------
# FalkorDB + Embedding accessors
# ----------------------------------------------------------------------


def get_falkordb_settings() -> FalkorDBSettings:
    """Return FalkorDB connection settings."""
    return get_settings().falkordb


def get_embedding_settings() -> EmbeddingSettings:
    """Return Embedding client settings."""
    return get_settings().embedding


def get_falkordb_enabled() -> bool:
    """Whether FalkorDB integration is active."""
    return bool(get_settings().falkordb.enabled)


def get_associations_source() -> str:
    """Where the associations-API endpoints read from.

    Reads :envvar:`KG_ASSOCIATIONS_SOURCE` via
    :class:`FalkorDBSettings.source`. Valid values:

    - ``"auto"``     — preferred default. Probe FalkorDB; fall back to the
      ``associations.json`` file when the graph store is disabled or
      unreachable.
    - ``"falkordb"`` — force FalkorDB (e.g. for staged rollout or
      canarying). Routes return an empty shell on failure.
    - ``"json"``     — always read from the on-disk ``associations.json``.
      Use this to roll back without redeploying if a FalkorDB
      regression sneaks in.
    """
    return (get_settings().falkordb.source or "auto").strip().lower()