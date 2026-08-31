"""Search infrastructure — proxy-aware multi-backend (DuckDuckGo / mmx / Bocha)."""

from .base import AsyncSearchClient, SearchResult
from .bocha import (
    DEFAULT_COUNT as BOCHA_DEFAULT_COUNT,
    DEFAULT_ENDPOINT as BOCHA_DEFAULT_ENDPOINT,
    MAX_COUNT as BOCHA_MAX_COUNT,
    BochaSearchClient,
)
from .mmx import (
    DualSearchClient,
    DuckDuckGoClient,
    DuckDuckGoFallbackClient,
    MmxSearchClient,
    detect_proxy,
)
from .preference import (
    BACKEND_BOCHA,
    BACKEND_DDG,
    BACKEND_MMX,
    BackendState,
    SearchPreferenceStore,
)

__all__ = [
    "AsyncSearchClient",
    "SearchResult",
    "MmxSearchClient",
    "DuckDuckGoClient",
    "DuckDuckGoFallbackClient",
    "DualSearchClient",
    "BochaSearchClient",
    "BOCHA_DEFAULT_ENDPOINT",
    "BOCHA_DEFAULT_COUNT",
    "BOCHA_MAX_COUNT",
    "detect_proxy",
    "SearchPreferenceStore",
    "BackendState",
    "BACKEND_DDG",
    "BACKEND_MMX",
    "BACKEND_BOCHA",
]
