"""
Patch Validator for QAPAL Repair Engine

Proves patches work by actually running Playwright.
No patch is trusted unless it passes execution.

Validation rules:
- A patch is "validated" only if it passes 2+ consecutive runs
- Failed patches are kept as "draft" with failure reason
- Flaky patches (pass sometimes) are marked as "flaky"
- All results are stored with execution traces
"""

import asyncio
import tempfile
import subprocess
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


# ============================================================================
# Validation Status
# ============================================================================

class PatchStatus(Enum):
    """Status of a validated patch."""
    VALIDATED = "validated"        # Passes 2+ consecutive runs
    DRAFT = "draft"               # Generated but not yet validated
    FAILED = "failed"             # Fails consistently
    FLAKY = "flaky"               # Passes sometimes
    ERROR = "error"               # Couldn't run (env issue)


# ============================================================================
# Validation Result
# ============================================================================

@dataclass
class PatchValidationResult:
    """Result of running a patched test."""
    status: PatchStatus
    
    # Execution details
    total_runs: int = 0
    pass_count: int = 0
    fail_count: int = 0
    
    # Failure details (if failed)
    failure_reason: Optional[str] = None
    failure_output: Optional[str] = None
    
    # Execution traces
    run_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Timing
    total_duration_ms: int = 0


# ============================================================================
# Patch Validator
# ============================================================================

class PatchValidator:
    """
    Validates patches by running them with Playwright.
    """
    
    def __init__(self, 
                 required_passes: int = 2,
                 max_attempts: int = 3,
                 timeout_ms: int = 30000):
        self.required_passes = required_passes
        self.max_attempts = max_attempts
        self.timeout_ms = timeout_ms
    
    def validate(self, patched_code: str, 
                target_url: Optional[str] = None,
                test_dir: Optional[str] = None) -> PatchValidationResult:
        """
        Validate a patched test by running it with Playwright.
        
        Args:
            patched_code: The patched test code
            target_url: URL to test against (optional override)
            test_dir: Directory containing test dependencies (optional)
            
        Returns:
            PatchValidationResult with execution details
        """
        result = PatchValidationResult(status=PatchStatus.DRAFT)
        
        consecutive_passes = 0
        
        for attempt in range(self.max_attempts):
            run_result = self._run_test(patched_code, target_url, test_dir)
            result.run_results.append(run_result)
            result.total_runs += 1
            result.total_duration_ms += run_result.get('duration_ms', 0)
            
            if run_result['passed']:
                result.pass_count += 1
                consecutive_passes += 1
                
                if consecutive_passes >= self.required_passes:
                    result.status = PatchStatus.VALIDATED
                    return result
            else:
                result.fail_count += 1
                consecutive_passes = 0
                result.failure_reason = run_result.get('error', 'Unknown')
                result.failure_output = run_result.get('output', '')
        
        # Classify final status
        if result.pass_count == 0:
            result.status = PatchStatus.FAILED
        elif result.pass_count > 0 and result.fail_count > 0:
            result.status = PatchStatus.FLAKY
        else:
            result.status = PatchStatus.DRAFT
        
        return result
    
    def _run_test(self, code: str, target_url: Optional[str] = None,
                  test_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Run a single test file with Playwright.
        
        Returns dict with: passed, error, output, duration_ms
        """
        import time
        start = time.monotonic()
        
        try:
            # Write patched test to temp file
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.spec.ts',
                dir=test_dir,
                delete=False
            ) as f:
                f.write(code)
                temp_path = f.name
            
            try:
                # Run with npx playwright test
                env = os.environ.copy()
                if target_url:
                    env['BASE_URL'] = target_url
                
                proc = subprocess.run(
                    ['npx', 'playwright', 'test', temp_path, '--reporter=json'],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_ms / 1000,
                    cwd=test_dir or os.getcwd(),
                    env=env,
                )
                
                duration_ms = int((time.monotonic() - start) * 1000)
                
                passed = proc.returncode == 0
                
                return {
                    'passed': passed,
                    'error': proc.stderr if not passed else None,
                    'output': proc.stdout,
                    'duration_ms': duration_ms,
                    'exit_code': proc.returncode,
                }
                
            finally:
                # Clean up temp file
                Path(temp_path).unlink(missing_ok=True)
        
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                'passed': False,
                'error': f'Test timed out after {self.timeout_ms}ms',
                'output': '',
                'duration_ms': duration_ms,
                'exit_code': -1,
            }
        
        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                'passed': False,
                'error': 'npx or playwright not found. Install with: npm install -D @playwright/test',
                'output': '',
                'duration_ms': duration_ms,
                'exit_code': -1,
            }
        
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                'passed': False,
                'error': str(e),
                'output': '',
                'duration_ms': duration_ms,
                'exit_code': -1,
            }
    
    def validate_dry_run(self, patched_code: str) -> PatchValidationResult:
        """
        Dry-run validation: check that code is syntactically valid
        without actually running Playwright.
        
        Useful for unit testing the pipeline without Playwright installed.
        """
        result = PatchValidationResult(status=PatchStatus.DRAFT)
        
        # Basic syntax checks
        checks = [
            ('has_test_block', 'test(' in patched_code or 'test.describe' in patched_code),
            ('has_await', 'await' in patched_code),
            ('has_page', 'page.' in patched_code),
            ('balanced_braces', patched_code.count('{') == patched_code.count('}')),
            ('balanced_parens', patched_code.count('(') == patched_code.count(')')),
            ('no_syntax_error', not any(kw in patched_code for kw in ['undefined', 'NaN'])),
        ]
        
        passed_checks = []
        failed_checks = []
        
        for name, passed in checks:
            if passed:
                passed_checks.append(name)
            else:
                failed_checks.append(name)
        
        result.run_results = [{
            'passed': len(failed_checks) == 0,
            'checks': {name: passed for name, passed in checks},
            'duration_ms': 0,
        }]
        
        result.total_runs = 1
        
        if len(failed_checks) == 0:
            result.pass_count = 1
            result.status = PatchStatus.DRAFT  # Still draft (not actually run)
        else:
            result.fail_count = 1
            result.status = PatchStatus.FAILED
            result.failure_reason = f"Syntax checks failed: {', '.join(failed_checks)}"
        
        return result
