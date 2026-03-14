"""
assertions.py — QAPal Assertion Definitions
============================================
Defines all assertion types the planner can specify and the executor can verify.

This module is the contract between:
  - Planner: Outputs assertions in this format
  - Executor: Validates and runs assertions in this format

Each assertion has:
  - type:         Assertion type identifier (e.g., "url_contains", "element_visible")
  - description:  Human-readable description
  - category:     Grouping for documentation (url, page, element, content, state)
  - needs_target: Whether the assertion needs an element selector
  - params:       Parameter definitions with types and requirements
  - examples:     Example assertion objects for documentation

Assertion Result Shape:
  {
    "type": "element_visible",
    "status": "pass" | "fail",
    "actual": "what was found",
    "expected": "what was expected",
    "selector": {...},
    "reason": "explanation if failed"
  }

Usage:
  from assertions import ASSERTIONS, validate_assertion, get_assertion
  
  # Get assertion definition
  assertion_def = get_assertion("element_visible")
  
  # Validate an assertion from a plan
  is_valid, errors = validate_assertion({"type": "url_contains", "value": "/dashboard"})
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union, Tuple
from enum import Enum
import json


# ── Assertion Types ───────────────────────────────────────────────────

class AssertionType(Enum):
    """All supported assertion types."""
    # URL assertions
    URL_EQUALS   = "url_equals"
    URL_CONTAINS = "url_contains"
    URL_MATCHES  = "url_matches"
    
    # Page assertions
    TITLE_EQUALS   = "title_equals"
    TITLE_CONTAINS = "title_contains"
    
    # Element existence
    ELEMENT_EXISTS     = "element_exists"
    ELEMENT_NOT_EXISTS = "element_not_exists"
    
    # Element visibility
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_HIDDEN  = "element_hidden"
    
    # Element state
    ELEMENT_ENABLED   = "element_enabled"
    ELEMENT_DISABLED  = "element_disabled"
    ELEMENT_CHECKED   = "element_checked"
    ELEMENT_UNCHECKED = "element_unchecked"
    ELEMENT_FOCUSED   = "element_focused"
    ELEMENT_EDITABLE  = "element_editable"
    ELEMENT_READONLY  = "element_readonly"
    
    # Element content
    ELEMENT_TEXT_EQUALS   = "element_text_equals"
    ELEMENT_TEXT_CONTAINS = "element_text_contains"
    ELEMENT_TEXT_MATCHES  = "element_text_matches"
    ELEMENT_VALUE_EQUALS  = "element_value_equals"
    ELEMENT_VALUE_CONTAINS = "element_value_contains"
    
    # Element attributes
    ELEMENT_ATTRIBUTE  = "element_attribute"
    ELEMENT_HAS_CLASS  = "element_has_class"
    ELEMENT_HAS_STYLE  = "element_has_style"
    
    # Element count
    ELEMENT_COUNT = "element_count"
    
    # Element position
    ELEMENT_IN_VIEWPORT = "element_in_viewport"
    
    # Custom
    JAVASCRIPT = "javascript"


class AssertionCategory(Enum):
    """Assertion categories for documentation and UI grouping."""
    URL      = "url"
    PAGE     = "page"
    EXISTENCE = "existence"
    VISIBILITY = "visibility"
    STATE    = "state"
    CONTENT  = "content"
    ATTRIBUTE = "attribute"
    POSITION = "position"
    CUSTOM   = "custom"


class ParamType(Enum):
    """Parameter data types."""
    STRING   = "string"
    NUMBER   = "number"
    BOOLEAN  = "boolean"
    OBJECT   = "object"
    ARRAY    = "array"
    SELECTOR = "selector"
    REGEX    = "regex"


# ── Parameter Definition ─────────────────────────────────────────────

@dataclass
class AssertionParam:
    """
    Definition of an assertion parameter.
    
    Attributes:
        name: Parameter name (used as key in assertion dict)
        type: Expected data type
        required: Whether this parameter must be present
        description: Human-readable description
        default: Default value if not provided
        enum: List of allowed values (for string types)
        min_value: Minimum value (for number types)
        max_value: Maximum value (for number types)
    """
    name: str
    type: ParamType
    required: bool
    description: str
    default: Optional[Any] = None
    enum: Optional[List[str]] = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "description": self.description,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.enum:
            result["enum"] = self.enum
        if self.min_value is not None:
            result["min_value"] = self.min_value
        if self.max_value is not None:
            result["max_value"] = self.max_value
        return result


# ── Assertion Definition ──────────────────────────────────────────────

@dataclass
class AssertionDefinition:
    """
    Complete definition of an executable assertion.
    
    Attributes:
        type: Assertion identifier (matches AssertionType value)
        description: Human-readable description
        category: Assertion grouping
        needs_target: Whether assertion needs an element selector
        params: List of parameter definitions
        examples: Example assertion objects
        returns: Description of what the assertion returns
    """
    type: str
    description: str
    category: AssertionCategory
    needs_target: bool
    params: List[AssertionParam] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    returns: str = "boolean"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "type": self.type,
            "description": self.description,
            "category": self.category.value,
            "needs_target": self.needs_target,
            "params": [p.to_dict() for p in self.params],
            "examples": self.examples,
            "returns": self.returns,
        }


# ── Reusable Parameters ───────────────────────────────────────────────

SELECTOR_PARAM = AssertionParam(
    name="selector",
    type=ParamType.SELECTOR,
    required=True,
    description="Element selector object with 'strategy' and 'value' keys",
)

ELEMENT_ID_PARAM = AssertionParam(
    name="element_id",
    type=ParamType.STRING,
    required=False,
    description="Reference ID to the element in the locator database",
)

TIMEOUT_PARAM = AssertionParam(
    name="timeout",
    type=ParamType.NUMBER,
    required=False,
    description="Maximum wait time in milliseconds",
    default=5000,
    min_value=0,
    max_value=60000,
)

VALUE_PARAM = AssertionParam(
    name="value",
    type=ParamType.STRING,
    required=True,
    description="Expected value to compare against",
)

MESSAGE_PARAM = AssertionParam(
    name="message",
    type=ParamType.STRING,
    required=False,
    description="Custom error message on failure",
)


# ── Assertion Definitions Registry ─────────────────────────────────────

ASSERTIONS: Dict[str, AssertionDefinition] = {

    # ═════════════════════════════════════════════════════════════════
    # URL ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.URL_EQUALS.value: AssertionDefinition(
        type="url_equals",
        description="Assert the current URL exactly matches a value",
        category=AssertionCategory.URL,
        needs_target=False,
        params=[
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected URL (absolute or relative)",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {"type": "url_equals", "value": "https://app.com/dashboard"},
            {"type": "url_equals", "value": "/login"},
        ],
        returns="actual_url: string",
    ),

    AssertionType.URL_CONTAINS.value: AssertionDefinition(
        type="url_contains",
        description="Assert the current URL contains a substring",
        category=AssertionCategory.URL,
        needs_target=False,
        params=[
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Substring to check for in URL",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {"type": "url_contains", "value": "/checkout"},
            {"type": "url_contains", "value": "session_id="},
            {"type": "url_contains", "value": "dashboard"},
        ],
        returns="actual_url: string",
    ),

    AssertionType.URL_MATCHES.value: AssertionDefinition(
        type="url_matches",
        description="Assert the current URL matches a regex pattern",
        category=AssertionCategory.URL,
        needs_target=False,
        params=[
            AssertionParam(
                name="pattern",
                type=ParamType.REGEX,
                required=True,
                description="Regex pattern to match against URL",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {"type": "url_matches", "pattern": r"^https://app\\.com/user/\\d+$"},
            {"type": "url_matches", "pattern": r"/product/[a-z0-9-]+"},
        ],
        returns="actual_url: string, matched: boolean",
    ),

    # ═════════════════════════════════════════════════════════════════
    # PAGE ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.TITLE_EQUALS.value: AssertionDefinition(
        type="title_equals",
        description="Assert the page title exactly matches a value",
        category=AssertionCategory.PAGE,
        needs_target=False,
        params=[
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected page title",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {"type": "title_equals", "value": "Dashboard - MyApp"},
            {"type": "title_equals", "value": "Login"},
        ],
        returns="actual_title: string",
    ),

    AssertionType.TITLE_CONTAINS.value: AssertionDefinition(
        type="title_contains",
        description="Assert the page title contains a substring",
        category=AssertionCategory.PAGE,
        needs_target=False,
        params=[
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Substring to check for in title",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {"type": "title_contains", "value": "Checkout"},
            {"type": "title_contains", "value": "Error"},
        ],
        returns="actual_title: string",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT EXISTENCE ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_EXISTS.value: AssertionDefinition(
        type="element_exists",
        description="Assert an element exists in the DOM",
        category=AssertionCategory.EXISTENCE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            TIMEOUT_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_exists",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
            },
            {
                "type": "element_exists",
                "selector": {"strategy": "testid", "value": "error-message"},
                "timeout": 10000,
            },
        ],
        returns="count: number",
    ),

    AssertionType.ELEMENT_NOT_EXISTS.value: AssertionDefinition(
        type="element_not_exists",
        description="Assert an element does not exist in the DOM",
        category=AssertionCategory.EXISTENCE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            TIMEOUT_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_not_exists",
                "selector": {"strategy": "role", "value": {"role": "alert"}},
            },
            {
                "type": "element_not_exists",
                "selector": {"strategy": "testid", "value": "loading-spinner"},
            },
        ],
        returns="count: number",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT VISIBILITY ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_VISIBLE.value: AssertionDefinition(
        type="element_visible",
        description="Assert an element is visible (not hidden, not display:none, has size)",
        category=AssertionCategory.VISIBILITY,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            TIMEOUT_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_visible",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Confirm"}},
            },
            {
                "type": "element_visible",
                "selector": {"strategy": "testid", "value": "success-message"},
                "timeout": 10000,
            },
        ],
        returns="visible: boolean",
    ),

    AssertionType.ELEMENT_HIDDEN.value: AssertionDefinition(
        type="element_hidden",
        description="Assert an element is hidden or not in DOM",
        category=AssertionCategory.VISIBILITY,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            TIMEOUT_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_hidden",
                "selector": {"strategy": "testid", "value": "loading-spinner"},
            },
            {
                "type": "element_hidden",
                "selector": {"strategy": "role", "value": {"role": "dialog"}},
            },
        ],
        returns="hidden: boolean",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT STATE ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_ENABLED.value: AssertionDefinition(
        type="element_enabled",
        description="Assert an element is enabled (not disabled)",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_enabled",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
            },
            {
                "type": "element_enabled",
                "selector": {"strategy": "testid", "value": "checkout-btn"},
            },
        ],
        returns="enabled: boolean",
    ),

    AssertionType.ELEMENT_DISABLED.value: AssertionDefinition(
        type="element_disabled",
        description="Assert an element is disabled",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_disabled",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
            },
        ],
        returns="disabled: boolean",
    ),

    AssertionType.ELEMENT_CHECKED.value: AssertionDefinition(
        type="element_checked",
        description="Assert a checkbox or radio button is checked",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_checked",
                "selector": {"strategy": "role", "value": {"role": "checkbox", "name": "Terms"}},
            },
            {
                "type": "element_checked",
                "selector": {"strategy": "testid", "value": "remember-me"},
            },
        ],
        returns="checked: boolean",
    ),

    AssertionType.ELEMENT_UNCHECKED.value: AssertionDefinition(
        type="element_unchecked",
        description="Assert a checkbox is unchecked",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_unchecked",
                "selector": {"strategy": "role", "value": {"role": "checkbox", "name": "Newsletter"}},
            },
        ],
        returns="checked: boolean",
    ),

    AssertionType.ELEMENT_FOCUSED.value: AssertionDefinition(
        type="element_focused",
        description="Assert an element has focus",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_focused",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
            },
        ],
        returns="focused: boolean",
    ),

    AssertionType.ELEMENT_EDITABLE.value: AssertionDefinition(
        type="element_editable",
        description="Assert an element is editable (not readonly)",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_editable",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
            },
        ],
        returns="editable: boolean",
    ),

    AssertionType.ELEMENT_READONLY.value: AssertionDefinition(
        type="element_readonly",
        description="Assert an element is readonly",
        category=AssertionCategory.STATE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_readonly",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
            },
        ],
        returns="readonly: boolean",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT CONTENT ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_TEXT_EQUALS.value: AssertionDefinition(
        type="element_text_equals",
        description="Assert element's text content exactly matches a value",
        category=AssertionCategory.CONTENT,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected text content",
            ),
            AssertionParam(
                name="trim",
                type=ParamType.BOOLEAN,
                required=False,
                description="Trim whitespace before comparison",
                default=True,
            ),
            AssertionParam(
                name="case_sensitive",
                type=ParamType.BOOLEAN,
                required=False,
                description="Case-sensitive comparison",
                default=True,
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_text_equals",
                "selector": {"strategy": "testid", "value": "greeting"},
                "value": "Hello, John!",
            },
            {
                "type": "element_text_equals",
                "selector": {"strategy": "testid", "value": "price"},
                "value": "$99.99",
                "case_sensitive": False,
            },
        ],
        returns="actual_text: string",
    ),

    AssertionType.ELEMENT_TEXT_CONTAINS.value: AssertionDefinition(
        type="element_text_contains",
        description="Assert element's text content contains a substring",
        category=AssertionCategory.CONTENT,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Substring to check for",
            ),
            AssertionParam(
                name="case_sensitive",
                type=ParamType.BOOLEAN,
                required=False,
                description="Case-sensitive comparison",
                default=True,
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_text_contains",
                "selector": {"strategy": "testid", "value": "price"},
                "value": "$99.99",
            },
            {
                "type": "element_text_contains",
                "selector": {"strategy": "role", "value": {"role": "alert"}},
                "value": "Success",
                "case_sensitive": False,
            },
        ],
        returns="actual_text: string, found: boolean",
    ),

    AssertionType.ELEMENT_TEXT_MATCHES.value: AssertionDefinition(
        type="element_text_matches",
        description="Assert element's text content matches a regex pattern",
        category=AssertionCategory.CONTENT,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="pattern",
                type=ParamType.REGEX,
                required=True,
                description="Regex pattern to match against text",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_text_matches",
                "selector": {"strategy": "testid", "value": "price"},
                "pattern": r"^\$\d+\.\d{2}$",
            },
            {
                "type": "element_text_matches",
                "selector": {"strategy": "testid", "value": "order-id"},
                "pattern": r"ORD-\d{6}",
            },
        ],
        returns="actual_text: string, matched: boolean",
    ),

    AssertionType.ELEMENT_VALUE_EQUALS.value: AssertionDefinition(
        type="element_value_equals",
        description="Assert input/textarea value exactly matches (for form inputs)",
        category=AssertionCategory.CONTENT,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected input value",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_value_equals",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
                "value": "user@test.com",
            },
        ],
        returns="actual_value: string",
    ),

    AssertionType.ELEMENT_VALUE_CONTAINS.value: AssertionDefinition(
        type="element_value_contains",
        description="Assert input/textarea value contains a substring",
        category=AssertionCategory.CONTENT,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Substring to check for in input value",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_value_contains",
                "selector": {"strategy": "role", "value": {"role": "searchbox"}},
                "value": "laptop",
            },
        ],
        returns="actual_value: string, found: boolean",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT ATTRIBUTE ASSERTIONS
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_ATTRIBUTE.value: AssertionDefinition(
        type="element_attribute",
        description="Assert an element has a specific attribute value",
        category=AssertionCategory.ATTRIBUTE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="attribute",
                type=ParamType.STRING,
                required=True,
                description="Attribute name to check",
            ),
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected attribute value",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_attribute",
                "selector": {"strategy": "role", "value": {"role": "link", "name": "Learn More"}},
                "attribute": "href",
                "value": "/docs",
            },
            {
                "type": "element_attribute",
                "selector": {"strategy": "testid", "value": "avatar"},
                "attribute": "alt",
                "value": "User avatar",
            },
            {
                "type": "element_attribute",
                "selector": {"strategy": "testid", "value": "link"},
                "attribute": "target",
                "value": "_blank",
            },
        ],
        returns="actual_value: string | null",
    ),

    AssertionType.ELEMENT_HAS_CLASS.value: AssertionDefinition(
        type="element_has_class",
        description="Assert an element has a specific CSS class",
        category=AssertionCategory.ATTRIBUTE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="class",
                type=ParamType.STRING,
                required=True,
                description="CSS class name to check for",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_has_class",
                "selector": {"strategy": "testid", "value": "status"},
                "class": "active",
            },
            {
                "type": "element_has_class",
                "selector": {"strategy": "testid", "value": "button"},
                "class": "btn-primary",
            },
        ],
        returns="classes: string[], has_class: boolean",
    ),

    AssertionType.ELEMENT_HAS_STYLE.value: AssertionDefinition(
        type="element_has_style",
        description="Assert an element has a specific CSS style",
        category=AssertionCategory.ATTRIBUTE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="property",
                type=ParamType.STRING,
                required=True,
                description="CSS property name",
            ),
            AssertionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Expected CSS property value",
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_has_style",
                "selector": {"strategy": "testid", "value": "banner"},
                "property": "background-color",
                "value": "rgb(0, 128, 0)",
            },
            {
                "type": "element_has_style",
                "selector": {"strategy": "testid", "value": "hidden-element"},
                "property": "display",
                "value": "none",
            },
        ],
        returns="actual_value: string",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT COUNT ASSERTION
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_COUNT.value: AssertionDefinition(
        type="element_count",
        description="Assert the number of matching elements",
        category=AssertionCategory.EXISTENCE,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="count",
                type=ParamType.NUMBER,
                required=True,
                description="Expected number of elements",
                min_value=0,
            ),
            AssertionParam(
                name="operator",
                type=ParamType.STRING,
                required=False,
                description="Comparison operator",
                default="equals",
                enum=["equals", "at_least", "at_most", "greater_than", "less_than"],
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_count",
                "selector": {"strategy": "role", "value": {"role": "listitem"}},
                "count": 5,
            },
            {
                "type": "element_count",
                "selector": {"strategy": "role", "value": {"role": "checkbox"}},
                "count": 3,
                "operator": "at_least",
            },
            {
                "type": "element_count",
                "selector": {"strategy": "testid", "value": "error-message"},
                "count": 0,
            },
        ],
        returns="actual_count: number",
    ),

    # ═════════════════════════════════════════════════════════════════
    # ELEMENT POSITION ASSERTION
    # ═════════════════════════════════════════════════════════════════

    AssertionType.ELEMENT_IN_VIEWPORT.value: AssertionDefinition(
        type="element_in_viewport",
        description="Assert an element is within the viewport",
        category=AssertionCategory.POSITION,
        needs_target=True,
        params=[
            SELECTOR_PARAM,
            ELEMENT_ID_PARAM,
            AssertionParam(
                name="ratio",
                type=ParamType.NUMBER,
                required=False,
                description="Minimum visible ratio (0.0-1.0)",
                default=0.5,
                min_value=0.0,
                max_value=1.0,
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "element_in_viewport",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
            },
            {
                "type": "element_in_viewport",
                "selector": {"strategy": "testid", "value": "hero-image"},
                "ratio": 1.0,
            },
        ],
        returns="visible_ratio: number, in_viewport: boolean",
    ),

    # ═════════════════════════════════════════════════════════════════
    # CUSTOM ASSERTION
    # ═════════════════════════════════════════════════════════════════

    AssertionType.JAVASCRIPT.value: AssertionDefinition(
        type="javascript",
        description="Assert result of custom JavaScript expression",
        category=AssertionCategory.CUSTOM,
        needs_target=False,
        params=[
            AssertionParam(
                name="script",
                type=ParamType.STRING,
                required=True,
                description="JavaScript expression that returns a boolean",
            ),
            AssertionParam(
                name="expected",
                type=ParamType.BOOLEAN,
                required=False,
                description="Expected return value (default: true)",
                default=True,
            ),
            MESSAGE_PARAM,
        ],
        examples=[
            {
                "type": "javascript",
                "script": "document.querySelectorAll('.item').length > 0",
            },
            {
                "type": "javascript",
                "script": "localStorage.getItem('token') !== null",
                "message": "User should be authenticated",
            },
        ],
        returns="result: any, passed: boolean",
    ),
}


# ── Helper Functions ───────────────────────────────────────────────────

def get_assertion(assertion_type: str) -> Optional[AssertionDefinition]:
    """
    Get assertion definition by type.
    
    Args:
        assertion_type: Assertion type name (e.g., "element_visible")
        
    Returns:
        AssertionDefinition or None if not found
    """
    return ASSERTIONS.get(assertion_type)


def get_all_assertions() -> List[AssertionDefinition]:
    """
    Get all assertion definitions.
    
    Returns:
        List of all AssertionDefinition objects
    """
    return list(ASSERTIONS.values())


def get_assertions_by_category(category: AssertionCategory) -> List[AssertionDefinition]:
    """
    Get assertions filtered by category.
    
    Args:
        category: AssertionCategory to filter by
        
    Returns:
        List of matching AssertionDefinition objects
    """
    return [a for a in ASSERTIONS.values() if a.category == category]


def validate_assertion(assertion: dict) -> Tuple[bool, List[str]]:
    """
    Validate an assertion object from a plan.
    
    Args:
        assertion: Assertion dictionary with 'type' key and parameters
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Check type field exists
    assertion_type = assertion.get("type")
    if not assertion_type:
        return False, ["Missing required field: 'type'"]
    
    # Check assertion type is known
    assertion_def = ASSERTIONS.get(assertion_type)
    if not assertion_def:
        return False, [f"Unknown assertion type: '{assertion_type}'"]
    
    # Check target requirement
    if assertion_def.needs_target:
        if not assertion.get("selector"):
            errors.append(f"Assertion '{assertion_type}' requires a 'selector' field")
    
    # Check required parameters
    for param in assertion_def.params:
        if param.required and param.name not in assertion:
            errors.append(f"Missing required parameter '{param.name}' for assertion '{assertion_type}'")
    
    # Validate parameter values
    for param in assertion_def.params:
        if param.name in assertion:
            value = assertion[param.name]
            
            # Check enum values
            if param.enum and isinstance(value, str):
                if value not in param.enum:
                    errors.append(
                        f"Invalid value '{value}' for '{param.name}'. "
                        f"Must be one of: {param.enum}"
                    )
            
            # Check numeric ranges
            if param.type == ParamType.NUMBER and isinstance(value, (int, float)):
                if param.min_value is not None and value < param.min_value:
                    errors.append(
                        f"Value {value} for '{param.name}' is below minimum {param.min_value}"
                    )
                if param.max_value is not None and value > param.max_value:
                    errors.append(
                        f"Value {value} for '{param.name}' exceeds maximum {param.max_value}"
                    )
    
    # Validate selector format
    selector = assertion.get("selector")
    if selector:
        selector_errors = validate_selector(selector, "selector")
        errors.extend(selector_errors)
    
    return len(errors) == 0, errors


