from __future__ import annotations

import unittest

from backend.email_parser import _normalize_title_company


class EmailParserNormalizationTests(unittest.TestCase):
    def test_extracts_company_from_job_application_title(self) -> None:
        item = {
            "company": "",
            "job_title": "Job Application for Senior Software Engineer - Workflows at Transcend Inc.",
            "source": "",
        }

        _normalize_title_company(item, "")

        self.assertEqual(item["company"], "Transcend Inc.")
        self.assertEqual(item["job_title"], "Senior Software Engineer - Workflows")

    def test_extracts_company_from_subject_when_title_is_clean(self) -> None:
        item = {
            "company": "Unknown Company",
            "job_title": "Senior Software Engineer - Workflows",
            "source": "",
        }

        _normalize_title_company(
            item,
            "Job Application for Senior Software Engineer - Workflows at Transcend Inc.",
        )

        self.assertEqual(item["company"], "Transcend Inc.")
        self.assertEqual(item["job_title"], "Senior Software Engineer - Workflows")

    def test_extracts_company_from_body_when_title_is_clean(self) -> None:
        item = {
            "company": "Unknown Company",
            "job_title": "Senior Software Engineer - Workflows",
            "source": "",
        }

        _normalize_title_company(
            item,
            "Manual pasted job listing",
            "Senior Software Engineer - Workflows at Transcend Inc.\nRemote",
        )

        self.assertEqual(item["company"], "Transcend Inc.")
        self.assertEqual(item["job_title"], "Senior Software Engineer - Workflows")

    def test_extracts_title_and_company_from_body_when_title_is_unknown(self) -> None:
        item = {
            "company": "Unknown Company",
            "job_title": "",
            "source": "",
        }

        _normalize_title_company(
            item,
            "Manual pasted job listing",
            "Senior Software Engineer - Workflows at Transcend Inc.\nRemote",
        )

        self.assertEqual(item["company"], "Transcend Inc.")
        self.assertEqual(item["job_title"], "Senior Software Engineer - Workflows")

    def test_keeps_existing_known_company(self) -> None:
        item = {
            "company": "Acme",
            "job_title": "Job Application for Senior Engineer at Transcend Inc.",
            "source": "",
        }

        _normalize_title_company(item, "")

        self.assertEqual(item["company"], "Acme")
        self.assertEqual(item["job_title"], "Job Application for Senior Engineer at Transcend Inc.")


if __name__ == "__main__":
    unittest.main()
