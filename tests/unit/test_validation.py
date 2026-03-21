"""
Unit tests for Validation Engine

Tests:
- DOM change detection
- URL change detection
- Error appearance detection
- Console error tracking
- Network error tracking
- No-op detection
- Outcome classification
"""

import pytest
from engine import (
    ValidationEngine,
    ValidationContext,
    ValidationRuleType,
    ValidationResult,
    StateSnapshot,
    ActionType,
)


class TestDOMChangeDetection:
    """Test DOM change validation."""
    
    def test_dom_changed_passes(self):
        """DOM changed after click — should pass."""
        before = StateSnapshot(
            url="https://example.com",
            title="Before",
            dom_hash="hash_before",
            a11y_hash="a11y_before",
            visible_text_hash="text_before",
            visible_text="Before",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="After",
            dom_hash="hash_after",  # Changed
            a11y_hash="a11y_after",
            visible_text_hash="text_after",
            visible_text="After",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.CLICK,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should pass (no DOM-change issues)
        assert not any(i.rule == ValidationRuleType.NO_OP for i in issues)
    
    def test_dom_unchanged_fails(self):
        """DOM unchanged after click — should fail as no-op."""
        before = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="same_hash",
            a11y_hash="same_a11y",
            visible_text_hash="same_text",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="same_hash",  # Unchanged
            a11y_hash="same_a11y",
            visible_text_hash="same_text",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.CLICK,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should detect no-op
        assert any(i.rule == ValidationRuleType.NO_OP for i in issues)


class TestURLChangeDetection:
    """Test URL change validation."""
    
    def test_navigate_changes_url(self):
        """NAVIGATE action that actually changes URL — pass."""
        before = StateSnapshot(
            url="https://example.com/page1",
            title="Page 1",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Page 1",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com/page2",  # URL changed
            title="Page 2",
            dom_hash="h2",
            a11y_hash="a2",
            visible_text_hash="t2",
            visible_text="Page 2",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.NAVIGATE,
            action_value="https://example.com/page2",
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should not have navigation_failed
        assert not any(i.rule == ValidationRuleType.NAVIGATION_FAILED for i in issues)
    
    def test_navigate_same_url_fails(self):
        """NAVIGATE action that doesn't change URL — fail."""
        before = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h",
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",  # URL didn't change
            title="Page",
            dom_hash="h",
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.NAVIGATE,
            action_value="https://example.com/expected",
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should have navigation_failed
        assert any(i.rule == ValidationRuleType.NAVIGATION_FAILED for i in issues)
        assert result == ValidationResult.FAIL


class TestErrorDetection:
    """Test error message detection."""
    
    def test_error_appears_after_action(self):
        """Error message appears after action — fail."""
        before = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Form",
            error_messages=[],  # No errors before
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h2",  # DOM changed (error message added)
            a11y_hash="a2",
            visible_text_hash="t2",
            visible_text="Form Error: Invalid email",
            error_messages=["Invalid email"],  # Error appeared
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.SUBMIT,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should detect error
        assert any(i.rule == ValidationRuleType.ERROR_APPEARED for i in issues)
        # Error appearance is high severity, not critical, so result might be PARTIAL
        assert result in [ValidationResult.PARTIAL, ValidationResult.FAIL]


class TestConsoleErrorDetection:
    """Test console error detection."""
    
    def test_console_error_after_action(self):
        """JS error appears in console after action."""
        before = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h",
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h",
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=["Cannot read property 'x' of undefined"],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.CLICK,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should detect console error
        assert any(i.rule == ValidationRuleType.CONSOLE_ERROR for i in issues)


class TestNetworkErrorDetection:
    """Test network error detection."""
    
    def test_network_failure_after_submit(self):
        """Network error appears after form submit."""
        before = StateSnapshot(
            url="https://example.com",
            title="Form",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Form",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="Form",
            dom_hash="h2",  # DOM changed (error state)
            a11y_hash="a2",
            visible_text_hash="t2",
            visible_text="Form — Error",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=["POST /api/submit 500"],
        )

        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.SUBMIT,
        )

        engine = ValidationEngine()
        result, issues = engine.validate(ctx)

        # Should detect network error
        assert any(i.rule == ValidationRuleType.NETWORK_ERROR for i in issues)
        assert result == ValidationResult.PARTIAL  # Network errors are high severity


class TestNoOpDetection:
    """Test no-op action detection."""
    
    def test_true_no_op(self):
        """Completely harmless no-op (nothing changed)."""
        before = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h",
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="h",  # Nothing changed
            a11y_hash="a",
            visible_text_hash="t",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.CLICK,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        # Should be classified as NO_OP
        assert result == ValidationResult.NO_OP


class TestOutcomeClassification:
    """Test outcome classification logic."""
    
    def test_pass_no_issues(self):
        """No issues at all — PASS."""
        before = StateSnapshot(
            url="https://example.com",
            title="Before",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Before",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com",
            title="After",
            dom_hash="h2",  # Changed
            a11y_hash="a2",
            visible_text_hash="t2",
            visible_text="After",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.CLICK,
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        assert result == ValidationResult.PASS
        assert len(issues) == 0
    
    def test_fail_critical_issue(self):
        """Critical issue → FAIL."""
        before = StateSnapshot(
            url="https://example.com/page1",
            title="Page 1",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Page 1",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        after = StateSnapshot(
            url="https://example.com/page1",  # Didn't navigate
            title="Page 1",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Page 1",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        ctx = ValidationContext(
            before_state=before,
            after_state=after,
            action_type=ActionType.NAVIGATE,
            action_value="https://example.com/page2",
        )
        
        engine = ValidationEngine()
        result, issues = engine.validate(ctx)
        
        assert result == ValidationResult.FAIL
        assert any(i.severity == 'critical' for i in issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
