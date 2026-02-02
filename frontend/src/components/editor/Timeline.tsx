import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import type { TimelineData, AudioClip, AudioTrack, Clip, Keyframe, ShapeType, Shape, ClipGroup, TextStyle, Layer } from '@/store/projectStore'
import { useProjectStore } from '@/store/projectStore'
import { v4 as uuidv4 } from 'uuid'
import { transcriptionApi, type Transcription } from '@/api/transcription'
import { assetsApi } from '@/api/assets'
import { addVolumeKeyframe } from '@/utils/volumeKeyframes'
import TimelineContextMenu from './timeline/TimelineContextMenu'
import ViewportBar from './timeline/ViewportBar'
import VideoLayers from './timeline/VideoLayers'
import AudioTracks from './timeline/AudioTracks'
import { useTimelineDrag } from './timeline/useTimelineDrag'
import type {
  TimelineContextMenuState,
} from './timeline/types'

export interface SelectedClipInfo {
  trackId: string
  trackType: string
  clipId: string
  assetId: string
  assetName: string
  startMs: number
  durationMs: number
  volume: number
  fadeInMs: number
  fadeOutMs: number
}

export interface SelectedVideoClipInfo {
  layerId: string
  layerName: string
  clipId: string
  assetId: string | null
  assetName: string
  startMs: number
  durationMs: number
  inPointMs: number
  transform: Clip['transform']
  effects: Clip['effects']
  keyframes?: Keyframe[]
  shape?: Shape
  crop?: Clip['crop']
  textContent?: string
  textStyle?: TextStyle
  fadeInMs?: number   // Fade in duration in milliseconds
  fadeOutMs?: number  // Fade out duration in milliseconds
}

interface TimelineProps {
  timeline: TimelineData
  projectId: string
  assets: Array<{
    id: string
    name: string
    type: string
    subtype?: string
    storage_url: string
    duration_ms: number | null
    width?: number | null
    height?: number | null
    chroma_key_color?: string | null
  }>
  currentTimeMs?: number
  isPlaying?: boolean
  onClipSelect?: (clip: SelectedClipInfo | null) => void
  onVideoClipSelect?: (clip: SelectedVideoClipInfo | null) => void
  onSeek?: (timeMs: number) => void
  selectedKeyframeIndex?: number | null
  onKeyframeSelect?: (clipId: string, keyframeIndex: number | null) => void
  unmappedAssetIds?: Set<string>
}

