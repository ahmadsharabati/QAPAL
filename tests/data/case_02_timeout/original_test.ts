import { test, expect } from '@playwright/test';

test('user sees dashboard after login', async ({ page }) => {
  await page.goto('https://app.example.com/login');
  await page.getByLabel('Email').fill('user@example.com');
  await page.getByLabel('Password').fill('secret');
  await page.locator('button.login-btn').click();
  await expect(page).toHaveURL('https://app.example.com/dashboard');
});
