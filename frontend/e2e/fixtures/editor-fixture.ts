import { test as base, expect } from '@playwright/test'
import { EditorPage } from '../page-objects/EditorPage'
import { EDITOR_URL } from './test-data'

type EditorFixtures = {
  editorPage: EditorPage
}

export const test = base.extend<EditorFixtures>({
  editorPage: async ({ page }, use) => {
    const editorPage = new EditorPage(page)

    if (EDITOR_URL) {
      // Direct navigation with known project/sequence IDs
      await page.goto(EDITOR_URL)
    } else {
      // Navigate via Dashboard (finds first available project)
      await page.goto('/')
      await page.waitForLoadState('networkidle')

      // Skip if login page appears
      const loginButton = page.locator('button:has-text("ログイン"), button:has-text("Login")')
      if (await loginButton.isVisible({ timeout: 3000 }).catch(() => false)) {
        base.skip(true, 'Authentication required')
        return
      }

      // Click first project card
      const projectCard = page
        .locator('[class*="cursor-pointer"]')
        .filter({ hasText: /TEST|test|プロジェクト/ })
        .first()
      if (await projectCard.isVisible({ timeout: 5000 }).catch(() => false)) {
        await projectCard.click()
      } else {
        // Fallback: click any project card
        const anyCard = page.locator('[class*="cursor-pointer"]').first()
        if (await anyCard.isVisible({ timeout: 3000 }).catch(() => false)) {
          await anyCard.click()
        } else {
          base.skip(true, 'No projects available')
          return
        }
      }
    }

    await editorPage.waitForReady()
    await use(editorPage)
  },
})

export { expect }
