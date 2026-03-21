import { test, expect } from '@playwright/test';

test('user can add item to cart', async ({ page }) => {
  await page.goto('https://shop.example.com/products');
  await page.getByTestId('add-to-cart-btn').click();
  await expect(page.getByTestId('cart-count')).toHaveText('1');
});
