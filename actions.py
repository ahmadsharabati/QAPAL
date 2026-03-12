"""
actions.py — QAPal Action Definitions
======================================
Defines all executable actions the planner can specify and the executor can run.

This module is the contract between:
  - Planner: Outputs actions in this format
  - Executor: Validates and executes actions in this format

Each action has:
  - name:         Action identifier (e.g., "click", "fill", "navigate")
  - description:  Human-readable description
  - category:     Grouping for documentation (navigation, input, interaction, utility)
  - requires_target: Whether the action needs an element selector
  - params:       Parameter definitions with types and requirements
  - examples:     Example action objects for documentation

Selector Format:
  All selectors follow the same structure:
    {"strategy": "role", "value": {"role": "button", "name": "Submit"}}
    {"strategy": "testid", "value": "submit-btn"}
    {"strategy": "css", "value": "form > button.primary"}

Usage:
  from actions import ACTIONS, validate_action, get_action
  
  # Get action definition
  action_def = get_action("click")
  
  # Validate an action from a plan
  is_valid, errors = validate_action({"action": "click", "selector": {...}})
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union, Tuple
from enum import Enum
import json


# ── Action Types ─────────────────────────────────────────────────────

class ActionType(Enum):
    """All supported action types."""
    # Navigation
    NAVIGATE   = "navigate"
    REFRESH    = "refresh"
    GO_BACK    = "go_back"
    GO_FORWARD = "go_forward"
    
    # Click/Press
    CLICK      = "click"
    DBLCLICK   = "dblclick"
    
    # Input
    FILL       = "fill"
    TYPE       = "type"
    CLEAR      = "clear"
    PRESS      = "press"
    SELECT     = "select"
    
    # Checkbox/Radio
    CHECK      = "check"
    UNCHECK    = "uncheck"
    
    # Hover/Focus
    HOVER      = "hover"
    FOCUS      = "focus"
    BLUR       = "blur"
    
    # Scroll
    SCROLL     = "scroll"
    
    # Wait
    WAIT       = "wait"
    
    # Utility
    SCREENSHOT = "screenshot"
    EVALUATE   = "evaluate"


class ActionCategory(Enum):
    """Action categories for documentation and UI grouping."""
    NAVIGATION  = "navigation"
    INPUT       = "input"
    INTERACTION = "interaction"
    STATE       = "state"
    UTILITY     = "utility"
    WAIT        = "wait"


class ParamType(Enum):
    """Parameter data types."""
    STRING   = "string"
    NUMBER   = "number"
    BOOLEAN  = "boolean"
    OBJECT   = "object"
    ARRAY    = "array"
    SELECTOR = "selector"


# ── Parameter Definition ─────────────────────────────────────────────

@dataclass
class ActionParam:
    """
    Definition of an action parameter.
    
    Attributes:
        name: Parameter name (used as key in action dict)
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


# ── Action Definition ────────────────────────────────────────────────

@dataclass
class ActionDefinition:
    """
    Complete definition of an executable action.
    
    Attributes:
        name: Action identifier (matches ActionType value)
        description: Human-readable description
        category: Action grouping
        requires_target: Whether action needs an element selector
        params: List of parameter definitions
        examples: Example action objects
    """
    name: str
    description: str
    category: ActionCategory
    requires_target: bool
    params: List[ActionParam] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "requires_target": self.requires_target,
            "params": [p.to_dict() for p in self.params],
            "examples": self.examples,
        }


# ── Selector Parameter (reusable) ─────────────────────────────────────

SELECTOR_PARAM = ActionParam(
    name="selector",
    type=ParamType.SELECTOR,
    required=True,
    description="Element selector object with 'strategy' and 'value' keys",
)

FALLBACK_PARAM = ActionParam(
    name="fallback",
    type=ParamType.SELECTOR,
    required=False,
    description="Fallback selector if primary fails",
)

ELEMENT_ID_PARAM = ActionParam(
    name="element_id",
    type=ParamType.STRING,
    required=False,
    description="Reference ID to the element in the locator database",
)

TIMEOUT_PARAM = ActionParam(
    name="timeout",
    type=ParamType.NUMBER,
    required=False,
    description="Timeout in milliseconds",
    default=10000,
    min_value=0,
    max_value=300000,
)

