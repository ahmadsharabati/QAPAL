"""
Unit tests for Failure Parser
"""

import pytest
from engine.repair.failure_parser import FailureParser, FailureType


@pytest.fixture
def parser():
    return FailureParser()


class TestTimeoutParsing:
    def test_locator_click_timeout(self, parser):
        error = "locator.click: Timeout 30000ms exceeded.\n  waiting for getByRole('button', { name: 'Submit' })"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.SELECTOR_NOT_FOUND
        assert result.timeout_ms == 30000
        assert result.action_type == "click"
    
    def test_locator_fill_timeout(self, parser):
        error = "locator.fill: Timeout 5000ms exceeded.\n  waiting for getByTestId('email-input')"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.SELECTOR_NOT_FOUND
        assert result.timeout_ms == 5000


class TestSelectorNotFound:
    def test_getbyrole_not_found(self, parser):
        error = "locator.click: Timeout 30000ms exceeded.\n  waiting for getByRole('button', { name: 'Login' })"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.SELECTOR_NOT_FOUND
        assert result.locator_method == "getByRole"
        assert "Login" in (result.locator_text or "")
    
    def test_getbytestid_not_found(self, parser):
        error = "locator.click: Timeout 30000ms exceeded.\n  waiting for getByTestId('submit-btn')"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.SELECTOR_NOT_FOUND
        assert result.locator_method == "getByTestId"
        assert result.locator_text == "submit-btn"
    
    def test_css_locator_not_found(self, parser):
        error = "locator.click: Timeout 30000ms exceeded.\n  waiting for locator('button.primary-action')"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.SELECTOR_NOT_FOUND
        assert result.locator_text == "button.primary-action"


class TestStrictModeViolation:
    def test_strict_mode_multiple_elements(self, parser):
        error = "strict mode violation: getByRole('button') resolved to 5 elements"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.STRICT_MODE_VIOLATION
        assert result.match_count == 5


class TestDetachedElement:
    def test_element_detached(self, parser):
        error = "Element is not attached to the DOM"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.DETACHED_ELEMENT
    
    def test_element_detached_variant(self, parser):
        error = "Element is detached from the DOM"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.DETACHED_ELEMENT


class TestNavigationTimeout:
    def test_goto_timeout(self, parser):
        error = "page.goto: Timeout 30000ms exceeded. navigating to 'https://example.com/dashboard'"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.NAVIGATION_TIMEOUT
        assert result.timeout_ms == 30000
        assert result.page_url == "https://example.com/dashboard"


class TestElementNotVisible:
    def test_not_visible(self, parser):
        error = "Element is not visible"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.ELEMENT_NOT_VISIBLE


class TestElementDisabled:
    def test_disabled(self, parser):
        error = "Element is not enabled"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.ELEMENT_DISABLED


class TestLocationExtraction:
    def test_line_number_from_stack(self, parser):
        error = "locator.click: Timeout 30000ms exceeded."
        stack = "at tests/login.spec.ts:15:5\n  at processTicksAndRejections"
        result = parser.parse(error, stack)
        
        assert result.line_number == 15
        assert result.column_number == 5
        assert "login.spec.ts" in (result.file_path or "")


class TestConfidence:
    def test_high_confidence_full_info(self, parser):
        error = "locator.click: Timeout 30000ms exceeded.\n  waiting for getByRole('button', { name: 'Submit' })"
        stack = "at tests/checkout.spec.ts:42:10"
        result = parser.parse(error, stack)
        
        assert result.confidence >= 0.7
    
    def test_low_confidence_unknown(self, parser):
        error = "Something went wrong"
        result = parser.parse(error)
        
        assert result.failure_type == FailureType.UNKNOWN
        assert result.confidence < 0.3


class TestMalformedErrors:
    def test_empty_error(self, parser):
        result = parser.parse("")
        assert result.failure_type == FailureType.UNKNOWN
    
    def test_garbage_error(self, parser):
        result = parser.parse("asdf1234!@#$")
        assert result.failure_type == FailureType.UNKNOWN
        assert result.confidence == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
