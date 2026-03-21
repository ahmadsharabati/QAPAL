"""
Unit tests for Patch Validator (dry-run mode)
"""

import pytest
from engine.repair.validator import PatchValidator, PatchStatus


VALID_TEST = """import { test, expect } from '@playwright/test';

test('user can log in', async ({ page }) => {
  await page.goto('https://example.com/login');
  await page.getByTestId('email-input').fill('user@example.com');
  await page.getByTestId('password-input').fill('password123');
  await page.getByTestId('login-submit').click();
  await expect(page).toHaveURL('https://example.com/dashboard');
});
"""

INVALID_TEST_UNBALANCED = """import { test, expect } from '@playwright/test';

test('broken test', async ({ page }) => {
  await page.goto('https://example.com');
  await page.getByTestId('submit').click();
"""  # Missing closing braces


@pytest.fixture
def validator():
    return PatchValidator()


class TestDryRunValidation:
    def test_valid_test_passes_dry_run(self, validator):
        result = validator.validate_dry_run(VALID_TEST)
        
        assert result.status == PatchStatus.DRAFT  # Dry run = draft (not actually run)
        assert result.pass_count == 1
        assert result.fail_count == 0
    
    def test_unbalanced_braces_fails(self, validator):
        result = validator.validate_dry_run(INVALID_TEST_UNBALANCED)
        
        assert result.status == PatchStatus.FAILED
        assert result.fail_count == 1
        assert "balanced_braces" in (result.failure_reason or "")
    
    def test_empty_code_fails(self, validator):
        result = validator.validate_dry_run("")
        
        assert result.status == PatchStatus.FAILED
    
    def test_valid_checks_recorded(self, validator):
        result = validator.validate_dry_run(VALID_TEST)
        
        assert len(result.run_results) == 1
        checks = result.run_results[0].get('checks', {})
        assert checks.get('has_test_block') is True
        assert checks.get('has_await') is True
        assert checks.get('has_page') is True


class TestValidatorConfig:
    def test_custom_required_passes(self):
        validator = PatchValidator(required_passes=3, max_attempts=5)
        assert validator.required_passes == 3
        assert validator.max_attempts == 5
    
    def test_custom_timeout(self):
        validator = PatchValidator(timeout_ms=60000)
        assert validator.timeout_ms == 60000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