def validate_selector(selector: dict, field_name: str = "selector") -> List[str]:
    """
    Validate a selector object.
    
    Args:
        selector: Selector dictionary
        field_name: Name of the field for error messages
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    
    if not isinstance(selector, dict):
        return [f"'{field_name}' must be an object"]
    
    strategy = selector.get("strategy")
    value = selector.get("value")
    
    if not strategy:
        errors.append(f"'{field_name}' missing required field: 'strategy'")
        return errors
    
    valid_strategies = ["role", "testid", "css", "xpath", "text", "label",
                        "placeholder", "alt_text", "title", "aria-label", "testid_prefix", "id"]
    if strategy not in valid_strategies:
        errors.append(f"'{field_name}' has invalid strategy: '{strategy}'")
    
    if value is None:
        errors.append(f"'{field_name}' missing required field: 'value'")
    
    # Role-specific validation
    if strategy == "role":
        if not isinstance(value, dict):
            errors.append(f"'{field_name}' role selector 'value' must be an object")
        elif "role" not in value:
            errors.append(f"'{field_name}' role selector missing 'role' in value")
    
    return errors


def assertion_to_schema() -> dict:
    """
    Export assertion definitions as JSON Schema.
    Useful for planner validation and documentation generation.
    
    Returns:
        Dictionary schema of all assertions
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "QAPal Assertions",
        "description": "Assertion definitions for QAPal test verification",
        "assertions": {
            assertion_type: assertion_def.to_dict()
            for assertion_type, assertion_def in ASSERTIONS.items()
        },
        "selector_schema": {
            "type": "object",
            "required": ["strategy", "value"],
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["role", "testid", "css", "xpath", "text", "label",
                            "placeholder", "alt_text", "title", "aria-label", "testid_prefix", "id"],
                },
                "value": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "object"},
                    ],
                },
                "element_id": {"type": "string"},
            },
        },
        "result_schema": {
            "type": "object",
            "required": ["type", "status"],
            "properties": {
                "type": {"type": "string"},
                "status": {"type": "string", "enum": ["pass", "fail"]},
                "actual": {"description": "What was actually found"},
                "expected": {"description": "What was expected"},
                "selector": {"type": "object"},
                "reason": {"type": "string", "description": "Explanation if failed"},
            },
        },
    }


