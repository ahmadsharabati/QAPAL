"""
Failure Parser for QAPAL Repair Engine

Extracts structured failure information from Playwright error messages,
stack traces, and test code. This is step 1 of the repair pipeline.

Supports:
- Selector not found (locator.click: timeout)
- Timeout waiting for element
- Strict mode violation (multiple matches)
- Detached element
- Navigation timeout
- Assertion failures (expect)
"""

import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


# ============================================================================
# Failure Types
# ============================================================================

class FailureType(Enum):
    """Categories of Playwright test failures."""
    SELECTOR_NOT_FOUND = "selector_not_found"
    TIMEOUT = "timeout"
    STRICT_MODE_VIOLATION = "strict_mode_violation"
    DETACHED_ELEMENT = "detached_element"
    NAVIGATION_TIMEOUT = "navigation_timeout"
    ASSERTION_FAILED = "assertion_failed"
    ELEMENT_NOT_VISIBLE = "element_not_visible"
    ELEMENT_DISABLED = "element_disabled"
    FRAME_DETACHED = "frame_detached"
    UNKNOWN = "unknown"


# ============================================================================
# Parsed Failure
# ============================================================================

@dataclass
class ParsedFailure:
    """
    Structured representation of a Playwright test failure.
    """
    failure_type: FailureType
    
    # Locator info
    locator_text: Optional[str] = None       # The selector that failed
    locator_method: Optional[str] = None     # getByRole, getByTestId, locator, etc.
    
    # Location
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    file_path: Optional[str] = None
    
    # Action context
    action_type: Optional[str] = None        # click, fill, check, etc.
    page_url: Optional[str] = None           # URL hint from error
    
    # Timing
    timeout_ms: Optional[int] = None
    
    # For strict mode violations
    match_count: Optional[int] = None        # How many elements matched
    
    # For assertion failures
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    
    # Raw data
    raw_error: str = ""
    raw_stack: str = ""
    
    # Confidence
    confidence: float = 0.0                  # How confident we are in the parse


# ============================================================================
# Regex patterns for Playwright errors
# ============================================================================

# Timeout waiting for locator
_RE_TIMEOUT_LOCATOR = re.compile(
    r'(?:locator\.(click|fill|check|uncheck|hover|press|type|selectOption|focus|blur|innerHTML|innerText|textContent|isVisible|isEnabled|isChecked|isDisabled|isEditable|isHidden|waitFor|scrollIntoViewIfNeeded|screenshot|evaluate|getAttribute|inputValue|setInputFiles|selectText|setChecked|tap|dblclick))'
    r'.*?(?:Timeout (\d+)ms exceeded)',
    re.DOTALL | re.IGNORECASE
)

# Locator text extraction (various patterns)
_RE_LOCATOR_GETBY = re.compile(
    r"(getBy(?:Role|TestId|Text|Label|Placeholder|AltText|Title))\s*\(\s*['\"]?([^'\")\n]+)['\"]?"
)

_RE_LOCATOR_CSS = re.compile(
    r"(?:locator|page\.locator)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
)

_RE_LOCATOR_GENERIC = re.compile(
    r"(?:Locator|locator)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
)

# Strict mode violation
_RE_STRICT_MODE = re.compile(
    r'strict mode violation.*?resolved to (\d+) elements',
    re.DOTALL | re.IGNORECASE
)

# Navigation timeout
_RE_NAV_TIMEOUT = re.compile(
    r'(?:page\.goto|page\.waitForURL|page\.waitForNavigation).*?Timeout (\d+)ms',
    re.DOTALL | re.IGNORECASE
)

# Element detached
_RE_DETACHED = re.compile(
    r'Element is (?:not attached|detached) (?:to|from) the DOM',
    re.IGNORECASE
)

# Element not visible
_RE_NOT_VISIBLE = re.compile(
    r'Element is not visible',
    re.IGNORECASE
)

# Element disabled
_RE_DISABLED = re.compile(
    r'Element is (?:not enabled|disabled)',
    re.IGNORECASE
)

# Frame detached
_RE_FRAME_DETACHED = re.compile(
    r'(?:Frame|frame) (?:was|is) detached',
    re.IGNORECASE
)

