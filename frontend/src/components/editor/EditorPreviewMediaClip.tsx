import { useEffect, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type MutableRefObject } from 'react'
import { useTranslation } from 'react-i18next'
import type { Asset } from '@/api/assets'
import { type ActiveClipInfo, getHandleCursor } from '@/components/editor/editorPreviewStageShared'
import type { PreviewDragHandle, PreviewDragState } from '@/hooks/usePreviewDragWorkflow'
import type { Clip } from '@/store/projectStore'

interface ChromaKeyCanvasProps {
  clipId: string
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
  chromaKey: { enabled: boolean; color: string; similarity: number; blend: number }
  isPlaying: boolean
  crop?: Clip['crop']
}

interface EditorPreviewMediaClipProps {
  activeClip: ActiveClipInfo | null
  asset: Asset
  clip: Clip
  chromaRenderOverlay: string | null
  chromaRenderOverlayDims: { width: number; height: number } | null
  dragCrop: Clip['crop'] | null
  handlePreviewDragStart: (
    event: ReactMouseEvent,
    type: PreviewDragHandle,
    layerId: string,
    clipId: string,
  ) => void
  invalidateAssetUrl: (assetId: string) => void
  isDragging: boolean
  isPlaying: boolean
  isSelected: boolean
  previewDrag: PreviewDragState | null
  syncVideoToTimelinePosition: (
    video: HTMLVideoElement,
    clip: Pick<Clip, 'start_ms' | 'duration_ms' | 'in_point_ms' | 'speed' | 'freeze_frame_ms'>,
  ) => void
  url: string
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
  zIndex: number
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
  return result
    ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16),
      }
    : { r: 0, g: 255, b: 0 }
}

function getCropMetrics(crop?: Clip['crop'] | null) {
  const cropT = (crop?.top || 0) * 100
  const cropR = (crop?.right || 0) * 100
  const cropB = (crop?.bottom || 0) * 100
  const cropL = (crop?.left || 0) * 100
  const centerX = cropL + (100 - cropL - cropR) / 2
  const centerY = cropT + (100 - cropT - cropB) / 2
  return { cropT, cropR, cropB, cropL, centerX, centerY }
}

