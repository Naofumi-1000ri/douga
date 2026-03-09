import { useCallback, useEffect, useState, type Dispatch, type MouseEvent as ReactMouseEvent, type RefObject, type SetStateAction } from 'react'
import type { Asset } from '@/api/assets'
import type { SelectedClipInfo, SelectedVideoClipInfo } from '@/components/editor/Timeline'
import { getArrowEndpointPositions, getMinimumArrowWidth } from '@/components/editor/shapeGeometry'
import type { Clip, ProjectDetail, TimelineData } from '@/store/projectStore'
import { addKeyframe, getInterpolatedTransform } from '@/utils/keyframes'

export type PreviewDragHandle =
  | 'move'
  | 'resize'
  | 'resize-tl'
  | 'resize-tr'
  | 'resize-bl'
  | 'resize-br'
  | 'resize-t'
  | 'resize-b'
  | 'resize-l'
  | 'resize-r'
  | 'rotate'
  | 'arrow-start'
  | 'arrow-end'
  | 'crop-t'
  | 'crop-r'
  | 'crop-b'
  | 'crop-l'

export interface PreviewDragState {
  type: PreviewDragHandle
  layerId: string
  clipId: string
  startX: number
  startY: number
  initialX: number
  initialY: number
  initialScale: number
  initialRotation?: number
  initialShapeWidth?: number
  initialShapeHeight?: number
  initialVideoWidth?: number
  initialVideoHeight?: number
  initialImageWidth?: number
  initialImageHeight?: number
  isImageClip?: boolean
  initialRotateHandleX?: number
  initialRotateHandleY?: number
  initialArrowStartX?: number
  initialArrowStartY?: number
  initialArrowEndX?: number
  initialArrowEndY?: number
  anchorX?: number
  anchorY?: number
  handleOffsetX?: number
  handleOffsetY?: number
  initialCrop?: { top: number; right: number; bottom: number; left: number }
  mediaWidth?: number
  mediaHeight?: number
}

export interface PreviewDragTransform {
  x: number
  y: number
  scale: number
  rotation?: number
  shapeWidth?: number
  shapeHeight?: number
  imageWidth?: number
  imageHeight?: number
}

export interface PreviewSnapGuide {
  type: 'x' | 'y'
  position: number
}

interface UsePreviewDragWorkflowParams {
  assets: Asset[]
  clearPreview: () => void
  currentProject: ProjectDetail | null
  currentTime: number
  effectivePreviewHeight: number
  projectId?: string
  selectedKeyframeIndex: number | null
  selectedVideoClip: SelectedVideoClipInfo | null
  setSelectedClip: Dispatch<SetStateAction<SelectedClipInfo | null>>
  setSelectedVideoClip: Dispatch<SetStateAction<SelectedVideoClipInfo | null>>
  textFallbackLabel: string
  timelineData?: TimelineData
  undoLabel: string
  updateTimeline: (timeline: TimelineData, label?: string) => void
  videoRefsMap: RefObject<Map<string, HTMLVideoElement>>
}

interface UsePreviewDragWorkflowResult {
  dragCrop: { top: number; right: number; bottom: number; left: number } | null
  dragTransform: PreviewDragTransform | null
  edgeSnapEnabled: boolean
  previewDrag: PreviewDragState | null
  snapGuides: PreviewSnapGuide[]
  toggleEdgeSnapEnabled: () => void
  handlePreviewDragStart: (
    event: ReactMouseEvent,
    type: PreviewDragHandle,
    layerId: string,
    clipId: string,
  ) => void
}

function calcEdgeSnap(
  bbox: { left: number; right: number; top: number; bottom: number; cx: number; cy: number },
  targets: { x: number[]; y: number[] },
  threshold: number,
): { dx: number; dy: number; guides: PreviewSnapGuide[] } {
  let dx = 0
  let dy = 0
  const guides: PreviewSnapGuide[] = []
  const edges = [bbox.left, bbox.cx, bbox.right]
  const verticalEdges = [bbox.top, bbox.cy, bbox.bottom]

  let bestSnapX: { dist: number; offset: number; target: number } | null = null
  for (const edge of edges) {
    for (const target of targets.x) {
      const dist = Math.abs(edge - target)
      if (dist < threshold && (!bestSnapX || dist < bestSnapX.dist)) {
        bestSnapX = { dist, offset: target - edge, target }
      }
    }
  }
  if (bestSnapX) {
    dx = bestSnapX.offset
    guides.push({ type: 'x', position: bestSnapX.target })
  }

  let bestSnapY: { dist: number; offset: number; target: number } | null = null
  for (const edge of verticalEdges) {
    for (const target of targets.y) {
      const dist = Math.abs(edge - target)
      if (dist < threshold && (!bestSnapY || dist < bestSnapY.dist)) {
        bestSnapY = { dist, offset: target - edge, target }
      }
    }
  }
  if (bestSnapY) {
    dy = bestSnapY.offset
    guides.push({ type: 'y', position: bestSnapY.target })
  }

  return { dx, dy, guides }
}