# Assertion failed (expect)
_RE_EXPECT_FAILED = re.compile(
    r'expect\(.*?\)\.(?:toHaveText|toHaveValue|toBeVisible|toBeHidden|toBeEnabled|toBeDisabled|toBeChecked|toHaveAttribute|toHaveClass|toHaveCount|toHaveCSS|toHaveId|toHaveURL|toHaveTitle|toContainText)\s*\(',
    re.DOTALL | re.IGNORECASE
)

_RE_EXPECT_VALUES = re.compile(
    r'Expected.*?:\s*["\']?(.+?)["\']?\s*\nReceived.*?:\s*["\']?(.+?)["\']?\s*$',
    re.MULTILINE | re.IGNORECASE
)

# Line number from stack trace
_RE_LINE_NUMBER = re.compile(
    r'(?:at\s+.*?|)\s*(?:[\w/\\.-]+\.(?:ts|js|mjs)):(\d+):(\d+)'
)

# File path from stack trace
_RE_FILE_PATH = re.compile(
    r'([\w/\\.-]+\.(?:ts|js|mjs)):(\d+):(\d+)'
)

# Timeout value
_RE_TIMEOUT_VALUE = re.compile(
    r'[Tt]imeout\s+(\d+)\s*ms'
)

# URL hint from error
_RE_URL_HINT = re.compile(
    r'(?:navigating to|waiting for URL|page\.goto)\s*["\']?(https?://[^\s"\']+)["\']?',
    re.IGNORECASE
)

# Action from error (e.g., "locator.click:" or "page.fill()")
_RE_ACTION_HINT = re.compile(
    r'(?:locator|page)\.(click|fill|check|uncheck|hover|press|type|selectOption|goto|waitForURL|waitForNavigation|focus|blur|dblclick|tap)',
    re.IGNORECASE
)


# ============================================================================
# Parser
# ============================================================================

