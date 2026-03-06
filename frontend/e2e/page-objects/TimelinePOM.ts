import { type Page, type Locator } from '@playwright/test'

export class TimelinePOM {
  readonly page: Page
  readonly container: Locator
  readonly tracks: Locator
  readonly playhead: Locator
  readonly clipElements: Locator

  constructor(page: Page) {
    this.page = page
    this.container = page.locator('[data-testid="timeline-area"]')
    this.tracks = this.container.locator('.h-12.border-b')
    this.playhead = this.container.locator('.bg-red-500, [class*="playhead"]')
    this.clipElements = this.container.locator(
      '.bg-purple-600\\/80, .bg-blue-600\\/80, .bg-teal-600\\/80, .bg-yellow-600\\/80, .bg-pink-600\\/80, .bg-green-600\\/80'
    )
  }

  async getClipCount() {
    return this.clipElements.count()
  }

  async clickClip(index: number) {
    await this.clipElements.nth(index).click()
  }

  async getTrackCount() {
    return this.tracks.count()
  }
}
