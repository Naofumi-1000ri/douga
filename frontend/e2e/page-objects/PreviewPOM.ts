import { type Page, type Locator } from '@playwright/test'

export class PreviewPOM {
  readonly page: Page
  readonly container: Locator
  readonly canvas: Locator
  readonly transformHandles: Locator

  constructor(page: Page) {
    this.page = page
    this.container = page.locator('[data-testid="preview-container"]')
    this.canvas = this.container.locator('canvas').first()
    this.transformHandles = this.container.locator('[class*="resize-handle"], [class*="cursor-"]')
  }

  async isCanvasVisible() {
    return this.canvas.isVisible({ timeout: 5000 }).catch(() => false)
  }
}
