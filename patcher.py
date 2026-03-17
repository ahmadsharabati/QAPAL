"""
patcher.py — Test File Patcher
================================
Generates diffs and applies selector fixes to Playwright test files.
Supports both Python and TypeScript.

Pure module — no browser dependency. Only file I/O + git operations.
"""

from __future__ import annotations

import difflib
import subprocess
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from parser import ParsedSelector, qapal_to_expression


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Patch:
    """A single selector replacement in a test file."""
    file_path: str
    line_number: int
    old_expression: str        # "page.locator('.submit-btn')"
    new_expression: str        # "page.get_by_test_id('submit')"
    old_selector: dict         # QAPAL selector dict (original)
    new_selector: dict         # QAPAL selector dict (replacement)
    confidence: float          # From ranker
    reason: str                # Human-readable reason for the change

    def __repr__(self) -> str:
        return f"Patch(L{self.line_number}, {self.old_expression!r} → {self.new_expression!r})"


# ---------------------------------------------------------------------------
# Patch generation
# ---------------------------------------------------------------------------

def generate_patch(
    parsed: ParsedSelector,
    new_selector: dict,
    confidence: float,
    reason: str = "",
) -> Patch:
    """
    Create a Patch from a parsed selector and its replacement.
    Converts new_selector to the correct Playwright expression for the target language.
    """
    new_expr = qapal_to_expression(new_selector, parsed.language)

    if not reason:
        old_strategy = parsed.selector_type
        new_strategy = new_selector.get("strategy", "?")
        reason = f"Replaced {old_strategy} with {new_strategy} (confidence: {confidence:.2f})"

    return Patch(
        file_path=parsed.file_path,
        line_number=parsed.line_number,
        old_expression=parsed.full_expression,
        new_expression=new_expr,
        old_selector={"strategy": parsed.selector_type, "value": parsed.value},
        new_selector=new_selector,
        confidence=confidence,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def apply_patch(patch: Patch) -> bool:
    """
    Apply a single patch to its file.
    Replaces old_expression with new_expression on the specified line.
    Returns True on success.
    """
    path = Path(patch.file_path)
    if not path.exists():
        return False

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Line numbers are 1-based
    idx = patch.line_number - 1
    if idx < 0 or idx >= len(lines):
        return False

    line = lines[idx]
    if patch.old_expression not in line:
        # Try searching nearby lines (multi-line selectors may shift)
        for offset in (-1, 1, -2, 2):
            check_idx = idx + offset
            if 0 <= check_idx < len(lines) and patch.old_expression in lines[check_idx]:
                idx = check_idx
                line = lines[idx]
                break
        else:
            return False

    lines[idx] = line.replace(patch.old_expression, patch.new_expression, 1)
    path.write_text("".join(lines), encoding="utf-8")
    return True


def apply_patches(patches: List[Patch]) -> Tuple[int, int]:
    """
    Apply multiple patches.
    Processes bottom-to-top per file to avoid line offset shifts.
    Returns (succeeded, failed) counts.
    """
    # Group by file
    by_file: dict[str, List[Patch]] = {}
    for p in patches:
        by_file.setdefault(p.file_path, []).append(p)

    succeeded = 0
    failed = 0

    for file_path, file_patches in by_file.items():
        # Sort by line number descending (bottom-to-top)
        for patch in sorted(file_patches, key=lambda p: p.line_number, reverse=True):
            if apply_patch(patch):
                succeeded += 1
            else:
                failed += 1

    return succeeded, failed


# ---------------------------------------------------------------------------
# Diff preview
# ---------------------------------------------------------------------------

def preview_patches(patches: List[Patch]) -> str:
    """
    Generate a unified diff preview for all patches.
    Useful for --dry-run mode.
    """
    output_parts: List[str] = []

    # Group by file
    by_file: dict[str, List[Patch]] = {}
    for p in patches:
        by_file.setdefault(p.file_path, []).append(p)

    for file_path, file_patches in sorted(by_file.items()):
        path = Path(file_path)
        if not path.exists():
            continue

        original_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        modified_lines = list(original_lines)

        # Apply patches to the copy (bottom-to-top)
        for patch in sorted(file_patches, key=lambda p: p.line_number, reverse=True):
            idx = patch.line_number - 1
            if 0 <= idx < len(modified_lines):
                modified_lines[idx] = modified_lines[idx].replace(
                    patch.old_expression, patch.new_expression, 1
                )

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="",
        )
        diff_text = "\n".join(diff)
        if diff_text:
            output_parts.append(diff_text)

    return "\n\n".join(output_parts)


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def format_patch_summary(patches: List[Patch]) -> str:
    """Format a human-readable summary of patches."""
    if not patches:
        return "No patches to apply."

    lines = [f"Found {len(patches)} selector replacement(s):\n"]

    for p in patches:
        from ranker import format_grade
        grade = format_grade(p.confidence)
        lines.append(
            f"  {Path(p.file_path).name}:{p.line_number}  "
            f"{p.old_expression}  →  {p.new_expression}  "
            f"{grade}  {p.reason}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git / PR automation
# ---------------------------------------------------------------------------

def create_pr(
    patches: List[Patch],
    branch_name: str = "qapal/fix-selectors",
    base_branch: str = "main",
    commit_message: str = "",
) -> Optional[str]:
    """
    Create a git branch, apply patches, commit, and open a PR.
    Returns the PR URL on success, None on failure.

    Requires: git, gh CLI.
    """
    if not patches:
        return None

    if not commit_message:
        n = len(patches)
        commit_message = f"fix(selectors): replace {n} weak selector{'s' if n > 1 else ''} with validated alternatives\n\nAuto-fixed by QAPAL locator intelligence engine."

    if not shutil.which("gh"):
        print("Error: 'gh' CLI is not found in PATH. Please install it to create PRs.", file=sys.stderr)
        return None

    try:
        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            check=True, capture_output=True, text=True,
        )

        # Apply patches
        succeeded, failed = apply_patches(patches)
        if succeeded == 0:
            return None

        # Stage changed files
        changed_files = list({p.file_path for p in patches})
        subprocess.run(
            ["git", "add"] + changed_files,
            check=True, capture_output=True, text=True,
        )

        # Commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            check=True, capture_output=True, text=True,
        )

        # Push
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            check=True, capture_output=True, text=True,
        )

        # Create PR
        body = _build_pr_body(patches, succeeded, failed)
        result = subprocess.run(
            ["gh", "pr", "create",
             "--title", f"fix(selectors): replace {succeeded} weak selectors",
             "--body", body,
             "--base", base_branch],
            check=True, capture_output=True, text=True,
        )

        return result.stdout.strip()

    except subprocess.CalledProcessError:
        return None


def _build_pr_body(patches: List[Patch], succeeded: int, failed: int) -> str:
    """Build the PR description body."""
    lines = [
        "## Summary",
        f"Replaced **{succeeded}** weak/broken Playwright selectors with validated alternatives.",
        "",
        "## Changes",
        "",
        "| File | Line | Before | After | Confidence |",
        "|------|------|--------|-------|------------|",
    ]

    for p in patches:
        fname = Path(p.file_path).name
        from ranker import format_grade
        grade = format_grade(p.confidence)
        lines.append(
            f"| `{fname}` | {p.line_number} | `{p.old_expression}` | `{p.new_expression}` | {grade} |"
        )

    lines.extend([
        "",
        "---",
        "Generated by [QAPAL](https://github.com/ahmadsharabati/QAPAL) locator intelligence engine.",
    ])

    if failed > 0:
        lines.insert(2, f"\n> **Note:** {failed} patch(es) could not be applied.\n")

    return "\n".join(lines)
