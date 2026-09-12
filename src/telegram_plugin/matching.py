"""Deterministic fuzzy matching for Telegram dialog metadata."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher


def normalize_dialog_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = normalized.removeprefix("@")
    words = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(words.split())


def rank_dialogs(dialogs: Iterable[dict], query: str, limit: int) -> list[dict]:
    normalized_query = normalize_dialog_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty")
    username_hint = unicodedata.normalize("NFKC", query).strip().startswith("@")

    candidates = []
    for dialog in dialogs:
        best = max(
            (
                _field_match(
                    "title",
                    dialog.get("title"),
                    normalized_query,
                    fuzzy_only=username_hint,
                ),
                _field_match("username", dialog.get("username"), normalized_query),
            ),
            key=lambda match: match[0],
        )
        if best[0]:
            candidates.append({**dialog, "score": best[0], "matched_by": best[1]})

    candidates.sort(
        key=lambda row: (
            -row["score"],
            normalize_dialog_text(row.get("title") or ""),
            int(row["id"]),
        )
    )
    return candidates[:limit]


def _field_match(
    field: str,
    value: object,
    query: str,
    *,
    fuzzy_only: bool = False,
) -> tuple[int, str]:
    if not isinstance(value, str):
        return 0, ""
    candidate = normalize_dialog_text(value)
    if not candidate:
        return 0, ""
    if not fuzzy_only and candidate == query:
        return 100, f"{field}_exact"
    if not fuzzy_only and candidate.startswith(query):
        return 90, f"{field}_prefix"
    if not fuzzy_only and query in candidate:
        return 80, f"{field}_substring"
    if not fuzzy_only and all(token in candidate.split() for token in query.split()):
        return 70, f"{field}_all_tokens"

    ratio = SequenceMatcher(None, query, candidate).ratio()
    if ratio < 0.60:
        return 0, ""
    score = min(69, 40 + int((ratio - 0.60) / 0.40 * 29))
    return score, f"{field}_typo"
