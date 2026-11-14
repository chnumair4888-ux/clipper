"""Lightweight contract tests for the GitHub Actions clip pipeline."""
from pathlib import Path
import unittest

WORKFLOW = Path(".github/workflows/clip.yml").read_text(encoding="utf-8")
DASHBOARD = Path("index.html").read_text(encoding="utf-8")


class WorkflowContractTests(unittest.TestCase):
    def test_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read", WORKFLOW)

    def test_concurrent_dispatches_are_not_cancelled(self):
        self.assertIn("cancel-in-progress: false", WORKFLOW)

    def test_python_syntax_check_runs_before_processing(self):
        syntax = WORKFLOW.index("python -m py_compile clipper.py")
        process = WORKFLOW.index("python clipper.py")
        self.assertLess(syntax, process)

    def test_validated_outputs_are_checked_before_upload(self):
        verify = WORKFLOW.index("name: Verify validated outputs")
        upload = WORKFLOW.index("name: Upload clips to Oracle")
        self.assertLess(verify, upload)
        self.assertIn("out/quality_report.json", WORKFLOW)
        self.assertIn('item.get("status") != "passed"', WORKFLOW)
        self.assertIn("MP4/report count mismatch", WORKFLOW)

    def test_upload_fails_without_files_and_retries_transient_errors(self):
        self.assertIn("No MP4 files available for upload.", WORKFLOW)
        self.assertIn("--retry-all-errors", WORKFLOW)
        self.assertIn("--connect-timeout 20", WORKFLOW)
        self.assertIn("--max-time 900", WORKFLOW)

    def test_dashboard_clip_limit_matches_workflow_limit(self):
        self.assertIn('max="10"', DASHBOARD)
        self.assertIn("between 1 and 10 clips", DASHBOARD)

    def test_dashboard_validates_time_range(self):
        self.assertIn("start-end in minutes", DASHBOARD)
        self.assertIn("end <= start", DASHBOARD)

    def test_dashboard_does_not_claim_webhook_ack_means_processing_started(self):
        self.assertIn("does not confirm that GitHub Actions started", DASHBOARD)

    def test_secrets_are_not_printed(self):
        self.assertNotIn('echo "$ORACLE_UPLOAD_TOKEN"', WORKFLOW)
        self.assertNotIn('echo "$GEMINI_API_KEY"', WORKFLOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
