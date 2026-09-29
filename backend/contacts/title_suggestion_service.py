# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Tier 1 (keyword) and Tier 2 (LLM) suggestions for the Titles report
(docs/contacts-recruiter-classification-plan.md, build-order item 4).

Precision first, per the plan's "Auto-flag safety": this module only ever
*previews* suggestions -- nothing here writes to the database. A suggestion
becomes a real title rule only when the caller (routes/contacts.py's
apply_title_suggestions) explicitly applies it, which reuses
PostgresTitleRuleRepository.set_rule -- the same path a person clicking
"Flag as recruiter" in the Titles report goes through, so an accepted
suggestion is a normal, undoable title rule with no special-casing."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.ai_provider import AIMessage, AIProviderClient, AIProviderError
from backend.contacts.title_suggestion_rules import classify_title_tier1

logger = logging.getLogger(__name__)

_AI_BATCH_SIZE = 50


@dataclass(frozen=True)
class TitleSuggestion:
    normalized_title: str
    title: str
    reason: str


@dataclass(frozen=True)
class SuggestionResult:
    suggestions: list[TitleSuggestion]
    ai_error: str | None = None


# ai: claude-sonnet-5 | 2026-09-28
def suggest_titles(user_id: int, repos: dict[str, Any], *, use_ai: bool) -> SuggestionResult:
    """Suggest recruiter titles among this account's currently-unflagged,
    undecided titles. Tier 1 (keyword match, always runs) flags high-confidence
    titles outright and promotes an ambiguous title only when at least one of
    its contacts is at an agency-flagged company. Tier 2 (use_ai=True) sends
    whatever's left over -- title + one example company, no names -- to the
    account's configured AI provider in batches."""
    report = repos["title_rules"].titles_report(user_id)
    candidates = [
        t
        for t in report
        if t["normalized_title"] and not t["rule_exists"] and t["unflagged_count"] > 0
    ]

    suggestions: list[TitleSuggestion] = []
    ambiguous: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for t in candidates:
        verdict = classify_title_tier1(t["normalized_title"])
        if verdict == "flag":
            suggestions.append(TitleSuggestion(t["normalized_title"], t["title"], "keyword match"))
        elif verdict == "skip":
            ambiguous.append(t)
        else:
            leftovers.append(t)

    if ambiguous:
        agency_titles = repos["title_rules"].titles_with_agency_company(
            user_id, [t["normalized_title"] for t in ambiguous]
        )
        for t in ambiguous:
            if t["normalized_title"] in agency_titles:
                suggestions.append(
                    TitleSuggestion(
                        t["normalized_title"], t["title"], "ambiguous title + recruiting agency"
                    )
                )

    ai_error: str | None = None
    if use_ai and leftovers:
        ai_suggestions, ai_error = _classify_leftovers_via_ai(user_id, repos, leftovers)
        suggestions.extend(ai_suggestions)

    return SuggestionResult(suggestions=suggestions, ai_error=ai_error)


def _classify_leftovers_via_ai(
    user_id: int, repos: dict[str, Any], leftovers: list[dict[str, Any]]
) -> tuple[list[TitleSuggestion], str | None]:
    normalized_titles = [t["normalized_title"] for t in leftovers]
    companies = repos["title_rules"].representative_companies(user_id, normalized_titles)
    client = AIProviderClient(user_id)

    suggestions: list[TitleSuggestion] = []
    for batch_start in range(0, len(leftovers), _AI_BATCH_SIZE):
        batch = leftovers[batch_start : batch_start + _AI_BATCH_SIZE]
        try:
            labels = _classify_batch(client, batch, companies)
        except (AIProviderError, ValueError) as exc:
            logger.warning("Title suggestion AI batch failed: %s", exc)
            return suggestions, str(exc)
        for t in batch:
            if labels.get(t["normalized_title"]) == "recruiter":
                suggestions.append(TitleSuggestion(t["normalized_title"], t["title"], "AI suggested"))
    return suggestions, None


def _classify_batch(
    client: AIProviderClient, batch: list[dict[str, Any]], companies: dict[str, str]
) -> dict[str, str]:
    lines = []
    for i, t in enumerate(batch, start=1):
        company = companies.get(t["normalized_title"]) or "(unknown company)"
        lines.append(f'{i}. "{t["title"]}" @ {company}')
    prompt = (
        "Classify each numbered job title (with an example employer) as a "
        "recruiting / talent-acquisition / sourcing role or not.\n"
        'Respond with a JSON array only, one object per line below in the same '
        'order, each shaped like {"label": "recruiter"} or {"label": "not_recruiter"} '
        'or {"label": "unsure"}.\n'
        'Use "recruiter" only when confident from the title alone; otherwise '
        '"unsure". False negatives are preferred over false positives -- when '
        "unsure, say unsure.\n\n" + "\n".join(lines)
    )
    raw = client.complete(
        messages=[AIMessage(role="user", content=prompt)],
        max_tokens=2000,
        json_response=True,
    )
    labels = _parse_label_array(raw, expected_count=len(batch))
    return {t["normalized_title"]: label for t, label in zip(batch, labels, strict=True)}


def _parse_label_array(raw: str, *, expected_count: int) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Model may prefix the JSON with prose and wrap it in a code fence.
        match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, flags=re.DOTALL)
        if match:
            raw = match.group(1).strip()
        else:
            bracket = raw.find("[")
            if bracket >= 0:
                raw = raw[bracket:]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI title classifier returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, list) or len(parsed) != expected_count:
        got = len(parsed) if isinstance(parsed, list) else type(parsed).__name__
        raise ValueError(f"AI title classifier returned {got} labels, expected {expected_count}")

    return [
        str(item.get("label", "unsure")).strip().lower() if isinstance(item, dict) else "unsure"
        for item in parsed
    ]