FORCE_PARAM = ActionParam(
    name="force",
    type=ParamType.BOOLEAN,
    required=False,
    description="Skip actionability checks",
    default=False,
)


# ── Action Definitions Registry ───────────────────────────────────────

ACTIONS: Dict[str, ActionDefinition] = {

    # ═════════════════════════════════════════════════════════════════
    # NAVIGATION ACTIONS (no target element)
    # ═════════════════════════════════════════════════════════════════

    ActionType.NAVIGATE.value: ActionDefinition(
        name="navigate",
        description="Navigate to a URL",
        category=ActionCategory.NAVIGATION,
        requires_target=False,
        params=[
            ActionParam(
                name="url",
                type=ParamType.STRING,
                required=True,
                description="URL to navigate to (absolute or relative)",
            ),
            ActionParam(
                name="wait_until",
                type=ParamType.STRING,
                required=False,
                description="Wait state after navigation",
                default="domcontentloaded",
                enum=["load", "domcontentloaded", "networkidle", "commit"],
            ),
            ActionParam(
                name="timeout",
                type=ParamType.NUMBER,
                required=False,
                description="Navigation timeout in milliseconds",
                default=30000,
            ),
        ],
        examples=[
            {"action": "navigate", "url": "https://app.com/login"},
            {"action": "navigate", "url": "/dashboard", "wait_until": "networkidle"},
            {"action": "navigate", "url": "https://app.com/settings", "timeout": 60000},
        ],
    ),

    ActionType.REFRESH.value: ActionDefinition(
        name="refresh",
        description="Refresh the current page",
        category=ActionCategory.NAVIGATION,
        requires_target=False,
        params=[
            ActionParam(
                name="wait_until",
                type=ParamType.STRING,
                required=False,
                description="Wait state after refresh",
                default="domcontentloaded",
                enum=["load", "domcontentloaded", "networkidle"],
            ),
            TIMEOUT_PARAM,
        ],
        examples=[
            {"action": "refresh"},
            {"action": "refresh", "wait_until": "networkidle"},
        ],
    ),

    ActionType.GO_BACK.value: ActionDefinition(
        name="go_back",
        description="Navigate back in browser history",
        category=ActionCategory.NAVIGATION,
        requires_target=False,
        params=[
            TIMEOUT_PARAM,
        ],
        examples=[
            {"action": "go_back"},
        ],
    ),

    ActionType.GO_FORWARD.value: ActionDefinition(
        name="go_forward",
        description="Navigate forward in browser history",
        category=ActionCategory.NAVIGATION,
        requires_target=False,
        params=[
            TIMEOUT_PARAM,
        ],
        examples=[
            {"action": "go_forward"},
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # CLICK ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.CLICK.value: ActionDefinition(
        name="click",
        description="Click on an element",
        category=ActionCategory.INTERACTION,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="button",
                type=ParamType.STRING,
                required=False,
                description="Mouse button to click",
                default="left",
                enum=["left", "right", "middle"],
            ),
            ActionParam(
                name="click_count",
                type=ParamType.NUMBER,
                required=False,
                description="Number of clicks (1=single, 2=double)",
                default=1,
                min_value=1,
                max_value=3,
            ),
            ActionParam(
                name="delay",
                type=ParamType.NUMBER,
                required=False,
                description="Delay between mousedown and mouseup in ms",
                min_value=0,
                max_value=5000,
            ),
            ActionParam(
                name="position",
                type=ParamType.OBJECT,
                required=False,
                description="Click position relative to element: {x, y}",
            ),
            ActionParam(
                name="modifiers",
                type=ParamType.ARRAY,
                required=False,
                description="Modifier keys: Alt, Control, Meta, Shift",
            ),
            FORCE_PARAM,
            TIMEOUT_PARAM,
            ActionParam(
                name="trial",
                type=ParamType.BOOLEAN,
                required=False,
                description="Perform trial click without actually clicking",
                default=False,
            ),
        ],
        examples=[
            {
                "action": "click",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
            },
            {
                "action": "click",
                "selector": {"strategy": "testid", "value": "menu-toggle"},
                "fallback": {"strategy": "css", "value": "#menu-toggle"},
                "button": "right",
            },
            {
                "action": "click",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "Settings"}},
                "modifiers": ["Control"],
            },
        ],
    ),

    ActionType.DBLCLICK.value: ActionDefinition(
        name="dblclick",
        description="Double-click on an element",
        category=ActionCategory.INTERACTION,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="button",
                type=ParamType.STRING,
                required=False,
                description="Mouse button",
                default="left",
                enum=["left", "right", "middle"],
            ),
            ActionParam(
                name="delay",
                type=ParamType.NUMBER,
                required=False,
                description="Delay between mousedown and mouseup in ms",
            ),
            ActionParam(
                name="position",
                type=ParamType.OBJECT,
                required=False,
                description="Click position: {x, y}",
            ),
            FORCE_PARAM,
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "dblclick",
                "selector": {"strategy": "role", "value": {"role": "button"}},
            },
            {
                "action": "dblclick",
                "selector": {"strategy": "testid", "value": "file-item"},
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # INPUT ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.FILL.value: ActionDefinition(
        name="fill",
        description="Fill a text input or textarea. Clears existing content first.",
        category=ActionCategory.INPUT,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="value",
                type=ParamType.STRING,
                required=True,
                description="Text value to fill",
            ),
            FORCE_PARAM,
            TIMEOUT_PARAM,
            ActionParam(
                name="no_wait_after",
                type=ParamType.BOOLEAN,
                required=False,
                description="Skip waiting for navigation after fill",
                default=False,
            ),
        ],
        examples=[
            {
                "action": "fill",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
                "value": "user@test.com",
            },
            {
                "action": "fill",
                "selector": {"strategy": "testid", "value": "search-input"},
                "fallback": {"strategy": "css", "value": "input[type='search']"},
                "value": "laptop computers",
            },
        ],
    ),

    ActionType.TYPE.value: ActionDefinition(
        name="type",
        description="Type text into an element (appends, does not clear first)",
        category=ActionCategory.INPUT,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="text",
                type=ParamType.STRING,
                required=True,
                description="Text to type",
            ),
            ActionParam(
                name="delay",
                type=ParamType.NUMBER,
                required=False,
                description="Delay between keystrokes in ms",
                default=0,
                min_value=0,
            ),
            FORCE_PARAM,
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "type",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
                "text": "Hello World",
            },
            {
                "action": "type",
                "selector": {"strategy": "testid", "value": "chat-input"},
                "text": "Additional text",
                "delay": 50,
            },
        ],
    ),

    ActionType.CLEAR.value: ActionDefinition(
        name="clear",
        description="Clear the content of a text input or textarea",
        category=ActionCategory.INPUT,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            FORCE_PARAM,
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "clear",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
            },
            {
                "action": "clear",
                "selector": {"strategy": "testid", "value": "search-box"},
            },
        ],
    ),

    ActionType.PRESS.value: ActionDefinition(
        name="press",
        description="Press a key or key combination on a focused element",
        category=ActionCategory.INPUT,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="key",
                type=ParamType.STRING,
                required=True,
                description="Key to press: Enter, Tab, Escape, ArrowDown, Control+a, etc.",
            ),
            ActionParam(
                name="delay",
                type=ParamType.NUMBER,
                required=False,
                description="Delay between keydown and keyup in ms",
            ),
            TIMEOUT_PARAM,
            ActionParam(
                name="no_wait_after",
                type=ParamType.BOOLEAN,
                required=False,
                description="Skip waiting for navigation",
                default=False,
            ),
        ],
        examples=[
            {
                "action": "press",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Search"}},
                "key": "Enter",
            },
            {
                "action": "press",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
                "key": "Control+a",
            },
            {
                "action": "press",
                "selector": {"strategy": "testid", "value": "editor"},
                "key": "Escape",
            },
        ],
    ),

    ActionType.SELECT.value: ActionDefinition(
        name="select",
        description="Select option(s) from a dropdown/select element",
        category=ActionCategory.INPUT,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="value",
                type=ParamType.STRING,
                required=False,
                description="Value of option to select",
            ),
            ActionParam(
                name="label",
                type=ParamType.STRING,
                required=False,
                description="Visible text of option to select",
            ),
            ActionParam(
                name="index",
                type=ParamType.NUMBER,
                required=False,
                description="Index of option to select (0-based)",
                min_value=0,
            ),
            ActionParam(
                name="values",
                type=ParamType.ARRAY,
                required=False,
                description="Multiple values for multi-select",
            ),
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "select",
                "selector": {"strategy": "role", "value": {"role": "combobox", "name": "Country"}},
                "label": "United States",
            },
            {
                "action": "select",
                "selector": {"strategy": "testid", "value": "size-select"},
                "value": "large",
            },
            {
                "action": "select",
                "selector": {"strategy": "role", "value": {"role": "listbox"}},
                "index": 2,
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # CHECKBOX / RADIO ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.CHECK.value: ActionDefinition(
        name="check",
        description="Check a checkbox or radio button",
        category=ActionCategory.STATE,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            FORCE_PARAM,
            ActionParam(
                name="position",
                type=ParamType.OBJECT,
                required=False,
                description="Click position: {x, y}",
            ),
            TIMEOUT_PARAM,
            ActionParam(
                name="trial",
                type=ParamType.BOOLEAN,
                required=False,
                description="Perform trial check",
                default=False,
            ),
        ],
        examples=[
            {
                "action": "check",
                "selector": {"strategy": "role", "value": {"role": "checkbox", "name": "Remember me"}},
            },
            {
                "action": "check",
                "selector": {"strategy": "testid", "value": "terms-checkbox"},
            },
        ],
    ),

    ActionType.UNCHECK.value: ActionDefinition(
        name="uncheck",
        description="Uncheck a checkbox",
        category=ActionCategory.STATE,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            FORCE_PARAM,
            ActionParam(
                name="position",
                type=ParamType.OBJECT,
                required=False,
                description="Click position: {x, y}",
            ),
            TIMEOUT_PARAM,
            ActionParam(
                name="trial",
                type=ParamType.BOOLEAN,
                required=False,
                description="Perform trial uncheck",
                default=False,
            ),
        ],
        examples=[
            {
                "action": "uncheck",
                "selector": {"strategy": "role", "value": {"role": "checkbox", "name": "Newsletter"}},
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # HOVER / FOCUS ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.HOVER.value: ActionDefinition(
        name="hover",
        description="Hover over an element",
        category=ActionCategory.INTERACTION,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            ActionParam(
                name="position",
                type=ParamType.OBJECT,
                required=False,
                description="Hover position: {x, y}",
            ),
            ActionParam(
                name="modifiers",
                type=ParamType.ARRAY,
                required=False,
                description="Modifier keys: Alt, Control, Meta, Shift",
            ),
            FORCE_PARAM,
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "hover",
                "selector": {"strategy": "role", "value": {"role": "button", "name": "More options"}},
            },
            {
                "action": "hover",
                "selector": {"strategy": "testid", "value": "dropdown-menu"},
                "modifiers": ["Shift"],
            },
        ],
    ),

    ActionType.FOCUS.value: ActionDefinition(
        name="focus",
        description="Focus an element",
        category=ActionCategory.STATE,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
            TIMEOUT_PARAM,
        ],
        examples=[
            {
                "action": "focus",
                "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Search"}},
            },
        ],
    ),

    ActionType.BLUR.value: ActionDefinition(
        name="blur",
        description="Remove focus from an element",
        category=ActionCategory.STATE,
        requires_target=True,
        params=[
            SELECTOR_PARAM,
            FALLBACK_PARAM,
            ELEMENT_ID_PARAM,
        ],
        examples=[
            {
                "action": "blur",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # SCROLL ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.SCROLL.value: ActionDefinition(
        name="scroll",
        description="Scroll the page or an element",
        category=ActionCategory.INTERACTION,
        requires_target=False,  # Can target element or page
        params=[
            ActionParam(
                name="selector",
                type=ParamType.SELECTOR,
                required=False,
                description="Element to scroll (omit for page scroll)",
            ),
            ELEMENT_ID_PARAM,
            ActionParam(
                name="x",
                type=ParamType.NUMBER,
                required=False,
                description="Horizontal scroll distance",
            ),
            ActionParam(
                name="y",
                type=ParamType.NUMBER,
                required=False,
                description="Vertical scroll distance",
            ),
            ActionParam(
                name="direction",
                type=ParamType.STRING,
                required=False,
                description="Scroll direction shortcut",
                enum=["up", "down", "left", "right", "top", "bottom"],
            ),
        ],
        examples=[
            {"action": "scroll", "direction": "down"},
            {"action": "scroll", "direction": "bottom"},
            {"action": "scroll", "x": 0, "y": 500},
            {
                "action": "scroll",
                "selector": {"strategy": "testid", "value": "chat-messages"},
                "direction": "bottom",
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # WAIT ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.WAIT.value: ActionDefinition(
        name="wait",
        description="Wait for a condition, time, or element state",
        category=ActionCategory.WAIT,
        requires_target=False,
        params=[
            ActionParam(
                name="duration",
                type=ParamType.NUMBER,
                required=False,
                description="Fixed wait duration in milliseconds",
                min_value=0,
                max_value=60000,
            ),
            ActionParam(
                name="selector",
                type=ParamType.SELECTOR,
                required=False,
                description="Element to wait for",
            ),
            ELEMENT_ID_PARAM,
            ActionParam(
                name="state",
                type=ParamType.STRING,
                required=False,
                description="Element state to wait for",
                enum=["visible", "hidden", "attached", "detached", "enabled", "disabled", "editable"],
            ),
            ActionParam(
                name="for_url",
                type=ParamType.STRING,
                required=False,
                description="Wait for URL to equal this value",
            ),
            ActionParam(
                name="for_url_contains",
                type=ParamType.STRING,
                required=False,
                description="Wait for URL to contain this substring",
            ),
            ActionParam(
                name="for_url_matches",
                type=ParamType.STRING,
                required=False,
                description="Wait for URL to match this regex",
            ),
            ActionParam(
                name="timeout",
                type=ParamType.NUMBER,
                required=False,
                description="Maximum wait time in milliseconds",
                default=30000,
            ),
        ],
        examples=[
            {"action": "wait", "duration": 2000},
            {
                "action": "wait",
                "selector": {"strategy": "role", "value": {"role": "alert"}},
                "state": "visible",
            },
            {"action": "wait", "for_url_contains": "/dashboard"},
            {
                "action": "wait",
                "selector": {"strategy": "testid", "value": "loading"},
                "state": "hidden",
                "timeout": 15000,
            },
        ],
    ),

    # ═════════════════════════════════════════════════════════════════
    # UTILITY ACTIONS
    # ═════════════════════════════════════════════════════════════════

    ActionType.SCREENSHOT.value: ActionDefinition(
        name="screenshot",
        description="Take a screenshot of the page or element",
        category=ActionCategory.UTILITY,
        requires_target=False,
        params=[
            ActionParam(
                name="selector",
                type=ParamType.SELECTOR,
                required=False,
                description="Element to screenshot (omit for full page)",
            ),
            ELEMENT_ID_PARAM,
            ActionParam(
                name="label",
                type=ParamType.STRING,
                required=False,
                description="Label for screenshot filename",
            ),
            ActionParam(
                name="full_page",
                type=ParamType.BOOLEAN,
                required=False,
                description="Capture full scrollable page",
                default=False,
            ),
            ActionParam(
                name="mask",
                type=ParamType.ARRAY,
                required=False,
                description="Selectors to mask for privacy",
            ),
            ActionParam(
                name="animations",
                type=ParamType.STRING,
                required=False,
                description="How to handle animations",
                enum=["allow", "disabled"],
                default="disabled",
            ),
        ],
        examples=[
            {"action": "screenshot"},
            {"action": "screenshot", "label": "checkout-page", "full_page": True},
            {
                "action": "screenshot",
                "selector": {"strategy": "testid", "value": "error-modal"},
                "label": "error",
            },
        ],
    ),

    ActionType.EVALUATE.value: ActionDefinition(
        name="evaluate",
        description="Execute JavaScript in the browser context",
        category=ActionCategory.UTILITY,
        requires_target=False,
        params=[
            ActionParam(
                name="script",
                type=ParamType.STRING,
                required=True,
                description="JavaScript code to execute",
            ),
            ActionParam(
                name="arg",
                type=ParamType.OBJECT,
                required=False,
                description="Argument to pass to the script",
            ),
        ],
        examples=[
            {
                "action": "evaluate",
                "script": "window.scrollTo(0, document.body.scrollHeight)",
            },
            {
                "action": "evaluate",
                "script": "return document.querySelector('.price')?.textContent",
            },
        ],
    ),
}


# ── Helper Functions ───────────────────────────────────────────────────

def get_action(name: str) -> Optional[ActionDefinition]:
    """
    Get action definition by name.
    
    Args:
        name: Action name (e.g., "click", "fill")
        
    Returns:
        ActionDefinition or None if not found
    """
    return ACTIONS.get(name)


def get_all_actions() -> List[ActionDefinition]:
    """
    Get all action definitions.
    
    Returns:
        List of all ActionDefinition objects
    """
    return list(ACTIONS.values())


def get_actions_by_category(category: ActionCategory) -> List[ActionDefinition]:
    """
    Get actions filtered by category.
    
    Args:
        category: ActionCategory to filter by
        
    Returns:
        List of matching ActionDefinition objects
    """
    return [a for a in ACTIONS.values() if a.category == category]


def validate_action(action: dict) -> Tuple[bool, List[str]]:
    """
    Validate an action object from a plan.
    
    Args:
        action: Action dictionary with 'action' key and parameters
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Check action field exists
    action_name = action.get("action")
    if not action_name:
        return False, ["Missing required field: 'action'"]
    
    # Check action is known
    action_def = ACTIONS.get(action_name)
    if not action_def:
        return False, [f"Unknown action: '{action_name}'"]
    
    # Check target requirement
    if action_def.requires_target:
        if not action.get("selector"):
            errors.append(f"Action '{action_name}' requires a 'selector' field")
    
    # Check required parameters
    for param in action_def.params:
        if param.required and param.name not in action:
            errors.append(f"Missing required parameter '{param.name}' for action '{action_name}'")
    
    # Validate parameter values
    for param in action_def.params:
        if param.name in action:
            value = action[param.name]
            
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
    selector = action.get("selector")
    if selector:
        selector_errors = validate_selector(selector, "selector")
        errors.extend(selector_errors)
    
    fallback = action.get("fallback")
    if fallback:
        selector_errors = validate_selector(fallback, "fallback")
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
                        "placeholder", "alt_text", "title", "aria-label"]
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


def action_to_schema() -> dict:
    """
    Export action definitions as JSON Schema.
    Useful for planner validation and documentation generation.
    
    Returns:
        Dictionary schema of all actions
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "QAPal Actions",
        "description": "Action definitions for QAPal test execution",
        "actions": {
            name: action_def.to_dict()
            for name, action_def in ACTIONS.items()
        },
        "selector_schema": {
            "type": "object",
            "required": ["strategy", "value"],
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["role", "testid", "css", "xpath", "text", "label",
                            "placeholder", "alt_text", "title", "aria-label"],
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
    }


def get_action_summary() -> str:
    """
    Get a human-readable summary of all actions.
    Useful for documentation or help text.
    
    Returns:
        Markdown-formatted summary string
    """
    lines = ["# QAPal Actions Reference\n"]
    
    for category in ActionCategory:
        actions_in_category = get_actions_by_category(category)
        if not actions_in_category:
            continue
        
        lines.append(f"\n## {category.value.title()}\n")
        
        for action in actions_in_category:
            lines.append(f"\n### `{action.name}`\n")
            lines.append(f"{action.description}\n")
            
            if action.requires_target:
                lines.append("**Requires element selector.**\n")
            
            if action.params:
                lines.append("\n**Parameters:**\n")
                lines.append("| Name | Type | Required | Description |")
                lines.append("|------|------|----------|-------------|")
                for p in action.params:
                    req = "Yes" if p.required else "No"
                    lines.append(f"| `{p.name}` | {p.type.value} | {req} | {p.description} |")
            
            if action.examples:
                lines.append("\n**Examples:**\n")
                lines.append("```json")
                for ex in action.examples[:2]:  # Limit to 2 examples
                    lines.append(json.dumps(ex, indent=2))
                lines.append("```\n")
    
    return "\n".join(lines)


# ── Export for convenience ────────────────────────────────────────────

__all__ = [
    # Core types
    "ActionType",
    "ActionCategory",
    "ParamType",
    "ActionParam",
    "ActionDefinition",
    
    # Registry
    "ACTIONS",
    
    # Functions
    "get_action",
    "get_all_actions",
    "get_actions_by_category",
    "validate_action",
    "validate_selector",
    "action_to_schema",
    "get_action_summary",
]
