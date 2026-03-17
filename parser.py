"""
parser.py — Playwright Test Selector Parser
=============================================
Extracts Playwright selectors from Python and TypeScript test files
using regex-based pattern matching.

Pure module — no browser or Playwright dependency.

Supports:
  - Python: page.get_by_test_id(), get_by_role(), get_by_text(), etc.
  - TypeScript: page.getByTestId(), getByRole(), getByText(), etc.
  - page.locator() with CSS, ID, attribute selectors
  - Chained locators (partial support)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedSelector:
    """A selector extracted from a test file."""
    file_path: str
    line_number: int
    selector_type: str          # "testid" | "role" | "text" | "label" | "placeholder" | "alt_text" | "locator" | "aria_label"
    value: Any                  # str or dict (for role: {"role": "button", "name": "Submit"})
    full_expression: str        # "page.get_by_test_id('email')"
    action: Optional[str]       # "click", "fill", "type", etc. or None
    language: str               # "python" | "typescript"
    raw_line: str = ""          # full source line

    def __repr__(self) -> str:
        return f"ParsedSelector(L{self.line_number}, {self.selector_type}={self.value!r})"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_MAP = {
    ".py":       "python",
    ".ts":       "typescript",
    ".spec.ts":  "typescript",
    ".tsx":      "typescript",
    ".js":       "typescript",   # JS uses same API as TS
    ".spec.js":  "typescript",
    ".jsx":      "typescript",
    ".mjs":      "typescript",
}


def detect_language(file_path: str) -> str:
    """Detect language from file extension. Defaults to 'typescript'."""
    p = Path(file_path)
    # Check compound extensions first
    name = p.name.lower()
    if name.endswith(".spec.ts") or name.endswith(".spec.js"):
        return "typescript"
    return _LANG_MAP.get(p.suffix.lower(), "typescript")


# ---------------------------------------------------------------------------
# Regex patterns — Python
# ---------------------------------------------------------------------------

# Quotes: either single or double
_Q = r"""(?:["'])(.+?)(?:["'])"""       # capture group for quoted string
_Qs = r"""["'](.+?)["']"""             # simpler version

_PY_PATTERNS = {
    "testid": re.compile(
        r'page\.get_by_test_id\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "role": re.compile(
        r'page\.get_by_role\(\s*' + _Qs + r'(?:\s*,\s*name\s*=\s*' + _Qs + r')?\s*\)', re.DOTALL
    ),
    "text": re.compile(
        r'page\.get_by_text\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "label": re.compile(
        r'page\.get_by_label\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "placeholder": re.compile(
        r'page\.get_by_placeholder\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "alt_text": re.compile(
        r'page\.get_by_alt_text\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "locator": re.compile(
        r'page\.locator\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
}

# ---------------------------------------------------------------------------
# Regex patterns — TypeScript
# ---------------------------------------------------------------------------

_TS_PATTERNS = {
    "testid": re.compile(
        r'page\.getByTestId\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "role": re.compile(
        r"page\.getByRole\(\s*" + _Qs +
        r"(?:\s*,\s*\{\s*name:\s*" + _Qs + r"\s*(?:,\s*exact:\s*(?:true|false))?\s*\})?\s*\)",
        re.DOTALL
    ),
    "text": re.compile(
        r'page\.getByText\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "label": re.compile(
        r'page\.getByLabel\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "placeholder": re.compile(
        r'page\.getByPlaceholder\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "alt_text": re.compile(
        r'page\.getByAltText\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
    "locator": re.compile(
        r'page\.locator\(\s*' + _Qs + r'\s*\)', re.DOTALL
    ),
}

# ---------------------------------------------------------------------------
# Action detection (shared for both languages)
# ---------------------------------------------------------------------------

_ACTION_PATTERN = re.compile(
    r'\)\s*\.\s*(click|fill|type|check|uncheck|hover|dblclick|press|'
    r'select_option|selectOption|clear|focus|blur|tap|scroll_into_view_if_needed)\s*\('
)


# ---------------------------------------------------------------------------
# Locator sub-classification
# ---------------------------------------------------------------------------

def _classify_locator(value: str) -> tuple[str, str]:
    """
    Sub-classify a page.locator() value.
    Returns (selector_type, cleaned_value).
    """
    v = value.strip()

    # [data-testid="value"] → testid
    m = re.match(r'\[data-(?:testid|test|cy|qa)=["\'](.+?)["\']\]', v)
    if m:
        return "testid", m.group(1)

    # [aria-label="value"] → aria_label
    m = re.match(r'\[aria-label=["\'](.+?)["\']\]', v)
    if m:
        return "aria_label", m.group(1)

    # #id → id
    if v.startswith("#") and " " not in v:
        return "id", v[1:]

    # Everything else → css
    return "css", v


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def _parse_line(
    line: str,
    line_number: int,
    file_path: str,
    language: str,
) -> List[ParsedSelector]:
    """Parse a single line for Playwright selectors. Returns 0 or more ParsedSelectors."""
    results = []
    patterns = _PY_PATTERNS if language == "python" else _TS_PATTERNS

    for sel_type, pattern in patterns.items():
        for match in pattern.finditer(line):
            full_expr = match.group(0)

            if sel_type == "role":
                role_name = match.group(1)
                name = match.group(2) if match.lastindex >= 2 and match.group(2) else None
                value: Any = {"role": role_name, "name": name} if name else {"role": role_name}
            elif sel_type == "locator":
                raw_val = match.group(1)
                sel_type_actual, clean_val = _classify_locator(raw_val)
                value = clean_val
                sel_type = sel_type_actual
            else:
                value = match.group(1)

            # Detect action
            action = None
            rest_of_line = line[match.end():]
            action_match = _ACTION_PATTERN.match(rest_of_line)
            if action_match:
                action = action_match.group(1)
                # Normalise TS camelCase to Python snake_case
                if action == "selectOption":
                    action = "select_option"

            results.append(ParsedSelector(
                file_path=file_path,
                line_number=line_number,
                selector_type=sel_type,
                value=value,
                full_expression=full_expr,
                action=action,
                language=language,
                raw_line=line.rstrip(),
            ))

    return results


def parse_file(file_path: str) -> List[ParsedSelector]:
    """
    Parse a Playwright test file and extract all selectors.
    Returns list of ParsedSelector sorted by line number.
    """
    path = Path(file_path)
    if not path.exists():
        return []

    language = detect_language(file_path)
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    results: List[ParsedSelector] = []

    # Handle multi-line by joining continuation lines
    joined_lines: List[tuple[int, str]] = []
    buffer = ""
    buffer_start = 0
    open_parens = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            if not buffer:
                continue

        if buffer:
            buffer += " " + stripped
        else:
            buffer = stripped
            buffer_start = i

        open_parens += stripped.count("(") - stripped.count(")")

        if open_parens <= 0:
            joined_lines.append((buffer_start, buffer))
            buffer = ""
            open_parens = 0

    # Flush remaining buffer
    if buffer:
        joined_lines.append((buffer_start, buffer))

    for line_num, joined_line in joined_lines:
        results.extend(_parse_line(joined_line, line_num, str(path), language))

    return results


def parse_directory(
    dir_path: str,
    glob_pattern: str = "**/*.spec.ts",
) -> List[ParsedSelector]:
    """
    Walk a directory and parse all matching test files.
    Returns aggregated list of ParsedSelector.
    """
    root = Path(dir_path)
    if not root.exists():
        return []

    results: List[ParsedSelector] = []
    for file_path in sorted(root.glob(glob_pattern)):
        if file_path.is_file():
            results.extend(parse_file(str(file_path)))

    return results


# ---------------------------------------------------------------------------
# Conversion: ParsedSelector → QAPAL selector dict
# ---------------------------------------------------------------------------

def selector_to_qapal(parsed: ParsedSelector) -> dict:
    """
    Convert a ParsedSelector to a QAPAL-format selector dict
    that can be passed to probe.py's resolve_locator().

    Examples:
        ParsedSelector(type="testid", value="email")
          → {"strategy": "testid", "value": "email"}

        ParsedSelector(type="role", value={"role": "button", "name": "Submit"})
          → {"strategy": "role", "value": {"role": "button", "name": "Submit"}}
    """
    strategy = parsed.selector_type

    # Map parser types to QAPAL strategy names
    type_map = {
        "testid":      "testid",
        "role":        "role",
        "text":        "text",
        "label":       "label",
        "placeholder": "placeholder",
        "alt_text":    "alt_text",
        "aria_label":  "aria-label",
        "css":         "css",
        "id":          "id",
        "locator":     "css",    # generic locator → css
    }

    return {
        "strategy": type_map.get(strategy, "css"),
        "value": parsed.value,
    }


# ---------------------------------------------------------------------------
# Conversion: QAPAL selector dict → Playwright expression
# ---------------------------------------------------------------------------

def qapal_to_expression(selector: dict, language: str = "python") -> str:
    """
    Convert a QAPAL selector dict to a Playwright code expression.
    Used by patcher to generate replacement code.
    """
    strategy = selector.get("strategy", "css")
    value = selector.get("value")

    if language == "python":
        return _qapal_to_python(strategy, value)
    else:
        return _qapal_to_typescript(strategy, value)


def _escape(s: str) -> str:
    """Escape a string for use in Python/TS string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _qapal_to_python(strategy: str, value) -> str:
    if strategy == "testid":
        return f'page.get_by_test_id("{_escape(str(value))}")'
    if strategy == "role":
        if isinstance(value, dict):
            role = value.get("role", "")
            name = value.get("name")
            if name:
                return f'page.get_by_role("{role}", name="{_escape(name)}")'
            return f'page.get_by_role("{role}")'
        return f'page.get_by_role("{_escape(str(value))}")'
    if strategy == "text":
        return f'page.get_by_text("{_escape(str(value))}")'
    if strategy == "label":
        return f'page.get_by_label("{_escape(str(value))}")'
    if strategy == "placeholder":
        return f'page.get_by_placeholder("{_escape(str(value))}")'
    if strategy == "alt_text":
        return f'page.get_by_alt_text("{_escape(str(value))}")'
    if strategy == "aria-label":
        return f'page.locator(\'[aria-label="{_escape(str(value))}"]\')'
    if strategy == "id":
        return f'page.locator("#{_escape(str(value))}")'
    # Default: css
    return f'page.locator("{_escape(str(value))}")'


def _qapal_to_typescript(strategy: str, value) -> str:
    if strategy == "testid":
        return f"page.getByTestId('{_escape(str(value))}')"
    if strategy == "role":
        if isinstance(value, dict):
            role = value.get("role", "")
            name = value.get("name")
            if name:
                return f"page.getByRole('{role}', {{ name: '{_escape(name)}' }})"
            return f"page.getByRole('{role}')"
        return f"page.getByRole('{_escape(str(value))}')"
    if strategy == "text":
        return f"page.getByText('{_escape(str(value))}')"
    if strategy == "label":
        return f"page.getByLabel('{_escape(str(value))}')"
    if strategy == "placeholder":
        return f"page.getByPlaceholder('{_escape(str(value))}')"
    if strategy == "alt_text":
        return f"page.getByAltText('{_escape(str(value))}')"
    if strategy == "aria-label":
        return f"page.locator('[aria-label=\"{_escape(str(value))}\"]')"
    if strategy == "id":
        return f"page.locator('#{_escape(str(value))}')"
    # Default: css
    return f"page.locator('{_escape(str(value))}')"
