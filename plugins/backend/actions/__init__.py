"""The backend's actions: one folder per action, named as the action is."""

from __future__ import annotations

from plugins.backend.actions.answer_question import ACTION as ANSWER_QUESTION
from plugins.backend.actions.trace_problem import ACTION as TRACE_PROBLEM

__all__ = ["ACTIONS", "ANSWER_QUESTION", "TRACE_PROBLEM"]

ACTIONS = (TRACE_PROBLEM, ANSWER_QUESTION)
