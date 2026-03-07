import type { Asset } from '@/api/assets'
import type { PreviewDragState, PreviewDragTransform } from '@/hooks/usePreviewDragWorkflow'
import type { Clip, Shape, TimelineData } from '@/store/projectStore'
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

export const DEFAULT_TEXT_STYLE = {
  fontFamily: 'Noto Sans JP',
  fontSize: 48,
  fontWeight: 'bold' as const,
  fontStyle: 'normal' as const,
  color: '#ffffff',
  backgroundColor: '#000000',
  backgroundOpacity: 0.4,
  textAlign: 'center' as const,
  verticalAlign: 'middle' as const,
  lineHeight: 1.4,
  letterSpacing: 0,
  strokeColor: '#000000',
  strokeWidth: 2,
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

export function getTextBackgroundColor(backgroundColor: string, backgroundOpacity: number) {
  if (backgroundColor === 'transparent' || backgroundOpacity === 0) return 'transparent'
  const hex = backgroundColor.replace('#', '')
  const r = parseInt(hex.substring(0, 2), 16)
  const g = parseInt(hex.substring(2, 4), 16)
  const b = parseInt(hex.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${backgroundOpacity})`
}

interface BuildActivePreviewClipsArgs {
  assets: Asset[]
  currentTime: number
  dragTransform: PreviewDragTransform | null
  previewDrag: PreviewDragState | null
  timelineData?: TimelineData
}

export function buildActivePreviewClips({
  assets,
  currentTime,
  dragTransform,
  previewDrag,
  timelineData,
}: BuildActivePreviewClipsArgs): ActiveClipInfo[] {
  if (!timelineData) return []

  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const activeClips: ActiveClipInfo[] = []

  for (let layerIndex = timelineData.layers.length - 1; layerIndex >= 0; layerIndex -= 1) {
    const layer = timelineData.layers[layerIndex]
    if (layer.visible === false) continue

    for (const clip of layer.clips) {
      if (!(currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0))) {
        continue
      }

      const asset = clip.asset_id ? assetById.get(clip.asset_id) : null
      const timeInClipMs = currentTime - clip.start_ms
      const interpolated = clip.keyframes && clip.keyframes.length > 0
        ? getInterpolatedTransform(clip, timeInClipMs)
        : {
            x: clip.transform.x,
            y: clip.transform.y,
            scale: clip.transform.scale,
            rotation: clip.transform.rotation,
            opacity: clip.effects.opacity,
          }

      let fadeOpacity = interpolated.opacity
      const fadeInMs = clip.effects.fade_in_ms ?? 0
      const fadeOutMs = clip.effects.fade_out_ms ?? 0

      if (fadeInMs > 0 && timeInClipMs < fadeInMs) {
        fadeOpacity = interpolated.opacity * (timeInClipMs / fadeInMs)
      }

      const timeFromEnd = clip.duration_ms - timeInClipMs
      if (fadeOutMs > 0 && timeFromEnd < fadeOutMs) {
        fadeOpacity = interpolated.opacity * (timeFromEnd / fadeOutMs)
      }

      const isDraggingThis = previewDrag?.clipId === clip.id && dragTransform
      const finalTransform = isDraggingThis
        ? {
            ...interpolated,
            x: dragTransform.x,
            y: dragTransform.y,
            scale: dragTransform.scale,
            opacity: fadeOpacity,
            width: dragTransform.imageWidth,
            height: dragTransform.imageHeight,
          }
        : {
            ...interpolated,
            opacity: fadeOpacity,
            width: (clip.transform as { width?: number | null }).width ?? undefined,
            height: (clip.transform as { height?: number | null }).height ?? undefined,
          }

      const finalShape = clip.shape && isDraggingThis && (dragTransform.shapeWidth || dragTransform.shapeHeight)
        ? {
            ...clip.shape,
            width: dragTransform.shapeWidth ?? clip.shape.width,
            height: dragTransform.shapeHeight ?? clip.shape.height,
          }
        : clip.shape || null

      let finalOpacity = finalTransform.opacity
      if (clip.shape && (clip.fade_in_ms || clip.fade_out_ms)) {
        finalOpacity = finalTransform.opacity * calculateFadeOpacity(
          timeInClipMs,
          clip.duration_ms,
          clip.fade_in_ms || 0,
          clip.fade_out_ms || 0,
        )
      }

      activeClips.push({
        layerId: layer.id,
        clip,
        assetId: clip.asset_id,
        assetType: asset?.type || null,
        shape: finalShape,
        transform: { ...finalTransform, opacity: finalOpacity },
        locked: layer.locked,
        chromaKey: asset?.type === 'video' && clip.effects.chroma_key?.enabled
          ? {
              enabled: true,
              color: clip.effects.chroma_key.color || '#00FF00',
              similarity: clip.effects.chroma_key.similarity ?? 0.05,
              blend: clip.effects.chroma_key.blend ?? 0,
            }
          : null,
      })
    }
  }

  return activeClips
}
