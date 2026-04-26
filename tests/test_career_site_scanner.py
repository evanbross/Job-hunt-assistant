import unittest
from urllib.parse import urlparse
from unittest.mock import patch

from backend.career_site_scanner import (
    CareerSiteScanner,
    _detect_embedded_ashby_base_url,
    _extract_json_array_by_key,
    _greenhouse_board_token,
    _is_greenhouse_single_job_url,
    _is_unsupported_greenhouse_search_url,
    _normalize_date,
    _unique_jobs,
    _workday_job_url,
    _workday_parts,
)


class CareerSiteScannerHelpersTests(unittest.TestCase):
    def test_greenhouse_token_from_query_param(self):
        parsed = urlparse("https://boards.greenhouse.io/embed/job_board?for=acme")
        self.assertEqual(_greenhouse_board_token(parsed), "acme")

    def test_greenhouse_token_from_path(self):
        parsed = urlparse("https://boards.greenhouse.io/acme")
        self.assertEqual(_greenhouse_board_token(parsed), "acme")

    def test_greenhouse_token_from_company_jobs_path(self):
        parsed = urlparse("https://job-boards.greenhouse.io/acme/jobs")
        self.assertEqual(_greenhouse_board_token(parsed), "acme")

    def test_greenhouse_token_from_jobs_prefix_path(self):
        parsed = urlparse("https://job-boards.greenhouse.io/jobs/acme")
        self.assertEqual(_greenhouse_board_token(parsed), "acme")

    def test_greenhouse_token_from_embed_jobs_path(self):
        parsed = urlparse("https://boards.greenhouse.io/embed/jobs/acme")
        self.assertEqual(_greenhouse_board_token(parsed), "acme")

    def test_greenhouse_token_absent_on_generic_embed_path(self):
        parsed = urlparse("https://boards.greenhouse.io/embed/job_board")
        self.assertIsNone(_greenhouse_board_token(parsed))

    def test_greenhouse_token_absent_on_cross_company_search(self):
        parsed = urlparse(
            "https://my.greenhouse.io/jobs?query=software&salary=more_than_200k"
        )
        self.assertIsNone(_greenhouse_board_token(parsed))

    def test_greenhouse_search_url_marked_unsupported(self):
        parsed = urlparse(
            "https://my.greenhouse.io/jobs?query=software&salary=more_than_200k"
        )
        self.assertTrue(_is_unsupported_greenhouse_search_url(parsed))

    def test_greenhouse_single_job_url_is_detected(self):
        parsed = urlparse("https://job-boards.greenhouse.io/acme/jobs/12345")
        self.assertTrue(_is_greenhouse_single_job_url(parsed))

    def test_greenhouse_board_url_is_not_single_job(self):
        parsed = urlparse("https://job-boards.greenhouse.io/acme/jobs")
        self.assertFalse(_is_greenhouse_single_job_url(parsed))

    def test_workday_parts_extract_tenant_and_slug(self):
        parts = _workday_parts("https://acme.wd5.myworkdayjobs.com/en-US/External")
        self.assertEqual(parts, ("acme", "External"))

    def test_workday_job_url_uses_host_root_not_listing_path(self):
        site_url = "https://acme.wd5.myworkdayjobs.com/en-US/External"
        external_path = "/en-US/External/job/NYC/Role_JOB-123"
        expected = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/NYC/Role_JOB-123"
        self.assertEqual(_workday_job_url(site_url, external_path), expected)

    def test_normalize_date(self):
        self.assertEqual(_normalize_date("2026-04-21T10:20:00Z"), "2026-04-21")
        self.assertEqual(_normalize_date("posted 2026-04-01"), "2026-04-01")
        self.assertEqual(_normalize_date("April 2, 2026"), "2026-04-02")
        self.assertEqual(_normalize_date("Apr 2, 2026"), "2026-04-02")

    def test_unique_jobs_deduplicates_by_url(self):
        jobs = [
            {
                "site_name": "Site",
                "site_url": "https://site",
                "job_title": "A",
                "job_url": "https://site/jobs/1",
                "job_text": "x",
                "posted_at": None,
                "confidence_level": "high",
                "confidence_score": 90,
            },
            {
                "site_name": "Site",
                "site_url": "https://site",
                "job_title": "B",
                "job_url": "https://site/jobs/1",
                "job_text": "y",
                "posted_at": None,
                "confidence_level": "medium",
                "confidence_score": 70,
            },
        ]
        unique = _unique_jobs(jobs)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["job_title"], "A")

    def test_adapter_failure_is_captured_and_falls_back(self):
        scanner = CareerSiteScanner()
        with patch.object(scanner, "_scan_greenhouse", side_effect=TypeError("bad parser")):
            with patch.object(scanner, "_scan_generic", return_value=([], "generic")):
                outcome = scanner.scan_site("Acme", "https://boards.greenhouse.io/acme")

        self.assertEqual(outcome["jobs"], [])
        self.assertEqual(outcome["detected_platform"], "generic")
        failures = scanner.pop_failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["platform"], "greenhouse")
        self.assertEqual(failures[0]["stage"], "adapter")
        self.assertEqual(failures[0]["error_type"], "TypeError")
        self.assertIn("bad parser", failures[0]["error_message"])

    def test_greenhouse_search_url_skips_board_adapter_failure(self):
        scanner = CareerSiteScanner()
        site_url = (
            "https://my.greenhouse.io/jobs?query=software&salary=more_than_200k"
            "&work_type[]=remote&employment_type[]=full_time"
        )
        with patch.object(scanner, "_scan_greenhouse") as greenhouse_scan:
            with self.assertRaisesRegex(ValueError, "not a public job board"):
                scanner.scan_site("Greenhouse", site_url)

        greenhouse_scan.assert_not_called()
        self.assertEqual(scanner.pop_failures(), [])

    def test_pop_failures_clears_internal_buffer(self):
        scanner = CareerSiteScanner()
        with patch.object(scanner, "_scan_greenhouse", side_effect=RuntimeError("boom")):
            with patch.object(scanner, "_scan_generic", return_value=([], "generic")):
                scanner.scan_site("Acme", "https://boards.greenhouse.io/acme")

        self.assertEqual(len(scanner.pop_failures()), 1)
        self.assertEqual(scanner.pop_failures(), [])

    def test_extract_json_array_by_key(self):
        text = '{"teams":[{"id":1}],"jobPostings":[{"title":"A"},{"title":"B"}],"x":1}'
        rows = _extract_json_array_by_key(text, "jobPostings")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "A")

    def test_detect_embedded_ashby_base_url(self):
        html = '<script src="https://jobs.ashbyhq.com/change/embed?version=2"></script>'
        self.assertEqual(
            _detect_embedded_ashby_base_url(html),
            "https://jobs.ashbyhq.com/change",
        )

    def test_scan_site_uses_single_greenhouse_job_adapter(self):
        scanner = CareerSiteScanner()
        expected_job = {
            "site_name": "Acme",
            "site_url": "https://job-boards.greenhouse.io/acme/jobs/12345",
            "job_title": "Senior Engineer",
            "job_url": "https://job-boards.greenhouse.io/acme/jobs/12345",
            "job_text": "Full description",
            "posted_at": None,
            "confidence_level": "high",
            "confidence_score": 88,
        }
        with patch.object(scanner, "scan_single_job_url", return_value=expected_job) as single_scan:
            outcome = scanner.scan_site("Acme", "https://job-boards.greenhouse.io/acme/jobs/12345")

        single_scan.assert_called_once()
        self.assertEqual(outcome["detected_platform"], "greenhouse")
        self.assertEqual(outcome["jobs"], [expected_job])


if __name__ == "__main__":
    unittest.main()
