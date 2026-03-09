import { type Page, type Locator } from '@playwright/test'

export class EditorPage {
  readonly page: Page

  // Main layout sections
  readonly header: Locator
  readonly leftPanel: Locator
  readonly previewContainer: Locator
  readonly rightPanel: Locator
  readonly timelineArea: Locator

  // Common elements
  readonly addDropdown: Locator
  readonly playButton: Locator

  constructor(page: Page) {
    this.page = page

    this.header = page.locator('[data-testid="editor-header"]')
    this.leftPanel = page.locator('[data-testid="left-panel"]')
    this.previewContainer = page.locator('[data-testid="preview-container"]')
    this.rightPanel = page.locator('[data-testid="right-panel"]')
    this.timelineArea = page.locator('[data-testid="timeline-area"]')

    this.addDropdown = page.locator('[data-menu-id="add"]')
    this.playButton = page.locator('button[title*="再生"], button[title*="Play"]')
  }

  async waitForReady() {
    await this.page.waitForLoadState('networkidle')
    await this.header.waitFor({ state: 'visible', timeout: 15000 })
    await this.timelineArea.waitFor({ state: 'visible', timeout: 15000 })
  }

  async resetHoverState() {
    await this.page.mouse.move(0, 0)
    await this.page.waitForTimeout(200)
  }

  async getTimelineClips() {
    return this.timelineArea.locator(
      '.bg-purple-600\\/80, .bg-blue-600\\/80, .bg-teal-600\\/80, .bg-yellow-600\\/80, .bg-pink-600\\/80, .bg-green-600\\/80'
    )
  }

  async selectFirstClip() {
    const clips = await this.getTimelineClips()
    const count = await clips.count()
    if (count === 0) return false
    await clips.first().click()
    return true
  }

  private async openAddDropdown() {
    const trigger = this.addDropdown.locator('button').first()
    if (!(await trigger.isVisible({ timeout: 3000 }).catch(() => false))) {
      return false
    }
    await trigger.click()
    await this.page.waitForTimeout(300)
    return true
  }

  async addRectangleShape() {
    if (!(await this.openAddDropdown())) return false
    const rectButton = this.addDropdown.locator('button:has-text("Rectangle"), button:has-text("矩形"), button:has-text("四角")')
    if (await rectButton.isVisible({ timeout: 3000 }).catch(() => false)) {
      await rectButton.click()
      await this.page.waitForTimeout(500)
      return true
    }
    return false
  }

  async addArrowShape() {
    if (!(await this.openAddDropdown())) return false
    const arrowButton = this.page.getByTestId('timeline-add-shape-arrow')
    if (await arrowButton.isVisible({ timeout: 3000 }).catch(() => false)) {
      await arrowButton.click()
      await this.page.waitForTimeout(500)
      return true
    }
    return false
  }

  async addTextClip() {
    if (!(await this.openAddDropdown())) return false
    const textButton = this.addDropdown.locator('button:has-text("Text"), button:has-text("テキスト")')
    if (await textButton.isVisible({ timeout: 3000 }).catch(() => false)) {
      await textButton.click()
      await this.page.waitForTimeout(500)
      return true
    }
    return false
  }

  async movePlayhead(xOffset: number) {
    const timeline = this.timelineArea
    const box = await timeline.boundingBox()
    if (!box) return
    // Click at an offset position on the timeline ruler area
    await this.page.mouse.click(box.x + xOffset, box.y + 20)
    await this.page.waitForTimeout(300)
  }
}
