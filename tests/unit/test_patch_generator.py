"""
Unit tests for Patch Generator
"""

import pytest
from engine.graph import LocatorCandidate, LocatorStrategy, InteractiveElement
from engine.repair.patch_generator import PatchGenerator
from engine.repair.locator_matcher import LocatorMatch
from engine.repair.failure_parser import ParsedFailure, FailureType


SAMPLE_TEST = """import { test, expect } from '@playwright/test';

test('user can log in', async ({ page }) => {
  await page.goto('https://example.com/login');
  await page.getByRole('textbox', { name: 'Email' }).fill('user@example.com');
  await page.getByRole('textbox', { name: 'Password' }).fill('password123');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(page).toHaveURL('https://example.com/dashboard');
});
"""


def _make_match(strategy, value, name, pw_expr, score=0.9):
    loc = LocatorCandidate(
        strategy=strategy, value=value,
        confidence=0.95, uniqueness=1.0, visibility=1.0,
    )
    elem = InteractiveElement(
        element_id="e1", tag="button", accessible_name=name,
        locators=[loc], role="button",
    )
    return LocatorMatch(
        candidate=loc, element=elem,
        match_reason="same_role", match_score=score,
        playwright_expression=pw_expr,
    )


@pytest.fixture
def generator():
    return PatchGenerator()


class TestLocatorReplacement:
    def test_replace_broken_button(self, generator):
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="button', { name: 'Login' }",
            locator_method="getByRole",
            line_number=7,
        )
        
        candidates = [
            _make_match(
                LocatorStrategy.TESTID, "login-submit", "Log in",
                "page.getByTestId('login-submit')",
            )
        ]
        
        result = generator.generate(SAMPLE_TEST, failure, candidates)
        
        assert result.success
        assert result.strategy == "locator_replace"
        assert "getByTestId('login-submit')" in result.patched_code
        assert result.lines_changed >= 1
    
    def test_no_candidates_returns_failure(self, generator):
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="button.missing",
        )
        
        result = generator.generate(SAMPLE_TEST, failure, [])
        
        assert not result.success
        assert result.strategy == "no_candidates"


class TestMinimalDiff:
    def test_only_one_line_changed(self, generator):
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="button', { name: 'Login' }",
            locator_method="getByRole",
            line_number=7,
        )
        
        candidates = [
            _make_match(
                LocatorStrategy.ROLE, "button: Log in", "Log in",
                "page.getByRole('button', { name: 'Log in' })",
            )
        ]
        
        result = generator.generate(SAMPLE_TEST, failure, candidates)
        
        assert result.success
        assert result.lines_changed == 1
        
        # Test name should be preserved
        assert "user can log in" in result.patched_code
        # Other lines should be unchanged
        assert "fill('user@example.com')" in result.patched_code
        assert "fill('password123')" in result.patched_code


class TestDiffGeneration:
    def test_diff_is_generated(self, generator):
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="button', { name: 'Login' }",
            locator_method="getByRole",
        )
        
        candidates = [
            _make_match(
                LocatorStrategy.TESTID, "login-btn", "Log in",
                "page.getByTestId('login-btn')",
            )
        ]
        
        result = generator.generate(SAMPLE_TEST, failure, candidates)
        
        assert result.success
        assert result.diff  # Diff should not be empty
        assert "---" in result.diff  # Unified diff format
        assert "+++" in result.diff


class TestWaitInsertion:
    def test_add_wait_for_timeout(self, generator):
        failure = ParsedFailure(
            failure_type=FailureType.TIMEOUT,
            locator_text="button', { name: 'Login' }",
            locator_method="getByRole",
            timeout_ms=30000,
        )
        
        candidates = [
            _make_match(
                LocatorStrategy.TESTID, "login-submit", "Log in",
                "page.getByTestId('login-submit')",
            )
        ]
        
        result = generator.generate(SAMPLE_TEST, failure, candidates)
        
        assert result.success
        # Should either replace locator or add wait
        assert result.strategy in ("locator_replace", "add_wait")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