def get_assertion_summary() -> str:
    """
    Get a human-readable summary of all assertions.
    Useful for documentation or help text.
    
    Returns:
        Markdown-formatted summary string
    """
    lines = ["# QAPal Assertions Reference\n"]
    
    for category in AssertionCategory:
        assertions_in_category = get_assertions_by_category(category)
        if not assertions_in_category:
            continue
        
        lines.append(f"\n## {category.value.title()} Assertions\n")
        
        for assertion in assertions_in_category:
            lines.append(f"\n### `{assertion.type}`\n")
            lines.append(f"{assertion.description}\n")
            
            if assertion.needs_target:
                lines.append("**Requires element selector.**\n")
            
            if assertion.params:
                lines.append("\n**Parameters:**\n")
                lines.append("| Name | Type | Required | Description |")
                lines.append("|------|------|----------|-------------|")
                for p in assertion.params:
                    req = "Yes" if p.required else "No"
                    lines.append(f"| `{p.name}` | {p.type.value} | {req} | {p.description} |")
            
            if assertion.returns:
                lines.append(f"\n**Returns:** `{assertion.returns}`\n")
            
            if assertion.examples:
                lines.append("\n**Examples:**\n")
                lines.append("```json")
                for ex in assertion.examples[:2]:  # Limit to 2 examples
                    lines.append(json.dumps(ex, indent=2))
                lines.append("```\n")
    
    return "\n".join(lines)


def get_assertion_result(
    assertion: dict,
    status: str,
    actual: Any = None,
    expected: Any = None,
    reason: Optional[str] = None,
) -> dict:
    """
    Build a standardized assertion result object.
    
    Args:
        assertion: Original assertion dict
        status: "pass" or "fail"
        actual: What was actually found
        expected: What was expected
        reason: Explanation if failed
        
    Returns:
        Standardized result dictionary
    """
    result = {
        "type": assertion.get("type"),
        "status": status,
    }
    
    if actual is not None:
        result["actual"] = actual
    
    if expected is not None:
        result["expected"] = expected
    
    if assertion.get("selector"):
        result["selector"] = assertion["selector"]
    
    if status == "fail" and reason:
        result["reason"] = reason
    
    return result


# ── Export for convenience ────────────────────────────────────────────

__all__ = [
    # Core types
    "AssertionType",
    "AssertionCategory",
    "ParamType",
    "AssertionParam",
    "AssertionDefinition",
    
    # Registry
    "ASSERTIONS",
    
    # Functions
    "get_assertion",
    "get_all_assertions",
    "get_assertions_by_category",
    "validate_assertion",
    "validate_selector",
    "assertion_to_schema",
    "get_assertion_summary",
    "get_assertion_result",
]
