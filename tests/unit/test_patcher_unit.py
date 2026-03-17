"""
Unit tests for the Patch generation and application pipeline in patcher.py.
"""
import textwrap, tempfile
from pathlib import Path
import pytest

from patcher import generate_patch, apply_patch, apply_patches, preview_patches, Patch
from parser import parse_file


def _write_tmp(content: str, suffix: str = ".py") -> str:
    tf = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8")
    tf.write(textwrap.dedent(content))
    tf.flush()
    return tf.name


# ---------------------------------------------------------------------------
# 1. generate_patch
# ---------------------------------------------------------------------------

class TestGeneratePatch:
    def test_generates_correct_new_expression_py(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_placeholder("Password").fill("secret")
        """)
        sel = parse_file(path)[0]
        new_sel = {"strategy": "testid", "value": "password-input"}
        patch = generate_patch(sel, new_sel, confidence=0.9)
        assert "get_by_test_id" in patch.new_expression
        assert "password-input" in patch.new_expression

    def test_reason_auto_generated(self):
        path = _write_tmp("""
            def test(page):
                page.locator("#submit").click()
        """)
        sel = parse_file(path)[0]
        patch = generate_patch(sel, {"strategy": "role", "value": {"role": "button", "name": "Submit"}}, 0.85)
        assert "id" in patch.reason
        assert "role" in patch.reason

    def test_custom_reason_preserved(self):
        path = _write_tmp("""
            def test(page):
                page.locator(".btn").click()
        """)
        sel = parse_file(path)[0]
        patch = generate_patch(sel, {"strategy": "testid", "value": "btn"}, 0.8, reason="Custom reason")
        assert patch.reason == "Custom reason"


# ---------------------------------------------------------------------------
# 2. apply_patch – roundtrip
# ---------------------------------------------------------------------------

class TestApplyPatch:
    def _make_patch_from_file(self, content, new_sel):
        path = _write_tmp(content)
        sel = parse_file(path)[0]
        return generate_patch(sel, new_sel, 0.9), path

    def test_patch_applies_without_error(self):
        patch, path = self._make_patch_from_file(
            """
            def test(page):
                page.locator("#user-email").fill("foo@bar.com")
            """,
            {"strategy": "testid", "value": "user-email"},
        )
        result = apply_patch(patch)
        assert result is True

    def test_patch_changes_file_content(self):
        patch, path = self._make_patch_from_file(
            """
            def test(page):
                page.locator("#submit-btn").click()
            """,
            {"strategy": "testid", "value": "submit-btn"},
        )
        apply_patch(patch)
        content = Path(path).read_text()
        assert "get_by_test_id" in content
        assert "#submit-btn" not in content

    def test_patch_fails_when_expression_not_found(self):
        """A patch with a wrong old_expression should return False."""
        p = Patch(
            file_path="/nonexistent_test.py",
            line_number=1,
            old_expression="page.locator('.nonexistent')",
            new_expression='page.get_by_test_id("x")',
            old_selector={"strategy": "css", "value": ".nonexistent"},
            new_selector={"strategy": "testid", "value": "x"},
            confidence=0.9,
            reason="test",
        )
        result = apply_patch(p)
        assert result is False


# ---------------------------------------------------------------------------
# 3. apply_patches – multi-file, bottom-to-top ordering
# ---------------------------------------------------------------------------

class TestApplyPatches:
    def test_multiple_patches_applied(self):
        path = _write_tmp("""
            def test(page):
                page.locator("#email").fill("a@b.com")
                page.locator("#password").fill("secret")
        """)
        sels = parse_file(path)
        assert len(sels) == 2

        patches = [
            generate_patch(sels[0], {"strategy": "testid", "value": "email"}, 0.9),
            generate_patch(sels[1], {"strategy": "testid", "value": "password"}, 0.9),
        ]
        succeeded, failed = apply_patches(patches)
        assert succeeded == 2
        assert failed == 0

        content = Path(path).read_text()
        assert "get_by_test_id" in content

    def test_bottom_to_top_ordering_preserves_lines(self):
        """Verify that applying bottom-to-top doesn't break line numbers."""
        path = _write_tmp("""
            def test(page):
                page.locator("#a").click()
                page.locator("#b").click()
                page.locator("#c").click()
        """)
        sels = parse_file(path)
        patches = [generate_patch(s, {"strategy": "testid", "value": s.value}, 0.9) for s in sels]
        succeeded, _ = apply_patches(patches)
        assert succeeded == len(sels)


# ---------------------------------------------------------------------------
# 4. preview_patches (diff output)
# ---------------------------------------------------------------------------

class TestPreviewPatches:
    def test_diff_output_shows_changes(self):
        path = _write_tmp("""
            def test(page):
                page.locator("#login-email").fill("x@y.com")
        """)
        sels = parse_file(path)
        patches = [generate_patch(sels[0], {"strategy": "testid", "value": "login-email"}, 0.9)]
        diff = preview_patches(patches)
        assert "---" in diff
        assert "get_by_test_id" in diff

    def test_no_diff_for_empty_patches(self):
        diff = preview_patches([])
        assert diff == ""
