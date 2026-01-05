import { test, expect } from '@playwright/test'

test.describe('Smoke Test', () => {
  test('should load the application', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')

    // The app should be visible
    await expect(page.locator('body')).toBeVisible()
  })
})
