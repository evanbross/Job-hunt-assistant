from __future__ import annotations

import unittest

from backend.captured_page_import_service import CapturedPageImportService


class CapturedPageImportServiceTests(unittest.TestCase):
    def test_extracts_public_supported_urls_from_html(self) -> None:
        service = CapturedPageImportService()
        html = """
        <html><body>
        <a href="https://job-boards.greenhouse.io/acme/jobs/123">Greenhouse</a>
        <a href="https://jobs.lever.co/acme/456">Lever</a>
        <a href="https://example.com/private">Ignore</a>
        </body></html>
        """

        rows = service.extract_public_job_urls(html, "html")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["platform"], "Greenhouse")
        self.assertEqual(rows[1]["platform"], "Lever")

    def test_extracts_html_parts_from_mhtml(self) -> None:
        service = CapturedPageImportService()
        mhtml = (
            "MIME-Version: 1.0\n"
            "Content-Type: multipart/related; boundary=\"BOUNDARY\"\n\n"
            "--BOUNDARY\n"
            "Content-Type: text/html; charset=\"utf-8\"\n\n"
            "<html><body><a href=\"https://job-boards.greenhouse.io/acme/jobs/123\">Job</a></body></html>\n"
            "--BOUNDARY--\n"
        )

        rows = service.extract_public_job_urls(mhtml, "mhtml")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(rows[0]["platform"], "Greenhouse")

    def test_analyzes_capture_source_from_single_greenhouse_board(self) -> None:
        service = CapturedPageImportService()
        html = """
        <html><body>
        <a href="https://job-boards.greenhouse.io/acme/jobs/123">Job</a>
        <a href="https://job-boards.greenhouse.io/acme/jobs/456">Job</a>
        </body></html>
        """

        analysis = service.analyze_capture(html, "html")

        self.assertEqual(len(analysis["urls"]), 2)
        self.assertEqual(analysis["source"]["url"], "https://job-boards.greenhouse.io/acme")
        self.assertEqual(analysis["source"]["platform"], "Greenhouse")

    def test_analyzes_capture_source_from_mhtml_content_location(self) -> None:
        service = CapturedPageImportService()
        mhtml = (
            "MIME-Version: 1.0\n"
            "Content-Type: multipart/related; boundary=\"BOUNDARY\"\n\n"
            "--BOUNDARY\n"
            "Content-Type: text/html; charset=\"utf-8\"\n"
            "Content-Location: https://jobs.lever.co/acme\n\n"
            "<html><body><a href=\"https://jobs.lever.co/acme/456\">Job</a></body></html>\n"
            "--BOUNDARY--\n"
        )

        analysis = service.analyze_capture(mhtml, "mhtml")

        self.assertEqual(analysis["source"]["url"], "https://jobs.lever.co/acme")
        self.assertEqual(analysis["source"]["platform"], "Lever")

    def test_rejects_empty_content(self) -> None:
        service = CapturedPageImportService()

        with self.assertRaisesRegex(ValueError, "Captured page content is required"):
            service.extract_public_job_urls("   ", "html")


if __name__ == "__main__":
    unittest.main()
