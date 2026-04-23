"""
Fit scorer — uses the Anthropic API to score a job posting against the
candidate's profile and resume. Penalty rules are applied after the AI score.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

import anthropic

import config
from config import ANTHROPIC_API_KEY, SCORER_MODEL

logger = logging.getLogger(__name__)


class ScoreResult(TypedDict):
    score: int          # 0-100, penalties already applied
    reasoning: str


class FitScorer:
    """Scores job postings against the candidate profile using the Anthropic API."""

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def score(
        self,
        job_title: str,
        company: str,
        email_body: str,
        resume_text: str,
        penalty_rules: list[dict],
        fit_feedback: list[dict] | None = None,
    ) -> ScoreResult:
        """
        Score a job against the candidate profile.

        penalty_rules: list of dicts with 'rule_text' and 'penalty_points' keys.
        fit_feedback: recent "not a fit" skips with user-supplied reasons; injected
                      into the prompt so the scorer learns from past rejections.
        Penalties are applied to the raw AI score and the result is clamped 0-100.
        """
        system_prompt = self._build_system_prompt(resume_text, penalty_rules, fit_feedback or [])

        user_content = (
            f"Job Title: {job_title}\n"
            f"Company: {company}\n\n"
            f"Email / Job Description:\n{email_body[:6000]}"
        )

        # Cache both the static system content blocks so repeated scoring calls
        # within a session reuse the cached tokens.
        response = self._client.messages.create(
            model=SCORER_MODEL,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            result: dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Fit scorer returned non-JSON: %s", raw)
            raise ValueError(f"Scorer returned invalid JSON: {exc}") from exc

        raw_score: int = int(result.get("score", 50))
        reasoning: str = result.get("reasoning", "No reasoning provided.")

        # Apply penalty deductions for matching rules
        total_penalty = 0
        penalties_applied: list[str] = []
        body_lower = (job_title + " " + company + " " + email_body).lower()

        for rule in penalty_rules:
            rule_text: str = rule.get("rule_text", "").lower()
            if rule_text and rule_text in body_lower:
                pts = int(rule.get("penalty_points", 0))
                total_penalty += pts
                penalties_applied.append(f"-{pts} for '{rule_text}'")

        final_score = max(0, min(100, raw_score - total_penalty))

        if penalties_applied:
            reasoning += " Penalties applied: " + ", ".join(penalties_applied) + "."

        return ScoreResult(score=final_score, reasoning=reasoning)

    def _build_system_prompt(self, resume_text: str, penalty_rules: list[dict], fit_feedback: list[dict]) -> str:
        penalty_list = "\n".join(
            f"  - '{r['rule_text']}' → -{r['penalty_points']} pts"
            for r in penalty_rules
        ) or "  (none configured)"

        resume_section = (
            f"CANDIDATE RESUME:\n{resume_text[:8000]}"
            if resume_text
            else "CANDIDATE RESUME: Not yet uploaded."
        )

        if fit_feedback:
            feedback_lines = "\n".join(
                self._format_fit_feedback_line(r)
                for r in fit_feedback
            )
            feedback_section = (
                "PAST \"NOT A FIT\" FEEDBACK"
                " (recent jobs I rejected with my reasons — use these to calibrate future scores):\n"
                f"{feedback_lines}\n"
                "Score similar roles lower. Identify the pattern in each rejection and apply it broadly."
            )
        else:
            feedback_section = ""

        return f"""\
You are a job fit evaluator for a senior software engineer.

CANDIDATE PROFILE:
{config.load_candidate_profile()}

{resume_section}

SCORING INSTRUCTIONS:
- Score the job 0-100 based on fit with the candidate's skills and preferences.
- 90-100: Near-perfect match; strong skills overlap, no office requirement stated or remote explicitly confirmed, senior level.
- 70-89: Good match; most skills apply, location ambiguous with some uncertainty, or minor level mismatch.
- 40-69: Partial match; some relevant skills, ambiguous location, or level concerns.
- 0-39: Poor match; wrong skills, explicitly on-site/hybrid required, or wrong level.
- IMPORTANT — location inference rule: a city or state name alone (e.g. "Redwood City, CA")
  does NOT mean the role is on-site. Many remote jobs list the company's HQ address.
  Only treat a role as non-remote if the posting explicitly uses words such as
  "on-site", "in-office", "hybrid", "must be local", or "no remote". If location
  is ambiguous, assume remote is possible and do not penalise for it.
- When past rejection feedback includes structured mismatch reasons, treat them as high-signal preferences.
- Return ONLY valid JSON — no markdown, no extra text.
- Do NOT apply penalties yourself; they are handled by the caller.

ACTIVE PENALTY RULES (for your awareness only — do not double-apply):
{penalty_list}

{feedback_section}
Response schema (JSON only):
{{
  "score": <integer 0-100 before penalties>,
  "reasoning": "<2-3 sentence explanation of the score>"
}}
"""

    @staticmethod
    def _format_fit_feedback_line(row: dict) -> str:
        mismatch_reasons_raw = (row.get("skip_mismatch_reasons") or "").strip()
        mismatch_reasons = [part.strip() for part in mismatch_reasons_raw.split("|") if part.strip()]
        note = (row.get("skip_feedback") or "").strip()
        details: list[str] = []
        if mismatch_reasons:
            details.append("Mismatch reasons: " + ", ".join(mismatch_reasons))
        if note:
            details.append(f'Note: "{note}"')
        detail_text = " | ".join(details) if details else "No additional note provided."
        return (
            f'  - "{row["job_title"]} at {row["company"]}"'
            f' (AI score was {row["fit_score"] if row["fit_score"] is not None else "unscored"}): '
            f"{detail_text}"
        )
