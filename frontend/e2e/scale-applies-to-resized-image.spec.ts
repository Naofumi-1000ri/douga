import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

/**
 * Regression for #194: scale slider / number-input must affect the image
 * preview wrapper transform even when the image clip has an explicit
 * transform.width / transform.height (i.e. hasExplicitSize === true).
 *
 * Pre-fix, the hasExplicitSize branch in EditorPreviewMediaClip.tsx omitted
 * scale() from the wrapper's CSS transform, so changing the Scale property
 * had no visual effect.
 */
test.describe('Scale applies to image preview wrapper (issue #194)', () => {
  // Helper: get the CSS transform string of the wrapper div that contains the
  // image element with a given data-clip-id attribute.
  async function getImageWrapperTransform(page: import('@playwright/test').Page, clipId: string): Promise<string> {
    return page.evaluate((id) => {
      const img = document.querySelector(`img[data-clip-id="${id}"]`)
      if (!img) throw new Error(`img[data-clip-id="${id}"] not found`)
      // The wrapper is the grandparent: img → div.relative → div.absolute
      const wrapper = img.parentElement?.parentElement
      if (!wrapper) throw new Error('wrapper div not found')
      return (wrapper as HTMLElement).style.transform
    }, clipId)
  }

  test('scale change is reflected in wrapper transform (no explicit size)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Drop the image asset onto layer-1.
    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    // Select the clip.
    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // Retrieve the clip id from the timeline element's data-testid.
    const clipTestId = await clip.getAttribute('data-testid') ?? ''
    const clipId = clipTestId.replace('timeline-video-clip-', '')

    // Verify initial scale=1 is present in the transform.
    const initialTransform = await getImageWrapperTransform(page, clipId)
    expect(initialTransform).toContain('scale(1)')

    // Change scale to 50% via the number input.
    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toHaveValue('100')
    await scaleInput.fill('50')
    await scaleInput.press('Tab')

    // The wrapper transform must now include scale(0.5).
    const updatedTransform = await getImageWrapperTransform(page, clipId)
    expect(updatedTransform).toContain('scale(0.5)')
  })

  test('scale change is reflected in wrapper transform when explicit size is set (hasExplicitSize)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    // Seed the timeline with a clip that already has transform.width / transform.height
    // so that hasExplicitSize === true from the very first render.
    const clipId = 'clip-explicit-size'
    const seededClip = {
      id: clipId,
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 5000,
      in_point_ms: 0,
      out_point_ms: null,
      transform: {
        x: 0,
        y: 0,
        width: 400,
        height: 300,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }
    // Inject the clip into the seeded sequence so GET /sequences/:id returns it.
    const seq = mock.sequences[mock.sequenceId]
    seq.timeline_data.layers[0].clips.push(seededClip as never)
    seq.timeline_data.duration_ms = 5000
    seq.duration_ms = 5000
    // Keep project detail in sync.
    mock.projectDetails[mock.projectId].timeline_data = JSON.parse(JSON.stringify(seq.timeline_data))
    mock.projectDetails[mock.projectId].duration_ms = 5000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Select the seeded clip from the timeline.
    const timelineClip = page.getByTestId(`timeline-video-clip-${clipId}`)
    await timelineClip.click()

    // Confirm scale input starts at 100.
    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toHaveValue('100')

    // Confirm wrapper initially has scale(1).
    const initialTransform = await getImageWrapperTransform(page, clipId)
    expect(initialTransform).toContain('scale(1)')

    // Change scale to 50% via the number input.
    await scaleInput.fill('50')
    await scaleInput.press('Tab')

    // Pre-fix: the hasExplicitSize branch omitted scale() entirely, so this
    // assertion would FAIL on old code. Post-fix, scale(0.5) must appear.
    const updatedTransform = await getImageWrapperTransform(page, clipId)
    expect(updatedTransform).toContain('scale(0.5)')
  })
})
