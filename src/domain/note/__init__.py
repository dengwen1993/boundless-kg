"""Note-generation domain — structured 3-section prompt + pure helpers."""

from .generator import NoteGenerator, PROMPT_VERSION, generate_note_async
from .text_utils import extract_first_definition_summary, count_words, extract_source

__all__ = [
    "NoteGenerator",
    "generate_note_async",
    "PROMPT_VERSION",
    "extract_first_definition_summary",
    "count_words",
    "extract_source",
]
