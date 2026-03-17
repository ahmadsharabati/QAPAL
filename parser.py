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
import ast
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

    return results


# ---------------------------------------------------------------------------
# AST Parsing — Python
# ---------------------------------------------------------------------------

class PlaywrightASTVisitor(ast.NodeVisitor):
    """
    Traverses a Python AST to find Playwright locator calls.
    More robust than regex for multi-line and dynamic locators.
    """
    def __init__(self, file_path: str, source_lines: List[str]):
        self.file_path = file_path
        self.source_lines = source_lines
        self.results: List[ParsedSelector] = []
        self.language = "python"

    def visit_Call(self, node: ast.Call):
        # Case 1: Direct call -> page.locator("...")
        # Case 2: Chained call -> page.locator("...").click()
        
        # We look for the "locator" part regardless of whether it's followed by an action
        self._check_node(node)
        
        # If this call is page.locator("...").click(), node.func is an Attribute
        # whose .value is the locator call.
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            self._check_node(node.func.value, action=node.func.attr)

        self.generic_visit(node)

    def _check_node(self, node: ast.Call, action: Optional[str] = None):
        if not isinstance(node.func, ast.Attribute):
            return

        method_name = node.func.attr
        valid_methods = {
            "get_by_test_id": "testid",
            "get_by_role": "role",
            "get_by_text": "text",
            "get_by_label": "label",
            "get_by_placeholder": "placeholder",
            "get_by_alt_text": "alt_text",
            "locator": "locator"
        }
        
        if method_name not in valid_methods:
            return

        sel_type = valid_methods[method_name]
        
        # Extract value
        value = None
        if sel_type == "role":
            if len(node.args) >= 1:
                role = self._eval_node(node.args[0])
                name = None
                for kw in node.keywords:
                    if kw.arg == "name":
                        name = self._eval_node(kw.value)
                value = {"role": role, "name": name} if name else {"role": role}
        else:
            if len(node.args) >= 1:
                value = self._eval_node(node.args[0])
            elif node.keywords:
                for kw in node.keywords:
                    value = self._eval_node(kw.value)
                    break

        if value is None:
            return

        # Classification for generic locator()
        if sel_type == "locator":
            sel_type, value = _classify_locator(str(value))

        # Check if already found to avoid duplicates from chained calls
        for r in self.results:
            if r.line_number == node.lineno and r.value == value:
                if action and not r.action:
                    r.action = action
                return

        # Reconstruct expression
        try:
            full_expr = ast.unparse(node)
        except Exception:
            full_expr = f"page.{method_name}(...)"

        self.results.append(ParsedSelector(
            file_path=self.file_path,
            line_number=node.lineno,
            selector_type=sel_type,
            value=value,
            full_expression=full_expr,
            action=action,
            language=self.language,
            raw_line=self.source_lines[node.lineno-1] if 0 < node.lineno <= len(self.source_lines) else ""
        ))

    def _eval_node(self, node: ast.AST) -> Any:
        """Safely evaluate simple expressions."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.JoinedStr):
            # f-string: "Welcome {user}"
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    parts.append("{v}")
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if left is not None and right is not None:
                return str(left) + str(right)
        return None


def parse_file_ast(file_path: str) -> List[ParsedSelector]:
    """Parse a Python file using AST."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
        visitor = PlaywrightASTVisitor(file_path, lines)
        visitor.visit(tree)
        return sorted(visitor.results, key=lambda x: x.line_number)
    except Exception as e:
        # Fallback to regex if AST fails (e.g. syntax error in test file)
        return []


def parse_file(file_path: str) -> List[ParsedSelector]:
    """
    Parse a Playwright test file and extract all selectors.
    Returns list of ParsedSelector sorted by line number.
    """
    path = Path(file_path)
    if not path.exists():
        return []

    language = detect_language(file_path)
    
    # Use AST for Python if possible
    if language == "python":
        ast_results = parse_file_ast(file_path)
        if ast_results:
            return ast_results

    # Fallback to Regex (or for TypeScript)
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
