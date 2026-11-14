"""Contract tests for the n8n webhook -> GitHub Actions dispatch boundary."""
import json
from pathlib import Path
import unittest

WORKFLOW = Path(".github/workflows/clip.yml").read_text(encoding="utf-8")
N8N = json.loads(Path("n8n/portable-clipper.json").read_text(encoding="utf-8"))
NODES = {node["name"]: node for node in N8N["nodes"]}


class N8nDispatchContractTests(unittest.TestCase):
    def test_webhook_path_and_method_match_dashboard(self):
        webhook = NODES["Submit Video"]["parameters"]
        self.assertEqual(webhook["httpMethod"], "POST")
        self.assertEqual(webhook["path"], "gaming-clipper")
        self.assertIn("/webhook/gaming-clipper", Path("index.html").read_text(encoding="utf-8"))
        self.assertEqual(webhook["responseMode"], "onReceived")

    def test_dispatch_targets_the_expected_github_workflow(self):
        dispatch = NODES["Start GitHub Clipper"]["parameters"]
        self.assertEqual(
            dispatch["url"],
            "https://api.github.com/repos/chnumair4888-ux/clipper/actions/workflows/clip.yml/dispatches",
        )
        self.assertEqual(dispatch["method"], "POST")
        self.assertTrue(dispatch["sendBody"])
        self.assertEqual(dispatch["nodeCredentialType"], "httpHeaderAuth")

    def test_dispatch_maps_all_supported_workflow_inputs(self):
        dispatch_body = NODES["Start GitHub Clipper"]["parameters"]["jsonBody"]
        expected_inputs = {
            "url", "clips", "orientation", "length", "captions", "background",
            "language", "edit_style", "audio_mode", "voice_language",
            "voice_gender", "title_overlay", "brand", "range",
        }
        for name in expected_inputs:
            with self.subTest(input_name=name):
                self.assertIn(f"{name}:", dispatch_body)
                self.assertIn(f"{name}:", WORKFLOW)

    def test_workflow_dispatch_ref_is_main(self):
        # The n8n template intentionally dispatches main. Stacked PRs must be
        # integrated before this workflow is used for a production test.
        body = NODES["Start GitHub Clipper"]["parameters"]["jsonBody"]
        self.assertIn("ref: 'main'", body)

    def test_exported_workflow_contains_no_access_token(self):
        serialized = json.dumps(N8N)
        self.assertNotIn("Bearer ", serialized)
        self.assertNotIn("ghp_", serialized)
        self.assertNotIn("github_pat_", serialized)

    def test_import_template_is_inactive_until_configured(self):
        self.assertFalse(N8N["active"])
        self.assertEqual(NODES["Start GitHub Clipper"]["parameters"]["authentication"],
                         "predefinedCredentialType")


if __name__ == "__main__":
    unittest.main(verbosity=2)
