from __future__ import annotations

import unittest

from backend.manual_job_ingest_service import ManualJobIngestService


class StubJobRepo:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def insert(self, job: dict) -> int:
        stored = dict(job)
        stored["id"] = len(self.inserted) + 1
        self.inserted.append(stored)
        return stored["id"]

    def get_by_id(self, job_id: int) -> dict | None:
        for row in self.inserted:
            if row["id"] == job_id:
                return dict(row)
        return None

    def get_not_a_fit_with_feedback(self, limit: int = 20) -> list[dict]:
        return []


class StubResumeRepo:
    def get_latest(self) -> dict | None:
        return {"content": "Python backend engineer"}


class StubPenaltyRepo:
    def get_enabled(self) -> list[dict]:
        return []


class StubParser:
    def __init__(self, parsed_jobs: list[dict]) -> None:
        self._parsed_jobs = parsed_jobs

    def parse(self, subject: str, sender: str, body_snippet: str) -> list[dict]:
        return list(self._parsed_jobs)


class StubScorer:
    def __init__(self, score: int = 82) -> None:
        self._score = score

    def score(
        self,
        job_title: str,
        company: str,
        email_body: str,
        resume_text: str,
        penalty_rules: list[dict],
        fit_feedback: list[dict] | None = None,
    ) -> dict:
        return {"score": self._score, "reasoning": f"{job_title} at {company}"}


class StubCareerSiteScanner:
    def __init__(self, job: dict | None = None) -> None:
        self._job = job or {
            "site_name": "Manual URL",
            "site_url": "https://job-boards.greenhouse.io/acme/jobs/12345",
            "job_title": "Principal Engineer",
            "job_url": "https://job-boards.greenhouse.io/acme/jobs/12345",
            "job_text": "Public Greenhouse description text",
            "posted_at": None,
            "confidence_level": "high",
            "confidence_score": 93,
        }

    def scan_single_job_url(self, site_name: str, job_url: str) -> dict:
        return dict(self._job)


class ManualJobIngestServiceTests(unittest.TestCase):
    def test_ingest_creates_pending_review_job_from_best_match(self) -> None:
        job_repo = StubJobRepo()
        parser = StubParser(
            [
                {
                    "company": "",
                    "job_title": "",
                    "url": "",
                    "source": "",
                    "confidence": "low",
                    "confidence_score": 30,
                    "confidence_reason": "",
                },
                {
                    "company": "Acme",
                    "job_title": "Senior Software Engineer",
                    "url": "https://example.com/jobs/1",
                    "source": "Greenhouse",
                    "confidence": "high",
                    "confidence_score": 92,
                    "confidence_reason": "",
                },
            ]
        )
        service = ManualJobIngestService(
            job_repo,
            StubResumeRepo(),
            StubPenaltyRepo(),
            parser=parser,
            scorer=StubScorer(score=88),
        )

        result = service.ingest("Pasted listing text")

        self.assertEqual(result.parsed_job_count, 2)
        self.assertEqual(result.job["company"], "Acme")
        self.assertEqual(result.job["job_title"], "Senior Software Engineer")
        self.assertEqual(result.job["status"], "pending_review")
        self.assertEqual(result.job["fit_score"], 88)
        self.assertEqual(result.job["source"], "Greenhouse")

    def test_ingest_uses_manual_overrides_when_parser_is_incomplete(self) -> None:
        job_repo = StubJobRepo()
        service = ManualJobIngestService(
            job_repo,
            StubResumeRepo(),
            StubPenaltyRepo(),
            parser=StubParser(
                [
                    {
                        "company": "",
                        "job_title": "",
                        "url": "",
                        "source": "",
                        "confidence": "low",
                        "confidence_score": 40,
                        "confidence_reason": "",
                    }
                ]
            ),
            scorer=StubScorer(score=74),
        )

        result = service.ingest(
            "Pasted listing text",
            company_override="Acme",
            job_title_override="Senior Platform Engineer",
        )

        self.assertEqual(result.job["company"], "Acme")
        self.assertEqual(result.job["job_title"], "Senior Platform Engineer")
        self.assertEqual(result.job["status"], "pending_review")

    def test_ingest_marks_possible_duplicate_when_sheet_match_exists(self) -> None:
        job_repo = StubJobRepo()
        parser = StubParser(
            [
                {
                    "company": "Acme",
                    "job_title": "Senior Software Engineer",
                    "url": "",
                    "source": "",
                    "confidence": "high",
                    "confidence_score": 90,
                    "confidence_reason": "",
                }
            ]
        )
        service = ManualJobIngestService(
            job_repo,
            StubResumeRepo(),
            StubPenaltyRepo(),
            parser=parser,
            scorer=StubScorer(score=91),
        )

        result = service.ingest(
            "Pasted listing text",
            sheet_jobs=[{"company": "Acme", "job_title": "Senior Software Engineer"}],
        )

        self.assertEqual(result.job["status"], "possible_duplicate")
        self.assertIn("Possible duplicate", result.job["duplicate_hint"])

    def test_ingest_from_public_job_url_without_pasted_text(self) -> None:
        job_repo = StubJobRepo()
        service = ManualJobIngestService(
            job_repo,
            StubResumeRepo(),
            StubPenaltyRepo(),
            parser=StubParser([]),
            scorer=StubScorer(score=79),
            career_site_scanner=StubCareerSiteScanner(),
        )

        result = service.ingest(
            "",
            source_url="https://job-boards.greenhouse.io/acme/jobs/12345",
        )

        self.assertEqual(result.job["job_title"], "Principal Engineer")
        self.assertEqual(result.job["source"], "Greenhouse")
        self.assertEqual(result.job["url"], "https://job-boards.greenhouse.io/acme/jobs/12345")
        self.assertEqual(result.job["status"], "pending_review")

    def test_ingest_rejects_empty_text(self) -> None:
        service = ManualJobIngestService(
            StubJobRepo(),
            StubResumeRepo(),
            StubPenaltyRepo(),
            parser=StubParser([]),
            scorer=StubScorer(),
        )

        with self.assertRaisesRegex(ValueError, "Job listing text or a public job URL is required"):
            service.ingest("   ")


if __name__ == "__main__":
    unittest.main()
