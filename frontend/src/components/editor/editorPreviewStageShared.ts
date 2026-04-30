import type { Asset } from '@/api/assets'
import type { PreviewDragState, PreviewDragTransform } from '@/hooks/usePreviewDragWorkflow'
import type { Clip, Shape, TimelineData } from '@/store/projectStore'
import { normalizeTextClip } from '@/utils/textStyle'
import { getInterpolatedTransform } from '@/utils/keyframes'

export interface ActiveClipInfo {
  layerId: string
  clip: Clip
  assetId: string | null
  assetType: string | null
  shape: Shape | null
  transform: {
    x: number
    y: number
    scale: number
    rotation: number
    opacity: number
    width?: number
    height?: number
  }
  locked: boolean
  chromaKey: { enabled: boolean; color: string; similarity: number; blend: number } | null
}

function calculateFadeOpacity(
  timeInClipMs: number,
  durationMs: number,
  fadeInMs: number,
  fadeOutMs: number,
): number {
  let fadeMultiplier = 1

  if (fadeInMs > 0 && timeInClipMs < fadeInMs) {
    fadeMultiplier = Math.min(fadeMultiplier, timeInClipMs / fadeInMs)
  }

  const timeFromEnd = durationMs - timeInClipMs
  if (fadeOutMs > 0 && timeFromEnd < fadeOutMs) {
    fadeMultiplier = Math.min(fadeMultiplier, timeFromEnd / fadeOutMs)
  }

  return Math.max(0, Math.min(1, fadeMultiplier))
}

export function getHandleCursor(rotation: number, handleType: string): string {
  if (handleType === 'arrow-start' || handleType === 'arrow-end') {
    return 'crosshair'
  }
  if (handleType === 'rotate') {
    return 'grab'
  }

  const normalizedRotation = ((rotation % 360) + 360) % 360
  const diagonalCursors = ['nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize']
  const edgeCursors = ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize']
  const cursorIndex = Math.round(normalizedRotation / 45) % 4

  const handleBaseIndex: Record<string, number> = {
    'resize-tl': 0,
    'resize-br': 0,
    'resize-tr': 2,
    'resize-bl': 2,
    'resize-t': 0,
    'resize-b': 0,
    'resize-l': 2,
    'resize-r': 2,
  }

  const isEdgeHandle = ['resize-t', 'resize-b', 'resize-l', 'resize-r'].includes(handleType)
  const baseIndex = handleBaseIndex[handleType] ?? 0
  const adjustedIndex = (baseIndex + cursorIndex) % 4

  return isEdgeHandle ? edgeCursors[adjustedIndex] : diagonalCursors[adjustedIndex]
}

interface BuildActivePreviewClipsArgs {
  assets: Asset[]
  currentTime: number
  dragTransform: PreviewDragTransform | null
  previewDrag: PreviewDragState | null
  timelineData?: TimelineData
  selectedClipId?: string | null
}

export function buildActivePreviewClips({
  assets,
  currentTime,
  dragTransform,
  previewDrag,
  timelineData,
  selectedClipId,
}: BuildActivePreviewClipsArgs): ActiveClipInfo[] {
  if (!timelineData) return []

  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const activeClips: ActiveClipInfo[] = []

  for (let layerIndex = timelineData.layers.length - 1; layerIndex >= 0; layerIndex -= 1) {
    const layer = timelineData.layers[layerIndex]
    if (layer.visible === false) continue

    for (const clip of layer.clips) {
      const endMs = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
      const isSelected = clip.id === selectedClipId
      const inRange = currentTime >= clip.start_ms && (isSelected ? currentTime <= endMs : currentTime < endMs)
      if (!inRange) {
        continue
      }

      const normalizedClip = clip.text_content !== undefined ? normalizeTextClip(clip) : clip

      const asset = normalizedClip.asset_id ? assetById.get(normalizedClip.asset_id) : null
      const timeInClipMs = currentTime - normalizedClip.start_ms
      const interpolated = normalizedClip.keyframes && normalizedClip.keyframes.length > 0
        ? getInterpolatedTransform(normalizedClip, timeInClipMs)
        : {
            x: normalizedClip.transform.x,
            y: normalizedClip.transform.y,
            scale: normalizedClip.transform.scale,
            rotation: normalizedClip.transform.rotation,
            opacity: normalizedClip.effects.opacity,
          }

      let fadeOpacity = interpolated.opacity
      const fadeInMs = normalizedClip.effects.fade_in_ms ?? 0
      const fadeOutMs = normalizedClip.effects.fade_out_ms ?? 0

      if (fadeInMs > 0 && timeInClipMs < fadeInMs) {
        fadeOpacity = interpolated.opacity * (timeInClipMs / fadeInMs)
      }

      const timeFromEnd = normalizedClip.duration_ms - timeInClipMs
      if (fadeOutMs > 0 && timeFromEnd < fadeOutMs) {
        fadeOpacity = interpolated.opacity * (timeFromEnd / fadeOutMs)
      }

      const isDraggingThis = previewDrag?.clipId === normalizedClip.id && dragTransform
      const finalTransform = isDraggingThis
        ? {
            ...interpolated,
            x: dragTransform.x,
            y: dragTransform.y,
            scale: dragTransform.scale,
            rotation: dragTransform.rotation ?? interpolated.rotation,
            opacity: fadeOpacity,
            width: dragTransform.imageWidth,
            height: dragTransform.imageHeight,
          }
        : {
            ...interpolated,
            opacity: fadeOpacity,
            width: (normalizedClip.transform as { width?: number | null }).width ?? undefined,
            height: (normalizedClip.transform as { height?: number | null }).height ?? undefined,
          }

      const finalShape = normalizedClip.shape && isDraggingThis && (dragTransform.shapeWidth || dragTransform.shapeHeight)
        ? {
            ...normalizedClip.shape,
            width: dragTransform.shapeWidth ?? normalizedClip.shape.width,
            height: dragTransform.shapeHeight ?? normalizedClip.shape.height,
          }
        : normalizedClip.shape || null

      let finalOpacity = finalTransform.opacity
      if (normalizedClip.shape && (normalizedClip.fade_in_ms || normalizedClip.fade_out_ms)) {
        finalOpacity = finalTransform.opacity * calculateFadeOpacity(
          timeInClipMs,
          normalizedClip.duration_ms,
          normalizedClip.fade_in_ms || 0,
          normalizedClip.fade_out_ms || 0,
        )
      }

      activeClips.push({
        layerId: layer.id,
        clip: normalizedClip,
        assetId: normalizedClip.asset_id,
        assetType: asset?.type || null,
        shape: finalShape,
        transform: { ...finalTransform, opacity: finalOpacity },
        locked: layer.locked,
        chromaKey: asset?.type === 'video' && normalizedClip.effects.chroma_key?.enabled
          ? {
              enabled: true,
              color: normalizedClip.effects.chroma_key.color || '#00FF00',
              similarity: normalizedClip.effects.chroma_key.similarity ?? 0.05,
              blend: normalizedClip.effects.chroma_key.blend ?? 0,
            }
          : null,
      })
    }
  }

  return activeClips
}
