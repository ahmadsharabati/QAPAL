import { test, expect } from '@playwright/test';

test('user clicks save button', async ({ page }) => {
  await page.goto('https://app.example.com/settings');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Settings saved')).toBeVisible();
});
