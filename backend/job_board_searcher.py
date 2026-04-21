"""
Job board searcher — proactively searches public job boards for new openings
that match the candidate profile.

Sources (free, no additional API keys required):
  - Remotive.com  — remote-only jobs, REST API, keyword search
  - The Muse      — broad tech job search, category + level filter

Each source is tried independently; failures are logged and skipped so a
single unavailable board does not abort the whole run.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from typing import TypedDict

import requests

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT: int = 15
_INTER_REQUEST_DELAY: float = 0.8  # seconds between API calls to be polite


class JobBoardResult(TypedDict):
    title: str
    company: str
    url: str
    description: str   # plain text, max 8000 chars
    source: str
    date_posted: str | None  # ISO date (YYYY-MM-DD) or None


def strip_html(raw: str) -> str:
    """Remove HTML tags and decode entities to get plain text."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def url_to_email_id(url: str) -> str:
    """Derive a stable, unique email_id for a board-sourced job from its URL."""
    digest = hashlib.md5(url.encode()).hexdigest()[:16]
    return f"board:{digest}"


class JobBoardSearcher:
    """Fetches and normalises job listings from public job board APIs."""

    _HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    def search_all(self, search_queries: list[str]) -> list[JobBoardResult]:
        """
        Search all boards and return a deduplicated list of results.

        search_queries — keyword strings used for Remotive's search param.
        """
        results: list[JobBoardResult] = []
        seen_urls: set[str] = set()

        def _add(r: JobBoardResult) -> None:
            if r["url"] and r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                results.append(r)

        # Remotive — one request per search query
        for query in search_queries:
            try:
                for r in self._search_remotive(query):
                    _add(r)
                time.sleep(_INTER_REQUEST_DELAY)
            except Exception as exc:
                logger.warning("Remotive search '%s' failed: %s", query, exc)

        # The Muse — category + level filter, first 3 pages
        try:
            for r in self._search_muse():
                _add(r)
        except Exception as exc:
            logger.warning("The Muse search failed: %s", exc)

        return results

    # ── Remotive ──────────────────────────────────────────────────────────────

    def _search_remotive(self, query: str) -> list[JobBoardResult]:
        resp = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"category": "software-dev", "search": query, "limit": 50},
            headers=self._HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        out: list[JobBoardResult] = []
        for job in data.get("jobs", []):
            title: str = (job.get("title") or "").strip()
            company: str = (job.get("company_name") or "").strip()
            url: str = (job.get("url") or "").strip()
            description: str = strip_html(job.get("description") or "")[:8000]
            raw_date: str = job.get("publication_date") or ""
            date_posted: str | None = raw_date[:10] if raw_date else None

            if not title or not url:
                continue

            out.append(
                JobBoardResult(
                    title=title,
                    company=company,
                    url=url,
                    description=description,
                    source="Remotive",
                    date_posted=date_posted,
                )
            )

        return out

    # ── The Muse ──────────────────────────────────────────────────────────────

    def _search_muse(self) -> list[JobBoardResult]:
        out: list[JobBoardResult] = []
        for page in range(3):
            resp = requests.get(
                "https://www.themuse.com/api/public/jobs",
                params={
                    "category": "Software Engineering",
                    "level": "Senior Level",
                    "page": page,
                },
                headers=self._HEADERS,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            page_results = data.get("results", [])
            if not page_results:
                break

            for job in page_results:
                title: str = (job.get("name") or "").strip()
                company: str = ((job.get("company") or {}).get("name") or "").strip()
                url: str = ((job.get("refs") or {}).get("landing_page") or "").strip()
                description: str = strip_html(job.get("contents") or "")[:8000]
                raw_date: str = job.get("publication_date") or ""
                date_posted: str | None = raw_date[:10] if raw_date else None

                if not title or not url:
                    continue

                out.append(
                    JobBoardResult(
                        title=title,
                        company=company,
                        url=url,
                        description=description,
                        source="The Muse",
                        date_posted=date_posted,
                    )
                )

            time.sleep(_INTER_REQUEST_DELAY)

        return out