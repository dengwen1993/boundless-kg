"""FastAPI dependencies — singletons pulled from the application layer."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from src.agent.dependencies import (
    get_generation_pipeline,
    get_graph_service,
    get_note_service,
    get_plan_service,
    get_resource_service,
    get_timeline_service,
)


@lru_cache(maxsize=1)
def _graph():
    return get_graph_service()


@lru_cache(maxsize=1)
def _note():
    return get_note_service()


@lru_cache(maxsize=1)
def _resource():
    return get_resource_service()


@lru_cache(maxsize=1)
def _plan():
    return get_plan_service()


@lru_cache(maxsize=1)
def _timeline():
    return get_timeline_service()


@lru_cache(maxsize=1)
def _pipeline():
    return get_generation_pipeline()


def graph_service_dep():
    return _graph()


def note_service_dep():
    return _note()


def resource_service_dep():
    return _resource()


def plan_service_dep():
    return _plan()


def timeline_service_dep():
    return _timeline()


def generation_pipeline_dep():
    return _pipeline()


__all__ = [
    "graph_service_dep",
    "note_service_dep",
    "resource_service_dep",
    "plan_service_dep",
    "timeline_service_dep",
    "generation_pipeline_dep",
]