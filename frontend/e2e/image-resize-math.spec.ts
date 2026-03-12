import { expect, test } from '@playwright/test'
import { computeImageResizeRect, resolveImageResizeDominantAxis, resolveImageResizeSnap } from '../src/utils/imageResize'

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

  test('locks the dominant axis from the initial drag delta for Shift-resize', () => {
    const lockedAxis = resolveImageResizeDominantAxis({
      handleType: 'resize-br',
      initialHeight: 720,
      initialWidth: 1280,
      logicalDeltaX: 260,
      logicalDeltaY: 30,
    })

    expect(lockedAxis).toBe('x')

    const resized = computeImageResizeRect({
      dominantAxis: lockedAxis,
      handleType: 'resize-br',
      initialHeight: 720,
      initialWidth: 1280,
      initialX: 0,
      initialY: 0,
      logicalDeltaX: 20,
      logicalDeltaY: 180,
      maintainAspect: true,
    })

    expect(resized.width).toBe(1300)
    expect(resized.height).toBeCloseTo(731.25, 2)
  })

  test('keeps vertical handles locked to the Y axis during Shift-resize', () => {
    const lockedAxis = resolveImageResizeDominantAxis({
      handleType: 'resize-b',
      initialHeight: 720,
      initialWidth: 1280,
      logicalDeltaX: 400,
      logicalDeltaY: 50,
    })

    expect(lockedAxis).toBe('y')

    const resized = computeImageResizeRect({
      dominantAxis: lockedAxis,
      handleType: 'resize-b',
      initialHeight: 720,
      initialWidth: 1280,
      initialX: 0,
      initialY: 0,
      logicalDeltaX: 400,
      logicalDeltaY: 50,
      maintainAspect: true,
    })

    expect(resized.height).toBe(770)
    expect(resized.width).toBeCloseTo(1368.89, 2)
  })

  test('prefers the locked X axis when snap candidates exist on both axes', () => {
    const snap = resolveImageResizeSnap({
      handleType: 'resize-br',
      horizontalSnap: { dist: 6, target: 1280 },
      lockedAxis: 'x',
      verticalSnap: { dist: 2, target: 720 },
    })

    expect(snap).toEqual({ axis: 'x', target: 1280 })
  })

  test('prefers the locked Y axis when snap candidates exist on both axes', () => {
    const snap = resolveImageResizeSnap({
      handleType: 'resize-br',
      horizontalSnap: { dist: 2, target: 1280 },
      lockedAxis: 'y',
      verticalSnap: { dist: 6, target: 720 },
    })

    expect(snap).toEqual({ axis: 'y', target: 720 })
  })
})
