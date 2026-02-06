import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import html2canvas from 'html2canvas'
import { useProjectStore, type Shape, type VolumeKeyframe, type TimelineData, type Clip, type AudioClip as AudioClipType } from '@/store/projectStore'
import Timeline, { type SelectedClipInfo, type SelectedVideoClipInfo } from '@/components/editor/Timeline'
import LeftPanel from '@/components/assets/LeftPanel'
import { assetsApi, type Asset, type SessionData } from '@/api/assets'
import Toast from '@/components/common/Toast'
import { aiV1Api, type ChromaKeyPreviewFrame } from '@/api/aiV1'
import { extractAssetReferences, mapSessionToProject, applyMappingToTimeline, type AssetCandidate, type MappingResult } from '@/utils/sessionMapper'
import { migrateSession } from '@/utils/sessionMigrator'
import { projectsApi, type RenderJob } from '@/api/projects'
import { addKeyframe, removeKeyframe, hasKeyframeAt, getInterpolatedTransform } from '@/utils/keyframes'
import { getInterpolatedVolume } from '@/utils/volumeKeyframes'
// AudioClip type already imported from projectStore above
import AIChatPanel from '@/components/editor/AIChatPanel'
import ExportDialog from '@/components/editor/ExportDialog'
import ActivityPanel from '@/components/editor/ActivityPanel'
import ActivitySettingsSection from '@/components/editor/ActivitySettingsSection'
import { useProjectSync } from '@/hooks/useProjectSync'
import { v4 as uuidv4 } from 'uuid'

// Preview panel border defaults
const DEFAULT_PREVIEW_BORDER_WIDTH = 3 // pixels
const DEFAULT_PREVIEW_BORDER_COLOR = '#ffffff' // white

// Editor layout localStorage key and defaults
const EDITOR_LAYOUT_STORAGE_KEY = 'douga-editor-layout'

interface EditorLayoutSettings {
  previewHeight: number
  leftPanelWidth: number
  rightPanelWidth: number
  aiPanelWidth: number
  activityPanelWidth: number
  isAIChatOpen: boolean
  isPropertyPanelOpen: boolean
  isAssetPanelOpen: boolean
  playheadPosition: number
  isSyncEnabled: boolean
  previewZoom: number
}

const DEFAULT_LAYOUT: EditorLayoutSettings = {
  previewHeight: 400,
  leftPanelWidth: 288,
  rightPanelWidth: 288,
  aiPanelWidth: 320,
  activityPanelWidth: 320,
  isAIChatOpen: true,
  isPropertyPanelOpen: true,
  isAssetPanelOpen: true,
  playheadPosition: 0,
  isSyncEnabled: true,
  previewZoom: 1.0,
}

function loadLayoutSettings(): EditorLayoutSettings {
  try {
    const stored = localStorage.getItem(EDITOR_LAYOUT_STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored)
      // Merge with defaults to handle missing keys from older versions
      return { ...DEFAULT_LAYOUT, ...parsed }
    }
  } catch {
    // Ignore parse errors, use defaults
  }
  return DEFAULT_LAYOUT
}

function saveLayoutSettings(settings: EditorLayoutSettings): void {
  try {
    localStorage.setItem(EDITOR_LAYOUT_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Ignore storage errors (quota exceeded, etc.)
  }
}

// Calculate fade opacity multiplier based on time position within clip
// Returns a value between 0 and 1 that should be multiplied with the base opacity
function calculateFadeOpacity(
  timeInClipMs: number,
  durationMs: number,
  fadeInMs: number,
  fadeOutMs: number
): number {
  let fadeMultiplier = 1

  // Apply fade in (0 to 1) at the start of the clip
  if (fadeInMs > 0 && timeInClipMs < fadeInMs) {
    fadeMultiplier = Math.min(fadeMultiplier, timeInClipMs / fadeInMs)
  }

  // Apply fade out (1 to 0) at the end of the clip
  const timeFromEnd = durationMs - timeInClipMs
  if (fadeOutMs > 0 && timeFromEnd < fadeOutMs) {
    fadeMultiplier = Math.min(fadeMultiplier, timeFromEnd / fadeOutMs)
  }

  return Math.max(0, Math.min(1, fadeMultiplier))
}

interface PreviewState {
  asset: Asset | null
  url: string | null
  loading: boolean
}

// Chroma key canvas component for real-time green screen preview
interface ChromaKeyCanvasProps {
  clipId: string
  videoRefsMap: React.MutableRefObject<Map<string, HTMLVideoElement>>
  chromaKey: { enabled: boolean; color: string; similarity: number; blend: number }
  isPlaying: boolean
  crop?: { top: number; right: number; bottom: number; left: number }
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
  return result
    ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16),
      }
    : { r: 0, g: 255, b: 0 } // Default to green
}

function ChromaKeyCanvas({ clipId, videoRefsMap, chromaKey, isPlaying, crop }: ChromaKeyCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animationFrameRef = useRef<number | null>(null)
  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({ width: 0, height: 0 })
  const [corsError, setCorsError] = useState(false)

  useEffect(() => {
    const video = videoRefsMap.current.get(clipId)
    const canvas = canvasRef.current
    if (!video || !canvas) return

    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return

    const keyColor = hexToRgb(chromaKey.color)
    // similarity is 0-1, we convert to a threshold (0-255 range for RGB distance)
    const threshold = chromaKey.similarity * 255 * 1.73 // ~441 max for RGB distance

    const processFrame = () => {
      if (!video || video.videoWidth === 0 || video.videoHeight === 0) {
        animationFrameRef.current = requestAnimationFrame(processFrame)
        return
      }

      // Update canvas dimensions if video dimensions changed
      if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        setDimensions({ width: video.videoWidth, height: video.videoHeight })
      }

      try {
        // Draw video frame to canvas
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

        // Get pixel data
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const data = imageData.data

        // Process each pixel for chroma key
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i]
          const g = data[i + 1]
          const b = data[i + 2]

          // Calculate color distance from key color
          const distance = Math.sqrt(
            (r - keyColor.r) ** 2 +
            (g - keyColor.g) ** 2 +
            (b - keyColor.b) ** 2
          )

          if (distance < threshold) {
            // Within threshold - make transparent
            // Use blend for soft edges
            const blendRange = threshold * chromaKey.blend * 2
            if (distance > threshold - blendRange) {
              // Partial transparency for blend zone
              const alpha = ((distance - (threshold - blendRange)) / blendRange) * 255
              data[i + 3] = Math.min(255, Math.max(0, alpha))
            } else {
              // Fully transparent
              data[i + 3] = 0
            }
          }
        }

        ctx.putImageData(imageData, 0, 0)
      } catch (e) {
        // CORS error - canvas is tainted, show fallback
        if (e instanceof DOMException && e.name === 'SecurityError') {
          console.warn('[ChromaKey] CORS error - video source does not allow pixel access')
          setCorsError(true)
          return // Stop processing
        }
        throw e
      }

      // Continue animation loop
      animationFrameRef.current = requestAnimationFrame(processFrame)
    }

    // Start processing
    setCorsError(false)
    processFrame()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [clipId, videoRefsMap, chromaKey, isPlaying])

  // Show fallback message if CORS error
  if (corsError) {
    return (
      <div className="flex items-center justify-center bg-gray-800 text-gray-400 text-xs p-4">
        <span>クロマキープレビュー: CORSエラー（エクスポートでは適用されます）</span>
      </div>
    )
  }

  // Calculate clipPath for crop
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