class FailureParser:
    """
    Parses Playwright error messages into structured failure objects.
    Uses regex and string matching — no AI.
    """
    
    def parse(self, error_message: str, stack_trace: str = "",
              test_code: str = "") -> ParsedFailure:
        """
        Parse a Playwright failure into a structured object.
        
        Args:
            error_message: The error text from Playwright
            stack_trace: Optional stack trace
            test_code: Optional test source code
            
        Returns:
            ParsedFailure with extracted fields
        """
        combined = f"{error_message}\n{stack_trace}"
        
        failure = ParsedFailure(
            failure_type=FailureType.UNKNOWN,
            raw_error=error_message,
            raw_stack=stack_trace,
        )
        
        # Step 1: Classify failure type
        failure.failure_type = self._classify_failure(combined)
        
        # Step 2: Extract locator
        locator_method, locator_text = self._extract_locator(combined, test_code)
        failure.locator_method = locator_method
        failure.locator_text = locator_text
        
        # Step 3: Extract location (line, file)
        failure.line_number, failure.column_number, failure.file_path = \
            self._extract_location(combined)
        
        # Step 4: Extract action type
        failure.action_type = self._extract_action(combined)
        
        # Step 5: Extract timeout
        failure.timeout_ms = self._extract_timeout(combined)
        
        # Step 6: Extract URL hint
        failure.page_url = self._extract_url(combined)
        
        # Step 7: Extract strict mode match count
        if failure.failure_type == FailureType.STRICT_MODE_VIOLATION:
            failure.match_count = self._extract_match_count(combined)
        
        # Step 8: Extract assertion values
        if failure.failure_type == FailureType.ASSERTION_FAILED:
            failure.expected_value, failure.actual_value = \
                self._extract_assertion_values(combined)
        
        # Step 9: Calculate confidence
        failure.confidence = self._calculate_confidence(failure)
        
        return failure
    
    def _classify_failure(self, text: str) -> FailureType:
        """Classify the failure type from error text."""
        text_lower = text.lower()
        
        # Order matters: check most specific first
        if _RE_STRICT_MODE.search(text):
            return FailureType.STRICT_MODE_VIOLATION
        
        if _RE_DETACHED.search(text):
            return FailureType.DETACHED_ELEMENT
        
        if _RE_FRAME_DETACHED.search(text):
            return FailureType.FRAME_DETACHED
        
        if _RE_NOT_VISIBLE.search(text):
            return FailureType.ELEMENT_NOT_VISIBLE
        
        if _RE_DISABLED.search(text):
            return FailureType.ELEMENT_DISABLED
        
        if _RE_NAV_TIMEOUT.search(text):
            return FailureType.NAVIGATION_TIMEOUT
        
        if _RE_EXPECT_FAILED.search(text):
            return FailureType.ASSERTION_FAILED
        
        # Generic timeout (selector not found is a subset)
        if 'timeout' in text_lower and ('exceeded' in text_lower or 'waiting' in text_lower):
            # Check if it's specifically about a locator
            if any(kw in text_lower for kw in ['locator', 'getby', 'selector', 'waiting for']):
                return FailureType.SELECTOR_NOT_FOUND
            return FailureType.TIMEOUT
        
        if 'no element' in text_lower or 'not found' in text_lower:
            return FailureType.SELECTOR_NOT_FOUND
        
        return FailureType.UNKNOWN
    
    def _extract_locator(self, text: str, test_code: str = "") -> tuple:
        """Extract the failing locator from error text or test code."""
        # Try getBy* patterns first
        match = _RE_LOCATOR_GETBY.search(text)
        if match:
            return match.group(1), match.group(2).strip()
        
        # Try CSS locator
        match = _RE_LOCATOR_CSS.search(text)
        if match:
            return "locator", match.group(1).strip()
        
        # Try generic locator
        match = _RE_LOCATOR_GENERIC.search(text)
        if match:
            return "locator", match.group(1).strip()
        
        # Try from test code if line number known
        if test_code:
            match = _RE_LOCATOR_GETBY.search(test_code)
            if match:
                return match.group(1), match.group(2).strip()
            
            match = _RE_LOCATOR_CSS.search(test_code)
            if match:
                return "locator", match.group(1).strip()
        
        return None, None
    
    def _extract_location(self, text: str) -> tuple:
        """Extract file path, line number, column from stack trace."""
        match = _RE_FILE_PATH.search(text)
        if match:
            return int(match.group(2)), int(match.group(3)), match.group(1)
        
        # Try just line number
        match = _RE_LINE_NUMBER.search(text)
        if match:
            return int(match.group(1)), int(match.group(2)), None
        
        return None, None, None
    
    def _extract_action(self, text: str) -> Optional[str]:
        """Extract the action that failed."""
        match = _RE_ACTION_HINT.search(text)
        if match:
            return match.group(1).lower()
        return None
    
    def _extract_timeout(self, text: str) -> Optional[int]:
        """Extract timeout value in ms."""
        match = _RE_TIMEOUT_VALUE.search(text)
        if match:
            return int(match.group(1))
        return None
    
    def _extract_url(self, text: str) -> Optional[str]:
        """Extract URL hint from error."""
        match = _RE_URL_HINT.search(text)
        if match:
            return match.group(1)
        return None
    
    def _extract_match_count(self, text: str) -> Optional[int]:
        """Extract element count from strict mode violation."""
        match = _RE_STRICT_MODE.search(text)
        if match:
            return int(match.group(1))
        return None
    
    def _extract_assertion_values(self, text: str) -> tuple:
        """Extract expected/actual from assertion failure."""
        match = _RE_EXPECT_VALUES.search(text)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return None, None
    
    def _calculate_confidence(self, failure: ParsedFailure) -> float:
        """Calculate confidence score for the parse."""
        score = 0.0
        
        # Type classified (not unknown)
        if failure.failure_type != FailureType.UNKNOWN:
            score += 0.3
        
        # Locator extracted
        if failure.locator_text:
            score += 0.25
        
        # Action identified
        if failure.action_type:
            score += 0.15
        
        # Location found
        if failure.line_number:
            score += 0.15
        
        # Timeout extracted
        if failure.timeout_ms:
            score += 0.05
        
        # URL found
        if failure.page_url:
            score += 0.10
        
        return min(score, 1.0)
