import { expect, test } from '@playwright/test'
import { computeImageResizeRect } from '../src/utils/imageResize'

test.describe('Image Resize Math', () => {
  test('keeps aspect ratio when Shift-resizing from a corner handle', () => {
    const resized = computeImageResizeRect({
      handleType: 'resize-br',
      initialHeight: 720,
      initialWidth: 1280,
      initialX: 0,
      initialY: 0,
      logicalDeltaX: 220,
      logicalDeltaY: 80,
      maintainAspect: true,
    })

    expect(resized.width).toBeGreaterThan(1280)
    expect(resized.height).toBeGreaterThan(720)
    expect(resized.width / resized.height).toBeCloseTo(1280 / 720, 4)
  })

  test('snaps the image resize handle to the canvas edge without breaking the resized frame', () => {
    const resized = computeImageResizeRect({
      handleType: 'resize-r',
      horizontalEdge: 1280,
      initialHeight: 720,
      initialWidth: 1280,
      initialX: 0,
      initialY: 0,
      logicalDeltaX: 0,
      logicalDeltaY: 0,
      maintainAspect: false,
    })

    expect(resized.width).toBe(1920)
    expect(resized.height).toBe(720)
    expect(resized.x).toBe(320)
    expect(resized.y).toBe(0)
  })
})