function ChromaKeyCanvas({ clipId, videoRefsMap, chromaKey, isPlaying, crop }: ChromaKeyCanvasProps) {
  const { t } = useTranslation('editor')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animationFrameRef = useRef<number | null>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const [corsError, setCorsError] = useState(false)

  useEffect(() => {
    const video = videoRefsMap.current.get(clipId)
    const canvas = canvasRef.current
    if (!video || !canvas) return

    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return

    const keyColor = hexToRgb(chromaKey.color)
    const similarity = chromaKey.similarity
    const blend = chromaKey.blend
    const maxDist = Math.sqrt(3) * 255
    const isGreenKey = keyColor.g > keyColor.r && keyColor.g > keyColor.b
    const isBlueKey = keyColor.b > keyColor.r && keyColor.b > keyColor.g

    const processFrame = () => {
      if (!video || video.videoWidth === 0 || video.videoHeight === 0) {
        animationFrameRef.current = requestAnimationFrame(processFrame)
        return
      }

      if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        setDimensions({ width: video.videoWidth, height: video.videoHeight })
      }

      try {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const data = imageData.data

        for (let index = 0; index < data.length; index += 4) {
          const r = data[index]
          const g = data[index + 1]
          const b = data[index + 2]

          const distance = Math.sqrt((r - keyColor.r) ** 2 + (g - keyColor.g) ** 2 + (b - keyColor.b) ** 2)
          const normalizedDist = distance / maxDist

          if (normalizedDist < similarity) {
            data[index + 3] = 0
          } else if (blend > 0.0001 && normalizedDist < similarity + blend) {
            if (isGreenKey) {
              data[index + 1] = Math.min(data[index + 1], Math.max(data[index], data[index + 2]))
            } else if (isBlueKey) {
              data[index + 2] = Math.min(data[index + 2], Math.max(data[index], data[index + 1]))
            }
            const alpha = ((normalizedDist - similarity) / blend) * 255
            data[index + 3] = Math.min(255, Math.max(0, Math.round(alpha)))
          }
        }

        ctx.putImageData(imageData, 0, 0)
      } catch (error) {
        if (error instanceof DOMException && error.name === 'SecurityError') {
          console.warn('[ChromaKey] CORS error - video source does not allow pixel access')
          setCorsError(true)
          return
        }
        throw error
      }

      animationFrameRef.current = requestAnimationFrame(processFrame)
    }

    setCorsError(false)
    processFrame()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [clipId, videoRefsMap, chromaKey, isPlaying])

  if (corsError) {
    return (
      <div className="flex items-center justify-center bg-gray-800 text-gray-400 text-xs p-4">
        <span>{t('editor.chromaKeyNote')}</span>
      </div>
    )
  }

  const clipPath = crop
    ? `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
    : undefined

  return (
    <canvas
      ref={canvasRef}
      className="block max-w-none pointer-events-none"
      style={{
        width: dimensions.width > 0 ? dimensions.width : 'auto',
        height: dimensions.height > 0 ? dimensions.height : 'auto',
        clipPath,
      }}
    />
  )
}

export default function EditorPreviewMediaClip({
  activeClip,
  asset,
  clip,
  chromaRenderOverlay,
  chromaRenderOverlayDims,
  dragCrop,
  handlePreviewDragStart,
  invalidateAssetUrl,
  isDragging,
  isPlaying,
  isSelected,
  previewDrag,
  syncVideoToTimelinePosition,
  url,
  videoRefsMap,
  zIndex,
}: EditorPreviewMediaClipProps) {
  const { t } = useTranslation('editor')
  const assetId = clip.asset_id
  if (!assetId) return null

  const isActive = activeClip !== null
  const effectiveClip = activeClip?.clip ?? clip
  const crop = isActive && previewDrag?.clipId === clip.id && dragCrop ? dragCrop : effectiveClip.crop
  const { cropT, cropR, cropB, cropL, centerX, centerY } = getCropMetrics(crop)
  const inactiveWrapperStyle: CSSProperties = {
    opacity: 0,
    pointerEvents: 'none',
    zIndex: -1,
    top: 0,
    left: 0,
  }

  if (asset.type === 'image') {
    const imageWidth = activeClip?.transform.width
    const imageHeight = activeClip?.transform.height
    const hasExplicitSize = isActive && typeof imageWidth === 'number' && typeof imageHeight === 'number'
    const wrapperStyle: CSSProperties = isActive && activeClip
      ? {
          top: '50%',
          left: '50%',
          transform: hasExplicitSize
            ? `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) rotate(${activeClip.transform.rotation}deg)`
            : `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
          opacity: activeClip.transform.opacity,
          zIndex,
          transformOrigin: 'center center',
        }
      : inactiveWrapperStyle

    return (
      <div className="absolute" style={wrapperStyle}>
        <div className="relative" style={{ userSelect: 'none' }}>
          <img
            src={url}
            alt=""
            data-clip-id={clip.id}
            data-asset-id={assetId}
            data-active={isActive ? 'true' : 'false'}
            className="block max-w-none pointer-events-none"
            style={{
              ...(hasExplicitSize ? { width: imageWidth, height: imageHeight } : {}),
              clipPath: crop
                ? `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
                : undefined,
            }}
            draggable={false}
            onError={() => invalidateAssetUrl(assetId)}
          />
          {isActive && activeClip && (
            <div
              className="absolute"
              style={{
                top: `${cropT}%`,
                left: `${cropL}%`,
                right: `${cropR}%`,
                bottom: `${cropB}%`,
                cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
              }}
              onMouseDown={(event) => handlePreviewDragStart(event, 'move', activeClip.layerId, activeClip.clip.id)}
            />
          )}
          {isActive && activeClip && isSelected && !activeClip.locked && (
            <>
              <div className="absolute pointer-events-none border-2 border-primary-500" style={{ top: `${cropT}%`, left: `${cropL}%`, right: `${cropR}%`, bottom: `${cropB}%` }} />
              <div className="absolute pointer-events-none" style={{ top: `calc(${cropT}% - 32px)`, left: `${centerX}%`, width: 2, height: 24, backgroundColor: '#60a5fa', transform: 'translateX(-50%)' }} />
              <div
                data-testid="preview-rotate-handle"
                className="absolute w-5 h-5 rounded-full bg-amber-400 border-2 border-white shadow"
                style={{ top: `calc(${cropT}% - 40px)`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'rotate') }}
                onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'rotate', activeClip.layerId, activeClip.clip.id) }}
              />
              <div data-testid="preview-image-resize-tl" className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ top: `${cropT}%`, left: `${cropL}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tl') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tl', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-tr" className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ top: `${cropT}%`, right: `${cropR}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tr') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tr', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-bl" className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ bottom: `${cropB}%`, left: `${cropL}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-bl') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-bl', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-br" className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ bottom: `${cropB}%`, right: `${cropR}%`, transform: 'translate(50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-br') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-br', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-t" className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm" style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-t') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-t', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-b" className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm" style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-b') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-b', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-l" className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm" style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-l') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-l', activeClip.layerId, activeClip.clip.id) }} />
              <div data-testid="preview-image-resize-r" className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm" style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-r') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-r', activeClip.layerId, activeClip.clip.id) }} />
              <div className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: 'ns-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-t', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropTopTitle')} />
              <div className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: 'ns-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-b', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropBottomTitle')} />
              <div className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: 'ew-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-l', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropLeftTitle')} />
              <div className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: 'ew-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-r', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropRightTitle')} />
            </>
          )}
        </div>
      </div>
    )
  }

  if (asset.type !== 'video') {
    return null
  }

  const chromaKeyEnabled = isActive && activeClip?.chromaKey?.enabled
  const wrapperStyle: CSSProperties = isActive && activeClip
    ? {
        top: '50%',
        left: '50%',
        transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
        opacity: activeClip.transform.opacity,
        zIndex,
        transformOrigin: 'center center',
      }
    : inactiveWrapperStyle

  return (
    <div className="absolute" style={wrapperStyle}>
      <div className="relative" style={{ userSelect: 'none' }}>
        <video
          ref={(element) => {
            if (element) videoRefsMap.current.set(clip.id, element)
            else videoRefsMap.current.delete(clip.id)
          }}
          src={url}
          crossOrigin="anonymous"
          data-clip-id={clip.id}
          data-asset-id={assetId}
          data-active={isActive ? 'true' : 'false'}
          className="block max-w-none pointer-events-none"
          style={{
            visibility: chromaKeyEnabled ? 'hidden' : 'visible',
            position: chromaKeyEnabled ? 'absolute' : 'relative',
            clipPath: crop
              ? `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
              : undefined,
          }}
          muted
          playsInline
          preload="auto"
          onError={() => invalidateAssetUrl(assetId)}
          onLoadedMetadata={(event) => {
            syncVideoToTimelinePosition(event.currentTarget, clip)
          }}
        />
        {chromaKeyEnabled && activeClip?.chromaKey && (
          <ChromaKeyCanvas
            clipId={clip.id}
            videoRefsMap={videoRefsMap}
            chromaKey={activeClip.chromaKey}
            isPlaying={isPlaying}
            crop={crop}
          />
        )}
        {isActive && chromaRenderOverlay && isSelected && chromaKeyEnabled && chromaRenderOverlayDims && (
          <div
            className="absolute pointer-events-none"
            style={{
              top: 0,
              left: 0,
              width: chromaRenderOverlayDims.width,
              height: chromaRenderOverlayDims.height,
              zIndex: 10,
            }}
          >
            <img
              src={chromaRenderOverlay}
              alt="FFmpeg render overlay"
              className="block pointer-events-none"
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'fill',
                clipPath: crop
                  ? `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
                  : undefined,
              }}
            />
            <div className="absolute top-2 left-2 px-2 py-1 text-xs font-bold rounded shadow-lg bg-orange-500 text-white" style={{ zIndex: 11 }}>
              {t('editor.FFmpegResult')}
            </div>
          </div>
        )}
        {isActive && activeClip && (
          <div
            className="absolute"
            style={{
              top: `${cropT}%`,
              left: `${cropL}%`,
              right: `${cropR}%`,
              bottom: `${cropB}%`,
              cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
            }}
            onMouseDown={(event) => handlePreviewDragStart(event, 'move', activeClip.layerId, activeClip.clip.id)}
          />
        )}
        {isActive && activeClip && isSelected && !activeClip.locked && (
          <>
            <div className="absolute pointer-events-none border-2 border-primary-500" style={{ top: `${cropT}%`, left: `${cropL}%`, right: `${cropR}%`, bottom: `${cropB}%` }} />
            <div className="absolute pointer-events-none" style={{ top: `calc(${cropT}% - 32px)`, left: `${centerX}%`, width: 2, height: 24, backgroundColor: '#60a5fa', transform: 'translateX(-50%)' }} />
            <div
              data-testid="preview-rotate-handle"
              className="absolute w-5 h-5 rounded-full bg-amber-400 border-2 border-white shadow"
              style={{ top: `calc(${cropT}% - 40px)`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'rotate') }}
              onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'rotate', activeClip.layerId, activeClip.clip.id) }}
            />
            <div className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ top: `${cropT}%`, left: `${cropL}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tl') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tl', activeClip.layerId, activeClip.clip.id) }} />
            <div className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ top: `${cropT}%`, right: `${cropR}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tr') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tr', activeClip.layerId, activeClip.clip.id) }} />
            <div className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ bottom: `${cropB}%`, left: `${cropL}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-bl') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-bl', activeClip.layerId, activeClip.clip.id) }} />
            <div className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm" style={{ bottom: `${cropB}%`, right: `${cropR}%`, transform: 'translate(50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-br') }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-br', activeClip.layerId, activeClip.clip.id) }} />
            <div className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: 'ns-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-t', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropTopTitle')} />
            <div className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: 'ns-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-b', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropBottomTitle')} />
            <div className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: 'ew-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-l', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropLeftTitle')} />
            <div className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100" style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: 'ew-resize' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'crop-r', activeClip.layerId, activeClip.clip.id) }} title={t('editor.cropRightTitle')} />
          </>
        )}
      </div>
    </div>
  )
}
