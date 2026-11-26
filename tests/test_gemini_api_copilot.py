"""Tests for Gemini API Copilot skill and tri-copilot-team integration."""

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Add skill script to path
SKILL_SCRIPTS = os.path.expanduser("~/.gemini/config/skills/gemini-api-copilot/scripts")
if SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, SKILL_SCRIPTS)

gemini_query = pytest.importorskip("gemini_query", reason="External gemini-api-copilot skill not installed")

# Also import harness for readback testing
HARNESS_DIR = os.path.expanduser("~/.gemini/config/plugins/tri-copilot-team/scripts")
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

harness = pytest.importorskip("harness", reason="External tri-copilot-team harness not installed")


class TestGeminiAPICopilot(unittest.TestCase):
    """Test suite for gemini_query CLI and library."""

    def test_load_api_key_explicit(self):
        """Explicit key should have the highest priority."""
        key = gemini_query.load_api_key("explicit-key-123")
        self.assertEqual(key, "explicit-key-123")

    def test_load_api_key_env_var(self):
        """GEMINI_API_KEY environment variable should be loaded."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key-456"}):
            key = gemini_query.load_api_key()
            self.assertEqual(key, "env-key-456")

    def test_load_api_key_missing_raises_runtime_error(self):
        """Missing key with clean environment should raise RuntimeError."""
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(Path, "is_file", return_value=False):
            with self.assertRaises(RuntimeError):
                gemini_query.load_api_key(None)

    def test_strip_markdown_fences(self):
        """strip_markdown_fences should strip surrounding fences."""
        raw = "```python\ndef hello():\n    return 42\n```"
        clean = gemini_query.strip_markdown_fences(raw)
        self.assertEqual(clean, "def hello():\n    return 42\n")

    def test_strip_markdown_fences_with_conversational_preamble(self):
        """strip_markdown_fences should extract block even with conversational intros."""
        raw = "Here is the code:\n```python\ndef hello():\n    return 42\n```\nHope this helps!"
        clean = gemini_query.strip_markdown_fences(raw)
        self.assertEqual(clean, "def hello():\n    return 42\n")

    def test_strip_markdown_fences_no_op(self):
        """strip_markdown_fences should leave clean code untouched."""
        raw = "def hello():\n    return 42\n"
        clean = gemini_query.strip_markdown_fences(raw)
        self.assertEqual(clean, raw)

    def test_strip_markdown_fences_multiple_blocks(self):
        """When multiple blocks exist with prose, returns original text without guessing."""
        raw = "Here are two snippets:\n```python\ndef a(): pass\n```\nand\n```python\ndef b(): pass\n```"
        clean = gemini_query.strip_markdown_fences(raw)
        self.assertEqual(clean, raw)

    def test_build_context_files(self):
        """build_context should format existing files into delimiters."""
        test_file = Path("tests/test_gemini_api_copilot.py")
        context, warnings = gemini_query.build_context([str(test_file)])
        self.assertIn("--- BEGIN FILE: tests/test_gemini_api_copilot.py ---", context)
        self.assertIn("--- END FILE: tests/test_gemini_api_copilot.py ---", context)
        self.assertEqual(len(warnings), 0)

    def test_build_context_truncation(self):
        """build_context should respect max_chars limit."""
        test_file = Path("tests/test_gemini_api_copilot.py")
        context, warnings = gemini_query.build_context([str(test_file)], max_chars=100)
        self.assertIn("TRUNCATED AT 100 CHARS BUDGET", context)
        self.assertGreater(len(warnings), 0)

    def test_build_context_empty(self):
        """build_context with empty list should return empty string."""
        context, warnings = gemini_query.build_context([])
        self.assertEqual(context, "")
        self.assertEqual(len(warnings), 0)

    @patch("gemini_query.urllib.request.urlopen")
    def test_call_gemini_rest_success(self, mock_urlopen):
        """Successful REST response parsing."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Code generated successfully"}]
                    }
                }
            ]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, text, meta = gemini_query.call_gemini_rest(
            model="gemini-3.5-flash-lite",
            prompt="Hello",
            api_key="test-key"
        )
        self.assertTrue(ok)
        self.assertEqual(text, "Code generated successfully")
        self.assertIn("candidates", meta)

    @patch("gemini_query.call_gemini_rest")
    def test_query_gemini_cascade_fallback(self, mock_call):
        """Should cascade to next model if the first model fails."""
        mock_call.side_effect = [
            (False, "HTTP 503: High demand spike", {}),
            (True, "Fallback model output", {"candidates": []})
        ]

        result = gemini_query.query_gemini(
            prompt="Test prompt",
            task="code",
            models=["gemini-3.8-flash", "gemini-3.5-flash-lite"],
            api_key="test-key"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["model_used"], "gemini-3.5-flash-lite")
        self.assertEqual(result["output"], "Fallback model output")
        self.assertEqual(len(result["attempted_models"]), 2)

    @patch("gemini_query.call_gemini_rest")
    def test_query_gemini_all_fail(self, mock_call):
        """Should return ok=False if all models fail."""
        mock_call.return_value = (False, "HTTP 500: Server error", {})

        result = gemini_query.query_gemini(
            prompt="Test prompt",
            task="general",
            models=["model-a", "model-b"],
            api_key="test-key"
        )

        self.assertFalse(result["ok"])
        self.assertIsNone(result["model_used"])
        self.assertIn("model-a: HTTP 500", result["output"])
        self.assertIn("model-b: HTTP 500", result["output"])

    def test_tri_copilot_symlink_integrity(self):
        """Verify tri-copilot-team plugin script links to gemini_query.py."""
        plugin_script = Path("~/.gemini/config/plugins/tri-copilot-team/scripts/gemini_query.py").expanduser()
        self.assertTrue(plugin_script.exists())
        self.assertTrue(plugin_script.is_symlink() or plugin_script.is_file())

    def test_harness_readback_verification(self):
        """Verify harness readback checks files and SHA256 correctly."""
        ok, defects, artifacts = harness.verify_readback(Path("."), ["tests/test_gemini_api_copilot.py"])
        self.assertTrue(ok)
        self.assertEqual(len(defects), 0)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(len(artifacts[0]["sha256"]), 64)

    def test_ast_check_blocks_dummy_test_modification(self):
        """Adding a dummy line outside test_* functions in test file triggers defect."""
        content = "import os\ndef test_existing():\n    assert 1 + 1 == 2\n"
        # Dummy addition on line 1 (import os) but no test_* modified
        defects = harness.check_python_ast(content, added_lines={1}, is_test=True)
        defect_rules = [d[1] for d in defects]
        self.assertIn("no_test_function_touched_in_test_file", defect_rules)

    def test_outer_fence_check_handles_multiline_strings(self):
        """Outer fence check must flag boundary fences but ignore internal multiline strings."""
        boundary_fenced = ["```python", "def foo():", "    return 1", "```"]
        boundary_defects = harness._find_outer_fence_defects(boundary_fenced)
        self.assertGreater(len(boundary_defects), 0)

        inner_multiline = ['def foo():', '    doc = """```python', 'code', '```"""', '    return len(doc) > 0']
        inner_defects = harness._find_outer_fence_defects(inner_multiline)
        self.assertEqual(len(inner_defects), 0)


if __name__ == "__main__":
    unittest.main()
