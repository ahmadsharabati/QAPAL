"""
Validation Engine for QAPAL

The validation engine is the system's truth layer. It determines:
- Whether an action actually succeeded
- Whether a locator is valid (element found and interactable)
- Whether a test patch is correct (by running it)
- Whether AI suggestions are trustworthy

Core principle: Only outcomes that pass deterministic execution are trusted.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
import json

from engine.graph import (
    StateSnapshot,
    ValidationResult,
    GraphEdge,
    ActionType,
)


# ============================================================================
# Validation Rules
# ============================================================================

class ValidationRuleType(Enum):
    """Types of validation rules."""
    DOM_CHANGED = "dom_changed"
    URL_CHANGED = "url_changed"
    ERROR_APPEARED = "error_appeared"
    CONSOLE_ERROR = "console_error"
    NETWORK_ERROR = "network_error"
    NO_OP = "no_op"
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_DISABLED = "element_disabled"
    FORM_NOT_SUBMITTED = "form_not_submitted"
    NAVIGATION_FAILED = "navigation_failed"


# ============================================================================
# Validation Issue
# ============================================================================

@dataclass
class ValidationIssue:
    """
    A single validation problem detected during action execution.
    """
    rule: ValidationRuleType
    severity: str                  # 'critical' | 'high' | 'medium' | 'low'
    message: str
    
    # Context
    page: str
    action: str                    # 'click', 'type', 'submit', etc.
    element: Optional[str] = None  # Selector or element description
    
    # Evidence
    before_snapshot: Optional[StateSnapshot] = None
    after_snapshot: Optional[StateSnapshot] = None
    screenshot_path: Optional[str] = None
    
    # Additional details
    details: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Validation Context
# ============================================================================

@dataclass
class ValidationContext:
    """
    Context for validating an action.
    Contains before/after state and execution details.
    """
    # States
    before_state: StateSnapshot
    after_state: StateSnapshot
    
    # Execution details
    action_type: ActionType
    target_element: Optional[str] = None
    action_value: Optional[str] = None
    
    # Execution metrics
    duration_ms: int = 0
    error_message: Optional[str] = None
    exception: Optional[Exception] = None
    
    # Execution logs
    console_output: List[str] = field(default_factory=list)
    network_log: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================================
# Validation Engine
# ============================================================================

class ValidationEngine:
    """
    Deterministic validation of user actions and test outcomes.
    """
    
    # URL patterns that indicate navigation happened
    NAVIGATION_INDICATORS = {
        'form_submit': ['/submit', '/api/', '/login', '/register', '/checkout'],
        'link_click': ['.com/', '.org/', '/'],
    }
    
    # Error keywords to detect failures
    ERROR_KEYWORDS = [
        'error', 'failed', 'failed to', 'cannot', 'invalid', 'unauthorized',
        'not found', 'does not exist', 'something went wrong', 'try again',
        'exception', 'fatal', 'critical', 'alert', 'warning',
    ]
    
    def __init__(self):
        self.issues: List[ValidationIssue] = []
    
    def validate(self, context: ValidationContext) -> Tuple[ValidationResult, List[ValidationIssue]]:
        """
        Validate whether an action succeeded or failed.
        
        Returns:
            (ValidationResult, list of ValidationIssues)
        """
        self.issues = []
        
        # Run all validation rules
        self._check_dom_changed(context)
        self._check_url_changed(context)
        self._check_errors(context)
        self._check_console_errors(context)
        self._check_network_errors(context)
        self._check_no_op(context)
        self._check_element_status(context)
        
        # Classify outcome
        result = self._classify_outcome()
        
        return result, self.issues
    
    def _check_dom_changed(self, ctx: ValidationContext) -> None:
        """Check if DOM changed after action (expected for most interactions)."""
        if ctx.before_state.dom_hash == ctx.after_state.dom_hash:
            # DOM didn't change at all
            # This might be OK for certain actions (hover), but usually signals failure
            if ctx.action_type not in [ActionType.WAIT, ActionType.HOVER]:
                self.issues.append(ValidationIssue(
                    rule=ValidationRuleType.NO_OP,
                    severity='high',
                    message=f"DOM unchanged after {ctx.action_type.value}",
                    page=ctx.after_state.url,
                    action=ctx.action_type.value,
                    before_snapshot=ctx.before_state,
                    after_snapshot=ctx.after_state,
                    details={
                        'dom_hash_before': ctx.before_state.dom_hash,
                        'dom_hash_after': ctx.after_state.dom_hash,
                    }
                ))
    
    def _check_url_changed(self, ctx: ValidationContext) -> None:
        """Check if URL changed when it should (navigation actions)."""
        if ctx.action_type == ActionType.NAVIGATE:
            if ctx.before_state.url == ctx.after_state.url:
                self.issues.append(ValidationIssue(
                    rule=ValidationRuleType.NAVIGATION_FAILED,
                    severity='critical',
                    message="Navigation action did not change URL",
                    page=ctx.before_state.url,
                    action='navigate',
                    before_snapshot=ctx.before_state,
                    after_snapshot=ctx.after_state,
                    details={
                        'expected_url': ctx.action_value,
                        'actual_url': ctx.after_state.url,
                    }
                ))
        
        # Check for unexpected navigation
        elif ctx.action_type in [ActionType.CLICK, ActionType.SUBMIT]:
            if ctx.before_state.url != ctx.after_state.url:
                # URL changed, this is usually good
                pass
    
    def _check_errors(self, ctx: ValidationContext) -> None:
        """Check if error messages appeared after action."""
        before_errors = set(ctx.before_state.error_messages)
        after_errors = set(ctx.after_state.error_messages)
        
        new_errors = after_errors - before_errors
        
        if new_errors:
            self.issues.append(ValidationIssue(
                rule=ValidationRuleType.ERROR_APPEARED,
                severity='high',
                message=f"Error messages appeared: {', '.join(list(new_errors)[:3])}",
                page=ctx.after_state.url,
                action=ctx.action_type.value,
                before_snapshot=ctx.before_state,
                after_snapshot=ctx.after_state,
                details={'errors': list(new_errors)}
            ))
    
    def _check_console_errors(self, ctx: ValidationContext) -> None:
        """Check if JS errors appeared in console."""
        before_errors = set(ctx.before_state.console_errors)
        after_errors = set(ctx.after_state.console_errors)
        
        new_errors = after_errors - before_errors
        
        if new_errors:
            self.issues.append(ValidationIssue(
                rule=ValidationRuleType.CONSOLE_ERROR,
                severity='medium',
                message=f"Console errors detected: {', '.join(list(new_errors)[:3])}",
                page=ctx.after_state.url,
                action=ctx.action_type.value,
                before_snapshot=ctx.before_state,
                after_snapshot=ctx.after_state,
                details={'errors': list(new_errors)}
            ))
    
    def _check_network_errors(self, ctx: ValidationContext) -> None:
        """Check if network requests failed."""
        before_errors = set(ctx.before_state.network_errors)
        after_errors = set(ctx.after_state.network_errors)
        
        new_errors = after_errors - before_errors
        
        if new_errors:
            self.issues.append(ValidationIssue(
                rule=ValidationRuleType.NETWORK_ERROR,
                severity='high',
                message=f"Network errors occurred: {', '.join(list(new_errors)[:3])}",
                page=ctx.after_state.url,
                action=ctx.action_type.value,
                before_snapshot=ctx.before_state,
                after_snapshot=ctx.after_state,
                details={'errors': list(new_errors)}
            ))
    
    def _check_no_op(self, ctx: ValidationContext) -> None:
        """Detect no-op actions (action fired but did nothing)."""
        # No-op = no DOM change + no URL change + no errors + no network
        if (ctx.before_state.dom_hash == ctx.after_state.dom_hash and
            ctx.before_state.url == ctx.after_state.url and
            not (set(ctx.after_state.error_messages) - set(ctx.before_state.error_messages)) and
            not (set(ctx.after_state.console_errors) - set(ctx.before_state.console_errors))):
            
            # This is a true no-op
            if not any(i.rule == ValidationRuleType.NO_OP for i in self.issues):
                self.issues.append(ValidationIssue(
                    rule=ValidationRuleType.NO_OP,
                    severity='medium',
                    message="Action had no observable effect (no-op)",
                    page=ctx.after_state.url,
                    action=ctx.action_type.value,
                    element=ctx.target_element,
                    details={
                        'action_value': ctx.action_value,
                    }
                ))
    
    def _check_element_status(self, ctx: ValidationContext) -> None:
        """Check if element was in wrong state (disabled, hidden, etc.)."""
        # This would be populated from Playwright's isDisabled(), isHidden(), etc.
        # For now, a placeholder
        pass
    
    def _classify_outcome(self) -> ValidationResult:
        """
        Classify the overall outcome based on issues found.
        
        Classification logic:
        - PASS: No critical issues
        - FAIL: Any critical issue
        - NO_OP: No-op issue but no other critical issues
        - PARTIAL: Some issues but action partially succeeded
        - UNKNOWN: Inconclusive
        """
        critical_issues = [i for i in self.issues if i.severity == 'critical']
        if critical_issues:
            return ValidationResult.FAIL
        
        no_op_issues = [i for i in self.issues if i.rule == ValidationRuleType.NO_OP]
        if no_op_issues:
            return ValidationResult.NO_OP
        
        high_issues = [i for i in self.issues if i.severity == 'high']
        if high_issues:
            # Probably failed but not completely sure
            return ValidationResult.PARTIAL
        
        if not self.issues:
            return ValidationResult.PASS
        
        return ValidationResult.UNKNOWN
    
    def validate_locator(self, locator: str, found: bool, 
                        is_visible: bool, is_enabled: bool) -> bool:
        """
        Validate whether a locator is trustworthy.
        
        A locator is valid if:
        1. Element exists (found=True)
        2. Element is visible
        3. Element is enabled/interactive
        """
        if not found:
            return False
        if not is_visible:
            return False
        if not is_enabled:
            return False
        return True
    
    def validate_patch(self, edge: GraphEdge, new_locator: str) -> bool:
        """
        Validate whether a patched locator repair is correct.
        (In real system, this would run the Playwright test)
        
        Returns True if patch passes, False otherwise.
        """
        # Placeholder for actual Playwright test execution
        # In practice: run the test with new_locator, check if it passes
        pass


# ============================================================================
# Validation Report
# ============================================================================

@dataclass
class ValidationReport:
    """
    Summary of validation results for an action or test.
    """
    result: ValidationResult
    issues: List[ValidationIssue]
    passed_rules: List[str] = field(default_factory=list)
    failed_rules: List[str] = field(default_factory=list)
    
    def summary(self) -> str:
        """Human-readable summary."""
        if self.result == ValidationResult.PASS:
            return "✓ Action succeeded"
        elif self.result == ValidationResult.FAIL:
            return f"✗ Action failed: {self.issues[0].message if self.issues else 'Unknown'}"
        elif self.result == ValidationResult.NO_OP:
            return "⚠ Action had no effect"
        elif self.result == ValidationResult.PARTIAL:
            return f"⚠ Action partially succeeded: {self.issues[0].message if self.issues else 'Unknown'}"
        else:
            return "? Validation inconclusive"
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage."""
        return {
            'result': self.result.value,
            'issue_count': len(self.issues),
            'issues': [
                {
                    'rule': i.rule.value,
                    'severity': i.severity,
                    'message': i.message,
                    'page': i.page,
                    'action': i.action,
                    'element': i.element,
                } for i in self.issues
            ],
            'passed_rules': self.passed_rules,
            'failed_rules': self.failed_rules,
        }


if __name__ == "__main__":
    # Quick test
    before = StateSnapshot(
        url="https://example.com",
        title="Before",
        dom_hash="hash1",
        a11y_hash="a11y1",
        visible_text_hash="text1",
        visible_text="Before state",
        error_messages=[],
        console_errors=[],
        pending_requests=0,
        network_errors=[],
    )
    
    after = StateSnapshot(
        url="https://example.com/after",
        title="After",
        dom_hash="hash2",
        a11y_hash="a11y2",
        visible_text_hash="text2",
        visible_text="After state",
        error_messages=[],
        console_errors=[],
        pending_requests=0,
        network_errors=[],
    )
    
    ctx = ValidationContext(
        before_state=before,
        after_state=after,
        action_type=ActionType.CLICK,
        target_element="button.submit",
    )
    
    engine = ValidationEngine()
    result, issues = engine.validate(ctx)
    
    print(f"Result: {result}")
    print(f"Issues: {len(issues)}")
    for issue in issues:
        print(f"  - {issue.rule.value}: {issue.message}")