export default function Editor() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const { currentProject, loading, error, fetchProject, updateProject, updateTimeline, updateTimelineLocal, undo, redo, canUndo, canRedo } = useProjectStore()
  const [assets, setAssets] = useState<Asset[]>([])
  const [renderJob, setRenderJob] = useState<RenderJob | null>(null)
  const [renderHistory, setRenderHistory] = useState<RenderJob[]>([])
  const [showRenderModal, setShowRenderModal] = useState(false)
  const [showSettingsModal, setShowSettingsModal] = useState(false)
  const [showShortcutsModal, setShowShortcutsModal] = useState(false)
  // Default duration for image clips (persisted to localStorage)
  const [defaultImageDurationMs, setDefaultImageDurationMs] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('timeline-default-image-duration-ms')
      return saved ? parseInt(saved, 10) : 5000
    } catch {
      return 5000
    }
  })
  const [showHistoryModal, setShowHistoryModal] = useState(false)
  const [showSaveSessionModal, setShowSaveSessionModal] = useState(false)
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const [sessionNameInput, setSessionNameInput] = useState('')
  const [lastSavedSessionName, setLastSavedSessionName] = useState('')
  const [savingSession, setSavingSession] = useState(false)
  const [assetLibraryRefreshTrigger, setAssetLibraryRefreshTrigger] = useState(0)
  // Current session tracking for overwrite save
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [currentSessionName, setCurrentSessionName] = useState<string | null>(null)
  // Toast notification
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null)
  const [isUndoRedoInProgress, setIsUndoRedoInProgress] = useState(false)
  const [showNewSessionConfirm, setShowNewSessionConfirm] = useState(false)
  const [saveCurrentSessionBeforeNew, setSaveCurrentSessionBeforeNew] = useState(false)
  const [newSessionName, setNewSessionName] = useState('')
  // Session open state
  const [pendingSessionData, setPendingSessionData] = useState<SessionData | null>(null)
  const [showOpenSessionConfirm, setShowOpenSessionConfirm] = useState(false)
  const [showAssetSelectDialog, setShowAssetSelectDialog] = useState(false)
  const [pendingSelections, setPendingSelections] = useState<AssetCandidate[]>([])
  const [userSelections, setUserSelections] = useState<Map<string, string>>(new Map())
  const [unmappedAssetIds, setUnmappedAssetIds] = useState<Set<string>>(new Set())
  const [apiKeyInput, setApiKeyInput] = useState('')
  const renderPollRef = useRef<number | null>(null)
  const lastUpdatedAtRef = useRef<string | null>(null)
  const staleCountRef = useRef<number>(0)
  const [selectedClip, setSelectedClip] = useState<SelectedClipInfo | null>(null)
  const [selectedVideoClip, setSelectedVideoClip] = useState<SelectedVideoClipInfo | null>(null)
  const [selectedKeyframeIndex, setSelectedKeyframeIndex] = useState<number | null>(null)
  // Clipboard state for copy/paste functionality
  const [copiedClip, setCopiedClip] = useState<{
    type: 'video' | 'audio'
    layerId?: string  // For video clips
    trackId?: string  // For audio clips
    clip: Clip | AudioClipType
  } | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  // Load saved layout settings from localStorage (run once on mount)
  const [savedLayout] = useState(() => loadLayoutSettings())
  const [currentTime, setCurrentTime] = useState(savedLayout.playheadPosition)
  const currentTimeRef = useRef(0) // Ref to always get latest currentTime
  const [preview, setPreview] = useState<PreviewState>({ asset: null, url: null, loading: false })
  const [assetUrlCache, setAssetUrlCache] = useState<Map<string, string>>(new Map())
  const [chromaPreviewFrames, setChromaPreviewFrames] = useState<ChromaKeyPreviewFrame[]>([])
  const [chromaPreviewLoading, setChromaPreviewLoading] = useState(false)
  const [chromaPreviewError, setChromaPreviewError] = useState<string | null>(null)
  const [chromaApplyLoading, setChromaApplyLoading] = useState(false)
  const [chromaPreviewSelectedIndex, setChromaPreviewSelectedIndex] = useState<number | null>(null)
  // Store the original chroma key color when editing starts (for Cancel functionality)
  const [chromaColorBeforeEdit, setChromaColorBeforeEdit] = useState<string | null>(null)
  // Eyedropper mode for picking color from preview frame
  const [chromaPickerMode, setChromaPickerMode] = useState(false)
  // Raw frame (before chroma key processing) for color picking
  const [chromaRawFrame, setChromaRawFrame] = useState<ChromaKeyPreviewFrame | null>(null)
  const [chromaRawFrameLoading, setChromaRawFrameLoading] = useState(false)
  // Chroma preview frame size (user adjustable)
  const [chromaPreviewSize, setChromaPreviewSize] = useState(80) // Default size in pixels per frame
  const [isResizingChromaPreview, setIsResizingChromaPreview] = useState(false)
  const chromaPreviewResizeStartY = useRef(0)
  const chromaPreviewResizeStartSize = useRef(0)
  // Track which image assets have been fully loaded (not just URL cached)
  const [preloadedImages, setPreloadedImages] = useState<Set<string>>(new Set())
  const [previewHeight, setPreviewHeight] = useState(savedLayout.previewHeight) // Resizable preview height
  const [isResizing, setIsResizing] = useState(false)
  // Preview zoom state (1.0 = 100%, range: 0.25 to 4.0)
  const [previewZoom, setPreviewZoom] = useState(savedLayout.previewZoom)
  // Preview pan offset (for panning when zoomed in)
  const [previewPan, setPreviewPan] = useState({ x: 0, y: 0 })
  const [isPanningPreview, setIsPanningPreview] = useState(false)
  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 })
  // Preview border settings (user adjustable)
  const [previewBorderWidth, setPreviewBorderWidth] = useState(DEFAULT_PREVIEW_BORDER_WIDTH)
  const [previewBorderColor, setPreviewBorderColor] = useState(DEFAULT_PREVIEW_BORDER_COLOR)
  // Panel resize state
  const [leftPanelWidth, setLeftPanelWidth] = useState(savedLayout.leftPanelWidth) // Default w-72 = 288px
  const [rightPanelWidth, setRightPanelWidth] = useState(savedLayout.rightPanelWidth)
  const [isResizingLeftPanel, setIsResizingLeftPanel] = useState(false)
  const [isResizingRightPanel, setIsResizingRightPanel] = useState(false)
  const leftPanelResizeStartX = useRef(0)
  const leftPanelResizeStartWidth = useRef(0)
  const rightPanelResizeStartX = useRef(0)
  const rightPanelResizeStartWidth = useRef(0)
  const [backendVersion, setBackendVersion] = useState<string>('...')
  // Local state for text editing with IME support
  const [localTextContent, setLocalTextContent] = useState('')
  const [isComposing, setIsComposing] = useState(false)
  // Local state for audio property editing (to avoid re-render on every input change)
  const [localAudioProps, setLocalAudioProps] = useState<{
    volume: string
    fadeInMs: string
    fadeOutMs: string
    startMs: string
  }>({ volume: '100', fadeInMs: '0', fadeOutMs: '0', startMs: '0' })
  // Local state for new volume keyframe input
  const [newKeyframeInput, setNewKeyframeInput] = useState({ timeMs: '', volume: '100' })
  const [isAIChatOpen, setIsAIChatOpen] = useState(savedLayout.isAIChatOpen)
  const [isPropertyPanelOpen, setIsPropertyPanelOpen] = useState(savedLayout.isPropertyPanelOpen)
  const [isAssetPanelOpen, setIsAssetPanelOpen] = useState(savedLayout.isAssetPanelOpen)
  const [isSyncEnabled, setIsSyncEnabled] = useState(savedLayout.isSyncEnabled)
  // AI and Activity panel widths
  const [aiPanelWidth, setAiPanelWidth] = useState(savedLayout.aiPanelWidth)
  const [activityPanelWidth, setActivityPanelWidth] = useState(savedLayout.activityPanelWidth)
  const [isResizingAiPanel, setIsResizingAiPanel] = useState(false)
  const [isResizingActivityPanel, setIsResizingActivityPanel] = useState(false)
  const aiPanelResizeStartX = useRef(0)
  const aiPanelResizeStartWidth = useRef(0)
  const activityPanelResizeStartX = useRef(0)
  const activityPanelResizeStartWidth = useRef(0)

  // Detect Mac for keyboard shortcut display
  const isMac = useMemo(() => {
    return typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform)
  }, [])
  const undoTooltip = isMac ? '元に戻す (⌘Z)' : '元に戻す (Ctrl+Z)'
  const redoTooltip = isMac ? 'やり直す (⌘⇧Z)' : 'やり直す (Ctrl+Shift+Z)'

  useEffect(() => {
    setChromaPreviewFrames([])
    setChromaPreviewError(null)
    setChromaRawFrame(null)
    setChromaPickerMode(false)
  }, [selectedVideoClip?.clipId])

  // ESC key to close chroma key preview modal and exit picker mode
  useEffect(() => {
    const handleEscKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (chromaPreviewSelectedIndex !== null) {
          setChromaPreviewSelectedIndex(null)
          setChromaPickerMode(false)
        }
        if (chromaPickerMode && chromaRawFrame) {
          setChromaPickerMode(false)
          setChromaRawFrame(null)
        }
      }
    }
    window.addEventListener('keydown', handleEscKey)
    return () => window.removeEventListener('keydown', handleEscKey)
  }, [chromaPreviewSelectedIndex, chromaPickerMode, chromaRawFrame])

  // Save layout settings to localStorage when they change (skip during playback to avoid constant writes)
  useEffect(() => {
    if (isPlaying) return // Don't save during playback
    saveLayoutSettings({
      previewHeight,
      leftPanelWidth,
      rightPanelWidth,
      aiPanelWidth,
      activityPanelWidth,
      isAIChatOpen,
      isPropertyPanelOpen,
      isAssetPanelOpen,
      playheadPosition: currentTime,
      isSyncEnabled,
      previewZoom,
    })
  }, [previewHeight, leftPanelWidth, rightPanelWidth, aiPanelWidth, activityPanelWidth, isAIChatOpen, isPropertyPanelOpen, isAssetPanelOpen, currentTime, isPlaying, isSyncEnabled, previewZoom])

  const textDebounceRef = useRef<NodeJS.Timeout | null>(null)
  // Preview drag state with anchor-based resizing
  // 'resize' = uniform scale (for images/videos), corner/edge types for shape width/height
  // 'crop-*' types for cropping from edges
  const [previewDrag, setPreviewDrag] = useState<{
    type: 'move' | 'resize' | 'resize-tl' | 'resize-tr' | 'resize-bl' | 'resize-br' | 'resize-t' | 'resize-b' | 'resize-l' | 'resize-r' | 'crop-t' | 'crop-r' | 'crop-b' | 'crop-l'
    layerId: string
    clipId: string
    startX: number  // Mouse position at drag start (screen coords)
    startY: number
    initialX: number  // Shape center at drag start (logical coords)
    initialY: number
    initialScale: number
    initialRotation?: number  // Element rotation at drag start (for rotation-aware resize)
    initialShapeWidth?: number
    initialShapeHeight?: number
    initialVideoWidth?: number  // Video natural dimensions (for uniform scale)
    initialVideoHeight?: number
    initialImageWidth?: number  // Image dimensions (for independent w/h resize)
    initialImageHeight?: number
    isImageClip?: boolean  // Flag to indicate image clip (independent w/h resize)
    // Anchor-based resizing: fixed point that doesn't move
    anchorX?: number  // Anchor position (logical coords)
    anchorY?: number
    handleOffsetX?: number  // Offset from mouse to handle (screen coords)
    handleOffsetY?: number
    // Crop initial values
    initialCrop?: { top: number; right: number; bottom: number; left: number }
    mediaWidth?: number  // Original media dimensions for crop calculation
    mediaHeight?: number
  } | null>(null)
  // Current transform during drag (local state, not saved until drag ends)
  const [dragTransform, setDragTransform] = useState<{
    x: number
    y: number
    scale: number
    shapeWidth?: number
    shapeHeight?: number
    imageWidth?: number
    imageHeight?: number
  } | null>(null)
  // Current crop during drag (local state)
  const [dragCrop, setDragCrop] = useState<{
    top: number
    right: number
    bottom: number
    left: number
  } | null>(null)
  const previewContainerRef = useRef<HTMLDivElement>(null)
  const previewAreaRef = useRef<HTMLDivElement>(null)
  const [previewAreaHeight, setPreviewAreaHeight] = useState(-1) // -1 = not measured yet
  // Effective preview container height: measured value or fallback estimate
  const effectivePreviewHeight = previewAreaHeight > 0 ? previewAreaHeight : Math.max(previewHeight - 104, 50)
  const audioRefs = useRef<Map<string, HTMLAudioElement>>(new Map())
  // Store clip timing info for each audio element to know when to stop playback and apply fades
  const audioClipTimingRefs = useRef<Map<string, {
    start_ms: number,
    end_ms: number,
    in_point_ms: number,
    fade_in_ms: number,
    fade_out_ms: number,
    base_volume: number,
    volume_keyframes?: VolumeKeyframe[],
    clip_volume?: number
  }>>(new Map())
  const videoRefsMap = useRef<Map<string, HTMLVideoElement>>(new Map())
  // Track which videos have loaded their first frame (for preload layer)
  const [preloadedVideos, setPreloadedVideos] = useState<Set<string>>(new Set())
  const playbackTimerRef = useRef<number | null>(null)
  const startTimeRef = useRef<number>(0)
  const isPlayingRef = useRef(false)
  const resizeStartY = useRef(0)
  const resizeStartHeight = useRef(0)

  // Fetch backend version on mount
  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_URL
      ? `${import.meta.env.VITE_API_URL}/api/version`
      : '/api/version'
    fetch(apiUrl)
      .then(res => res.json())
      .then(data => setBackendVersion(data.git_hash || 'unknown'))
      .catch(() => setBackendVersion('err'))
  }, [])

  // Persist default image duration to localStorage
  useEffect(() => {
    localStorage.setItem('timeline-default-image-duration-ms', String(defaultImageDurationMs))
  }, [defaultImageDurationMs])

  // Sync local text content when selected video clip changes
  useEffect(() => {
    if (selectedVideoClip?.textContent !== undefined) {
      setLocalTextContent(selectedVideoClip.textContent || '')
    }
  }, [selectedVideoClip?.clipId, selectedVideoClip?.textContent])

  // Reset keyframe selection when clip selection changes
  useEffect(() => {
    setSelectedKeyframeIndex(null)
  }, [selectedVideoClip?.clipId])

  // Keep currentTimeRef in sync with currentTime state
  useEffect(() => {
    currentTimeRef.current = currentTime
  }, [currentTime])

  // Sync local audio properties when selected audio clip changes
  // selectedClip is for audio tracks (narration, bgm, se), selectedVideoClip is for video/image layers
  useEffect(() => {
    if (selectedClip) {
      setLocalAudioProps({
        volume: String(Math.round(selectedClip.volume * 100)),
        fadeInMs: String(selectedClip.fadeInMs),
        fadeOutMs: String(selectedClip.fadeOutMs),
        startMs: String(selectedClip.startMs),
      })
    }
  }, [selectedClip?.clipId, selectedClip?.volume, selectedClip?.fadeInMs, selectedClip?.fadeOutMs, selectedClip?.startMs])

  // Clean up orphaned audio/video refs when timeline changes
  // Also stop playback to prevent ghost audio with stale timing
  useEffect(() => {
    if (!currentProject) return

    // Stop playback if audio tracks change while playing
    // This prevents ghost audio with stale timing info
    if (isPlayingRef.current) {
      isPlayingRef.current = false
      setIsPlaying(false)
      if (playbackTimerRef.current) {
        cancelAnimationFrame(playbackTimerRef.current)
        playbackTimerRef.current = null
      }
      audioRefs.current.forEach(audio => {
        audio.pause()
        audio.currentTime = 0
      })
      videoRefsMap.current.forEach(video => video.pause())
    }

    // Clear all audio timing refs - they'll be re-populated on next playback
    audioClipTimingRefs.current.clear()

    // Get current clip IDs
    const currentAudioClipIds = new Set<string>()
    for (const track of currentProject.timeline_data.audio_tracks) {
      for (const clip of track.clips) {
        currentAudioClipIds.add(clip.id)
      }
    }
    const currentVideoClipIds = new Set<string>()
    for (const layer of currentProject.timeline_data.layers) {
      for (const clip of layer.clips) {
        if (clip.asset_id) currentVideoClipIds.add(clip.id)
      }
    }

    // Clean up orphaned audio refs
    audioRefs.current.forEach((audio, clipId) => {
      if (!currentAudioClipIds.has(clipId)) {
        audio.pause()
        audio.src = ''
        audioRefs.current.delete(clipId)
      }
    })

    // Clean up orphaned video refs
    videoRefsMap.current.forEach((video, clipId) => {
      if (!currentVideoClipIds.has(clipId)) {
        video.pause()
        video.src = ''
        videoRefsMap.current.delete(clipId)
      }
    })
  // Use JSON.stringify to detect deep changes in timeline data
  // This ensures the effect fires when clips are modified (cropped, etc.)
  }, [JSON.stringify(currentProject?.timeline_data)])

  const fetchAssets = useCallback(async () => {
    if (!projectId) return
    try {
      const data = await assetsApi.list(projectId)
      setAssets(data)
    } catch (error) {
      console.error('Failed to fetch assets:', error)
    }
  }, [projectId])

  const handlePreviewAsset = useCallback(async (asset: Asset) => {
    if (!projectId) return

    // If same asset, just toggle off
    if (preview.asset?.id === asset.id) {
      setPreview({ asset: null, url: null, loading: false })
      return
    }

    setPreview({ asset, url: null, loading: true })

    try {
      const { url } = await assetsApi.getSignedUrl(projectId, asset.id)
      setPreview({ asset, url, loading: false })
    } catch (error) {
      console.error('Failed to get preview URL:', error)
      setPreview({ asset: null, url: null, loading: false })
    }
  }, [projectId, preview.asset?.id])

  useEffect(() => {
    if (projectId) {
      fetchProject(projectId)
      fetchAssets()
    }
  }, [projectId, fetchProject, fetchAssets])

  // Subscribe to real-time project updates via Firestore
  // This enables automatic UI refresh when MCP tools modify the project
  useProjectSync(projectId, {
    enabled: !!projectId && isSyncEnabled,
    onSync: (event) => {
      console.log('[Editor] Firestore sync event:', event.source, event.operation)
    },
  })

  // Preload all asset URLs (video, image, audio) for instant preview
  useEffect(() => {
    if (!projectId || assets.length === 0) return

    const preloadUrls = async () => {
      // Preload video, image, AND audio assets
      const allAssets = assets.filter(a => a.type === 'video' || a.type === 'image' || a.type === 'audio')

      // Process each asset and update cache incrementally (not all at once)
      // This prevents temporary loss of cached URLs when one asset is slow
      await Promise.all(
        allAssets.map(async (asset) => {
          // Skip URL fetch if already cached
          if (assetUrlCache.has(asset.id)) {
            // Still need to preload image if not yet preloaded
            if (asset.type === 'image' && !preloadedImages.has(asset.id)) {
              const url = assetUrlCache.get(asset.id)!
              const img = new Image()
              try {
                img.src = url
                await img.decode() // Use decode() for more reliable loading
                setPreloadedImages(prev => new Set(prev).add(asset.id))
              } catch {
                console.error('Failed to decode image:', asset.id)
              }
            }
            return
          }
          try {
            const { url } = await assetsApi.getSignedUrl(projectId, asset.id)
            // Update cache immediately for this asset (incremental update)
            setAssetUrlCache(prev => new Map(prev).set(asset.id, url))

            // For audio assets, also preload the actual audio data
            if (asset.type === 'audio') {
              const audio = new Audio()
              audio.preload = 'auto'
              audio.src = url
              // Store in audioRefs for later use during playback
              // This ensures the audio data is cached by the browser
            }

            // For image assets, preload the actual image data using decode()
            // This prevents brief black flashes when clips switch during playback
            if (asset.type === 'image') {
              const img = new Image()
              try {
                img.src = url
                await img.decode() // Use decode() for more reliable loading
                setPreloadedImages(prev => new Set(prev).add(asset.id))
              } catch {
                console.error('Failed to decode image:', asset.id)
              }
            }
          } catch (error) {
            console.error('Failed to preload asset URL:', asset.id, error)
          }
        })
      )
    }

    preloadUrls()
  }, [projectId, assets])

  // Find the video clip at current playhead position (for preview)
  const getVideoClipAtPlayhead = useCallback(() => {
    if (!currentProject) return null

    // Check each layer from TOP to BOTTOM (reverse order)
    // Return the first (topmost) clip that contains the current time
    const layers = currentProject.timeline_data.layers
    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i]
      if (layer.visible === false) continue

      for (const clip of layer.clips) {
        if (currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
          return { layer, clip }
        }
      }
    }
    return null
  }, [currentProject, currentTime])

  // Track the clip at playhead for preview
  const clipAtPlayhead = getVideoClipAtPlayhead()

  // Load preview based on playhead position (always follows playhead, not selection)
  // Uses cached URLs for instant switching
  useEffect(() => {
    if (!projectId) return

    // Always use clip at playhead position for preview
    // Selection is only for the properties panel
    if (!clipAtPlayhead || !clipAtPlayhead.clip.asset_id) {
      // No clip at playhead - clear preview if we had one
      if (preview.asset) {
        setPreview({ asset: null, url: null, loading: false })
      }
      return
    }

    const assetId = clipAtPlayhead.clip.asset_id

    // Find the asset
    const asset = assets.find(a => a.id === assetId)

    if (!asset) {
      return
    }

    // Only preview video and image assets
    if (asset.type !== 'video' && asset.type !== 'image') {
      return
    }

    // Don't reload if same asset is already in preview
    if (preview.asset?.id === asset.id) {
      return
    }

    // Check cache first for instant switching
    const cachedUrl = assetUrlCache.get(assetId)
    if (cachedUrl) {
      setPreview({ asset, url: cachedUrl, loading: false })
      return
    }

    // Fallback to fetching if not in cache
    const fetchUrl = async () => {
      setPreview({ asset, url: null, loading: true })
      try {
        const { url } = await assetsApi.getSignedUrl(projectId, assetId)
        setPreview({ asset, url, loading: false })
        // Add to cache
        setAssetUrlCache(prev => new Map(prev).set(assetId, url))
      } catch (error) {
        console.error('Failed to load video clip preview:', error)
        setPreview({ asset: null, url: null, loading: false })
      }
    }
    fetchUrl()
  }, [clipAtPlayhead, projectId, assets, assetUrlCache, preview.asset?.id, preview.asset])

  // Update project dimensions
  const handleUpdateProjectDimensions = async (width: number, height: number) => {
    if (!currentProject) return
    try {
      // Ensure even numbers
      const evenWidth = Math.round(width / 2) * 2
      const evenHeight = Math.round(height / 2) * 2
      await projectsApi.update(currentProject.id, { width: evenWidth, height: evenHeight })
      // Refresh project data
      await fetchProject(currentProject.id)
    } catch (error) {
      console.error('Failed to update project dimensions:', error)
      alert('プロジェクト設定の更新に失敗しました')
    }
  }

  // Update AI provider
  const handleUpdateAIProvider = async (provider: 'openai' | 'gemini' | 'anthropic' | null) => {
    if (!currentProject) return
    try {
      await updateProject(currentProject.id, { ai_provider: provider })
    } catch (error) {
      console.error('Failed to update AI provider:', error)
      alert('プロジェクト設定の更新に失敗しました')
    }
  }

  const handleUpdateAIApiKey = async (apiKey: string) => {
    if (!currentProject) return
    try {
      await updateProject(currentProject.id, { ai_api_key: apiKey || null })
      alert('APIキーを保存しました')
    } catch (error) {
      console.error('Failed to update AI API key:', error)
      alert('APIキーの更新に失敗しました')
    }
  }

  // Capture and upload project thumbnail from preview at 0ms
  const captureThumbnail = useCallback(async () => {
    if (!currentProject || !previewContainerRef.current) return

    try {
      // Use html2canvas to capture the preview container
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const canvas = await html2canvas(previewContainerRef.current, {
        backgroundColor: '#000000',
        scale: 0.5, // Reduce size for thumbnail (saves bandwidth and storage)
        logging: false,
        useCORS: true,
        allowTaint: true,
      } as any)

      // Convert to base64 PNG
      const imageData = canvas.toDataURL('image/png', 0.8)

      // Upload to backend
      await projectsApi.uploadThumbnail(currentProject.id, imageData)
      console.log('[Thumbnail] Captured and uploaded thumbnail for project:', currentProject.id)
    } catch (error) {
      console.error('[Thumbnail] Failed to capture thumbnail:', error)
      // Don't show error to user - thumbnail is a background operation
    }
  }, [currentProject])

  // Capture thumbnail when timeline data changes (debounced)
  // This captures the preview at the current time (not necessarily 0ms) as a representative thumbnail
  const thumbnailTimeoutRef = useRef<number | null>(null)
  const lastThumbnailCaptureRef = useRef<number>(0)
  const THUMBNAIL_DEBOUNCE_MS = 5000 // Wait 5 seconds after last change
  const THUMBNAIL_MIN_INTERVAL_MS = 60000 // At least 60 seconds between captures

  useEffect(() => {
    if (!currentProject?.timeline_data) return

    // Clear any pending timeout
    if (thumbnailTimeoutRef.current) {
      window.clearTimeout(thumbnailTimeoutRef.current)
    }

    // Schedule thumbnail capture after debounce period
    thumbnailTimeoutRef.current = window.setTimeout(() => {
      const now = Date.now()
      if (now - lastThumbnailCaptureRef.current >= THUMBNAIL_MIN_INTERVAL_MS) {
        // Capture thumbnail at current preview state (don't change playhead position)
        captureThumbnail().then(() => {
          lastThumbnailCaptureRef.current = Date.now()
        })
      }
    }, THUMBNAIL_DEBOUNCE_MS)

    return () => {
      if (thumbnailTimeoutRef.current) {
        window.clearTimeout(thumbnailTimeoutRef.current)
      }
    }
  }, [currentProject?.timeline_data, captureThumbnail])

  // Video render handlers
  const pollRenderStatus = useCallback(async () => {
    if (!currentProject) return

    try {
      const status = await projectsApi.getRenderStatus(currentProject.id)
      if (status) {
        console.log(`[POLL] status=${status.status} progress=${status.progress}% stage=${status.current_stage} updated_at=${status.updated_at}`)
        // Check for stale job (no progress for 300 consecutive polls = 10 minutes)
        // FFmpeg operations can take a very long time without progress updates
        if (status.status === 'processing' && status.updated_at) {
          if (lastUpdatedAtRef.current === status.updated_at) {
            staleCountRef.current++
            console.log(`[RENDER] Stale check: ${staleCountRef.current}/300 (updated_at: ${status.updated_at})`)
            if (staleCountRef.current >= 300) {
              // Job is stale - likely the worker died
              console.error('[RENDER] Job appears stale, cancelling and marking as failed')
              // Cancel the stale job in backend so new renders can start
              try {
                await projectsApi.cancelRender(currentProject.id)
              } catch (e) {
                console.error('[RENDER] Failed to cancel stale job:', e)
              }
              setRenderJob({ ...status, status: 'failed', error_message: 'レンダリングが停止しました（サーバーエラー）' })
              lastUpdatedAtRef.current = null
              staleCountRef.current = 0
              return
            }
          } else {
            // Progress is being made, reset stale counter
            lastUpdatedAtRef.current = status.updated_at
            staleCountRef.current = 0
          }
        }

        setRenderJob(status)

        // Continue polling if still processing
        if (status.status === 'queued' || status.status === 'processing') {
          renderPollRef.current = window.setTimeout(pollRenderStatus, 2000)
        } else {
          // Job finished, reset stale tracking and reload history
          lastUpdatedAtRef.current = null
          staleCountRef.current = 0
          // Reload render history when job completes
          if (currentProject) {
            projectsApi.getRenderHistory(currentProject.id)
              .then(setRenderHistory)
              .catch(console.error)
          }
        }
      }
    } catch (error) {
      console.error('Failed to poll render status:', error)
    }
  }, [currentProject])

  // Load render history from API
  const loadRenderHistory = useCallback(async () => {
    if (!currentProject) return
    try {
      const history = await projectsApi.getRenderHistory(currentProject.id)
      setRenderHistory(history)
    } catch (error) {
      console.error('Failed to load render history:', error)
    }
  }, [currentProject])

  // Load render history when project loads
  useEffect(() => {
    if (currentProject) {
      loadRenderHistory()
    }
  }, [currentProject, loadRenderHistory])

  // === New Session Handler ===
  const handleNewSession = async () => {
    if (!currentProject || !projectId) return

    // Save current session first if option is selected
    if (saveCurrentSessionBeforeNew) {
      const saveSessionName = lastSavedSessionName || `セクション_${new Date().toLocaleString('ja-JP', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
      }).replace(/[\/\s:]/g, '')}`

      try {
        const assetRefs = extractAssetReferences(currentProject.timeline_data, assets)
        const sessionData: SessionData = {
          schema_version: '1.0',
          timeline_data: currentProject.timeline_data,
          asset_references: assetRefs,
        }
        await assetsApi.saveSession(projectId, saveSessionName, sessionData)

        // Refresh assets list
        const updatedAssets = await assetsApi.list(projectId)
        setAssets(updatedAssets)
        setAssetLibraryRefreshTrigger(prev => prev + 1)
      } catch (error) {
        console.error('Failed to save current session:', error)
        alert('現在のセクションの保存に失敗しました')
        return
      }
    }

    // Create empty timeline with default structure
    const emptyTimeline: TimelineData = {
      version: '1.0',
      duration_ms: 0,
      layers: [
        { id: crypto.randomUUID(), name: 'レイヤー 1', type: 'content', order: 0, visible: true, locked: false, clips: [] },
      ],
      audio_tracks: [
        { id: crypto.randomUUID(), name: 'オーディオ 1', type: 'narration', volume: 1, muted: false, clips: [] },
      ],
      groups: [],
      markers: [],
    }

    // Update timeline
    await updateTimeline(projectId, emptyTimeline)

    // Clear selection and set new session name
    setSelectedClip(null)
    setSelectedVideoClip(null)
    // Set the new session name as the "last saved" name for future reference
    const finalSessionName = newSessionName.trim() || `セクション_${new Date().toLocaleString('ja-JP', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit'
    }).replace(/[\/\s:]/g, '')}`
    setLastSavedSessionName(finalSessionName)

    // Reset modal state
    setShowNewSessionConfirm(false)
    setSaveCurrentSessionBeforeNew(false)
    setNewSessionName('')

    // Clear history
    useProjectStore.getState().clearHistory()
  }

  // === Session Save Handler ===
  const handleSaveSession = async () => {
    if (!currentProject || !projectId) return
    if (!sessionNameInput.trim()) {
      alert('セクション名を入力してください')
      return
    }

    setSavingSession(true)
    try {
      // Extract asset references from current timeline
      const assetRefs = extractAssetReferences(currentProject.timeline_data, assets)

      // Build session data
      const sessionData: SessionData = {
        schema_version: '1.0',
        timeline_data: currentProject.timeline_data,
        asset_references: assetRefs,
      }

      // Save session via API
      await assetsApi.saveSession(projectId, sessionNameInput.trim(), sessionData)

      // Refresh assets list to show new session
      const updatedAssets = await assetsApi.list(projectId)
      setAssets(updatedAssets)

      // Also trigger refresh in AssetLibrary component
      setAssetLibraryRefreshTrigger(prev => prev + 1)

      // Remember the saved session name for next time
      setLastSavedSessionName(sessionNameInput.trim())

      // Close dialog and reset state
      setShowSaveSessionModal(false)
      setSessionNameInput('')

      // Show success message via toast
      setToastMessage({ text: '保存しました', type: 'success' })
    } catch (error) {
      console.error('Failed to save session:', error)
      alert('セクションの保存に失敗しました')
    } finally {
      setSavingSession(false)
    }
  }

  // === Session Save Handler for LeftPanel ===
  const handleSaveSessionFromPanel = useCallback(async (_sessionId: string | null, sessionName: string) => {
    // Note: _sessionId is available for future use (e.g., updating existing session)
    if (!currentProject || !projectId) return

    // Extract asset references from current timeline
    const assetRefs = extractAssetReferences(currentProject.timeline_data, assets)

    // Build session data
    const sessionData: SessionData = {
      schema_version: '1.0',
      timeline_data: currentProject.timeline_data,
      asset_references: assetRefs,
    }

    // Save session via API
    const savedAsset = await assetsApi.saveSession(projectId, sessionName, sessionData)

    // Update current session tracking
    setCurrentSessionId(savedAsset.id)
    setCurrentSessionName(sessionName)
    setLastSavedSessionName(sessionName)

    // Refresh assets list to show new session
    const updatedAssets = await assetsApi.list(projectId)
    setAssets(updatedAssets)

    // Also trigger refresh in AssetLibrary component
    setAssetLibraryRefreshTrigger(prev => prev + 1)

    // Show success toast
    setToastMessage({ text: '保存しました', type: 'success' })
  }, [currentProject, projectId, assets])

  // === Session Open Handler ===
  // pendingSessionInfo stores session ID and name to track which session is being opened
  const [pendingSessionInfo, setPendingSessionInfo] = useState<{ id: string; name: string } | null>(null)

  const handleOpenSession = (sessionData: SessionData, sessionId?: string, sessionName?: string) => {
    // Store pending session and show confirmation dialog
    setPendingSessionData(sessionData)
    if (sessionId && sessionName) {
      setPendingSessionInfo({ id: sessionId, name: sessionName })
    }
    setShowOpenSessionConfirm(true)
  }

  const handleConfirmOpenSession = async (saveFirst: boolean) => {
    if (!pendingSessionData || !projectId) return

    // Close confirmation dialog
    setShowOpenSessionConfirm(false)

    // Optionally save current work first
    if (saveFirst) {
      setSessionNameInput(lastSavedSessionName || '名称なし')
      setShowSaveSessionModal(true)
      // Wait for save to complete (user will trigger it manually)
      // For now, just return - user will need to open session again after saving
      setPendingSessionData(null)
      return
    }

    // Migrate session data if needed
    const { data: migratedData, warnings: migrationWarnings } = migrateSession(pendingSessionData)

    // Show migration warnings if any
    if (migrationWarnings.length > 0) {
      console.warn('Session migration warnings:', migrationWarnings)
    }

    // Map assets
    const mappingResult = mapSessionToProject(migratedData, assets)

    // If there are pending selections, show asset select dialog
    if (mappingResult.pendingSelections.length > 0) {
      setPendingSelections(mappingResult.pendingSelections)
      setUserSelections(new Map())
      setShowAssetSelectDialog(true)
      return
    }

    // Apply session immediately
    applySession(migratedData, mappingResult)
  }

  const handleAssetSelectionComplete = (selections: Map<string, string>) => {
    if (!pendingSessionData) return

    // Close asset select dialog
    setShowAssetSelectDialog(false)

    // Migrate session data again (in case it wasn't stored)
    const { data: migratedData } = migrateSession(pendingSessionData)

    // Re-run mapping with user selections
    const finalResult = mapSessionToProject(migratedData, assets, selections)

    // Apply session
    applySession(migratedData, finalResult)
  }

  const applySession = (sessionData: SessionData, mappingResult: MappingResult) => {
    if (!projectId || !currentProject) return

    // Apply mapping to timeline
    const mappedTimeline = applyMappingToTimeline(sessionData.timeline_data, mappingResult.assetMap)

    // Update unmapped assets state for UI warning
    setUnmappedAssetIds(new Set(mappingResult.unmappedAssetIds))

    // Update timeline
    updateTimeline(projectId, mappedTimeline as typeof currentProject.timeline_data)

    // Update current session tracking from pending info
    if (pendingSessionInfo) {
      setCurrentSessionId(pendingSessionInfo.id)
      setCurrentSessionName(pendingSessionInfo.name)
      setLastSavedSessionName(pendingSessionInfo.name)
    }

    // Clear pending session
    setPendingSessionData(null)
    setPendingSessionInfo(null)

    // Show completion message with warnings
    if (mappingResult.unmappedAssetIds.length > 0 || mappingResult.warnings.length > 0) {
      const messages: string[] = []
      if (mappingResult.unmappedAssetIds.length > 0) {
        messages.push(`${mappingResult.unmappedAssetIds.length}件のアセットがマッピングできませんでした。`)
      }
      if (mappingResult.warnings.length > 0) {
        messages.push(...mappingResult.warnings)
      }
      alert(`セクションを開きました。\n\n${messages.join('\n')}`)
    }
  }

  const handleCancelAssetSelection = () => {
    // Cancel session opening entirely
    setShowAssetSelectDialog(false)
    setPendingSessionData(null)
    setPendingSelections([])
    setUserSelections(new Map())
  }

  const handleStartRender = async (options: { start_ms?: number; end_ms?: number } = {}) => {
    if (!currentProject) return

    // Reset stale tracking
    lastUpdatedAtRef.current = null
    staleCountRef.current = 0

    // Show modal immediately with "processing" state
    setRenderJob({ status: 'processing', progress: 0 } as RenderJob)

    // Load render history in background
    loadRenderHistory()

    // Start polling FIRST (before the POST call)
    // This ensures we get progress updates while the synchronous render runs
    renderPollRef.current = window.setTimeout(pollRenderStatus, 1000)

    // Fire POST request - returns immediately, background task does the work
    // Polling handles the UI updates
    projectsApi.startRender(currentProject.id, { ...options })
      .then((job) => {
        console.log('[RENDER] POST completed:', job.status)
        // Just log - don't update state or stop polling
        // Let pollRenderStatus handle everything
      })
      .catch(async (error: unknown) => {
        // Handle 409 Conflict (stuck job) - auto-retry with force
        const axiosError = error as { response?: { status?: number } }
        if (axiosError.response?.status === 409) {
          console.log('409 Conflict - retrying with force=true')
          // Stop current polling before retry
          if (renderPollRef.current) {
            clearTimeout(renderPollRef.current)
            renderPollRef.current = null
          }
          // Retry with force flag
          projectsApi.startRender(currentProject.id, { ...options, force: true })
            .then((job) => {
              console.log('[RENDER] Force retry POST completed:', job.status)
            })
            .catch((retryError) => {
              console.error('Failed to start render (force retry):', retryError)
              setRenderJob(null)
              if (renderPollRef.current) {
                clearTimeout(renderPollRef.current)
                renderPollRef.current = null
              }
              alert('レンダリングの開始に失敗しました。')
            })
          return
        }
        console.error('Failed to start render:', error)
        setRenderJob(null)
        // Stop polling on error
        if (renderPollRef.current) {
          clearTimeout(renderPollRef.current)
          renderPollRef.current = null
        }
        alert('レンダリングの開始に失敗しました。')
      })
  }

  const handleCancelRender = async () => {
    if (!currentProject) return

    try {
      await projectsApi.cancelRender(currentProject.id)
      setRenderJob(prev => prev ? { ...prev, status: 'cancelled' } : null)

      // Stop polling
      if (renderPollRef.current) {
        clearTimeout(renderPollRef.current)
        renderPollRef.current = null
      }
    } catch (error) {
      console.error('Failed to cancel render:', error)
      alert('レンダリングのキャンセルに失敗しました。')
    }
  }

  const handleDownloadVideo = async () => {
    if (!currentProject) return

    try {
      const { download_url } = await projectsApi.getDownloadUrl(currentProject.id)
      window.open(download_url, '_blank')
    } catch (error) {
      console.error('Failed to get download URL:', error)
      alert('ダウンロードURLの取得に失敗しました。')
    }
  }

  // Clean up render polling on unmount
  useEffect(() => {
    return () => {
      if (renderPollRef.current) {
        clearTimeout(renderPollRef.current)
      }
    }
  }, [])

  // Helper to calculate volume with fade and volume keyframes applied
  const calculateFadeVolume = useCallback((
    timeMs: number,
    timing: {
      start_ms: number
      end_ms: number
      fade_in_ms: number
      fade_out_ms: number
      base_volume: number
      volume_keyframes?: VolumeKeyframe[]
      clip_volume?: number
    }
  ) => {
    const positionInClip = timeMs - timing.start_ms
    const clipDuration = timing.end_ms - timing.start_ms

    // If volume keyframes exist, use interpolated volume
    if (timing.volume_keyframes && timing.volume_keyframes.length > 0) {
      // Create a minimal AudioClip-like object for getInterpolatedVolume
      const fakeClip = {
        volume: timing.clip_volume ?? 1,
        volume_keyframes: timing.volume_keyframes
      } as AudioClipType
      const interpolatedVolume = getInterpolatedVolume(fakeClip, positionInClip)
      // Apply track volume (base_volume = track.volume * clip.volume, so divide by clip.volume to get track volume)
      const trackVolume = timing.clip_volume ? timing.base_volume / timing.clip_volume : 1
      return trackVolume * interpolatedVolume
    }

    // Fallback to original fade in/out logic
    let fadeMultiplier = 1.0

    // Fade in (at start of clip)
    if (timing.fade_in_ms > 0 && positionInClip < timing.fade_in_ms) {
      fadeMultiplier = positionInClip / timing.fade_in_ms
    }

    // Fade out (at end of clip)
    if (timing.fade_out_ms > 0 && positionInClip > clipDuration - timing.fade_out_ms) {
      const fadeOutPosition = clipDuration - positionInClip
      fadeMultiplier = Math.min(fadeMultiplier, fadeOutPosition / timing.fade_out_ms)
    }

    return timing.base_volume * Math.max(0, Math.min(1, fadeMultiplier))
  }, [])

  // Playback controls
  const stopPlayback = useCallback(() => {
    isPlayingRef.current = false
    setIsPlaying(false)
    if (playbackTimerRef.current) {
      cancelAnimationFrame(playbackTimerRef.current)
      playbackTimerRef.current = null
    }
    audioRefs.current.forEach(audio => {
      audio.pause()
      audio.currentTime = 0
    })
    // Pause all video previews
    videoRefsMap.current.forEach(video => video.pause())
  }, [])

  const startPlayback = useCallback(() => {
    if (!currentProject || !projectId) return

    // Stop any existing playback
    stopPlayback()
    isPlayingRef.current = true
    setIsPlaying(true)
    startTimeRef.current = performance.now() - currentTime

    // Clear previous audio timing info
    audioClipTimingRefs.current.clear()

    // Clean up orphaned audio refs - only keep audio elements for current clips
    const currentClipIds = new Set<string>()
    for (const track of currentProject.timeline_data.audio_tracks) {
      for (const clip of track.clips) {
        currentClipIds.add(clip.id)
      }
    }
    // Remove audio elements that are no longer in the timeline
    audioRefs.current.forEach((audio, clipId) => {
      if (!currentClipIds.has(clipId)) {
        audio.pause()
        audio.src = '' // Release the audio resource
        audioRefs.current.delete(clipId)
      }
    })

    // Clean up orphaned video refs
    const currentVideoClipIds = new Set<string>()
    for (const layer of currentProject.timeline_data.layers) {
      for (const clip of layer.clips) {
        if (clip.asset_id) currentVideoClipIds.add(clip.id)
      }
    }
    videoRefsMap.current.forEach((video, clipId) => {
      if (!currentVideoClipIds.has(clipId)) {
        video.pause()
        video.src = ''
        videoRefsMap.current.delete(clipId)
      }
    })

    // Load audio clips asynchronously (non-blocking)
    // The updatePlayhead callback will start each audio when it comes into range
    const loadAudioClips = async () => {
      for (const track of currentProject.timeline_data.audio_tracks) {
        if (track.muted) continue

        for (const clip of track.clips) {
          // Skip if playback was stopped
          if (!isPlayingRef.current) return

          try {
            // Use cached URL if available, otherwise fetch new one
            let url = assetUrlCache.get(clip.asset_id)
            if (!url) {
              const result = await assetsApi.getSignedUrl(projectId, clip.asset_id)
              url = result.url
            }
            let audio = audioRefs.current.get(clip.id)
            if (!audio) {
              audio = new Audio()
              audioRefs.current.set(clip.id, audio)
            }
            audio.src = url
            const baseVolume = track.volume * clip.volume

            // Store clip timing info for playback control including fades
            const clipEndMs = clip.start_ms + clip.duration_ms
            audioClipTimingRefs.current.set(clip.id, {
              start_ms: clip.start_ms,
              end_ms: clipEndMs,
              in_point_ms: clip.in_point_ms,
              fade_in_ms: clip.fade_in_ms || 0,
              fade_out_ms: clip.fade_out_ms || 0,
              base_volume: baseVolume,
              volume_keyframes: clip.volume_keyframes,
              clip_volume: clip.volume // Original clip volume for keyframe interpolation
            })

            // If playback is still active and clip is in range, start it now
            if (isPlayingRef.current) {
              const elapsed = performance.now() - startTimeRef.current
              const isCurrentlyInRange = elapsed >= clip.start_ms && elapsed < clipEndMs

              if (isCurrentlyInRange) {
                const offsetInClip = elapsed - clip.start_ms
                audio.currentTime = (clip.in_point_ms + offsetInClip) / 1000
                const timing = audioClipTimingRefs.current.get(clip.id)!
                audio.volume = calculateFadeVolume(elapsed, timing)
                audio.play().catch(console.error)
              }
            }
          } catch (error) {
            console.error('Failed to load audio:', error)
          }
        }
      }
    }
    // Fire and forget - don't wait for audio to load
    loadAudioClips()

    // Helper to find clip by ID
    const findClipById = (clipId: string) => {
      if (!currentProject) return null
      const layers = currentProject.timeline_data.layers
      for (const layer of layers) {
        if (layer.visible === false) continue
        for (const clip of layer.clips) {
          if (clip.id === clipId) {
            return clip
          }
        }
      }
      return null
    }

    // Start video playback for all video clips at current time
    videoRefsMap.current.forEach((video, clipId) => {
      const clip = findClipById(clipId)
      if (clip && currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
        // Video time = in_point + (timeline elapsed) * speed
        const speed = clip.speed || 1
        const videoTimeMs = clip.in_point_ms + (currentTime - clip.start_ms) * speed
        video.currentTime = videoTimeMs / 1000
        video.playbackRate = speed
        video.play().catch(console.error)
      }
    })

    // Update playhead position
    const updatePlayhead = () => {
      if (!isPlayingRef.current) return

      const elapsed = performance.now() - startTimeRef.current
      setCurrentTime(elapsed)

      // Sync audio playback with timeline - stop/start audio based on clip boundaries
      // and apply fade in/out effects
      audioRefs.current.forEach((audio, clipId) => {
        const timing = audioClipTimingRefs.current.get(clipId)
        if (!timing) return

        const isWithinClipRange = elapsed >= timing.start_ms && elapsed < timing.end_ms

        if (isWithinClipRange) {
          // Audio should be playing
          if (audio.paused) {
            // Calculate the correct position within the audio file
            const audioTimeMs = timing.in_point_ms + (elapsed - timing.start_ms)
            audio.currentTime = audioTimeMs / 1000
            audio.play().catch(console.error)
          }
          // Apply fade effect based on current position
          audio.volume = calculateFadeVolume(elapsed, timing)
        } else {
          // Audio should be paused (outside clip range)
          if (!audio.paused) {
            audio.pause()
          }
        }
      })

      // Sync video playback with timeline - for each video, find its clip and sync
      videoRefsMap.current.forEach((video, clipId) => {
        const clip = findClipById(clipId)
        if (!clip) return

        if (elapsed >= clip.start_ms && elapsed <= clip.start_ms + clip.duration_ms) {
          // Video should be playing
          if (video.paused) {
            // Video time = in_point + (timeline elapsed) * speed
            const speed = clip.speed || 1
            const videoTimeMs = clip.in_point_ms + (elapsed - clip.start_ms) * speed
            video.currentTime = videoTimeMs / 1000
            video.playbackRate = speed
            video.play().catch(console.error)
          }
        } else {
          // Video should be paused (outside clip range)
          if (!video.paused) {
            video.pause()
          }
        }
      })

      if (elapsed < (currentProject?.duration_ms || 0)) {
        playbackTimerRef.current = requestAnimationFrame(updatePlayhead)
      } else {
        // 最後まで再生したら停止（ループせず、currentTimeはそのまま維持）
        setCurrentTime(currentProject?.duration_ms || elapsed)
        stopPlayback()
      }
    }
    playbackTimerRef.current = requestAnimationFrame(updatePlayhead)
  }, [currentProject, projectId, assets, currentTime, stopPlayback, calculateFadeVolume])

  const togglePlayback = useCallback(() => {
    if (isPlaying) {
      stopPlayback()
    } else {
      startPlayback()
    }
  }, [isPlaying, startPlayback, stopPlayback])

  const handleSeek = useCallback((timeMs: number) => {
    // Stop current playback if playing
    if (isPlaying) {
      stopPlayback()
    }
    // Set new time position - video sync is handled by the useEffect
    setCurrentTime(timeMs)
  }, [isPlaying, stopPlayback])

  // Update video clip properties
  const handleUpdateVideoClip = useCallback(async (
    updates: Partial<{
      transform: { x?: number; y?: number; scale?: number; rotation?: number }
      effects: { opacity?: number; fade_in_ms?: number; fade_out_ms?: number; chroma_key?: { enabled?: boolean; color?: string; similarity?: number; blend?: number } }
      crop?: { top: number; right: number; bottom: number; left: number }
      speed?: number
      text_content?: string
      text_style?: Partial<{
        fontFamily: string
        fontSize: number
        fontWeight: 'normal' | 'bold'
        fontStyle: 'normal' | 'italic'
        color: string
        backgroundColor: string
        backgroundOpacity: number
        textAlign: 'left' | 'center' | 'right'
        verticalAlign: 'top' | 'middle' | 'bottom'
        lineHeight: number
        letterSpacing: number
        strokeColor: string
        strokeWidth: number
      }>
    }>
  ) => {
    if (!selectedVideoClip || !projectId) return

    // Get the latest currentProject from store to avoid stale closure issues
    const latestProject = useProjectStore.getState().currentProject
    if (!latestProject) return

    const updatedLayers = latestProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip

          // When keyframes exist and transform or opacity is being updated, update the keyframe at current time
          let updatedKeyframes = clip.keyframes
          const hasTransformOrOpacityUpdate = updates.transform || updates.effects?.opacity !== undefined
          if (hasTransformOrOpacityUpdate && clip.keyframes && clip.keyframes.length > 0) {
            const timeInClipMs = currentTime - clip.start_ms
            if (timeInClipMs >= 0 && timeInClipMs <= clip.duration_ms) {
              // Get current interpolated values as base
              const interpolated = getInterpolatedTransform(clip, timeInClipMs)
              const newTransform = {
                x: updates.transform?.x ?? interpolated.x,
                y: updates.transform?.y ?? interpolated.y,
                scale: updates.transform?.scale ?? interpolated.scale,
                rotation: updates.transform?.rotation ?? interpolated.rotation,
              }
              const newOpacity = updates.effects?.opacity ?? interpolated.opacity
              updatedKeyframes = addKeyframe(clip, timeInClipMs, newTransform, newOpacity)
            }
          }

          // Calculate new duration if speed is being updated
          let newDurationMs = clip.duration_ms
          const newSpeed = updates.speed ?? clip.speed
          if (updates.speed !== undefined && updates.speed !== clip.speed) {
            // When speed changes, recalculate duration to keep source portion same
            const sourceDuration = (clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1))) - clip.in_point_ms
            newDurationMs = Math.round(sourceDuration / updates.speed)
          }

          return {
            ...clip,
            transform: updates.transform ? { ...clip.transform, ...updates.transform } : clip.transform,
            keyframes: updatedKeyframes,
            effects: updates.effects ? {
              ...clip.effects,
              opacity: updates.effects.opacity ?? clip.effects.opacity,
              fade_in_ms: updates.effects.fade_in_ms ?? clip.effects.fade_in_ms,
              fade_out_ms: updates.effects.fade_out_ms ?? clip.effects.fade_out_ms,
              chroma_key: updates.effects.chroma_key ? {
                enabled: updates.effects.chroma_key.enabled ?? clip.effects.chroma_key?.enabled ?? false,
                color: updates.effects.chroma_key.color ?? clip.effects.chroma_key?.color ?? '#00ff00',
                similarity: updates.effects.chroma_key.similarity ?? clip.effects.chroma_key?.similarity ?? 0.05,
                blend: updates.effects.chroma_key.blend ?? clip.effects.chroma_key?.blend ?? 0.0,
              } : clip.effects.chroma_key,
            } : clip.effects,
            crop: updates.crop ?? clip.crop,
            speed: newSpeed,
            duration_ms: newDurationMs,
            text_content: updates.text_content ?? clip.text_content,
            text_style: updates.text_style && clip.text_style
              ? { ...clip.text_style, ...updates.text_style } as typeof clip.text_style
              : clip.text_style,
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...latestProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        transform: clip.transform,
        effects: clip.effects,
        keyframes: clip.keyframes,
        speed: clip.speed ?? 1,
        durationMs: clip.duration_ms,
        outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
        textContent: clip.text_content,
        textStyle: clip.text_style as typeof selectedVideoClip.textStyle,
      })
    }
  }, [selectedVideoClip, projectId, currentTime, updateTimeline])

  // Local-only version of handleUpdateVideoClip (no API call, no undo history).
  // Used during slider drag for instant preview without flooding the backend.
  const handleUpdateVideoClipLocal = useCallback((
    updates: Parameters<typeof handleUpdateVideoClip>[0]
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip

          // When keyframes exist and transform or opacity is being updated, update the keyframe at current time
          let updatedKeyframes = clip.keyframes
          const hasTransformOrOpacityUpdate = updates.transform || updates.effects?.opacity !== undefined
          if (hasTransformOrOpacityUpdate && clip.keyframes && clip.keyframes.length > 0) {
            const timeInClipMs = currentTime - clip.start_ms
            if (timeInClipMs >= 0 && timeInClipMs <= clip.duration_ms) {
              const interpolated = getInterpolatedTransform(clip, timeInClipMs)
              const newTransform = {
                x: updates.transform?.x ?? interpolated.x,
                y: updates.transform?.y ?? interpolated.y,
                scale: updates.transform?.scale ?? interpolated.scale,
                rotation: updates.transform?.rotation ?? interpolated.rotation,
              }
              const newOpacity = updates.effects?.opacity ?? interpolated.opacity
              updatedKeyframes = addKeyframe(clip, timeInClipMs, newTransform, newOpacity)
            }
          }

          // Calculate new duration if speed is being updated
          let newDurationMs = clip.duration_ms
          const newSpeed = updates.speed ?? clip.speed
          if (updates.speed !== undefined && updates.speed !== clip.speed) {
            // When speed changes, recalculate duration to keep source portion same
            const sourceDuration = (clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1))) - clip.in_point_ms
            newDurationMs = Math.round(sourceDuration / updates.speed)
          }

          return {
            ...clip,
            transform: updates.transform ? { ...clip.transform, ...updates.transform } : clip.transform,
            keyframes: updatedKeyframes,
            effects: updates.effects ? {
              ...clip.effects,
              opacity: updates.effects.opacity ?? clip.effects.opacity,
              fade_in_ms: updates.effects.fade_in_ms ?? clip.effects.fade_in_ms,
              fade_out_ms: updates.effects.fade_out_ms ?? clip.effects.fade_out_ms,
              chroma_key: updates.effects.chroma_key ? {
                enabled: updates.effects.chroma_key.enabled ?? clip.effects.chroma_key?.enabled ?? false,
                color: updates.effects.chroma_key.color ?? clip.effects.chroma_key?.color ?? '#00ff00',
                similarity: updates.effects.chroma_key.similarity ?? clip.effects.chroma_key?.similarity ?? 0.05,
                blend: updates.effects.chroma_key.blend ?? clip.effects.chroma_key?.blend ?? 0.0,
              } : clip.effects.chroma_key,
            } : clip.effects,
            crop: updates.crop ?? clip.crop,
            speed: newSpeed,
            duration_ms: newDurationMs,
            text_content: updates.text_content ?? clip.text_content,
            text_style: updates.text_style && clip.text_style
              ? { ...clip.text_style, ...updates.text_style } as typeof clip.text_style
              : clip.text_style,
          }
        }),
      }
    })

    updateTimelineLocal(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        transform: clip.transform,
        effects: clip.effects,
        keyframes: clip.keyframes,
        speed: clip.speed ?? 1,
        durationMs: clip.duration_ms,
        outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
        textContent: clip.text_content,
        textStyle: clip.text_style as typeof selectedVideoClip.textStyle,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, currentTime, updateTimelineLocal])

  // Update video clip timing (start_ms, duration_ms)
  const handleUpdateVideoClipTiming = useCallback(async (
    updates: Partial<{ startMs: number; durationMs: number }>
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip
          return {
            ...clip,
            start_ms: updates.startMs !== undefined ? Math.max(0, updates.startMs) : clip.start_ms,
            duration_ms: updates.durationMs !== undefined ? Math.max(100, updates.durationMs) : clip.duration_ms,
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        startMs: clip.start_ms,
        durationMs: clip.duration_ms,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  // Delete selected video clip
  const handleDeleteVideoClip = useCallback(async () => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.filter(clip => clip.id !== selectedVideoClip.clipId),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })
    setSelectedVideoClip(null)
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  // Update audio clip properties
  const handleUpdateAudioClip = useCallback(async (
    updates: Partial<{
      volume: number
      fade_in_ms: number
      fade_out_ms: number
      start_ms: number
      volume_keyframes: VolumeKeyframe[]
    }>
  ) => {
    if (!selectedClip || !currentProject || !projectId) return

    const updatedTracks = currentProject.timeline_data.audio_tracks.map(track => {
      if (track.id !== selectedClip.trackId) return track
      return {
        ...track,
        clips: track.clips.map(clip => {
          if (clip.id !== selectedClip.clipId) return clip
          return {
            ...clip,
            volume: updates.volume ?? clip.volume,
            fade_in_ms: updates.fade_in_ms ?? clip.fade_in_ms,
            fade_out_ms: updates.fade_out_ms ?? clip.fade_out_ms,
            start_ms: updates.start_ms ?? clip.start_ms,
            volume_keyframes: updates.volume_keyframes !== undefined ? updates.volume_keyframes : clip.volume_keyframes,
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, audio_tracks: updatedTracks })

    // Update selected clip state to reflect changes
    const track = updatedTracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (clip) {
      setSelectedClip({
        ...selectedClip,
        volume: clip.volume,
        fadeInMs: clip.fade_in_ms,
        fadeOutMs: clip.fade_out_ms,
        startMs: clip.start_ms,
      })
    }
  }, [selectedClip, currentProject, projectId, updateTimeline])

  // Add volume keyframe at current playhead position
  const handleAddVolumeKeyframeAtCurrent = useCallback(async (volume: number = 1.0) => {
    if (!selectedClip || !currentProject || !projectId) return

    // Use ref to get the latest currentTime value (avoids stale closure)
    const latestCurrentTime = currentTimeRef.current

    // Get the LATEST clip data from timeline (selectedClip might be stale)
    const track = currentProject.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip) {
      console.log('[VolumeKeyframe] Clip not found in timeline')
      return
    }

    // Calculate time relative to clip start using LATEST clip data
    const clipStartMs = clip.start_ms
    const clipDurationMs = clip.duration_ms
    const timeInClipMs = latestCurrentTime - clipStartMs

    console.log('[VolumeKeyframe] Adding keyframe:', {
      latestCurrentTime,
      clipStartMs,
      clipDurationMs,
      timeInClipMs,
      volume
    })

    // Check if playhead is within clip bounds
    if (timeInClipMs < 0 || timeInClipMs > clipDurationMs) {
      console.log('[VolumeKeyframe] Playhead is outside clip bounds:', { timeInClipMs, clipDurationMs })
      return
    }

    // Get existing keyframes
    const existingKeyframes = clip.volume_keyframes || []

    // Add new keyframe (addVolumeKeyframe handles deduplication)
    const { addVolumeKeyframe } = await import('@/utils/volumeKeyframes')
    const newKeyframes = addVolumeKeyframe(existingKeyframes, timeInClipMs, volume)

    console.log('[VolumeKeyframe] New keyframes:', newKeyframes)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, currentProject, projectId, handleUpdateAudioClip])

  // Clear all volume keyframes
  const handleClearVolumeKeyframes = useCallback(async () => {
    if (!selectedClip || !currentProject || !projectId) return
    await handleUpdateAudioClip({ volume_keyframes: [] })
  }, [selectedClip, currentProject, projectId, handleUpdateAudioClip])

  // Remove a single volume keyframe by index
  const handleRemoveVolumeKeyframe = useCallback(async (index: number) => {
    if (!selectedClip || !currentProject || !projectId) return

    const track = currentProject.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    const newKeyframes = sortedKeyframes.filter((_, i) => i !== index)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, currentProject, projectId, handleUpdateAudioClip])

  // Update a single volume keyframe
  const handleUpdateVolumeKeyframe = useCallback(async (index: number, timeMs: number, value: number) => {
    if (!selectedClip || !currentProject || !projectId) return

    const track = currentProject.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    const newKeyframes = sortedKeyframes.map((kf, i) =>
      i === index ? { time_ms: Math.max(0, Math.round(timeMs)), value: Math.max(0, Math.min(1, value)) } : kf
    )

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, currentProject, projectId, handleUpdateAudioClip])

  // Add volume keyframe with specific time and value
  const handleAddVolumeKeyframeManual = useCallback(async (timeMs: number, value: number) => {
    if (!selectedClip || !currentProject || !projectId) return

    const track = currentProject.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip) return

    const existingKeyframes = clip.volume_keyframes || []
    const { addVolumeKeyframe } = await import('@/utils/volumeKeyframes')
    const newKeyframes = addVolumeKeyframe(existingKeyframes, timeMs, value)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, currentProject, projectId, handleUpdateAudioClip])

  // Fit, Fill, or Stretch video/image to canvas
  const handleFitFillStretch = useCallback((mode: 'fit' | 'fill' | 'stretch') => {
    console.log('[Fit/Fill/Stretch] Called with mode:', mode)
    if (!selectedVideoClip || !currentProject) return

    // Find the asset to get original dimensions
    const asset = assets.find(a => a.id === selectedVideoClip.assetId)
    const isImageClip = asset?.type === 'image'
    console.log('[Fit/Fill] isImageClip:', isImageClip, 'asset:', asset?.name)

    // Get the clip to access crop values
    const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    const crop = clip?.crop || { top: 0, right: 0, bottom: 0, left: 0 }

    // Try to get dimensions from multiple sources
    let assetWidth = asset?.width
    let assetHeight = asset?.height

    // If asset dimensions not available, try getting from video element
    if (!assetWidth || !assetHeight) {
      const videoEl = videoRefsMap.current.get(selectedVideoClip.clipId)
      if (videoEl && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
        assetWidth = videoEl.videoWidth
        assetHeight = videoEl.videoHeight
      }
    }

    // Try stored dimensions from clip transform
    if (!assetWidth || !assetHeight) {
      const storedWidth = (clip?.transform as { width?: number | null })?.width
      const storedHeight = (clip?.transform as { height?: number | null })?.height
      if (storedWidth && storedHeight) {
        assetWidth = storedWidth
        assetHeight = storedHeight
        console.log('[Fit/Fill] Got dimensions from clip transform:', assetWidth, 'x', assetHeight)
      }
    }

    // For images without dimensions, try to get from the rendered image element
    if ((!assetWidth || !assetHeight) && isImageClip) {
      // Find the image element in the preview using data attributes
      const imgEl = document.querySelector(`img[data-clip-id="${selectedVideoClip.clipId}"]`) as HTMLImageElement
        || document.querySelector(`img[data-asset-id="${selectedVideoClip.assetId}"]`) as HTMLImageElement
      console.log('[Fit/Fill] Looking for IMG element, found:', imgEl, 'naturalWidth:', imgEl?.naturalWidth)
      if (imgEl && imgEl.naturalWidth > 0 && imgEl.naturalHeight > 0) {
        assetWidth = imgEl.naturalWidth
        assetHeight = imgEl.naturalHeight
        console.log('[Fit/Fill] Got dimensions from IMG element:', assetWidth, 'x', assetHeight)
      }
    }

    // If still not available, cannot perform fit/fill/stretch
    if (!assetWidth || !assetHeight) {
      console.warn('Fit/Fill/Stretch: Unable to get asset dimensions, operation cancelled')
      return
    }

    const canvasWidth = currentProject.width || 1920
    const canvasHeight = currentProject.height || 1080

    // Calculate visible dimensions after crop
    const visibleWidth = assetWidth * (1 - crop.left - crop.right)
    const visibleHeight = assetHeight * (1 - crop.top - crop.bottom)
    console.log('[Fit/Fill] crop:', crop, '| visible:', visibleWidth, 'x', visibleHeight)

    // Calculate scale based on VISIBLE dimensions (cropped area)
    const scaleX = canvasWidth / visibleWidth
    const scaleY = canvasHeight / visibleHeight

    let newWidth: number
    let newHeight: number

    if (mode === 'stretch') {
      // Stretch: change aspect ratio to match canvas exactly (based on visible area)
      // Calculate the full asset size needed so visible area fills canvas
      newWidth = canvasWidth / (1 - crop.left - crop.right)
      newHeight = canvasHeight / (1 - crop.top - crop.bottom)
      console.log('[Fit/Fill/Stretch] STRETCH to canvas:', newWidth, 'x', newHeight)
    } else {
      // Fit or Fill: maintain aspect ratio based on visible dimensions
      const targetScale = mode === 'fit' ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY)
      // Apply scale to FULL asset dimensions (so visible area fits/fills canvas)
      newWidth = assetWidth * targetScale
      newHeight = assetHeight * targetScale
      console.log('[Fit/Fill/Stretch] asset:', assetWidth, 'x', assetHeight, '| visible:', visibleWidth, 'x', visibleHeight, '| canvas:', canvasWidth, 'x', canvasHeight)
      console.log('[Fit/Fill/Stretch] scaleX:', scaleX, 'scaleY:', scaleY, '| mode:', mode, '| targetScale:', targetScale)
      console.log('[Fit/Fill/Stretch] newSize:', newWidth, 'x', newHeight)
    }

    // Calculate offset to center the CROPPED area (not the full image)
    // When crop.left != crop.right, the visible center is offset from the image center
    // Offset = (crop.left - crop.right) / 2 * displayWidth (negative because we shift left if crop.left > crop.right)
    const cropOffsetX = -((crop.left - crop.right) / 2) * newWidth
    const cropOffsetY = -((crop.top - crop.bottom) / 2) * newHeight
    console.log('[Fit/Fill/Stretch] cropOffset:', cropOffsetX, cropOffsetY)

    if (isImageClip) {
      // For images: use width/height

      // Update the clip's transform with calculated dimensions
      const updatedLayers = currentProject.timeline_data.layers.map(l => {
        if (l.id !== selectedVideoClip.layerId) return l
        return {
          ...l,
          clips: l.clips.map(c => {
            if (c.id !== selectedVideoClip.clipId) return c
            return {
              ...c,
              transform: {
                ...c.transform,
                x: Math.round(cropOffsetX),
                y: Math.round(cropOffsetY),
                width: Math.round(newWidth),
                height: Math.round(newHeight),
                scale: 1, // Reset scale since we're using explicit dimensions
                rotation: 0,
              },
            }
          }),
        }
      })

      updateTimeline(projectId!, { ...currentProject.timeline_data, layers: updatedLayers })
    } else {
      // For videos: use scale (stretch not supported for videos, use fill instead)
      const videoScale = mode === 'stretch'
        ? Math.max(scaleX, scaleY)  // Fallback to fill for videos
        : mode === 'fit'
          ? Math.min(scaleX, scaleY)
          : Math.max(scaleX, scaleY)
      // For videos, calculate offset based on video dimensions after scale
      const videoOffsetX = Math.round(-((crop.left - crop.right) / 2) * assetWidth * videoScale)
      const videoOffsetY = Math.round(-((crop.top - crop.bottom) / 2) * assetHeight * videoScale)
      handleUpdateVideoClip({
        transform: {
          x: videoOffsetX,
          y: videoOffsetY,
          scale: videoScale,
          rotation: 0,
        }
      })
    }
  }, [selectedVideoClip, currentProject, assets, handleUpdateVideoClip, projectId, updateTimeline])

  // Update shape properties
  const handleUpdateShape = useCallback(async (
    updates: Partial<Shape>
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip
          if (!clip.shape) return clip
          return {
            ...clip,
            shape: { ...clip.shape, ...updates },
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip?.shape) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        shape: clip.shape,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  // Local-only version of handleUpdateShape (no API call, no undo history).
  // Used during slider drag for instant preview without flooding the backend.
  const handleUpdateShapeLocal = useCallback((
    updates: Partial<Shape>
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip
          if (!clip.shape) return clip
          return {
            ...clip,
            shape: { ...clip.shape, ...updates },
          }
        }),
      }
    })

    updateTimelineLocal(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip?.shape) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        shape: clip.shape,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimelineLocal])

  const replaceClipAsset = useCallback(async (clipId: string, newAsset: Asset) => {
    if (!projectId) return
    const latestProject = useProjectStore.getState().currentProject
    if (!latestProject) return

    const updatedLayers = latestProject.timeline_data.layers.map(layer => {
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== clipId) return clip
          const nextEffects = clip.effects?.chroma_key
            ? { ...clip.effects, chroma_key: { ...clip.effects.chroma_key, enabled: false } }
            : clip.effects
          return {
            ...clip,
            asset_id: newAsset.id,
            effects: nextEffects,
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...latestProject.timeline_data, layers: updatedLayers })

    if (selectedVideoClip?.clipId === clipId) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        assetId: newAsset.id,
        assetName: newAsset.name,
        effects: selectedVideoClip.effects?.chroma_key
          ? {
              ...selectedVideoClip.effects,
              chroma_key: { ...selectedVideoClip.effects.chroma_key, enabled: false },
            }
          : selectedVideoClip.effects,
      })
    }
  }, [projectId, updateTimeline, selectedVideoClip])

  const handleChromaKeyPreview = useCallback(async () => {
    if (!selectedVideoClip || !projectId) return
    const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
    if (!clipAsset || clipAsset.type !== 'video') return

    const chromaKey = selectedVideoClip.effects?.chroma_key || {
      enabled: false,
      color: '#00FF00',
      similarity: 0.05,
      blend: 0.0,
    }

    setChromaPreviewLoading(true)
    setChromaPreviewError(null)
    setChromaPreviewFrames([])
    setChromaPreviewSelectedIndex(null)

    try {
      // Generate 5-frame preview at 10%, 30%, 50%, 70%, 90% of clip duration
      // Use return_transparent_png to get transparent images for compositing with other layers
      const result = await aiV1Api.chromaKeyPreview(projectId, selectedVideoClip.clipId, {
        key_color: chromaKey.color,
        similarity: chromaKey.similarity,
        blend: chromaKey.blend,
        resolution: '640x360',
        return_transparent_png: true,
      })
      console.log('Chroma key preview result:', result)

      setChromaPreviewFrames(result.frames)
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error?.message
        || (err as Error).message
        || 'プレビューに失敗しました'
      setChromaPreviewError(message)
    } finally {
      setChromaPreviewLoading(false)
    }
  }, [selectedVideoClip, projectId, assets, handleUpdateVideoClip])

  const handleChromaKeyApply = useCallback(async () => {
    if (!selectedVideoClip || !projectId) return
    const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
    if (!clipAsset || clipAsset.type !== 'video') return

    const chromaKey = selectedVideoClip.effects?.chroma_key || {
      enabled: false,
      color: '#00FF00',
      similarity: 0.05,
      blend: 0.0,
    }

    setChromaApplyLoading(true)
    setChromaPreviewError(null)

    try {
      const result = await aiV1Api.chromaKeyApply(
        projectId,
        selectedVideoClip.clipId,
        {
          key_color: chromaKey.color,
          similarity: chromaKey.similarity,
          blend: chromaKey.blend,
        },
        uuidv4()
      )

      setAssets(prev => {
        if (prev.some(a => a.id === result.asset.id)) return prev
        return [...prev, result.asset]
      })

      const shouldReplace = window.confirm('クロマキー処理が完了しました。新しいクリップに置き換えますか？')
      if (shouldReplace) {
        await replaceClipAsset(selectedVideoClip.clipId, result.asset)
      }

      try {
        const { url } = await assetsApi.getSignedUrl(projectId, result.asset.id)
        setAssetUrlCache(prev => new Map(prev).set(result.asset.id, url))
      } catch {
        // Ignore signed URL prefetch errors
      }
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error?.message
        || (err as Error).message
        || 'クロマキー処理に失敗しました'
      setChromaPreviewError(message)
    } finally {
      setChromaApplyLoading(false)
    }
  }, [selectedVideoClip, projectId, assets, replaceClipAsset])

  // Local-only version of handleUpdateShapeFade (no API call, no undo history).
  const handleUpdateShapeFadeLocal = useCallback((
    updates: { fadeInMs?: number; fadeOutMs?: number }
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip
          return {
            ...clip,
            fade_in_ms: updates.fadeInMs !== undefined ? updates.fadeInMs : clip.fade_in_ms,
            fade_out_ms: updates.fadeOutMs !== undefined ? updates.fadeOutMs : clip.fade_out_ms,
          }
        }),
      }
    })

    updateTimelineLocal(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        fadeInMs: clip.fade_in_ms,
        fadeOutMs: clip.fade_out_ms,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimelineLocal])

  // Update shape fade properties
  const handleUpdateShapeFade = useCallback(async (
    updates: { fadeInMs?: number; fadeOutMs?: number }
  ) => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const updatedLayers = currentProject.timeline_data.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== selectedVideoClip.clipId) return clip
          return {
            ...clip,
            fade_in_ms: updates.fadeInMs !== undefined ? updates.fadeInMs : clip.fade_in_ms,
            fade_out_ms: updates.fadeOutMs !== undefined ? updates.fadeOutMs : clip.fade_out_ms,
          }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        fadeInMs: clip.fade_in_ms,
        fadeOutMs: clip.fade_out_ms,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  // Add or update keyframe at current time
  const handleAddKeyframe = useCallback(async () => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return

    // Calculate time relative to clip start
    const timeInClipMs = currentTime - clip.start_ms
    if (timeInClipMs < 0 || timeInClipMs > clip.duration_ms) {
      alert('再生ヘッドがクリップの範囲内にありません')
      return
    }

    // Get current transform values (either interpolated or base)
    const currentTransform = clip.keyframes && clip.keyframes.length > 0
      ? getInterpolatedTransform(clip, timeInClipMs)
      : {
          x: clip.transform.x,
          y: clip.transform.y,
          scale: clip.transform.scale,
          rotation: clip.transform.rotation,
          opacity: clip.effects.opacity,
        }

    const newKeyframes = addKeyframe(
      clip,
      timeInClipMs,
      {
        x: currentTransform.x,
        y: currentTransform.y,
        scale: currentTransform.scale,
        rotation: currentTransform.rotation,
      },
      currentTransform.opacity
    )

    const updatedLayers = currentProject.timeline_data.layers.map(l => {
      if (l.id !== selectedVideoClip.layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== selectedVideoClip.clipId) return c
          return { ...c, keyframes: newKeyframes }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    // Update selected clip state
    setSelectedVideoClip({
      ...selectedVideoClip,
      keyframes: newKeyframes,
    })

    // Auto-select the newly added keyframe
    const newIndex = newKeyframes.findIndex(kf => Math.abs(kf.time_ms - timeInClipMs) < 100)
    if (newIndex >= 0) {
      setSelectedKeyframeIndex(newIndex)
    }
  }, [selectedVideoClip, currentProject, projectId, currentTime, updateTimeline])

  // Remove keyframe at current time
  const handleRemoveKeyframe = useCallback(async () => {
    if (!selectedVideoClip || !currentProject || !projectId) return

    const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return

    const timeInClipMs = currentTime - clip.start_ms

    const newKeyframes = removeKeyframe(clip, timeInClipMs)

    const updatedLayers = currentProject.timeline_data.layers.map(l => {
      if (l.id !== selectedVideoClip.layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== selectedVideoClip.clipId) return c
          return { ...c, keyframes: newKeyframes }
        }),
      }
    })

    await updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

    setSelectedVideoClip({
      ...selectedVideoClip,
      keyframes: newKeyframes,
    })
  }, [selectedVideoClip, currentProject, projectId, currentTime, updateTimeline])

  // Check if keyframe exists at current time
  const currentKeyframeExists = useCallback(() => {
    if (!selectedVideoClip || !currentProject) return false

    const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return false

    const timeInClipMs = currentTime - clip.start_ms
    return hasKeyframeAt(clip, timeInClipMs)
  }, [selectedVideoClip, currentProject, currentTime])

  // Get current interpolated values for display
  const getCurrentInterpolatedValues = useCallback(() => {
    if (!selectedVideoClip || !currentProject) return null

    const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return null

    const timeInClipMs = currentTime - clip.start_ms
    if (timeInClipMs < 0 || timeInClipMs > clip.duration_ms) return null

    if (clip.keyframes && clip.keyframes.length > 0) {
      return getInterpolatedTransform(clip, timeInClipMs)
    }

    return {
      x: clip.transform.x,
      y: clip.transform.y,
      scale: clip.transform.scale,
      rotation: clip.transform.rotation,
      opacity: clip.effects.opacity,
    }
  }, [selectedVideoClip, currentProject, currentTime])

  // Handle keyframe selection from timeline diamond markers
  const handleKeyframeSelect = useCallback((clipId: string, keyframeIndex: number | null) => {
    if (!currentProject) return

    // Find the clip to select it and move playhead
    for (const layer of currentProject.timeline_data.layers) {
      const clip = layer.clips.find(c => c.id === clipId)
      if (clip) {
        // Select the clip if not already selected
        if (selectedVideoClip?.clipId !== clipId) {
          const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
          let assetName = 'Clip'
          if (asset) assetName = asset.name
          else if (clip.text_content) assetName = `テキスト: ${clip.text_content.slice(0, 10)}`
          else if (clip.shape) assetName = clip.shape.type
          setSelectedVideoClip({
            layerId: layer.id,
            layerName: layer.name,
            clipId,
            assetId: clip.asset_id,
            assetName,
            startMs: clip.start_ms,
            durationMs: clip.duration_ms,
            inPointMs: clip.in_point_ms,
            outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
            transform: clip.transform,
            effects: clip.effects,
            keyframes: clip.keyframes,
            shape: clip.shape,
            textContent: clip.text_content,
            textStyle: clip.text_style,
            fadeInMs: clip.effects.fade_in_ms ?? 0,
            fadeOutMs: clip.effects.fade_out_ms ?? 0,
          })
          setSelectedClip(null)
        }

        // Set keyframe selection
        setSelectedKeyframeIndex(keyframeIndex)

        // Move playhead to keyframe time
        if (keyframeIndex !== null && clip.keyframes && clip.keyframes[keyframeIndex]) {
          const kfTimeMs = clip.start_ms + clip.keyframes[keyframeIndex].time_ms
          setCurrentTime(kfTimeMs)
        }
        break
      }
    }
  }, [currentProject, selectedVideoClip, assets])

  // Simplified preview drag handlers
  const handlePreviewDragStart = useCallback((
    e: React.MouseEvent,
    type: 'move' | 'resize' | 'resize-tl' | 'resize-tr' | 'resize-bl' | 'resize-br' | 'resize-t' | 'resize-b' | 'resize-l' | 'resize-r' | 'crop-t' | 'crop-r' | 'crop-b' | 'crop-l',
    layerId: string,
    clipId: string
  ) => {
    e.preventDefault()
    e.stopPropagation()

    if (!currentProject) return
    const layer = currentProject.timeline_data.layers.find(l => l.id === layerId)
    const clip = layer?.clips.find(c => c.id === clipId)
    if (!clip || !layer) return
    if (layer.locked) return

    // Select the clip when clicking in preview
    const clickedAsset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    setSelectedVideoClip({
      layerId,
      layerName: layer.name,
      clipId,
      assetId: clip.asset_id || '',
      assetName: clickedAsset?.name || clip.shape?.type || 'テキスト',
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
    setSelectedClip(null) // Deselect audio clip
    // Clear asset library preview when selecting a clip
    if (preview.asset) {
      setPreview({ asset: null, url: null, loading: false })
    }

    // Get current transform
    const timeInClipMs = currentTime - clip.start_ms
    const currentTransform = clip.keyframes && clip.keyframes.length > 0
      ? getInterpolatedTransform(clip, timeInClipMs)
      : { x: clip.transform.x, y: clip.transform.y, scale: clip.transform.scale, rotation: clip.transform.rotation }

    // ============================================================
    // SHAPE RESIZE COORDINATE SYSTEM (重要！)
    // ============================================================
    // CSS変換順序: translate(-50%, -50%) translate(x, y) scale(s)
    //
    // 座標系の理解:
    // - shape.width/height: SVGの固有サイズ（論理ピクセル）
    // - transform.x/y: キャンバス中心からのオフセット（論理ピクセル）
    // - transform.scale: 表示倍率
    // - 画面上の実際サイズ = shape.width * scale
    //
    // アンカーベースリサイズのルール:
    // 1. アンカー位置計算: offset = (size / 2) * scale
    // 2. マウス移動→サイズ変化: deltaSize = logicalDelta / scale
    // 3. 新中心位置: newCenter = anchor ± (newSize / 2) * scale
    // ============================================================
    const cx = currentTransform.x
    const cy = currentTransform.y
    const scale = currentTransform.scale

    // Detect if this is an image clip (independent w/h resize) vs video clip (uniform scale)
    const clipAsset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    const isImageClip = clipAsset?.type === 'image'

    // Get dimensions: for shapes use shape.width/height, for images use transform.width/height, for videos get natural dimensions
    let w = clip.shape?.width || 100
    let h = clip.shape?.height || 100
    if (!clip.shape && clip.asset_id) {
      if (isImageClip) {
        // For images: use stored width/height from transform, or fall back to asset dimensions
        // Images use independent w/h resize like shapes
        const transformWidth = (clip.transform as { width?: number | null }).width
        const transformHeight = (clip.transform as { height?: number | null }).height

        if (transformWidth && transformHeight) {
          w = transformWidth
          h = transformHeight
        } else if (clipAsset?.width && clipAsset?.height) {
          w = clipAsset.width
          h = clipAsset.height
        } else {
          // Fallback: get dimensions from the rendered image element
          const imgEl = document.querySelector(`img[data-clip-id="${clipId}"]`) as HTMLImageElement
          if (imgEl && imgEl.naturalWidth > 0 && imgEl.naturalHeight > 0) {
            w = imgEl.naturalWidth
            h = imgEl.naturalHeight
            console.log('[handlePreviewDragStart] Got image dimensions from DOM:', w, 'x', h)
          } else {
            // Last resort default
            w = 400
            h = 300
          }
        }
        console.log('[handlePreviewDragStart] Image dimensions - transform:', transformWidth, 'x', transformHeight,
          '| asset:', clipAsset?.width, 'x', clipAsset?.height,
          '| final:', w, 'x', h)
      } else {
        // For videos: try to get natural dimensions from the video element
        const videoEl = videoRefsMap.current.get(clipId)
        // Also check stored dimensions in clip transform
        const storedWidth = (clip.transform as { width?: number | null }).width
        const storedHeight = (clip.transform as { height?: number | null }).height

        if (videoEl && videoEl.videoWidth > 0) {
          w = videoEl.videoWidth
          h = videoEl.videoHeight
        } else if (storedWidth && storedHeight) {
          // Use stored dimensions from clip transform (set when clip was created)
          w = storedWidth
          h = storedHeight
        } else if (clipAsset?.width && clipAsset?.height) {
          // Fallback to asset metadata
          w = clipAsset.width
          h = clipAsset.height
        }
        console.log('[handlePreviewDragStart] Video dimensions - videoEl:', videoEl?.videoWidth, 'x', videoEl?.videoHeight,
          '| stored:', storedWidth, 'x', storedHeight,
          '| asset:', clipAsset?.width, 'x', clipAsset?.height,
          '| final:', w, 'x', h)
      }
    }
    console.log('[handlePreviewDragStart] Final dimensions: w=', w, 'h=', h, 'scale=', scale)

    // アンカー計算: scaleを考慮した画面上の位置
    const halfW = (w / 2) * scale
    const halfH = (h / 2) * scale

    let anchorX = cx
    let anchorY = cy

    // Calculate anchor based on handle type (in screen coordinates relative to center)
    if (type === 'resize-tl') {
      // Anchor at bottom-right corner
      anchorX = cx + halfW
      anchorY = cy + halfH
    } else if (type === 'resize-tr') {
      // Anchor at bottom-left corner
      anchorX = cx - halfW
      anchorY = cy + halfH
    } else if (type === 'resize-bl') {
      // Anchor at top-right corner
      anchorX = cx + halfW
      anchorY = cy - halfH
    } else if (type === 'resize-br') {
      // Anchor at top-left corner
      anchorX = cx - halfW
      anchorY = cy - halfH
    } else if (type === 'resize-t') {
      // Anchor at bottom edge center
      anchorY = cy + halfH
    } else if (type === 'resize-b') {
      // Anchor at top edge center
      anchorY = cy - halfH
    } else if (type === 'resize-l') {
      // Anchor at right edge center
      anchorX = cx + halfW
    } else if (type === 'resize-r') {
      // Anchor at left edge center
      anchorX = cx - halfW
    }

    setPreviewDrag({
      type,
      layerId,
      clipId,
      startX: e.clientX,
      startY: e.clientY,
      initialX: currentTransform.x,
      initialY: currentTransform.y,
      initialScale: currentTransform.scale,
      initialRotation: currentTransform.rotation || 0,
      initialShapeWidth: clip.shape?.width,
      initialShapeHeight: clip.shape?.height,
      initialVideoWidth: !clip.shape && !isImageClip ? w : undefined,
      initialVideoHeight: !clip.shape && !isImageClip ? h : undefined,
      initialImageWidth: isImageClip ? w : undefined,
      initialImageHeight: isImageClip ? h : undefined,
      isImageClip,
      anchorX,
      anchorY,
      // Crop initial values
      initialCrop: clip.crop || { top: 0, right: 0, bottom: 0, left: 0 },
      mediaWidth: w,
      mediaHeight: h,
    })

    // Initialize dragTransform with current values
    setDragTransform({
      x: currentTransform.x,
      y: currentTransform.y,
      scale: currentTransform.scale,
      shapeWidth: clip.shape?.width,
      shapeHeight: clip.shape?.height,
      imageWidth: isImageClip ? w : undefined,
      imageHeight: isImageClip ? h : undefined,
    })

    // Set cursor based on resize direction and element rotation
    document.body.classList.add('dragging-preview')

    // Get rotation-adjusted cursor
    const rotation = currentTransform.rotation || 0
    // Normalize rotation to 0-360
    const normalizedRotation = ((rotation % 360) + 360) % 360

    // Cursor types in 45-degree increments (starting from nwse-resize at 0°)
    const diagonalCursors = ['nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize']
    const edgeCursors = ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize']

    // Calculate cursor index based on rotation (each 45° shifts by one cursor type)
    const cursorIndex = Math.round(normalizedRotation / 45) % 4

    const getRotatedCursor = (handleType: string): string => {
      if (handleType === 'move') return 'grabbing'
      if (handleType === 'resize') return diagonalCursors[cursorIndex]

      // Map handle types to base cursor indices
      const handleBaseIndex: Record<string, number> = {
        'resize-tl': 0, // nwse at 0°
        'resize-br': 0, // nwse at 0°
        'resize-tr': 2, // nesw at 0°
        'resize-bl': 2, // nesw at 0°
        'resize-t': 0,  // ns at 0° (use edgeCursors)
        'resize-b': 0,  // ns at 0°
        'resize-l': 2,  // ew at 0°
        'resize-r': 2,  // ew at 0°
      }

      const isEdgeHandle = ['resize-t', 'resize-b', 'resize-l', 'resize-r'].includes(handleType)
      const baseIndex = handleBaseIndex[handleType] ?? 0
      const adjustedIndex = (baseIndex + cursorIndex) % 4

      return isEdgeHandle ? edgeCursors[adjustedIndex] : diagonalCursors[adjustedIndex]
    }

    document.body.dataset.dragCursor = getRotatedCursor(type)

    // Select this clip
    const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    // Determine asset name: use asset name, or shape type name, or fallback to 'Clip'
    let assetName = 'Clip'
    if (asset) {
      assetName = asset.name
    } else if (clip.shape) {
      const shapeNames: Record<string, string> = { rectangle: '四角形', circle: '円', line: '線' }
      assetName = shapeNames[clip.shape.type] || clip.shape.type
    } else if (clip.asset_id) {
      assetName = clip.asset_id.slice(0, 8)
    }
    setSelectedVideoClip({
      layerId,
      layerName: layer?.name || '',
      clipId,
      assetId: clip.asset_id,
      assetName,
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
    })
    setSelectedClip(null)
  }, [currentProject, currentTime, assets])

  // Anchor-based drag move - only updates local state, no network calls
  const handlePreviewDragMove = useCallback((e: MouseEvent) => {
    if (!previewDrag || !currentProject) return

    const rawDeltaX = e.clientX - previewDrag.startX
    const rawDeltaY = e.clientY - previewDrag.startY

    // Calculate preview scale - must match the rendering formula
    const containerHeight = effectivePreviewHeight
    const containerWidth = containerHeight * currentProject.width / currentProject.height
    const previewScale = Math.min(containerWidth / currentProject.width, containerHeight / currentProject.height)

    // Convert screen delta to logical pixels
    const rawLogicalDeltaX = rawDeltaX / previewScale
    const rawLogicalDeltaY = rawDeltaY / previewScale

    // Apply inverse rotation to delta for resize operations
    // This ensures dragging aligns with the element's local coordinate system
    const rotation = previewDrag.initialRotation || 0
    const radians = (-rotation * Math.PI) / 180 // Inverse rotation
    const cos = Math.cos(radians)
    const sin = Math.sin(radians)

    // For move operations, don't rotate the delta (move in screen space)
    // For resize operations, rotate the delta to element's local space
    const isResizeOp = previewDrag.type !== 'move'
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
    const initW = previewDrag.initialShapeWidth || previewDrag.initialImageWidth || 100
    const initH = previewDrag.initialShapeHeight || previewDrag.initialImageHeight || 100
    const scale = previewDrag.initialScale

    // Anchor position (fixed point that never moves) - stored in screen coords
    const anchorX = previewDrag.anchorX ?? previewDrag.initialX
    const anchorY = previewDrag.anchorY ?? previewDrag.initialY

    if (type === 'move') {
      // Simple move
      newX = previewDrag.initialX + logicalDeltaX
      newY = previewDrag.initialY + logicalDeltaY
    } else if (type === 'resize') {
      // Uniform scale for images/videos (center anchor)
      const scaleFactor = 1 + (rawDeltaX + rawDeltaY) / 200
      newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale * scaleFactor))
    } else if (type === 'resize-br') {
      // Bottom-right handle: anchor at top-left
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
        newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
        newX = anchorX + (newShapeWidth / 2) * scale
        newY = anchorY + (newShapeHeight / 2) * scale
      } else if (isImageClip) {
        // Image: independent width/height resize (like shapes)
        newImageWidth = Math.max(10, initW + logicalDeltaX)
        newImageHeight = Math.max(10, initH + logicalDeltaY)
        newX = anchorX + newImageWidth / 2
        newY = anchorY + newImageHeight / 2
      } else {
        // Video: uniform scale
        const w = previewDrag.initialVideoWidth || 100
        const h = previewDrag.initialVideoHeight || 100
        const deltaScaleX = logicalDeltaX / w
        const deltaScaleY = logicalDeltaY / h
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX + (w / 2) * newScale
        newY = anchorY + (h / 2) * newScale
      }
    } else if (type === 'resize-tl') {
      // Top-left handle: anchor at bottom-right
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
        newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
        newX = anchorX - (newShapeWidth / 2) * scale
        newY = anchorY - (newShapeHeight / 2) * scale
      } else if (isImageClip) {
        // Image: independent width/height resize (like shapes)
        newImageWidth = Math.max(10, initW - logicalDeltaX)
        newImageHeight = Math.max(10, initH - logicalDeltaY)
        newX = anchorX - newImageWidth / 2
        newY = anchorY - newImageHeight / 2
      } else {
        // Video: uniform scale
        const w = previewDrag.initialVideoWidth || 100
        const h = previewDrag.initialVideoHeight || 100
        const deltaScaleX = -logicalDeltaX / w
        const deltaScaleY = -logicalDeltaY / h
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX - (w / 2) * newScale
        newY = anchorY - (h / 2) * newScale
      }
    } else if (type === 'resize-tr') {
      // Top-right handle: anchor at bottom-left
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
        newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
        newX = anchorX + (newShapeWidth / 2) * scale
        newY = anchorY - (newShapeHeight / 2) * scale
      } else if (isImageClip) {
        // Image: independent width/height resize (like shapes)
        newImageWidth = Math.max(10, initW + logicalDeltaX)
        newImageHeight = Math.max(10, initH - logicalDeltaY)
        newX = anchorX + newImageWidth / 2
        newY = anchorY - newImageHeight / 2
      } else {
        // Video: uniform scale
        const w = previewDrag.initialVideoWidth || 100
        const h = previewDrag.initialVideoHeight || 100
        const deltaScaleX = logicalDeltaX / w
        const deltaScaleY = -logicalDeltaY / h
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX + (w / 2) * newScale
        newY = anchorY - (h / 2) * newScale
      }
    } else if (type === 'resize-bl') {
      // Bottom-left handle: anchor at top-right
      const isShapeClip = previewDrag.initialShapeWidth !== undefined
      if (isShapeClip) {
        newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
        newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
        newX = anchorX - (newShapeWidth / 2) * scale
        newY = anchorY + (newShapeHeight / 2) * scale
      } else if (isImageClip) {
        // Image: independent width/height resize (like shapes)
        newImageWidth = Math.max(10, initW - logicalDeltaX)
        newImageHeight = Math.max(10, initH + logicalDeltaY)
        newX = anchorX - newImageWidth / 2
        newY = anchorY + newImageHeight / 2
      } else {
        // Video: uniform scale
        const w = previewDrag.initialVideoWidth || 100
        const h = previewDrag.initialVideoHeight || 100
        const deltaScaleX = -logicalDeltaX / w
        const deltaScaleY = logicalDeltaY / h
        newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale + (deltaScaleX + deltaScaleY) / 2))
        newX = anchorX - (w / 2) * newScale
        newY = anchorY + (h / 2) * newScale
      }
    } else if (type === 'resize-r') {
      // Right edge: anchor at left edge center
      if (isImageClip) {
        newImageWidth = Math.max(10, initW + logicalDeltaX)
        newX = anchorX + newImageWidth / 2
      } else {
        newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
        newX = anchorX + (newShapeWidth / 2) * scale
      }
    } else if (type === 'resize-l') {
      // Left edge: anchor at right edge center
      if (isImageClip) {
        newImageWidth = Math.max(10, initW - logicalDeltaX)
        newX = anchorX - newImageWidth / 2
      } else {
        newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
        newX = anchorX - (newShapeWidth / 2) * scale
      }
    } else if (type === 'resize-b') {
      // Bottom edge: anchor at top edge center
      if (isImageClip) {
        newImageHeight = Math.max(10, initH + logicalDeltaY)
        newY = anchorY + newImageHeight / 2
      } else {
        newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
        newY = anchorY + (newShapeHeight / 2) * scale
      }
    } else if (type === 'resize-t') {
      // Top edge: anchor at bottom edge center
      if (isImageClip) {
        newImageHeight = Math.max(10, initH - logicalDeltaY)
        newY = anchorY - newImageHeight / 2
      } else {
        newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
        newY = anchorY - (newShapeHeight / 2) * scale
      }
    } else if (type.startsWith('crop-')) {
      // Crop handling - update dragCrop state
      const initialCrop = previewDrag.initialCrop || { top: 0, right: 0, bottom: 0, left: 0 }
      const mediaW = previewDrag.mediaWidth || 100
      const mediaH = previewDrag.mediaHeight || 100
      // Calculate crop delta as percentage of media dimension
      const cropDeltaX = logicalDeltaX / (mediaW * scale)
      const cropDeltaY = logicalDeltaY / (mediaH * scale)

      const newCrop = { ...initialCrop }
      if (type === 'crop-t') {
        newCrop.top = Math.max(0, Math.min(1 - newCrop.bottom - 0.1, initialCrop.top + cropDeltaY))
      } else if (type === 'crop-b') {
        newCrop.bottom = Math.max(0, Math.min(1 - newCrop.top - 0.1, initialCrop.bottom - cropDeltaY))
      } else if (type === 'crop-l') {
        newCrop.left = Math.max(0, Math.min(1 - newCrop.right - 0.1, initialCrop.left + cropDeltaX))
      } else if (type === 'crop-r') {
        newCrop.right = Math.max(0, Math.min(1 - newCrop.left - 0.1, initialCrop.right - cropDeltaX))
      }
      setDragCrop(newCrop)
      return // Don't update dragTransform for crop operations
    }

    // Update local drag transform only (no network call)
    // Round position to integers for pixel-perfect placement
    setDragTransform({
      x: Math.round(newX),
      y: Math.round(newY),
      scale: newScale,
      shapeWidth: newShapeWidth,
      shapeHeight: newShapeHeight,
      imageWidth: newImageWidth,
      imageHeight: newImageHeight,
    })
  }, [previewDrag, currentProject, effectivePreviewHeight])

  // Save changes on drag end
  const handlePreviewDragEnd = useCallback(() => {
    // Handle crop drag end
    if (previewDrag && dragCrop && currentProject && projectId) {
      const updatedLayers = currentProject.timeline_data.layers.map(l => {
        if (l.id !== previewDrag.layerId) return l
        return {
          ...l,
          clips: l.clips.map(c => {
            if (c.id !== previewDrag.clipId) return c
            return {
              ...c,
              crop: dragCrop,
            }
          }),
        }
      })
      updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

      // Update selectedVideoClip with new crop
      if (selectedVideoClip) {
        setSelectedVideoClip({
          ...selectedVideoClip,
          crop: dragCrop,
        })
      }

      setPreviewDrag(null)
      setDragCrop(null)
      document.body.classList.remove('dragging-preview')
      delete document.body.dataset.dragCursor
      return
    }

    if (previewDrag && dragTransform && currentProject && projectId) {
      // Save the final transform to backend
      const updatedLayers = currentProject.timeline_data.layers.map(l => {
        if (l.id !== previewDrag.layerId) return l
        return {
          ...l,
          clips: l.clips.map(c => {
            if (c.id !== previewDrag.clipId) return c

            // Update shape dimensions if applicable
            const updatedShape = c.shape && (dragTransform.shapeWidth || dragTransform.shapeHeight) ? {
              ...c.shape,
              width: dragTransform.shapeWidth ?? c.shape.width,
              height: dragTransform.shapeHeight ?? c.shape.height,
            } : c.shape

            // If a keyframe is selected, update the keyframe's transform instead of the base transform
            if (selectedKeyframeIndex !== null && c.keyframes && c.keyframes[selectedKeyframeIndex]) {
              const updatedKeyframes = c.keyframes.map((kf, idx) => {
                if (idx !== selectedKeyframeIndex) return kf
                return {
                  ...kf,
                  transform: {
                    x: dragTransform.x,
                    y: dragTransform.y,
                    scale: dragTransform.scale,
                    rotation: kf.transform.rotation,
                  },
                }
              })

              return {
                ...c,
                keyframes: updatedKeyframes,
                shape: updatedShape,
              }
            }

            // Build updated transform - include width/height for images
            // Use null (not undefined) for type compatibility with Clip type
            const existingWidth = (c.transform as { width?: number | null }).width
            const existingHeight = (c.transform as { height?: number | null }).height
            const updatedTransform = {
              ...c.transform,
              x: dragTransform.x,
              y: dragTransform.y,
              scale: dragTransform.scale,
              width: dragTransform.imageWidth !== undefined ? dragTransform.imageWidth : (existingWidth ?? null),
              height: dragTransform.imageHeight !== undefined ? dragTransform.imageHeight : (existingHeight ?? null),
            }

            // When keyframes exist, also update the keyframe at current time
            let updatedKeyframes = c.keyframes
            if (c.keyframes && c.keyframes.length > 0) {
              const timeInClipMs = currentTime - c.start_ms
              if (timeInClipMs >= 0 && timeInClipMs <= c.duration_ms) {
                // Preserve current interpolated opacity when updating keyframe via drag
                const currentInterpolated = getInterpolatedTransform(c, timeInClipMs)
                const newKfTransform = {
                  x: dragTransform.x,
                  y: dragTransform.y,
                  scale: dragTransform.scale,
                  rotation: updatedTransform.rotation,
                }
                updatedKeyframes = addKeyframe(c, timeInClipMs, newKfTransform, currentInterpolated.opacity)
              }
            }

            return {
              ...c,
              transform: updatedTransform,
              shape: updatedShape,
              keyframes: updatedKeyframes,
            }
          }),
        }
      })

      updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })

      // Update selected clip keyframes state
      if (selectedVideoClip) {
        const layer = updatedLayers.find(l => l.id === previewDrag.layerId)
        const clip = layer?.clips.find(c => c.id === previewDrag.clipId)
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
    document.body.classList.remove('dragging-preview')
    delete document.body.dataset.dragCursor
  }, [previewDrag, dragTransform, dragCrop, currentProject, projectId, currentTime, selectedVideoClip, updateTimeline, selectedKeyframeIndex])

  // Global mouse listeners for preview drag
  useEffect(() => {
    if (previewDrag) {
      window.addEventListener('mousemove', handlePreviewDragMove)
      window.addEventListener('mouseup', handlePreviewDragEnd)
      return () => {
        window.removeEventListener('mousemove', handlePreviewDragMove)
        window.removeEventListener('mouseup', handlePreviewDragEnd)
      }
    }
  }, [previewDrag, handlePreviewDragMove, handlePreviewDragEnd])

  // Sync all video frames with timeline when not playing
  // Compute clip position directly for better accuracy
  useEffect(() => {
    if (isPlaying || !currentProject) return

    // Build a map of clipId -> clip data for quick lookup
    const clipMap = new Map<string, { start_ms: number; in_point_ms: number; duration_ms: number; asset_id: string | null; speed?: number }>()
    const layers = currentProject.timeline_data.layers
    for (const layer of layers) {
      if (layer.visible === false) continue
      for (const clip of layer.clips) {
        clipMap.set(clip.id, clip)
      }
    }

    // Sync each video element to its clip's current frame
    videoRefsMap.current.forEach((video, clipId) => {
      const clip = clipMap.get(clipId)
      if (!clip) return

      // Check if current time is within this clip's range (exclusive end to prevent overlap)
      if (currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
        // Video time = in_point + (timeline elapsed) * speed
        const speed = clip.speed || 1
        const videoTimeMs = clip.in_point_ms + (currentTime - clip.start_ms) * speed
        const targetTime = videoTimeMs / 1000

        // Only seek if difference is significant (avoid micro-seeks)
        if (Math.abs(video.currentTime - targetTime) > 0.05) {
          video.currentTime = targetTime
        }
      }
    })
  }, [currentTime, isPlaying, currentProject])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (playbackTimerRef.current) {
        cancelAnimationFrame(playbackTimerRef.current)
      }
      audioRefs.current.forEach(audio => {
        audio.pause()
        audio.src = ''
      })
      audioRefs.current.clear()
      audioClipTimingRefs.current.clear()
    }
  }, [])

  // Measure actual preview area height (excludes playback controls + padding)
  useEffect(() => {
    const el = previewAreaRef.current
    if (!el) return
    const obs = new ResizeObserver(entries => {
      const h = entries[0]?.contentRect.height ?? 0
      if (h > 0) setPreviewAreaHeight(h)
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // Left panel resize handlers
  const handleLeftPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingLeftPanel(true)
    leftPanelResizeStartX.current = e.clientX
    leftPanelResizeStartWidth.current = leftPanelWidth
  }, [leftPanelWidth])

  useEffect(() => {
    if (!isResizingLeftPanel) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - leftPanelResizeStartX.current
      // Min: 200px, Max: 500px
      const newWidth = Math.max(200, Math.min(500, leftPanelResizeStartWidth.current + deltaX))
      setLeftPanelWidth(newWidth)
    }

    const handleMouseUp = () => {
      setIsResizingLeftPanel(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizingLeftPanel])

  // Right panel resize handlers
  const handleRightPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingRightPanel(true)
    rightPanelResizeStartX.current = e.clientX
    rightPanelResizeStartWidth.current = rightPanelWidth
  }, [rightPanelWidth])

  useEffect(() => {
    if (!isResizingRightPanel) return

    const handleMouseMove = (e: MouseEvent) => {
      // Right panel: dragging left increases width, dragging right decreases
      const deltaX = rightPanelResizeStartX.current - e.clientX
      // Min: 200px, Max: 500px
      const newWidth = Math.max(200, Math.min(500, rightPanelResizeStartWidth.current + deltaX))
      setRightPanelWidth(newWidth)
    }

    const handleMouseUp = () => {
      setIsResizingRightPanel(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizingRightPanel])

  // Chroma preview frame resize handlers
  const handleChromaPreviewResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingChromaPreview(true)
    chromaPreviewResizeStartY.current = e.clientY
    chromaPreviewResizeStartSize.current = chromaPreviewSize
  }, [chromaPreviewSize])

  useEffect(() => {
    if (!isResizingChromaPreview) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaY = e.clientY - chromaPreviewResizeStartY.current
      // Min: 40px, Max: 200px per frame
      const newSize = Math.max(40, Math.min(200, chromaPreviewResizeStartSize.current + deltaY))
      setChromaPreviewSize(newSize)
    }

    const handleMouseUp = () => {
      setIsResizingChromaPreview(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizingChromaPreview])

  // AI Panel resize handlers
  const handleAiPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingAiPanel(true)
    aiPanelResizeStartX.current = e.clientX
    aiPanelResizeStartWidth.current = aiPanelWidth
  }, [aiPanelWidth])

  useEffect(() => {
    if (!isResizingAiPanel) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = aiPanelResizeStartX.current - e.clientX
      const newWidth = Math.max(200, Math.min(500, aiPanelResizeStartWidth.current + deltaX))
      setAiPanelWidth(newWidth)
    }

    const handleMouseUp = () => {
      setIsResizingAiPanel(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizingAiPanel])

  // Activity Panel resize handlers
  const handleActivityPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizingActivityPanel(true)
    activityPanelResizeStartX.current = e.clientX
    activityPanelResizeStartWidth.current = activityPanelWidth
  }, [activityPanelWidth])

  useEffect(() => {
    if (!isResizingActivityPanel) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = activityPanelResizeStartX.current - e.clientX
      const newWidth = Math.max(200, Math.min(500, activityPanelResizeStartWidth.current + deltaX))
      setActivityPanelWidth(newWidth)
    }

    const handleMouseUp = () => {
      setIsResizingActivityPanel(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizingActivityPanel])

  // Preview resize handlers
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizing(true)
    resizeStartY.current = e.clientY
    resizeStartHeight.current = previewHeight
  }, [previewHeight])

  useEffect(() => {
    if (!isResizing) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaY = e.clientY - resizeStartY.current
      // Max height: 90% of viewport height to always leave room for timeline
      const maxHeight = Math.floor(window.innerHeight * 0.9)
      const newHeight = Math.max(150, Math.min(maxHeight, resizeStartHeight.current + deltaY))
      setPreviewHeight(newHeight)
    }

    const handleMouseUp = () => {
      setIsResizing(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizing])

  // Preview zoom handlers
  const handlePreviewZoomIn = useCallback(() => {
    setPreviewZoom(prev => {
      const target = prev * 1.25
      // Snap to 100% if crossing it while zooming in
      if (prev < 1 && target > 1) return 1
      return Math.min(4, target)
    })
  }, [])

  const handlePreviewZoomOut = useCallback(() => {
    setPreviewZoom(prev => {
      const target = prev * 0.8
      // Snap to 100% if crossing it while zooming out
      if (prev > 1 && target < 1) return 1
      return Math.max(0.25, target)
    })
  }, [])

  const handlePreviewZoomFit = useCallback(() => {
    setPreviewZoom(1)
    setPreviewPan({ x: 0, y: 0 })
  }, [])

  // Preview wheel zoom handler (Ctrl+scroll or Cmd+scroll)
  const handlePreviewWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    if (!e.ctrlKey && !e.metaKey) return
    e.preventDefault()

    const delta = -e.deltaY
    const zoomFactor = delta > 0 ? 1.1 : 0.9

    setPreviewZoom(prev => {
      const newZoom = prev * zoomFactor
      // Snap to 100% if crossing it
      if ((prev < 1 && newZoom > 1) || (prev > 1 && newZoom < 1)) return 1
      return Math.max(0.25, Math.min(4, newZoom))
    })
  }, [])

  // Preview pan handlers (middle mouse button or space+drag)
  const handlePreviewPanStart = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    // Only start panning on middle mouse button (button 1) or when space is held
    if (e.button !== 1 && !e.altKey) return
    e.preventDefault()
    setIsPanningPreview(true)
    panStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: previewPan.x,
      panY: previewPan.y,
    }
  }, [previewPan])

  useEffect(() => {
    if (!isPanningPreview) return

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - panStartRef.current.x
      const deltaY = e.clientY - panStartRef.current.y
      setPreviewPan({
        x: panStartRef.current.panX + deltaX,
        y: panStartRef.current.panY + deltaY,
      })
    }

    const handleMouseUp = () => {
      setIsPanningPreview(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isPanningPreview])

  // Reset pan when zoom returns to 1 (fit)
  useEffect(() => {
    if (previewZoom <= 1) {
      setPreviewPan({ x: 0, y: 0 })
    }
  }, [previewZoom])

  // Keyboard shortcuts for undo/redo
  useEffect(() => {
    const handleKeyDown = async (e: KeyboardEvent) => {
      // Ignore if typing in an input field
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
        if (e.shiftKey) {
          // Redo: Ctrl/Cmd + Shift + Z
          e.preventDefault()
          if (projectId && canRedo() && !isUndoRedoInProgress) {
            setIsUndoRedoInProgress(true)
            await redo(projectId)
            setTimeout(() => setIsUndoRedoInProgress(false), 150)
          }
        } else {
          // Undo: Ctrl/Cmd + Z
          e.preventDefault()
          if (projectId && canUndo() && !isUndoRedoInProgress) {
            setIsUndoRedoInProgress(true)
            await undo(projectId)
            setTimeout(() => setIsUndoRedoInProgress(false), 150)
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'y') {
        // Redo: Ctrl/Cmd + Y (alternative)
        e.preventDefault()
        if (projectId && canRedo() && !isUndoRedoInProgress) {
          setIsUndoRedoInProgress(true)
          await redo(projectId)
          setTimeout(() => setIsUndoRedoInProgress(false), 150)
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        // Save session: Ctrl/Cmd + S
        e.preventDefault()
        if (currentSessionId && currentSessionName && !savingSession) {
          // Overwrite save existing session
          handleSaveSessionFromPanel(currentSessionId, currentSessionName)
        } else if (!currentSessionId) {
          // No session loaded - show toast hint
          setToastMessage({ text: 'セクションタブから「名前をつけて保存」してください', type: 'info' })
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'c') {
        // Copy clip: Ctrl/Cmd + C
        e.preventDefault()
        if (selectedVideoClip && currentProject) {
          // Copy video clip
          const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
          const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
          if (clip) {
            setCopiedClip({
              type: 'video',
              layerId: selectedVideoClip.layerId,
              clip: JSON.parse(JSON.stringify(clip)) // Deep copy
            })
            setToastMessage({ text: 'クリップをコピーしました', type: 'success' })
          }
        } else if (selectedClip && currentProject) {
          // Copy audio clip
          const track = currentProject.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
          const clip = track?.clips.find(c => c.id === selectedClip.clipId)
          if (clip) {
            setCopiedClip({
              type: 'audio',
              trackId: selectedClip.trackId,
              clip: JSON.parse(JSON.stringify(clip)) // Deep copy
            })
            setToastMessage({ text: 'オーディオクリップをコピーしました', type: 'success' })
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'v') {
        // Paste clip: Ctrl/Cmd + V
        e.preventDefault()
        if (!copiedClip || !currentProject || !projectId) return

        if (copiedClip.type === 'video') {
          // Paste video clip at current playhead position
          const copiedVideoClip = copiedClip.clip as Clip
          const newClip: Clip = {
            ...copiedVideoClip,
            id: uuidv4(),
            start_ms: currentTime,
            group_id: null, // Don't copy group association
          }

          // Find the target layer (same layer or first available)
          let targetLayerId = copiedClip.layerId
          const targetLayer = currentProject.timeline_data.layers.find(l => l.id === targetLayerId)
          if (!targetLayer) {
            // If original layer doesn't exist, use first layer
            targetLayerId = currentProject.timeline_data.layers[0]?.id
          }

          if (targetLayerId) {
            const updatedLayers = currentProject.timeline_data.layers.map(layer => {
              if (layer.id === targetLayerId) {
                return { ...layer, clips: [...layer.clips, newClip] }
              }
              return layer
            })

            updateTimeline(projectId, {
              ...currentProject.timeline_data,
              layers: updatedLayers
            })
            setToastMessage({ text: 'クリップをペーストしました', type: 'success' })
          }
        } else if (copiedClip.type === 'audio') {
          // Paste audio clip at current playhead position
          const copiedAudioClip = copiedClip.clip as AudioClipType
          const newClip: AudioClipType = {
            ...copiedAudioClip,
            id: uuidv4(),
            start_ms: currentTime,
            group_id: null, // Don't copy group association
          }

          // Find the target track (same track or first available of same type)
          let targetTrackId = copiedClip.trackId
          const originalTrack = currentProject.timeline_data.audio_tracks.find(t => t.id === targetTrackId)
          if (!originalTrack) {
            // If original track doesn't exist, use first track
            targetTrackId = currentProject.timeline_data.audio_tracks[0]?.id
          }

          if (targetTrackId) {
            const updatedTracks = currentProject.timeline_data.audio_tracks.map(track => {
              if (track.id === targetTrackId) {
                return { ...track, clips: [...track.clips, newClip] }
              }
              return track
            })

            updateTimeline(projectId, {
              ...currentProject.timeline_data,
              audio_tracks: updatedTracks
            })
            setToastMessage({ text: 'オーディオクリップをペーストしました', type: 'success' })
          }
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [projectId, undo, redo, canUndo, canRedo, isUndoRedoInProgress, currentSessionId, currentSessionName, savingSession, handleSaveSessionFromPanel, selectedVideoClip, selectedClip, currentProject, copiedClip, currentTime, updateTimeline])

  // Delete key handler for canvas-selected clips
  useEffect(() => {
    const handleDeleteKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return

      // Ignore if typing in an input field
      const activeEl = document.activeElement
      const isInputFocused = activeEl instanceof HTMLInputElement ||
                            activeEl instanceof HTMLTextAreaElement ||
                            activeEl?.getAttribute('contenteditable') === 'true'
      if (isInputFocused) return

      if (!selectedVideoClip || !currentProject || !projectId) return

      e.preventDefault()

      // Find the video clip to get its group_id
      const layer = currentProject.timeline_data.layers.find(l => l.id === selectedVideoClip.layerId)
      const videoClip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
      const groupId = videoClip?.group_id

      // Remove the video clip from layers
      const updatedLayers = currentProject.timeline_data.layers.map((l) =>
        l.id === selectedVideoClip.layerId
          ? { ...l, clips: l.clips.filter((c) => c.id !== selectedVideoClip.clipId) }
          : l
      )

      // Also remove audio clips in the same group
      const updatedTracks = currentProject.timeline_data.audio_tracks.map((track) => ({
        ...track,
        clips: track.clips.filter((c) => {
          if (groupId && c.group_id === groupId) return false
          return true
        })
      }))

      updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers, audio_tracks: updatedTracks })
      setSelectedVideoClip(null)
    }

    window.addEventListener('keydown', handleDeleteKey)
    return () => window.removeEventListener('keydown', handleDeleteKey)
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500"></div>
      </div>
    )
  }

  if (error || !currentProject) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-500 mb-4">{error || 'プロジェクトが見つかりません'}</p>
          <button
            onClick={() => navigate('/')}
            className="text-primary-500 hover:text-primary-400"
          >
            ダッシュボードに戻る
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`h-screen bg-gray-900 flex flex-col overflow-hidden ${(isResizingLeftPanel || isResizingRightPanel || isResizingAiPanel || isResizingActivityPanel) ? 'cursor-ew-resize select-none' : ''} ${isResizingChromaPreview ? 'cursor-ns-resize select-none' : ''}`}>
      {/* Header */}
      <header className="h-14 bg-gray-800 border-b border-gray-700 flex items-center px-4 flex-shrink-0 sticky top-0 z-50">
        <button
          onClick={() => setShowExitConfirm(true)}
          className="text-gray-500 hover:text-gray-300 mr-3 opacity-60 hover:opacity-100 transition-opacity"
          title="プロジェクトリストに戻る"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-white font-medium flex items-center">
          {currentProject.name}
          {currentSessionName && (
            <>
              <span className="mx-2 text-gray-500">/</span>
              <span className="text-primary-400">{currentSessionName}</span>
            </>
          )}
          {!currentSessionName && (
            <>
              <span className="mx-2 text-gray-500">/</span>
              <span className="text-gray-500 italic">未保存</span>
            </>
          )}
        </h1>
        {/* Project settings button */}
        <button
          onClick={() => setShowSettingsModal(true)}
          className="ml-2 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded flex items-center gap-1"
          title="プロジェクト設定"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          {currentProject.width}×{currentProject.height}
        </button>
        {/* Keyboard shortcuts button */}
        <button
          onClick={() => setShowShortcutsModal(true)}
          className="ml-2 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded flex items-center gap-1"
          title="キーボードショートカット"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
          </svg>
        </button>
        {/* Undo/Redo buttons */}
        <div className="flex items-center gap-1 ml-4">
          <button
            onClick={async () => {
              if (!projectId || isUndoRedoInProgress) return
              setIsUndoRedoInProgress(true)
              await undo(projectId)
              setTimeout(() => setIsUndoRedoInProgress(false), 150)
            }}
            disabled={!canUndo() || isUndoRedoInProgress}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title={undoTooltip}
          >
            {isUndoRedoInProgress ? (
              <div className="w-5 h-5 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
              </svg>
            )}
          </button>
          <button
            onClick={async () => {
              if (!projectId || isUndoRedoInProgress) return
              setIsUndoRedoInProgress(true)
              await redo(projectId)
              setTimeout(() => setIsUndoRedoInProgress(false), 150)
            }}
            disabled={!canRedo() || isUndoRedoInProgress}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title={redoTooltip}
          >
            {isUndoRedoInProgress ? (
              <div className="w-5 h-5 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 10h-10a8 8 0 00-8 8v2M21 10l-6 6m6-6l-6-6" />
              </svg>
            )}
          </button>
        </div>
        {/* Sync toggle */}
        <button
          onClick={() => setIsSyncEnabled(prev => !prev)}
          className={`ml-4 px-2 py-1 text-xs rounded transition-colors flex items-center gap-1.5 ${
            isSyncEnabled
              ? 'bg-green-600/20 text-green-400 hover:bg-green-600/30'
              : 'bg-gray-700 text-gray-500 hover:bg-gray-600'
          }`}
          title={isSyncEnabled ? 'Sync有効（クリックで無効化）' : 'Sync無効（クリックで有効化）'}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Sync
        </button>
        <div className="ml-auto flex items-center gap-4">
          <span className="text-gray-400 text-sm">
            {Math.floor(currentProject.duration_ms / 60000)}:
            {Math.floor((currentProject.duration_ms % 60000) / 1000).toString().padStart(2, '0')}
          </span>
          <button
            onClick={() => setIsAIChatOpen(prev => !prev)}
            className={`px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-2 ${
              isAIChatOpen
                ? 'bg-primary-600 text-white'
                : 'bg-gray-600 hover:bg-gray-500 text-white'
            }`}
            title="AI アシスタント"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
            </svg>
            AI
          </button>
          <button
            onClick={() => {
              setSaveCurrentSessionBeforeNew(true)  // Default to saving current session
              setNewSessionName('')
              setShowNewSessionConfirm(true)
            }}
            className="px-3 py-1.5 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors flex items-center gap-2"
            title="新規セクション"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            新規
          </button>
          <button
            onClick={() => {
              setSessionNameInput(lastSavedSessionName || '名称なし')
              setShowSaveSessionModal(true)
            }}
            className="px-3 py-1.5 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors flex items-center gap-2"
            title="セクションを保存"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
            </svg>
            保存
          </button>
          <button
            onClick={() => setShowHistoryModal(true)}
            className="px-3 py-1.5 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors flex items-center gap-2"
            title="エクスポート履歴"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            履歴
          </button>
          <button
            onClick={() => {
              loadRenderHistory()
              setShowRenderModal(true)
            }}
            disabled={renderJob?.status === 'queued' || renderJob?.status === 'processing'}
            className="px-4 py-1.5 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            動画エクスポート
          </button>
        </div>
      </header>

      {/* Export Dialog */}
      <ExportDialog
        isOpen={showRenderModal}
        onClose={() => {
          setShowRenderModal(false)
          setRenderJob(null)
        }}
        onStartExport={handleStartRender}
        onCancelExport={handleCancelRender}
        onDownload={handleDownloadVideo}
        renderJob={renderJob}
        totalDurationMs={currentProject?.duration_ms || 0}
      />

      {/* Save Session Modal */}
      {showSaveSessionModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">セクションを保存</h3>
              <button
                onClick={() => {
                  setShowSaveSessionModal(false)
                  setSessionNameInput('')
                }}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">セクション名</label>
              <input
                type="text"
                value={sessionNameInput}
                onChange={(e) => setSessionNameInput(e.target.value)}
                onKeyDown={(e) => {
                  // IME変換中はEnterを無視
                  if (e.nativeEvent.isComposing || e.key === 'Process') return
                  if (e.key === 'Enter' && !savingSession && sessionNameInput.trim()) {
                    handleSaveSession()
                  }
                }}
                placeholder="例: intro_v1"
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-primary-500"
                autoFocus
                disabled={savingSession}
              />
              <p className="mt-2 text-xs text-gray-500">
                同名のセクションが存在する場合は自動的に連番が付加されます
              </p>
            </div>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowSaveSessionModal(false)
                  setSessionNameInput('')
                }}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
                disabled={savingSession}
              >
                キャンセル
              </button>
              <button
                onClick={handleSaveSession}
                disabled={savingSession || !sessionNameInput.trim()}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {savingSession ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>
                    保存中...
                  </>
                ) : (
                  '保存'
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Open Session Confirmation Modal */}
      {showOpenSessionConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">セクションを開く</h3>
              <button
                onClick={() => {
                  setShowOpenSessionConfirm(false)
                  setPendingSessionData(null)
                }}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <p className="text-gray-300 mb-4">
              現在の編集内容を保存しますか？
            </p>
            <p className="text-gray-500 text-sm mb-4">
              「いいえ」を選ぶと、保存されていない変更は失われます。
            </p>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowOpenSessionConfirm(false)
                  setPendingSessionData(null)
                }}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={() => handleConfirmOpenSession(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                いいえ
              </button>
              <button
                onClick={() => handleConfirmOpenSession(true)}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors"
              >
                はい
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Session Confirmation Modal */}
      {showNewSessionConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">新規セクション</h3>
              <button
                onClick={() => {
                  setShowNewSessionConfirm(false)
                  setSaveCurrentSessionBeforeNew(false)
                  setNewSessionName('')
                }}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Save current session option */}
            <label className="flex items-center gap-2 mb-4 cursor-pointer">
              <input
                type="checkbox"
                checked={saveCurrentSessionBeforeNew}
                onChange={(e) => setSaveCurrentSessionBeforeNew(e.target.checked)}
                className="w-4 h-4 rounded border-gray-500 bg-gray-700 text-primary-600 focus:ring-primary-500 focus:ring-offset-gray-800"
              />
              <span className="text-gray-300 text-sm">現在のセクションを保存してから作成</span>
            </label>

            {/* New session name input */}
            <div className="mb-4">
              <label className="block text-gray-400 text-sm mb-1">新規セクション名</label>
              <input
                type="text"
                value={newSessionName}
                onChange={(e) => setNewSessionName(e.target.value)}
                placeholder={`セクション_${new Date().toLocaleString('ja-JP', {
                  year: 'numeric', month: '2-digit', day: '2-digit',
                  hour: '2-digit', minute: '2-digit'
                }).replace(/[\/\s:]/g, '')}`}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm placeholder-gray-500 focus:outline-none focus:border-primary-500"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleNewSession()
                  }
                }}
              />
            </div>

            <p className="text-gray-500 text-sm mb-4">
              タイムラインの内容がクリアされます。
            </p>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowNewSessionConfirm(false)
                  setSaveCurrentSessionBeforeNew(false)
                  setNewSessionName('')
                }}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={handleNewSession}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors"
              >
                作成
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Exit Confirmation Modal */}
      {showExitConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">プロジェクトリストに戻る</h3>
              <button
                onClick={() => setShowExitConfirm(false)}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <p className="text-gray-300 text-sm mb-2">
              エディタを離れてプロジェクトリストに戻りますか？
            </p>
            <p className="text-gray-500 text-sm mb-4">
              未保存の変更がある場合は、保存してから戻ることをお勧めします。
            </p>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowExitConfirm(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={() => {
                  setShowExitConfirm(false)
                  navigate('/')
                }}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm rounded transition-colors"
              >
                戻る
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Asset Selection Dialog */}
      {showAssetSelectDialog && pendingSelections.length > 0 && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-[500px] max-w-[90vw] max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">アセットの選択</h3>
              <button
                onClick={handleCancelAssetSelection}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto space-y-4">
              {pendingSelections.map((selection) => (
                <div key={selection.refId} className="bg-gray-700 rounded-lg p-3">
                  <p className="text-white text-sm mb-2">
                    <span className="font-medium">「{selection.refName}」</span>
                    <span className="text-gray-400 text-xs ml-2">
                      ({selection.matchType === 'fingerprint' ? 'フィンガープリント一致' : 'サイズ一致'})
                    </span>
                  </p>
                  <p className="text-gray-400 text-xs mb-3">
                    複数の候補があります。使用するアセットを選択してください。
                  </p>
                  <div className="space-y-2">
                    {selection.candidates.map((candidate) => (
                      <label
                        key={candidate.id}
                        className={`flex items-center gap-3 p-2 rounded cursor-pointer transition-colors ${
                          userSelections.get(selection.refId) === candidate.id
                            ? 'bg-primary-900/50 ring-1 ring-primary-500'
                            : 'hover:bg-gray-600'
                        }`}
                      >
                        <input
                          type="radio"
                          name={`asset-${selection.refId}`}
                          checked={userSelections.get(selection.refId) === candidate.id}
                          onChange={() => {
                            const newSelections = new Map(userSelections)
                            newSelections.set(selection.refId, candidate.id)
                            setUserSelections(newSelections)
                          }}
                          className="sr-only"
                        />
                        <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                          userSelections.get(selection.refId) === candidate.id
                            ? 'border-primary-500 bg-primary-500'
                            : 'border-gray-500'
                        }`}>
                          {userSelections.get(selection.refId) === candidate.id && (
                            <div className="w-2 h-2 rounded-full bg-white"></div>
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-white text-sm truncate">{candidate.name}</p>
                          <p className="text-gray-400 text-xs">
                            {candidate.file_size ? `${(candidate.file_size / 1024 / 1024).toFixed(1)} MB` : ''}
                            {candidate.duration_ms ? ` • ${Math.floor(candidate.duration_ms / 1000)}秒` : ''}
                          </p>
                        </div>
                      </label>
                    ))}
                    {/* Skip option */}
                    <label
                      className={`flex items-center gap-3 p-2 rounded cursor-pointer transition-colors ${
                        userSelections.get(selection.refId) === 'skip'
                          ? 'bg-orange-900/30 ring-1 ring-orange-500'
                          : 'hover:bg-gray-600'
                      }`}
                    >
                      <input
                        type="radio"
                        name={`asset-${selection.refId}`}
                        checked={userSelections.get(selection.refId) === 'skip'}
                        onChange={() => {
                          const newSelections = new Map(userSelections)
                          newSelections.set(selection.refId, 'skip')
                          setUserSelections(newSelections)
                        }}
                        className="sr-only"
                      />
                      <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                        userSelections.get(selection.refId) === 'skip'
                          ? 'border-orange-500 bg-orange-500'
                          : 'border-gray-500'
                      }`}>
                        {userSelections.get(selection.refId) === 'skip' && (
                          <div className="w-2 h-2 rounded-full bg-white"></div>
                        )}
                      </div>
                      <div className="flex-1">
                        <p className="text-orange-400 text-sm">スキップ（マッピングしない）</p>
                      </div>
                    </label>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex gap-2 justify-end mt-4 pt-4 border-t border-gray-700">
              <button
                onClick={handleCancelAssetSelection}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={() => handleAssetSelectionComplete(userSelections)}
                disabled={pendingSelections.some(s => !userSelections.has(s.refId))}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                適用
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Project Settings Modal */}
      {showSettingsModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">プロジェクト設定</h3>
              <button
                onClick={() => setShowSettingsModal(false)}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Preset buttons */}
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">プリセット</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: '1920×1080', w: 1920, h: 1080, desc: 'Full HD 横' },
                  { label: '1280×720', w: 1280, h: 720, desc: 'HD 横' },
                  { label: '1080×1920', w: 1080, h: 1920, desc: 'Full HD 縦' },
                  { label: '1080×1080', w: 1080, h: 1080, desc: '正方形' },
                ].map((preset) => (
                  <button
                    key={preset.label}
                    onClick={() => {
                      handleUpdateProjectDimensions(preset.w, preset.h)
                      setShowSettingsModal(false)
                    }}
                    className={`px-3 py-2 text-sm rounded text-left transition-colors ${
                      currentProject.width === preset.w && currentProject.height === preset.h
                        ? 'bg-primary-600 text-white'
                        : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                    }`}
                  >
                    <div className="font-medium">{preset.label}</div>
                    <div className="text-xs opacity-70">{preset.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Custom dimensions */}
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">カスタムサイズ</label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min="256"
                  max="4096"
                  step="2"
                  defaultValue={currentProject.width}
                  onBlur={(e) => {
                    const newWidth = parseInt(e.target.value) || 1920
                    handleUpdateProjectDimensions(newWidth, currentProject.height)
                  }}
                  className="w-24 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                  placeholder="幅"
                />
                <span className="text-gray-400">×</span>
                <input
                  type="number"
                  min="256"
                  max="4096"
                  step="2"
                  defaultValue={currentProject.height}
                  onBlur={(e) => {
                    const newHeight = parseInt(e.target.value) || 1080
                    handleUpdateProjectDimensions(currentProject.width, newHeight)
                  }}
                  className="w-24 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                  placeholder="高さ"
                />
                <span className="text-gray-400 text-xs">px</span>
              </div>
              <p className="text-xs text-gray-500 mt-1">256〜4096px、偶数のみ</p>
            </div>

            {/* AI Assistant Settings */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <label className="block text-sm text-gray-400 mb-2">AIアシスタント設定</label>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs text-gray-500 w-20">プロバイダー:</span>
                <select
                  value={currentProject.ai_provider || ''}
                  onChange={(e) => {
                    const value = e.target.value as 'openai' | 'gemini' | 'anthropic' | ''
                    handleUpdateAIProvider(value || null)
                  }}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                >
                  <option value="">未選択</option>
                  <option value="openai">OpenAI (GPT-4o)</option>
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic Claude</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-20">APIキー:</span>
                {currentProject.ai_api_key ? (
                  <span className="flex-1 px-2 py-1 bg-gray-700 text-green-400 text-sm rounded border border-green-600">
                    ✓ 設定済み
                  </span>
                ) : (
                  <span className="flex-1 px-2 py-1 bg-gray-700 text-yellow-400 text-sm rounded border border-yellow-600">
                    未設定
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2">
                <span className="text-xs text-gray-500 w-20">{currentProject.ai_api_key ? '変更:' : ''}</span>
                <input
                  type="password"
                  placeholder="新しいAPIキーを入力..."
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                />
                {apiKeyInput.trim() && (
                  <button
                    onClick={() => {
                      handleUpdateAIApiKey(apiKeyInput.trim())
                      setApiKeyInput('')
                    }}
                    className="px-2 py-1 bg-primary-600 hover:bg-primary-500 text-white text-xs rounded transition-colors"
                  >
                    保存
                  </button>
                )}
              </div>
            </div>

            {/* Default Image Duration Setting */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <label className="block text-sm text-gray-400 mb-2">タイムライン設定</label>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-32">静止画デフォルト尺:</span>
                <select
                  value={defaultImageDurationMs}
                  onChange={(e) => setDefaultImageDurationMs(Number(e.target.value))}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
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
              <p className="text-xs text-gray-500 mt-1">画像をタイムラインに配置する際のデフォルト表示時間</p>
            </div>

            {/* Activity Panel Settings */}
            <ActivitySettingsSection />

            <div className="flex justify-end">
              <button
                onClick={() => setShowSettingsModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                閉じる
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Keyboard Shortcuts Modal */}
      {showShortcutsModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-[480px] max-w-[90vw] max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">キーボードショートカット</h3>
              <button
                onClick={() => setShowShortcutsModal(false)}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Timeline Operations */}
            <div className="mb-4">
              <h4 className="text-sm font-medium text-gray-300 mb-2">タイムライン操作</h4>
              <div className="space-y-1">
                {[
                  { key: 'Delete / Backspace', desc: '選択中のクリップを削除' },
                  { key: 'S', desc: 'スナップ機能のオン/オフ' },
                  { key: 'C', desc: '選択中のクリップを再生ヘッド位置で分割' },
                  { key: 'A', desc: '再生ヘッド以降のクリップを全選択' },
                  { key: 'Shift + E', desc: 'タイムライン末尾へスクロール' },
                  { key: 'Shift + H', desc: '再生ヘッド位置へスクロール' },
                  { key: 'Escape', desc: 'コンテキストメニューを閉じる / 選択解除' },
                ].map((shortcut) => (
                  <div key={shortcut.key} className="flex items-center justify-between py-1">
                    <span className="text-gray-400 text-sm">{shortcut.desc}</span>
                    <kbd className="px-2 py-0.5 bg-gray-700 text-gray-200 text-xs rounded border border-gray-600 font-mono">
                      {shortcut.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>

            {/* Undo/Redo */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <h4 className="text-sm font-medium text-gray-300 mb-2">編集操作（Undo/Redo）</h4>
              <div className="space-y-1">
                {[
                  { key: '⌘/Ctrl + Z', desc: '元に戻す（Undo）' },
                  { key: '⌘/Ctrl + Shift + Z', desc: 'やり直し（Redo）' },
                  { key: '⌘/Ctrl + Y', desc: 'やり直し（Redo）※代替' },
                ].map((shortcut) => (
                  <div key={shortcut.key} className="flex items-center justify-between py-1">
                    <span className="text-gray-400 text-sm">{shortcut.desc}</span>
                    <kbd className="px-2 py-0.5 bg-gray-700 text-gray-200 text-xs rounded border border-gray-600 font-mono">
                      {shortcut.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>

            {/* Text Input */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <h4 className="text-sm font-medium text-gray-300 mb-2">テキスト入力時</h4>
              <div className="space-y-1">
                {[
                  { key: 'Enter', desc: '入力を確定' },
                  { key: 'Escape', desc: '入力をキャンセル' },
                ].map((shortcut) => (
                  <div key={shortcut.key} className="flex items-center justify-between py-1">
                    <span className="text-gray-400 text-sm">{shortcut.desc}</span>
                    <kbd className="px-2 py-0.5 bg-gray-700 text-gray-200 text-xs rounded border border-gray-600 font-mono">
                      {shortcut.key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>

            <p className="text-xs text-gray-500 mt-2">
              ※ 入力フォーカスがある場合、タイムライン操作のショートカットは無効化されます
            </p>

            <div className="flex justify-end mt-4">
              <button
                onClick={() => setShowShortcutsModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                閉じる
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Export History Modal */}
      {showHistoryModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-[500px] max-w-[90vw] max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">エクスポート履歴</h3>
              <button
                onClick={() => setShowHistoryModal(false)}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {renderHistory.length > 0 ? (
                <div className="space-y-2">
                  {renderHistory.map((job) => (
                    <div key={job.id} className="flex items-center justify-between bg-gray-700/50 rounded px-4 py-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-gray-300">
                          {job.completed_at && new Date(job.completed_at).toLocaleString('ja-JP')}
                        </div>
                        <div className="flex items-center gap-3 mt-1">
                          {job.output_size && (
                            <span className="text-xs text-gray-500">
                              {(job.output_size / 1024 / 1024).toFixed(1)} MB
                            </span>
                          )}
                          <span className={`text-xs px-2 py-0.5 rounded ${
                            job.status === 'completed' ? 'bg-green-600/20 text-green-400' :
                            job.status === 'failed' ? 'bg-red-600/20 text-red-400' :
                            'bg-yellow-600/20 text-yellow-400'
                          }`}>
                            {job.status === 'completed' ? '完了' :
                             job.status === 'failed' ? '失敗' :
                             job.status === 'processing' ? '処理中' : '待機中'}
                          </span>
                        </div>
                      </div>
                      {job.output_url ? (
                        <button
                          onClick={() => window.open(job.output_url!, '_blank')}
                          className="ml-3 px-3 py-1.5 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded flex items-center gap-2"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                          ダウンロード
                        </button>
                      ) : job.status === 'completed' ? (
                        <span className="ml-3 text-sm text-gray-500">期限切れ</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-500">
                  エクスポート履歴がありません
                </div>
              )}
            </div>

            <div className="flex justify-end mt-4 pt-4 border-t border-gray-700">
              <button
                onClick={() => setShowHistoryModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                閉じる
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main Editor Area */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left Sidebar - Asset Library */}
        {isAssetPanelOpen ? (
          <aside
            className="bg-gray-800 border-r border-gray-700 flex flex-col overflow-y-auto relative"
            style={{ width: leftPanelWidth, scrollbarGutter: 'stable' }}
          >
            {/* Header with close button */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 flex-shrink-0">
              <span className="text-white font-medium text-sm">アセット</span>
              <button
                onClick={() => setIsAssetPanelOpen(false)}
                className="text-gray-400 hover:text-white transition-colors"
                title="閉じる"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-hidden">
              <LeftPanel
                projectId={currentProject.id}
                currentTimeline={currentProject.timeline_data}
                currentSessionId={currentSessionId}
                currentSessionName={currentSessionName}
                assets={assets}
                onPreviewAsset={handlePreviewAsset}
                onAssetsChange={fetchAssets}
                onOpenSession={handleOpenSession}
                onSaveSession={handleSaveSessionFromPanel}
                refreshTrigger={assetLibraryRefreshTrigger}
              />
            </div>
          </aside>
        ) : (
          /* Asset Panel - Collapsed */
          <div
            onClick={() => setIsAssetPanelOpen(true)}
            className="bg-gray-800 border-r border-gray-700 w-10 flex flex-col items-center py-3 cursor-pointer hover:bg-gray-700 transition-colors"
          >
            <svg className="w-4 h-4 text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>アセット</span>
          </div>
        )}

        {/* Left panel resize handle - placed outside aside for reliable interaction */}
        {isAssetPanelOpen && (
          <div
            className="w-1 h-full cursor-ew-resize hover:bg-blue-500/50 active:bg-blue-500 transition-colors flex-shrink-0 -ml-0.5"
            onMouseDown={handleLeftPanelResizeStart}
          />
        )}

        {/* Center - Preview */}
        <main className="flex-1 flex flex-col min-h-0 min-w-0 overflow-hidden">
          {/* Preview Canvas - Resizable */}
          <div
            className="bg-gray-900 flex flex-col items-center p-4 flex-shrink-0 relative"
            style={{ height: previewHeight }}
            onClick={(e) => {
              // Deselect when clicking on the outer gray area
              if (e.target === e.currentTarget) {
                setSelectedVideoClip(null)
                setSelectedClip(null)
              }
            }}
          >
            {/* Preview controls: border settings and zoom */}
            <div className="absolute top-2 right-2 flex items-center gap-4 bg-gray-800/80 rounded px-2 py-1 z-10">
              {/* Border controls */}
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-400">枠:</label>
                <input
                  type="color"
                  value={previewBorderColor}
                  onChange={(e) => setPreviewBorderColor(e.target.value)}
                  className="w-6 h-6 rounded cursor-pointer border border-gray-600"
                  title="枠の色"
                />
                <input
                  type="number"
                  value={previewBorderWidth}
                  onChange={(e) => setPreviewBorderWidth(Math.max(0, Math.min(20, parseInt(e.target.value) || 0)))}
                  className="w-12 px-1 py-0.5 text-xs bg-gray-700 border border-gray-600 rounded text-white text-center"
                  min={0}
                  max={20}
                  title="枠の太さ (px)"
                />
              </div>
              {/* Zoom controls separator */}
              <div className="w-px h-5 bg-gray-600" />
              {/* Zoom controls */}
              <div className="flex items-center gap-1">
                <button
                  onClick={handlePreviewZoomOut}
                  className="text-gray-400 hover:text-white p-1"
                  title="縮小 (Ctrl+スクロールでも可能)"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
                  </svg>
                </button>
                <span className="text-gray-400 text-xs w-10 text-center">{Math.round(previewZoom * 100)}%</span>
                <button
                  onClick={handlePreviewZoomIn}
                  className="text-gray-400 hover:text-white p-1"
                  title="拡大 (Ctrl+スクロールでも可能)"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                </button>
                <button
                  onClick={handlePreviewZoomFit}
                  className="px-2 py-0.5 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded ml-1"
                  title="フィット (100%に戻す)"
                >
                  Fit
                </button>
              </div>
            </div>
            {/* Preview area wrapper - takes remaining space after playback controls */}
            <div
              ref={previewAreaRef}
              className={`flex-1 min-h-0 w-full flex items-center justify-center ${previewZoom > 1 ? 'overflow-auto' : 'overflow-hidden'}`}
              onClick={(e) => {
                if (e.target === e.currentTarget) {
                  setSelectedVideoClip(null)
                  setSelectedClip(null)
                }
              }}
              onWheel={handlePreviewWheel}
              onMouseDown={handlePreviewPanStart}
              style={{ cursor: isPanningPreview ? 'grabbing' : (previewZoom > 1 ? 'grab' : 'default') }}
            >
            <div
              ref={previewContainerRef}
              className={`bg-black relative ${selectedVideoClip ? 'overflow-visible' : 'overflow-hidden'}`}
              style={{
                // Container maintains aspect ratio based on measured area height, scaled by previewZoom
                width: effectivePreviewHeight * currentProject.width / currentProject.height * previewZoom,
                height: effectivePreviewHeight * previewZoom,
                // Apply pan offset when zoomed
                transform: previewZoom > 1 ? `translate(${previewPan.x}px, ${previewPan.y}px)` : undefined,
                flexShrink: 0,
              }}
              onClick={(e) => {
                // Deselect when clicking on the background (not on a clip)
                if (e.target === e.currentTarget) {
                  setSelectedVideoClip(null)
                  setSelectedClip(null)
                }
              }}
            >
              {/* Preview content - Buffer approach for images, single element for video */}
              {(() => {
                // Calculate scale factor for the preview
                // Uses measured previewAreaHeight (via ResizeObserver) for accurate sizing
                // The previewZoom factor is applied to allow zooming in/out of the preview
                const containerHeight = effectivePreviewHeight * previewZoom
                const containerWidth = containerHeight * currentProject.width / currentProject.height
                const previewScale = Math.min(containerWidth / currentProject.width, containerHeight / currentProject.height)
                // Compute which clips are visible at current time
                // Collect all visible clips from bottom to top layer
                interface ActiveClipInfo {
                  layerId: string
                  clip: typeof currentProject.timeline_data.layers[0]['clips'][0]
                  assetId: string | null
                  assetType: string | null
                  shape: Shape | null
                  transform: { x: number; y: number; scale: number; rotation: number; opacity: number }
                  locked: boolean
                  chromaKey: { enabled: boolean; color: string; similarity: number; blend: number } | null
                }
                const activeClips: ActiveClipInfo[] = []

                if (currentProject) {
                  const layers = currentProject.timeline_data.layers
                  // Iterate from bottom to top (higher index = bottom layer = lower z-index)
                  // Layer 0 is at top of UI and should render on top (highest z-index)
                  for (let i = layers.length - 1; i >= 0; i--) {
                    const layer = layers[i]
                    if (layer.visible === false) continue
                    for (const clip of layer.clips) {
                      // Use exclusive end boundary to prevent overlapping display at clip transitions
                      if (currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
                        const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
                        const timeInClipMs = currentTime - clip.start_ms

                        // Get interpolated transform
                        const interpolated = clip.keyframes && clip.keyframes.length > 0
                          ? getInterpolatedTransform(clip, timeInClipMs)
                          : {
                              x: clip.transform.x,
                              y: clip.transform.y,
                              scale: clip.transform.scale,
                              rotation: clip.transform.rotation,
                              opacity: clip.effects.opacity,
                            }

                        // Calculate fade-adjusted opacity
                        let fadeOpacity = interpolated.opacity
                        const fadeInMs = clip.effects.fade_in_ms ?? 0
                        const fadeOutMs = clip.effects.fade_out_ms ?? 0

                        // Apply fade in: linear interpolation from 0 to base opacity
                        if (fadeInMs > 0 && timeInClipMs < fadeInMs) {
                          const fadeInProgress = timeInClipMs / fadeInMs
                          fadeOpacity = interpolated.opacity * fadeInProgress
                        }

                        // Apply fade out: linear interpolation from base opacity to 0
                        const timeFromEnd = clip.duration_ms - timeInClipMs
                        if (fadeOutMs > 0 && timeFromEnd < fadeOutMs) {
                          const fadeOutProgress = timeFromEnd / fadeOutMs
                          fadeOpacity = interpolated.opacity * fadeOutProgress
                        }

                        // Apply dragTransform if this clip is being dragged
                        const isDraggingThis = previewDrag?.clipId === clip.id && dragTransform
                        const finalTransform = isDraggingThis
                          ? {
                              ...interpolated,
                              x: dragTransform.x,
                              y: dragTransform.y,
                              scale: dragTransform.scale,
                              opacity: fadeOpacity,
                              // Include image dimensions during drag
                              width: dragTransform.imageWidth,
                              height: dragTransform.imageHeight,
                            }
                          : {
                              ...interpolated,
                              opacity: fadeOpacity,
                              // Get stored image dimensions from clip transform
                              width: (clip.transform as { width?: number }).width,
                              height: (clip.transform as { height?: number }).height,
                            }
                        const finalShape = clip.shape && isDraggingThis && (dragTransform.shapeWidth || dragTransform.shapeHeight)
                          ? { ...clip.shape, width: dragTransform.shapeWidth ?? clip.shape.width, height: dragTransform.shapeHeight ?? clip.shape.height }
                          : clip.shape || null

                        // Apply fade in/out for shape clips
                        let finalOpacity = finalTransform.opacity
                        if (clip.shape && (clip.fade_in_ms || clip.fade_out_ms)) {
                          const fadeMultiplier = calculateFadeOpacity(
                            timeInClipMs,
                            clip.duration_ms,
                            clip.fade_in_ms || 0,
                            clip.fade_out_ms || 0
                          )
                          finalOpacity = finalTransform.opacity * fadeMultiplier
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
                                blend: clip.effects.chroma_key.blend ?? 0.0,
                              }
                            : null,
                        })
                      }
                    }
                  }
                }

                // Check if any clips are still loading (asset exists but not cached or not preloaded)
                const clipsLoading = activeClips.filter(c => {
                  if (!c.assetId) return false
                  // Video: check if URL is cached AND video first frame is loaded
                  if (c.assetType === 'video') return !assetUrlCache.has(c.assetId) || !preloadedVideos.has(c.assetId)
                  // Image: check if URL is cached AND image is decoded/preloaded
                  if (c.assetType === 'image') return !assetUrlCache.has(c.assetId) || !preloadedImages.has(c.assetId)
                  return false
                })
                const needsLoading = clipsLoading.length > 0


                return (
                  <div
                    className="absolute inset-0 origin-top-left"
                    style={{
                      width: currentProject.width,
                      height: currentProject.height,
                      transform: `scale(${previewScale})`,
                    }}
                  >
                    {/* Render area border - user configurable, always on top */}
                    {previewBorderWidth > 0 && (
                      <div
                        className="absolute pointer-events-none"
                        style={{
                          inset: -previewBorderWidth,
                          border: `${previewBorderWidth}px solid ${previewBorderColor}`,
                          zIndex: 9999,
                        }}
                      />
                    )}

                    {/* Background layer for click-to-deselect when clips are present */}
                    {activeClips.length > 0 && (
                      <div
                        className="absolute inset-0 bg-black"
                        style={{ zIndex: 1 }}
                        onClick={() => {
                          setSelectedVideoClip(null)
                          setSelectedClip(null)
                        }}
                      />
                    )}

                    {/* Preload layer - hidden video/image elements for instant switching */}
                    <div className="absolute" style={{ visibility: 'hidden', pointerEvents: 'none' }}>
                      {currentProject.timeline_data.layers.flatMap(layer =>
                        layer.clips
                          .filter(clip => clip.asset_id)
                          .map(clip => {
                            const asset = assets.find(a => a.id === clip.asset_id)
                            const url = assetUrlCache.get(clip.asset_id!)
                            if (!url || !asset) return null

                            if (asset.type === 'video') {
                              return (
                                <video
                                  key={`preload-${clip.id}`}
                                  src={`${url}#t=0.001`}
                                  preload="auto"
                                  muted
                                  playsInline
                                  onLoadedData={() => {
                                    setPreloadedVideos(prev => new Set(prev).add(clip.asset_id!))
                                  }}
                                />
                              )
                            }
                            if (asset.type === 'image') {
                              return (
                                <img
                                  key={`preload-${clip.id}`}
                                  src={url}
                                  alt=""
                                />
                              )
                            }
                            return null
                          })
                      )}
                    </div>

                    {/* Render all active clips with transforms - interactive */}
                    {activeClips.map((activeClip, index) => {
                      const isSelected = selectedVideoClip?.clipId === activeClip.clip.id
                      const isDragging = previewDrag?.clipId === activeClip.clip.id

                      // Helper to get rotation-adjusted cursor for resize handles
                      const getHandleCursor = (handleType: string): string => {
                        const rotation = activeClip.transform.rotation || 0
                        const normalizedRotation = ((rotation % 360) + 360) % 360
                        const diagonalCursors = ['nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize']
                        const edgeCursors = ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize']
                        const cursorIndex = Math.round(normalizedRotation / 45) % 4

                        const handleBaseIndex: Record<string, number> = {
                          'resize-tl': 0, 'resize-br': 0,
                          'resize-tr': 2, 'resize-bl': 2,
                          'resize-t': 0, 'resize-b': 0,
                          'resize-l': 2, 'resize-r': 2,
                        }

                        const isEdgeHandle = ['resize-t', 'resize-b', 'resize-l', 'resize-r'].includes(handleType)
                        const baseIndex = handleBaseIndex[handleType] ?? 0
                        const adjustedIndex = (baseIndex + cursorIndex) % 4
                        return isEdgeHandle ? edgeCursors[adjustedIndex] : diagonalCursors[adjustedIndex]
                      }

                      // Render shape clips
                      if (activeClip.shape) {
                        const shape = activeClip.shape
                        return (
                          <div
                            key={`${activeClip.clip.id}-shape`}
                            className="absolute"
                            style={{
                              top: '50%',
                              left: '50%',
                              transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
                              opacity: activeClip.transform.opacity,
                              zIndex: isSelected ? 1000 : index + 10,
                              transformOrigin: 'center center',
                            }}
                          >
                            <div
                              className={`relative ${isSelected && !activeClip.locked ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-transparent' : ''}`}
                              style={{
                                cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
                                userSelect: 'none',
                              }}
                              onMouseDown={(e) => handlePreviewDragStart(e, 'move', activeClip.layerId, activeClip.clip.id)}
                            >
                              <svg
                                width={shape.width + shape.strokeWidth}
                                height={shape.height + shape.strokeWidth}
                                className="block pointer-events-none"
                              >
                                {shape.type === 'rectangle' && (
                                  <rect
                                    x={shape.strokeWidth / 2}
                                    y={shape.strokeWidth / 2}
                                    width={shape.width}
                                    height={shape.height}
                                    fill={shape.filled ? shape.fillColor : 'none'}
                                    stroke={shape.strokeColor}
                                    strokeWidth={shape.strokeWidth}
                                  />
                                )}
                                {shape.type === 'circle' && (
                                  <ellipse
                                    cx={(shape.width + shape.strokeWidth) / 2}
                                    cy={(shape.height + shape.strokeWidth) / 2}
                                    rx={shape.width / 2}
                                    ry={shape.height / 2}
                                    fill={shape.filled ? shape.fillColor : 'none'}
                                    stroke={shape.strokeColor}
                                    strokeWidth={shape.strokeWidth}
                                  />
                                )}
                                {shape.type === 'line' && (
                                  <line
                                    x1={shape.strokeWidth / 2}
                                    y1={(shape.height + shape.strokeWidth) / 2}
                                    x2={shape.width + shape.strokeWidth / 2}
                                    y2={(shape.height + shape.strokeWidth) / 2}
                                    stroke={shape.strokeColor}
                                    strokeWidth={shape.strokeWidth}
                                    strokeLinecap="round"
                                  />
                                )}
                              </svg>
                              {/* Resize handles when selected and not locked */}
                              {isSelected && !activeClip.locked && (
                                <>
                                  {/* Corner handles - anchor at opposite corner */}
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: 0, left: 0, transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-tl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: 0, right: 0, transform: 'translate(50%, -50%)', cursor: getHandleCursor('resize-tr') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tr', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: 0, left: 0, transform: 'translate(-50%, 50%)', cursor: getHandleCursor('resize-bl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-bl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: 0, right: 0, transform: 'translate(50%, 50%)', cursor: getHandleCursor('resize-br') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-br', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  {/* Edge handles - anchor at opposite edge */}
                                  <div
                                    className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ top: 0, left: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-t') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-t', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ bottom: 0, left: '50%', transform: 'translate(-50%, 50%)', cursor: getHandleCursor('resize-b') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-b', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ left: 0, top: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-l') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-l', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ right: 0, top: '50%', transform: 'translate(50%, -50%)', cursor: getHandleCursor('resize-r') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-r', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                </>
                              )}
                            </div>
                          </div>
                        )
                      }

                      // Render text clips (telops)
                      if (activeClip.clip.text_content !== undefined) {
                        const textStyle = activeClip.clip.text_style || {
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

                        // Convert hex color + opacity to rgba
                        const getBackgroundColor = () => {
                          const bgColor = textStyle.backgroundColor || 'transparent'
                          const bgOpacity = textStyle.backgroundOpacity ?? 1
                          if (bgColor === 'transparent' || bgOpacity === 0) return 'transparent'
                          // Parse hex color to rgba
                          const hex = bgColor.replace('#', '')
                          const r = parseInt(hex.substring(0, 2), 16)
                          const g = parseInt(hex.substring(2, 4), 16)
                          const b = parseInt(hex.substring(4, 6), 16)
                          return `rgba(${r}, ${g}, ${b}, ${bgOpacity})`
                        }
                        return (
                          <div
                            key={`${activeClip.clip.id}-text`}
                            className="absolute"
                            style={{
                              top: '50%',
                              left: '50%',
                              transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
                              opacity: activeClip.transform.opacity,
                              zIndex: isSelected ? 1000 : index + 10,
                              transformOrigin: 'center center',
                            }}
                          >
                            <div
                              className={`relative ${isSelected && !activeClip.locked ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-transparent' : ''}`}
                              style={{
                                cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
                                userSelect: 'none',
                                backgroundColor: getBackgroundColor(),
                                padding: textStyle.backgroundColor !== 'transparent' && (textStyle.backgroundOpacity ?? 1) > 0 ? '8px 16px' : '0',
                                borderRadius: textStyle.backgroundColor !== 'transparent' && (textStyle.backgroundOpacity ?? 1) > 0 ? '4px' : '0',
                                display: 'flex',
                                flexDirection: 'column',
                                justifyContent: textStyle.verticalAlign === 'top' ? 'flex-start' : textStyle.verticalAlign === 'bottom' ? 'flex-end' : 'center',
                                textAlign: textStyle.textAlign,
                                minWidth: '50px',
                              }}
                              onMouseDown={(e) => handlePreviewDragStart(e, 'move', activeClip.layerId, activeClip.clip.id)}
                            >
                              <span
                                style={{
                                  fontFamily: textStyle.fontFamily,
                                  fontSize: `${textStyle.fontSize}px`,
                                  fontWeight: textStyle.fontWeight,
                                  fontStyle: textStyle.fontStyle,
                                  color: textStyle.color,
                                  lineHeight: textStyle.lineHeight,
                                  letterSpacing: `${textStyle.letterSpacing}px`,
                                  WebkitTextStroke: textStyle.strokeWidth > 0 ? `${textStyle.strokeWidth}px ${textStyle.strokeColor}` : 'none',
                                  paintOrder: 'stroke fill',
                                  whiteSpace: 'pre-wrap',
                                  display: 'block',
                                }}
                              >
                                {activeClip.clip.text_content}
                              </span>
                            </div>
                          </div>
                        )
                      }

                      // Render asset-based clips (images)
                      if (!activeClip.assetId) return null
                      const url = assetUrlCache.get(activeClip.assetId)
                      if (!url) return null

                      if (activeClip.assetType === 'image') {
                        // Check if image has explicit width/height (independent resize mode)
                        // Note: transform.width/height can be null, so we need to check for both null and undefined
                        const imageWidth = (activeClip.transform as { width?: number | null }).width
                        const imageHeight = (activeClip.transform as { height?: number | null }).height
                        const hasExplicitSize = typeof imageWidth === 'number' && typeof imageHeight === 'number'

                        return (
                          <div
                            key={`${activeClip.clip.id}-${activeClip.assetId}`}
                            className="absolute"
                            style={{
                              top: '50%',
                              left: '50%',
                              // Use scale=1 if we have explicit width/height, otherwise use the stored scale
                              transform: hasExplicitSize
                                ? `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) rotate(${activeClip.transform.rotation}deg)`
                                : `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
                              opacity: activeClip.transform.opacity,
                              zIndex: isSelected ? 1000 : index + 10,
                              transformOrigin: 'center center',
                            }}
                          >
                            <div
                              className="relative"
                              style={{
                                userSelect: 'none',
                              }}
                            >
                              <img
                                src={url}
                                alt=""
                                data-clip-id={activeClip.clip.id}
                                data-asset-id={activeClip.assetId}
                                className="block max-w-none pointer-events-none"
                                style={{
                                  // Use explicit width/height if available, otherwise let natural size
                                  ...(hasExplicitSize
                                    ? { width: imageWidth, height: imageHeight }
                                    : {}
                                  ),
                                  clipPath: (() => {
                                    // Use dragCrop during drag for live preview, otherwise use stored crop
                                    const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                    if (!crop) return undefined
                                    return `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
                                  })(),
                                }}
                                draggable={false}
                              />
                              {/* Invisible move handle - only covers the visible (cropped) area */}
                              {(() => {
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                return (
                                  <div
                                    className="absolute"
                                    style={{
                                      top: `${cropT}%`,
                                      left: `${cropL}%`,
                                      right: `${cropR}%`,
                                      bottom: `${cropB}%`,
                                      cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
                                    }}
                                    onMouseDown={(e) => handlePreviewDragStart(e, 'move', activeClip.layerId, activeClip.clip.id)}
                                  />
                                )
                              })()}
                              {/* Selection outline - follows crop area */}
                              {isSelected && !activeClip.locked && (() => {
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                return (
                                  <div
                                    className="absolute pointer-events-none border-2 border-primary-500"
                                    style={{
                                      top: `${cropT}%`,
                                      left: `${cropL}%`,
                                      right: `${cropR}%`,
                                      bottom: `${cropB}%`,
                                    }}
                                  />
                                )
                              })()}
                              {/* Resize handles when selected and not locked */}
                              {isSelected && !activeClip.locked && (() => {
                                // Get current crop values for positioning resize handles
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                // Calculate center positions within cropped area
                                const centerX = cropL + (100 - cropL - cropR) / 2
                                const centerY = cropT + (100 - cropT - cropB) / 2
                                return (
                                <>
                                  {/* Corner resize handles - positioned at cropped area corners */}
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: `${cropT}%`, left: `${cropL}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-tl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: `${cropT}%`, right: `${cropR}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor('resize-tr') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tr', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: `${cropB}%`, left: `${cropL}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor('resize-bl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-bl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: `${cropB}%`, right: `${cropR}%`, transform: 'translate(50%, 50%)', cursor: getHandleCursor('resize-br') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-br', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  {/* Edge handles - for independent width/height resize */}
                                  <div
                                    className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-t') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-t', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-3 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor('resize-b') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-b', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-l') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-l', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-5 bg-green-500 border-2 border-white rounded-sm"
                                    style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor('resize-r') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-r', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  {/* Crop handles - orange, positioned at crop edges */}
                                  <div
                                    className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: 'ns-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-t', activeClip.layerId, activeClip.clip.id) }}
                                    title="上をクロップ"
                                  />
                                  <div
                                    className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: 'ns-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-b', activeClip.layerId, activeClip.clip.id) }}
                                    title="下をクロップ"
                                  />
                                  <div
                                    className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: 'ew-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-l', activeClip.layerId, activeClip.clip.id) }}
                                    title="左をクロップ"
                                  />
                                  <div
                                    className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: 'ew-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-r', activeClip.layerId, activeClip.clip.id) }}
                                    title="右をクロップ"
                                  />
                                </>
                              )})()}
                            </div>
                          </div>
                        )
                      }

                      // Render video clips
                      if (activeClip.assetType === 'video') {
                        const chromaKeyEnabled = activeClip.chromaKey?.enabled

                        return (
                          <div
                            key={`${activeClip.clip.id}-video`}
                            className="absolute"
                            style={{
                              top: '50%',
                              left: '50%',
                              transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
                              opacity: activeClip.transform.opacity,
                              zIndex: isSelected ? 1000 : index + 10,
                              transformOrigin: 'center center',
                            }}
                          >
                            <div
                              className="relative"
                              style={{
                                userSelect: 'none',
                              }}
                            >
                              {/* Video element - hidden when chroma key is enabled */}
                              <video
                                ref={(el) => {
                                  if (el) videoRefsMap.current.set(activeClip.clip.id, el)
                                  else videoRefsMap.current.delete(activeClip.clip.id)
                                }}
                                src={url}
                                crossOrigin="anonymous"
                                className="block max-w-none pointer-events-none"
                                style={{
                                  visibility: chromaKeyEnabled ? 'hidden' : 'visible',
                                  position: chromaKeyEnabled ? 'absolute' : 'relative',
                                  clipPath: (() => {
                                    // Use dragCrop during drag for live preview, otherwise use stored crop
                                    const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                    if (!crop) return undefined
                                    return `inset(${crop.top * 100}% ${crop.right * 100}% ${crop.bottom * 100}% ${crop.left * 100}%)`
                                  })(),
                                }}
                                muted
                                playsInline
                                preload="auto"
                              />
                              {/* Chroma key canvas overlay */}
                              {chromaKeyEnabled && activeClip.chromaKey && (
                                <ChromaKeyCanvas
                                  clipId={activeClip.clip.id}
                                  videoRefsMap={videoRefsMap}
                                  chromaKey={activeClip.chromaKey}
                                  isPlaying={isPlaying}
                                  crop={(previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop}
                                />
                              )}
                              {/* Invisible move handle - only covers the visible (cropped) area */}
                              {(() => {
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                return (
                                  <div
                                    className="absolute"
                                    style={{
                                      top: `${cropT}%`,
                                      left: `${cropL}%`,
                                      right: `${cropR}%`,
                                      bottom: `${cropB}%`,
                                      cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
                                    }}
                                    onMouseDown={(e) => handlePreviewDragStart(e, 'move', activeClip.layerId, activeClip.clip.id)}
                                  />
                                )
                              })()}
                              {/* Selection outline - follows crop area */}
                              {isSelected && !activeClip.locked && (() => {
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                return (
                                  <div
                                    className="absolute pointer-events-none border-2 border-primary-500"
                                    style={{
                                      top: `${cropT}%`,
                                      left: `${cropL}%`,
                                      right: `${cropR}%`,
                                      bottom: `${cropB}%`,
                                    }}
                                  />
                                )
                              })()}
                              {/* Resize handles when selected and not locked */}
                              {isSelected && !activeClip.locked && (() => {
                                // Get current crop values for positioning resize handles
                                const crop = (previewDrag?.clipId === activeClip.clip.id && dragCrop) ? dragCrop : activeClip.clip.crop
                                const cropT = (crop?.top || 0) * 100
                                const cropR = (crop?.right || 0) * 100
                                const cropB = (crop?.bottom || 0) * 100
                                const cropL = (crop?.left || 0) * 100
                                // Calculate center positions within cropped area
                                const centerX = cropL + (100 - cropL - cropR) / 2
                                const centerY = cropT + (100 - cropT - cropB) / 2
                                return (
                                <>
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: `${cropT}%`, left: `${cropL}%`, transform: 'translate(-50%, -50%)', cursor: getHandleCursor('resize-tl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ top: `${cropT}%`, right: `${cropR}%`, transform: 'translate(50%, -50%)', cursor: getHandleCursor('resize-tr') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tr', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: `${cropB}%`, left: `${cropL}%`, transform: 'translate(-50%, 50%)', cursor: getHandleCursor('resize-bl') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-bl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-5 h-5 bg-primary-500 border-2 border-white rounded-sm"
                                    style={{ bottom: `${cropB}%`, right: `${cropR}%`, transform: 'translate(50%, 50%)', cursor: getHandleCursor('resize-br') }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-br', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  {/* Crop handles - orange, positioned at crop edges */}
                                  <div
                                    className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ top: `${cropT}%`, left: `${centerX}%`, transform: 'translate(-50%, -50%)', cursor: 'ns-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-t', activeClip.layerId, activeClip.clip.id) }}
                                    title="上をクロップ"
                                  />
                                  <div
                                    className="absolute w-10 h-2 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ bottom: `${cropB}%`, left: `${centerX}%`, transform: 'translate(-50%, 50%)', cursor: 'ns-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-b', activeClip.layerId, activeClip.clip.id) }}
                                    title="下をクロップ"
                                  />
                                  <div
                                    className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ left: `${cropL}%`, top: `${centerY}%`, transform: 'translate(-50%, -50%)', cursor: 'ew-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-l', activeClip.layerId, activeClip.clip.id) }}
                                    title="左をクロップ"
                                  />
                                  <div
                                    className="absolute w-2 h-10 bg-orange-500 border border-white rounded-sm opacity-70 hover:opacity-100"
                                    style={{ right: `${cropR}%`, top: `${centerY}%`, transform: 'translate(50%, -50%)', cursor: 'ew-resize' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'crop-r', activeClip.layerId, activeClip.clip.id) }}
                                    title="右をクロップ"
                                  />
                                </>
                              )})()}
                            </div>
                          </div>
                        )
                      }

                      return null
                    })}


                    {/* Loading indicator for video (non-blocking - video will appear when ready) */}
                    {needsLoading && (
                      <div
                        className="absolute flex items-center justify-center pointer-events-none"
                        style={{
                          top: '50%',
                          left: '50%',
                          transform: 'translate(-50%, -50%)',
                          zIndex: 1000,
                        }}
                      >
                        <div className="bg-gray-800/80 rounded-lg px-4 py-3 flex items-center gap-3">
                          <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-primary-500"></div>
                          <span className="text-sm text-gray-300">Loading video...</span>
                        </div>
                      </div>
                    )}

                    {/* Audio preview (from asset library manual preview) */}
                    {preview.url && preview.asset?.type === 'audio' && (
                      <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-400 bg-black">
                        <svg className="w-16 h-16 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                        </svg>
                        <p className="text-sm mb-2">{preview.asset.name}</p>
                        <audio src={preview.url} controls autoPlay className="w-64" />
                      </div>
                    )}

                    {/* Black screen with timecode when no active clips or video is loading */}
                    {(activeClips.length === 0 || needsLoading) && !(preview.url && preview.asset?.type === 'audio') && (
                      <div
                        className="absolute inset-0 bg-black cursor-default"
                        style={{ zIndex: 0 }}
                        onClick={() => {
                          setSelectedVideoClip(null)
                          setSelectedClip(null)
                        }}
                      >
                        <div className="absolute bottom-2 right-2 text-gray-600 text-xs font-mono pointer-events-none">
                          {Math.floor(currentTime / 60000)}:
                          {Math.floor((currentTime % 60000) / 1000).toString().padStart(2, '0')}
                          .{Math.floor((currentTime % 1000) / 10).toString().padStart(2, '0')}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })()}

            </div>
            </div>{/* Close preview area wrapper */}

            {/* Playback Controls */}
            <div className="mt-2 flex items-center gap-4 flex-shrink-0">
              {/* Stop Button */}
              <button
                onClick={() => { stopPlayback(); setCurrentTime(0); }}
                className="p-2 text-gray-400 hover:text-white transition-colors"
                title="停止"
              >
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              </button>

              {/* Play/Pause Button */}
              <button
                onClick={togglePlayback}
                className="p-3 bg-primary-600 hover:bg-primary-700 rounded-full text-white transition-colors"
                title={isPlaying ? '一時停止' : '再生'}
              >
                {isPlaying ? (
                  <svg className="w-8 h-8" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="5" width="4" height="14" rx="1" />
                    <rect x="14" y="5" width="4" height="14" rx="1" />
                  </svg>
                ) : (
                  <svg className="w-8 h-8" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                )}
              </button>

              {/* Time Display */}
              <div className="text-white font-mono text-sm min-w-[100px]">
                <span>
                  {Math.floor(currentTime / 60000)}:
                  {Math.floor((currentTime % 60000) / 1000).toString().padStart(2, '0')}
                </span>
                <span className="text-gray-500"> / </span>
                <span className="text-gray-400">
                  {Math.floor(currentProject.duration_ms / 60000)}:
                  {Math.floor((currentProject.duration_ms % 60000) / 1000).toString().padStart(2, '0')}
                </span>
              </div>
            </div>
          </div>

          {/* Resize Handle */}
          <div
            className={`h-3 bg-gray-700 hover:bg-primary-600 cursor-ns-resize flex items-center justify-center transition-colors ${isResizing ? 'bg-primary-600' : ''}`}
            style={{ zIndex: 20 }}
            onMouseDown={handleResizeStart}
          >
            <div className="w-12 h-1 bg-gray-500 rounded"></div>
          </div>

          {/* Timeline - fills remaining space */}
          <div className="flex-1 border-t border-gray-700 bg-gray-800 min-h-0 flex flex-col">
            <Timeline
              timeline={currentProject.timeline_data}
              projectId={currentProject.id}
              assets={assets}
              currentTimeMs={currentTime}
              isPlaying={isPlaying}
              onClipSelect={setSelectedClip}
              onVideoClipSelect={setSelectedVideoClip}
              onSeek={handleSeek}
              selectedKeyframeIndex={selectedKeyframeIndex}
              onKeyframeSelect={handleKeyframeSelect}
              unmappedAssetIds={unmappedAssetIds}
              defaultImageDurationMs={defaultImageDurationMs}
              onAssetsChange={fetchAssets}
            />
          </div>
        </main>

        {/* Right Panels Container - Horizontal layout */}
        <div className="flex">
          {/* Property Panel */}
          {isPropertyPanelOpen ? (
            <div
              className="bg-gray-800 border-l border-gray-700 flex flex-col relative"
              style={{ width: rightPanelWidth }}
            >
              {/* Right panel resize handle */}
              <div
                className="absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-blue-500/50 active:bg-blue-500 transition-colors z-10"
                onMouseDown={handleRightPanelResizeStart}
              />
              {/* Header */}
              <div
                onClick={() => setIsPropertyPanelOpen(false)}
                className="flex items-center justify-between px-3 py-2 border-b border-gray-700 cursor-pointer hover:bg-gray-700 transition-colors flex-shrink-0"
              >
                <h2 className="text-white font-medium text-sm">プロパティ</h2>
                <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </div>
              {/* Content */}
              <div className="flex-1 overflow-y-auto p-4" style={{ scrollbarGutter: 'stable' }}>
          {selectedVideoClip ? (
            <div className="space-y-4">
              {/* Video Clip Name */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">クリップ名</label>
                <p className="text-white text-sm truncate">{selectedVideoClip.assetName}</p>
              </div>

              {/* Layer Name */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">レイヤー</label>
                <span className="inline-block px-2 py-0.5 text-xs rounded bg-gray-600 text-white">
                  {selectedVideoClip.layerName}
                </span>
              </div>

              {/* Start Time */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">開始位置 (秒)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  defaultValue={(selectedVideoClip.startMs / 1000).toFixed(2)}
                  key={`start-${selectedVideoClip.clipId}-${selectedVideoClip.startMs}`}
                  onBlur={(e) => {
                    const val = parseFloat(e.target.value)
                    if (!isNaN(val) && val >= 0) {
                      handleUpdateVideoClipTiming({ startMs: Math.round(val * 1000) })
                    } else {
                      e.target.value = (selectedVideoClip.startMs / 1000).toFixed(2)
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.currentTarget.blur()
                    }
                  }}
                  className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                />
              </div>

              {/* Duration */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">長さ (秒)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  defaultValue={(selectedVideoClip.durationMs / 1000).toFixed(2)}
                  key={`duration-${selectedVideoClip.clipId}-${selectedVideoClip.durationMs}`}
                  onBlur={(e) => {
                    const val = parseFloat(e.target.value)
                    if (!isNaN(val) && val >= 0.1) {
                      handleUpdateVideoClipTiming({ durationMs: Math.round(val * 1000) })
                    } else {
                      e.target.value = (selectedVideoClip.durationMs / 1000).toFixed(2)
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.currentTarget.blur()
                    }
                  }}
                  className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                />
              </div>

              {/* Source Cut Information - Only for video/audio assets */}
              {selectedVideoClip.assetId && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-2">ソース情報</label>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-xs text-gray-400 mb-1">カット開始</label>
                      <p className="text-white text-sm bg-gray-700 px-2 py-1 rounded">
                        {(selectedVideoClip.inPointMs / 1000).toFixed(2)}s
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-gray-400 mb-1">カット長さ</label>
                      <p className="text-white text-sm bg-gray-700 px-2 py-1 rounded">
                        {((selectedVideoClip.outPointMs - selectedVideoClip.inPointMs) / 1000).toFixed(2)}s
                      </p>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    ※ タイムライン長さ = カット長さ ÷ 速度
                  </p>
                </div>
              )}

              {/* Speed - Only for video/audio clips (not text, shape, or image) */}
              {(() => {
                // Hide for text clips
                if (selectedVideoClip.textContent) return false
                // Hide for shape clips
                if (selectedVideoClip.shape) return false
                // Hide for image assets
                const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
                if (clipAsset?.type === 'image') return false
                return true
              })() && (
                <div className="pt-4 border-t border-gray-700">
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs text-gray-500">再生速度</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        min="20"
                        max="500"
                        step="10"
                        key={`speed-${selectedVideoClip.speed ?? 1}`}
                        defaultValue={Math.round((selectedVideoClip.speed ?? 1) * 100)}
                        onKeyDown={(e) => {
                          e.stopPropagation()
                          if (e.key === 'Enter') {
                            const val = Math.max(20, Math.min(500, parseInt(e.currentTarget.value) || 100)) / 100
                            handleUpdateVideoClip({ speed: val })
                            e.currentTarget.blur()
                          }
                        }}
                        onBlur={(e) => {
                          const val = Math.max(20, Math.min(500, parseInt(e.target.value) || 100)) / 100
                          if (val !== (selectedVideoClip.speed ?? 1)) {
                            handleUpdateVideoClip({ speed: val })
                          }
                        }}
                        className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                      />
                      <span className="text-xs text-gray-500 ml-1">%</span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0.2"
                    max="5"
                    step="0.1"
                    value={selectedVideoClip.speed ?? 1}
                    onChange={(e) => handleUpdateVideoClipLocal({ speed: parseFloat(e.target.value) })}
                    onMouseUp={(e) => handleUpdateVideoClip({ speed: parseFloat(e.currentTarget.value) })}
                    onTouchEnd={(e) => handleUpdateVideoClip({ speed: parseFloat((e.target as HTMLInputElement).value) })}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                  />
                </div>
              )}

              {/* Keyframes Section */}
              <div className="pt-4 border-t border-gray-700">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-gray-500">キーフレーム</label>
                  <span className="text-xs text-gray-400">
                    {selectedVideoClip.keyframes?.length || 0}個
                  </span>
                </div>
                {selectedKeyframeIndex !== null && selectedVideoClip.keyframes && selectedVideoClip.keyframes[selectedKeyframeIndex] && (
                  <div className="mb-2 p-2 bg-yellow-900/30 border border-yellow-600/50 rounded text-xs">
                    <div className="flex items-center justify-between">
                      <span className="text-yellow-400 font-medium">
                        KF {selectedKeyframeIndex + 1} 編集中 ({(selectedVideoClip.keyframes[selectedKeyframeIndex].time_ms / 1000).toFixed(2)}s)
                      </span>
                      <button
                        onClick={() => setSelectedKeyframeIndex(null)}
                        className="text-gray-400 hover:text-white text-xs"
                      >
                        解除
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-1 text-gray-300 mt-1">
                      <span>X: {Math.round(selectedVideoClip.keyframes[selectedKeyframeIndex].transform.x)}</span>
                      <span>Y: {Math.round(selectedVideoClip.keyframes[selectedKeyframeIndex].transform.y)}</span>
                      <span>スケール: {(selectedVideoClip.keyframes[selectedKeyframeIndex].transform.scale * 100).toFixed(0)}%</span>
                      <span>回転: {Math.round(selectedVideoClip.keyframes[selectedKeyframeIndex].transform.rotation)}°</span>
                    </div>
                  </div>
                )}
                <div className="flex gap-2">
                  {currentKeyframeExists() ? (
                    <button
                      onClick={handleRemoveKeyframe}
                      className="flex-1 px-3 py-1.5 text-xs bg-red-600 hover:bg-red-700 text-white rounded transition-colors flex items-center justify-center gap-1"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2L2 12l10 10 10-10L12 2z" />
                      </svg>
                      キーフレーム削除
                    </button>
                  ) : (
                    <button
                      onClick={handleAddKeyframe}
                      className="flex-1 px-3 py-1.5 text-xs bg-yellow-600 hover:bg-yellow-700 text-white rounded transition-colors flex items-center justify-center gap-1"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2L2 12l10 10 10-10L12 2z" />
                      </svg>
                      キーフレーム追加
                    </button>
                  )}
                </div>
                {selectedVideoClip.keyframes && selectedVideoClip.keyframes.length > 0 && (
                  <div className="mt-2 text-xs text-gray-400">
                    <p>アニメーション有効: 位置・サイズが時間で補間されます</p>
                  </div>
                )}
                {(() => {
                  const interpolated = getCurrentInterpolatedValues()
                  if (interpolated && selectedVideoClip.keyframes && selectedVideoClip.keyframes.length > 0) {
                    return (
                      <div className="mt-2 p-2 bg-gray-700/50 rounded text-xs">
                        <p className="text-gray-400 mb-1">現在の補間値:</p>
                        <div className="grid grid-cols-2 gap-1 text-gray-300">
                          <span>X: {Math.round(interpolated.x)}</span>
                          <span>Y: {Math.round(interpolated.y)}</span>
                          <span>スケール: {(interpolated.scale * 100).toFixed(0)}%</span>
                          <span>回転: {Math.round(interpolated.rotation)}°</span>
                        </div>
                      </div>
                    )
                  }
                  return null
                })()}
              </div>

              {/* Transform - Position */}
              {(() => {
                // When keyframes exist, show interpolated values instead of base transform
                const interpolated = getCurrentInterpolatedValues()
                const hasKeyframes = selectedVideoClip.keyframes && selectedVideoClip.keyframes.length > 0
                const displayX = hasKeyframes && interpolated ? Math.round(interpolated.x) : Math.round(selectedVideoClip.transform.x)
                const displayY = hasKeyframes && interpolated ? Math.round(interpolated.y) : Math.round(selectedVideoClip.transform.y)
                const displayScale = hasKeyframes && interpolated ? interpolated.scale : selectedVideoClip.transform.scale
                const displayRotation = hasKeyframes && interpolated ? Math.round(interpolated.rotation) : selectedVideoClip.transform.rotation

                return (
                  <>
                    <div className="pt-4 border-t border-gray-700">
                      <label className="block text-xs text-gray-500 mb-2">位置{hasKeyframes ? ' (キーフレーム)' : ''}</label>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-xs text-gray-600">X</label>
                          <input
                            type="number"
                            value={displayX}
                            onChange={(e) => handleUpdateVideoClip({ transform: { x: parseInt(e.target.value) || 0 } })}
                            className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-600">Y</label>
                          <input
                            type="number"
                            value={displayY}
                            onChange={(e) => handleUpdateVideoClip({ transform: { y: parseInt(e.target.value) || 0 } })}
                            className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                          />
                        </div>
                      </div>
                    </div>

                    {/* Transform - Scale & Rotation */}
                    <div>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <label className="text-xs text-gray-500">スケール{hasKeyframes ? ' (KF)' : ''}</label>
                            <div className="flex items-center">
                              <input
                                type="number"
                                min="10"
                                max="300"
                                step="10"
                                key={`scale-${displayScale}`}
                                defaultValue={Math.round(displayScale * 100)}
                                onKeyDown={(e) => {
                                  e.stopPropagation()
                                  if (e.key === 'Enter') {
                                    const val = Math.max(10, Math.min(300, parseInt(e.currentTarget.value) || 100)) / 100
                                    handleUpdateVideoClip({ transform: { scale: val } })
                                    e.currentTarget.blur()
                                  }
                                }}
                                onBlur={(e) => {
                                  const val = Math.max(10, Math.min(300, parseInt(e.target.value) || 100)) / 100
                                  if (val !== displayScale) {
                                    handleUpdateVideoClip({ transform: { scale: val } })
                                  }
                                }}
                                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                              />
                              <span className="text-xs text-gray-500 ml-1">%</span>
                            </div>
                          </div>
                          <input
                            type="range"
                            min="0.1"
                            max="3"
                            step="0.01"
                            value={displayScale}
                            onChange={(e) => handleUpdateVideoClipLocal({ transform: { scale: parseFloat(e.target.value) } })}
                            onMouseUp={(e) => handleUpdateVideoClip({ transform: { scale: parseFloat(e.currentTarget.value) } })}
                            onTouchEnd={(e) => handleUpdateVideoClip({ transform: { scale: parseFloat((e.target as HTMLInputElement).value) } })}
                            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                          />
                        </div>
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <label className="text-xs text-gray-500">回転{hasKeyframes ? ' (KF)' : ''}</label>
                            <div className="flex items-center">
                              <input
                                type="number"
                                min="-180"
                                max="180"
                                step="1"
                                key={`rot-${displayRotation}`}
                                defaultValue={Math.round(displayRotation)}
                                onKeyDown={(e) => {
                                  e.stopPropagation()
                                  if (e.key === 'Enter') {
                                    const val = Math.max(-180, Math.min(180, parseInt(e.currentTarget.value) || 0))
                                    handleUpdateVideoClip({ transform: { rotation: val } })
                                    e.currentTarget.blur()
                                  }
                                }}
                                onBlur={(e) => {
                                  const val = Math.max(-180, Math.min(180, parseInt(e.target.value) || 0))
                                  if (val !== displayRotation) {
                                    handleUpdateVideoClip({ transform: { rotation: val } })
                                  }
                                }}
                                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                              />
                              <span className="text-xs text-gray-500 ml-1">°</span>
                            </div>
                          </div>
                          <input
                            type="range"
                            min="-180"
                            max="180"
                            step="1"
                            value={displayRotation}
                            onChange={(e) => handleUpdateVideoClipLocal({ transform: { rotation: parseInt(e.target.value) } })}
                            onMouseUp={(e) => handleUpdateVideoClip({ transform: { rotation: parseInt(e.currentTarget.value) } })}
                            onTouchEnd={(e) => handleUpdateVideoClip({ transform: { rotation: parseInt((e.target as HTMLInputElement).value) } })}
                            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                          />
                        </div>
                      </div>
                    </div>
                  </>
                )
              })()}

              {/* Fit/Fill/Stretch to Screen Buttons - Only show for video/image clips with asset */}
              {selectedVideoClip.assetId && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-2">画面サイズ調整</label>
                  <div className="grid grid-cols-3 gap-1">
                    <button
                      onClick={() => handleFitFillStretch('fit')}
                      className="px-2 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                      title="アスペクト比を維持して画面内に収める（余白あり）"
                    >
                      Fit
                    </button>
                    <button
                      onClick={() => handleFitFillStretch('fill')}
                      className="px-2 py-1.5 text-xs bg-green-600 hover:bg-green-700 text-white rounded transition-colors"
                      title="アスペクト比を維持して画面を覆う（はみ出しあり）"
                    >
                      Fill
                    </button>
                    <button
                      onClick={() => handleFitFillStretch('stretch')}
                      className="px-2 py-1.5 text-xs bg-purple-600 hover:bg-purple-700 text-white rounded transition-colors"
                      title="アスペクト比を変更して画面にぴったり合わせる"
                    >
                      Stretch
                    </button>
                  </div>
                </div>
              )}

              {/* Effects - Opacity */}
              <div className="pt-4 border-t border-gray-700">
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs text-gray-500">不透明度</label>
                  <div className="flex items-center">
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="1"
                      key={`op-${selectedVideoClip.effects.opacity ?? 1}`}
                      defaultValue={Math.round((selectedVideoClip.effects.opacity ?? 1) * 100)}
                      onKeyDown={(e) => {
                        e.stopPropagation()
                        if (e.key === 'Enter') {
                          const val = Math.max(0, Math.min(100, parseInt(e.currentTarget.value) || 0)) / 100
                          handleUpdateVideoClip({ effects: { opacity: val } })
                          e.currentTarget.blur()
                        }
                      }}
                      onBlur={(e) => {
                        const val = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100
                        if (val !== (selectedVideoClip.effects.opacity ?? 1)) {
                          handleUpdateVideoClip({ effects: { opacity: val } })
                        }
                      }}
                      className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                    />
                    <span className="text-xs text-gray-500 ml-1">%</span>
                  </div>
                </div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={selectedVideoClip.effects.opacity ?? 1}
                  onChange={(e) => handleUpdateVideoClipLocal({ effects: { opacity: parseFloat(e.target.value) } })}
                  onMouseUp={(e) => handleUpdateVideoClip({ effects: { opacity: parseFloat(e.currentTarget.value) } })}
                  onTouchEnd={(e) => handleUpdateVideoClip({ effects: { opacity: parseFloat((e.target as HTMLInputElement).value) } })}
                  className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                />
              </div>

              {/* Fade In / Fade Out */}
              <div className="pt-4 border-t border-gray-700 space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs text-gray-500">フェードイン</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        min="0"
                        max="3000"
                        step="100"
                        key={`vfi-${selectedVideoClip.fadeInMs ?? 0}`}
                        defaultValue={selectedVideoClip.fadeInMs ?? 0}
                        onKeyDown={(e) => {
                          e.stopPropagation()
                          if (e.key === 'Enter') {
                            const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                            handleUpdateVideoClip({ effects: { fade_in_ms: val } })
                            e.currentTarget.blur()
                          }
                        }}
                        onBlur={(e) => {
                          const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                          if (val !== (selectedVideoClip.fadeInMs ?? 0)) {
                            handleUpdateVideoClip({ effects: { fade_in_ms: val } })
                          }
                        }}
                        className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                      />
                      <span className="text-xs text-gray-500 ml-1">ms</span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="3000"
                    step="100"
                    value={selectedVideoClip.fadeInMs ?? 0}
                    onChange={(e) => handleUpdateVideoClipLocal({ effects: { fade_in_ms: parseInt(e.target.value) } })}
                    onMouseUp={(e) => handleUpdateVideoClip({ effects: { fade_in_ms: parseInt(e.currentTarget.value) } })}
                    onTouchEnd={(e) => handleUpdateVideoClip({ effects: { fade_in_ms: parseInt((e.target as HTMLInputElement).value) } })}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                  />
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs text-gray-500">フェードアウト</label>
                    <div className="flex items-center">
                      <input
                        type="number"
                        min="0"
                        max="3000"
                        step="100"
                        key={`vfo-${selectedVideoClip.fadeOutMs ?? 0}`}
                        defaultValue={selectedVideoClip.fadeOutMs ?? 0}
                        onKeyDown={(e) => {
                          e.stopPropagation()
                          if (e.key === 'Enter') {
                            const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                            handleUpdateVideoClip({ effects: { fade_out_ms: val } })
                            e.currentTarget.blur()
                          }
                        }}
                        onBlur={(e) => {
                          const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                          if (val !== (selectedVideoClip.fadeOutMs ?? 0)) {
                            handleUpdateVideoClip({ effects: { fade_out_ms: val } })
                          }
                        }}
                        className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                      />
                      <span className="text-xs text-gray-500 ml-1">ms</span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="3000"
                    step="100"
                    value={selectedVideoClip.fadeOutMs ?? 0}
                    onChange={(e) => handleUpdateVideoClipLocal({ effects: { fade_out_ms: parseInt(e.target.value) } })}
                    onMouseUp={(e) => handleUpdateVideoClip({ effects: { fade_out_ms: parseInt(e.currentTarget.value) } })}
                    onTouchEnd={(e) => handleUpdateVideoClip({ effects: { fade_out_ms: parseInt((e.target as HTMLInputElement).value) } })}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                  />
                </div>
              </div>

              {/* Chroma Key - Show for video clips only */}
              {(() => {
                const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
                if (clipAsset?.type !== 'video') return null

                // Default chroma key values
                const chromaKey = selectedVideoClip.effects.chroma_key || {
                  enabled: false,
                  color: '#00FF00',
                  similarity: 0.05,
                  blend: 0.0
                }

                return (
                  <div className="pt-4 border-t border-gray-700">
                    <div className="flex items-center justify-between mb-2">
                      <label className="text-xs text-gray-500">クロマキー</label>
                      <button
                        onClick={() => {
                          // Optimistic update: first update local state immediately
                          const newChromaKey = {
                            enabled: !chromaKey.enabled,
                            color: chromaKey.color,
                            similarity: chromaKey.similarity,
                            blend: chromaKey.blend
                          }
                          handleUpdateVideoClipLocal({
                            effects: { chroma_key: newChromaKey }
                          })
                          // Then persist to API in background
                          handleUpdateVideoClip({
                            effects: { chroma_key: newChromaKey }
                          })
                        }}
                        className={`px-2 py-0.5 text-xs rounded cursor-pointer transition-colors ${
                          chromaKey.enabled
                            ? 'bg-green-600 text-white hover:bg-green-700'
                            : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
                        }`}
                      >
                        {chromaKey.enabled ? 'ON' : 'OFF'}
                      </button>
                    </div>
                    <div className="space-y-3">
                      <div className="space-y-2">
                        {/* Row 1: Color picker and hex input */}
                        <div className="flex items-center gap-2">
                          <label className="text-xs text-gray-600 w-16">色</label>
                          <input
                            type="color"
                            value={chromaKey.color}
                            onFocus={() => {
                              // Store original color when editing starts
                              if (chromaColorBeforeEdit === null) {
                                setChromaColorBeforeEdit(chromaKey.color)
                              }
                            }}
                            onChange={(e) => {
                              // Store original color on first change if not already stored
                              if (chromaColorBeforeEdit === null) {
                                setChromaColorBeforeEdit(chromaKey.color)
                              }
                              // Local preview only - update timeline data for preview, no API call, no history
                              handleUpdateVideoClipLocal({
                                effects: { chroma_key: { ...chromaKey, color: e.target.value } }
                              })
                            }}
                            className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
                          />
                          <input
                            type="text"
                            value={chromaKey.color.toUpperCase()}
                            onFocus={() => {
                              // Store original color when editing starts
                              if (chromaColorBeforeEdit === null) {
                                setChromaColorBeforeEdit(chromaKey.color)
                              }
                            }}
                            onChange={(e) => {
                              let val = e.target.value.toUpperCase()
                              if (!val.startsWith('#')) val = '#' + val
                              // Allow partial input while typing
                              if (/^#[0-9A-F]{0,6}$/.test(val) || val === '#') {
                                // Only update if it's a complete valid color
                                if (/^#[0-9A-F]{6}$/.test(val)) {
                                  // Store original color on first valid change if not already stored
                                  if (chromaColorBeforeEdit === null) {
                                    setChromaColorBeforeEdit(chromaKey.color)
                                  }
                                  // Local preview only - update timeline data for preview, no API call, no history
                                  handleUpdateVideoClipLocal({
                                    effects: { chroma_key: { ...chromaKey, color: val } }
                                  })
                                }
                              }
                            }}
                            onKeyDown={(e) => {
                              e.stopPropagation()
                              // No Enter key handling - use Apply button instead
                            }}
                            onBlur={() => {
                              // No onBlur handling - use Apply button instead
                            }}
                            className="w-20 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded font-mono"
                            placeholder="#00FF00"
                          />
                          <button
                            onClick={() => {
                              // Auto-detect color from video corner (preview only)
                              const video = videoRefsMap.current.get(selectedVideoClip.clipId)
                              if (!video) return
                              const canvas = document.createElement('canvas')
                              canvas.width = video.videoWidth
                              canvas.height = video.videoHeight
                              const ctx = canvas.getContext('2d')
                              if (!ctx) return
                              try {
                                ctx.drawImage(video, 0, 0)
                                // Sample from top-left corner (10x10 average)
                                const imageData = ctx.getImageData(0, 0, 10, 10)
                                let r = 0, g = 0, b = 0
                                for (let i = 0; i < imageData.data.length; i += 4) {
                                  r += imageData.data[i]
                                  g += imageData.data[i + 1]
                                  b += imageData.data[i + 2]
                                }
                                const count = imageData.data.length / 4
                                r = Math.round(r / count)
                                g = Math.round(g / count)
                                b = Math.round(b / count)
                                const hex = '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('').toUpperCase()
                                // Store original color if not already stored
                                if (chromaColorBeforeEdit === null) {
                                  setChromaColorBeforeEdit(chromaKey.color)
                                }
                                // Local preview only - update timeline data for preview, no API call, no history
                                handleUpdateVideoClipLocal({
                                  effects: { chroma_key: { ...chromaKey, color: hex } }
                                })
                              } catch (err) {
                                console.error('Failed to sample color:', err)
                              }
                            }}
                            className="px-2 py-1 text-xs bg-gray-600 text-gray-300 rounded hover:bg-gray-500"
                            title="左上隅から色を自動取得"
                          >
                            自動
                          </button>
                          <button
                            onClick={async () => {
                              // Fetch raw frame (without chroma key processing) for color picking
                              if (!selectedVideoClip || !projectId) return
                              const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
                              if (!clipAsset || clipAsset.type !== 'video') return

                              setChromaRawFrameLoading(true)
                              setChromaPreviewError(null)
                              setChromaRawFrame(null)

                              try {
                                // Use skip_chroma_key=true to get raw frame without chroma key processing
                                const result = await aiV1Api.chromaKeyPreview(projectId, selectedVideoClip.clipId, {
                                  key_color: chromaKey.color,
                                  similarity: chromaKey.similarity,
                                  blend: chromaKey.blend,
                                  resolution: '640x360',
                                  skip_chroma_key: true,
                                })

                                if (result.frames.length > 0) {
                                  // Use the first frame (middle of clip) as the raw frame for picking
                                  setChromaRawFrame(result.frames[0])
                                  // Store original color if not already stored
                                  if (chromaColorBeforeEdit === null) {
                                    setChromaColorBeforeEdit(chromaKey.color)
                                  }
                                  setChromaPickerMode(true)
                                }
                              } catch (err) {
                                const message =
                                  (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error?.message
                                  || (err as Error).message
                                  || '生フレームの取得に失敗しました'
                                setChromaPreviewError(message)
                              } finally {
                                setChromaRawFrameLoading(false)
                              }
                            }}
                            disabled={chromaPreviewLoading || chromaApplyLoading || chromaRawFrameLoading}
                            className={`px-2 py-1 text-xs rounded ${
                              chromaPickerMode
                                ? 'bg-yellow-600 text-white'
                                : chromaPreviewLoading || chromaApplyLoading || chromaRawFrameLoading
                                  ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                                  : 'bg-purple-600 text-white hover:bg-purple-700'
                            }`}
                            title="元動画から色をピック（緑背景が残った状態）"
                          >
                            {chromaRawFrameLoading ? '取得中...' : 'スポイト'}
                          </button>
                        </div>
                        {/* Row 2: Cancel and Apply buttons */}
                        <div className="flex items-center gap-2 ml-16">
                          <button
                            onClick={() => {
                              // Cancel: restore original color (update timeline data for preview, no API call, no history)
                              if (chromaColorBeforeEdit !== null) {
                                handleUpdateVideoClipLocal({
                                  effects: { chroma_key: { ...chromaKey, color: chromaColorBeforeEdit } }
                                })
                                setChromaColorBeforeEdit(null)
                              }
                            }}
                            disabled={chromaColorBeforeEdit === null}
                            className={`px-2 py-1 text-xs rounded ${
                              chromaColorBeforeEdit === null
                                ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                                : 'bg-gray-600 text-gray-300 hover:bg-gray-500 cursor-pointer'
                            }`}
                            title="色の変更をキャンセル"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={() => {
                              // Apply: save to API and history
                              handleUpdateVideoClip({
                                effects: { chroma_key: { ...chromaKey, color: chromaKey.color } }
                              })
                              setChromaColorBeforeEdit(null)
                            }}
                            disabled={chromaColorBeforeEdit === null}
                            className={`px-2 py-1 text-xs rounded ${
                              chromaColorBeforeEdit === null
                                ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                                : 'bg-green-600 text-white hover:bg-green-700 cursor-pointer'
                            }`}
                            title="色の変更を確定"
                          >
                            Apply
                          </button>
                        </div>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">類似度</label>
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="1"
                            key={`sim-${chromaKey.similarity}`}
                            defaultValue={Math.round(chromaKey.similarity * 100)}
                            onKeyDown={(e) => {
                              e.stopPropagation()
                              if (e.key === 'Enter') {
                                const val = Math.max(0, Math.min(100, parseInt(e.currentTarget.value) || 0)) / 100
                                handleUpdateVideoClip({
                                  effects: { chroma_key: { ...chromaKey, similarity: val } }
                                })
                                e.currentTarget.blur()
                              }
                            }}
                            onBlur={(e) => {
                              const val = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100
                              if (val !== chromaKey.similarity) {
                                handleUpdateVideoClip({
                                  effects: { chroma_key: { ...chromaKey, similarity: val } }
                                })
                              }
                            }}
                            className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                          />
                          <span className="text-xs text-gray-500 ml-1">%</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.01"
                          value={chromaKey.similarity}
                          onChange={(e) => handleUpdateVideoClipLocal({
                            effects: { chroma_key: { ...chromaKey, similarity: parseFloat(e.target.value) } }
                          })}
                          onMouseUp={(e) => handleUpdateVideoClip({
                            effects: { chroma_key: { ...chromaKey, similarity: parseFloat(e.currentTarget.value) } }
                          })}
                          onTouchEnd={(e) => handleUpdateVideoClip({
                            effects: { chroma_key: { ...chromaKey, similarity: parseFloat((e.target as HTMLInputElement).value) } }
                          })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">ブレンド</label>
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="1"
                            key={`blend-${chromaKey.blend}`}
                            defaultValue={Math.round(chromaKey.blend * 100)}
                            onKeyDown={(e) => {
                              e.stopPropagation()
                              if (e.key === 'Enter') {
                                const val = Math.max(0, Math.min(100, parseInt(e.currentTarget.value) || 0)) / 100
                                handleUpdateVideoClip({
                                  effects: { chroma_key: { ...chromaKey, blend: val } }
                                })
                                e.currentTarget.blur()
                              }
                            }}
                            onBlur={(e) => {
                              const val = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100
                              if (val !== chromaKey.blend) {
                                handleUpdateVideoClip({
                                  effects: { chroma_key: { ...chromaKey, blend: val } }
                                })
                              }
                            }}
                            className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                          />
                          <span className="text-xs text-gray-500 ml-1">%</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.01"
                          value={chromaKey.blend}
                          onChange={(e) => handleUpdateVideoClipLocal({
                            effects: { chroma_key: { ...chromaKey, blend: parseFloat(e.target.value) } }
                          })}
                          onMouseUp={(e) => handleUpdateVideoClip({
                            effects: { chroma_key: { ...chromaKey, blend: parseFloat(e.currentTarget.value) } }
                          })}
                          onTouchEnd={(e) => handleUpdateVideoClip({
                            effects: { chroma_key: { ...chromaKey, blend: parseFloat((e.target as HTMLInputElement).value) } }
                          })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div className="pt-2 space-y-2">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleChromaKeyPreview()}
                            disabled={chromaPreviewLoading || chromaApplyLoading}
                            className={`px-2 py-1 text-xs rounded ${
                              chromaPreviewLoading || chromaApplyLoading
                                ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                                : 'bg-blue-600 text-white hover:bg-blue-700'
                            }`}
                          >
                            {chromaPreviewLoading ? 'プレビュー中...' : '実際のレンダリング処理でのプレビュー'}
                          </button>
                          <button
                            onClick={handleChromaKeyApply}
                            disabled={chromaApplyLoading || chromaPreviewLoading}
                            className={`px-2 py-1 text-xs rounded ${
                              chromaApplyLoading || chromaPreviewLoading
                                ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                                : 'bg-green-600 text-white hover:bg-green-700'
                            }`}
                          >
                            {chromaApplyLoading ? '処理中...' : 'クロマキー処理'}
                          </button>
                        </div>
                        {chromaPreviewError && (
                          <div className="text-xs text-red-400">{chromaPreviewError}</div>
                        )}
                        {chromaPreviewFrames.length > 0 && (
                          <div className="space-y-2">
                            <div className="text-[10px] text-gray-500">プレビュー（5点）- クリックで拡大 / 下端ドラッグでリサイズ</div>
                            {/* Thumbnail grid with resize handle */}
                            <div className="relative">
                              <div
                                className="grid grid-cols-5 gap-1"
                                style={{
                                  gridTemplateColumns: `repeat(5, ${chromaPreviewSize}px)`,
                                }}
                              >
                                {chromaPreviewFrames.map((frame, index) => {
                                  const imageFormat = frame.image_format || 'jpeg'
                                  const mimeType = imageFormat === 'png' ? 'image/png' : 'image/jpeg'
                                  return (
                                    <div
                                      key={`${frame.time_ms}-${frame.resolution}`}
                                      className="space-y-0.5 cursor-pointer"
                                      onClick={() => setChromaPreviewSelectedIndex(index)}
                                    >
                                      <div
                                        style={{
                                          width: chromaPreviewSize,
                                          // Checkerboard background for transparent PNG
                                          background: imageFormat === 'png'
                                            ? 'repeating-conic-gradient(#4a4a4a 0% 25%, #3a3a3a 0% 50%) 50% / 16px 16px'
                                            : undefined,
                                        }}
                                        className={`rounded border-2 overflow-hidden transition-all ${
                                          chromaPreviewSelectedIndex === index
                                            ? 'border-blue-500 ring-1 ring-blue-400'
                                            : 'border-gray-700 hover:border-gray-500'
                                        }`}
                                      >
                                        <img
                                          src={`data:${mimeType};base64,${frame.frame_base64}`}
                                          alt={`preview-${frame.time_ms}`}
                                          style={{ width: '100%', height: 'auto', display: 'block' }}
                                        />
                                      </div>
                                      <div className="text-[9px] text-gray-500 text-center">
                                        {(frame.time_ms / 1000).toFixed(2)}s
                                      </div>
                                    </div>
                                  )
                                })}
                              </div>
                              {/* Resize handle at bottom */}
                              <div
                                className="absolute left-0 right-0 h-3 cursor-ns-resize flex items-center justify-center group hover:bg-gray-700/50 rounded-b"
                                style={{ bottom: -12 }}
                                onMouseDown={handleChromaPreviewResizeStart}
                              >
                                <div className="w-8 h-1 bg-gray-600 rounded group-hover:bg-blue-500 transition-colors" />
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })()}

              {/* Crop - Show for video and image clips only */}
              {(() => {
                const clipAsset = assets.find(a => a.id === selectedVideoClip.assetId)
                if (!clipAsset || (clipAsset.type !== 'video' && clipAsset.type !== 'image')) return null

                const crop = selectedVideoClip.crop || { top: 0, right: 0, bottom: 0, left: 0 }

                return (
                  <div className="pt-4 border-t border-gray-700">
                    <label className="block text-xs text-gray-500 mb-3">クロップ</label>
                    <div className="space-y-3">
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">上</label>
                          <div className="flex items-center">
                            <input
                              type="number"
                              min="0"
                              max="50"
                              step="1"
                              key={`crop-top-${crop.top}`}
                              defaultValue={Math.round(crop.top * 100)}
                              onKeyDown={(e) => {
                                e.stopPropagation()
                                if (e.key === 'Enter') {
                                  const val = Math.max(0, Math.min(50, parseInt(e.currentTarget.value) || 0)) / 100
                                  handleUpdateVideoClip({ crop: { ...crop, top: val } })
                                  e.currentTarget.blur()
                                }
                              }}
                              onBlur={(e) => {
                                const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                                if (val !== crop.top) {
                                  handleUpdateVideoClip({ crop: { ...crop, top: val } })
                                }
                              }}
                              className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                            />
                            <span className="text-xs text-gray-500 ml-1">%</span>
                          </div>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="0.5"
                          step="0.01"
                          value={crop.top}
                          onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, top: parseFloat(e.target.value) } })}
                          onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, top: parseFloat(e.currentTarget.value) } })}
                          onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, top: parseFloat((e.target as HTMLInputElement).value) } })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">下</label>
                          <div className="flex items-center">
                            <input
                              type="number"
                              min="0"
                              max="50"
                              step="1"
                              key={`crop-bottom-${crop.bottom}`}
                              defaultValue={Math.round(crop.bottom * 100)}
                              onKeyDown={(e) => {
                                e.stopPropagation()
                                if (e.key === 'Enter') {
                                  const val = Math.max(0, Math.min(50, parseInt(e.currentTarget.value) || 0)) / 100
                                  handleUpdateVideoClip({ crop: { ...crop, bottom: val } })
                                  e.currentTarget.blur()
                                }
                              }}
                              onBlur={(e) => {
                                const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                                if (val !== crop.bottom) {
                                  handleUpdateVideoClip({ crop: { ...crop, bottom: val } })
                                }
                              }}
                              className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                            />
                            <span className="text-xs text-gray-500 ml-1">%</span>
                          </div>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="0.5"
                          step="0.01"
                          value={crop.bottom}
                          onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, bottom: parseFloat(e.target.value) } })}
                          onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, bottom: parseFloat(e.currentTarget.value) } })}
                          onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, bottom: parseFloat((e.target as HTMLInputElement).value) } })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">左</label>
                          <div className="flex items-center">
                            <input
                              type="number"
                              min="0"
                              max="50"
                              step="1"
                              key={`crop-left-${crop.left}`}
                              defaultValue={Math.round(crop.left * 100)}
                              onKeyDown={(e) => {
                                e.stopPropagation()
                                if (e.key === 'Enter') {
                                  const val = Math.max(0, Math.min(50, parseInt(e.currentTarget.value) || 0)) / 100
                                  handleUpdateVideoClip({ crop: { ...crop, left: val } })
                                  e.currentTarget.blur()
                                }
                              }}
                              onBlur={(e) => {
                                const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                                if (val !== crop.left) {
                                  handleUpdateVideoClip({ crop: { ...crop, left: val } })
                                }
                              }}
                              className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                            />
                            <span className="text-xs text-gray-500 ml-1">%</span>
                          </div>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="0.5"
                          step="0.01"
                          value={crop.left}
                          onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, left: parseFloat(e.target.value) } })}
                          onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, left: parseFloat(e.currentTarget.value) } })}
                          onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, left: parseFloat((e.target as HTMLInputElement).value) } })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-xs text-gray-600">右</label>
                          <div className="flex items-center">
                            <input
                              type="number"
                              min="0"
                              max="50"
                              step="1"
                              key={`crop-right-${crop.right}`}
                              defaultValue={Math.round(crop.right * 100)}
                              onKeyDown={(e) => {
                                e.stopPropagation()
                                if (e.key === 'Enter') {
                                  const val = Math.max(0, Math.min(50, parseInt(e.currentTarget.value) || 0)) / 100
                                  handleUpdateVideoClip({ crop: { ...crop, right: val } })
                                  e.currentTarget.blur()
                                }
                              }}
                              onBlur={(e) => {
                                const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                                if (val !== crop.right) {
                                  handleUpdateVideoClip({ crop: { ...crop, right: val } })
                                }
                              }}
                              className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                            />
                            <span className="text-xs text-gray-500 ml-1">%</span>
                          </div>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="0.5"
                          step="0.01"
                          value={crop.right}
                          onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, right: parseFloat(e.target.value) } })}
                          onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, right: parseFloat(e.currentTarget.value) } })}
                          onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, right: parseFloat((e.target as HTMLInputElement).value) } })}
                          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      {(crop.top > 0 || crop.bottom > 0 || crop.left > 0 || crop.right > 0) && (
                        <button
                          onClick={() => handleUpdateVideoClip({ crop: { top: 0, right: 0, bottom: 0, left: 0 } })}
                          className="w-full px-2 py-1 text-xs bg-gray-600 text-gray-300 rounded hover:bg-gray-500"
                        >
                          クロップをリセット
                        </button>
                      )}
                    </div>
                  </div>
                )
              })()}

              {/* Shape Properties */}
              {selectedVideoClip.shape && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-3">図形プロパティ</label>
                  <div className="space-y-3">
                    {/* Fill toggle and color (not for lines) */}
                    {selectedVideoClip.shape.type !== 'line' && (
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <label className="text-xs text-gray-400">塗りつぶし</label>
                          <button
                            onClick={() => handleUpdateShape({ filled: !selectedVideoClip.shape?.filled })}
                            className={`px-2 py-0.5 text-xs rounded cursor-pointer transition-colors ${
                              selectedVideoClip.shape.filled
                                ? 'bg-green-600 text-white hover:bg-green-700'
                                : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
                            }`}
                          >
                            {selectedVideoClip.shape.filled ? 'ON' : 'OFF'}
                          </button>
                        </div>
                        {selectedVideoClip.shape.filled && (
                          <div className="flex items-center gap-2">
                            <label className="text-xs text-gray-600 w-16">塗り色</label>
                            <input
                              type="color"
                              value={selectedVideoClip.shape.fillColor === 'transparent' ? '#000000' : selectedVideoClip.shape.fillColor}
                              onChange={(e) => handleUpdateShape({ fillColor: e.target.value })}
                              className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
                            />
                            <span className="text-xs text-gray-400">{selectedVideoClip.shape.fillColor}</span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Stroke color */}
                    <div className="flex items-center gap-2">
                      <label className="text-xs text-gray-600 w-16">線の色</label>
                      <input
                        type="color"
                        value={selectedVideoClip.shape.strokeColor}
                        onChange={(e) => handleUpdateShape({ strokeColor: e.target.value })}
                        className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
                      />
                      <span className="text-xs text-gray-400">{selectedVideoClip.shape.strokeColor}</span>
                    </div>

                    {/* Stroke width */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs text-gray-600">線の太さ</label>
                        <div className="flex items-center">
                          <input
                            type="number"
                            min="0"
                            max="20"
                            step="1"
                            key={`sw-${selectedVideoClip.shape.strokeWidth}`}
                            defaultValue={selectedVideoClip.shape.strokeWidth}
                            onKeyDown={(e) => {
                              e.stopPropagation()
                              if (e.key === 'Enter') {
                                const val = Math.max(0, Math.min(20, parseInt(e.currentTarget.value) || 0))
                                handleUpdateShape({ strokeWidth: val })
                                e.currentTarget.blur()
                              }
                            }}
                            onBlur={(e) => {
                              const val = Math.max(0, Math.min(20, parseInt(e.target.value) || 0))
                              if (val !== selectedVideoClip.shape?.strokeWidth) {
                                handleUpdateShape({ strokeWidth: val })
                              }
                            }}
                            className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                          />
                          <span className="text-xs text-gray-500 ml-1">px</span>
                        </div>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="20"
                        step="1"
                        value={selectedVideoClip.shape.strokeWidth}
                        onChange={(e) => handleUpdateShapeLocal({ strokeWidth: parseInt(e.target.value) })}
                        onMouseUp={(e) => handleUpdateShape({ strokeWidth: parseInt(e.currentTarget.value) })}
                        onTouchEnd={(e) => handleUpdateShape({ strokeWidth: parseInt((e.target as HTMLInputElement).value) })}
                        className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>

                    {/* Shape size */}
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-xs text-gray-600">幅</label>
                        <input
                          type="number"
                          value={selectedVideoClip.shape.width}
                          onChange={(e) => handleUpdateShape({ width: Math.max(10, parseInt(e.target.value) || 10) })}
                          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-gray-600">高さ</label>
                        <input
                          type="number"
                          value={selectedVideoClip.shape.height}
                          onChange={(e) => handleUpdateShape({ height: Math.max(10, parseInt(e.target.value) || 10) })}
                          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                        />
                      </div>
                    </div>

                    {/* Shape fade in/out */}
                    <div className="pt-3 border-t border-gray-600">
                      <label className="block text-xs text-gray-500 mb-2">フェード効果</label>
                      <div className="space-y-2">
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <label className="text-xs text-gray-600">フェードイン</label>
                            <div className="flex items-center">
                              <input
                                type="number"
                                min="0"
                                max="3000"
                                step="100"
                                key={`fi-${selectedVideoClip.fadeInMs || 0}`}
                                defaultValue={selectedVideoClip.fadeInMs || 0}
                                onKeyDown={(e) => {
                                  e.stopPropagation()
                                  if (e.key === 'Enter') {
                                    const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                                    handleUpdateShapeFade({ fadeInMs: val })
                                    e.currentTarget.blur()
                                  }
                                }}
                                onBlur={(e) => {
                                  const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                                  if (val !== (selectedVideoClip.fadeInMs || 0)) {
                                    handleUpdateShapeFade({ fadeInMs: val })
                                  }
                                }}
                                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                              />
                              <span className="text-xs text-gray-500 ml-1">ms</span>
                            </div>
                          </div>
                          <input
                            type="range"
                            min="0"
                            max="3000"
                            step="100"
                            value={selectedVideoClip.fadeInMs || 0}
                            onChange={(e) => handleUpdateShapeFadeLocal({ fadeInMs: parseInt(e.target.value) })}
                            onMouseUp={(e) => handleUpdateShapeFade({ fadeInMs: parseInt(e.currentTarget.value) })}
                            onTouchEnd={(e) => handleUpdateShapeFade({ fadeInMs: parseInt((e.target as HTMLInputElement).value) })}
                            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                          />
                          <div className="w-full h-1.5 bg-gray-700 rounded-lg overflow-hidden mt-1">
                            <div
                              className="h-full bg-green-500 transition-all"
                              style={{ width: `${Math.min(100, ((selectedVideoClip.fadeInMs || 0) / 3000) * 100)}%` }}
                            />
                          </div>
                        </div>
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <label className="text-xs text-gray-600">フェードアウト</label>
                            <div className="flex items-center">
                              <input
                                type="number"
                                min="0"
                                max="3000"
                                step="100"
                                key={`fo-${selectedVideoClip.fadeOutMs || 0}`}
                                defaultValue={selectedVideoClip.fadeOutMs || 0}
                                onKeyDown={(e) => {
                                  e.stopPropagation()
                                  if (e.key === 'Enter') {
                                    const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                                    handleUpdateShapeFade({ fadeOutMs: val })
                                    e.currentTarget.blur()
                                  }
                                }}
                                onBlur={(e) => {
                                  const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                                  if (val !== (selectedVideoClip.fadeOutMs || 0)) {
                                    handleUpdateShapeFade({ fadeOutMs: val })
                                  }
                                }}
                                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                              />
                              <span className="text-xs text-gray-500 ml-1">ms</span>
                            </div>
                          </div>
                          <input
                            type="range"
                            min="0"
                            max="3000"
                            step="100"
                            value={selectedVideoClip.fadeOutMs || 0}
                            onChange={(e) => handleUpdateShapeFadeLocal({ fadeOutMs: parseInt(e.target.value) })}
                            onMouseUp={(e) => handleUpdateShapeFade({ fadeOutMs: parseInt(e.currentTarget.value) })}
                            onTouchEnd={(e) => handleUpdateShapeFade({ fadeOutMs: parseInt((e.target as HTMLInputElement).value) })}
                            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                          />
                          <div className="w-full h-1.5 bg-gray-700 rounded-lg overflow-hidden mt-1">
                            <div
                              className="h-full bg-red-500 transition-all"
                              style={{ width: `${Math.min(100, ((selectedVideoClip.fadeOutMs || 0) / 3000) * 100)}%` }}
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Text/Telop Properties */}
              {selectedVideoClip.textContent !== undefined && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-3">テロップ設定</label>

                  {/* Text Content - IME対応 + debounce */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">テキスト</label>
                    <textarea
                      value={localTextContent}
                      onChange={(e) => {
                        const value = e.target.value
                        setLocalTextContent(value)
                        // Debounce update when not composing
                        if (!isComposing) {
                          if (textDebounceRef.current) {
                            clearTimeout(textDebounceRef.current)
                          }
                          textDebounceRef.current = setTimeout(() => {
                            handleUpdateVideoClip({ text_content: value })
                          }, 300)
                        }
                      }}
                      onCompositionStart={() => setIsComposing(true)}
                      onCompositionEnd={(e) => {
                        setIsComposing(false)
                        const value = (e.target as HTMLTextAreaElement).value
                        // Clear any pending debounce and update immediately
                        if (textDebounceRef.current) {
                          clearTimeout(textDebounceRef.current)
                        }
                        handleUpdateVideoClip({ text_content: value })
                      }}
                      onBlur={(e) => {
                        // Clear debounce and save immediately on blur
                        if (textDebounceRef.current) {
                          clearTimeout(textDebounceRef.current)
                        }
                        handleUpdateVideoClip({ text_content: e.target.value })
                      }}
                      className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded resize-none"
                      rows={3}
                      placeholder="テキストを入力..."
                    />
                  </div>

                  {/* Font Family */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">フォント</label>
                    <select
                      value={selectedVideoClip.textStyle?.fontFamily || 'Noto Sans JP'}
                      onChange={(e) => {
                        handleUpdateVideoClipLocal({ text_style: { fontFamily: e.target.value } })
                        handleUpdateVideoClip({ text_style: { fontFamily: e.target.value } })
                      }}
                      className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                    >
                      <option value="Noto Sans JP">Noto Sans JP</option>
                      <option value="Noto Serif JP">Noto Serif JP</option>
                      <option value="M PLUS 1p">M PLUS 1p</option>
                      <option value="M PLUS Rounded 1c">M PLUS Rounded 1c</option>
                      <option value="Kosugi Maru">Kosugi Maru</option>
                      <option value="Sawarabi Gothic">Sawarabi Gothic</option>
                      <option value="Sawarabi Mincho">Sawarabi Mincho</option>
                      <option value="BIZ UDPGothic">BIZ UDPGothic</option>
                      <option value="Zen Maru Gothic">Zen Maru Gothic</option>
                      <option value="Shippori Mincho">Shippori Mincho</option>
                    </select>
                  </div>

                  {/* Font Size */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">
                      サイズ (px)
                    </label>
                    <div className="flex gap-2 items-center">
                      <input
                        type="range"
                        min="12"
                        max="500"
                        value={selectedVideoClip.textStyle?.fontSize || 48}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { fontSize: parseInt(e.target.value) || 48 } })}
                        onMouseUp={(e) => handleUpdateVideoClip({ text_style: { fontSize: parseInt(e.currentTarget.value) || 48 } })}
                        onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { fontSize: parseInt((e.target as HTMLInputElement).value) || 48 } })}
                        className="flex-1 accent-primary-500"
                      />
                      <input
                        type="number"
                        min="12"
                        max="500"
                        value={selectedVideoClip.textStyle?.fontSize || 48}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { fontSize: parseInt(e.target.value) || 48 } })}
                        onBlur={(e) => handleUpdateVideoClip({ text_style: { fontSize: parseInt(e.target.value) || 48 } })}
                        className="w-16 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
                      />
                    </div>
                  </div>

                  {/* Font Weight & Style */}
                  <div className="mb-3 flex gap-2">
                    <button
                      onClick={() => {
                        const newWeight = selectedVideoClip.textStyle?.fontWeight === 'bold' ? 'normal' : 'bold'
                        handleUpdateVideoClipLocal({ text_style: { fontWeight: newWeight } })
                        handleUpdateVideoClip({ text_style: { fontWeight: newWeight } })
                      }}
                      className={`flex-1 px-2 py-1 text-sm rounded ${
                        selectedVideoClip.textStyle?.fontWeight === 'bold'
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-700 text-gray-400'
                      }`}
                    >
                      <strong>B</strong>
                    </button>
                    <button
                      onClick={() => {
                        const newStyle = selectedVideoClip.textStyle?.fontStyle === 'italic' ? 'normal' : 'italic'
                        handleUpdateVideoClipLocal({ text_style: { fontStyle: newStyle } })
                        handleUpdateVideoClip({ text_style: { fontStyle: newStyle } })
                      }}
                      className={`flex-1 px-2 py-1 text-sm rounded ${
                        selectedVideoClip.textStyle?.fontStyle === 'italic'
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-700 text-gray-400'
                      }`}
                    >
                      <em>I</em>
                    </button>
                  </div>

                  {/* Text Color */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">文字色</label>
                    <div className="flex gap-2 items-center">
                      <input
                        type="color"
                        value={selectedVideoClip.textStyle?.color || '#ffffff'}
                        onChange={(e) => {
                          handleUpdateVideoClipLocal({ text_style: { color: e.target.value } })
                          handleUpdateVideoClip({ text_style: { color: e.target.value } })
                        }}
                        className="w-8 h-8 rounded cursor-pointer border border-gray-600"
                      />
                      <input
                        type="text"
                        value={selectedVideoClip.textStyle?.color || '#ffffff'}
                        onChange={(e) => handleUpdateVideoClip({ text_style: { color: e.target.value } })}
                        className="flex-1 bg-gray-700 text-white text-xs px-2 py-1 rounded font-mono"
                      />
                    </div>
                  </div>

                  {/* Background Color */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">背景色（テロップ帯）</label>
                    <div className="flex gap-2 items-center mb-2">
                      <input
                        type="color"
                        value={selectedVideoClip.textStyle?.backgroundColor === 'transparent' ? '#000000' : (selectedVideoClip.textStyle?.backgroundColor || '#000000')}
                        onChange={(e) => {
                          handleUpdateVideoClipLocal({ text_style: { backgroundColor: e.target.value, backgroundOpacity: selectedVideoClip.textStyle?.backgroundOpacity ?? 1 } })
                          handleUpdateVideoClip({ text_style: { backgroundColor: e.target.value, backgroundOpacity: selectedVideoClip.textStyle?.backgroundOpacity ?? 1 } })
                        }}
                        className="w-8 h-8 rounded cursor-pointer border border-gray-600"
                      />
                      <input
                        type="text"
                        value={selectedVideoClip.textStyle?.backgroundColor || 'transparent'}
                        onChange={(e) => {
                          handleUpdateVideoClipLocal({ text_style: { backgroundColor: e.target.value } })
                          handleUpdateVideoClip({ text_style: { backgroundColor: e.target.value } })
                        }}
                        className="flex-1 bg-gray-700 text-white text-xs px-2 py-1 rounded font-mono"
                        placeholder="#000000"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-400 w-12">透明度</span>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="5"
                        value={Math.round((selectedVideoClip.textStyle?.backgroundOpacity ?? 0.3) * 100)}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { backgroundOpacity: parseInt(e.target.value) / 100 } })}
                        onMouseUp={(e) => handleUpdateVideoClip({ text_style: { backgroundOpacity: parseInt(e.currentTarget.value) / 100 } })}
                        onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { backgroundOpacity: parseInt((e.target as HTMLInputElement).value) / 100 } })}
                        className="flex-1 accent-primary-500"
                      />
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="5"
                        value={Math.round((selectedVideoClip.textStyle?.backgroundOpacity ?? 0.3) * 100)}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { backgroundOpacity: Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100 } })}
                        onBlur={(e) => handleUpdateVideoClip({ text_style: { backgroundOpacity: Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100 } })}
                        className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
                      />
                      <span className="text-xs text-gray-400">%</span>
                    </div>
                  </div>

                  {/* Stroke (Outline) */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">縁取り</label>
                    <div className="flex gap-2 items-center">
                      <input
                        type="color"
                        value={selectedVideoClip.textStyle?.strokeColor || '#000000'}
                        onChange={(e) => {
                          handleUpdateVideoClipLocal({ text_style: { strokeColor: e.target.value } })
                          handleUpdateVideoClip({ text_style: { strokeColor: e.target.value } })
                        }}
                        className="w-8 h-8 rounded cursor-pointer border border-gray-600"
                      />
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        value={selectedVideoClip.textStyle?.strokeWidth || 0}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { strokeWidth: parseFloat(e.target.value) } })}
                        onMouseUp={(e) => handleUpdateVideoClip({ text_style: { strokeWidth: parseFloat(e.currentTarget.value) } })}
                        onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { strokeWidth: parseFloat((e.target as HTMLInputElement).value) } })}
                        className="flex-1 accent-primary-500"
                      />
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        value={selectedVideoClip.textStyle?.strokeWidth || 0}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { strokeWidth: Math.max(0, Math.min(100, parseFloat(e.target.value) || 0)) } })}
                        onBlur={(e) => handleUpdateVideoClip({ text_style: { strokeWidth: Math.max(0, Math.min(100, parseFloat(e.target.value) || 0)) } })}
                        className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
                      />
                      <span className="text-xs text-gray-400">px</span>
                    </div>
                  </div>

                  {/* Text Alignment */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">配置</label>
                    <div className="flex gap-1">
                      {(['left', 'center', 'right'] as const).map((align) => (
                        <button
                          key={align}
                          onClick={() => {
                            handleUpdateVideoClipLocal({ text_style: { textAlign: align } })
                            handleUpdateVideoClip({ text_style: { textAlign: align } })
                          }}
                          className={`flex-1 px-2 py-1 text-xs rounded ${
                            (selectedVideoClip.textStyle?.textAlign || 'center') === align
                              ? 'bg-primary-600 text-white'
                              : 'bg-gray-700 text-gray-400'
                          }`}
                        >
                          {align === 'left' ? '左' : align === 'center' ? '中央' : '右'}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Line Height */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">行間</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min="0.5"
                        max="3"
                        step="0.1"
                        value={selectedVideoClip.textStyle?.lineHeight || 1.4}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { lineHeight: parseFloat(e.target.value) } })}
                        onMouseUp={(e) => handleUpdateVideoClip({ text_style: { lineHeight: parseFloat(e.currentTarget.value) } })}
                        onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { lineHeight: parseFloat((e.target as HTMLInputElement).value) } })}
                        className="flex-1 accent-primary-500"
                      />
                      <input
                        type="number"
                        min="0.5"
                        max="5"
                        step="0.1"
                        value={selectedVideoClip.textStyle?.lineHeight || 1.4}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { lineHeight: Math.max(0.5, Math.min(5, parseFloat(e.target.value) || 1.4)) } })}
                        onBlur={(e) => handleUpdateVideoClip({ text_style: { lineHeight: Math.max(0.5, Math.min(5, parseFloat(e.target.value) || 1.4)) } })}
                        className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
                      />
                    </div>
                  </div>

                  {/* Letter Spacing */}
                  <div className="mb-3">
                    <label className="block text-xs text-gray-500 mb-1">字間</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min="-5"
                        max="20"
                        step="1"
                        value={selectedVideoClip.textStyle?.letterSpacing || 0}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { letterSpacing: parseInt(e.target.value) } })}
                        onMouseUp={(e) => handleUpdateVideoClip({ text_style: { letterSpacing: parseInt(e.currentTarget.value) } })}
                        onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { letterSpacing: parseInt((e.target as HTMLInputElement).value) } })}
                        className="flex-1 accent-primary-500"
                      />
                      <input
                        type="number"
                        min="-10"
                        max="50"
                        step="1"
                        value={selectedVideoClip.textStyle?.letterSpacing || 0}
                        onChange={(e) => handleUpdateVideoClipLocal({ text_style: { letterSpacing: Math.max(-10, Math.min(50, parseInt(e.target.value) || 0)) } })}
                        onBlur={(e) => handleUpdateVideoClip({ text_style: { letterSpacing: Math.max(-10, Math.min(50, parseInt(e.target.value) || 0)) } })}
                        className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
                      />
                      <span className="text-xs text-gray-400">px</span>
                    </div>
                  </div>

                </div>
              )}

              {/* Asset ID */}
              {selectedVideoClip.assetId && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-1">アセットID</label>
                  <p className="text-gray-400 text-xs font-mono break-all">{selectedVideoClip.assetId}</p>
                </div>
              )}

              {/* Delete Button */}
              <div className="pt-4 border-t border-gray-700">
                <button
                  onClick={handleDeleteVideoClip}
                  className="w-full px-3 py-2 text-sm text-red-400 hover:text-white hover:bg-red-600 border border-red-600 rounded transition-colors"
                >
                  クリップを削除
                </button>
              </div>
            </div>
          ) : selectedClip ? (
            <div className="space-y-4">
              {/* Audio Clip Name */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">クリップ名</label>
                <p className="text-white text-sm truncate">{selectedClip.assetName}</p>
              </div>

              {/* Track Type */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">トラック</label>
                <span className={`inline-block px-2 py-0.5 text-xs rounded ${
                  selectedClip.trackType === 'narration'
                    ? 'bg-green-600 text-white'
                    : selectedClip.trackType === 'bgm'
                    ? 'bg-blue-600 text-white'
                    : 'bg-yellow-600 text-white'
                }`}>
                  {selectedClip.trackType === 'narration' ? 'ナレーション' :
                   selectedClip.trackType === 'bgm' ? 'BGM' : 'SE'}
                </span>
              </div>

              {/* Duration */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">長さ</label>
                <p className="text-white text-sm">
                  {Math.floor(selectedClip.durationMs / 60000)}:
                  {Math.floor((selectedClip.durationMs % 60000) / 1000).toString().padStart(2, '0')}
                  .{Math.floor((selectedClip.durationMs % 1000) / 10).toString().padStart(2, '0')}
                </p>
              </div>

              {/* Start Time */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">開始位置 (ms)</label>
                <input
                  type="number"
                  min="0"
                  step="100"
                  value={localAudioProps.startMs}
                  onChange={(e) => setLocalAudioProps(prev => ({ ...prev, startMs: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = Math.max(0, parseInt(localAudioProps.startMs) || 0)
                      setLocalAudioProps(prev => ({ ...prev, startMs: String(val) }))
                      handleUpdateAudioClip({ start_ms: val })
                    }
                  }}
                  onBlur={() => {
                    const val = Math.max(0, parseInt(localAudioProps.startMs) || 0)
                    setLocalAudioProps(prev => ({ ...prev, startMs: String(val) }))
                    handleUpdateAudioClip({ start_ms: val })
                  }}
                  className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
                />
              </div>

              {/* Volume */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">音量 (%)</label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  value={localAudioProps.volume}
                  onChange={(e) => setLocalAudioProps(prev => ({ ...prev, volume: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = Math.max(0, Math.min(100, parseInt(localAudioProps.volume) || 0))
                      setLocalAudioProps(prev => ({ ...prev, volume: String(val) }))
                      handleUpdateAudioClip({ volume: val / 100 })
                    }
                  }}
                  onBlur={() => {
                    const val = Math.max(0, Math.min(100, parseInt(localAudioProps.volume) || 0))
                    setLocalAudioProps(prev => ({ ...prev, volume: String(val) }))
                    handleUpdateAudioClip({ volume: val / 100 })
                  }}
                  className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
                />
              </div>

              {/* Fade In */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">フェードイン (ms)</label>
                <input
                  type="number"
                  min="0"
                  max="10000"
                  step="100"
                  value={localAudioProps.fadeInMs}
                  onChange={(e) => setLocalAudioProps(prev => ({ ...prev, fadeInMs: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = Math.max(0, parseInt(localAudioProps.fadeInMs) || 0)
                      setLocalAudioProps(prev => ({ ...prev, fadeInMs: String(val) }))
                      handleUpdateAudioClip({ fade_in_ms: val })
                    }
                  }}
                  onBlur={() => {
                    const val = Math.max(0, parseInt(localAudioProps.fadeInMs) || 0)
                    setLocalAudioProps(prev => ({ ...prev, fadeInMs: String(val) }))
                    handleUpdateAudioClip({ fade_in_ms: val })
                  }}
                  className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
                />
              </div>

              {/* Fade Out */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">フェードアウト (ms)</label>
                <input
                  type="number"
                  min="0"
                  max="10000"
                  step="100"
                  value={localAudioProps.fadeOutMs}
                  onChange={(e) => setLocalAudioProps(prev => ({ ...prev, fadeOutMs: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = Math.max(0, parseInt(localAudioProps.fadeOutMs) || 0)
                      setLocalAudioProps(prev => ({ ...prev, fadeOutMs: String(val) }))
                      handleUpdateAudioClip({ fade_out_ms: val })
                    }
                  }}
                  onBlur={() => {
                    const val = Math.max(0, parseInt(localAudioProps.fadeOutMs) || 0)
                    setLocalAudioProps(prev => ({ ...prev, fadeOutMs: String(val) }))
                    handleUpdateAudioClip({ fade_out_ms: val })
                  }}
                  className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
                />
              </div>

              {/* Volume Envelope Section */}
              <div className="pt-4 border-t border-gray-700">
                <label className="block text-xs text-gray-500 mb-2">ボリュームエンベロープ</label>
                <div className="space-y-2">
                  {/* Add keyframe form */}
                  <div className="flex gap-1 items-end">
                    <div className="flex-1">
                      <label className="block text-xs text-gray-500 mb-0.5">時間(ms)</label>
                      <input
                        type="number"
                        min="0"
                        step="100"
                        value={newKeyframeInput.timeMs}
                        onChange={(e) => setNewKeyframeInput(prev => ({ ...prev, timeMs: e.target.value }))}
                        placeholder="0"
                        className="w-full px-1.5 py-1 bg-gray-700 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-orange-500"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs text-gray-500 mb-0.5">音量(%)</label>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="10"
                        value={newKeyframeInput.volume}
                        onChange={(e) => setNewKeyframeInput(prev => ({ ...prev, volume: e.target.value }))}
                        className="w-full px-1.5 py-1 bg-gray-700 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-orange-500"
                      />
                    </div>
                    <button
                      onClick={() => {
                        const timeMs = parseInt(newKeyframeInput.timeMs) || 0
                        const volume = (parseInt(newKeyframeInput.volume) || 100) / 100
                        handleAddVolumeKeyframeManual(timeMs, volume)
                        setNewKeyframeInput({ timeMs: '', volume: '100' })
                      }}
                      className="px-2 py-1 text-xs text-orange-400 hover:text-white hover:bg-orange-600 border border-orange-600 rounded transition-colors"
                      title="キーフレームを追加"
                    >
                      追加
                    </button>
                  </div>

                  {/* Quick add at current position */}
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleAddVolumeKeyframeAtCurrent(1.0)}
                      className="flex-1 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-600 border border-gray-600 rounded transition-colors"
                      title="カレント位置に100%キーフレームを追加"
                    >
                      カレント+100%
                    </button>
                    <button
                      onClick={() => handleAddVolumeKeyframeAtCurrent(0)}
                      className="flex-1 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-600 border border-gray-600 rounded transition-colors"
                      title="カレント位置に0%キーフレームを追加"
                    >
                      カレント+0%
                    </button>
                  </div>

                  {/* Keyframe list */}
                  {(() => {
                    const track = currentProject?.timeline_data.audio_tracks.find(t => t.id === selectedClip.trackId)
                    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
                    const keyframes = clip?.volume_keyframes || []
                    const clipStartMs = clip?.start_ms ?? selectedClip.startMs
                    const timeInClipMs = currentTime - clipStartMs
                    const isWithinClip = clip && timeInClipMs >= 0 && timeInClipMs <= clip.duration_ms

                    if (keyframes.length === 0) {
                      return (
                        <p className="text-gray-500 text-xs py-2">
                          キーフレームなし（デフォルト: 100%）
                          <br />
                          <span className="text-gray-600">カレント: {(timeInClipMs / 1000).toFixed(2)}s {!isWithinClip && '⚠️範囲外'}</span>
                        </p>
                      )
                    }

                    return (
                      <>
                        <div className="text-xs text-gray-400 mb-1">
                          {keyframes.length}キー（カレント: {(timeInClipMs / 1000).toFixed(2)}s {!isWithinClip && '⚠️'}）
                        </div>
                        <div className="max-h-40 overflow-y-auto space-y-1">
                          {[...keyframes].sort((a, b) => a.time_ms - b.time_ms).map((kf, i) => (
                            <div key={i} className="flex items-center gap-1 text-xs bg-gray-700/50 px-1.5 py-1 rounded">
                              <input
                                type="number"
                                min="0"
                                step="100"
                                value={kf.time_ms}
                                onChange={(e) => handleUpdateVolumeKeyframe(i, parseInt(e.target.value) || 0, kf.value)}
                                className="w-16 px-1 py-0.5 bg-gray-600 border border-gray-500 rounded text-white text-xs"
                                title="時間(ms)"
                              />
                              <span className="text-gray-500">ms</span>
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="10"
                                value={Math.round(kf.value * 100)}
                                onChange={(e) => handleUpdateVolumeKeyframe(i, kf.time_ms, (parseInt(e.target.value) || 0) / 100)}
                                className="w-12 px-1 py-0.5 bg-gray-600 border border-gray-500 rounded text-orange-400 text-xs"
                                title="音量(%)"
                              />
                              <span className="text-gray-500">%</span>
                              <button
                                onClick={() => handleRemoveVolumeKeyframe(i)}
                                className="ml-auto px-1.5 py-0.5 text-red-400 hover:text-white hover:bg-red-600 rounded transition-colors"
                                title="このキーを削除"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                        </div>
                        <button
                          onClick={handleClearVolumeKeyframes}
                          className="w-full px-3 py-1 text-xs text-red-400 hover:text-white hover:bg-red-600 border border-red-600 rounded transition-colors"
                        >
                          全削除
                        </button>
                      </>
                    )
                  })()}
                </div>
              </div>

              {/* Asset ID */}
              <div className="pt-4 border-t border-gray-700">
                <label className="block text-xs text-gray-500 mb-1">アセットID</label>
                <p className="text-gray-400 text-xs font-mono break-all">{selectedClip.assetId}</p>
              </div>
            </div>
          ) : (
            <p className="text-gray-400 text-sm">要素を選択してください</p>
          )}
              </div>
            </div>
          ) : (
            /* Property Panel - Collapsed */
            <div
              onClick={() => setIsPropertyPanelOpen(true)}
              className="bg-gray-800 border-l border-gray-700 w-10 flex flex-col items-center py-3 cursor-pointer hover:bg-gray-700 transition-colors"
            >
              <svg className="w-4 h-4 text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>プロパティ</span>
            </div>
          )}

          {/* AI Chat Panel */}
          <AIChatPanel
            projectId={currentProject.id}
            aiProvider={currentProject.ai_provider}
            isOpen={isAIChatOpen}
            onToggle={() => setIsAIChatOpen(prev => !prev)}
            mode="inline"
            width={aiPanelWidth}
            onResizeStart={handleAiPanelResizeStart}
          />

          {/* Activity Panel */}
          <ActivityPanel
            width={activityPanelWidth}
            onResizeStart={handleActivityPanelResizeStart}
          />
        </div>
      </div>

      {/* Version indicator */}
      <div className="fixed bottom-2 right-2 text-xs text-gray-500 font-mono opacity-50 hover:opacity-100 transition-opacity">
        F:{__APP_VERSION__} B:{backendVersion}
      </div>

      {/* Chroma Key Preview Modal - displayed at screen center */}
      {chromaPreviewSelectedIndex !== null && chromaPreviewFrames[chromaPreviewSelectedIndex] && (() => {
        const frame = chromaPreviewFrames[chromaPreviewSelectedIndex]
        const imageFormat = frame.image_format || 'jpeg'
        const mimeType = imageFormat === 'png' ? 'image/png' : 'image/jpeg'
        const isTransparent = imageFormat === 'png'
        return (
          <div
            className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/80"
            onClick={() => {
              setChromaPreviewSelectedIndex(null)
              setChromaPickerMode(false)
            }}
          >
            <div
              className="relative max-w-[90vw] max-h-[80vh]"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Close button */}
              <button
                onClick={() => {
                  setChromaPreviewSelectedIndex(null)
                  setChromaPickerMode(false)
                }}
                className="absolute -top-10 right-0 text-white hover:text-gray-300 text-2xl font-bold p-2"
                title="閉じる (ESC)"
              >
                x
              </button>
              {/* Preview image with checkerboard background for transparent PNG */}
              <div
                className="rounded-lg border-2 border-blue-500 overflow-hidden"
                style={{
                  maxWidth: '90vw',
                  maxHeight: '80vh',
                  // Checkerboard background for transparent PNG
                  background: isTransparent
                    ? 'repeating-conic-gradient(#4a4a4a 0% 25%, #3a3a3a 0% 50%) 50% / 32px 32px'
                    : undefined,
                }}
              >
                <img
                  src={`data:${mimeType};base64,${frame.frame_base64}`}
                  alt={`preview-enlarged-${frame.time_ms}`}
                  className="max-w-[90vw] max-h-[80vh] object-contain"
                  style={{ display: 'block' }}
                />
              </div>
              {/* Time indicator */}
              <div className="absolute bottom-4 left-4 bg-black/70 text-white text-sm px-3 py-1.5 rounded">
                {(frame.time_ms / 1000).toFixed(2)}s
                {isTransparent && <span className="ml-2 text-xs text-gray-400">(透過PNG)</span>}
              </div>
              {/* Navigation hint */}
              <div className="absolute bottom-4 right-4 bg-black/70 text-gray-400 text-xs px-3 py-1.5 rounded">
                ESC または画面外クリックで閉じる
              </div>
            </div>
          </div>
        )
      })()}

      {/* Eyedropper Modal - displays raw frame for color picking */}
      {chromaPickerMode && chromaRawFrame && (
        <div
          className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/80"
          onClick={() => {
            setChromaPickerMode(false)
            setChromaRawFrame(null)
          }}
        >
          <div
            className="relative max-w-[90vw] max-h-[80vh]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => {
                setChromaPickerMode(false)
                setChromaRawFrame(null)
              }}
              className="absolute -top-10 right-0 text-white hover:text-gray-300 text-2xl font-bold p-2"
              title="閉じる (ESC)"
            >
              x
            </button>
            {/* Eyedropper mode indicator */}
            <div className="absolute -top-10 left-0 bg-yellow-600 text-white text-sm px-3 py-1.5 rounded flex items-center gap-2">
              <span>スポイトモード: 画像をクリックして色を選択</span>
            </div>
            {/* Raw frame image for color picking */}
            <img
              src={`data:image/jpeg;base64,${chromaRawFrame.frame_base64}`}
              alt="raw-frame-for-color-picking"
              className="max-w-[90vw] max-h-[80vh] object-contain rounded-lg border-2 border-yellow-500 cursor-crosshair"
              onClick={(e) => {
                const img = e.currentTarget
                const canvas = document.createElement('canvas')
                const ctx = canvas.getContext('2d')
                if (!ctx) return

                // Create image element to draw on canvas
                const tempImg = new Image()
                tempImg.onload = () => {
                  canvas.width = tempImg.naturalWidth
                  canvas.height = tempImg.naturalHeight
                  ctx.drawImage(tempImg, 0, 0)

                  const rect = img.getBoundingClientRect()
                  const scaleX = tempImg.naturalWidth / rect.width
                  const scaleY = tempImg.naturalHeight / rect.height
                  const x = Math.floor((e.clientX - rect.left) * scaleX)
                  const y = Math.floor((e.clientY - rect.top) * scaleY)

                  try {
                    const pixel = ctx.getImageData(x, y, 1, 1).data
                    const hex = '#' + [pixel[0], pixel[1], pixel[2]].map(v => v.toString(16).padStart(2, '0')).join('').toUpperCase()

                    // Update the chroma key color
                    if (selectedVideoClip) {
                      const currentChromaKey = selectedVideoClip.effects.chroma_key || {
                        enabled: false,
                        color: '#00FF00',
                        similarity: 0.05,
                        blend: 0.0
                      }
                      handleUpdateVideoClipLocal({
                        effects: { chroma_key: { ...currentChromaKey, color: hex } }
                      })
                    }

                    // Exit eyedropper mode and close modal
                    setChromaPickerMode(false)
                    setChromaRawFrame(null)
                  } catch (err) {
                    console.error('Failed to pick color:', err)
                  }
                }
                tempImg.src = `data:image/jpeg;base64,${chromaRawFrame.frame_base64}`
              }}
            />
            {/* Time indicator */}
            <div className="absolute bottom-4 left-4 bg-black/70 text-white text-sm px-3 py-1.5 rounded">
              {(chromaRawFrame.time_ms / 1000).toFixed(2)}s (元動画)
            </div>
            {/* Navigation hint */}
            <div className="absolute bottom-4 right-4 bg-black/70 text-gray-400 text-xs px-3 py-1.5 rounded">
              緑背景をクリックして色を選択
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <Toast
          message={toastMessage.text}
          type={toastMessage.type}
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  )
}