function resetDragCursor(): void {
  document.body.classList.remove('dragging-preview')
  delete document.body.dataset.dragCursor
}

export function usePreviewDragWorkflow({
  assets,
  clearPreview,
  currentProject,
  currentTime,
  effectivePreviewHeight,
  projectId,
  selectedKeyframeIndex,
  selectedVideoClip,
  setSelectedClip,
  setSelectedVideoClip,
  textFallbackLabel,
  timelineData,
  undoLabel,
  updateTimeline,
  videoRefsMap,
}: UsePreviewDragWorkflowParams): UsePreviewDragWorkflowResult {
  const [previewDrag, setPreviewDrag] = useState<PreviewDragState | null>(null)
  const [dragTransform, setDragTransform] = useState<PreviewDragTransform | null>(null)
  const [dragCrop, setDragCrop] = useState<{ top: number; right: number; bottom: number; left: number } | null>(null)
  const [snapGuides, setSnapGuides] = useState<PreviewSnapGuide[]>([])
  const [edgeSnapEnabled, setEdgeSnapEnabled] = useState(true)

  const toggleEdgeSnapEnabled = useCallback(() => {
    setEdgeSnapEnabled((prev) => !prev)
  }, [])

  const handlePreviewDragStart = useCallback((
    event: ReactMouseEvent,
    type: PreviewDragHandle,
    layerId: string,
    clipId: string,
  ) => {
    event.preventDefault()
    event.stopPropagation()

    if (!timelineData) return
    const layer = timelineData.layers.find((candidate) => candidate.id === layerId)
    const clip = layer?.clips.find((candidate) => candidate.id === clipId)
    if (!clip || !layer || layer.locked) return

    const clickedAsset = clip.asset_id ? assets.find((asset) => asset.id === clip.asset_id) : null
    setSelectedVideoClip({
      layerId,
      layerName: layer.name,
      clipId,
      assetId: clip.asset_id || '',
      assetName: clickedAsset?.name || clip.shape?.type || textFallbackLabel,
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
      inPointMs: clip.in_point_ms,
      outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
      transform: clip.transform,
      effects: clip.effects,
      keyframes: clip.keyframes,
      shape: clip.shape,
      fadeInMs: clip.effects.fade_in_ms ?? 0,
      fadeOutMs: clip.effects.fade_out_ms ?? 0,
      textContent: clip.text_content,
      textStyle: clip.text_style,
    })
    setSelectedClip(null)
    clearPreview()

    const timeInClipMs = currentTime - clip.start_ms
    const currentTransform = clip.keyframes && clip.keyframes.length > 0
      ? getInterpolatedTransform(clip, timeInClipMs)
      : { x: clip.transform.x, y: clip.transform.y, scale: clip.transform.scale, rotation: clip.transform.rotation }
    const effectiveScale = clip.shape?.type === 'arrow' ? 1 : currentTransform.scale

    const cx = currentTransform.x
    const cy = currentTransform.y
    const scale = effectiveScale
    const clipAsset = clip.asset_id ? assets.find((asset) => asset.id === clip.asset_id) : null
    const isImageClip = clipAsset?.type === 'image'

    let width = clip.shape?.width || 100
    let height = clip.shape?.height || 100

    if (!clip.shape && clip.asset_id) {
      if (isImageClip) {
        const transformWidth = (clip.transform as { width?: number | null }).width
        const transformHeight = (clip.transform as { height?: number | null }).height

        if (transformWidth && transformHeight) {
          width = transformWidth
          height = transformHeight
        } else if (clipAsset?.width && clipAsset?.height) {
          width = clipAsset.width
          height = clipAsset.height
        } else {
          const imageElement = document.querySelector(`img[data-clip-id="${clipId}"]`) as HTMLImageElement | null
          if (imageElement && imageElement.naturalWidth > 0 && imageElement.naturalHeight > 0) {
            width = imageElement.naturalWidth
            height = imageElement.naturalHeight
          } else {
            width = 400
            height = 300
          }
        }
      } else {
        const videoElement = videoRefsMap.current?.get(clipId)
        const storedWidth = (clip.transform as { width?: number | null }).width
        const storedHeight = (clip.transform as { height?: number | null }).height

        if (videoElement && videoElement.videoWidth > 0) {
          width = videoElement.videoWidth
          height = videoElement.videoHeight
        } else if (storedWidth && storedHeight) {
          width = storedWidth
          height = storedHeight
        } else if (clipAsset?.width && clipAsset?.height) {
          width = clipAsset.width
          height = clipAsset.height
        }
      }
    }

    const halfWidth = (width / 2) * scale
    const halfHeight = (height / 2) * scale

    let anchorX = cx
    let anchorY = cy
    let initialRotateHandleX: number | undefined
    let initialRotateHandleY: number | undefined
    let initialArrowStartX: number | undefined
    let initialArrowStartY: number | undefined
    let initialArrowEndX: number | undefined
    let initialArrowEndY: number | undefined

    if (type === 'rotate') {
      const rotateHandleDistance = halfHeight + 32
      const radians = ((currentTransform.rotation || 0) - 90) * (Math.PI / 180)
      initialRotateHandleX = cx + Math.cos(radians) * rotateHandleDistance
      initialRotateHandleY = cy + Math.sin(radians) * rotateHandleDistance
      anchorX = cx
      anchorY = cy
    } else if ((type === 'arrow-start' || type === 'arrow-end') && clip.shape?.type === 'arrow') {
      const endpoints = getArrowEndpointPositions(
        cx,
        cy,
        clip.shape.width * scale,
        clip.shape.height * scale,
        currentTransform.rotation || 0,
      )
      initialArrowStartX = endpoints.start.x
      initialArrowStartY = endpoints.start.y
      initialArrowEndX = endpoints.end.x
      initialArrowEndY = endpoints.end.y
      if (type === 'arrow-start') {
        anchorX = endpoints.end.x
        anchorY = endpoints.end.y
      } else {
        anchorX = endpoints.start.x
        anchorY = endpoints.start.y
      }
    } else if (type === 'resize-tl') {
      anchorX = cx + halfWidth
      anchorY = cy + halfHeight
    } else if (type === 'resize-tr') {
      anchorX = cx - halfWidth
      anchorY = cy + halfHeight
    } else if (type === 'resize-bl') {
      anchorX = cx + halfWidth
      anchorY = cy - halfHeight
    } else if (type === 'resize-br') {
      anchorX = cx - halfWidth
      anchorY = cy - halfHeight
    } else if (type === 'resize-t') {
      anchorY = cy + halfHeight
    } else if (type === 'resize-b') {
      anchorY = cy - halfHeight
    } else if (type === 'resize-l') {
      anchorX = cx + halfWidth
    } else if (type === 'resize-r') {
      anchorX = cx - halfWidth
    }

    setPreviewDrag({
      type,
      layerId,
      clipId,
      startX: event.clientX,
      startY: event.clientY,
      initialX: currentTransform.x,
      initialY: currentTransform.y,
      initialScale: effectiveScale,
      initialRotation: currentTransform.rotation || 0,
      initialShapeWidth: clip.shape?.width,
      initialShapeHeight: clip.shape?.height,
      initialVideoWidth: !clip.shape && !isImageClip ? width : undefined,
      initialVideoHeight: !clip.shape && !isImageClip ? height : undefined,
      initialImageWidth: isImageClip ? width : undefined,
      initialImageHeight: isImageClip ? height : undefined,
      isImageClip,
      initialRotateHandleX,
      initialRotateHandleY,
      initialArrowStartX,
      initialArrowStartY,
      initialArrowEndX,
      initialArrowEndY,
      anchorX,
      anchorY,
      initialCrop: clip.crop || { top: 0, right: 0, bottom: 0, left: 0 },
      mediaWidth: width,
      mediaHeight: height,
    })

    setDragTransform({
      x: currentTransform.x,
      y: currentTransform.y,
      scale: effectiveScale,
      rotation: currentTransform.rotation || 0,
      shapeWidth: clip.shape?.width,
      shapeHeight: clip.shape?.height,
      imageWidth: isImageClip ? width : undefined,
      imageHeight: isImageClip ? height : undefined,
    })

    document.body.classList.add('dragging-preview')

    const rotation = currentTransform.rotation || 0
    const normalizedRotation = ((rotation % 360) + 360) % 360
    const diagonalCursors = ['nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize']
    const edgeCursors = ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize']
    const cursorIndex = Math.round(normalizedRotation / 45) % 4

    const getRotatedCursor = (handleType: PreviewDragHandle): string => {
      if (handleType === 'move') return 'grabbing'
      if (handleType === 'rotate') return 'grabbing'
      if (handleType === 'resize') return diagonalCursors[cursorIndex]

      const handleBaseIndex: Partial<Record<PreviewDragHandle, number>> = {
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

    document.body.dataset.dragCursor = getRotatedCursor(type)
  }, [assets, clearPreview, currentTime, setSelectedClip, setSelectedVideoClip, textFallbackLabel, timelineData, videoRefsMap])

  const handlePreviewDragMove = useCallback((event: MouseEvent) => {
    if (!previewDrag || !currentProject) return

    const rawDeltaX = event.clientX - previewDrag.startX
    const rawDeltaY = event.clientY - previewDrag.startY
    const containerHeight = effectivePreviewHeight
    const containerWidth = containerHeight * currentProject.width / currentProject.height
    const previewScale = Math.min(containerWidth / currentProject.width, containerHeight / currentProject.height)
    const rawLogicalDeltaX = rawDeltaX / previewScale
    const rawLogicalDeltaY = rawDeltaY / previewScale

    const rotation = previewDrag.initialRotation || 0
    const radians = (-rotation * Math.PI) / 180
    const cos = Math.cos(radians)
    const sin = Math.sin(radians)

    const isResizeOp = !['move', 'rotate', 'arrow-start', 'arrow-end'].includes(previewDrag.type)
    const logicalDeltaX = isResizeOp ? rawLogicalDeltaX * cos - rawLogicalDeltaY * sin : rawLogicalDeltaX
    const logicalDeltaY = isResizeOp ? rawLogicalDeltaX * sin + rawLogicalDeltaY * cos : rawLogicalDeltaY

    let newX = previewDrag.initialX
    let newY = previewDrag.initialY
    let newScale = previewDrag.initialScale
    let newShapeWidth = previewDrag.initialShapeWidth
    let newShapeHeight = previewDrag.initialShapeHeight
    let newImageWidth = previewDrag.initialImageWidth
    let newImageHeight = previewDrag.initialImageHeight

    const { type, isImageClip } = previewDrag
    const initialWidth = previewDrag.initialShapeWidth || previewDrag.initialImageWidth || 100
    const initialHeight = previewDrag.initialShapeHeight || previewDrag.initialImageHeight || 100
    const initialScale = previewDrag.initialScale
    const anchorX = previewDrag.anchorX ?? previewDrag.initialX
    const anchorY = previewDrag.anchorY ?? previewDrag.initialY
    let newRotation = previewDrag.initialRotation || 0

    if (type === 'move') {
      newX = previewDrag.initialX + logicalDeltaX
      newY = previewDrag.initialY + logicalDeltaY
    } else if (type === 'rotate') {
      const initialRotateHandleX = previewDrag.initialRotateHandleX ?? previewDrag.initialX
      const initialRotateHandleY = previewDrag.initialRotateHandleY ?? previewDrag.initialY
      const rotatedHandleX = initialRotateHandleX + rawLogicalDeltaX
      const rotatedHandleY = initialRotateHandleY + rawLogicalDeltaY
      const vectorX = rotatedHandleX - previewDrag.initialX
      const vectorY = rotatedHandleY - previewDrag.initialY
      newRotation = (Math.atan2(vectorY, vectorX) * 180) / Math.PI + 90
    } else if (type === 'arrow-start' || type === 'arrow-end') {
      const initialDraggedX = type === 'arrow-start'
        ? previewDrag.initialArrowStartX ?? previewDrag.initialX
        : previewDrag.initialArrowEndX ?? previewDrag.initialX
      const initialDraggedY = type === 'arrow-start'
        ? previewDrag.initialArrowStartY ?? previewDrag.initialY
        : previewDrag.initialArrowEndY ?? previewDrag.initialY
      const draggedX = initialDraggedX + rawLogicalDeltaX
      const draggedY = initialDraggedY + rawLogicalDeltaY
      const vectorX = type === 'arrow-start' ? anchorX - draggedX : draggedX - anchorX
      const vectorY = type === 'arrow-start' ? anchorY - draggedY : draggedY - anchorY
      const minimumArrowWidth = getMinimumArrowWidth(previewDrag.initialShapeHeight ?? initialHeight)
      const nextWidth = Math.max(minimumArrowWidth, Math.hypot(vectorX, vectorY))
      newShapeWidth = nextWidth
      newX = Math.round((anchorX + draggedX) / 2)
      newY = Math.round((anchorY + draggedY) / 2)
      newRotation = (Math.atan2(vectorY, vectorX) * 180) / Math.PI
    } else if (type === 'resize') {
      const scaleFactor = 1 + (rawDeltaX + rawDeltaY) / 200
      newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale * scaleFactor))
    } else if (type === 'resize-br') {
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initialWidth + logicalDeltaX / initialScale)
        newShapeHeight = Math.max(10, initialHeight + logicalDeltaY / initialScale)
        newX = anchorX + (newShapeWidth / 2) * initialScale
        newY = anchorY + (newShapeHeight / 2) * initialScale
      } else if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth + logicalDeltaX)
        newImageHeight = Math.max(10, initialHeight + logicalDeltaY)
        newX = anchorX + newImageWidth / 2
        newY = anchorY + newImageHeight / 2
      } else {
        const width = previewDrag.initialVideoWidth || 100
        const height = previewDrag.initialVideoHeight || 100
        const deltaScaleX = logicalDeltaX / width
        const deltaScaleY = logicalDeltaY / height
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX + (width / 2) * newScale
        newY = anchorY + (height / 2) * newScale
      }
    } else if (type === 'resize-tl') {
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initialWidth - logicalDeltaX / initialScale)
        newShapeHeight = Math.max(10, initialHeight - logicalDeltaY / initialScale)
        newX = anchorX - (newShapeWidth / 2) * initialScale
        newY = anchorY - (newShapeHeight / 2) * initialScale
      } else if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth - logicalDeltaX)
        newImageHeight = Math.max(10, initialHeight - logicalDeltaY)
        newX = anchorX - newImageWidth / 2
        newY = anchorY - newImageHeight / 2
      } else {
        const width = previewDrag.initialVideoWidth || 100
        const height = previewDrag.initialVideoHeight || 100
        const deltaScaleX = -logicalDeltaX / width
        const deltaScaleY = -logicalDeltaY / height
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX - (width / 2) * newScale
        newY = anchorY - (height / 2) * newScale
      }
    } else if (type === 'resize-tr') {
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initialWidth + logicalDeltaX / initialScale)
        newShapeHeight = Math.max(10, initialHeight - logicalDeltaY / initialScale)
        newX = anchorX + (newShapeWidth / 2) * initialScale
        newY = anchorY - (newShapeHeight / 2) * initialScale
      } else if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth + logicalDeltaX)
        newImageHeight = Math.max(10, initialHeight - logicalDeltaY)
        newX = anchorX + newImageWidth / 2
        newY = anchorY - newImageHeight / 2
      } else {
        const width = previewDrag.initialVideoWidth || 100
        const height = previewDrag.initialVideoHeight || 100
        const deltaScaleX = logicalDeltaX / width
        const deltaScaleY = -logicalDeltaY / height
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX + (width / 2) * newScale
        newY = anchorY - (height / 2) * newScale
      }
    } else if (type === 'resize-bl') {
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initialWidth - logicalDeltaX / initialScale)
        newShapeHeight = Math.max(10, initialHeight + logicalDeltaY / initialScale)
        newX = anchorX - (newShapeWidth / 2) * initialScale
        newY = anchorY + (newShapeHeight / 2) * initialScale
      } else if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth - logicalDeltaX)
        newImageHeight = Math.max(10, initialHeight + logicalDeltaY)
        newX = anchorX - newImageWidth / 2
        newY = anchorY + newImageHeight / 2
      } else {
        const width = previewDrag.initialVideoWidth || 100
        const height = previewDrag.initialVideoHeight || 100
        const deltaScaleX = -logicalDeltaX / width
        const deltaScaleY = logicalDeltaY / height
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX - (width / 2) * newScale
        newY = anchorY + (height / 2) * newScale
      }
    } else if (type === 'resize-r') {
      if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth + logicalDeltaX)
        newX = anchorX + newImageWidth / 2
      } else {
        newShapeWidth = Math.max(10, initialWidth + logicalDeltaX / initialScale)
        newX = anchorX + (newShapeWidth / 2) * initialScale
      }
    } else if (type === 'resize-l') {
      if (isImageClip) {
        newImageWidth = Math.max(10, initialWidth - logicalDeltaX)
        newX = anchorX - newImageWidth / 2
      } else {
        newShapeWidth = Math.max(10, initialWidth - logicalDeltaX / initialScale)
        newX = anchorX - (newShapeWidth / 2) * initialScale
      }
    } else if (type === 'resize-b') {
      if (isImageClip) {
        newImageHeight = Math.max(10, initialHeight + logicalDeltaY)
        newY = anchorY + newImageHeight / 2
      } else {
        newShapeHeight = Math.max(10, initialHeight + logicalDeltaY / initialScale)
        newY = anchorY + (newShapeHeight / 2) * initialScale
      }
    } else if (type === 'resize-t') {
      if (isImageClip) {
        newImageHeight = Math.max(10, initialHeight - logicalDeltaY)
        newY = anchorY - newImageHeight / 2
      } else {
        newShapeHeight = Math.max(10, initialHeight - logicalDeltaY / initialScale)
        newY = anchorY - (newShapeHeight / 2) * initialScale
      }
    } else if (type.startsWith('crop-')) {
      const initialCrop = previewDrag.initialCrop || { top: 0, right: 0, bottom: 0, left: 0 }
      const mediaWidth = previewDrag.mediaWidth || 100
      const mediaHeight = previewDrag.mediaHeight || 100
      const cropDeltaX = logicalDeltaX / (mediaWidth * initialScale)
      const cropDeltaY = logicalDeltaY / (mediaHeight * initialScale)

      const nextCrop = { ...initialCrop }
      if (type === 'crop-t') {
        nextCrop.top = Math.max(0, Math.min(1 - nextCrop.bottom - 0.1, initialCrop.top + cropDeltaY))
      } else if (type === 'crop-b') {
        nextCrop.bottom = Math.max(0, Math.min(1 - nextCrop.top - 0.1, initialCrop.bottom - cropDeltaY))
      } else if (type === 'crop-l') {
        nextCrop.left = Math.max(0, Math.min(1 - nextCrop.right - 0.1, initialCrop.left + cropDeltaX))
      } else if (type === 'crop-r') {
        nextCrop.right = Math.max(0, Math.min(1 - nextCrop.left - 0.1, initialCrop.right - cropDeltaX))
      }
      setDragCrop(nextCrop)
      return
    }

    if (edgeSnapEnabled && (type === 'move' || type.startsWith('resize-'))) {
      const canvasWidth = currentProject.width
      const canvasHeight = currentProject.height

      const isShape = previewDrag.initialShapeWidth !== undefined
      const isImage = previewDrag.isImageClip
      let halfWidth = 50
      let halfHeight = 50

      if (isShape) {
        halfWidth = ((newShapeWidth ?? previewDrag.initialShapeWidth!) / 2) * newScale
        halfHeight = ((newShapeHeight ?? previewDrag.initialShapeHeight!) / 2) * newScale
      } else if (isImage && previewDrag.initialImageWidth && previewDrag.initialImageHeight) {
        halfWidth = (newImageWidth ?? previewDrag.initialImageWidth) / 2
        halfHeight = (newImageHeight ?? previewDrag.initialImageHeight) / 2
      } else if (previewDrag.initialVideoWidth && previewDrag.initialVideoHeight) {
        halfWidth = (previewDrag.initialVideoWidth / 2) * newScale
        halfHeight = (previewDrag.initialVideoHeight / 2) * newScale
      }

      const objectCenterX = canvasWidth / 2 + newX
      const objectCenterY = canvasHeight / 2 + newY
      const bbox = {
        left: objectCenterX - halfWidth,
        right: objectCenterX + halfWidth,
        top: objectCenterY - halfHeight,
        bottom: objectCenterY + halfHeight,
        cx: objectCenterX,
        cy: objectCenterY,
      }

      const snapTargetsX: number[] = [0, canvasWidth / 2, canvasWidth]
      const snapTargetsY: number[] = [0, canvasHeight / 2, canvasHeight]
      const snapLayers = timelineData?.layers ?? []

      for (const layer of snapLayers) {
        if (layer.visible === false) continue
        for (const clip of layer.clips) {
          if (clip.id === previewDrag.clipId) continue
          if (!(currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms)) continue

          const transform = clip.transform
          const otherCenterX = canvasWidth / 2 + transform.x
          const otherCenterY = canvasHeight / 2 + transform.y
          const otherScale = clip.shape?.type === 'arrow' ? 1 : transform.scale
          let otherHalfWidth = 50
          let otherHalfHeight = 50

          if (clip.shape) {
            otherHalfWidth = (clip.shape.width / 2) * otherScale
            otherHalfHeight = (clip.shape.height / 2) * otherScale
          } else {
            const transformWidth = (transform as { width?: number | null }).width
            const transformHeight = (transform as { height?: number | null }).height
            if (transformWidth && transformHeight) {
              otherHalfWidth = transformWidth / 2
              otherHalfHeight = transformHeight / 2
            } else {
              const asset = clip.asset_id ? assets.find((candidate) => candidate.id === clip.asset_id) : null
              if (asset?.width && asset?.height) {
                otherHalfWidth = (asset.width / 2) * otherScale
                otherHalfHeight = (asset.height / 2) * otherScale
              }
            }
          }

          snapTargetsX.push(otherCenterX - otherHalfWidth, otherCenterX, otherCenterX + otherHalfWidth)
          snapTargetsY.push(otherCenterY - otherHalfHeight, otherCenterY, otherCenterY + otherHalfHeight)
        }
      }

      if (type === 'move') {
        const snap = calcEdgeSnap(bbox, { x: snapTargetsX, y: snapTargetsY }, 10)
        newX += snap.dx
        newY += snap.dy
        setSnapGuides(snap.guides)
      } else {
        const threshold = 10
        const guides: PreviewSnapGuide[] = []
        const anchorCenterX = canvasWidth / 2 + (previewDrag.anchorX ?? previewDrag.initialX)
        const anchorCenterY = canvasHeight / 2 + (previewDrag.anchorY ?? previewDrag.initialY)
        const freeRight = ['resize-br', 'resize-tr', 'resize-r'].includes(type)
        const freeLeft = ['resize-tl', 'resize-bl', 'resize-l'].includes(type)
        const freeBottom = ['resize-br', 'resize-bl', 'resize-b'].includes(type)
        const freeTop = ['resize-tl', 'resize-tr', 'resize-t'].includes(type)

        const nearest = (edge: number, targets: number[]) => {
          let best: { dist: number; target: number } | null = null
          for (const target of targets) {
            const distance = Math.abs(edge - target)
            if (distance < threshold && (!best || distance < best.dist)) {
              best = { dist: distance, target }
            }
          }
          return best
        }

        if (freeRight) {
          const snap = nearest(bbox.right, snapTargetsX)
          if (snap) {
            const newEdge = snap.target
            if (isShape) {
              newShapeWidth = Math.max(10, (newEdge - anchorCenterX) / newScale)
              newX = (previewDrag.anchorX ?? previewDrag.initialX) + (newShapeWidth / 2) * newScale
            } else if (isImage) {
              newImageWidth = Math.max(10, newEdge - anchorCenterX)
              newX = (previewDrag.anchorX ?? previewDrag.initialX) + newImageWidth / 2
            } else {
              const width = previewDrag.initialVideoWidth || 100
              newScale = Math.max(0.1, (newEdge - anchorCenterX) / width)
              const height = previewDrag.initialVideoHeight || 100
              newX = (previewDrag.anchorX ?? previewDrag.initialX) + (width / 2) * newScale
              newY = (previewDrag.anchorY ?? previewDrag.initialY) + (height / 2) * newScale
            }
            guides.push({ type: 'x', position: newEdge })
          }
        }

        if (freeLeft) {
          const snap = nearest(bbox.left, snapTargetsX)
          if (snap) {
            const newEdge = snap.target
            if (isShape) {
              newShapeWidth = Math.max(10, (anchorCenterX - newEdge) / newScale)
              newX = (previewDrag.anchorX ?? previewDrag.initialX) - (newShapeWidth / 2) * newScale
            } else if (isImage) {
              newImageWidth = Math.max(10, anchorCenterX - newEdge)
              newX = (previewDrag.anchorX ?? previewDrag.initialX) - newImageWidth / 2
            } else {
              const width = previewDrag.initialVideoWidth || 100
              newScale = Math.max(0.1, (anchorCenterX - newEdge) / width)
              const height = previewDrag.initialVideoHeight || 100
              newX = (previewDrag.anchorX ?? previewDrag.initialX) - (width / 2) * newScale
              newY = (previewDrag.anchorY ?? previewDrag.initialY) - (height / 2) * newScale
            }
            guides.push({ type: 'x', position: newEdge })
          }
        }

        if (freeBottom) {
          const snap = nearest(bbox.bottom, snapTargetsY)
          if (snap) {
            const newEdge = snap.target
            if (isShape) {
              newShapeHeight = Math.max(10, (newEdge - anchorCenterY) / newScale)
              newY = (previewDrag.anchorY ?? previewDrag.initialY) + (newShapeHeight / 2) * newScale
            } else if (isImage) {
              newImageHeight = Math.max(10, newEdge - anchorCenterY)
              newY = (previewDrag.anchorY ?? previewDrag.initialY) + newImageHeight / 2
            } else {
              const height = previewDrag.initialVideoHeight || 100
              newScale = Math.max(0.1, (newEdge - anchorCenterY) / height)
              const width = previewDrag.initialVideoWidth || 100
              newX = (previewDrag.anchorX ?? previewDrag.initialX) + (width / 2) * newScale
              newY = (previewDrag.anchorY ?? previewDrag.initialY) + (height / 2) * newScale
            }
            guides.push({ type: 'y', position: newEdge })
          }
        }

        if (freeTop) {
          const snap = nearest(bbox.top, snapTargetsY)
          if (snap) {
            const newEdge = snap.target
            if (isShape) {
              newShapeHeight = Math.max(10, (anchorCenterY - newEdge) / newScale)
              newY = (previewDrag.anchorY ?? previewDrag.initialY) - (newShapeHeight / 2) * newScale
            } else if (isImage) {
              newImageHeight = Math.max(10, anchorCenterY - newEdge)
              newY = (previewDrag.anchorY ?? previewDrag.initialY) - newImageHeight / 2
            } else {
              const height = previewDrag.initialVideoHeight || 100
              newScale = Math.max(0.1, (anchorCenterY - newEdge) / height)
              const width = previewDrag.initialVideoWidth || 100
              newX = (previewDrag.anchorX ?? previewDrag.initialX) - (width / 2) * newScale
              newY = (previewDrag.anchorY ?? previewDrag.initialY) - (height / 2) * newScale
            }
            guides.push({ type: 'y', position: newEdge })
          }
        }

        setSnapGuides(guides)
      }
    } else if (!edgeSnapEnabled || type === 'resize' || type.startsWith('crop-')) {
      setSnapGuides([])
    }

    setDragTransform({
      x: Math.round(newX),
      y: Math.round(newY),
      scale: newScale,
      rotation: newRotation,
      shapeWidth: newShapeWidth,
      shapeHeight: newShapeHeight,
      imageWidth: newImageWidth,
      imageHeight: newImageHeight,
    })
  }, [assets, currentProject, currentTime, edgeSnapEnabled, effectivePreviewHeight, previewDrag, timelineData])

  const handlePreviewDragEnd = useCallback(() => {
    if (previewDrag && dragCrop && timelineData && projectId) {
      const updatedLayers = timelineData.layers.map((layer) => {
        if (layer.id !== previewDrag.layerId) return layer
        return {
          ...layer,
          clips: layer.clips.map((clip) => {
            if (clip.id !== previewDrag.clipId) return clip
            return {
              ...clip,
              crop: dragCrop,
            }
          }),
        }
      })

      updateTimeline({ ...timelineData, layers: updatedLayers }, undoLabel)

      if (selectedVideoClip) {
        setSelectedVideoClip({
          ...selectedVideoClip,
          crop: dragCrop,
        })
      }

      setPreviewDrag(null)
      setDragCrop(null)
      setSnapGuides([])
      resetDragCursor()
      return
    }

    if (previewDrag && dragTransform && timelineData && projectId) {
      const updatedLayers = timelineData.layers.map((layer) => {
        if (layer.id !== previewDrag.layerId) return layer
        return {
          ...layer,
          clips: layer.clips.map((clip) => {
            if (clip.id !== previewDrag.clipId) return clip

            const updatedShape = clip.shape && (dragTransform.shapeWidth || dragTransform.shapeHeight)
              ? {
                  ...clip.shape,
                  width: dragTransform.shapeWidth ?? clip.shape.width,
                  height: dragTransform.shapeHeight ?? clip.shape.height,
                }
              : clip.shape

            if (selectedKeyframeIndex !== null && clip.keyframes && clip.keyframes[selectedKeyframeIndex]) {
              const updatedKeyframes = clip.keyframes.map((keyframe, index) => {
                if (index !== selectedKeyframeIndex) return keyframe
                return {
                  ...keyframe,
                  transform: {
                    x: dragTransform.x,
                    y: dragTransform.y,
                    scale: dragTransform.scale,
                    rotation: dragTransform.rotation ?? keyframe.transform.rotation,
                  },
                }
              })

              return {
                ...clip,
                keyframes: updatedKeyframes,
                shape: updatedShape,
              }
            }

            const existingWidth = (clip.transform as { width?: number | null }).width
            const existingHeight = (clip.transform as { height?: number | null }).height
            const updatedTransform = {
              ...clip.transform,
              x: dragTransform.x,
              y: dragTransform.y,
              scale: dragTransform.scale,
              rotation: dragTransform.rotation ?? clip.transform.rotation,
              width: dragTransform.imageWidth !== undefined ? dragTransform.imageWidth : (existingWidth ?? null),
              height: dragTransform.imageHeight !== undefined ? dragTransform.imageHeight : (existingHeight ?? null),
            }

            let updatedKeyframes = clip.keyframes
            if (clip.keyframes && clip.keyframes.length > 0) {
              const timeInClipMs = currentTime - clip.start_ms
              if (timeInClipMs >= 0 && timeInClipMs <= clip.duration_ms) {
                const currentInterpolated = getInterpolatedTransform(clip as Clip, timeInClipMs)
                const newKeyframeTransform = {
                  x: dragTransform.x,
                  y: dragTransform.y,
                  scale: dragTransform.scale,
                  rotation: updatedTransform.rotation,
                }
                updatedKeyframes = addKeyframe(clip as Clip, timeInClipMs, newKeyframeTransform, currentInterpolated.opacity)
              }
            }

            return {
              ...clip,
              transform: updatedTransform,
              shape: updatedShape,
              keyframes: updatedKeyframes,
            }
          }),
        }
      })

      updateTimeline({ ...timelineData, layers: updatedLayers }, undoLabel)

      if (selectedVideoClip) {
        const layer = updatedLayers.find((candidate) => candidate.id === previewDrag.layerId)
        const clip = layer?.clips.find((candidate) => candidate.id === previewDrag.clipId)
        if (clip) {
          setSelectedVideoClip({
            ...selectedVideoClip,
            transform: clip.transform,
            keyframes: clip.keyframes,
          })
        }
      }
    }

    setPreviewDrag(null)
    setDragTransform(null)
    setSnapGuides([])
    resetDragCursor()
  }, [currentTime, dragCrop, dragTransform, previewDrag, projectId, selectedKeyframeIndex, selectedVideoClip, setSelectedVideoClip, timelineData, undoLabel, updateTimeline])

  useEffect(() => {
    if (!previewDrag) return

    window.addEventListener('mousemove', handlePreviewDragMove)
    window.addEventListener('mouseup', handlePreviewDragEnd)

    return () => {
      window.removeEventListener('mousemove', handlePreviewDragMove)
      window.removeEventListener('mouseup', handlePreviewDragEnd)
    }
  }, [handlePreviewDragEnd, handlePreviewDragMove, previewDrag])

  return {
    dragCrop,
    dragTransform,
    edgeSnapEnabled,
    previewDrag,
    snapGuides,
    toggleEdgeSnapEnabled,
    handlePreviewDragStart,
  }
}