export default function Timeline({ timeline, projectId, assets, currentTimeMs = 0, isPlaying = false, onClipSelect, onVideoClipSelect, onSeek, selectedKeyframeIndex, onKeyframeSelect, unmappedAssetIds = new Set() }: TimelineProps) {
  const [zoom, setZoom] = useState(1)
  const [selectedClip, setSelectedClip] = useState<{ trackId: string; clipId: string } | null>(null)
  const [selectedVideoClip, setSelectedVideoClip] = useState<{ layerId: string; clipId: string } | null>(null)
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null) // Selected layer (for shape placement)
  const [dragOverTrack, setDragOverTrack] = useState<string | null>(null)
  const [dragOverLayer, setDragOverLayer] = useState<string | null>(null)
  // Drop preview state for showing where asset will be placed
  const [dropPreview, setDropPreview] = useState<{
    layerId: string
    timeMs: number
    durationMs: number
  } | null>(null)
  const [snapLineMs, setSnapLineMs] = useState<number | null>(null) // Snap line position in ms
  const [isSnapEnabled, setIsSnapEnabled] = useState(true) // Snap on/off state
  const [isDraggingPlayhead, setIsDraggingPlayhead] = useState(false)
  const [editingLayerId, setEditingLayerId] = useState<string | null>(null) // Layer being renamed
  const [editingLayerName, setEditingLayerName] = useState('')
  const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null) // Layer being dragged for reorder
  const [dropTargetIndex, setDropTargetIndex] = useState<number | null>(null) // Index where layer will be dropped
  // Multi-selection state
  const [selectedVideoClips, setSelectedVideoClips] = useState<Set<string>>(new Set())
  const [selectedAudioClips, setSelectedAudioClips] = useState<Set<string>>(new Set())
  // State for dragging asset over new layer drop zone
  const [isDraggingNewLayer, setIsDraggingNewLayer] = useState(false)
  // Loading state for audio extraction
  const [isExtractingAudio, setIsExtractingAudio] = useState(false)
  // Master mute state for all audio tracks
  const [masterMuted, setMasterMuted] = useState(false)
  // Transcription / AI analysis state
  const [transcription, setTranscription] = useState<Transcription | null>(null)
  const [isTranscribing, _setIsTranscribing] = useState(false)
  const [showTranscriptionPanel, setShowTranscriptionPanel] = useState(false)
  // Layer heights state (persisted to localStorage)
  const [layerHeights, setLayerHeights] = useState<Record<string, number>>(() => {
    try {
      const saved = localStorage.getItem(`timeline-layer-heights-${projectId}`)
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  })
  const [resizingLayerId, setResizingLayerId] = useState<string | null>(null)
  const resizeStartY = useRef<number>(0)
  const resizeStartHeight = useRef<number>(0)
  const DEFAULT_LAYER_HEIGHT = 48 // Default height for video layers (h-12 = 48px)
  const MIN_LAYER_HEIGHT = 32
  const MAX_LAYER_HEIGHT = 200
  // Track header width state (resizable)
  const [headerWidth, setHeaderWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(`timeline-header-width-${projectId}`)
      return saved ? parseInt(saved, 10) : 192 // Default w-48 = 192px
    } catch {
      return 192
    }
  })
  const [isResizingHeader, setIsResizingHeader] = useState(false)
  const headerResizeStartX = useRef<number>(0)
  const headerResizeStartWidth = useRef<number>(0)
  const MIN_HEADER_WIDTH = 120
  const MAX_HEADER_WIDTH = 400
  // Context menu state
  const [contextMenu, setContextMenu] = useState<TimelineContextMenuState | null>(null)
  // Default duration for image clips (persisted to localStorage)
  const [defaultImageDurationMs, setDefaultImageDurationMs] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('timeline-default-image-duration-ms')
      return saved ? parseInt(saved, 10) : 5000
    } catch {
      return 5000
    }
  })
  const { updateTimeline } = useProjectStore()
  const trackRefs = useRef<{ [trackId: string]: HTMLDivElement | null }>({})
  const layerRefs = useRef<{ [layerId: string]: HTMLDivElement | null }>({})
  const labelsScrollRef = useRef<HTMLDivElement>(null)
  const tracksScrollRef = useRef<HTMLDivElement>(null)
  const timelineContainerRef = useRef<HTMLDivElement>(null)
  const isScrollSyncing = useRef(false)
  // Viewport bar resize state
  const [viewportBarDrag, setViewportBarDrag] = useState<{
    type: 'left' | 'right' | 'move'
    startX: number
    initialZoom: number
    initialScrollLeft: number
    initialBarLeft: number  // bar left edge position in container (px)
    initialBarRight: number // bar right edge position in container (px)
    initialRightTimeMs: number // timeline right edge time (ms)
    initialLeftTimeMs: number  // timeline left edge time (ms)
  } | null>(null)
  const viewportBarRef = useRef<HTMLDivElement>(null)
  // Track scroll position for viewport bar rendering (horizontal and vertical)
  const [scrollPosition, setScrollPosition] = useState({
    scrollLeft: 0, scrollWidth: 0, clientWidth: 0,
    scrollTop: 0, scrollHeight: 0, clientHeight: 0
  })
  // Vertical scrollbar drag state
  const [verticalScrollDrag, setVerticalScrollDrag] = useState(false)
  const verticalScrollStartY = useRef(0)
  const verticalScrollStartTop = useRef(0)

  // Sort layers by order descending (highest order = topmost layer = first in UI)
  const sortedLayers = useMemo(() => {
    return [...timeline.layers].sort((a, b) => (b.order ?? 0) - (a.order ?? 0))
  }, [timeline.layers])

  // Sync vertical scroll between labels and tracks
  const handleLabelsScroll = useCallback(() => {
    if (isScrollSyncing.current) return
    isScrollSyncing.current = true
    if (tracksScrollRef.current && labelsScrollRef.current) {
      tracksScrollRef.current.scrollTop = labelsScrollRef.current.scrollTop
    }
    isScrollSyncing.current = false
  }, [])

  const handleTracksScroll = useCallback(() => {
    if (isScrollSyncing.current) return
    isScrollSyncing.current = true
    if (labelsScrollRef.current && tracksScrollRef.current) {
      labelsScrollRef.current.scrollTop = tracksScrollRef.current.scrollTop
    }
    // Update scroll position for viewport bar (skip during viewport bar drag to preserve custom values)
    if (tracksScrollRef.current && !viewportBarDrag && !verticalScrollDrag) {
      const el = tracksScrollRef.current
      const clientW = el.clientWidth
      // Use canvasWidth for scrollWidth (always allow scrolling)
      const pps = 10 * zoom
      const clipW = (timeline.duration_ms / 1000) * pps
      const minW = 120 * pps
      const contentW = Math.max(clipW, minW)
      // Include right padding in total canvas width (allows scrolling end to left edge)
      const canvasW = contentW + clientW
      setScrollPosition({
        scrollLeft: el.scrollLeft,
        scrollWidth: canvasW,
        clientWidth: clientW,
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      })
    }
    isScrollSyncing.current = false
  }, [viewportBarDrag, zoom, timeline.duration_ms])

  const pixelsPerSecond = 10 * zoom

  // Clip-based width (actual content)
  const clipBasedWidth = (timeline.duration_ms / 1000) * pixelsPerSecond
  // Canvas width: always allow scrolling beyond clips
  // At minimum, canvas is 120 seconds worth
  const minCanvasSeconds = 120 // 2 minutes minimum canvas
  const minCanvasWidth = minCanvasSeconds * pixelsPerSecond
  const contentWidth = Math.max(clipBasedWidth, minCanvasWidth)
  // Right padding: allows scrolling timeline end to the left edge of view
  const clientWidthForPadding = scrollPosition.clientWidth || 800
  const rightPadding = clientWidthForPadding
  // Total canvas width: content + right padding for scrolling flexibility
  const canvasWidth = contentWidth + rightPadding

  // Scroll timeline to a specific time position
  const scrollToTime = useCallback((timeMs: number, align: 'left' | 'center' = 'left') => {
    if (!tracksScrollRef.current) return

    const pps = 10 * zoom
    const clientWidth = tracksScrollRef.current.clientWidth
    // Content starts at 0, so targetPx is just the time position
    const targetPx = (timeMs / 1000) * pps

    let scrollLeft: number
    if (align === 'center') {
      scrollLeft = targetPx - clientWidth / 2
    } else {
      scrollLeft = targetPx
    }

    // Total canvas width = contentWidth + rightPadding
    const clipW = (timeline.duration_ms / 1000) * pps
    const minW = 120 * pps
    const contentW = Math.max(clipW, minW)
    const totalCanvasW = contentW + clientWidth  // rightPadding = clientWidth
    const maxScroll = totalCanvasW - clientWidth

    scrollLeft = Math.max(0, Math.min(scrollLeft, maxScroll))

    tracksScrollRef.current.scrollTo({
      left: scrollLeft,
      behavior: 'smooth'
    })
  }, [zoom, timeline.duration_ms])

  // Fit timeline to window: adjust zoom so entire timeline fits in visible area
  const handleFitToWindow = useCallback(() => {
    if (!tracksScrollRef.current || timeline.duration_ms <= 0) return

    const clientWidth = tracksScrollRef.current.clientWidth
    // Leave some padding (20px on each side) for better visibility
    const availableWidth = clientWidth - 40
    const durationSeconds = timeline.duration_ms / 1000

    // Calculate zoom: pixelsPerSecond = 10 * zoom, so zoom = availableWidth / (durationSeconds * 10)
    const targetZoom = availableWidth / (durationSeconds * 10)

    // Clamp zoom between 0.1 and 20 (same as manual zoom limits)
    const clampedZoom = Math.max(0.1, Math.min(20, targetZoom))

    setZoom(clampedZoom)

    // Scroll to the beginning
    tracksScrollRef.current.scrollTo({ left: 0, behavior: 'smooth' })
  }, [timeline.duration_ms])

  // Get selected audio clip's group_id (for highlighting linked video clips)
  const selectedAudioGroupId = useMemo(() => {
    if (!selectedClip) return null
    for (const track of timeline.audio_tracks) {
      const clip = track.clips.find(c => c.id === selectedClip.clipId)
      if (clip?.group_id) return clip.group_id
    }
    return null
  }, [selectedClip, timeline.audio_tracks])

  // Detect overlapping video clips per layer
  const videoClipOverlaps = useMemo(() => {
    const overlaps = new Map<string, Set<string>>() // clipId -> Set of overlapping clipIds
    for (const layer of timeline.layers) {
      const clips = layer.clips
      for (let i = 0; i < clips.length; i++) {
        const clipA = clips[i]
        const aStart = clipA.start_ms
        const aEnd = aStart + clipA.duration_ms
        for (let j = i + 1; j < clips.length; j++) {
          const clipB = clips[j]
          const bStart = clipB.start_ms
          const bEnd = bStart + clipB.duration_ms
          // Check if they overlap
          if (aStart < bEnd && aEnd > bStart) {
            // Add to both clips' overlap sets
            if (!overlaps.has(clipA.id)) overlaps.set(clipA.id, new Set())
            if (!overlaps.has(clipB.id)) overlaps.set(clipB.id, new Set())
            overlaps.get(clipA.id)!.add(clipB.id)
            overlaps.get(clipB.id)!.add(clipA.id)
          }
        }
      }
    }
    return overlaps
  }, [timeline.layers])

  // Detect overlapping audio clips per track
  const audioClipOverlaps = useMemo(() => {
    const overlaps = new Map<string, Set<string>>() // clipId -> Set of overlapping clipIds
    for (const track of timeline.audio_tracks) {
      const clips = track.clips
      for (let i = 0; i < clips.length; i++) {
        const clipA = clips[i]
        const aStart = clipA.start_ms
        const aEnd = aStart + clipA.duration_ms
        for (let j = i + 1; j < clips.length; j++) {
          const clipB = clips[j]
          const bStart = clipB.start_ms
          const bEnd = bStart + clipB.duration_ms
          // Check if they overlap
          if (aStart < bEnd && aEnd > bStart) {
            // Add to both clips' overlap sets
            if (!overlaps.has(clipA.id)) overlaps.set(clipA.id, new Set())
            if (!overlaps.has(clipB.id)) overlaps.set(clipB.id, new Set())
            overlaps.get(clipA.id)!.add(clipB.id)
            overlaps.get(clipB.id)!.add(clipA.id)
          }
        }
      }
    }
    return overlaps
  }, [timeline.audio_tracks])

  // Snap threshold in milliseconds (equivalent to ~5 pixels at normal zoom)
  const SNAP_THRESHOLD_MS = 500

  // Get all snap points (start and end of all clips) excluding specified clips
  const getSnapPoints = useCallback((excludeClipIds: Set<string>): number[] => {
    const points = new Set<number>()

    // Add video clip boundaries
    for (const layer of timeline.layers) {
      for (const clip of layer.clips) {
        if (!excludeClipIds.has(clip.id)) {
          points.add(clip.start_ms)
          points.add(clip.start_ms + clip.duration_ms)
        }
      }
    }

    // Add audio clip boundaries
    for (const track of timeline.audio_tracks) {
      for (const clip of track.clips) {
        if (!excludeClipIds.has(clip.id)) {
          points.add(clip.start_ms)
          points.add(clip.start_ms + clip.duration_ms)
        }
      }
    }

    // Add playhead position (0)
    points.add(0)

    return Array.from(points).sort((a, b) => a - b)
  }, [timeline])

  // Find nearest snap point within threshold
  const findNearestSnapPoint = useCallback((timeMs: number, snapPoints: number[], threshold: number): number | null => {
    let nearest: number | null = null
    let minDistance = threshold

    for (const point of snapPoints) {
      const distance = Math.abs(timeMs - point)
      if (distance < minDistance) {
        minDistance = distance
        nearest = point
      }
    }

    return nearest
  }, [])

  const formatTime = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  // Default layer colors (hue rotation)
  const DEFAULT_LAYER_COLORS = [
    '#8b5cf6', // violet
    '#3b82f6', // blue
    '#22c55e', // green
    '#eab308', // yellow
    '#f97316', // orange
    '#ef4444', // red
    '#ec4899', // pink
    '#06b6d4', // cyan
  ]

  // Hash function to generate consistent color index from layer ID
  const hashLayerId = useCallback((layerId: string): number => {
    let hash = 0
    for (let i = 0; i < layerId.length; i++) {
      const char = layerId.charCodeAt(i)
      hash = ((hash << 5) - hash) + char
      hash = hash & hash // Convert to 32bit integer
    }
    return Math.abs(hash)
  }, [])

  // Get layer color (use layer.color if set, otherwise generate from layer ID hash)
  const getLayerColor = useCallback((layer: Layer, _index: number): string => {
    if (layer.color) return layer.color
    const colorIndex = hashLayerId(layer.id) % DEFAULT_LAYER_COLORS.length
    return DEFAULT_LAYER_COLORS[colorIndex]
  }, [hashLayerId])

  // Update layer color
  const handleUpdateLayerColor = useCallback(async (layerId: string, color: string) => {
    const updatedLayers = timeline.layers.map(layer =>
      layer.id === layerId ? { ...layer, color } : layer
    )
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }, [timeline, projectId, updateTimeline])

  // Get layer height (from state or default)
  const getLayerHeight = useCallback((layerId: string): number => {
    return layerHeights[layerId] ?? DEFAULT_LAYER_HEIGHT
  }, [layerHeights, DEFAULT_LAYER_HEIGHT])

  // Handle layer resize start
  const handleLayerResizeStart = useCallback((e: React.MouseEvent, layerId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setResizingLayerId(layerId)
    resizeStartY.current = e.clientY
    resizeStartHeight.current = getLayerHeight(layerId)
  }, [getLayerHeight])

  // Handle layer resize move
  const handleLayerResizeMove = useCallback((e: MouseEvent) => {
    if (!resizingLayerId) return
    const deltaY = e.clientY - resizeStartY.current
    const newHeight = Math.min(MAX_LAYER_HEIGHT, Math.max(MIN_LAYER_HEIGHT, resizeStartHeight.current + deltaY))
    setLayerHeights(prev => ({ ...prev, [resizingLayerId]: newHeight }))
  }, [resizingLayerId, MIN_LAYER_HEIGHT, MAX_LAYER_HEIGHT])

  // Handle layer resize end
  const handleLayerResizeEnd = useCallback(() => {
    if (resizingLayerId) {
      // Save to localStorage
      const newHeights = { ...layerHeights }
      localStorage.setItem(`timeline-layer-heights-${projectId}`, JSON.stringify(newHeights))
    }
    setResizingLayerId(null)
  }, [resizingLayerId, layerHeights, projectId])

  // Add resize listeners
  useEffect(() => {
    if (resizingLayerId) {
      window.addEventListener('mousemove', handleLayerResizeMove)
      window.addEventListener('mouseup', handleLayerResizeEnd)
      return () => {
        window.removeEventListener('mousemove', handleLayerResizeMove)
        window.removeEventListener('mouseup', handleLayerResizeEnd)
      }
    }
  }, [resizingLayerId, handleLayerResizeMove, handleLayerResizeEnd])

  // Persist default image duration to localStorage
  useEffect(() => {
    localStorage.setItem('timeline-default-image-duration-ms', String(defaultImageDurationMs))
  }, [defaultImageDurationMs])

  // Handle header resize start
  const handleHeaderResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingHeader(true)
    headerResizeStartX.current = e.clientX
    headerResizeStartWidth.current = headerWidth
  }, [headerWidth])

  // Handle header resize move
  const handleHeaderResizeMove = useCallback((e: MouseEvent) => {
    if (!isResizingHeader) return
    const deltaX = e.clientX - headerResizeStartX.current
    const newWidth = Math.min(MAX_HEADER_WIDTH, Math.max(MIN_HEADER_WIDTH, headerResizeStartWidth.current + deltaX))
    setHeaderWidth(newWidth)
  }, [isResizingHeader])

  // Handle header resize end
  const handleHeaderResizeEnd = useCallback(() => {
    if (isResizingHeader) {
      localStorage.setItem(`timeline-header-width-${projectId}`, String(headerWidth))
    }
    setIsResizingHeader(false)
  }, [isResizingHeader, headerWidth, projectId])

  // Add header resize listeners
  useEffect(() => {
    if (isResizingHeader) {
      window.addEventListener('mousemove', handleHeaderResizeMove)
      window.addEventListener('mouseup', handleHeaderResizeEnd)
      return () => {
        window.removeEventListener('mousemove', handleHeaderResizeMove)
        window.removeEventListener('mouseup', handleHeaderResizeEnd)
      }
    }
  }, [isResizingHeader, handleHeaderResizeMove, handleHeaderResizeEnd])

  // Viewport bar drag handlers for zoom control
  const handleViewportBarDragStart = useCallback((e: React.MouseEvent, type: 'left' | 'right' | 'move') => {
    e.preventDefault()
    e.stopPropagation()

    if (!viewportBarRef.current) return

    const containerRect = viewportBarRef.current.getBoundingClientRect()

    // Get actual bar position from DOM
    // Use currentTarget (element with the listener) not target (element clicked)
    // For 'move': currentTarget is the bar itself
    // For 'left'/'right': currentTarget is the handle, parent is the bar
    const currentTarget = e.currentTarget as HTMLElement
    const barElement = type === 'move' ? currentTarget : currentTarget.parentElement
    if (!barElement) return

    const barRect = barElement.getBoundingClientRect()
    const barLeft = barRect.left - containerRect.left
    const barRight = barRect.right - containerRect.left

    // Get scroll state
    const clientWidth = scrollPosition.clientWidth || 800
    const scrollLeft = scrollPosition.scrollLeft
    const pps = 10 * zoom

    // Calculate time values for edge anchoring (no left padding - content starts at 0)
    const leftTimeMs = (scrollLeft / pps) * 1000
    const rightTimeMs = ((scrollLeft + clientWidth) / pps) * 1000

    console.log('[DragStart]', {
      type,
      containerWidth: containerRect.width,
      barLeft,
      barRight,
      barWidth: barRight - barLeft,
      scrollLeft,
      clientWidth,
      zoom,
    })

    setViewportBarDrag({
      type,
      startX: e.clientX,
      initialZoom: zoom,
      initialScrollLeft: scrollLeft,
      initialBarLeft: barLeft,
      initialBarRight: barRight,
      initialLeftTimeMs: leftTimeMs,
      initialRightTimeMs: rightTimeMs,
    })
  }, [zoom, scrollPosition])

  const handleViewportBarDragMove = useCallback((e: MouseEvent) => {
    if (!viewportBarDrag || !viewportBarRef.current || !tracksScrollRef.current) return

    const containerWidth = viewportBarRef.current.clientWidth
    const scrollContainer = tracksScrollRef.current
    const clientWidth = scrollContainer.clientWidth
    const deltaX = e.clientX - viewportBarDrag.startX
    const pps = 10 * viewportBarDrag.initialZoom

    // Canvas width: always allow scrolling (120 seconds minimum)
    // Include right padding for scrolling end to left edge
    const clipBasedWidth = (timeline.duration_ms / 1000) * pps
    const minCanvasWidth = 120 * pps
    const contentWidth = Math.max(clipBasedWidth, minCanvasWidth)
    const canvasWidthCalc = contentWidth + clientWidth

    if (viewportBarDrag.type === 'move') {
      // Move: bar follows mouse 1:1
      const initialBarWidth = viewportBarDrag.initialBarRight - viewportBarDrag.initialBarLeft
      const barMovableRange = containerWidth - initialBarWidth

      // Map bar delta to scroll delta
      const maxScroll = canvasWidthCalc - clientWidth
      let newScrollLeft = viewportBarDrag.initialScrollLeft
      if (barMovableRange > 0) {
        newScrollLeft = viewportBarDrag.initialScrollLeft + (deltaX / barMovableRange) * maxScroll
      }

      // Clamp to valid range (0 to maxScroll)
      newScrollLeft = Math.max(0, Math.min(maxScroll, newScrollLeft))

      // Update DOM scroll
      scrollContainer.scrollLeft = newScrollLeft

      // Move current time to center of visible area
      if (onSeek) {
        const centerPx = newScrollLeft + clientWidth / 2
        const centerTimeMs = (centerPx / pps) * 1000
        onSeek(Math.max(0, centerTimeMs))
      }

      setScrollPosition(prev => ({
        ...prev,
        scrollLeft: newScrollLeft,
        scrollWidth: canvasWidthCalc,
        clientWidth: clientWidth,
      }))
    } else {
      // Resize: handle follows mouse, opposite edge stays fixed
      let newBarLeft: number
      let newBarRight: number

      if (viewportBarDrag.type === 'left') {
        newBarLeft = viewportBarDrag.initialBarLeft + deltaX
        newBarRight = viewportBarDrag.initialBarRight // fixed
      } else {
        newBarLeft = viewportBarDrag.initialBarLeft // fixed
        newBarRight = viewportBarDrag.initialBarRight + deltaX
      }

      // Calculate new bar width (enforce minimum)
      let newBarWidth = newBarRight - newBarLeft
      const minBarWidth = 20

      if (newBarWidth < minBarWidth) {
        newBarWidth = minBarWidth
        if (viewportBarDrag.type === 'left') {
          newBarLeft = newBarRight - newBarWidth
        } else {
          newBarRight = newBarLeft + newBarWidth
        }
      }

      // Simple ratio-based zoom: ensures deltaX=0 means no change
      const initialBarWidth = viewportBarDrag.initialBarRight - viewportBarDrag.initialBarLeft
      let newZoom = viewportBarDrag.initialZoom * (initialBarWidth / newBarWidth)
      newZoom = Math.max(0.1, Math.min(20, newZoom))

      // Recompute canvas width with new zoom
      const clipDurationSec = Math.max(timeline.duration_ms, 1) / 1000
      const newPixelsPerSecond = 10 * newZoom
      const newContentWidth = Math.max(
        clipDurationSec * newPixelsPerSecond,
        120 * newPixelsPerSecond
      )
      const newCanvasWidth = newContentWidth + clientWidth  // rightPadding = clientWidth
      const maxScroll = newCanvasWidth - clientWidth

      // Use TIME-based anchoring for stable scroll position
      // Time is independent of zoom, so it's more reliable
      let newScrollLeft: number
      if (viewportBarDrag.type === 'left') {
        // Right edge fixed: keep initialRightTimeMs at right edge of view
        const rightTimeSec = viewportBarDrag.initialRightTimeMs / 1000
        // scrollLeft = timeSec * pps - clientWidth (to put time at right edge)
        newScrollLeft = rightTimeSec * newPixelsPerSecond - clientWidth
      } else {
        // Left edge fixed: keep initialLeftTimeMs at left edge of view
        const leftTimeSec = viewportBarDrag.initialLeftTimeMs / 1000
        // scrollLeft = timeSec * pps (to put time at left edge)
        newScrollLeft = leftTimeSec * newPixelsPerSecond
      }

      newScrollLeft = Math.max(0, Math.min(maxScroll, newScrollLeft))

      console.log('[DragMove]', {
        deltaX,
        newZoom,
        newScrollLeft,
        fixedTime: viewportBarDrag.type === 'left'
          ? viewportBarDrag.initialRightTimeMs
          : viewportBarDrag.initialLeftTimeMs,
      })

      setZoom(newZoom)

      setScrollPosition(prev => ({
        ...prev,
        scrollLeft: newScrollLeft,
        scrollWidth: newCanvasWidth,
        clientWidth: clientWidth,
      }))

      // Set scroll position after DOM update
      requestAnimationFrame(() => {
        if (tracksScrollRef.current) {
          tracksScrollRef.current.scrollLeft = newScrollLeft
        }
      })
    }
  }, [viewportBarDrag, timeline.duration_ms, onSeek])

  const handleViewportBarDragEnd = useCallback(() => {
    setViewportBarDrag(null)
  }, [])

  // Add viewport bar drag listeners
  useEffect(() => {
    if (viewportBarDrag) {
      window.addEventListener('mousemove', handleViewportBarDragMove)
      window.addEventListener('mouseup', handleViewportBarDragEnd)
      return () => {
        window.removeEventListener('mousemove', handleViewportBarDragMove)
        window.removeEventListener('mouseup', handleViewportBarDragEnd)
      }
    }
  }, [viewportBarDrag, handleViewportBarDragMove, handleViewportBarDragEnd])

  // Vertical scrollbar drag handlers
  const handleVerticalScrollDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!tracksScrollRef.current) return
    setVerticalScrollDrag(true)
    verticalScrollStartY.current = e.clientY
    verticalScrollStartTop.current = tracksScrollRef.current.scrollTop
  }, [])

  const handleVerticalScrollDragMove = useCallback((e: MouseEvent) => {
    if (!verticalScrollDrag || !tracksScrollRef.current) return
    const el = tracksScrollRef.current
    const trackHeight = el.clientHeight - 24 // Account for ruler height
    const thumbHeight = Math.max(30, (el.clientHeight / el.scrollHeight) * trackHeight)
    const scrollableTrack = trackHeight - thumbHeight
    const scrollableContent = el.scrollHeight - el.clientHeight
    if (scrollableTrack <= 0 || scrollableContent <= 0) return
    const deltaY = e.clientY - verticalScrollStartY.current
    const scrollDelta = (deltaY / scrollableTrack) * scrollableContent
    el.scrollTop = Math.max(0, Math.min(scrollableContent, verticalScrollStartTop.current + scrollDelta))
  }, [verticalScrollDrag])

  const handleVerticalScrollDragEnd = useCallback(() => {
    setVerticalScrollDrag(false)
  }, [])

  // Add vertical scrollbar drag listeners
  useEffect(() => {
    if (verticalScrollDrag) {
      window.addEventListener('mousemove', handleVerticalScrollDragMove)
      window.addEventListener('mouseup', handleVerticalScrollDragEnd)
      return () => {
        window.removeEventListener('mousemove', handleVerticalScrollDragMove)
        window.removeEventListener('mouseup', handleVerticalScrollDragEnd)
      }
    }
  }, [verticalScrollDrag, handleVerticalScrollDragMove, handleVerticalScrollDragEnd])

  // Update scroll position on mount and when zoom changes
  useEffect(() => {
    const updateScrollPosition = () => {
      // Skip during drag operations to preserve custom values
      if (tracksScrollRef.current && !viewportBarDrag && !verticalScrollDrag) {
        const el = tracksScrollRef.current
        const clientW = el.clientWidth
        // Use canvasWidth for scrollWidth (always allow scrolling)
        const pps = 10 * zoom
        const clipW = (timeline.duration_ms / 1000) * pps
        const minW = 120 * pps
        const contentW = Math.max(clipW, minW)
        const canvasW = contentW + clientW  // rightPadding = clientWidth
        setScrollPosition({
          scrollLeft: el.scrollLeft,
          scrollWidth: canvasW,
          clientWidth: clientW,
          scrollTop: el.scrollTop,
          scrollHeight: el.scrollHeight,
          clientHeight: el.clientHeight,
        })
      }
    }
    // Initial update
    updateScrollPosition()
    // Update on resize
    window.addEventListener('resize', updateScrollPosition)
    return () => window.removeEventListener('resize', updateScrollPosition)
  }, [zoom, timeline.duration_ms, viewportBarDrag, verticalScrollDrag])

  // Auto-scroll to follow playhead during playback
  // Maintains relative position - only scrolls when playhead goes off-screen
  useEffect(() => {
    if (!isPlaying || !tracksScrollRef.current) return

    const el = tracksScrollRef.current
    const pps = 10 * zoom
    const playheadPx = (currentTimeMs / 1000) * pps
    const scrollLeft = el.scrollLeft
    const clientWidth = el.clientWidth

    // Check if playhead is outside visible area
    const rightEdge = scrollLeft + clientWidth
    const leftEdge = scrollLeft

    // Playhead exceeded right edge - scroll just enough to keep it visible
    if (playheadPx > rightEdge) {
      el.scrollLeft = playheadPx - clientWidth + 50 // Keep 50px margin from right edge
    }
    // Playhead is before left edge - scroll to show it with margin
    else if (playheadPx < leftEdge) {
      el.scrollLeft = Math.max(0, playheadPx - 50) // Keep 50px margin from left edge
    }
  }, [isPlaying, currentTimeMs, zoom])

  // Helper: Calculate max duration from all clips in timeline
  const calculateMaxDuration = useCallback((layers: Layer[], audioTracks: AudioTrack[]): number => {
    let maxDuration = 0
    for (const layer of layers) {
      for (const clip of layer.clips) {
        const clipEnd = clip.start_ms + clip.duration_ms
        if (clipEnd > maxDuration) maxDuration = clipEnd
      }
    }
    for (const track of audioTracks) {
      for (const clip of track.clips) {
        const clipEnd = clip.start_ms + clip.duration_ms
        if (clipEnd > maxDuration) maxDuration = clipEnd
      }
    }
    console.log('[calculateMaxDuration] newDuration:', maxDuration, 'ms')
    return maxDuration
  }, [])

  // Helper function to find all clips in the same group
  const findGroupClips = useCallback((groupId: string | null | undefined) => {
    if (!groupId) return { videoClipIds: new Set<string>(), audioClipIds: new Set<string>() }

    const videoClipIds = new Set<string>()
    const audioClipIds = new Set<string>()

    // Search video layers
    for (const layer of timeline.layers) {
      for (const clip of layer.clips) {
        if (clip.group_id === groupId) {
          videoClipIds.add(clip.id)
        }
      }
    }

    // Search audio tracks
    for (const track of timeline.audio_tracks) {
      for (const clip of track.clips) {
        if (clip.group_id === groupId) {
          audioClipIds.add(clip.id)
        }
      }
    }

    return { videoClipIds, audioClipIds }
  }, [timeline])

  const handleClipSelect = useCallback((trackId: string, clipId: string, e?: React.MouseEvent) => {
    // SHIFT+click for multi-selection
    if (e?.shiftKey) {
      setSelectedAudioClips(prev => {
        const newSet = new Set(prev)
        if (newSet.has(clipId)) {
          newSet.delete(clipId)
        } else {
          newSet.add(clipId)
        }
        return newSet
      })
      // Keep or add to primary selection
      if (!selectedClip) {
        setSelectedClip({ trackId, clipId })
      }
      return
    }

    // Find the selected clip and check for group membership
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    const selectedAudioClip = track?.clips.find(c => c.id === clipId)
    const groupId = selectedAudioClip?.group_id

    setSelectedClip({ trackId, clipId })
    setSelectedVideoClip(null) // Deselect video clip

    // If the clip is already in multi-selection, preserve the selection (allows drag of multi-selected clips)
    if (selectedAudioClips.has(clipId)) {
      // Don't clear multi-selection - user clicked on an already-selected clip
    } else if (groupId) {
      // If the clip belongs to a group, select all clips in the group
      const { videoClipIds, audioClipIds } = findGroupClips(groupId)
      setSelectedVideoClips(videoClipIds)
      setSelectedAudioClips(audioClipIds)
    } else {
      // Clear multi-selection for non-grouped clips
      setSelectedVideoClips(new Set())
      setSelectedAudioClips(new Set())
    }

    // Notify parent of selection
    if (onClipSelect) {
      const track = timeline.audio_tracks.find(t => t.id === trackId)
      if (track) {
        const clip = track.clips.find(c => c.id === clipId)
        if (clip) {
          const asset = assets.find(a => a.id === clip.asset_id)
          onClipSelect({
            trackId,
            trackType: track.type,
            clipId,
            assetId: clip.asset_id,
            assetName: asset?.name || clip.asset_id.slice(0, 8),
            startMs: clip.start_ms,
            durationMs: clip.duration_ms,
            volume: clip.volume,
            fadeInMs: clip.fade_in_ms,
            fadeOutMs: clip.fade_out_ms,
          })
          // Move current time to clip start
          if (onSeek) {
            onSeek(clip.start_ms)
          }
          return
        }
      }
      onClipSelect(null)
    }
    if (onVideoClipSelect) {
      onVideoClipSelect(null)
    }
  }, [timeline, assets, onClipSelect, onVideoClipSelect, onSeek, selectedVideoClip, selectedClip, findGroupClips, selectedAudioClips])

  // Video clip selection handler
  const handleVideoClipSelect = useCallback((layerId: string, clipId: string, e?: React.MouseEvent) => {
    console.log('[handleVideoClipSelect] layerId:', layerId, 'clipId:', clipId, 'shiftKey:', e?.shiftKey)

    // SHIFT+click for multi-selection
    if (e?.shiftKey) {
      setSelectedVideoClips(prev => {
        const newSet = new Set(prev)
        if (newSet.has(clipId)) {
          newSet.delete(clipId)
        } else {
          newSet.add(clipId)
        }
        return newSet
      })
      // Keep or add to primary selection
      if (!selectedVideoClip) {
        setSelectedVideoClip({ layerId, clipId })
        setSelectedLayerId(layerId)
      }
      return
    }

    // Find the selected clip and check for group membership
    const layer = timeline.layers.find(l => l.id === layerId)
    const selectedClipObj = layer?.clips.find(c => c.id === clipId)
    const groupId = selectedClipObj?.group_id

    setSelectedVideoClip({ layerId, clipId })
    setSelectedLayerId(layerId) // Also select the layer
    setSelectedClip(null) // Deselect audio clip

    // If the clip is already in multi-selection, preserve the selection (allows drag of multi-selected clips)
    if (selectedVideoClips.has(clipId)) {
      // Don't clear multi-selection - user clicked on an already-selected clip
    } else if (groupId) {
      // If the clip belongs to a group, select all clips in the group
      const { videoClipIds, audioClipIds } = findGroupClips(groupId)
      setSelectedVideoClips(videoClipIds)
      setSelectedAudioClips(audioClipIds)
    } else {
      // Clear multi-selection for non-grouped clips
      setSelectedVideoClips(new Set())
      setSelectedAudioClips(new Set())
    }

    // Notify parent of selection
    if (onVideoClipSelect) {
      const layer = timeline.layers.find(l => l.id === layerId)
      if (layer) {
        const clip = layer.clips.find(c => c.id === clipId)
        if (clip) {
          const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
          // Determine asset name: use asset name, shape type name, text clip indicator, or fallback to 'Clip'
          let assetName = 'Clip'
          if (asset) {
            assetName = asset.name
          } else if (clip.text_content) {
            assetName = `テキスト: ${clip.text_content.slice(0, 10)}${clip.text_content.length > 10 ? '...' : ''}`
          } else if (clip.shape) {
            const shapeNames: Record<string, string> = { rectangle: '四角形', circle: '円', line: '線' }
            assetName = shapeNames[clip.shape.type] || clip.shape.type
          } else if (clip.asset_id) {
            assetName = clip.asset_id.slice(0, 8)
          }
          onVideoClipSelect({
            layerId,
            layerName: layer.name,
            clipId,
            assetId: clip.asset_id,
            assetName,
            startMs: clip.start_ms,
            durationMs: clip.duration_ms,
            inPointMs: clip.in_point_ms,
            transform: clip.transform,
            effects: clip.effects,
            keyframes: clip.keyframes,
            shape: clip.shape,
            crop: clip.crop,
            textContent: clip.text_content,
            textStyle: clip.text_style,
            fadeInMs: clip.fade_in_ms ?? clip.effects?.fade_in_ms ?? 0,
            fadeOutMs: clip.fade_out_ms ?? clip.effects?.fade_out_ms ?? 0,
          })
          // Move current time to clip start
          if (onSeek) {
            onSeek(clip.start_ms)
          }
          return
        }
      }
      onVideoClipSelect(null)
    }
    if (onClipSelect) {
      onClipSelect(null)
    }
  }, [timeline, assets, onClipSelect, onVideoClipSelect, onSeek, selectedVideoClip, findGroupClips, selectedVideoClips])

  // Handle double-click on video clip to fill gap (extend to next clip or shrink to previous clip)
  const handleVideoClipDoubleClick = useCallback(async (layerId: string, clipId: string) => {
    console.log('[handleVideoClipDoubleClick] layerId:', layerId, 'clipId:', clipId)

    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) return

    const clip = layer.clips.find(c => c.id === clipId)
    if (!clip) return

    // Find all other clips in the same layer, sorted by start_ms
    const otherClips = layer.clips
      .filter(c => c.id !== clipId)
      .sort((a, b) => a.start_ms - b.start_ms)

    const clipEnd = clip.start_ms + clip.duration_ms

    // Find the next clip (closest clip that starts after this clip ends)
    const nextClip = otherClips.find(c => c.start_ms >= clipEnd)

    // Find the previous clip (closest clip that ends before this clip starts)
    const prevClips = otherClips.filter(c => c.start_ms + c.duration_ms <= clip.start_ms)
    const prevClip = prevClips.length > 0 ? prevClips[prevClips.length - 1] : null

    let newStartMs = clip.start_ms
    let newDurationMs = clip.duration_ms

    // Extend duration to fill gap to next clip (if there is one)
    if (nextClip && nextClip.start_ms > clipEnd) {
      // There's a gap between this clip and the next clip
      newDurationMs = nextClip.start_ms - clip.start_ms
      console.log('[handleVideoClipDoubleClick] Extending to next clip, new duration:', newDurationMs)
    }

    // Extend start to fill gap from previous clip (if there is one)
    if (prevClip) {
      const prevClipEnd = prevClip.start_ms + prevClip.duration_ms
      if (clip.start_ms > prevClipEnd) {
        // There's a gap between previous clip and this clip
        const gapMs = clip.start_ms - prevClipEnd
        newStartMs = prevClipEnd
        newDurationMs = newDurationMs + gapMs
        console.log('[handleVideoClipDoubleClick] Extending to fill gap from previous clip, new start:', newStartMs)
      }
    } else if (clip.start_ms > 0) {
      // No previous clip but clip doesn't start at 0
      newDurationMs = newDurationMs + clip.start_ms
      newStartMs = 0
      console.log('[handleVideoClipDoubleClick] Extending to start of timeline')
    }

    // If nothing changed, no update needed
    if (newStartMs === clip.start_ms && newDurationMs === clip.duration_ms) {
      console.log('[handleVideoClipDoubleClick] No gaps to fill')
      return
    }

    // Update the clip
    const updatedLayers = timeline.layers.map(l => {
      if (l.id !== layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== clipId) return c
          return { ...c, start_ms: newStartMs, duration_ms: newDurationMs }
        })
      }
    })

    // Also update linked audio clip if exists
    let updatedAudioTracks = timeline.audio_tracks
    if (clip.group_id) {
      updatedAudioTracks = updatedAudioTracks.map(track => ({
        ...track,
        clips: track.clips.map(audioClip => {
          if (audioClip.group_id === clip.group_id) {
            return { ...audioClip, start_ms: newStartMs, duration_ms: newDurationMs }
          }
          return audioClip
        })
      }))
    }

    console.log('[handleVideoClipDoubleClick] Updating timeline')
    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedAudioTracks,
    })
  }, [timeline, projectId, updateTimeline])

  const {
    dragState,
    videoDragState,
    handleClipDragStart,
    handleVideoClipDragStart,
  } = useTimelineDrag({
    timeline,
    assets,
    pixelsPerSecond,
    isSnapEnabled,
    snapThresholdMs: SNAP_THRESHOLD_MS,
    getSnapPoints,
    findNearestSnapPoint,
    updateTimeline,
    projectId,
    calculateMaxDuration,
    selectedClip,
    selectedVideoClip,
    selectedAudioClips,
    selectedVideoClips,
    handleClipSelect,
    handleVideoClipSelect,
    setSnapLineMs,
  })

  // Pre-compute group clip IDs as Sets for O(1) lookup during render
  const dragGroupVideoClipIds = useMemo(() => {
    if (!dragState?.groupVideoClips) return new Set<string>()
    return new Set(dragState.groupVideoClips.map(gc => gc.clipId))
  }, [dragState?.groupVideoClips])

  const dragGroupAudioClipIds = useMemo(() => {
    if (!dragState?.groupAudioClips) return new Set<string>()
    return new Set(dragState.groupAudioClips.map(gc => gc.clipId))
  }, [dragState?.groupAudioClips])

  const videoDragGroupVideoClipIds = useMemo(() => {
    if (!videoDragState?.groupVideoClips) return new Set<string>()
    return new Set(videoDragState.groupVideoClips.map(gc => gc.clipId))
  }, [videoDragState?.groupVideoClips])

  const videoDragGroupAudioClipIds = useMemo(() => {
    if (!videoDragState?.groupAudioClips) return new Set<string>()
    return new Set(videoDragState.groupAudioClips.map(gc => gc.clipId))
  }, [videoDragState?.groupAudioClips])

  // All audio tracks are treated equally (no linked/standalone distinction)
  const audioTracks = useMemo(() => timeline.audio_tracks, [timeline.audio_tracks])

  // Sync masterMuted state when individual track mute states change
  useEffect(() => {
    if (audioTracks.length === 0) {
      setMasterMuted(false)
      return
    }
    const allMuted = audioTracks.every(track => track.muted)
    setMasterMuted(allMuted)
  }, [audioTracks])

  // Helper functions for track type restriction (Issue #016)
  // Video and shape clips cannot coexist on the same layer
  // Shape and image clips CAN coexist
  const layerHasVideoClips = useCallback((layerId: string): boolean => {
    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) return false
    return layer.clips.some(clip => {
      if (!clip.asset_id) return false
      const asset = assets.find(a => a.id === clip.asset_id)
      return asset?.type === 'video'
    })
  }, [timeline.layers, assets])

  const layerHasShapeClips = useCallback((layerId: string): boolean => {
    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) return false
    return layer.clips.some(clip => clip.shape !== undefined)
  }, [timeline.layers])

  // Find or create a layer suitable for shapes (no video clips)
  const findOrCreateShapeCompatibleLayer = useCallback(async (
    excludeLayerId?: string
  ): Promise<{ layerId: string; updatedLayers: typeof timeline.layers }> => {
    let updatedLayers = [...timeline.layers]

    // Find an unlocked layer without video clips
    const compatibleLayer = timeline.layers.find(l =>
      !l.locked &&
      l.id !== excludeLayerId &&
      !layerHasVideoClips(l.id)
    )

    if (compatibleLayer) {
      return { layerId: compatibleLayer.id, updatedLayers }
    }

    // Create a new layer for shapes (prepend = topmost)
    const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
    const newLayer = {
      id: uuidv4(),
      name: `シェイプレイヤー ${timeline.layers.length + 1}`,
      order: maxOrder + 1,
      visible: true,
      locked: false,
      clips: [] as Clip[],
    }
    updatedLayers = [newLayer, ...updatedLayers]
    return { layerId: newLayer.id, updatedLayers }
  }, [timeline.layers, layerHasVideoClips])

  // Find or create a layer suitable for video (no shape clips)
  const findOrCreateVideoCompatibleLayer = useCallback(async (
    excludeLayerId?: string
  ): Promise<{ layerId: string; updatedLayers: typeof timeline.layers }> => {
    let updatedLayers = [...timeline.layers]

    // Find an unlocked layer without shape clips
    const compatibleLayer = timeline.layers.find(l =>
      !l.locked &&
      l.id !== excludeLayerId &&
      !layerHasShapeClips(l.id)
    )

    if (compatibleLayer) {
      return { layerId: compatibleLayer.id, updatedLayers }
    }

    // Create a new layer for video (prepend = topmost)
    const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
    const newLayer = {
      id: uuidv4(),
      name: `ビデオレイヤー ${timeline.layers.length + 1}`,
      order: maxOrder + 1,
      visible: true,
      locked: false,
      clips: [] as Clip[],
    }
    updatedLayers = [newLayer, ...updatedLayers]
    return { layerId: newLayer.id, updatedLayers }
  }, [timeline.layers, layerHasShapeClips])

  const handleTrackVolumeChange = async (trackId: string, volume: number) => {
    const updatedTracks = timeline.audio_tracks.map((track) =>
      track.id === trackId ? { ...track, volume } : track
    )
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  // Apply ducking by generating volume keyframes for BGM clips
  const handleApplyDucking = async (trackId: string) => {
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    if (!track || track.clips.length === 0) return

    // Confirm with user
    if (!confirm('ナレーションに合わせてダッキングを適用します。\n既存のボリュームキーフレームは上書きされます。')) {
      return
    }

    // Collect all narration clips from narration tracks (excluding muted tracks)
    const narrationClips: Array<{ start_ms: number; end_ms: number }> = []
    for (const t of timeline.audio_tracks) {
      if (t.type === 'narration' && !t.muted) {
        for (const clip of t.clips) {
          narrationClips.push({
            start_ms: clip.start_ms,
            end_ms: clip.start_ms + clip.duration_ms,
          })
        }
      }
    }
    narrationClips.sort((a, b) => a.start_ms - b.start_ms)

    if (narrationClips.length === 0) {
      alert('ナレーションクリップがありません。')
      return
    }

    // Ducking parameters
    const attackMs = 200
    const releaseMs = 500
    const duckTo = 0.1

    // Generate volume keyframes for each clip in the target track
    const updatedClips = track.clips.map(clip => {
      const clipStart = clip.start_ms
      const clipEnd = clip.start_ms + clip.duration_ms
      const keyframes: Array<{ time_ms: number; value: number }> = []

      // Start with full volume
      keyframes.push({ time_ms: 0, value: 1.0 })

      // Find narration overlaps and create duck points
      for (const narr of narrationClips) {
        // Check if narration overlaps with this clip
        if (narr.end_ms < clipStart || narr.start_ms > clipEnd) continue

        // Narration start relative to clip (with attack)
        const duckStartAbsolute = Math.max(clipStart, narr.start_ms - attackMs)
        const duckStartRelative = duckStartAbsolute - clipStart

        // Narration end relative to clip (with release)
        const duckEndAbsolute = Math.min(clipEnd, narr.end_ms + releaseMs)
        const duckEndRelative = duckEndAbsolute - clipStart

        // Add keyframes: ramp down to duck_to, hold, ramp up to 1.0
        if (duckStartRelative > 0) {
          // Make sure we have a keyframe at full volume before ducking
          const lastKf = keyframes[keyframes.length - 1]
          if (lastKf.time_ms < duckStartRelative - attackMs) {
            keyframes.push({ time_ms: duckStartRelative - attackMs, value: 1.0 })
          }
          keyframes.push({ time_ms: duckStartRelative, value: duckTo })
        } else {
          // Narration starts before or at clip start
          keyframes[0] = { time_ms: 0, value: duckTo }
        }

        // Keyframe at narration end (still ducked)
        const narrEndRelative = Math.min(narr.end_ms - clipStart, clip.duration_ms)
        if (narrEndRelative > 0 && narrEndRelative < clip.duration_ms) {
          keyframes.push({ time_ms: narrEndRelative, value: duckTo })
        }

        // Ramp back up
        if (duckEndRelative < clip.duration_ms) {
          keyframes.push({ time_ms: duckEndRelative, value: 1.0 })
        }
      }

      // Sort and deduplicate keyframes
      keyframes.sort((a, b) => a.time_ms - b.time_ms)
      const uniqueKeyframes = keyframes.filter((kf, i, arr) =>
        i === 0 || kf.time_ms !== arr[i - 1].time_ms
      )

      return { ...clip, volume_keyframes: uniqueKeyframes }
    })

    // Update timeline
    const updatedTracks = timeline.audio_tracks.map(t =>
      t.id === trackId ? { ...t, clips: updatedClips } : t
    )
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  const handleMuteToggle = async (trackId: string) => {
    const updatedTracks = timeline.audio_tracks.map((track) =>
      track.id === trackId ? { ...track, muted: !track.muted } : track
    )
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  // Master mute toggle - mute/unmute all audio tracks at once
  const handleMasterMuteToggle = async () => {
    const newMutedState = !masterMuted
    setMasterMuted(newMutedState)
    const updatedTracks = timeline.audio_tracks.map((track) => ({
      ...track,
      muted: newMutedState
    }))
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  // Volume keyframe handlers with debounced DB updates
  const volumeKeyframeDebounceRef = useRef<NodeJS.Timeout | null>(null)
  const pendingVolumeKeyframeUpdate = useRef<{ trackId: string; clipId: string; keyframes: { time_ms: number; value: number }[] } | null>(null)

  // Flush pending volume keyframe update to DB
  const flushVolumeKeyframeUpdate = useCallback(() => {
    if (volumeKeyframeDebounceRef.current) {
      clearTimeout(volumeKeyframeDebounceRef.current)
      volumeKeyframeDebounceRef.current = null
    }
    if (pendingVolumeKeyframeUpdate.current) {
      const { trackId, clipId, keyframes } = pendingVolumeKeyframeUpdate.current
      const updatedTracks = timeline.audio_tracks.map(t =>
        t.id === trackId
          ? { ...t, clips: t.clips.map(c => c.id === clipId ? { ...c, volume_keyframes: keyframes } : c) }
          : t
      )
      updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
      pendingVolumeKeyframeUpdate.current = null
    }
  }, [timeline, projectId, updateTimeline])

  const handleVolumeKeyframeAdd = useCallback((trackId: string, clipId: string, timeMs: number, value: number) => {
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    const clip = track?.clips.find(c => c.id === clipId)
    if (!clip) return

    const newKeyframes = addVolumeKeyframe(clip.volume_keyframes, timeMs, value)
    const updatedTracks = timeline.audio_tracks.map(t =>
      t.id === trackId
        ? { ...t, clips: t.clips.map(c => c.id === clipId ? { ...c, volume_keyframes: newKeyframes } : c) }
        : t
    )
    // Direct update for add (not during drag)
    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }, [timeline, projectId, updateTimeline])

  const handleVolumeKeyframeUpdate = useCallback((trackId: string, clipId: string, index: number, timeMs: number, value: number) => {
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    const clip = track?.clips.find(c => c.id === clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    if (index < 0 || index >= sortedKeyframes.length) return

    // Update the keyframe at the given index
    const newKeyframes = sortedKeyframes.map((kf, i) =>
      i === index ? { time_ms: Math.round(timeMs), value: Math.max(0, Math.min(1, value)) } : kf
    )

    // Store pending update and debounce DB save
    pendingVolumeKeyframeUpdate.current = { trackId, clipId, keyframes: newKeyframes }

    // Clear existing debounce timer
    if (volumeKeyframeDebounceRef.current) {
      clearTimeout(volumeKeyframeDebounceRef.current)
    }

    // Debounce: save to DB after 300ms of no updates
    volumeKeyframeDebounceRef.current = setTimeout(() => {
      flushVolumeKeyframeUpdate()
    }, 300)
  }, [timeline, flushVolumeKeyframeUpdate])

  const handleVolumeKeyframeRemove = useCallback((trackId: string, clipId: string, index: number) => {
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    const clip = track?.clips.find(c => c.id === clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    if (index < 0 || index >= sortedKeyframes.length) return

    // Remove the keyframe at the given index
    const newKeyframes = sortedKeyframes.filter((_, i) => i !== index)

    const updatedTracks = timeline.audio_tracks.map(t =>
      t.id === trackId
        ? { ...t, clips: t.clips.map(c => c.id === clipId ? { ...c, volume_keyframes: newKeyframes } : c) }
        : t
    )
    // Direct update for remove (not during drag)
    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }, [timeline, projectId, updateTimeline])

  // Layer management
  const handleAddLayer = async () => {
    const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
    const newLayer = {
      id: uuidv4(),
      name: `レイヤー ${timeline.layers.length + 1}`,
      order: maxOrder + 1,
      visible: true,
      locked: false,
      clips: [],
    }
    await updateTimeline(projectId, { ...timeline, layers: [newLayer, ...timeline.layers] })
  }

  const handleDeleteLayer = async (layerId: string) => {
    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) return
    if (layer.clips.length > 0) {
      if (!confirm('このレイヤーにはクリップが含まれています。削除しますか？')) return
    }

    // Collect all group IDs from the layer being deleted
    const groupIdsToDelete = new Set(layer.clips.map(c => c.group_id).filter(Boolean) as string[])

    const updatedLayers = timeline.layers.filter(l => l.id !== layerId)

    // Also remove audio clips in the same group as deleted video clips
    const updatedTracks = timeline.audio_tracks.map((track) => ({
      ...track,
      clips: track.clips.filter((c) => {
        // Remove if in the same group as any clip in the deleted layer
        if (c.group_id && groupIdsToDelete.has(c.group_id)) {
          console.log('[handleDeleteLayer] Removing grouped audio clip:', c.id)
          return false
        }
        return true
      })
    }))

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }

  const handleToggleLayerVisibility = async (layerId: string) => {
    const updatedLayers = timeline.layers.map(layer =>
      layer.id === layerId ? { ...layer, visible: !layer.visible } : layer
    )
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  const handleToggleLayerLock = async (layerId: string) => {
    const updatedLayers = timeline.layers.map(layer =>
      layer.id === layerId ? { ...layer, locked: !layer.locked } : layer
    )
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  const handleMoveLayerUp = async (layerId: string) => {
    const index = timeline.layers.findIndex(l => l.id === layerId)
    if (index <= 0) return // Already at top
    const updatedLayers = [...timeline.layers]
    ;[updatedLayers[index - 1], updatedLayers[index]] = [updatedLayers[index], updatedLayers[index - 1]]
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  const handleMoveLayerDown = async (layerId: string) => {
    const index = timeline.layers.findIndex(l => l.id === layerId)
    if (index < 0 || index >= timeline.layers.length - 1) return // Already at bottom
    const updatedLayers = [...timeline.layers]
    ;[updatedLayers[index], updatedLayers[index + 1]] = [updatedLayers[index + 1], updatedLayers[index]]
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  // Layer drag-and-drop reordering
  const handleLayerReorderDragStart = (e: React.DragEvent, layerId: string) => {
    setDraggingLayerId(layerId)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('application/x-layer-reorder', layerId)
  }

  const handleLayerReorderDragOver = (e: React.DragEvent, targetIndex: number) => {
    // Only handle if this is a layer reorder drag (not asset drop)
    if (!e.dataTransfer.types.includes('application/x-layer-reorder')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDropTargetIndex(targetIndex)
  }

  const handleLayerReorderDragLeave = () => {
    setDropTargetIndex(null)
  }

  const handleLayerReorderDrop = async (e: React.DragEvent, targetIndex: number) => {
    // Only handle if this is a layer reorder drag (not asset drop)
    if (!e.dataTransfer.types.includes('application/x-layer-reorder')) return
    e.preventDefault()
    if (!draggingLayerId) return

    const sourceIndex = timeline.layers.findIndex(l => l.id === draggingLayerId)
    if (sourceIndex < 0 || sourceIndex === targetIndex) {
      setDraggingLayerId(null)
      setDropTargetIndex(null)
      return
    }

    const updatedLayers = [...timeline.layers]
    const [movedLayer] = updatedLayers.splice(sourceIndex, 1)
    const adjustedTargetIndex = targetIndex > sourceIndex ? targetIndex - 1 : targetIndex
    updatedLayers.splice(adjustedTargetIndex, 0, movedLayer)

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
    setDraggingLayerId(null)
    setDropTargetIndex(null)
  }

  const handleLayerReorderDragEnd = () => {
    setDraggingLayerId(null)
    setDropTargetIndex(null)
  }

  // Shape creation
  const handleAddShape = async (shapeType: ShapeType, shapeName?: string) => {
    // Create shape on: selected layer > layer with selected clip > first layer > new layer
    let targetLayerId = selectedLayerId || selectedVideoClip?.layerId || timeline.layers[0]?.id
    let updatedLayers = [...timeline.layers]

    // Check if target layer is locked
    const targetLayer = timeline.layers.find(l => l.id === targetLayerId)
    if (targetLayer?.locked) {
      // Find first unlocked layer
      const unlockedLayer = timeline.layers.find(l => !l.locked)
      if (unlockedLayer) {
        targetLayerId = unlockedLayer.id
      } else {
        alert('すべてのレイヤーがロックされています')
        return
      }
    }

    if (!targetLayerId) {
      // Create a new layer first
      const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
      const newLayer = {
        id: uuidv4(),
        name: 'シェイプレイヤー 1',
        order: maxOrder + 1,
        visible: true,
        locked: false,
        clips: [] as Clip[],
      }
      updatedLayers = [newLayer]
      targetLayerId = newLayer.id
    } else {
      // Issue #016: Check if target layer has video clips (video and shapes cannot coexist)
      if (layerHasVideoClips(targetLayerId)) {
        console.log('[handleAddShape] Target layer has video clips, finding/creating compatible layer')
        const result = await findOrCreateShapeCompatibleLayer(targetLayerId)
        targetLayerId = result.layerId
        updatedLayers = result.updatedLayers
      }
    }

    // Default shape properties
    const defaultShape: Shape = {
      type: shapeType,
      name: shapeName,  // Optional name provided by user
      width: shapeType === 'circle' ? 100 : 150,
      height: shapeType === 'circle' ? 100 : (shapeType === 'line' ? 4 : 100),
      fillColor: 'transparent',
      strokeColor: '#FF0000',
      strokeWidth: 5,
      filled: false,
    }

    // Create new shape clip at current time (or 0)
    const newClip: Clip = {
      id: uuidv4(),
      asset_id: null,
      shape: defaultShape,
      start_ms: currentTimeMs,
      duration_ms: 5000, // 5 seconds default
      in_point_ms: 0,
      out_point_ms: null,
      transform: {
        x: 0,
        y: 0,
        width: null,
        height: null,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    // Add clip to target layer
    updatedLayers = updatedLayers.map(layer => {
      if (layer.id === targetLayerId) {
        return { ...layer, clips: [...layer.clips, newClip] }
      }
      return layer
    })

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  const handleAddText = async () => {
    // Create text clip on: selected layer > layer with selected clip > first layer > new layer
    let targetLayerId = selectedLayerId || selectedVideoClip?.layerId || timeline.layers[0]?.id
    let updatedLayers = [...timeline.layers]

    // Check if target layer is locked
    const targetLayer = timeline.layers.find(l => l.id === targetLayerId)
    if (targetLayer?.locked) {
      // Find first unlocked layer
      const unlockedLayer = timeline.layers.find(l => !l.locked)
      if (unlockedLayer) {
        targetLayerId = unlockedLayer.id
      } else {
        alert('すべてのレイヤーがロックされています')
        return
      }
    }

    if (!targetLayerId) {
      // Create a new layer first
      const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
      const newLayer = {
        id: uuidv4(),
        name: 'レイヤー 1',
        order: maxOrder + 1,
        visible: true,
        locked: false,
        clips: [] as Clip[],
      }
      updatedLayers = [newLayer]
      targetLayerId = newLayer.id
    }

    // Default text style
    const defaultTextStyle: TextStyle = {
      fontFamily: 'Noto Sans JP',
      fontSize: 48,
      fontWeight: 'bold',
      fontStyle: 'normal',
      color: '#ffffff',
      backgroundColor: '#000000',
      backgroundOpacity: 0.4,
      textAlign: 'center',
      verticalAlign: 'middle',
      lineHeight: 1.4,
      letterSpacing: 0,
      strokeColor: '#000000',
      strokeWidth: 2,
    }

    // Create new text clip at current time
    const newClip: Clip = {
      id: uuidv4(),
      asset_id: null,
      text_content: 'テキストを入力',
      text_style: defaultTextStyle,
      start_ms: currentTimeMs,
      duration_ms: 5000, // 5 seconds default
      in_point_ms: 0,
      out_point_ms: null,
      transform: {
        x: 0,
        y: 0,
        width: null,
        height: null,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    // Add clip to target layer
    updatedLayers = updatedLayers.map(layer => {
      if (layer.id === targetLayerId) {
        return { ...layer, clips: [...layer.clips, newClip] }
      }
      return layer
    })

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }

  const handleStartRenameLayer = (layerId: string, currentName: string) => {
    setEditingLayerId(layerId)
    setEditingLayerName(currentName)
  }

  const handleFinishRenameLayer = async () => {
    if (editingLayerId && editingLayerName.trim()) {
      const updatedLayers = timeline.layers.map(layer =>
        layer.id === editingLayerId ? { ...layer, name: editingLayerName.trim() } : layer
      )
      await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
    }
    setEditingLayerId(null)
    setEditingLayerName('')
  }

  const handleCancelRenameLayer = () => {
    setEditingLayerId(null)
    setEditingLayerName('')
  }

  // Audio track management
  const handleAddAudioTrack = async (type: 'narration' | 'bgm' | 'se') => {
    const typeNames = { narration: 'ナレーション', bgm: 'BGM', se: 'SE' }
    const newTrack = {
      id: uuidv4(),
      name: `${typeNames[type]} ${timeline.audio_tracks.filter(t => t.type === type).length + 1}`,
      type,
      volume: 1.0,
      muted: false,
      ducking: type === 'bgm' ? { enabled: true, duck_to: 0.3, attack_ms: 200, release_ms: 500 } : undefined,
      clips: [],
    }
    await updateTimeline(projectId, { ...timeline, audio_tracks: [...timeline.audio_tracks, newTrack] })
  }

  const handleDeleteAudioTrack = async (trackId: string) => {
    const track = timeline.audio_tracks.find(t => t.id === trackId)
    if (!track) return
    if (track.clips.length > 0) {
      if (!confirm('このトラックにはクリップが含まれています。削除しますか？')) return
    }
    const updatedTracks = timeline.audio_tracks.filter(t => t.id !== trackId)
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }


  // Get group info for a clip
  const getClipGroup = useCallback((groupId: string | null | undefined): ClipGroup | undefined => {
    if (!groupId) return undefined
    return timeline.groups?.find(g => g.id === groupId)
  }, [timeline.groups])

  const handleDragOver = useCallback((e: React.DragEvent, trackId: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDragOverTrack(trackId)
  }, [])

  const handleDragLeave = useCallback(() => {
    setDragOverTrack(null)
  }, [])

  const handleDrop = useCallback(async (e: React.DragEvent, trackId: string) => {
    e.preventDefault()
    setDragOverTrack(null)
    console.log('[handleDrop] START - trackId:', trackId)

    const assetId = e.dataTransfer.getData('application/x-asset-id')
    const assetType = e.dataTransfer.getData('application/x-asset-type')
    console.log('[handleDrop] assetId:', assetId, 'assetType:', assetType)

    if (!assetId || assetType !== 'audio') {
      console.log('[handleDrop] SKIP - not audio or no assetId')
      return
    }

    const asset = assets.find(a => a.id === assetId)
    console.log('[handleDrop] asset found:', asset)
    if (!asset) {
      console.log('[handleDrop] SKIP - asset not found in assets array. Available:', assets.map(a => a.id))
      return
    }

    const track = timeline.audio_tracks.find(t => t.id === trackId)
    if (!track) {
      console.log('[handleDrop] SKIP - track not found')
      return
    }

    // Snap to end of last clip in the track (or 0 if empty)
    const lastClipEndMs = track.clips.length > 0
      ? Math.max(...track.clips.map(c => c.start_ms + c.duration_ms))
      : 0
    const startMs = lastClipEndMs
    console.log('[handleDrop] Snapping to end of last clip:', startMs)

    // Create new clip
    const newClip: AudioClip = {
      id: uuidv4(),
      asset_id: assetId,
      start_ms: startMs,
      duration_ms: asset.duration_ms || 5000,
      in_point_ms: 0,
      out_point_ms: null,
      volume: 1.0,
      fade_in_ms: 0,
      fade_out_ms: 0,
    }
    console.log('[handleDrop] Creating clip:', newClip)

    const updatedTracks = timeline.audio_tracks.map((t) =>
      t.id === trackId ? { ...t, clips: [...t.clips, newClip] } : t
    )

    // Update duration if needed
    const newDuration = Math.max(
      timeline.duration_ms,
      startMs + (asset.duration_ms || 5000)
    )

    console.log('[handleDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      audio_tracks: updatedTracks,
      duration_ms: newDuration,
    })
    console.log('[handleDrop] DONE')
  }, [assets, timeline, projectId, updateTimeline])

  // Video layer drag handlers
  const handleLayerDragOver = useCallback((e: React.DragEvent, layerId: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDragOverLayer(layerId)

    // Calculate drop preview position
    const assetId = e.dataTransfer.types.includes('application/x-asset-id')
      ? e.dataTransfer.getData('application/x-asset-id')
      : null
    const layerEl = layerRefs.current[layerId]

    if (layerEl) {
      const rect = layerEl.getBoundingClientRect()
      const offsetX = e.clientX - rect.left + (tracksScrollRef.current?.scrollLeft || 0)
      const dropTimeMs = Math.max(0, Math.round((offsetX / pixelsPerSecond) * 1000))

      // Get asset duration for preview width (default to 5000ms if unknown)
      let durationMs = 5000
      if (assetId) {
        const asset = assets.find(a => a.id === assetId)
        if (asset?.duration_ms) {
          durationMs = asset.duration_ms
        }
      }

      setDropPreview({
        layerId,
        timeMs: dropTimeMs,
        durationMs,
      })
    }
  }, [assets, pixelsPerSecond])

  const handleLayerDragLeave = useCallback(() => {
    setDragOverLayer(null)
    setDropPreview(null)
  }, [])

  const handleLayerDrop = useCallback(async (e: React.DragEvent, layerId: string) => {
    e.preventDefault()
    setDragOverLayer(null)
    setDropPreview(null)
    console.log('[handleLayerDrop] START - layerId:', layerId)

    const assetId = e.dataTransfer.getData('application/x-asset-id')
    const assetType = e.dataTransfer.getData('application/x-asset-type')
    console.log('[handleLayerDrop] assetId:', assetId, 'assetType:', assetType)

    // Accept video and image assets
    if (!assetId || (assetType !== 'video' && assetType !== 'image')) {
      console.log('[handleLayerDrop] SKIP - not video/image or no assetId')
      return
    }

    const asset = assets.find(a => a.id === assetId)
    console.log('[handleLayerDrop] asset found:', asset)
    if (!asset) {
      console.log('[handleLayerDrop] SKIP - asset not found')
      return
    }

    let targetLayerId = layerId
    let updatedLayers = [...timeline.layers]

    const layer = timeline.layers.find(l => l.id === targetLayerId)
    if (!layer) {
      console.log('[handleLayerDrop] SKIP - layer not found')
      return
    }

    if (layer.locked) {
      console.log('[handleLayerDrop] SKIP - layer is locked')
      return
    }

    // Issue #016: Video and shape clips cannot coexist on the same layer
    // Check if dropping a video on a layer with shapes
    if (assetType === 'video' && layerHasShapeClips(targetLayerId)) {
      console.log('[handleLayerDrop] Target layer has shape clips, finding/creating compatible layer')
      const result = await findOrCreateVideoCompatibleLayer(targetLayerId)
      targetLayerId = result.layerId
      updatedLayers = result.updatedLayers
    }

    // Calculate insertion position from mouse X coordinate
    const layerEl = layerRefs.current[targetLayerId]
    let startMs = 0
    if (layerEl) {
      const rect = layerEl.getBoundingClientRect()
      const offsetX = e.clientX - rect.left + (tracksScrollRef.current?.scrollLeft || 0)
      startMs = Math.max(0, Math.round((offsetX / pixelsPerSecond) * 1000))
      console.log('[handleLayerDrop] Drop position calculated:', startMs, 'ms')
    }

    // For images, use the default image duration; for videos, use asset duration
    const clipDurationMs = assetType === 'image'
      ? defaultImageDurationMs
      : (asset.duration_ms || 5000)

    // Generate group_id for linking video and audio clips
    const groupId = uuidv4()

    // For video assets, extract audio first (await to get audio asset before placing clips)
    let audioAsset: typeof assets[0] | null = null
    if (assetType === 'video') {
      console.log('[handleLayerDrop] Extracting audio (await)...')
      setIsExtractingAudio(true)
      try {
        // This will return existing audio asset if already extracted, or extract new one
        audioAsset = await assetsApi.extractAudio(projectId, assetId)
        console.log('[handleLayerDrop] Audio asset ready:', audioAsset)
      } catch (err) {
        console.log('[handleLayerDrop] Audio extraction failed:', err)
        // Continue without audio - video can still be placed
      } finally {
        setIsExtractingAudio(false)
      }
    }

    // Create new video clip with default transform and effects
    const newClip: Clip = {
      id: uuidv4(),
      asset_id: assetId,
      start_ms: startMs,
      duration_ms: clipDurationMs,
      in_point_ms: 0,
      out_point_ms: assetType === 'video' ? (asset.duration_ms || null) : null,
      group_id: assetType === 'video' ? groupId : undefined,  // Link with audio if video
      transform: {
        x: 0,
        y: 0,
        width: asset.width || null,
        height: asset.height || null,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
        ...(asset.chroma_key_color ? {
          chroma_key: {
            enabled: true,
            color: asset.chroma_key_color,
            similarity: 0.05,
            blend: 0.0,
          },
        } : {}),
      },
    }
    console.log('[handleLayerDrop] Creating clip:', newClip)
    console.log('[handleLayerDrop] asset.width:', asset.width, 'asset.height:', asset.height)
    console.log('[handleLayerDrop] clip.transform:', JSON.stringify(newClip.transform))

    // Add clip to target layer (simple placement, no ripple edit)
    updatedLayers = updatedLayers.map((l) => {
      if (l.id !== targetLayerId) return l
      return { ...l, clips: [...l.clips, newClip] }
    })

    let updatedAudioTracks = timeline.audio_tracks

    // For video assets with audio, add to appropriate track based on layer type
    // avatar/effects/text → Narration, background/content → BGM
    if (assetType === 'video' && audioAsset) {
      const audioClip: AudioClip = {
        id: uuidv4(),
        asset_id: audioAsset.id,
        start_ms: startMs,
        duration_ms: audioAsset.duration_ms || asset.duration_ms || 5000,
        in_point_ms: 0,
        out_point_ms: audioAsset.duration_ms || null,
        volume: 1,
        fade_in_ms: 0,
        fade_out_ms: 0,
        group_id: groupId,
      }

      // Determine target audio track type based on layer type
      const targetTrackType: 'narration' | 'bgm' =
        ['avatar', 'effects', 'text'].includes(layer?.type || '') ? 'narration' : 'bgm'

      // Find an empty track of the target type or create a new one
      const emptyTargetTrack = updatedAudioTracks.find(
        t => t.type === targetTrackType && t.clips.length === 0
      )

      if (emptyTargetTrack) {
        // Add clip to existing empty track
        updatedAudioTracks = updatedAudioTracks.map(t =>
          t.id === emptyTargetTrack.id
            ? { ...t, clips: [...t.clips, audioClip] }
            : t
        )
      } else {
        // Create new track of target type
        const trackCount = updatedAudioTracks.filter(t => t.type === targetTrackType).length
        const trackLabel = targetTrackType === 'narration' ? 'Narration' : 'BGM'
        const newAudioTrack: AudioTrack = {
          id: uuidv4(),
          name: trackCount === 0 ? trackLabel : `${trackLabel} ${trackCount + 1}`,
          type: targetTrackType,
          volume: 1,
          muted: false,
          clips: [audioClip],
        }
        updatedAudioTracks = [...updatedAudioTracks, newAudioTrack]
      }
    }

    // Calculate new duration
    const maxVideoEndMs = updatedLayers.reduce((max, l) => {
      const layerMax = l.clips.reduce((m, c) => Math.max(m, c.start_ms + c.duration_ms), 0)
      return Math.max(max, layerMax)
    }, 0)
    const maxAudioEndMs = updatedAudioTracks.reduce((max, t) => {
      const trackMax = t.clips.reduce((m, c) => Math.max(m, c.start_ms + c.duration_ms), 0)
      return Math.max(max, trackMax)
    }, 0)
    const newDuration = Math.max(timeline.duration_ms, maxVideoEndMs, maxAudioEndMs)

    // Update timeline with video clip and audio track/clip together
    console.log('[handleLayerDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedAudioTracks,
      duration_ms: newDuration,
    })
    console.log('[handleLayerDrop] DONE')
  }, [assets, timeline, projectId, updateTimeline, layerHasShapeClips, findOrCreateVideoCompatibleLayer, pixelsPerSecond, defaultImageDurationMs])

  // Handle drop on new layer zone (creates new layer and adds clip)
  const handleNewLayerDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    setIsDraggingNewLayer(false)
    console.log('[handleNewLayerDrop] START')

    const assetId = e.dataTransfer.getData('application/x-asset-id')
    const assetType = e.dataTransfer.getData('application/x-asset-type')
    console.log('[handleNewLayerDrop] assetId:', assetId, 'assetType:', assetType)

    // Accept video and image assets
    if (!assetId || (assetType !== 'video' && assetType !== 'image')) {
      console.log('[handleNewLayerDrop] SKIP - not video/image or no assetId')
      return
    }

    const asset = assets.find(a => a.id === assetId)
    console.log('[handleNewLayerDrop] asset found:', asset)
    if (!asset) {
      console.log('[handleNewLayerDrop] SKIP - asset not found')
      return
    }

    // Create new layer (prepend to array = topmost in UI)
    // Default to 'content' type - audio will route to BGM track
    const newLayerId = uuidv4()
    const layerCount = timeline.layers.length
    const maxOrder = timeline.layers.reduce((max, l) => Math.max(max, l.order), -1)
    const newLayerType: 'content' = 'content'
    const newLayer = {
      id: newLayerId,
      name: `レイヤー ${layerCount + 1}`,
      type: newLayerType,
      order: maxOrder + 1,
      visible: true,
      locked: false,
      clips: [] as Clip[],
    }

    // New layer has no clips, so start at 0ms
    const startMs = 0
    console.log('[handleNewLayerDrop] New layer, placing clip at 0ms')

    // Generate group_id for linking video and audio clips
    const groupId = uuidv4()

    // For video assets, extract audio first (await to get audio asset before placing clips)
    let audioAsset: typeof assets[0] | null = null
    if (assetType === 'video') {
      console.log('[handleNewLayerDrop] Extracting audio (await)...')
      setIsExtractingAudio(true)
      try {
        // This will return existing audio asset if already extracted, or extract new one
        audioAsset = await assetsApi.extractAudio(projectId, assetId)
        console.log('[handleNewLayerDrop] Audio asset ready:', audioAsset)
      } catch (err) {
        console.log('[handleNewLayerDrop] Audio extraction failed:', err)
        // Continue without audio - video can still be placed
      } finally {
        setIsExtractingAudio(false)
      }
    }

    // For images, use the default image duration; for videos, use asset duration
    const clipDurationMs = assetType === 'image'
      ? defaultImageDurationMs
      : (asset.duration_ms || 5000)

    const newClip: Clip = {
      id: uuidv4(),
      asset_id: asset.id,
      start_ms: startMs,
      duration_ms: clipDurationMs,
      in_point_ms: 0,
      out_point_ms: assetType === 'video' ? (asset.duration_ms || null) : null,
      group_id: assetType === 'video' ? groupId : undefined,  // Link with audio if video
      transform: {
        x: 0,
        y: 0,
        scale: 1,
        rotation: 0,
        width: asset.width || null,
        height: asset.height || null,
      },
      effects: {
        opacity: 1,
        ...(asset.chroma_key_color ? {
          chroma_key: {
            enabled: true,
            color: asset.chroma_key_color,
            similarity: 0.05,
            blend: 0.0,
          },
        } : {}),
      },
    }

    // Add clip to new layer
    newLayer.clips.push(newClip)
    console.log('[handleNewLayerDrop] Creating layer:', newLayer)

    // Update timeline duration if needed
    const newDuration = Math.max(
      timeline.duration_ms,
      startMs + clipDurationMs
    )

    // For video assets with audio, add to appropriate track based on layer type
    // New layers default to 'content' type → BGM track
    let updatedAudioTracks = timeline.audio_tracks
    if (assetType === 'video' && audioAsset) {
      const audioClip: AudioClip = {
        id: uuidv4(),
        asset_id: audioAsset.id,
        start_ms: startMs,
        duration_ms: audioAsset.duration_ms || asset.duration_ms || 5000,
        in_point_ms: 0,
        out_point_ms: audioAsset.duration_ms || null,
        volume: 1,
        fade_in_ms: 0,
        fade_out_ms: 0,
        group_id: groupId,
      }

      // Determine target audio track type based on layer type
      // avatar/effects/text → narration, background/content → bgm
      const targetTrackType: 'narration' | 'bgm' =
        ['avatar', 'effects', 'text'].includes(newLayerType) ? 'narration' : 'bgm'

      // Find an empty track of the target type or create a new one
      const emptyTargetTrack = timeline.audio_tracks.find(
        t => t.type === targetTrackType && t.clips.length === 0
      )

      if (emptyTargetTrack) {
        // Add clip to existing empty track
        updatedAudioTracks = timeline.audio_tracks.map(t =>
          t.id === emptyTargetTrack.id
            ? { ...t, clips: [audioClip] }
            : t
        )
      } else {
        // Create new track of target type
        const trackCount = timeline.audio_tracks.filter(t => t.type === targetTrackType).length
        const trackLabel = targetTrackType === 'narration' ? 'Narration' : 'BGM'
        const newAudioTrack: AudioTrack = {
          id: uuidv4(),
          name: trackCount === 0 ? trackLabel : `${trackLabel} ${trackCount + 1}`,
          type: targetTrackType,
          volume: 1,
          muted: false,
          clips: [audioClip],
        }
        updatedAudioTracks = [...timeline.audio_tracks, newAudioTrack]
      }
    }

    // Update timeline with video clip and audio track/clip together
    console.log('[handleNewLayerDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      layers: [newLayer, ...timeline.layers],
      audio_tracks: updatedAudioTracks,
      duration_ms: newDuration,
    })
    console.log('[handleNewLayerDrop] DONE')
  }, [assets, timeline, projectId, updateTimeline, defaultImageDurationMs])

  // Context menu handlers
  const handleContextMenu = useCallback((
    e: React.MouseEvent,
    clipId: string,
    type: 'video' | 'audio',
    layerId?: string,
    trackId?: string
  ) => {
    e.preventDefault()
    e.stopPropagation()

    // Find overlapping clips at this position
    let overlappingClips: Array<{ clipId: string; name: string }> = []

    if (type === 'video' && layerId) {
      const overlappingIds = videoClipOverlaps.get(clipId)
      if (overlappingIds && overlappingIds.size > 0) {
        const layer = timeline.layers.find(l => l.id === layerId)
        if (layer) {
          // Include the clicked clip and all overlapping clips
          const allClipIds = [clipId, ...overlappingIds]
          overlappingClips = allClipIds
            .map(id => {
              const clip = layer.clips.find(c => c.id === id)
              if (!clip) return null
              const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
              const name = clip.text_content
                ? `テキスト: ${clip.text_content.slice(0, 15)}${clip.text_content.length > 15 ? '...' : ''}`
                : clip.shape
                  ? `シェイプ: ${clip.shape.type}`
                  : asset?.name || 'クリップ'
              return { clipId: id, name }
            })
            .filter((c): c is { clipId: string; name: string } => c !== null)
        }
      }
    } else if (type === 'audio' && trackId) {
      const overlappingIds = audioClipOverlaps.get(clipId)
      if (overlappingIds && overlappingIds.size > 0) {
        const track = timeline.audio_tracks.find(t => t.id === trackId)
        if (track) {
          // Include the clicked clip and all overlapping clips
          const allClipIds = [clipId, ...overlappingIds]
          overlappingClips = allClipIds
            .map(id => {
              const clip = track.clips.find(c => c.id === id)
              if (!clip) return null
              const asset = assets.find(a => a.id === clip.asset_id)
              return { clipId: id, name: asset?.name || 'オーディオクリップ' }
            })
            .filter((c): c is { clipId: string; name: string } => c !== null)
        }
      }
    }

    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      clipId,
      layerId,
      trackId,
      type,
      overlappingClips: overlappingClips.length > 1 ? overlappingClips : undefined,
    })
  }, [videoClipOverlaps, audioClipOverlaps, timeline.layers, timeline.audio_tracks, assets])

  const handleCloseContextMenu = useCallback(() => {
    setContextMenu(null)
  }, [])

  // Group selected clips (video + audio) into a new group
  const handleGroupClips = useCallback(async () => {
    if (selectedVideoClips.size === 0 && selectedAudioClips.size === 0) return

    const newGroupId = uuidv4()
    const newGroup: ClipGroup = {
      id: newGroupId,
      name: `グループ ${(timeline.groups?.length || 0) + 1}`,
      color: `hsl(${Math.random() * 360}, 70%, 50%)`,
    }

    // Update video clips
    const updatedLayers = timeline.layers.map(layer => ({
      ...layer,
      clips: layer.clips.map(clip =>
        selectedVideoClips.has(clip.id) ? { ...clip, group_id: newGroupId } : clip
      ),
    }))

    // Update audio clips
    const updatedTracks = timeline.audio_tracks.map(track => ({
      ...track,
      clips: track.clips.map(clip =>
        selectedAudioClips.has(clip.id) ? { ...clip, group_id: newGroupId } : clip
      ),
    }))

    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedTracks,
      groups: [...(timeline.groups || []), newGroup],
    })

    // Clear multi-selection
    setSelectedVideoClips(new Set())
    setSelectedAudioClips(new Set())
    setContextMenu(null)
  }, [selectedVideoClips, selectedAudioClips, timeline, projectId, updateTimeline])

  // Ungroup a clip (remove from its group)
  const handleUngroupClip = useCallback(async (clipId: string, type: 'video' | 'audio') => {
    console.log('[handleUngroupClip] START - clipId:', clipId, 'type:', type)
    let groupIdToCheck: string | null = null

    if (type === 'video') {
      // Find the clip and its group
      for (const layer of timeline.layers) {
        const clip = layer.clips.find(c => c.id === clipId)
        if (clip?.group_id) {
          groupIdToCheck = clip.group_id
          break
        }
      }
    } else {
      // Find the audio clip and its group
      for (const track of timeline.audio_tracks) {
        const clip = track.clips.find(c => c.id === clipId)
        if (clip?.group_id) {
          groupIdToCheck = clip.group_id
          console.log('[handleUngroupClip] Found audio clip with group_id:', groupIdToCheck)
          break
        }
      }
    }

    if (!groupIdToCheck) {
      console.log('[handleUngroupClip] No group_id found, returning early')
      return
    }

    console.log('[handleUngroupClip] Clearing group_id:', groupIdToCheck)

    // Remove group_id from all clips in the group
    const updatedLayers = timeline.layers.map(layer => ({
      ...layer,
      clips: layer.clips.map(clip =>
        clip.group_id === groupIdToCheck
          ? { ...clip, group_id: null }
          : clip
      ),
    }))

    const updatedTracks = timeline.audio_tracks.map(track => ({
      ...track,
      clips: track.clips.map(clip => {
        if (clip.group_id === groupIdToCheck) {
          console.log('[handleUngroupClip] Clearing group_id for audio clip:', clip.id)
          return { ...clip, group_id: null }
        }
        return clip
      }),
    }))

    // Remove the group from groups array
    const updatedGroups = (timeline.groups || []).filter(g => g.id !== groupIdToCheck)

    console.log('[handleUngroupClip] Calling updateTimeline')
    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedTracks,
      groups: updatedGroups,
    })
    console.log('[handleUngroupClip] DONE')
    // Clear multi-selection to prevent clips from moving together
    setSelectedVideoClips(new Set())
    setSelectedAudioClips(new Set())
    setContextMenu(null)
  }, [timeline, projectId, updateTimeline])


  const handleDeleteClip = useCallback(async () => {
    console.log('[handleDeleteClip] called - selectedClip:', selectedClip, 'selectedVideoClip:', selectedVideoClip)
    if (selectedClip) {
      console.log('[handleDeleteClip] Deleting audio clip')
      const updatedTracks = timeline.audio_tracks.map((track) =>
        track.id === selectedClip.trackId
          ? { ...track, clips: track.clips.filter((c) => c.id !== selectedClip.clipId) }
          : track
      )
      await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
      setSelectedClip(null)
      if (onClipSelect) onClipSelect(null)
    } else if (selectedVideoClip) {
      console.log('[handleDeleteClip] Deleting video clip:', selectedVideoClip)

      // Find the video clip to get its group_id
      const layer = timeline.layers.find(l => l.id === selectedVideoClip.layerId)
      const videoClip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
      const groupId = videoClip?.group_id

      // Remove the video clip from layers
      const updatedLayers = timeline.layers.map((layer) =>
        layer.id === selectedVideoClip.layerId
          ? { ...layer, clips: layer.clips.filter((c) => c.id !== selectedVideoClip.clipId) }
          : layer
      )

      // Also remove audio clips in the same group
      const updatedTracks = timeline.audio_tracks.map((track) => ({
        ...track,
        clips: track.clips.filter((c) => {
          // Remove if in the same group
          if (groupId && c.group_id === groupId) {
            console.log('[handleDeleteClip] Removing grouped audio clip:', c.id)
            return false
          }
          return true
        })
      }))

      await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
      setSelectedVideoClip(null)
      if (onVideoClipSelect) onVideoClipSelect(null)
    } else {
      console.log('[handleDeleteClip] No clip selected')
    }
  }, [selectedClip, selectedVideoClip, timeline, projectId, updateTimeline, onClipSelect, onVideoClipSelect])

  // Cut clip at playhead position (with group support)
  const handleCutClip = useCallback(async () => {
    console.log('[handleCutClip] called - currentTimeMs:', currentTimeMs)

    // Helper function to cut a video clip
    const cutVideoClip = (clip: Clip, cutTimeMs: number, newGroupId1: string | null, newGroupId2: string | null): { clip1: Clip, clip2: Clip } | null => {
      const clipEnd = clip.start_ms + clip.duration_ms
      if (cutTimeMs <= clip.start_ms || cutTimeMs >= clipEnd) {
        return null // Cut position not within clip bounds
      }

      const timeIntoClip = cutTimeMs - clip.start_ms

      const clip1: Clip = {
        ...clip,
        duration_ms: timeIntoClip,
        out_point_ms: (clip.in_point_ms || 0) + timeIntoClip,
        group_id: newGroupId1,
      }

      const newInPointMs = (clip.in_point_ms || 0) + timeIntoClip
      const newDurationMs = clip.duration_ms - timeIntoClip
      const clip2: Clip = {
        ...clip,
        id: uuidv4(),
        start_ms: cutTimeMs,
        duration_ms: newDurationMs,
        in_point_ms: newInPointMs,
        out_point_ms: newInPointMs + newDurationMs,
        group_id: newGroupId2,
        keyframes: clip.keyframes?.map(kf => ({
          ...kf,
          time_ms: kf.time_ms - timeIntoClip,
        })).filter(kf => kf.time_ms >= 0),
      }

      return { clip1, clip2 }
    }

    // Helper function to cut an audio clip
    const cutAudioClip = (
      clip: AudioClip,
      cutTimeMs: number,
      newGroupId1: string | null,
      newGroupId2: string | null,
    ): { clip1: AudioClip, clip2: AudioClip } | null => {
      const clipEnd = clip.start_ms + clip.duration_ms
      if (cutTimeMs <= clip.start_ms || cutTimeMs >= clipEnd) {
        return null // Cut position not within clip bounds
      }

      const timeIntoClip = cutTimeMs - clip.start_ms

      // MICRO_FADE_MS: Apply 10ms micro-fades at cut points to eliminate click/pop noise
      const MICRO_FADE_MS = 10

      const clip1: AudioClip = {
        ...clip,
        duration_ms: timeIntoClip,
        out_point_ms: (clip.in_point_ms || 0) + timeIntoClip,
        // Original fade_in is preserved via spread; add micro-fade at cut point
        fade_out_ms: MICRO_FADE_MS,
        group_id: newGroupId1,
      }

      const newInPointMs = (clip.in_point_ms || 0) + timeIntoClip
      const newDurationMs = clip.duration_ms - timeIntoClip
      const clip2: AudioClip = {
        ...clip,
        id: uuidv4(),
        start_ms: cutTimeMs,
        duration_ms: newDurationMs,
        in_point_ms: newInPointMs,
        out_point_ms: newInPointMs + newDurationMs,
        // Add micro-fade at cut point; original fade_out is preserved via spread
        fade_in_ms: MICRO_FADE_MS,
        group_id: newGroupId2,
      }

      return { clip1, clip2 }
    }

    if (selectedVideoClip) {
      const layer = timeline.layers.find(l => l.id === selectedVideoClip.layerId)
      const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
      if (!clip || !layer) {
        console.log('[handleCutClip] SKIP - clip or layer not found')
        return
      }
      if (layer.locked) {
        console.log('[handleCutClip] SKIP - layer is locked')
        return
      }

      const clipEnd = clip.start_ms + clip.duration_ms
      if (currentTimeMs <= clip.start_ms || currentTimeMs >= clipEnd) {
        console.log('[handleCutClip] SKIP - playhead not within clip bounds')
        return
      }

      // Check if clip is part of a group
      if (clip.group_id) {
        console.log('[handleCutClip] Cutting group:', clip.group_id)

        // Collect all clips in the group
        const groupVideoClips: { clip: Clip, layerId: string }[] = []
        const groupAudioClips: { clip: AudioClip, trackId: string }[] = []

        for (const l of timeline.layers) {
          for (const c of l.clips) {
            if (c.group_id === clip.group_id) {
              groupVideoClips.push({ clip: c, layerId: l.id })
            }
          }
        }

        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (c.group_id === clip.group_id) {
              groupAudioClips.push({ clip: c, trackId: t.id })
            }
          }
        }

        console.log('[handleCutClip] Group clips found - video:', groupVideoClips.length, 'audio:', groupAudioClips.length)

        // Generate new group IDs for the cut clips (if there are multiple clips in the group)
        const hasMultipleClips = groupVideoClips.length + groupAudioClips.length > 1
        const newGroupId1 = hasMultipleClips ? uuidv4() : null
        const newGroupId2 = hasMultipleClips ? uuidv4() : null

        // Track which clips to add to each layer/track
        const videoClipUpdates: Map<string, { original: Clip, clip1: Clip, clip2: Clip }[]> = new Map()
        const audioClipUpdates: Map<string, { original: AudioClip, clip1: AudioClip, clip2: AudioClip }[]> = new Map()

        // Cut each video clip in the group
        for (const { clip: groupClip, layerId } of groupVideoClips) {
          const result = cutVideoClip(groupClip, currentTimeMs, newGroupId1, newGroupId2)
          if (result) {
            if (!videoClipUpdates.has(layerId)) {
              videoClipUpdates.set(layerId, [])
            }
            videoClipUpdates.get(layerId)!.push({ original: groupClip, ...result })
          }
        }

        // Cut each audio clip in the group
        for (const { clip: groupClip, trackId } of groupAudioClips) {
          const result = cutAudioClip(groupClip, currentTimeMs, newGroupId1, newGroupId2)
          if (result) {
            if (!audioClipUpdates.has(trackId)) {
              audioClipUpdates.set(trackId, [])
            }
            audioClipUpdates.get(trackId)!.push({ original: groupClip, ...result })
          }
        }

        // Apply updates to layers
        const updatedLayers = timeline.layers.map(l => {
          const updates = videoClipUpdates.get(l.id)
          if (!updates || updates.length === 0) return l

          const newClips = l.clips
            .map(c => {
              const update = updates.find(u => u.original.id === c.id)
              return update ? update.clip1 : c
            })
            .concat(updates.map(u => u.clip2))

          return { ...l, clips: newClips }
        })

        // Apply updates to audio tracks
        const updatedTracks = timeline.audio_tracks.map(t => {
          const updates = audioClipUpdates.get(t.id)
          if (!updates || updates.length === 0) return t

          const newClips = t.clips
            .map(c => {
              const update = updates.find(u => u.original.id === c.id)
              return update ? update.clip1 : c
            })
            .concat(updates.map(u => u.clip2))

          return { ...t, clips: newClips }
        })

        await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
        console.log('[handleCutClip] Group clips split successfully')

      } else {
        // Single clip cut (no group)
        const result = cutVideoClip(clip, currentTimeMs, null, null)
        if (!result) {
          console.log('[handleCutClip] SKIP - could not cut clip')
          return
        }

        const updatedLayers = timeline.layers.map(l => {
          if (l.id !== selectedVideoClip.layerId) return l
          return {
            ...l,
            clips: l.clips.map(c => c.id === clip.id ? result.clip1 : c).concat([result.clip2]),
          }
        })

        await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
        console.log('[handleCutClip] Video clip split successfully')
      }

    } else if (selectedClip) {
      const track = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
      const clip = track?.clips.find(c => c.id === selectedClip.clipId)
      if (!clip || !track) {
        console.log('[handleCutClip] SKIP - clip or track not found')
        return
      }

      const clipEnd = clip.start_ms + clip.duration_ms
      if (currentTimeMs <= clip.start_ms || currentTimeMs >= clipEnd) {
        console.log('[handleCutClip] SKIP - playhead not within clip bounds')
        return
      }

      // Check if clip is part of a group
      if (clip.group_id) {
        console.log('[handleCutClip] Cutting group:', clip.group_id)

        // Collect all clips in the group
        const groupVideoClips: { clip: Clip, layerId: string }[] = []
        const groupAudioClips: { clip: AudioClip, trackId: string }[] = []

        for (const l of timeline.layers) {
          for (const c of l.clips) {
            if (c.group_id === clip.group_id) {
              groupVideoClips.push({ clip: c, layerId: l.id })
            }
          }
        }

        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (c.group_id === clip.group_id) {
              groupAudioClips.push({ clip: c, trackId: t.id })
            }
          }
        }

        console.log('[handleCutClip] Group clips found - video:', groupVideoClips.length, 'audio:', groupAudioClips.length)

        // Generate new group IDs for the cut clips (if there are multiple clips in the group)
        const hasMultipleClips = groupVideoClips.length + groupAudioClips.length > 1
        const newGroupId1 = hasMultipleClips ? uuidv4() : null
        const newGroupId2 = hasMultipleClips ? uuidv4() : null

        // Track which clips to add to each layer/track
        const videoClipUpdates: Map<string, { original: Clip, clip1: Clip, clip2: Clip }[]> = new Map()
        const audioClipUpdates: Map<string, { original: AudioClip, clip1: AudioClip, clip2: AudioClip }[]> = new Map()

        // Cut each video clip in the group
        for (const { clip: groupClip, layerId } of groupVideoClips) {
          const result = cutVideoClip(groupClip, currentTimeMs, newGroupId1, newGroupId2)
          if (result) {
            if (!videoClipUpdates.has(layerId)) {
              videoClipUpdates.set(layerId, [])
            }
            videoClipUpdates.get(layerId)!.push({ original: groupClip, ...result })
          }
        }

        // Cut each audio clip in the group
        for (const { clip: groupClip, trackId } of groupAudioClips) {
          const result = cutAudioClip(groupClip, currentTimeMs, newGroupId1, newGroupId2)
          if (result) {
            if (!audioClipUpdates.has(trackId)) {
              audioClipUpdates.set(trackId, [])
            }
            audioClipUpdates.get(trackId)!.push({ original: groupClip, ...result })
          }
        }

        // Apply updates to layers
        const updatedLayers = timeline.layers.map(l => {
          const updates = videoClipUpdates.get(l.id)
          if (!updates || updates.length === 0) return l

          const newClips = l.clips
            .map(c => {
              const update = updates.find(u => u.original.id === c.id)
              return update ? update.clip1 : c
            })
            .concat(updates.map(u => u.clip2))

          return { ...l, clips: newClips }
        })

        // Apply updates to audio tracks
        const updatedTracks = timeline.audio_tracks.map(t => {
          const updates = audioClipUpdates.get(t.id)
          if (!updates || updates.length === 0) return t

          const newClips = t.clips
            .map(c => {
              const update = updates.find(u => u.original.id === c.id)
              return update ? update.clip1 : c
            })
            .concat(updates.map(u => u.clip2))

          return { ...t, clips: newClips }
        })

        await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
        console.log('[handleCutClip] Group clips split successfully')

      } else {
        // Single clip cut (no group)
        const result = cutAudioClip(clip, currentTimeMs, null, null)
        if (!result) {
          console.log('[handleCutClip] SKIP - could not cut clip')
          return
        }

        const updatedTracks = timeline.audio_tracks.map(t => {
          if (t.id !== selectedClip.trackId) return t
          return {
            ...t,
            clips: t.clips.map(c => c.id === clip.id ? result.clip1 : c).concat([result.clip2]),
          }
        })

        await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
        console.log('[handleCutClip] Audio clip split successfully')
      }
    } else {
      console.log('[handleCutClip] No clip selected')
    }
  }, [selectedVideoClip, selectedClip, timeline, projectId, currentTimeMs, updateTimeline])

  // Snap selected clip to end of previous clip
  const handleSnapToPrevious = useCallback(async () => {
    console.log('[handleSnapToPrevious] called')

    // Helper: Check if any clip exists at a given time position (excluding specified clip IDs)
    const hasClipAtTime = (timeMs: number, excludeClipIds: Set<string>): boolean => {
      for (const layer of timeline.layers) {
        for (const clip of layer.clips) {
          if (excludeClipIds.has(clip.id)) continue
          if (clip.start_ms <= timeMs && timeMs < clip.start_ms + clip.duration_ms) {
            return true
          }
        }
      }
      for (const track of timeline.audio_tracks) {
        for (const clip of track.clips) {
          if (excludeClipIds.has(clip.id)) continue
          if (clip.start_ms <= timeMs && timeMs < clip.start_ms + clip.duration_ms) {
            return true
          }
        }
      }
      return false
    }

    // Helper: Find the last ending clip across all tracks (excluding specified clip IDs)
    const findGlobalLastEndMs = (excludeClipIds: Set<string>): number => {
      let lastEndMs = 0
      for (const layer of timeline.layers) {
        for (const clip of layer.clips) {
          if (excludeClipIds.has(clip.id)) continue
          const endMs = clip.start_ms + clip.duration_ms
          if (endMs > lastEndMs) lastEndMs = endMs
        }
      }
      for (const track of timeline.audio_tracks) {
        for (const clip of track.clips) {
          if (excludeClipIds.has(clip.id)) continue
          const endMs = clip.start_ms + clip.duration_ms
          if (endMs > lastEndMs) lastEndMs = endMs
        }
      }
      return lastEndMs
    }

    // Helper: Find previous clip in the same track/layer
    const findPrevClipEndMs = (clips: Array<{ id: string; start_ms: number; duration_ms: number }>, currentClipId: string, currentStartMs: number): number => {
      const prevClips = clips
        .filter(c => c.id !== currentClipId)
        .filter(c => c.start_ms + c.duration_ms <= currentStartMs)
        .sort((a, b) => (b.start_ms + b.duration_ms) - (a.start_ms + a.duration_ms))
      return prevClips.length > 0 ? prevClips[0].start_ms + prevClips[0].duration_ms : 0
    }

    if (selectedVideoClip) {
      const layer = timeline.layers.find(l => l.id === selectedVideoClip.layerId)
      const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
      if (!clip || !layer) {
        console.log('[handleSnapToPrevious] SKIP - clip or layer not found')
        return
      }
      if (layer.locked) {
        console.log('[handleSnapToPrevious] SKIP - layer is locked')
        return
      }

      // Collect group clips first
      const groupVideoClipIds = new Set<string>([clip.id])
      const groupAudioClipIds = new Set<string>()

      if (clip.group_id) {
        for (const l of timeline.layers) {
          for (const c of l.clips) {
            if (c.group_id === clip.group_id) groupVideoClipIds.add(c.id)
          }
        }
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (c.group_id === clip.group_id) groupAudioClipIds.add(c.id)
          }
        }
      }

      const allGroupClipIds = new Set([...groupVideoClipIds, ...groupAudioClipIds])

      // Step 1: Check if there's any content at the clip's current position (on other tracks)
      const hasContentAtCurrentPos = hasClipAtTime(clip.start_ms, allGroupClipIds)

      let newStartMs: number
      if (!hasContentAtCurrentPos) {
        // No content on other tracks - snap to global last end (across all tracks)
        newStartMs = findGlobalLastEndMs(allGroupClipIds)
        console.log('[handleSnapToPrevious] Step 1: Global snap to', newStartMs)
      } else {
        // Content exists on other tracks - snap to same track's previous clip
        newStartMs = findPrevClipEndMs(layer.clips, clip.id, clip.start_ms)
        console.log('[handleSnapToPrevious] Step 2: Same-track snap to', newStartMs)
      }

      const deltaMs = newStartMs - clip.start_ms

      // Update all clips in the group
      const updatedLayers = timeline.layers.map(l => ({
        ...l,
        clips: l.clips.map(c =>
          groupVideoClipIds.has(c.id)
            ? { ...c, start_ms: Math.max(0, c.start_ms + deltaMs) }
            : c
        ),
      }))

      const updatedTracks = timeline.audio_tracks.map(t => ({
        ...t,
        clips: t.clips.map(c =>
          groupAudioClipIds.has(c.id)
            ? { ...c, start_ms: Math.max(0, c.start_ms + deltaMs) }
            : c
        ),
      }))

      await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
      console.log('[handleSnapToPrevious] Video clip snapped to', newStartMs, 'with', groupVideoClipIds.size, 'video and', groupAudioClipIds.size, 'audio clips')

    } else if (selectedClip) {
      const track = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
      const clip = track?.clips.find(c => c.id === selectedClip.clipId)
      if (!clip || !track) {
        console.log('[handleSnapToPrevious] SKIP - clip or track not found')
        return
      }

      // Collect group clips first
      const groupVideoClipIds = new Set<string>()
      const groupAudioClipIds = new Set<string>([clip.id])

      if (clip.group_id) {
        for (const l of timeline.layers) {
          for (const c of l.clips) {
            if (c.group_id === clip.group_id) groupVideoClipIds.add(c.id)
          }
        }
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (c.group_id === clip.group_id) groupAudioClipIds.add(c.id)
          }
        }
      }

      const allGroupClipIds = new Set([...groupVideoClipIds, ...groupAudioClipIds])

      // Step 1: Check if there's any content at the clip's current position (on other tracks)
      const hasContentAtCurrentPos = hasClipAtTime(clip.start_ms, allGroupClipIds)

      let newStartMs: number
      if (!hasContentAtCurrentPos) {
        // No content on other tracks - snap to global last end (across all tracks)
        newStartMs = findGlobalLastEndMs(allGroupClipIds)
        console.log('[handleSnapToPrevious] Step 1: Global snap to', newStartMs)
      } else {
        // Content exists on other tracks - snap to same track's previous clip
        newStartMs = findPrevClipEndMs(track.clips, clip.id, clip.start_ms)
        console.log('[handleSnapToPrevious] Step 2: Same-track snap to', newStartMs)
      }

      const deltaMs = newStartMs - clip.start_ms

      // Update all clips in the group
      const updatedLayers = timeline.layers.map(l => ({
        ...l,
        clips: l.clips.map(c =>
          groupVideoClipIds.has(c.id)
            ? { ...c, start_ms: Math.max(0, c.start_ms + deltaMs) }
            : c
        ),
      }))

      const updatedTracks = timeline.audio_tracks.map(t => ({
        ...t,
        clips: t.clips.map(c =>
          groupAudioClipIds.has(c.id)
            ? { ...c, start_ms: Math.max(0, c.start_ms + deltaMs) }
            : c
        ),
      }))

      await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
      console.log('[handleSnapToPrevious] Audio clip snapped to', newStartMs, 'with', groupVideoClipIds.size, 'video and', groupAudioClipIds.size, 'audio clips')
    } else {
      console.log('[handleSnapToPrevious] No clip selected')
    }
  }, [selectedVideoClip, selectedClip, timeline, projectId, updateTimeline])

  // Select all clips that extend beyond the current playhead position
  // This includes clips starting at/after the playhead AND clips currently playing
  const handleSelectForward = useCallback(() => {
    console.log('[handleSelectForward] called - currentTimeMs:', currentTimeMs)

    const newVideoClipIds = new Set<string>()
    const newAudioClipIds = new Set<string>()

    // Select video clips whose end point is beyond the playhead
    // (clip.start_ms + clip.duration_ms > currentTimeMs)
    for (const layer of timeline.layers) {
      for (const clip of layer.clips) {
        const clipEndMs = clip.start_ms + clip.duration_ms
        if (clipEndMs > currentTimeMs) {
          newVideoClipIds.add(clip.id)
        }
      }
    }

    // Select audio clips whose end point is beyond the playhead
    for (const track of timeline.audio_tracks) {
      for (const clip of track.clips) {
        const clipEndMs = clip.start_ms + clip.duration_ms
        if (clipEndMs > currentTimeMs) {
          newAudioClipIds.add(clip.id)
        }
      }
    }

    setSelectedVideoClips(newVideoClipIds)
    setSelectedAudioClips(newAudioClipIds)

    // Clear single selection
    setSelectedClip(null)
    setSelectedVideoClip(null)
    if (onClipSelect) onClipSelect(null)
    if (onVideoClipSelect) onVideoClipSelect(null)

    console.log('[handleSelectForward] Selected', newVideoClipIds.size, 'video clips and', newAudioClipIds.size, 'audio clips')
  }, [timeline, currentTimeMs, onClipSelect, onVideoClipSelect])

  // Playhead drag handlers
  const handlePlayheadDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingPlayhead(true)
  }, [])

  const handlePlayheadDragMove = useCallback((e: MouseEvent) => {
    if (!isDraggingPlayhead || !timelineContainerRef.current || !tracksScrollRef.current || !onSeek) return

    const rect = timelineContainerRef.current.getBoundingClientRect()
    const scrollContainer = tracksScrollRef.current
    const scrollLeft = scrollContainer.scrollLeft
    const containerWidth = scrollContainer.clientWidth

    // Calculate position relative to scroll container
    const offsetX = e.clientX - rect.left + scrollLeft
    const timeMs = Math.max(0, Math.min(timeline.duration_ms, Math.round((offsetX / pixelsPerSecond) * 1000)))
    onSeek(timeMs)

    // Auto-scroll when near edges
    const edgeThreshold = 50 // pixels from edge to trigger scroll
    const scrollSpeed = 10 // pixels per frame
    const mouseXInContainer = e.clientX - rect.left

    if (mouseXInContainer < edgeThreshold && scrollLeft > 0) {
      // Near left edge - scroll left
      scrollContainer.scrollLeft = Math.max(0, scrollLeft - scrollSpeed)
    } else if (mouseXInContainer > containerWidth - edgeThreshold) {
      // Near right edge - scroll right
      const maxScroll = scrollContainer.scrollWidth - containerWidth
      scrollContainer.scrollLeft = Math.min(maxScroll, scrollLeft + scrollSpeed)
    }
  }, [isDraggingPlayhead, pixelsPerSecond, timeline.duration_ms, onSeek])

  const handlePlayheadDragEnd = useCallback(() => {
    setIsDraggingPlayhead(false)
  }, [])

  // Add global mouse listeners for playhead drag
  useEffect(() => {
    if (isDraggingPlayhead) {
      window.addEventListener('mousemove', handlePlayheadDragMove)
      window.addEventListener('mouseup', handlePlayheadDragEnd)
      return () => {
        window.removeEventListener('mousemove', handlePlayheadDragMove)
        window.removeEventListener('mouseup', handlePlayheadDragEnd)
      }
    }
  }, [isDraggingPlayhead, handlePlayheadDragMove, handlePlayheadDragEnd])

  const getAssetName = useCallback((assetId: string) => {
    const asset = assets.find(a => a.id === assetId)
    return asset?.name || assetId.slice(0, 8)
  }, [assets])

  // Get display name for a clip (used for tooltip and clip label)
  const getClipDisplayName = useCallback((clip: Clip) => {
    if (clip.asset_id) {
      return getAssetName(clip.asset_id)
    }
    if (clip.text_content) {
      return clip.text_content.slice(0, 20) + (clip.text_content.length > 20 ? '...' : '')
    }
    if (clip.shape) {
      // Use shape name if available, otherwise use shape type
      return clip.shape.name || clip.shape.type
    }
    return 'Clip'
  }, [getAssetName])

  const getSelectedClipData = useCallback(() => {
    if (!selectedClip) return null
    const track = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
    if (!track) return null
    return track.clips.find(c => c.id === selectedClip.clipId) || null
  }, [selectedClip, timeline])

  const selectedClipData = getSelectedClipData()

  // Toggle cut flag on a transcription segment
  const handleToggleSegmentCut = useCallback(async (segmentId: string, currentCut: boolean) => {
    if (!transcription || !selectedClipData) return

    try {
      const updatedSegment = await transcriptionApi.updateSegment(
        selectedClipData.asset_id,
        segmentId,
        { cut: !currentCut }
      )

      // Update local state
      setTranscription(prev => {
        if (!prev) return prev
        return {
          ...prev,
          segments: prev.segments.map(seg =>
            seg.id === segmentId ? updatedSegment : seg
          )
        }
      })
    } catch (error) {
      console.error('Failed to update segment:', error)
      alert('セグメントの更新に失敗しました')
    }
  }, [transcription, selectedClipData])

  // Apply cuts - create clips based on non-cut segments
  const handleApplyCuts = useCallback(async () => {
    if (!transcription || !selectedClip || !selectedClipData || !projectId) return

    try {
      // Call the apply-cuts API
      const result = await transcriptionApi.applyCuts(selectedClipData.asset_id)

      // Refresh project to get updated timeline
      await updateTimeline(projectId, timeline)

      alert(`${result.clips_created}個のクリップを作成しました。カットされた時間: ${Math.round(result.cut_duration_ms / 1000)}秒`)

      // Close the transcription panel
      setShowTranscriptionPanel(false)
      setTranscription(null)
    } catch (error) {
      console.error('Failed to apply cuts:', error)
      alert('カットの適用に失敗しました')
    }
  }, [transcription, selectedClip, selectedClipData, timeline, projectId, updateTimeline])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      console.log('[handleKeyDown] key:', e.key, 'selectedClip:', selectedClip, 'selectedVideoClip:', selectedVideoClip)

      // Check if user is typing in an input field
      const activeEl = document.activeElement
      const isInputFocused = activeEl instanceof HTMLInputElement ||
                            activeEl instanceof HTMLTextAreaElement ||
                            activeEl?.getAttribute('contenteditable') === 'true'

      if (e.key === 'Escape') {
        if (contextMenu) {
          setContextMenu(null)
          return
        }
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if ((selectedClip || selectedVideoClip) && !isInputFocused) {
          console.log('[handleKeyDown] Deleting clip...')
          e.preventDefault()
          handleDeleteClip()
        } else {
          console.log('[handleKeyDown] No clip selected or input focused')
        }
      }
      // Snap toggle shortcut (S key)
      if (e.key === 's' && !e.metaKey && !e.ctrlKey && !isInputFocused) {
        console.log('[handleKeyDown] Toggling snap...')
        e.preventDefault()
        setIsSnapEnabled(prev => !prev)
      }
      // Cut clip shortcut (C key)
      if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !isInputFocused) {
        if (selectedClip || selectedVideoClip) {
          console.log('[handleKeyDown] Cutting clip...')
          e.preventDefault()
          handleCutClip()
        }
      }
      // Forward select shortcut (A key)
      if (e.key === 'a' && !e.metaKey && !e.ctrlKey && !isInputFocused) {
        console.log('[handleKeyDown] Selecting forward...')
        e.preventDefault()
        handleSelectForward()
      }
      // Scroll to end shortcut (Shift+E)
      if (e.key === 'E' && e.shiftKey && !e.metaKey && !e.ctrlKey && !isInputFocused) {
        console.log('[handleKeyDown] Scrolling to end...')
        e.preventDefault()
        scrollToTime(timeline.duration_ms, 'left')
      }
      // Scroll to playhead shortcut (Shift+H)
      if (e.key === 'H' && e.shiftKey && !e.metaKey && !e.ctrlKey && !isInputFocused) {
        console.log('[handleKeyDown] Scrolling to playhead...')
        e.preventDefault()
        scrollToTime(currentTimeMs, 'left')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedClip, selectedVideoClip, handleDeleteClip, handleCutClip, handleSelectForward, contextMenu, scrollToTime, currentTimeMs, timeline.duration_ms])

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      {/* Timeline Header */}
      <div className="h-10 flex items-center justify-between px-4 border-b border-gray-700">
        <div className="flex items-center gap-4">
          <button className="text-gray-400 hover:text-white">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </button>
          <span className="text-white text-sm font-mono">{formatTime(currentTimeMs)}</span>
        </div>

        {/* Add Track/Layer Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleAddLayer}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            レイヤー追加
          </button>
          <div className="relative group">
            <button className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              音声
            </button>
            <div className="absolute top-full left-0 mt-1 bg-gray-700 rounded shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-20 min-w-[120px]">
              <button onClick={() => handleAddAudioTrack('narration')} className="block w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600">ナレーション</button>
              <button onClick={() => handleAddAudioTrack('bgm')} className="block w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600">BGM</button>
              <button onClick={() => handleAddAudioTrack('se')} className="block w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600">SE</button>
            </div>
          </div>
          {/* Shape creation dropdown */}
          <div className="relative group">
            <button className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              図形
            </button>
            <div className="absolute top-full left-0 mt-1 bg-gray-700 rounded shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-20 min-w-[120px]">
              <button onClick={() => handleAddShape('rectangle')} className="w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600 flex items-center gap-2">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <rect x="3" y="3" width="18" height="18" rx="2" strokeWidth={2} />
                </svg>
                四角形
              </button>
              <button onClick={() => handleAddShape('circle')} className="w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600 flex items-center gap-2">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <circle cx="12" cy="12" r="9" strokeWidth={2} />
                </svg>
                円
              </button>
              <button onClick={() => handleAddShape('line')} className="w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600 flex items-center gap-2">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <line x1="4" y1="20" x2="20" y2="4" strokeWidth={2} />
                </svg>
                線
              </button>
            </div>
          </div>
          {/* Text button */}
          <button
            onClick={handleAddText}
            className="px-2 py-1 text-xs bg-green-600 hover:bg-green-500 text-white rounded flex items-center gap-1"
            title="テキストを追加"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
            テキスト
          </button>
          {/* Cut button */}
          <button
            onClick={handleCutClip}
            disabled={!selectedClip && !selectedVideoClip}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            title="カット (C)"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.121 14.121L19 19m-7-7l7-7m-7 7l-2.879 2.879M12 12L9.121 9.121m0 5.758a3 3 0 10-4.243 4.243 3 3 0 004.243-4.243zm0-5.758a3 3 0 10-4.243-4.243 3 3 0 004.243 4.243z" />
            </svg>
            カット
          </button>
          {/* Snap toggle button - Enhanced visibility */}
          <button
            onClick={() => setIsSnapEnabled(prev => !prev)}
            className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-1.5 transition-all duration-200 font-medium ${
              isSnapEnabled
                ? 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/30 ring-1 ring-emerald-400/50'
                : 'bg-gray-700 hover:bg-gray-600 text-gray-400 ring-1 ring-gray-600'
            }`}
            title={`スナップ ${isSnapEnabled ? 'オン' : 'オフ'} (S)`}
          >
            {/* Magnet icon - more intuitive for snap */}
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              {isSnapEnabled ? (
                <>
                  {/* Active magnet with attraction lines */}
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v6a6 6 0 0012 0V3M6 3h3m9 0h-3M6 9h3m9 0h-3" />
                  {/* Attraction indicator lines */}
                  <path strokeLinecap="round" strokeDasharray="2 2" d="M12 15v4M9 17l3 2 3-2" opacity="0.7" />
                </>
              ) : (
                <>
                  {/* Inactive magnet with slash */}
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v6a6 6 0 0012 0V3M6 3h3m9 0h-3M6 9h3m9 0h-3" opacity="0.5" />
                  {/* Diagonal slash to indicate off */}
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4l16 16" />
                </>
              )}
            </svg>
            <span className={isSnapEnabled ? '' : 'line-through opacity-70'}>
              スナップ
            </span>
            <span className={`text-[10px] px-1 rounded ${
              isSnapEnabled
                ? 'bg-emerald-500/50 text-emerald-100'
                : 'bg-gray-600 text-gray-500'
            }`}>
              {isSnapEnabled ? 'ON' : 'OFF'}
            </span>
          </button>
          {/* Snap to previous button */}
          <button
            onClick={handleSnapToPrevious}
            disabled={!selectedClip && !selectedVideoClip}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            title="前のクリップにスナップ"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
            前にスナップ
          </button>
          {/* Select forward button */}
          <button
            onClick={handleSelectForward}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1"
            title="再生ヘッド以降を選択 (A)"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            </svg>
            以降を選択
          </button>
          {/* Scroll to end button */}
          <button
            onClick={() => scrollToTime(timeline.duration_ms, 'left')}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1"
            title="終端を左端に寄せる (Shift+E)"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 12h14" />
            </svg>
            終端へ
          </button>
          {/* Scroll to playhead button */}
          <button
            onClick={() => scrollToTime(currentTimeMs, 'left')}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1"
            title="再生ヘッドを左端に寄せる (Shift+H)"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16M4 12h16" />
            </svg>
            ヘッドへ
          </button>
        </div>

        {/* Zoom Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              const target = zoom * 0.7
              // Snap to 100% if crossing it while zooming out
              if (zoom > 1 && target < 1) {
                setZoom(1)
              } else {
                setZoom(Math.max(0.1, target))
              }
            }}
            className="text-gray-400 hover:text-white"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
            </svg>
          </button>
          <span className="text-gray-400 text-sm w-12 text-center">{Math.round(zoom * 100)}%</span>
          <button
            onClick={() => {
              const target = zoom * 1.4
              // Snap to 100% if crossing it while zooming in
              if (zoom < 1 && target > 1) {
                setZoom(1)
              } else {
                setZoom(Math.min(20, target))
              }
            }}
            className="text-gray-400 hover:text-white"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
          <button
            onClick={handleFitToWindow}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded"
            title="タイムライン全体を表示"
          >
            Fit
          </button>
        </div>

        {/* Default Image Duration Setting */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-400">静止画:</span>
          <select
            value={defaultImageDurationMs}
            onChange={(e) => setDefaultImageDurationMs(Number(e.target.value))}
            className="bg-gray-700 text-white text-xs rounded px-1 py-0.5"
          >
            <option value={1000}>1秒</option>
            <option value={2000}>2秒</option>
            <option value={3000}>3秒</option>
            <option value={5000}>5秒</option>
            <option value={10000}>10秒</option>
            <option value={15000}>15秒</option>
            <option value={30000}>30秒</option>
          </select>
        </div>
      </div>

      {/* Timeline Content */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Track Labels */}
        <div
          ref={labelsScrollRef}
          onScroll={handleLabelsScroll}
          className="flex-shrink-0 border-r border-gray-700 relative overflow-y-auto scrollbar-hide"
          style={{ width: headerWidth, scrollbarGutter: 'stable' }}
        >
          {/* Resize handle for header width */}
          <div
            className="absolute top-0 right-0 w-1 h-full cursor-ew-resize hover:bg-primary-500/50 transition-colors z-10"
            onMouseDown={handleHeaderResizeStart}
            title="ドラッグして幅を変更"
          />
          {/* Header spacer to align with Time Ruler */}
          <div className="h-6 border-b border-gray-700 flex items-center px-2">
            <span className="text-xs text-gray-500">トラック</span>
          </div>

          {/* Video Layers with linked audio tracks (sorted by order descending) */}
          {sortedLayers.map((layer, layerIndex) => {
            const isLayerSelected = selectedLayerId === layer.id
            const canMoveUp = layerIndex > 0
            const canMoveDown = layerIndex < sortedLayers.length - 1
            const isDragging = draggingLayerId === layer.id
            const isDropTarget = dropTargetIndex === layerIndex
            return (
            <React.Fragment key={layer.id}>
            <div
              className={`border-b border-gray-700 flex items-center group cursor-pointer transition-colors relative ${
                dragOverLayer === layer.id
                  ? 'bg-purple-900/20'
                  : isLayerSelected
                    ? 'bg-primary-900/50 border-l-2 border-l-primary-500'
                    : 'hover:bg-gray-700/50'
              } ${isDragging ? 'opacity-50' : ''} ${isDropTarget ? 'border-t-2 border-t-primary-500' : ''}`}
              style={{ height: getLayerHeight(layer.id) }}
              onClick={() => {
                setSelectedLayerId(layer.id)
                setSelectedVideoClip(null)
                setSelectedClip(null)
                if (onVideoClipSelect) onVideoClipSelect(null)
                if (onClipSelect) onClipSelect(null)
              }}
              onDragOver={(e) => { handleLayerDragOver(e, layer.id); handleLayerReorderDragOver(e, layerIndex) }}
              onDragLeave={() => { handleLayerDragLeave(); handleLayerReorderDragLeave() }}
              onDrop={(e) => { handleLayerDrop(e, layer.id); handleLayerReorderDrop(e, layerIndex) }}
            >
              {/* Drag Handle */}
              <div
                draggable
                onDragStart={(e) => handleLayerReorderDragStart(e, layer.id)}
                onDragEnd={handleLayerReorderDragEnd}
                className="w-6 h-full flex items-center justify-center cursor-grab active:cursor-grabbing text-gray-500 hover:text-gray-300 hover:bg-gray-700/50"
                onClick={(e) => e.stopPropagation()}
                title="ドラッグして並び替え"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm0 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm0 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm8-12a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm0 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm0 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0z" />
                </svg>
              </div>
              {/* Layer Color Picker */}
              <div className="relative">
                <input
                  type="color"
                  value={getLayerColor(layer, layerIndex)}
                  onChange={(e) => handleUpdateLayerColor(layer.id, e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  className="w-4 h-4 rounded cursor-pointer border border-gray-600 bg-transparent"
                  title="レイヤー色を変更"
                  style={{ padding: 0 }}
                />
              </div>
              <div className="flex-1 flex items-center gap-1 px-2 py-1 min-w-0 overflow-hidden">
              {/* Layer Name - priority display, always visible with ellipsis */}
              {editingLayerId === layer.id ? (
                <input
                  type="text"
                  value={editingLayerName}
                  onChange={(e) => setEditingLayerName(e.target.value)}
                  onBlur={handleFinishRenameLayer}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleFinishRenameLayer()
                    if (e.key === 'Escape') handleCancelRenameLayer()
                  }}
                  className="text-sm text-white bg-gray-700 border border-gray-600 rounded px-1 flex-1 min-w-[40px] outline-none focus:border-primary-500"
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span
                  className={`text-sm truncate flex-1 min-w-[40px] ${isLayerSelected ? 'text-primary-300' : 'text-white hover:text-primary-400'}`}
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    handleStartRenameLayer(layer.id, layer.name)
                  }}
                  title={`${layer.name} - クリックで選択、ダブルクリックで名前変更`}
                >
                  {layer.name}
                </span>
              )}
              {/* Control icons - shrink and hide when space is limited */}
              <div className="flex items-center gap-1 flex-shrink" onClick={(e) => e.stopPropagation()}>
                {/* Visibility - show always when hidden, hover-only when visible */}
                <button
                  onClick={() => handleToggleLayerVisibility(layer.id)}
                  className={`text-xs hover:text-white transition-opacity ${layer.visible ? 'text-gray-400 opacity-0 group-hover:opacity-100' : 'text-red-400'}`}
                  title={layer.visible ? '非表示' : '表示'}
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    {layer.visible ? (
                      <>
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                      </>
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                    )}
                  </svg>
                </button>
                {/* Lock - show always when locked, hover-only when unlocked */}
                <button
                  onClick={() => handleToggleLayerLock(layer.id)}
                  className={`text-xs hover:text-white transition-opacity ${layer.locked ? 'text-yellow-500' : 'text-gray-400 opacity-0 group-hover:opacity-100'}`}
                  title={layer.locked ? 'ロック解除' : 'ロック'}
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    {layer.locked ? (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
                    )}
                  </svg>
                </button>
                {/* Up/Down/Delete - hover-only */}
                <button
                  onClick={() => handleMoveLayerUp(layer.id)}
                  disabled={!canMoveUp}
                  className={`text-xs transition-opacity opacity-0 group-hover:opacity-100 ${canMoveUp ? 'text-gray-400 hover:text-white' : 'text-gray-600 cursor-not-allowed'}`}
                  title="上へ移動"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                  </svg>
                </button>
                <button
                  onClick={() => handleMoveLayerDown(layer.id)}
                  disabled={!canMoveDown}
                  className={`text-xs transition-opacity opacity-0 group-hover:opacity-100 ${canMoveDown ? 'text-gray-400 hover:text-white' : 'text-gray-600 cursor-not-allowed'}`}
                  title="下へ移動"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                <button
                  onClick={() => handleDeleteLayer(layer.id)}
                  className="text-xs text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="削除"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
              </div>
              {/* Resize handle */}
              <div
                className="absolute bottom-0 left-0 right-0 h-1 cursor-ns-resize hover:bg-primary-500/50 transition-colors"
                onMouseDown={(e) => handleLayerResizeStart(e, layer.id)}
                title="ドラッグして高さを変更"
              />
            </div>
            </React.Fragment>
            )
          })}

          {/* Audio Tracks Header with Master Mute */}
          {audioTracks.length > 0 && (
            <div className="h-8 px-2 py-1 border-b border-gray-600 bg-gray-750 flex items-center justify-between">
              <span className="text-xs text-gray-400 font-medium">Audio</span>
              <button
                onClick={handleMasterMuteToggle}
                className={`text-sm px-2 py-0.5 rounded flex items-center gap-1 transition-colors ${
                  masterMuted
                    ? 'bg-red-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
                title={masterMuted ? '全トラックのミュート解除' : '全トラックをミュート'}
              >
                {masterMuted ? (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                  </svg>
                )}
              </button>
            </div>
          )}

          {/* Audio Tracks (BGM, SE, Narration) */}
          {audioTracks.map((track) => (
            <div
              key={track.id}
              className={`h-16 px-2 py-1 border-b border-gray-700 flex flex-col justify-center group transition-colors ${
                dragOverTrack === track.id ? 'bg-green-900/20' : ''
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1 flex-1">
                  <span className="text-sm text-white truncate">{track.name}</span>
                  {track.name.includes('抽出中') && (
                    <svg className="w-4 h-4 text-blue-400 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  {/* Apply ducking button - BGM tracks only */}
                  {track.type === 'bgm' && (
                    <button
                      onClick={() => handleApplyDucking(track.id)}
                      className="text-xs px-1.5 py-0.5 rounded bg-yellow-600 text-white hover:bg-yellow-500"
                      title="ナレーションに合わせてダッキング適用"
                    >
                      Duck
                    </button>
                  )}
                  <button
                    onClick={() => handleMuteToggle(track.id)}
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      track.muted
                        ? 'bg-red-600 text-white'
                        : 'bg-gray-700 text-gray-400'
                    }`}
                    title="ミュート"
                  >
                    M
                  </button>
                  <button
                    onClick={() => handleDeleteAudioTrack(track.id)}
                    className="text-xs px-1.5 py-0.5 rounded bg-gray-700 text-gray-400 hover:bg-red-600 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity"
                    title="削除"
                  >
                    ×
                  </button>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={track.volume}
                onChange={(e) => handleTrackVolumeChange(track.id, parseFloat(e.target.value))}
                className="w-full h-1 mt-1"
              />
            </div>
          ))}

          {/* New Layer Drop Zone - Label Side (only visible during drag) */}
          <div
            className={`transition-all overflow-hidden ${
              isDraggingNewLayer ? 'h-10 border-b border-dashed border-blue-500 bg-blue-900/30' : 'h-0'
            }`}
            onDragOver={(e) => { e.preventDefault(); setIsDraggingNewLayer(true) }}
            onDragLeave={() => setIsDraggingNewLayer(false)}
            onDrop={handleNewLayerDrop}
          >
            <div className="flex items-center gap-2 text-gray-400 text-xs h-full px-4">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
              </svg>
              <span>ドロップして新規レイヤー作成</span>
            </div>
          </div>
        </div>

        {/* Timeline Tracks */}
        <div
          ref={tracksScrollRef}
          onScroll={handleTracksScroll}
          className="flex-1 overflow-x-scroll overflow-y-scroll scrollbar-hide"
        >
          <div ref={timelineContainerRef} className="relative" style={{ minWidth: Math.max(canvasWidth, 800) }}>
            {/* Time Ruler - click to seek - sticky so it stays visible when scrolling */}
            <div
              className="h-6 border-b border-gray-700 relative cursor-pointer hover:bg-gray-700/30 sticky top-0 bg-gray-800 z-10"
              onClick={(e) => {
                if (!onSeek) return
                const rect = e.currentTarget.getBoundingClientRect()
                const offsetX = e.clientX - rect.left
                const timeMs = Math.max(0, Math.round((offsetX / pixelsPerSecond) * 1000))
                onSeek(timeMs)
              }}
            >
              {/* Adaptive grid based on pixels per second */}
              {(() => {
                // Determine interval based on pixelsPerSecond for readable grid
                let majorIntervalSec: number
                let minorIntervalSec: number
                let showMinor = true

                if (pixelsPerSecond < 3) {
                  // Extremely zoomed out: 2-minute major marks, 30-second minor
                  majorIntervalSec = 120
                  minorIntervalSec = 30
                } else if (pixelsPerSecond < 8) {
                  // Very zoomed out: 30-second major marks, 10-second minor
                  majorIntervalSec = 30
                  minorIntervalSec = 10
                } else if (pixelsPerSecond < 20) {
                  // Zoomed out: 10-second major marks, 5-second minor
                  majorIntervalSec = 10
                  minorIntervalSec = 5
                } else if (pixelsPerSecond < 50) {
                  // Normal: 5-second major marks, 1-second minor
                  majorIntervalSec = 5
                  minorIntervalSec = 1
                } else if (pixelsPerSecond < 120) {
                  // Zoomed in: 1-second major marks, 0.5-second minor
                  majorIntervalSec = 1
                  minorIntervalSec = 0.5
                } else {
                  // Very zoomed in: 0.5-second major marks, 0.1-second minor
                  majorIntervalSec = 0.5
                  minorIntervalSec = 0.1
                  showMinor = false
                }

                const durationSec = timeline.duration_ms / 1000
                const marks: { timeSec: number; isMajor: boolean }[] = []

                // Generate major marks
                for (let t = 0; t <= durationSec; t += majorIntervalSec) {
                  marks.push({ timeSec: t, isMajor: true })
                }

                // Generate minor marks (if enabled and different from major)
                if (showMinor && minorIntervalSec < majorIntervalSec) {
                  for (let t = 0; t <= durationSec; t += minorIntervalSec) {
                    // Skip if it's a major mark position
                    if (t % majorIntervalSec !== 0) {
                      marks.push({ timeSec: t, isMajor: false })
                    }
                  }
                }

                return marks.map((mark) => (
                  <div
                    key={mark.timeSec}
                    className="absolute top-0 h-full flex flex-col justify-end pointer-events-none"
                    style={{ left: mark.timeSec * pixelsPerSecond }}
                  >
                    <div className={`border-l ${mark.isMajor ? 'h-3 border-gray-500' : 'h-1.5 border-gray-600'}`}></div>
                    {mark.isMajor && (
                      <span className="text-xs text-gray-500 ml-1 whitespace-nowrap">
                        {formatTime(mark.timeSec * 1000)}
                      </span>
                    )}
                  </div>
                ))
              })()}
            </div>

            <VideoLayers
              layers={sortedLayers}
              projectId={projectId}
              assets={assets}
              pixelsPerSecond={pixelsPerSecond}
              getLayerColor={getLayerColor}
              selectedLayerId={selectedLayerId}
              selectedVideoClip={selectedVideoClip}
              selectedVideoClips={selectedVideoClips}
              selectedAudioGroupId={selectedAudioGroupId}
              dragState={dragState}
              videoDragState={videoDragState}
              dragGroupVideoClipIds={dragGroupVideoClipIds}
              dragGroupAudioClipIds={dragGroupAudioClipIds}
              videoDragGroupVideoClipIds={videoDragGroupVideoClipIds}
              videoDragGroupAudioClipIds={videoDragGroupAudioClipIds}
              videoClipOverlaps={videoClipOverlaps}
              getClipGroup={getClipGroup}
              handleVideoClipSelect={handleVideoClipSelect}
              handleVideoClipDoubleClick={handleVideoClipDoubleClick}
              handleVideoClipDragStart={handleVideoClipDragStart}
              handleContextMenu={handleContextMenu}
              getClipDisplayName={getClipDisplayName}
              getLayerHeight={getLayerHeight}
              handleLayerResizeStart={handleLayerResizeStart}
              dragOverLayer={dragOverLayer}
              dropPreview={dropPreview}
              handleLayerDragOver={handleLayerDragOver}
              handleLayerDragLeave={handleLayerDragLeave}
              handleLayerDrop={handleLayerDrop}
              onLayerClick={(layerId) => {
                setSelectedLayerId(layerId)
                setSelectedVideoClip(null)
                setSelectedClip(null)
                onVideoClipSelect?.(null)
                onClipSelect?.(null)
              }}
              registerLayerRef={(layerId, el) => { layerRefs.current[layerId] = el }}
              selectedKeyframeIndex={selectedKeyframeIndex}
              onKeyframeSelect={onKeyframeSelect}
              unmappedAssetIds={unmappedAssetIds}
            />

            <AudioTracks
              tracks={audioTracks}
              assets={assets}
              projectId={projectId}
              pixelsPerSecond={pixelsPerSecond}
              selectedClip={selectedClip}
              selectedAudioClips={selectedAudioClips}
              dragState={dragState}
              videoDragState={videoDragState}
              dragGroupAudioClipIds={dragGroupAudioClipIds}
              videoDragGroupAudioClipIds={videoDragGroupAudioClipIds}
              audioClipOverlaps={audioClipOverlaps}
              getClipGroup={getClipGroup}
              handleClipSelect={handleClipSelect}
              handleClipDragStart={handleClipDragStart}
              handleContextMenu={handleContextMenu}
              handleVolumeKeyframeAdd={handleVolumeKeyframeAdd}
              handleVolumeKeyframeUpdate={handleVolumeKeyframeUpdate}
              handleVolumeKeyframeRemove={handleVolumeKeyframeRemove}
              getAssetName={getAssetName}
              dragOverTrack={dragOverTrack}
              handleDragOver={handleDragOver}
              handleDragLeave={handleDragLeave}
              handleDrop={handleDrop}
              registerTrackRef={(trackId, el) => { trackRefs.current[trackId] = el }}
            />

            {/* New Layer Drop Zone - Clip Area Side (only visible during drag) */}
            <div
              className={`transition-all overflow-hidden relative ${
                isDraggingNewLayer ? 'h-10 border-b border-dashed border-blue-500 bg-blue-900/30' : 'h-0'
              }`}
              onDragOver={(e) => { e.preventDefault(); setIsDraggingNewLayer(true) }}
              onDragLeave={() => setIsDraggingNewLayer(false)}
              onDrop={handleNewLayerDrop}
            >
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="text-blue-400 text-sm">ドロップして新規レイヤー作成</span>
              </div>
            </div>

            {/* Snap Line - Enhanced visibility with animation and label */}
            {snapLineMs !== null && (
              <>
                {/* Main snap line with pulse animation */}
                <div
                  className="absolute top-0 bottom-0 w-0.5 bg-emerald-400 z-30 pointer-events-none animate-pulse"
                  style={{
                    left: (snapLineMs / 1000) * pixelsPerSecond,
                    boxShadow: '0 0 8px 2px rgba(52, 211, 153, 0.7), 0 0 16px 4px rgba(52, 211, 153, 0.3)',
                  }}
                />
                {/* Secondary glow line for extra visibility */}
                <div
                  className="absolute top-0 bottom-0 w-1 bg-emerald-400/30 z-29 pointer-events-none"
                  style={{
                    left: (snapLineMs / 1000) * pixelsPerSecond - 1,
                  }}
                />
                {/* Snap time label at top */}
                <div
                  className="absolute z-31 pointer-events-none"
                  style={{
                    left: (snapLineMs / 1000) * pixelsPerSecond,
                    top: 0,
                    transform: 'translateX(-50%)',
                  }}
                >
                  <div className="bg-emerald-600 text-white text-[10px] px-1.5 py-0.5 rounded-b font-mono shadow-lg">
                    {(snapLineMs / 1000).toFixed(2)}s
                  </div>
                </div>
              </>
            )}

            {/* Playhead */}
            <div
              className={`absolute top-0 bottom-0 z-20 transition-opacity ${
                isPlaying ? 'opacity-100' : 'opacity-70'
              }`}
              style={{
                left: (currentTimeMs / 1000) * pixelsPerSecond - 6,
                width: 13,
              }}
            >
              {/* Playhead line */}
              <div
                className="absolute left-1/2 -translate-x-1/2 top-0 bottom-0 w-0.5"
                style={{
                  backgroundColor: '#ef4444',
                  boxShadow: isPlaying ? '0 0 8px 2px rgba(239, 68, 68, 0.5)' : 'none',
                }}
              />
              {/* Playhead drag handle (top marker) */}
              <div
                className={`absolute -top-1 left-1/2 -translate-x-1/2 ${isDraggingPlayhead ? 'cursor-grabbing' : 'cursor-grab'}`}
                style={{
                  width: 0,
                  height: 0,
                  borderLeft: '6px solid transparent',
                  borderRight: '6px solid transparent',
                  borderTop: '8px solid #ef4444',
                }}
                onMouseDown={handlePlayheadDragStart}
              />
              {/* Invisible wider drag area */}
              <div
                className={`absolute top-0 bottom-0 left-0 right-0 ${isDraggingPlayhead ? 'cursor-grabbing' : 'cursor-ew-resize'}`}
                onMouseDown={handlePlayheadDragStart}
              />
              {/* Current time indicator */}
              {(isPlaying || isDraggingPlayhead) && (
                <div className="absolute -top-6 left-1/2 -translate-x-1/2 bg-red-500 text-white text-xs px-1.5 py-0.5 rounded whitespace-nowrap pointer-events-none">
                  {formatTime(currentTimeMs)}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Custom Vertical Scrollbar - Always visible */}
        {(() => {
          const { scrollTop, scrollHeight, clientHeight } = scrollPosition
          const hasScroll = scrollHeight > clientHeight
          const trackHeight = clientHeight - 24 // Account for ruler height
          const thumbHeight = hasScroll ? Math.max(30, (clientHeight / scrollHeight) * trackHeight) : trackHeight
          const thumbTop = hasScroll && scrollHeight > clientHeight
            ? 24 + (scrollTop / (scrollHeight - clientHeight)) * (trackHeight - thumbHeight)
            : 24
          return (
            <div className="w-3 bg-gray-800 border-l border-gray-700 flex-shrink-0 relative">
              {/* Spacer for ruler alignment */}
              <div className="h-6 border-b border-gray-700" />
              {/* Scrollbar track */}
              <div className="absolute left-0 right-0 bottom-0" style={{ top: 24 }}>
                {/* Thumb */}
                <div
                  className={`absolute left-0.5 right-0.5 rounded cursor-pointer transition-colors ${
                    verticalScrollDrag ? 'bg-gray-500' : 'bg-gray-600 hover:bg-gray-500'
                  }`}
                  style={{
                    top: thumbTop - 24,
                    height: thumbHeight,
                  }}
                  onMouseDown={handleVerticalScrollDragStart}
                />
              </div>
            </div>
          )
        })()}
      </div>

      <ViewportBar
        headerWidth={headerWidth}
        scrollPosition={scrollPosition}
        zoom={zoom}
        timelineDurationMs={timeline.duration_ms}
        viewportBarDrag={viewportBarDrag}
        onViewportBarDragStart={handleViewportBarDragStart}
        viewportBarRef={viewportBarRef}
      />

      {/* Audio Clip Properties - moved to Editor.tsx right sidebar */}

      {/* Video Clip Properties - moved to Editor.tsx right sidebar */}

      {/* Transcription / AI Analysis Panel */}
      {showTranscriptionPanel && (
        <div className="border-t border-gray-700 bg-gray-900">
          <div className="px-4 py-2 flex items-center justify-between border-b border-gray-700">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-white">AI音声分析</span>
              {isTranscribing && (
                <span className="text-xs text-purple-400 animate-pulse">分析中...</span>
              )}
              {transcription?.status === 'completed' && (
                <span className="text-xs text-gray-400">
                  {transcription.segments.filter(s => s.cut).length}件のカット候補 /
                  {transcription.segments.length}セグメント
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {transcription?.status === 'completed' && (
                <button
                  onClick={handleApplyCuts}
                  className="px-3 py-1 text-xs bg-green-600 hover:bg-green-500 text-white rounded"
                >
                  カットを適用
                </button>
              )}
              <button
                onClick={() => {
                  setShowTranscriptionPanel(false)
                  setTranscription(null)
                }}
                className="px-2 py-1 text-xs text-gray-400 hover:text-white"
              >
                閉じる
              </button>
            </div>
          </div>

          {transcription?.status === 'completed' && transcription.segments.length > 0 && (
            <div className="max-h-48 overflow-y-auto">
              {transcription.segments.map((segment) => (
                <div
                  key={segment.id}
                  className={`px-4 py-2 flex items-center gap-3 border-b border-gray-800 cursor-pointer hover:bg-gray-800/50 ${
                    segment.cut ? 'bg-red-900/20' : ''
                  }`}
                  onClick={() => onSeek?.(segment.start_ms)}
                >
                  {/* Cut toggle checkbox */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleToggleSegmentCut(segment.id, segment.cut)
                    }}
                    className={`w-5 h-5 flex items-center justify-center rounded border ${
                      segment.cut
                        ? 'bg-red-600 border-red-500 text-white'
                        : 'border-gray-500 text-transparent hover:border-gray-400'
                    }`}
                  >
                    {segment.cut && (
                      <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                      </svg>
                    )}
                  </button>

                  {/* Time range */}
                  <span className="text-xs text-gray-500 w-24 font-mono">
                    {formatTime(segment.start_ms)} - {formatTime(segment.end_ms)}
                  </span>

                  {/* Cut reason badge */}
                  {segment.cut && segment.cut_reason && (
                    <span className={`px-1.5 py-0.5 text-xs rounded ${
                      segment.cut_reason === 'silence' ? 'bg-gray-600 text-gray-200' :
                      segment.cut_reason === 'filler' ? 'bg-yellow-600 text-yellow-100' :
                      segment.cut_reason === 'mistake' ? 'bg-orange-600 text-orange-100' :
                      'bg-blue-600 text-blue-100'
                    }`}>
                      {segment.cut_reason === 'silence' ? '無音' :
                       segment.cut_reason === 'filler' ? 'フィラー' :
                       segment.cut_reason === 'mistake' ? '言い間違い' :
                       '手動'}
                    </span>
                  )}

                  {/* Text content */}
                  <span className={`flex-1 text-sm truncate ${
                    segment.cut ? 'text-gray-500 line-through' : 'text-white'
                  }`}>
                    {segment.text}
                  </span>
                </div>
              ))}
            </div>
          )}

          {transcription?.status === 'failed' && (
            <div className="px-4 py-3 text-sm text-red-400">
              エラー: {transcription.error_message || '分析に失敗しました'}
            </div>
          )}
        </div>
      )}

      <TimelineContextMenu
        contextMenu={contextMenu}
        timeline={timeline}
        selectedVideoClips={selectedVideoClips}
        selectedAudioClips={selectedAudioClips}
        onGroupClips={handleGroupClips}
        onUngroupClip={handleUngroupClip}
        onVideoClipSelect={handleVideoClipSelect}
        onAudioClipSelect={handleClipSelect}
        onClose={handleCloseContextMenu}
      />

      {/* Loading overlay for audio extraction */}
      {isExtractingAudio && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg px-6 py-4 flex items-center gap-3">
            <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-500 border-t-transparent" />
            <span className="text-white text-sm">音声を抽出中...</span>
          </div>
        </div>
      )}
    </div>
  )
}
