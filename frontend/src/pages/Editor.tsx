import { lazy, Suspense, useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import i18n from '@/i18n'
import { useParams } from 'react-router-dom'
import type { Options as Html2CanvasOptions } from 'html2canvas'
import { useProjectStore, type Shape, type VolumeKeyframe, type TimelineData, type Clip, type AudioClip as AudioClipType } from '@/store/projectStore'
import type { SelectedClipInfo, SelectedVideoClipInfo } from '@/components/editor/Timeline'
import { assetsApi } from '@/api/assets'
import Toast from '@/components/common/Toast'
import { type ChromaKeyPreviewFrame } from '@/api/aiV1'
import { projectsApi } from '@/api/projects'
import { sequencesApi } from '@/api/sequences'
import { addKeyframe, removeKeyframe, hasKeyframeAt, getInterpolatedTransform } from '@/utils/keyframes'
import { addVolumeKeyframe, getInterpolatedVolume } from '@/utils/volumeKeyframes'
// AudioClip type already imported from projectStore above
import { useOperationSync } from '@/hooks/useOperationSync'
import { useSequenceLock } from '@/hooks/useSequenceLock'
import { useProjectPresence } from '@/hooks/useProjectPresence'
import { useRemoteSync } from '@/hooks/useRemoteSync'
import { PresenceIndicator } from '@/components/editor/PresenceIndicator'
import type { SyncResumeAction } from '@/components/editor/SyncResumeDialog'
import { useAssetPreviewWorkflow } from '@/hooks/useAssetPreviewWorkflow'
import { usePreviewDragWorkflow } from '@/hooks/usePreviewDragWorkflow'
import { usePreviewViewport } from '@/hooks/usePreviewViewport'
import { useRenderWorkflow } from '@/hooks/useRenderWorkflow'
import { operationsApi, type Operation } from '@/api/operations'
import { useAuthStore } from '@/store/authStore'
import { useSequenceSaveState } from '@/hooks/useSequenceSaveState'
import { useSessionSaveWorkflow } from '@/hooks/useSessionSaveWorkflow'
import { loadEditorLayoutSettings, saveEditorLayoutSettings } from '@/utils/editorLayoutSettings'
import { mergeTextStyle } from '@/utils/textStyle'
import NumericInput from '@/components/common/NumericInput'
import { v4 as uuidv4 } from 'uuid'

// Preview panel border defaults
const DEFAULT_PREVIEW_BORDER_WIDTH = 3 // pixels
const DEFAULT_PREVIEW_BORDER_COLOR = '#ffffff' // white
const VIDEO_PLAY_RETRY_MS = 250
const LazyLeftPanel = lazy(() => import('@/components/assets/LeftPanel'))
const LazyEditorPreviewStage = lazy(() => import('@/components/editor/EditorPreviewStage'))
const LazyTimeline = lazy(() => import('@/components/editor/Timeline'))
const LazyEditorPropertyPanel = lazy(() => import('@/components/editor/EditorPropertyPanel'))
const LazyAIChatPanel = lazy(() => import('@/components/editor/AIChatPanel'))
const LazyActivityPanel = lazy(() => import('@/components/editor/ActivityPanel'))
const LazyExportDialog = lazy(() => import('@/components/editor/ExportDialog'))
const LazyMembersManager = lazy(() => import('@/components/settings/MembersManager'))
const LazyConflictResolutionDialog = lazy(() => import('@/components/editor/ConflictResolutionDialog').then((module) => ({ default: module.ConflictResolutionDialog })))
const LazySyncResumeDialog = lazy(() => import('@/components/editor/SyncResumeDialog').then((module) => ({ default: module.SyncResumeDialog })))

// Determine label for handleUpdateVideoClip based on updates content
function determineUpdateLabel(updates: Record<string, unknown>): string {
  if ((updates.effects as Record<string, unknown>)?.opacity !== undefined) return i18n.t('editor:undo.opacityChange')
  if (updates.transform) return i18n.t('editor:undo.transformChange')
  if (updates.text_content !== undefined) return i18n.t('editor:undo.textChange')
  if (updates.text_style) return i18n.t('editor:undo.textStyleChange')
  if (updates.speed !== undefined) return i18n.t('editor:undo.speedChange')
  if (updates.freeze_frame_ms !== undefined) return i18n.t('editor:undo.freezeChange')
  if (updates.crop) return i18n.t('editor:undo.cropChange')
  if ((updates.effects as Record<string, unknown>)?.chroma_key) return i18n.t('editor:undo.chromaKeyChange')
  if (updates.effects) return i18n.t('editor:undo.effectChange')
  return i18n.t('editor:undo.clipChange')
}

function CompositePreviewViewer({ src, onClose }: { src: string; onClose: () => void }) {
  const { t } = useTranslation('editor')
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const panStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setZoom(z => Math.min(10, Math.max(0.1, z * delta)))
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    setIsPanning(true)
    panStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y }
  }, [pan])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning) return
    setPan({
      x: panStart.current.panX + (e.clientX - panStart.current.x),
      y: panStart.current.panY + (e.clientY - panStart.current.y),
    })
  }, [isPanning])

  const handleMouseUp = useCallback(() => {
    setIsPanning(false)
  }, [])

  const resetView = useCallback(() => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '+' || e.key === '=') setZoom(z => Math.min(10, z * 1.2))
      else if (e.key === '-') setZoom(z => Math.max(0.1, z / 1.2))
      else if (e.key === '0') resetView()
      else if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, resetView])

  return (
    <div className="flex flex-col bg-gray-900 rounded-b-lg">
      <div className="flex items-center gap-1 px-3 py-1.5 bg-gray-800 border-t border-gray-700">
        <button
          className="text-gray-300 hover:text-white text-sm px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600"
          onClick={() => setZoom(z => Math.max(0.1, z / 1.3))}
        >&minus;</button>
        <button
          className="text-gray-300 hover:text-white text-xs px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 min-w-[48px]"
          onClick={resetView}
        >{Math.round(zoom * 100)}%</button>
        <button
          className="text-gray-300 hover:text-white text-sm px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600"
          onClick={() => setZoom(z => Math.min(10, z * 1.3))}
        >+</button>
        <span className="text-gray-500 text-[10px] ml-2">{t('editor.previewHint')}</span>
      </div>
      <div
        ref={containerRef}
        className="overflow-hidden cursor-grab active:cursor-grabbing"
        style={{ width: '90vw', height: '80vh', maxWidth: 1600 }}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <div
          className="w-full h-full flex items-center justify-center"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: 'center center',
            transition: isPanning ? 'none' : 'transform 0.1s ease-out',
          }}
        >
          <img
            src={src}
            alt="Composite preview"
            className="max-w-full max-h-full object-contain select-none"
            draggable={false}
          />
        </div>
      </div>
    </div>
  )
}

export default function Editor() {
  const { t, i18n: i18nHook } = useTranslation('editor')
  const { projectId, sequenceId } = useParams<{ projectId: string; sequenceId: string }>()
  const { currentProject, loading, error, fetchProject, updateProject, updateTimelineLocal, undo, redo, canUndo, canRedo, getUndoLabel, getRedoLabel, historyVersion, currentSequence, fetchSequence, saveSequence } = useProjectStore()
  const timelineHistory = useProjectStore(state => state.timelineHistory)
  const timelineFuture = useProjectStore(state => state.timelineFuture)
  const isConflictDialogOpen = useProjectStore(state => state.conflictState?.isConflicting ?? false)
  const [showRenderModal, setShowRenderModal] = useState(false)
  const [showSettingsModal, setShowSettingsModal] = useState(false)
  const [showShortcutsModal, setShowShortcutsModal] = useState(false)
  const [showMembersModal, setShowMembersModal] = useState(false)
  const [isRenderPackageLoading, setIsRenderPackageLoading] = useState(false)
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
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  // Toast notification
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'success' | 'error' | 'info'; duration?: number } | null>(null)
  const [isUndoRedoInProgress, setIsUndoRedoInProgress] = useState(false)
  // Undo/Redo long-press dropdown
  const [undoDropdownOpen, setUndoDropdownOpen] = useState(false)
  const [redoDropdownOpen, setRedoDropdownOpen] = useState(false)
  const undoLongPressRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const redoLongPressRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const undoDropdownRef = useRef<HTMLDivElement>(null)
  const redoDropdownRef = useRef<HTMLDivElement>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
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
  const [savedLayout] = useState(() => loadEditorLayoutSettings())
  const [currentTime, setCurrentTime] = useState(savedLayout.playheadPosition)
  const currentTimeRef = useRef(0) // Ref to always get latest currentTime
  const {
    effectivePreviewHeight,
    effectivePreviewWidth,
    handlePreviewMouseLeave,
    handlePreviewMouseMove,
    handlePreviewPanStart,
    handlePreviewWheel,
    handlePreviewZoomFit,
    handlePreviewZoomIn,
    handlePreviewZoomOut,
    handleResizeStart,
    isPanningPreview,
    isResizing,
    previewAreaRef,
    previewContainerRef,
    previewHeight,
    previewPan,
    previewResizeSnap,
    previewZoom,
    recenterPreview,
    showPreviewControls,
    togglePreviewResizeSnap,
  } = usePreviewViewport({
    initialPreviewHeight: savedLayout.previewHeight,
    initialPreviewZoom: savedLayout.previewZoom,
  })
  const [chromaPreviewFrames, setChromaPreviewFrames] = useState<ChromaKeyPreviewFrame[]>([])
  const [chromaPreviewLoading] = useState(false)
  const [chromaPreviewError, setChromaPreviewError] = useState<string | null>(null)
  const [chromaApplyLoading] = useState(false)
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
  // 1-frame actual FFmpeg render overlay on preview canvas
  const [chromaRenderOverlay, setChromaRenderOverlay] = useState<string | null>(null)

  const [chromaRenderOverlayTimeMs, setChromaRenderOverlayTimeMs] = useState<number | null>(null)
  const [chromaRenderOverlayDims, setChromaRenderOverlayDims] = useState<{ width: number; height: number } | null>(null)
  // Composite frame lightbox
  const [compositeLightbox, setCompositeLightbox] = useState<{ src: string; timeMs: number } | null>(null)
  const [compositeLightboxLoading, setCompositeLightboxLoading] = useState(false)
  // Preview border settings
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
  // Local state for new volume keyframe input
  const [newKeyframeInput, setNewKeyframeInput] = useState({ timeMs: '', volume: '100' })
  const [isAIChatOpen, setIsAIChatOpen] = useState(savedLayout.isAIChatOpen)
  const [isPropertyPanelOpen, setIsPropertyPanelOpen] = useState(savedLayout.isPropertyPanelOpen)
  const [isAssetPanelOpen, setIsAssetPanelOpen] = useState(savedLayout.isAssetPanelOpen)
  const [isActivityPanelOpen, setIsActivityPanelOpen] = useState(false)
  const [isSyncEnabled, setIsSyncEnabled] = useState(savedLayout.isSyncEnabled)
  const [syncResumeDialog, setSyncResumeDialog] = useState<{ remoteOpCount: number } | null>(null)
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
  const { isReadOnly, lockHolder, acquireLock: retryLock, releaseLock } = useSequenceLock(projectId, sequenceId)
  const goToDashboard = useCallback(() => {
    void releaseLock({ keepalive: true })
    // Force a document navigation so the return path does not depend on the
    // in-memory router state that may have led users back to the landing page.
    window.location.assign('/app')
  }, [releaseLock])
  const undoLabel = getUndoLabel()
  const redoLabel = getRedoLabel()
  const undoTooltip = undoLabel
    ? (isMac ? t('editor.undoLabel', { label: undoLabel }) : t('editor.undoLabelCtrl', { label: undoLabel }))
    : (isMac ? t('editor.undoNoLabel') : t('editor.undoNoLabelCtrl'))
  const redoTooltip = redoLabel
    ? (isMac ? t('editor.redoLabel', { label: redoLabel }) : t('editor.redoLabelCtrl', { label: redoLabel }))
    : (isMac ? t('editor.redoNoLabel') : t('editor.redoNoLabelCtrl'))

  // Undo/Redo long-press dropdown: multiple undo/redo handler
  const handleUndoMultiple = async (count: number) => {
    setUndoDropdownOpen(false)
    if (!projectId || isUndoRedoInProgress) return
    setIsUndoRedoInProgress(true)
    try {
      for (let i = 0; i < count; i++) {
        const success = await runHistoryMutation('undo')
        if (!success) break
      }
      setToastMessage({ text: t('undo.opacityChange'), type: 'info', duration: 1500 })
    } finally {
      setTimeout(() => setIsUndoRedoInProgress(false), 150)
    }
  }

  const handleRedoMultiple = async (count: number) => {
    setRedoDropdownOpen(false)
    if (!projectId || isUndoRedoInProgress) return
    setIsUndoRedoInProgress(true)
    try {
      for (let i = 0; i < count; i++) {
        const success = await runHistoryMutation('redo')
        if (!success) break
      }
      setToastMessage({ text: t('undo.opacityChange'), type: 'info', duration: 1500 })
    } finally {
      setTimeout(() => setIsUndoRedoInProgress(false), 150)
    }
  }

  // Close undo/redo dropdown on outside click or Escape
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (undoDropdownOpen && undoDropdownRef.current && !undoDropdownRef.current.contains(e.target as Node)) {
        setUndoDropdownOpen(false)
      }
      if (redoDropdownOpen && redoDropdownRef.current && !redoDropdownRef.current.contains(e.target as Node)) {
        setRedoDropdownOpen(false)
      }
    }
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setUndoDropdownOpen(false)
        setRedoDropdownOpen(false)
      }
    }
    if (undoDropdownOpen || redoDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [undoDropdownOpen, redoDropdownOpen])

  useEffect(() => {
    setChromaPreviewFrames([])
    setChromaPreviewError(null)
    setChromaRawFrame(null)
    setChromaPickerMode(false)
    setChromaRenderOverlay(null)
    setChromaRenderOverlayTimeMs(null)
    setChromaRenderOverlayDims(null)
  }, [selectedVideoClip?.clipId])

  // After undo/redo, refresh the selected clip's cached data from the new
  // timeline so users can immediately verify property changes. Only clear the
  // selection if the clip was actually removed by the history mutation.
  // (Issue #189 — previously this unconditionally cleared the selection,
  //  making it hard to confirm what the Undo reverted.)
  useEffect(() => {
    if (historyVersion === 0) return
    const state = useProjectStore.getState()
    const timeline = state.currentSequence?.timeline_data ?? state.currentProject?.timeline_data
    if (!timeline) {
      setSelectedVideoClip(null)
      setSelectedClip(null)
      return
    }

    setSelectedVideoClip(prev => {
      if (!prev) return prev
      const layer = timeline.layers.find(l => l.id === prev.layerId)
      const clip = layer?.clips.find(c => c.id === prev.clipId)
      if (!clip) return null
      return {
        ...prev,
        transform: clip.transform,
        effects: clip.effects,
        keyframes: clip.keyframes,
        speed: clip.speed ?? 1,
        freezeFrameMs: clip.freeze_frame_ms ?? 0,
        startMs: clip.start_ms,
        durationMs: clip.duration_ms,
        inPointMs: clip.in_point_ms,
        outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
        textContent: clip.text_content,
        textStyle: clip.text_style as typeof prev.textStyle,
        crop: clip.crop,
        shape: clip.shape,
      }
    })

    setSelectedClip(prev => {
      if (!prev) return prev
      const track = timeline.audio_tracks.find(t => t.id === prev.trackId)
      const clip = track?.clips.find(c => c.id === prev.clipId)
      if (!clip) return null
      return {
        ...prev,
        volume: clip.volume,
        fadeInMs: clip.fade_in_ms,
        fadeOutMs: clip.fade_out_ms,
        startMs: clip.start_ms,
        durationMs: clip.duration_ms,
      }
    })
  }, [historyVersion])

  // Dismiss chroma render overlay when playhead moves more than 50ms from captured time
  useEffect(() => {
    if (chromaRenderOverlayTimeMs !== null && Math.abs(currentTime - chromaRenderOverlayTimeMs) > 50) {
      setChromaRenderOverlay(null)
      setChromaRenderOverlayTimeMs(null)
      setChromaRenderOverlayDims(null)
    }
  }, [currentTime, chromaRenderOverlayTimeMs])

  // Clear chroma render overlay when chroma key parameters change
  useEffect(() => {
    if (chromaRenderOverlay) {
      setChromaRenderOverlay(null)
      setChromaRenderOverlayTimeMs(null)
      setChromaRenderOverlayDims(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedVideoClip?.effects?.chroma_key?.color,
    selectedVideoClip?.effects?.chroma_key?.similarity,
    selectedVideoClip?.effects?.chroma_key?.blend,
  ])

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
    saveEditorLayoutSettings({
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
    clip_volume?: number,
    speed: number
  }>>(new Map())
  const videoRefsMap = useRef<Map<string, HTMLVideoElement>>(new Map())
  const videoPlayAttemptAtRef = useRef<Map<string, number>>(new Map())
  // Track which videos have loaded their first frame (no longer needed for preload layer, kept for potential future use)
  // const [preloadedVideos, setPreloadedVideos] = useState<Set<string>>(new Set())
  const playbackTimerRef = useRef<number | null>(null)
  const startTimeRef = useRef<number>(0)
  const isPlayingRef = useRef(false)
  const pendingPlayPromisesRef = useRef<Map<HTMLMediaElement, Promise<void>>>(new Map())
  const pendingPauseAfterPlayRef = useRef<Set<HTMLMediaElement>>(new Set())

  const clearPendingPlaybackState = useCallback((media: HTMLMediaElement) => {
    pendingPauseAfterPlayRef.current.delete(media)
    pendingPlayPromisesRef.current.delete(media)
  }, [])

  const safePlay = useCallback((media: HTMLMediaElement, errorLabel = 'Playback error') => {
    pendingPauseAfterPlayRef.current.delete(media)

    const pendingPlay = pendingPlayPromisesRef.current.get(media)
    if (pendingPlay) return pendingPlay

    try {
      const maybePromise = media.play()
      if (!maybePromise) return undefined

      const trackedPromise = maybePromise.catch((err: unknown) => {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          console.error(errorLabel, err)
        }
      }).finally(() => {
        pendingPlayPromisesRef.current.delete(media)
        const shouldPauseAfterPlay = pendingPauseAfterPlayRef.current.delete(media)
        if (shouldPauseAfterPlay && !media.paused) {
          media.pause()
        }
      })

      pendingPlayPromisesRef.current.set(media, trackedPromise)
      return trackedPromise
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        console.error(errorLabel, err)
      }
      return undefined
    }
  }, [])

  const safePause = useCallback((media: HTMLMediaElement) => {
    const pendingPlay = pendingPlayPromisesRef.current.get(media)
    if (pendingPlay) {
      pendingPauseAfterPlayRef.current.add(media)
      return
    }

    pendingPauseAfterPlayRef.current.delete(media)
    media.pause()
  }, [])

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

  // Computed timeline data: prefer sequence, fallback to project
  const timelineData = currentSequence?.timeline_data ?? currentProject?.timeline_data
  const timelineDataSignature = JSON.stringify(timelineData)

  // Clean up orphaned audio/video refs when timeline changes
  // Also stop playback to prevent ghost audio with stale timing
  useEffect(() => {
    if (!timelineData) return

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
        safePause(audio)
        audio.currentTime = 0
      })
      videoRefsMap.current.forEach(video => safePause(video))
      videoPlayAttemptAtRef.current.clear()
    }

    // Clear all audio timing refs - they'll be re-populated on next playback
    audioClipTimingRefs.current.clear()

    // Get current clip IDs
    const currentAudioClipIds = new Set<string>()
    const tl = timelineData
    if (tl) {
      for (const track of tl.audio_tracks) {
        for (const clip of track.clips) {
          currentAudioClipIds.add(clip.id)
        }
      }
    }
    const currentVideoClipIds = new Set<string>()
    if (tl) {
      for (const layer of tl.layers) {
        for (const clip of layer.clips) {
          if (clip.asset_id) currentVideoClipIds.add(clip.id)
        }
      }
    }

    // Clean up orphaned audio refs
    audioRefs.current.forEach((audio, clipId) => {
      if (!currentAudioClipIds.has(clipId)) {
        safePause(audio)
        audio.src = ''
        clearPendingPlaybackState(audio)
        audioRefs.current.delete(clipId)
      }
    })

    // Clean up orphaned video refs
    videoRefsMap.current.forEach((video, clipId) => {
      if (!currentVideoClipIds.has(clipId)) {
        safePause(video)
        video.src = ''
        clearPendingPlaybackState(video)
        videoRefsMap.current.delete(clipId)
        videoPlayAttemptAtRef.current.delete(clipId)
      }
    })
  }, [clearPendingPlaybackState, safePause, timelineData, timelineDataSignature])

  // Fetch sequence data when sequenceId is available
  useEffect(() => {
    if (projectId && sequenceId) {
      fetchSequence(projectId, sequenceId)
    }
  }, [projectId, sequenceId, fetchSequence])

  // Re-fetch sequence data when lock transitions from read-only to editable (debounced)
  const prevIsReadOnlyRef = useRef(isReadOnly)
  const lockFetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (prevIsReadOnlyRef.current && !isReadOnly && projectId && sequenceId) {
      if (lockFetchTimerRef.current) clearTimeout(lockFetchTimerRef.current)
      lockFetchTimerRef.current = setTimeout(() => {
        console.log('[Editor] Lock acquired - re-fetching sequence data')
        fetchSequence(projectId, sequenceId)
        lockFetchTimerRef.current = null
      }, 300)
    }
    prevIsReadOnlyRef.current = isReadOnly
    return () => {
      if (lockFetchTimerRef.current) {
        clearTimeout(lockFetchTimerRef.current)
        lockFetchTimerRef.current = null
      }
    }
  }, [isReadOnly, projectId, sequenceId, fetchSequence])

  // Subscribe to operation-based sync for collaborative editing (disabled for sequence mode)
  const { operationHistory } = useOperationSync(projectId, {
    enabled: false,
  })

  const { users: presenceUsers } = useProjectPresence(projectId)

  // Subscribe to Firestore project_updates for remote changes (V1 API / MCP)
  useRemoteSync(projectId, sequenceId)

  const refreshTimelineAfterAiApply = useCallback(async () => {
    if (!projectId) return

    if (currentSequence?.id) {
      await fetchSequence(projectId, currentSequence.id)
      return
    }

    await fetchProject(projectId)
  }, [currentSequence?.id, fetchProject, fetchSequence, projectId])

  // Effective duration: prefer sequence duration (updated on save), fallback to project
  const effectiveDurationMs = currentSequence?.duration_ms ?? currentProject?.duration_ms ?? 0

  const {
    assets,
    assetUrlCache,
    clearPreview,
    fetchAssets,
    invalidateAssetUrl,
    preview,
    previewAsset: handlePreviewAsset,
    replaceAssets,
  } = useAssetPreviewWorkflow({
    currentTime,
    projectId,
    timelineData,
  })
  const assetsLoadedSequenceRef = useRef<string | null>(null)

  useEffect(() => {
    if (projectId) {
      fetchProject(projectId)
    }
  }, [projectId, fetchProject])

  useEffect(() => {
    assetsLoadedSequenceRef.current = null
  }, [projectId, sequenceId])

  useEffect(() => {
    if (!projectId || !sequenceId || currentSequence?.id !== sequenceId) return

    const sequenceKey = `${projectId}:${sequenceId}`
    if (assetsLoadedSequenceRef.current === sequenceKey) return

    assetsLoadedSequenceRef.current = sequenceKey
    void fetchAssets()
  }, [currentSequence?.id, fetchAssets, projectId, sequenceId])

  const extractSaveErrorMessage = useCallback((error: unknown, fallback: string) => {
    if (typeof error === 'object' && error !== null && 'response' in error) {
      const axiosError = error as {
        response?: {
          data?: {
            detail?: string | { message?: string }
          }
        }
      }
      const detail = axiosError.response?.data?.detail
      if (typeof detail === 'string' && detail.trim().length > 0) return detail
      if (typeof detail === 'object' && typeof detail?.message === 'string' && detail.message.trim().length > 0) {
        return detail.message
      }
    }
    if (error instanceof Error && error.message.trim().length > 0) {
      return error.message
    }
    return fallback
  }, [])

  const formatSaveTime = useCallback((isoString: string | null) => {
    if (!isoString) return ''
    try {
      return new Date(isoString).toLocaleTimeString(i18nHook.language === 'ja' ? 'ja-JP' : 'en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    } catch {
      return ''
    }
  }, [i18nHook.language])

  const {
    lastSequenceSaveAt,
    retrySequenceSave,
    runTrackedSequenceSave,
    sequenceSaveError,
    sequenceSaveState,
  } = useSequenceSaveState({
    currentSequenceId: currentSequence?.id,
    currentSequenceUpdatedAt: currentSequence?.updated_at ?? null,
    getErrorMessage: extractSaveErrorMessage,
    projectId,
    saveConflictMessage: t('editor.sequenceConflictMessage'),
    saveFailedMessage: t('editor.sequenceSaveFailedMessage'),
    saveSequence,
    sequenceId,
    timelineData,
  })
  const {
    assetLibraryRefreshTrigger,
    clearSessionSaveFailure,
    currentSessionId,
    currentSessionName,
    retryFailedSessionSave,
    saveSession: handleSaveSession,
    savingSession,
    sessionSaveFailure,
  } = useSessionSaveWorkflow({
    assets,
    getErrorMessage: extractSaveErrorMessage,
    onAssetsUpdated: replaceAssets,
    onToast: (message) => setToastMessage(message),
    projectId,
    saveFailedMessage: t('editor.sessionSaveFailedMessage'),
    saveFailedToast: t('editor.sessionSaveFailedToast'),
    timelineData,
  })
  const {
    cancelRender: handleCancelRender,
    clearRenderJob,
    downloadVideo: handleDownloadVideo,
    loadRenderHistory,
    renderHistory,
    renderJob,
    startRender: handleStartRender,
  } = useRenderWorkflow({
    projectId: currentProject?.id,
    renderErrorTitle: t('conflict.title'),
  })

  const handleDownloadRenderPackage = useCallback(async () => {
    if (!projectId || isRenderPackageLoading) return

    setIsRenderPackageLoading(true)

    try {
      const { download_url } = await projectsApi.createRenderPackage(projectId)
      window.open(download_url, '_blank', 'noopener,noreferrer')
      setToastMessage({
        text: t('editor.renderPackageStarted'),
        type: 'success',
      })
    } catch (error) {
      setToastMessage({
        text: extractSaveErrorMessage(error, t('editor.renderPackageFailed')),
        type: 'error',
        duration: 5000,
      })
    } finally {
      setIsRenderPackageLoading(false)
    }
  }, [extractSaveErrorMessage, isRenderPackageLoading, projectId, t])

  const runHistoryMutation = useCallback(async (direction: 'undo' | 'redo') => {
    if (!projectId) return false
    try {
      if (direction === 'undo') {
        await runTrackedSequenceSave(() => undo(projectId))
      } else {
        await runTrackedSequenceSave(() => redo(projectId))
      }
      return true
    } catch (error) {
      console.error(`Failed to ${direction}:`, error)
      return false
    }
  }, [projectId, redo, runTrackedSequenceSave, undo])

  // Wrapper for saving timeline changes through sequence API
  const handleTimelineUpdate = useCallback((timeline: TimelineData, label?: string) => {
    if (isReadOnly || !projectId || !sequenceId) return
    void runTrackedSequenceSave(() => saveSequence(projectId, sequenceId, timeline, label))
  }, [isReadOnly, projectId, sequenceId, runTrackedSequenceSave, saveSequence])
  const {
    dragCrop,
    dragTransform,
    edgeSnapEnabled,
    previewDrag,
    snapGuides,
    toggleEdgeSnapEnabled,
    handlePreviewDragStart,
  } = usePreviewDragWorkflow({
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
    textFallbackLabel: t('timeline.text'),
    timelineData,
    undoLabel: i18n.t('editor:undo.clipDrag'),
    updateTimeline: handleTimelineUpdate,
    videoRefsMap,
  })

  // Local-only wrapper for timeline updates (no API call, for drag operations)
  const handleTimelineUpdateLocal = useCallback((timeline: TimelineData) => {
    if (isReadOnly || !projectId) return
    updateTimelineLocal(projectId, timeline)
  }, [isReadOnly, projectId, updateTimelineLocal])

  // Handle sync toggle: check for remote changes before re-enabling
  const handleSyncToggle = useCallback(async () => {
    if (isSyncEnabled) {
      // Turning OFF — just disable, no dialog needed
      setIsSyncEnabled(false)
      return
    }

    // Turning ON — check for remote changes first
    if (!projectId) {
      setIsSyncEnabled(true)
      return
    }

    const state = useProjectStore.getState()
    const currentVersion = state.currentProject?.version ?? 0

    try {
      const result = await operationsApi.poll(projectId, currentVersion)
      // Filter out own operations
      const currentUserId = useAuthStore.getState().user?.uid
      const remoteItems = currentUserId
        ? result.operations.filter(item => item.user_id !== currentUserId)
        : result.operations

      if (remoteItems.length === 0) {
        // No remote changes — just resume
        // Update version if needed (our own ops may have advanced it)
        if (result.current_version > currentVersion) {
          useProjectStore.getState().applyRemoteOps(projectId, result.current_version, [])
        }
        setIsSyncEnabled(true)
      } else {
        // Remote changes detected — show dialog
        setSyncResumeDialog({ remoteOpCount: remoteItems.length })
      }
    } catch (error) {
      console.error('[SyncResume] Failed to check remote changes:', error)
      // On error, just enable sync (will catch up via normal polling)
      setIsSyncEnabled(true)
    }
  }, [isSyncEnabled, projectId])

  // Handle sync resume dialog action
  const handleSyncResumeAction = useCallback(async (action: SyncResumeAction) => {
    if (!projectId) return

    try {
      if (action === 'load_remote') {
        // Reload project from server (discards local changes)
        await useProjectStore.getState().fetchProject(projectId)
        setIsSyncEnabled(true)
      } else if (action === 'apply_diff') {
        // Apply remote changes as diff on top of local state
        const state = useProjectStore.getState()
        const currentVersion = state.currentProject?.version ?? 0
        const result = await operationsApi.poll(projectId, currentVersion)
        const currentUserId = useAuthStore.getState().user?.uid
        const remoteItems = currentUserId
          ? result.operations.filter(item => item.user_id !== currentUserId)
          : result.operations
        const allOps = remoteItems.flatMap(item =>
          (item.data?.operations as Operation[]) || []
        )
        useProjectStore.getState().applyRemoteOps(projectId, result.current_version, allOps)
        setIsSyncEnabled(true)
      } else if (action === 'overwrite_remote') {
        // Force-save local state to server
        const state = useProjectStore.getState()
        const timeline = state.currentSequence?.timeline_data ?? state.currentProject?.timeline_data
        if (timeline && sequenceId) {
          await runTrackedSequenceSave(() => state.saveSequence(projectId, sequenceId, timeline, { label: i18n.t('editor:undo.syncOverwrite'), skipHistory: true }))
        }
        setIsSyncEnabled(true)
      }
    } catch (error) {
      console.error('[SyncResume] Action failed:', error)
      // Even on error, enable sync to avoid stuck state
      setIsSyncEnabled(true)
    } finally {
      setSyncResumeDialog(null)
    }
  }, [projectId, runTrackedSequenceSave, sequenceId])

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
      alert(t('conflict.title'))
    }
  }

  // Update AI provider
  const handleUpdateAIProvider = async (provider: 'openai' | 'gemini' | 'anthropic' | null) => {
    if (!currentProject) return
    try {
      await updateProject(currentProject.id, { ai_provider: provider })
    } catch (error) {
      console.error('Failed to update AI provider:', error)
      alert(t('conflict.title'))
    }
  }

  const handleUpdateAIApiKey = async (apiKey: string) => {
    if (!currentProject) return
    try {
      await updateProject(currentProject.id, { ai_api_key: apiKey || null })
      alert(t('conflict.loadLatest'))
    } catch (error) {
      console.error('Failed to update AI API key:', error)
      alert(t('conflict.title'))
    }
  }

  // Capture and upload thumbnail from preview
  const captureThumbnail = useCallback(async () => {
    if (!currentProject || !previewContainerRef.current) return

    try {
      const { default: html2canvas } = await import('html2canvas')
      // Use html2canvas to capture the preview container
      const canvas = await html2canvas(previewContainerRef.current, {
        backgroundColor: '#000000',
        scale: 0.5, // Reduce size for thumbnail (saves bandwidth and storage)
        logging: false,
        useCORS: true,
        allowTaint: true,
      } satisfies Partial<Html2CanvasOptions>)

      // Convert to base64 PNG
      const imageData = canvas.toDataURL('image/png', 0.8)

      // Upload project thumbnail
      await projectsApi.uploadThumbnail(currentProject.id, imageData)

      // Upload sequence thumbnail
      if (sequenceId) {
        await sequencesApi.uploadThumbnail(currentProject.id, sequenceId, imageData)
      }

      console.log('[Thumbnail] Captured and uploaded thumbnails')
    } catch (error) {
      console.error('[Thumbnail] Failed to capture thumbnail:', error)
    }
  }, [currentProject, previewContainerRef, sequenceId])

  // Capture thumbnail when timeline data changes (debounced)
  // This captures the preview at the current time (not necessarily 0ms) as a representative thumbnail
  const thumbnailTimeoutRef = useRef<number | null>(null)
  const lastThumbnailCaptureRef = useRef<number>(0)
  const THUMBNAIL_DEBOUNCE_MS = 5000 // Wait 5 seconds after last change
  const THUMBNAIL_MIN_INTERVAL_MS = 60000 // At least 60 seconds between captures

  useEffect(() => {
    if (!timelineData) return

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
  }, [timelineData, captureThumbnail])

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

  const seekVideoIfNeeded = useCallback((video: HTMLVideoElement, targetTimeSec: number, thresholdSec = 0.05) => {
    if (!Number.isFinite(targetTimeSec) || video.seeking) return
    if (Math.abs(video.currentTime - targetTimeSec) <= thresholdSec) return
    try {
      video.currentTime = targetTimeSec
    } catch {
      // Some browsers can reject seek before metadata is fully available.
    }
  }, [])

  const requestVideoPlay = useCallback((video: HTMLVideoElement, clipId: string, speed: number) => {
    video.playbackRate = speed

    const now = performance.now()
    const lastAttempt = videoPlayAttemptAtRef.current.get(clipId) ?? 0
    if (now - lastAttempt < VIDEO_PLAY_RETRY_MS) return
    videoPlayAttemptAtRef.current.set(clipId, now)

    safePlay(video, 'Failed to start video playback:')
  }, [safePlay])

  const syncVideoToTimelinePosition = useCallback((
    video: HTMLVideoElement,
    clip: Pick<Clip, 'start_ms' | 'duration_ms' | 'in_point_ms' | 'speed' | 'freeze_frame_ms'>
  ) => {
    const speed = clip.speed || 1
    const timelineNowMs = isPlayingRef.current
      ? performance.now() - startTimeRef.current
      : currentTimeRef.current

    const clipEffectiveEndMs = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
    if (timelineNowMs >= clipEffectiveEndMs) return

    let targetMs = clip.in_point_ms
    if (timelineNowMs >= clip.start_ms) {
      const isInFreezeRegion = timelineNowMs >= clip.start_ms + clip.duration_ms
      targetMs = isInFreezeRegion
        ? clip.in_point_ms + clip.duration_ms * speed
        : clip.in_point_ms + (timelineNowMs - clip.start_ms) * speed
    }

    seekVideoIfNeeded(video, Math.max(0, targetMs / 1000))
  }, [seekVideoIfNeeded])

  // Playback controls
  const stopPlayback = useCallback(() => {
    isPlayingRef.current = false
    setIsPlaying(false)
    if (playbackTimerRef.current) {
      cancelAnimationFrame(playbackTimerRef.current)
      playbackTimerRef.current = null
    }
    audioRefs.current.forEach(audio => {
      safePause(audio)
      audio.currentTime = 0
    })
    // Pause all video previews
    videoRefsMap.current.forEach(video => safePause(video))
    videoPlayAttemptAtRef.current.clear()
  }, [safePause])

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
    const playbackTimeline = timelineData
    if (playbackTimeline) {
      for (const track of playbackTimeline.audio_tracks) {
        for (const clip of track.clips) {
          currentClipIds.add(clip.id)
        }
      }
    }
    // Remove audio elements that are no longer in the timeline
    audioRefs.current.forEach((audio, clipId) => {
      if (!currentClipIds.has(clipId)) {
        safePause(audio)
        audio.src = '' // Release the audio resource
        clearPendingPlaybackState(audio)
        audioRefs.current.delete(clipId)
      }
    })

    // Clean up orphaned video refs
    const currentVideoClipIds = new Set<string>()
    if (playbackTimeline) {
      for (const layer of playbackTimeline.layers) {
        for (const clip of layer.clips) {
          if (clip.asset_id) currentVideoClipIds.add(clip.id)
        }
      }
    }
    videoRefsMap.current.forEach((video, clipId) => {
      if (!currentVideoClipIds.has(clipId)) {
        safePause(video)
        video.src = ''
        clearPendingPlaybackState(video)
        videoRefsMap.current.delete(clipId)
        videoPlayAttemptAtRef.current.delete(clipId)
      }
    })

    // Load audio clips asynchronously (non-blocking)
    // The updatePlayhead callback will start each audio when it comes into range
    const loadAudioClips = async () => {
      if (!playbackTimeline) return
      for (const track of playbackTimeline.audio_tracks) {
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
              clip_volume: clip.volume, // Original clip volume for keyframe interpolation
              speed: clip.speed || 1
            })

            // If playback is still active and clip is in range, start it now
            if (isPlayingRef.current) {
              const elapsed = performance.now() - startTimeRef.current
              const isCurrentlyInRange = elapsed >= clip.start_ms && elapsed < clipEndMs

              if (isCurrentlyInRange) {
                const clipSpeed = clip.speed || 1
                const offsetInClip = (elapsed - clip.start_ms) * clipSpeed
                audio.currentTime = (clip.in_point_ms + offsetInClip) / 1000
                audio.playbackRate = clipSpeed
                const timing = audioClipTimingRefs.current.get(clip.id)!
                audio.volume = calculateFadeVolume(elapsed, timing)
                safePlay(audio, 'Failed to start audio playback:')
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
      if (!playbackTimeline) return null
      const layers = playbackTimeline.layers
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

    // Start video playback for all active clips, pre-seek upcoming clips
    videoRefsMap.current.forEach((video, clipId) => {
      const clip = findClipById(clipId)
      if (!clip) return

      const clipEffectiveEnd = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
      const isActive = currentTime >= clip.start_ms && currentTime < clipEffectiveEnd

      if (isActive) {
        const speed = clip.speed || 1
        const isInFreezeRegion = currentTime >= clip.start_ms + clip.duration_ms
        if (isInFreezeRegion) {
          const lastFrameTimeMs = clip.in_point_ms + clip.duration_ms * speed
          seekVideoIfNeeded(video, lastFrameTimeMs / 1000)
          if (!video.paused) safePause(video)
          videoPlayAttemptAtRef.current.delete(clipId)
        } else {
          const videoTimeMs = clip.in_point_ms + (currentTime - clip.start_ms) * speed
          seekVideoIfNeeded(video, videoTimeMs / 1000)
          requestVideoPlay(video, clipId, speed)
        }
      } else if (currentTime < clip.start_ms && currentTime >= clip.start_ms - 3000) {
        // Pre-seek only clips starting within 3 seconds (not ALL future clips)
        // to avoid saturating the network with parallel GCS requests
        seekVideoIfNeeded(video, clip.in_point_ms / 1000)
        if (!video.paused) safePause(video)
        videoPlayAttemptAtRef.current.delete(clipId)
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
          const clipSpeed = timing.speed
          if (audio.paused) {
            // Calculate the correct position within the audio file
            const audioTimeMs = timing.in_point_ms + (elapsed - timing.start_ms) * clipSpeed
            audio.currentTime = audioTimeMs / 1000
            audio.playbackRate = clipSpeed
            safePlay(audio, 'Failed to resume audio playback:')
          }
          // Apply fade effect based on current position
          audio.volume = calculateFadeVolume(elapsed, timing)
        } else {
          // Audio should be paused (outside clip range)
          if (!audio.paused) {
            safePause(audio)
          }
        }
      })

      // Sync video playback with timeline
      // Uses video.play() for smooth browser-native decoding, with drift correction
      videoRefsMap.current.forEach((video, clipId) => {
        const clip = findClipById(clipId)
        if (!clip) return

        const clipEffectiveEndMs = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
        const isActive = elapsed >= clip.start_ms && elapsed < clipEffectiveEndMs
        const isUpcoming = !isActive && elapsed < clip.start_ms && elapsed >= clip.start_ms - 2000

        if (isActive) {
          const speed = clip.speed || 1
          const isInFreezeRegion = elapsed >= clip.start_ms + clip.duration_ms
          if (isInFreezeRegion) {
            // During freeze frame: hold at last frame
            const lastFrameTimeMs = clip.in_point_ms + clip.duration_ms * speed
            seekVideoIfNeeded(video, lastFrameTimeMs / 1000)
            if (!video.paused) safePause(video)
            videoPlayAttemptAtRef.current.delete(clipId)
          } else {
            // Video should be playing
            const videoTimeMs = clip.in_point_ms + (elapsed - clip.start_ms) * speed
            const expectedTimeSec = videoTimeMs / 1000
            if (video.paused) {
              // Avoid seek/play spam while Chrome is still resolving a pending seek.
              seekVideoIfNeeded(video, expectedTimeSec, 0.12)
              requestVideoPlay(video, clipId, speed)
            } else {
              // Correct drift if video has drifted from expected position
              if (!video.seeking && video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
                const drift = Math.abs(video.currentTime - expectedTimeSec)
                if (drift > 0.2) {
                  seekVideoIfNeeded(video, expectedTimeSec, 0)
                }
              }
            }
          }
        } else if (isUpcoming) {
          // Pre-seek to first frame so it's ready when clip becomes active
          seekVideoIfNeeded(video, clip.in_point_ms / 1000)
          if (!video.paused) safePause(video)
          videoPlayAttemptAtRef.current.delete(clipId)
        } else {
          // Video should be paused (outside clip range)
          if (!video.paused) {
            safePause(video)
          }
          videoPlayAttemptAtRef.current.delete(clipId)
        }
      })

      if (elapsed < effectiveDurationMs) {
        playbackTimerRef.current = requestAnimationFrame(updatePlayhead)
      } else {
        // 最後まで再生したら停止（ループせず、currentTimeはそのまま維持）
        setCurrentTime(effectiveDurationMs || elapsed)
        stopPlayback()
      }
    }
    playbackTimerRef.current = requestAnimationFrame(updatePlayhead)
  }, [assetUrlCache, calculateFadeVolume, clearPendingPlaybackState, currentProject, currentTime, effectiveDurationMs, projectId, requestVideoPlay, safePause, safePlay, seekVideoIfNeeded, stopPlayback, timelineData])

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
      freeze_frame_ms?: number
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

    // Get the latest timeline data from store to avoid stale closure issues
    const storeState = useProjectStore.getState()
    const latestTimelineData = storeState.currentSequence?.timeline_data ?? storeState.currentProject?.timeline_data
    if (!latestTimelineData) return

    const updatedLayers = latestTimelineData.layers.map(layer => {
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
            const safeSpeed = Math.max(updates.speed, 0.1)
            newDurationMs = Math.round(sourceDuration / safeSpeed)
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
            freeze_frame_ms: updates.freeze_frame_ms ?? clip.freeze_frame_ms,
            duration_ms: newDurationMs,
            text_content: updates.text_content ?? clip.text_content,
            text_style: updates.text_style
              ? mergeTextStyle(
                  clip.text_style as Record<string, unknown> | undefined,
                  updates.text_style as Record<string, unknown>,
                )
              : clip.text_style,
          }
        }),
      }
    })

    handleTimelineUpdate({ ...latestTimelineData, layers: updatedLayers }, determineUpdateLabel(updates as Record<string, unknown>))

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
        freezeFrameMs: clip.freeze_frame_ms ?? 0,
        durationMs: clip.duration_ms,
        outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
        textContent: clip.text_content,
        textStyle: clip.text_style as typeof selectedVideoClip.textStyle,
        crop: clip.crop,
      })
    }
  }, [selectedVideoClip, projectId, currentTime, handleTimelineUpdate])

  // Local-only version of handleUpdateVideoClip (no API call, no undo history).
  // Used during slider drag for instant preview without flooding the backend.
  const handleUpdateVideoClipLocal = useCallback((
    updates: Parameters<typeof handleUpdateVideoClip>[0]
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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
            const safeSpeed = Math.max(updates.speed, 0.1)
            newDurationMs = Math.round(sourceDuration / safeSpeed)
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
            freeze_frame_ms: updates.freeze_frame_ms ?? clip.freeze_frame_ms,
            duration_ms: newDurationMs,
            text_content: updates.text_content ?? clip.text_content,
            text_style: updates.text_style
              ? mergeTextStyle(
                  clip.text_style as Record<string, unknown> | undefined,
                  updates.text_style as Record<string, unknown>,
                )
              : clip.text_style,
          }
        }),
      }
    })

    handleTimelineUpdateLocal({ ...timelineData, layers: updatedLayers })

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
        freezeFrameMs: clip.freeze_frame_ms ?? 0,
        durationMs: clip.duration_ms,
        outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
        textContent: clip.text_content,
        textStyle: clip.text_style as typeof selectedVideoClip.textStyle,
        crop: clip.crop,
      })
    }
  }, [selectedVideoClip, timelineData, projectId, currentTime, handleTimelineUpdateLocal])

  // Debounced version of handleUpdateVideoClip for continuous inputs (color pickers)
  // Prevents 409 ETag conflicts by coalescing rapid onChange events into a single API call
  const debouncedUpdateVideoClipRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const handleUpdateVideoClipDebounced = useCallback((updates: Parameters<typeof handleUpdateVideoClip>[0]) => {
    if (debouncedUpdateVideoClipRef.current) {
      clearTimeout(debouncedUpdateVideoClipRef.current)
    }
    debouncedUpdateVideoClipRef.current = setTimeout(() => {
      handleUpdateVideoClip(updates)
      debouncedUpdateVideoClipRef.current = null
    }, 300)
  }, [handleUpdateVideoClip])

  useEffect(() => {
    return () => {
      if (debouncedUpdateVideoClipRef.current) {
        clearTimeout(debouncedUpdateVideoClipRef.current)
        debouncedUpdateVideoClipRef.current = null
      }
    }
  }, [sequenceId])

  // Toggle or set freeze frame on a video clip
  const handleFreezeFrame = useCallback((clipId: string, layerId: string) => {
    const layer = timelineData?.layers.find(l => l.id === layerId)
    const clip = layer?.clips.find(c => c.id === clipId)
    if (!clip) return
    const currentFreeze = clip.freeze_frame_ms ?? 0
    const newFreeze = currentFreeze > 0 ? 0 : 3000
    // Select this clip and update via direct timeline update
    const asset = assets.find(a => a.id === clip.asset_id)
    setSelectedVideoClip({
      layerId,
      layerName: layer!.name,
      clipId,
      assetId: clip.asset_id,
      assetName: asset?.name ?? '',
      startMs: clip.start_ms,
      durationMs: clip.duration_ms,
      inPointMs: clip.in_point_ms,
      outPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
      transform: clip.transform,
      effects: clip.effects,
      keyframes: clip.keyframes,
      shape: clip.shape,
      crop: clip.crop,
      textContent: clip.text_content,
      textStyle: clip.text_style,
      fadeInMs: clip.effects.fade_in_ms ?? 0,
      fadeOutMs: clip.effects.fade_out_ms ?? 0,
      speed: clip.speed ?? 1,
      freezeFrameMs: newFreeze,
    })
    // Use direct timeline update instead of handleUpdateVideoClip to avoid dependency on selectedVideoClip
    if (!timelineData || !projectId) return
    const updatedLayers = timelineData.layers.map(l => {
      if (l.id !== layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== clipId) return c
          return { ...c, freeze_frame_ms: newFreeze }
        }),
      }
    })
    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, newFreeze > 0 ? i18n.t('editor:undo.freezeFrameChange') : i18n.t('editor:undo.freezeFrameChange'))
  }, [timelineData, projectId, assets, handleTimelineUpdate])

  // Update video clip timing (start_ms, duration_ms)
  const handleUpdateVideoClipTiming = useCallback(async (
    updates: Partial<{ startMs: number; durationMs: number }>
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.timingChange'))

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
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdate])

  // Delete selected video clip
  const handleDeleteVideoClip = useCallback(async () => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
      if (layer.id !== selectedVideoClip.layerId) return layer
      return {
        ...layer,
        clips: layer.clips.filter(clip => clip.id !== selectedVideoClip.clipId),
      }
    })

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.clipDelete'))
    setSelectedVideoClip(null)
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdate])

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
    if (!selectedClip || !timelineData || !projectId) return

    const updatedTracks = timelineData.audio_tracks.map(track => {
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

    handleTimelineUpdate({ ...timelineData, audio_tracks: updatedTracks }, i18n.t('editor:undo.audioChange'))

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
  }, [selectedClip, timelineData, projectId, handleTimelineUpdate])

  // Add volume keyframe at current playhead position
  const handleAddVolumeKeyframeAtCurrent = useCallback(async (volume: number = 1.0) => {
    if (!selectedClip || !timelineData || !projectId) return

    // Use ref to get the latest currentTime value (avoids stale closure)
    const latestCurrentTime = currentTimeRef.current

    // Get the LATEST clip data from timeline (selectedClip might be stale)
    const track = timelineData.audio_tracks.find(t => t.id === selectedClip.trackId)
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
    const newKeyframes = addVolumeKeyframe(existingKeyframes, timeInClipMs, volume)

    console.log('[VolumeKeyframe] New keyframes:', newKeyframes)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, timelineData, projectId, handleUpdateAudioClip])

  // Clear all volume keyframes
  const handleClearVolumeKeyframes = useCallback(async () => {
    if (!selectedClip || !timelineData || !projectId) return
    await handleUpdateAudioClip({ volume_keyframes: [] })
  }, [selectedClip, timelineData, projectId, handleUpdateAudioClip])

  // Remove a single volume keyframe by index
  const handleRemoveVolumeKeyframe = useCallback(async (index: number) => {
    if (!selectedClip || !timelineData || !projectId) return

    const track = timelineData.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    const newKeyframes = sortedKeyframes.filter((_, i) => i !== index)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, timelineData, projectId, handleUpdateAudioClip])

  // Update a single volume keyframe
  const handleUpdateVolumeKeyframe = useCallback(async (index: number, timeMs: number, value: number) => {
    if (!selectedClip || !timelineData || !projectId) return

    const track = timelineData.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip || !clip.volume_keyframes) return

    const sortedKeyframes = [...clip.volume_keyframes].sort((a, b) => a.time_ms - b.time_ms)
    const newKeyframes = sortedKeyframes.map((kf, i) =>
      i === index ? { time_ms: Math.max(0, Math.round(timeMs)), value: Math.max(0, Math.min(1, value)) } : kf
    )

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, timelineData, projectId, handleUpdateAudioClip])

  // Add volume keyframe with specific time and value
  const handleAddVolumeKeyframeManual = useCallback(async (timeMs: number, value: number) => {
    if (!selectedClip || !timelineData || !projectId) return

    const track = timelineData.audio_tracks.find(t => t.id === selectedClip.trackId)
    const clip = track?.clips.find(c => c.id === selectedClip.clipId)
    if (!clip) return

    const existingKeyframes = clip.volume_keyframes || []
    const newKeyframes = addVolumeKeyframe(existingKeyframes, timeMs, value)

    await handleUpdateAudioClip({ volume_keyframes: newKeyframes })
  }, [selectedClip, timelineData, projectId, handleUpdateAudioClip])

  // Fit, Fill, or Stretch video/image to canvas
  const handleFitFillStretch = useCallback((mode: 'fit' | 'fill' | 'stretch') => {
    console.log('[Fit/Fill/Stretch] Called with mode:', mode)
    if (!selectedVideoClip || !timelineData) return

    // Find the asset to get original dimensions
    const asset = assets.find(a => a.id === selectedVideoClip.assetId)
    const isImageClip = asset?.type === 'image'
    console.log('[Fit/Fill] isImageClip:', isImageClip, 'asset:', asset?.name)

    // Get the clip to access crop values
    const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
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

    const canvasWidth = currentProject?.width || 1920
    const canvasHeight = currentProject?.height || 1080

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
      const updatedLayers = timelineData.layers.map(l => {
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

      handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.fillModeChange'))
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
  }, [selectedVideoClip, timelineData, assets, currentProject?.height, currentProject?.width, handleUpdateVideoClip, handleTimelineUpdate])

  // Update shape properties
  const handleUpdateShape = useCallback(async (
    updates: Partial<Shape>
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.shapeChange'))

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip?.shape) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        shape: clip.shape,
      })
    }
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdate])

  // Local-only version of handleUpdateShape (no API call, no undo history).
  // Used during slider drag for instant preview without flooding the backend.
  const handleUpdateShapeLocal = useCallback((
    updates: Partial<Shape>
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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

    handleTimelineUpdateLocal({ ...timelineData, layers: updatedLayers })

    // Update selected clip state to reflect changes
    const layer = updatedLayers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (clip?.shape) {
      setSelectedVideoClip({
        ...selectedVideoClip,
        shape: clip.shape,
      })
    }
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdateLocal])

  // Debounced version of handleUpdateShape for continuous inputs (color pickers)
  const debouncedUpdateShapeRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const handleUpdateShapeDebounced = useCallback((updates: Partial<Shape>) => {
    if (debouncedUpdateShapeRef.current) {
      clearTimeout(debouncedUpdateShapeRef.current)
    }
    debouncedUpdateShapeRef.current = setTimeout(() => {
      handleUpdateShape(updates)
      debouncedUpdateShapeRef.current = null
    }, 300)
  }, [handleUpdateShape])

  useEffect(() => {
    return () => {
      if (debouncedUpdateShapeRef.current) {
        clearTimeout(debouncedUpdateShapeRef.current)
        debouncedUpdateShapeRef.current = null
      }
    }
  }, [sequenceId])

  // Render full composite frame at current playhead and show in lightbox
  const handleCompositePreview = useCallback(async () => {
    if (!projectId) return
    setCompositeLightboxLoading(true)
    try {
      const result = await projectsApi.sampleFrame(projectId, {
        time_ms: currentTime,
        resolution: '1920x1080',
      })
      setCompositeLightbox({
        src: `data:image/png;base64,${result.frame_base64}`,
        timeMs: result.time_ms,
      })
    } catch (err) {
      console.error('Composite preview failed:', err)
      setChromaPreviewError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as Error).message
        || t('editor.compositePreview')
      )
    } finally {
      setCompositeLightboxLoading(false)
    }
  }, [projectId, currentTime, t])

  // Local-only version of handleUpdateShapeFade (no API call, no undo history).
  const handleUpdateShapeFadeLocal = useCallback((
    updates: { fadeInMs?: number; fadeOutMs?: number }
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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

    handleTimelineUpdateLocal({ ...timelineData, layers: updatedLayers })

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
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdateLocal])

  // Update shape fade properties
  const handleUpdateShapeFade = useCallback(async (
    updates: { fadeInMs?: number; fadeOutMs?: number }
  ) => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const updatedLayers = timelineData.layers.map(layer => {
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

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.fadeChange'))

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
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdate])

  // Add or update keyframe at current time
  const handleAddKeyframe = useCallback(async () => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return

    // Calculate time relative to clip start
    const timeInClipMs = currentTime - clip.start_ms
    if (timeInClipMs < 0 || timeInClipMs > clip.duration_ms) {
      alert(t('conflict.title'))
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

    const updatedLayers = timelineData.layers.map(l => {
      if (l.id !== selectedVideoClip.layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== selectedVideoClip.clipId) return c
          return { ...c, keyframes: newKeyframes }
        }),
      }
    })

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.keyframeAdd'))

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
  }, [selectedVideoClip, timelineData, projectId, currentTime, handleTimelineUpdate, t])

  // Remove keyframe at current time
  const handleRemoveKeyframe = useCallback(async () => {
    if (!selectedVideoClip || !timelineData || !projectId) return

    const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return

    const timeInClipMs = currentTime - clip.start_ms

    const newKeyframes = removeKeyframe(clip, timeInClipMs)

    const updatedLayers = timelineData.layers.map(l => {
      if (l.id !== selectedVideoClip.layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== selectedVideoClip.clipId) return c
          return { ...c, keyframes: newKeyframes }
        }),
      }
    })

    handleTimelineUpdate({ ...timelineData, layers: updatedLayers }, i18n.t('editor:undo.keyframeDelete'))

    setSelectedVideoClip({
      ...selectedVideoClip,
      keyframes: newKeyframes,
    })
  }, [selectedVideoClip, timelineData, projectId, currentTime, handleTimelineUpdate])

  // Check if keyframe exists at current time
  const currentKeyframeExists = useCallback(() => {
    if (!selectedVideoClip || !timelineData) return false

    const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
    const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
    if (!clip) return false

    const timeInClipMs = currentTime - clip.start_ms
    return hasKeyframeAt(clip, timeInClipMs)
  }, [selectedVideoClip, timelineData, currentTime])

  // Get current interpolated values for display
  const getCurrentInterpolatedValues = useCallback(() => {
    if (!selectedVideoClip || !timelineData) return null

    const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
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
  }, [selectedVideoClip, timelineData, currentTime])

  // Handle keyframe selection from timeline diamond markers
  const handleKeyframeSelect = useCallback((clipId: string, keyframeIndex: number | null) => {
    if (!timelineData) return

    // Find the clip to select it and move playhead
    for (const layer of timelineData.layers) {
      const clip = layer.clips.find(c => c.id === clipId)
      if (clip) {
        // Select the clip if not already selected
        if (selectedVideoClip?.clipId !== clipId) {
          const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
          let assetName = 'Clip'
          if (asset) assetName = asset.name
          else if (clip.text_content) assetName = `${t('timeline.text')}: ${clip.text_content.slice(0, 10)}`
          else if (clip.shape) {
            assetName = ({
              rectangle: t('timeline.rectangle'),
              circle: t('timeline.circle'),
              line: t('timeline.line'),
              arrow: t('timeline.arrow'),
            } as const)[clip.shape.type] || clip.shape.type
          }
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
  }, [timelineData, selectedVideoClip, assets, t])

  // Sync all video frames with timeline when not playing
  // Compute clip position directly for better accuracy
  useEffect(() => {
    if (isPlaying || !timelineData) return

    // Build a map of clipId -> clip data for quick lookup
    const clipMap = new Map<string, { start_ms: number; in_point_ms: number; duration_ms: number; asset_id: string | null; speed?: number; freeze_frame_ms?: number }>()
    const layers = timelineData.layers
    for (const layer of layers) {
      if (layer.visible === false) continue
      for (const clip of layer.clips) {
        clipMap.set(clip.id, clip)
      }
    }

    // Sync each video element to its clip's current frame
    // Also pre-seek upcoming clips (within 500ms lookahead) to prevent blackout on transition
    videoRefsMap.current.forEach((video, clipId) => {
      const clip = clipMap.get(clipId)
      if (!clip) return

      const clipEffectiveEnd = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
      const isActive = currentTime >= clip.start_ms && currentTime < clipEffectiveEnd
      const isUpcoming = !isActive && currentTime < clip.start_ms && currentTime >= clip.start_ms - 500

      if (isActive) {
        const speed = clip.speed || 1
        const isInFreezeRegion = currentTime >= clip.start_ms + clip.duration_ms
        if (isInFreezeRegion) {
          // During freeze frame: show last frame
          const lastFrameTimeMs = clip.in_point_ms + clip.duration_ms * speed
          const targetTime = lastFrameTimeMs / 1000
          if (Math.abs(video.currentTime - targetTime) > 0.05) {
            video.currentTime = targetTime
          }
        } else {
          // Video time = in_point + (timeline elapsed) * speed
          const videoTimeMs = clip.in_point_ms + (currentTime - clip.start_ms) * speed
          const targetTime = videoTimeMs / 1000

          // Only seek if difference is significant (avoid micro-seeks)
          if (Math.abs(video.currentTime - targetTime) > 0.05) {
            video.currentTime = targetTime
          }
        }
      } else if (isUpcoming) {
        // Pre-seek to first frame so it's ready when clip becomes active
        const targetTime = clip.in_point_ms / 1000
        if (Math.abs(video.currentTime - targetTime) > 0.05) {
          video.currentTime = targetTime
        }
      }
    })
  }, [currentTime, isPlaying, timelineData])

  // Cleanup on unmount
  useEffect(() => {
    const audioElements = audioRefs.current
    const audioTimingEntries = audioClipTimingRefs.current
    const videoPlayAttempts = videoPlayAttemptAtRef.current
    const pendingPauseAfterPlay = pendingPauseAfterPlayRef.current
    const pendingPlayPromises = pendingPlayPromisesRef.current

    return () => {
      if (playbackTimerRef.current) {
        cancelAnimationFrame(playbackTimerRef.current)
      }
      audioElements.forEach(audio => {
        safePause(audio)
        audio.src = ''
        clearPendingPlaybackState(audio)
      })
      audioElements.clear()
      audioTimingEntries.clear()
      videoPlayAttempts.clear()
      pendingPauseAfterPlay.clear()
      pendingPlayPromises.clear()
    }
  }, [clearPendingPlaybackState, safePause])

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
            const label = getRedoLabel()
            setIsUndoRedoInProgress(true)
            try {
              const success = await runHistoryMutation('redo')
              if (success && label) setToastMessage({ text: t('editor.redid', { label }), type: 'info', duration: 1500 })
            } finally {
              setTimeout(() => setIsUndoRedoInProgress(false), 150)
            }
          }
        } else {
          // Undo: Ctrl/Cmd + Z
          e.preventDefault()
          if (projectId && canUndo() && !isUndoRedoInProgress) {
            const label = getUndoLabel()
            setIsUndoRedoInProgress(true)
            try {
              const success = await runHistoryMutation('undo')
              if (success && label) setToastMessage({ text: t('editor.undid', { label }), type: 'info', duration: 1500 })
            } finally {
              setTimeout(() => setIsUndoRedoInProgress(false), 150)
            }
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'y') {
        // Redo: Ctrl/Cmd + Y (alternative)
        e.preventDefault()
        if (projectId && canRedo() && !isUndoRedoInProgress) {
          const label = getRedoLabel()
          setIsUndoRedoInProgress(true)
          try {
            const success = await runHistoryMutation('redo')
            if (success && label) setToastMessage({ text: t('editor.redid', { label }), type: 'info', duration: 1500 })
          } finally {
            setTimeout(() => setIsUndoRedoInProgress(false), 150)
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        // Save session: Ctrl/Cmd + S
        e.preventDefault()
        if (currentSessionId && currentSessionName && !savingSession) {
          // Overwrite save existing session
          void handleSaveSession(currentSessionId, currentSessionName).catch(() => {})
        } else if (!currentSessionId) {
          // No session loaded - show toast hint
          setToastMessage({ text: t('editor.saveSuggestion'), type: 'info' })
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'c') {
        // Copy clip: Ctrl/Cmd + C
        e.preventDefault()
        if (selectedVideoClip && timelineData) {
          // Copy video clip
          const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
          const clip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
          if (clip) {
            setCopiedClip({
              type: 'video',
              layerId: selectedVideoClip.layerId,
              clip: JSON.parse(JSON.stringify(clip)) // Deep copy
            })
            setToastMessage({ text: t('editor.clipCopied'), type: 'success' })
          }
        } else if (selectedClip && timelineData) {
          // Copy audio clip
          const track = timelineData.audio_tracks.find(t => t.id === selectedClip.trackId)
          const clip = track?.clips.find(c => c.id === selectedClip.clipId)
          if (clip) {
            setCopiedClip({
              type: 'audio',
              trackId: selectedClip.trackId,
              clip: JSON.parse(JSON.stringify(clip)) // Deep copy
            })
            setToastMessage({ text: t('editor.audioCopied'), type: 'success' })
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'v') {
        // Paste clip: Ctrl/Cmd + V
        e.preventDefault()
        if (!copiedClip || !timelineData || !projectId) return

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
          const targetLayer = timelineData.layers.find(l => l.id === targetLayerId)
          if (!targetLayer) {
            // If original layer doesn't exist, use first layer
            targetLayerId = timelineData.layers[0]?.id
          }

          if (targetLayerId) {
            const updatedLayers = timelineData.layers.map(layer => {
              if (layer.id === targetLayerId) {
                return { ...layer, clips: [...layer.clips, newClip] }
              }
              return layer
            })

            handleTimelineUpdate({
              ...timelineData,
              layers: updatedLayers
            }, i18n.t('editor:undo.audioClipPaste'))
            setToastMessage({ text: t('editor.clipPasted'), type: 'success' })
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
          const originalTrack = timelineData.audio_tracks.find(t => t.id === targetTrackId)
          if (!originalTrack) {
            // If original track doesn't exist, use first track
            targetTrackId = timelineData.audio_tracks[0]?.id
          }

          if (targetTrackId) {
            const updatedTracks = timelineData.audio_tracks.map(track => {
              if (track.id === targetTrackId) {
                return { ...track, clips: [...track.clips, newClip] }
              }
              return track
            })

            handleTimelineUpdate({
              ...timelineData,
              audio_tracks: updatedTracks
            }, i18n.t('editor:undo.audioClipPaste'))
            setToastMessage({ text: t('editor.audioPasted'), type: 'success' })
          }
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [projectId, canUndo, canRedo, isUndoRedoInProgress, currentSessionId, currentSessionName, savingSession, handleSaveSession, selectedVideoClip, selectedClip, timelineData, copiedClip, currentTime, handleTimelineUpdate, getRedoLabel, getUndoLabel, runHistoryMutation, t])

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

      if (!selectedVideoClip || !timelineData || !projectId) return

      e.preventDefault()

      // Find the video clip to get its group_id
      const layer = timelineData.layers.find(l => l.id === selectedVideoClip.layerId)
      const videoClip = layer?.clips.find(c => c.id === selectedVideoClip.clipId)
      const groupId = videoClip?.group_id

      // Remove the video clip from layers
      const updatedLayers = timelineData.layers.map((l) =>
        l.id === selectedVideoClip.layerId
          ? { ...l, clips: l.clips.filter((c) => c.id !== selectedVideoClip.clipId) }
          : l
      )

      // Also remove audio clips in the same group
      const updatedTracks = timelineData.audio_tracks.map((track) => ({
        ...track,
        clips: track.clips.filter((c) => {
          if (groupId && c.group_id === groupId) return false
          return true
        })
      }))

      handleTimelineUpdate({ ...timelineData, layers: updatedLayers, audio_tracks: updatedTracks }, i18n.t('editor:undo.clipDelete'))
      setSelectedVideoClip(null)
    }

    window.addEventListener('keydown', handleDeleteKey)
    return () => window.removeEventListener('keydown', handleDeleteKey)
  }, [selectedVideoClip, timelineData, projectId, handleTimelineUpdate])

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
          <p className="text-red-500 mb-4">{error || t('editor.projectNotFound')}</p>
          <button
            data-testid="editor-back-to-dashboard"
            onClick={goToDashboard}
            className="text-primary-500 hover:text-primary-400"
          >
            {t('editor.backToDashboard')}
          </button>
        </div>
      </div>
    )
  }

  const saveStatusLabel = sequenceSaveState === 'saving'
    ? t('editor.sequenceSaving')
    : sequenceSaveState === 'failed'
      ? t('editor.sequenceFailed')
      : lastSequenceSaveAt
        ? t('editor.sequenceSavedAt', { time: formatSaveTime(lastSequenceSaveAt) })
        : t('editor.sequenceSaved')

  return (
    <div className={`h-screen bg-gray-900 flex flex-col overflow-hidden ${(isResizingLeftPanel || isResizingRightPanel || isResizingAiPanel || isResizingActivityPanel) ? 'cursor-ew-resize select-none' : ''} ${isResizingChromaPreview ? 'cursor-ns-resize select-none' : ''}`}>
      {/* Read-only banner */}
      {isReadOnly && (
        <div className="bg-yellow-600/80 text-white px-4 py-2 text-sm text-center flex-shrink-0 flex items-center justify-center gap-3">
          <span>{lockHolder ? t('editor.editingBy', { user: lockHolder }) : t('editor.viewOnly')}</span>
          <button
            onClick={() => retryLock()}
            className="px-2 py-0.5 bg-white/20 hover:bg-white/30 rounded text-xs transition-colors"
          >
            {t('editor.reacquireLock')}
          </button>
        </div>
      )}
      {/* Header */}
      <header data-testid="editor-header" className="h-12 bg-gray-800 border-b border-gray-700 flex items-center px-3 flex-shrink-0 sticky top-0 z-50">
        {/* Left: Navigation */}
        <div className="flex items-center gap-1">
          <button
            data-testid="editor-open-exit-confirm"
            onClick={() => setShowExitConfirm(true)}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            title={t('editor.backToProjects')}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <h1 className="text-white font-medium flex items-center text-sm ml-1">
            {currentProject.name}
            {currentSequence && (
              <>
                <span className="mx-2 text-gray-500">/</span>
                <span className="text-primary-400">{currentSequence.name}</span>
              </>
            )}
          </h1>
          <button
            onClick={() => setShowSettingsModal(true)}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            title={t('editor.projectSettings')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </button>
          <button
            onClick={() => setShowShortcutsModal(true)}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            title={t('editor.keyboardShortcuts')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
            </svg>
          </button>
          <button
            onClick={() => setShowMembersModal(true)}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            title={t('editor.membersManagement')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          </button>
          <PresenceIndicator users={presenceUsers} />
        </div>

        <div className="w-px h-6 bg-gray-600 mx-3" />

        {/* Center: Edit Operations */}
        <div className="flex items-center gap-1">
          {/* Undo button with long-press dropdown */}
          <div className="relative" ref={undoDropdownRef}>
            <button
              onMouseDown={() => {
                if (!canUndo() || isUndoRedoInProgress) return
                undoLongPressRef.current = setTimeout(() => {
                  setUndoDropdownOpen(true)
                  setRedoDropdownOpen(false)
                  undoLongPressRef.current = null
                }, 300)
              }}
              onMouseUp={async () => {
                if (undoLongPressRef.current) {
                  clearTimeout(undoLongPressRef.current)
                  undoLongPressRef.current = null
                  // Short press: normal undo
                  if (!undoDropdownOpen && !isUndoRedoInProgress && projectId) {
                    const label = getUndoLabel()
                    setIsUndoRedoInProgress(true)
                    try {
                      const success = await runHistoryMutation('undo')
                      if (success && label) setToastMessage({ text: t('editor.undid', { label }), type: 'info', duration: 1500 })
                    } finally {
                      setTimeout(() => setIsUndoRedoInProgress(false), 150)
                    }
                  }
                }
              }}
              onMouseLeave={() => {
                if (undoLongPressRef.current) {
                  clearTimeout(undoLongPressRef.current)
                  undoLongPressRef.current = null
                }
              }}
              disabled={!canUndo() || isUndoRedoInProgress}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title={undoTooltip + t('editor.undoLongPress')}
            >
              {isUndoRedoInProgress ? (
                <div className="w-5 h-5 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                </svg>
              )}
            </button>
            {undoDropdownOpen && (
              <div className="absolute top-full left-0 mt-1 bg-gray-700 rounded shadow-lg z-50 min-w-[200px] py-1 border border-gray-600">
                <div className="px-3 py-1 text-[10px] text-gray-500 uppercase tracking-wider border-b border-gray-600">{t('editor.undoHistory')}</div>
                {timelineHistory.length === 0 ? (
                  <div className="px-3 py-1.5 text-xs text-gray-500">{t('editor.noHistory')}</div>
                ) : (
                  [...timelineHistory].reverse().slice(0, 10).map((entry, index) => (
                    <button
                      key={index}
                      className="w-full px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-600 flex items-center gap-2"
                      onClick={() => handleUndoMultiple(index + 1)}
                    >
                      <span className="text-gray-500 w-4 text-right shrink-0">{index + 1}.</span>
                      <span className="truncate">{entry.label}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          {/* Redo button with long-press dropdown */}
          <div className="relative" ref={redoDropdownRef}>
            <button
              onMouseDown={() => {
                if (!canRedo() || isUndoRedoInProgress) return
                redoLongPressRef.current = setTimeout(() => {
                  setRedoDropdownOpen(true)
                  setUndoDropdownOpen(false)
                  redoLongPressRef.current = null
                }, 300)
              }}
              onMouseUp={async () => {
                if (redoLongPressRef.current) {
                  clearTimeout(redoLongPressRef.current)
                  redoLongPressRef.current = null
                  // Short press: normal redo
                  if (!redoDropdownOpen && !isUndoRedoInProgress && projectId) {
                    const label = getRedoLabel()
                    setIsUndoRedoInProgress(true)
                    try {
                      const success = await runHistoryMutation('redo')
                      if (success && label) setToastMessage({ text: t('editor.redid', { label }), type: 'info', duration: 1500 })
                    } finally {
                      setTimeout(() => setIsUndoRedoInProgress(false), 150)
                    }
                  }
                }
              }}
              onMouseLeave={() => {
                if (redoLongPressRef.current) {
                  clearTimeout(redoLongPressRef.current)
                  redoLongPressRef.current = null
                }
              }}
              disabled={!canRedo() || isUndoRedoInProgress}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title={redoTooltip + t('editor.undoLongPress')}
            >
              {isUndoRedoInProgress ? (
                <div className="w-5 h-5 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 10h-10a8 8 0 00-8 8v2M21 10l-6 6m6-6l-6-6" />
                </svg>
              )}
            </button>
            {redoDropdownOpen && (
              <div className="absolute top-full left-0 mt-1 bg-gray-700 rounded shadow-lg z-50 min-w-[200px] py-1 border border-gray-600">
                <div className="px-3 py-1 text-[10px] text-gray-500 uppercase tracking-wider border-b border-gray-600">{t('editor.redoHistory')}</div>
                {timelineFuture.length === 0 ? (
                  <div className="px-3 py-1.5 text-xs text-gray-500">{t('editor.noHistory')}</div>
                ) : (
                  timelineFuture.slice(0, 10).map((entry, index) => (
                    <button
                      key={index}
                      className="w-full px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-600 flex items-center gap-2"
                      onClick={() => handleRedoMultiple(index + 1)}
                    >
                      <span className="text-gray-500 w-4 text-right shrink-0">{index + 1}.</span>
                      <span className="truncate">{entry.label}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          <button
            onClick={handleSyncToggle}
            className={`ml-1 p-1.5 rounded transition-colors ${
              isSyncEnabled
                ? 'text-green-400 hover:bg-green-600/20'
                : 'text-gray-400 hover:text-white hover:bg-gray-700'
            }`}
            title={isSyncEnabled ? t('editor.syncEnabled') : t('editor.syncDisabled')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>

        <div className="w-px h-6 bg-gray-600 mx-3" />

        {/* Duration display */}
        <span className="text-gray-500 text-xs font-mono tabular-nums">
          {Math.floor(currentProject.duration_ms / 60000)}:
          {Math.floor((currentProject.duration_ms % 60000) / 1000).toString().padStart(2, '0')}
        </span>

        {/* Right: Save & Export */}
        <div className="ml-auto flex items-center gap-2">
          <div
            data-testid="sequence-save-status"
            className={`px-2.5 py-1 rounded-full border text-xs font-medium flex items-center gap-2 ${
              sequenceSaveState === 'failed'
                ? 'border-red-400/40 bg-red-500/10 text-red-200'
                : sequenceSaveState === 'saving'
                  ? 'border-amber-400/40 bg-amber-500/10 text-amber-100'
                  : 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200'
            }`}
            title={t('editor.sequenceSaveStatusTitle')}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                sequenceSaveState === 'failed'
                  ? 'bg-red-300'
                  : sequenceSaveState === 'saving'
                    ? 'bg-amber-300'
                    : 'bg-emerald-300'
              }`}
            />
            <span>{saveStatusLabel}</span>
          </div>
          <button
            data-testid="editor-ai-toggle"
            onClick={() => setIsAIChatOpen(prev => !prev)}
            className={`px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5 ${
              isAIChatOpen
                ? 'bg-primary-600 text-white'
                : 'bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white'
            }`}
            title={t('editor.aiAssistant')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
            </svg>
            AI
          </button>
          <button
            onClick={() => setShowHistoryModal(true)}
            className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white text-sm rounded transition-colors flex items-center gap-1.5"
            title={t('editor.exportHistory')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {t('editor.history')}
          </button>
          <div className="w-px h-6 bg-gray-600 mx-1" />
          <button
            onClick={() => {
              loadRenderHistory()
              setShowRenderModal(true)
            }}
            disabled={renderJob?.status === 'queued' || renderJob?.status === 'processing'}
            className="px-4 py-1.5 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            {t('editor.videoExport')}
          </button>
          <button
            data-testid="editor-render-package-button"
            onClick={() => void handleDownloadRenderPackage()}
            disabled={isRenderPackageLoading}
            className="px-4 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-200 hover:text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-wait flex items-center gap-1.5"
            title={t('editor.renderPackageTooltip')}
          >
            {isRenderPackageLoading ? (
              <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-current" />
            ) : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
            )}
            {isRenderPackageLoading ? t('editor.renderPackagePreparing') : t('editor.renderPackage')}
          </button>
        </div>
      </header>

      {sequenceSaveState === 'failed' && (
        <div className="border-b border-red-500/30 bg-red-500/15 px-4 py-3 text-sm text-red-100 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="font-medium">{t('editor.sequenceSaveFailedBanner')}</div>
            <div className="text-red-100/80">
              {sequenceSaveError || t('editor.sequenceSaveFailedMessage')}
              {lastSequenceSaveAt && (
                <span className="ml-2">{t('editor.sequenceSaveLastSuccess', { time: formatSaveTime(lastSequenceSaveAt) })}</span>
              )}
            </div>
          </div>
          <button
            onClick={() => void retrySequenceSave()}
            className="px-3 py-1.5 rounded bg-red-500/20 hover:bg-red-500/30 text-white transition-colors shrink-0"
          >
            {t('editor.retrySave')}
          </button>
        </div>
      )}

      {sessionSaveFailure && (
        <div className="border-b border-amber-500/30 bg-amber-500/15 px-4 py-3 text-sm text-amber-50 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="font-medium">{t('editor.sessionSaveFailedBanner', { name: sessionSaveFailure.attempt.sessionName })}</div>
            <div className="text-amber-50/80">
              {sessionSaveFailure.message} {t('editor.sessionSaveFailedKeepEditing')}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => void retryFailedSessionSave()}
              className="px-3 py-1.5 rounded bg-amber-500/20 hover:bg-amber-500/30 text-white transition-colors"
            >
              {t('editor.retrySave')}
            </button>
            <button
              onClick={clearSessionSaveFailure}
              className="px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 text-white transition-colors"
            >
              {t('editor.dismissSaveWarning')}
            </button>
          </div>
        </div>
      )}

      {/* Export Dialog */}
      {showRenderModal && (
        <Suspense fallback={null}>
          <LazyExportDialog
            isOpen={showRenderModal}
            onClose={() => {
              setShowRenderModal(false)
              clearRenderJob()
            }}
            onStartExport={handleStartRender}
            onCancelExport={handleCancelRender}
            onDownload={handleDownloadVideo}
            renderJob={renderJob}
            totalDurationMs={effectiveDurationMs}
          />
        </Suspense>
      )}

      {/* Exit Confirmation Modal */}
      {showExitConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">{t('editor.backTitle')}</h3>
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
              {t('editor.backMessage')}
            </p>
            <p className="text-gray-500 text-sm mb-4">
              {sequenceSaveState === 'failed'
                ? t('editor.sequenceSaveExitWarning')
                : sequenceSaveState === 'saving'
                  ? t('editor.sequenceSaveInProgressWarning')
                  : t('editor.backNote')}
            </p>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowExitConfirm(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                {t('editor.backCancel')}
              </button>
              <button
                data-testid="editor-confirm-exit"
                onClick={() => {
                  setShowExitConfirm(false)
                  goToDashboard()
                }}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm rounded transition-colors"
              >
                {t('editor.backConfirm')}
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
              <h3 className="text-white font-medium text-lg">{t('editor.settingsTitle')}</h3>
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
              <label className="block text-sm text-gray-400 mb-2">{t('editor.preset')}</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: '1920×1080', w: 1920, h: 1080, desc: 'Full HD' },
                  { label: '1280×720', w: 1280, h: 720, desc: 'HD' },
                  { label: '1080×1920', w: 1080, h: 1920, desc: 'Full HD (vertical)' },
                  { label: '1080×1080', w: 1080, h: 1080, desc: 'Square' },
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
              <label className="block text-sm text-gray-400 mb-2">{t('editor.customSize')}</label>
              <div className="flex items-center gap-2">
                <NumericInput
                  value={currentProject.width}
                  onCommit={(val) => handleUpdateProjectDimensions(val, currentProject.height)}
                  min={256}
                  max={4096}
                  step={2}
                  formatDisplay={(v) => String(Math.round(v))}
                  className="w-24 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                  placeholder={t('editor.widthPlaceholder')}
                />
                <span className="text-gray-400">×</span>
                <NumericInput
                  value={currentProject.height}
                  onCommit={(val) => handleUpdateProjectDimensions(currentProject.width, val)}
                  min={256}
                  max={4096}
                  step={2}
                  formatDisplay={(v) => String(Math.round(v))}
                  className="w-24 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                  placeholder={t('editor.heightPlaceholder')}
                />
                <span className="text-gray-400 text-xs">px</span>
              </div>
              <p className="text-xs text-gray-500 mt-1">{t('editor.sizeNote')}</p>
            </div>

            {/* AI Assistant Settings */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <label className="block text-sm text-gray-400 mb-2">{t('editor.aiSettings')}</label>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs text-gray-500 w-20">{t('editor.providerLabel')}</span>
                <select
                  value={currentProject.ai_provider || ''}
                  onChange={(e) => {
                    const value = e.target.value as 'openai' | 'gemini' | 'anthropic' | ''
                    handleUpdateAIProvider(value || null)
                  }}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                >
                  <option value="">{t('editor.providerNotSelected')}</option>
                  <option value="openai">OpenAI (GPT-4o)</option>
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic Claude</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-20">{t('editor.apiKeyStatus')}</span>
                {currentProject.ai_api_key ? (
                  <span className="flex-1 px-2 py-1 bg-gray-700 text-green-400 text-sm rounded border border-green-600">
                    {t('editor.apiKeySet')}
                  </span>
                ) : (
                  <span className="flex-1 px-2 py-1 bg-gray-700 text-yellow-400 text-sm rounded border border-yellow-600">
                    {t('editor.apiKeyNotSet')}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2">
                <span className="text-xs text-gray-500 w-20">{currentProject.ai_api_key ? t('editor.apiKeyChangeLabel') : ''}</span>
                <input
                  type="password"
                  placeholder={t('editor.apiKeyNewPlaceholder')}
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
                    {t('editor.save')}
                  </button>
                )}
              </div>
            </div>

            {/* Default Image Duration Setting */}
            <div className="mb-4 pt-4 border-t border-gray-700">
              <label className="block text-sm text-gray-400 mb-2">{t('editor.timelineSettings')}</label>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-32">{t('editor.defaultImageDuration')}</span>
                <select
                  value={defaultImageDurationMs}
                  onChange={(e) => setDefaultImageDurationMs(Number(e.target.value))}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
                >
                  <option value={1000}>1{t('editor.sec')}</option>
                  <option value={2000}>2{t('editor.sec')}</option>
                  <option value={3000}>3{t('editor.sec')}</option>
                  <option value={5000}>5{t('editor.sec')}</option>
                  <option value={10000}>10{t('editor.sec')}</option>
                  <option value={15000}>15{t('editor.sec')}</option>
                  <option value={30000}>30{t('editor.sec')}</option>
                </select>
              </div>
              <p className="text-xs text-gray-500 mt-1">{t('editor.sizeNote')}</p>
            </div>

            {/* Activity Panel Settings removed - now driven by operations */}

            <div className="flex justify-end">
              <button
                onClick={() => setShowSettingsModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                {t('editor.closeSettings')}
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
              <h3 className="text-white font-medium text-lg">{t('editor.keyboardShortcutsTitle')}</h3>
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
              <h4 className="text-sm font-medium text-gray-300 mb-2">{t('editor.timelineShortcuts')}</h4>
              <div className="space-y-1">
                {[
                  { key: 'Delete / Backspace', desc: t('undo.clipDelete') },
                  { key: 'S', desc: t('timeline.snapOff') + '/' + t('timeline.snapOn') },
                  { key: 'C', desc: t('timeline.cut') },
                  { key: 'A', desc: t('timeline.selectAfterPlayhead') },
                  { key: 'Shift + E', desc: t('timeline.scrollToEnd') },
                  { key: 'Shift + H', desc: t('timeline.scrollToPlayhead') },
                  { key: 'Escape', desc: t('timeline.contextMenu.copy') + '/' + t('timeline.delete') },
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
              <h4 className="text-sm font-medium text-gray-300 mb-2">{t('editor.editShortcuts')}</h4>
              <div className="space-y-1">
                {[
                  { key: '⌘/Ctrl + Z', desc: t('editor.undoNoLabel') },
                  { key: '⌘/Ctrl + Shift + Z', desc: t('editor.redoNoLabel') },
                  { key: '⌘/Ctrl + Y', desc: t('editor.redoNoLabel') },
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
              <h4 className="text-sm font-medium text-gray-300 mb-2">{t('editor.textShortcuts')}</h4>
              <div className="space-y-1">
                {[
                  { key: 'Enter', desc: t('transcription.applyButton') },
                  { key: 'Escape', desc: t('editor.sessionCancel') },
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
              {t('editor.shortcutsNote')}
            </p>

            <div className="flex justify-end mt-4">
              <button
                onClick={() => setShowShortcutsModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                {t('editor.closeShortcuts')}
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
              <h3 className="text-white font-medium text-lg">{t('editor.exportHistoryTitle')}</h3>
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
                          {job.completed_at && new Date(job.completed_at).toLocaleString(i18nHook.language === 'ja' ? 'ja-JP' : 'en-US')}
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
                            {job.status === 'completed' ? t('editor.exportStatus.completed') :
                             job.status === 'failed' ? t('editor.exportStatus.failed') :
                             job.status === 'processing' ? t('editor.exportStatus.processing') : t('editor.exportStatus.queued')}
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
                          {t('editor.downloadLink')}
                        </button>
                      ) : job.status === 'completed' ? (
                        <span className="ml-3 text-sm text-gray-500">{t('editor.expired')}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-500">
                  {t('editor.noExportHistory')}
                </div>
              )}
            </div>

            <div className="flex justify-end mt-4 pt-4 border-t border-gray-700">
              <button
                onClick={() => setShowHistoryModal(false)}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                {t('editor.closeHistory')}
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
            data-testid="left-panel"
            className="bg-gray-800 border-r border-gray-700 flex flex-col overflow-y-auto relative"
            style={{ width: leftPanelWidth, scrollbarGutter: 'stable' }}
          >
            <Suspense fallback={<div className="h-full bg-gray-800" />}>
              <LazyLeftPanel
                assetLibraryReady={currentSequence?.id === sequenceId}
                projectId={currentProject.id}
                currentSequenceId={sequenceId}
                onPreviewAsset={handlePreviewAsset}
                onAssetsChange={fetchAssets}
                refreshTrigger={assetLibraryRefreshTrigger}
                onClose={() => setIsAssetPanelOpen(false)}
                onSnapshotRestored={() => {
                  if (projectId && sequenceId) {
                    fetchSequence(projectId, sequenceId)
                  }
                }}
              />
            </Suspense>
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
            <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>{t('editor.assetPanel')}</span>
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
            data-testid="preview-container"
            className="bg-gray-900 flex flex-col items-center px-1 py-1 flex-shrink-0 relative"
            style={{ height: previewHeight }}
            onMouseMove={handlePreviewMouseMove}
            onMouseLeave={handlePreviewMouseLeave}
            onClick={(e) => {
              // Deselect when clicking on the outer gray area
              if (e.target === e.currentTarget) {
                setSelectedVideoClip(null)
                setSelectedClip(null)
              }
            }}
          >
            {/* Preview controls: border settings, snap, zoom */}
            <div className={`absolute top-2 right-2 flex items-center gap-1.5 bg-gray-900/60 hover:bg-gray-900/90 backdrop-blur-sm rounded-lg px-2 py-1 z-10 transition-all duration-300 ${showPreviewControls ? 'opacity-80 hover:opacity-100' : 'opacity-0 pointer-events-none'}`}>
              {/* Border settings */}
              <span className="text-gray-400 text-[10px]">{t('editor.previewBorder')}</span>
              <input
                type="color"
                value={previewBorderColor}
                onChange={(e) => setPreviewBorderColor(e.target.value)}
                className="w-5 h-5 rounded cursor-pointer border border-gray-600 bg-transparent p-0"
                title={t('editor.borderColor')}
              />
              <NumericInput
                value={previewBorderWidth}
                onCommit={(val) => setPreviewBorderWidth(val)}
                min={0}
                max={20}
                step={1}
                formatDisplay={(v) => String(Math.round(v))}
                className="w-8 h-5 text-[10px] text-gray-300 bg-gray-700/80 border border-gray-600 rounded text-center px-0.5 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                aria-label={t('editor.borderWidth')}
              />
              {/* Separator */}
              <div className="w-px h-4 bg-gray-500/50" />
              {/* Snap toggle */}
              <button
                onClick={togglePreviewResizeSnap}
                className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
                  previewResizeSnap
                    ? 'bg-primary-600/80 text-white'
                    : 'bg-gray-600/60 text-gray-400 hover:text-gray-200'
                }`}
                title={t('editor.resizeSnap', { state: previewResizeSnap ? 'ON' : 'OFF' })}
              >
                Snap
              </button>
              {/* Edge snap toggle */}
              <button
                onClick={toggleEdgeSnapEnabled}
                className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
                  edgeSnapEnabled
                    ? 'bg-primary-600/80 text-white'
                    : 'bg-gray-600/60 text-gray-400 hover:text-gray-200'
                }`}
                title={t('editor.edgeSnap', { state: edgeSnapEnabled ? 'ON' : 'OFF' })}
              >
                Edge
              </button>
              {/* Separator */}
              <div className="w-px h-4 bg-gray-500/50" />
              {/* Zoom controls */}
              <button
                onClick={handlePreviewZoomOut}
                className="text-gray-400 hover:text-white p-1 rounded hover:bg-white/10 transition-colors"
                title={t('editor.zoomOut')}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
                </svg>
              </button>
              <span className="text-gray-300 text-xs w-10 text-center font-mono tabular-nums">{Math.round(previewZoom * 100)}%</span>
              <button
                onClick={handlePreviewZoomIn}
                className="text-gray-400 hover:text-white p-1 rounded hover:bg-white/10 transition-colors"
                title={t('editor.zoomIn')}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
              <button
                onClick={handlePreviewZoomFit}
                className="px-1.5 py-0.5 text-xs text-gray-400 hover:text-white hover:bg-white/10 rounded transition-colors"
                title={t('editor.fit')}
              >
                Fit
              </button>
              <button
                onClick={recenterPreview}
                className="px-1.5 py-0.5 text-xs text-gray-400 hover:text-white hover:bg-white/10 rounded transition-colors"
                title={t('editor.center')}
              >
                {t('editor.center')}
              </button>
            </div>
            {/* Preview area wrapper - takes remaining space after playback controls */}
            <div
              ref={previewAreaRef}
              className={`flex-1 min-h-0 w-full overflow-auto`}
              onClick={(e) => {
                if (e.target === e.currentTarget) {
                  setSelectedVideoClip(null)
                  setSelectedClip(null)
                }
              }}
              onWheel={handlePreviewWheel}
              onMouseDown={handlePreviewPanStart}
              style={{ cursor: isPanningPreview ? 'grabbing' : (previewZoom > 1 ? 'grab' : 'default'), padding: 100 }}
            >
            <div
              ref={previewContainerRef}
              className={`bg-black relative ${selectedVideoClip ? 'overflow-visible' : 'overflow-hidden'}`}
              style={(() => {
                // Fit video to available space (container CSS padding provides edge margin)
                const aspectRatio = currentProject.width / currentProject.height
                const fitByHeight = effectivePreviewHeight
                const fitByWidth = effectivePreviewWidth / aspectRatio
                const baseHeight = Math.min(fitByHeight, fitByWidth)
                return {
                  width: baseHeight * aspectRatio * previewZoom,
                  height: baseHeight * previewZoom,
                  margin: 'auto',
                  transform: previewZoom > 1 ? `translate(${previewPan.x}px, ${previewPan.y}px)` : undefined,
                  flexShrink: 0,
                }
              })()}
              onClick={(e) => {
                // Deselect when clicking on the background (not on a clip)
                if (e.target === e.currentTarget) {
                  setSelectedVideoClip(null)
                  setSelectedClip(null)
                }
              }}
            >
              <Suspense fallback={<div className="absolute inset-0 bg-black" />}>
                <LazyEditorPreviewStage
                  assetUrlCache={assetUrlCache}
                  assets={assets}
                  chromaRenderOverlay={chromaRenderOverlay}
                  chromaRenderOverlayDims={chromaRenderOverlayDims}
                  currentProject={currentProject}
                  currentTime={currentTime}
                  dragCrop={dragCrop}
                  dragTransform={dragTransform}
                  effectivePreviewHeight={effectivePreviewHeight}
                  effectivePreviewWidth={effectivePreviewWidth}
                  handlePreviewDragStart={handlePreviewDragStart}
                  invalidateAssetUrl={invalidateAssetUrl}
                  isPlaying={isPlaying}
                  onDeselect={() => {
                    setSelectedVideoClip(null)
                    setSelectedClip(null)
                  }}
                  preview={preview}
                  previewBorderColor={previewBorderColor}
                  previewBorderWidth={previewBorderWidth}
                  previewDrag={previewDrag}
                  previewZoom={previewZoom}
                  selectedVideoClip={selectedVideoClip}
                  snapGuides={snapGuides}
                  syncVideoToTimelinePosition={syncVideoToTimelinePosition}
                  timelineData={timelineData}
                  videoRefsMap={videoRefsMap}
                />
              </Suspense>
            </div>
            </div>{/* Close preview area wrapper */}

            {/* Playback Controls */}
            <div className="mt-2 flex items-center gap-4 flex-shrink-0">
              {/* Stop Button */}
              <button
                onClick={() => { stopPlayback(); setCurrentTime(0); }}
                data-testid="editor-stop-playback"
                className="p-2 text-gray-400 hover:text-white transition-colors"
                title={t('editor.stopPlayback')}
              >
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              </button>

              {/* Play/Pause Button */}
              <button
                onClick={togglePlayback}
                data-testid="editor-play-toggle"
                className="p-3 bg-primary-600 hover:bg-primary-700 rounded-full text-white transition-colors"
                title={isPlaying ? t('editor.pausePlay') : t('editor.pausePlay')}
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
                  {Math.floor(effectiveDurationMs / 60000)}:
                  {Math.floor((effectiveDurationMs % 60000) / 1000).toString().padStart(2, '0')}
                </span>
              </div>
            </div>
          </div>

          {/* Resize Handle */}
          {/* Resize Handle */}
          <div
            className={`h-3 bg-gray-700 hover:bg-primary-600 cursor-ns-resize flex items-center justify-center transition-colors ${isResizing ? 'bg-primary-600' : ''}`}
            style={{ zIndex: 20 }}
            onMouseDown={handleResizeStart}
          >
            <div className="w-12 h-1 bg-gray-500 rounded"></div>
          </div>

          {/* Timeline - fills remaining space */}
          <div data-testid="timeline-area" className="flex-1 border-t border-gray-700 bg-gray-800 min-h-0 flex flex-col">
            <Suspense fallback={<div className="flex-1 bg-gray-800" />}>
              <LazyTimeline
                timeline={timelineData!}
                projectId={currentProject.id}
                assets={assets}
                currentTimeMs={currentTime}
                isPlaying={isPlaying}
                onClipSelect={setSelectedClip}
                onVideoClipSelect={setSelectedVideoClip}
                onSeek={handleSeek}
                selectedKeyframeIndex={selectedKeyframeIndex}
                onKeyframeSelect={handleKeyframeSelect}
                defaultImageDurationMs={defaultImageDurationMs}
                onAssetsChange={fetchAssets}
                onFreezeFrame={handleFreezeFrame}
              />
            </Suspense>
          </div>
        </main>

        {/* Right Panels Container - Horizontal layout */}
        <div className="flex">
          {/* Property Panel */}
          {isPropertyPanelOpen ? (
            <Suspense fallback={<div className="bg-gray-800 border-l border-gray-700" style={{ width: rightPanelWidth }} />}>
              <LazyEditorPropertyPanel
                assets={assets}
                chromaApplyLoading={chromaApplyLoading}
                chromaColorBeforeEdit={chromaColorBeforeEdit}
                chromaPickerMode={chromaPickerMode}
                chromaPreviewError={chromaPreviewError}
                chromaPreviewFrames={chromaPreviewFrames}
                chromaPreviewLoading={chromaPreviewLoading}
                chromaPreviewSelectedIndex={chromaPreviewSelectedIndex}
                chromaPreviewSize={chromaPreviewSize}
                chromaRawFrameLoading={chromaRawFrameLoading}
                compositeLightboxLoading={compositeLightboxLoading}
                currentKeyframeExists={currentKeyframeExists}
                currentTime={currentTime}
                getCurrentInterpolatedValues={getCurrentInterpolatedValues}
                handleAddKeyframe={handleAddKeyframe}
                handleAddVolumeKeyframeAtCurrent={handleAddVolumeKeyframeAtCurrent}
                handleAddVolumeKeyframeManual={handleAddVolumeKeyframeManual}
                handleChromaPreviewResizeStart={handleChromaPreviewResizeStart}
                handleClearVolumeKeyframes={handleClearVolumeKeyframes}
                handleCompositePreview={handleCompositePreview}
                handleDeleteVideoClip={handleDeleteVideoClip}
                handleFitFillStretch={handleFitFillStretch}
                handleRemoveKeyframe={handleRemoveKeyframe}
                handleRemoveVolumeKeyframe={handleRemoveVolumeKeyframe}
                handleRightPanelResizeStart={handleRightPanelResizeStart}
                handleUpdateAudioClip={handleUpdateAudioClip}
                handleUpdateShape={handleUpdateShape}
                handleUpdateShapeDebounced={handleUpdateShapeDebounced}
                handleUpdateShapeFade={handleUpdateShapeFade}
                handleUpdateShapeFadeLocal={handleUpdateShapeFadeLocal}
                handleUpdateShapeLocal={handleUpdateShapeLocal}
                handleUpdateVideoClip={handleUpdateVideoClip}
                handleUpdateVideoClipDebounced={handleUpdateVideoClipDebounced}
                handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
                handleUpdateVideoClipTiming={handleUpdateVideoClipTiming}
                handleUpdateVolumeKeyframe={handleUpdateVolumeKeyframe}
                isPropertyPanelOpen={isPropertyPanelOpen}
                isComposing={isComposing}
                localTextContent={localTextContent}
                newKeyframeInput={newKeyframeInput}
                projectId={projectId ?? null}
                rightPanelWidth={rightPanelWidth}
                selectedClip={selectedClip}
                selectedKeyframeIndex={selectedKeyframeIndex}
                selectedVideoClip={selectedVideoClip}
                setChromaColorBeforeEdit={setChromaColorBeforeEdit}
                setChromaPickerMode={setChromaPickerMode}
                setChromaPreviewError={setChromaPreviewError}
                setChromaPreviewSelectedIndex={setChromaPreviewSelectedIndex}
                setChromaRawFrame={setChromaRawFrame}
                setChromaRawFrameLoading={setChromaRawFrameLoading}
                setChromaRenderOverlay={setChromaRenderOverlay}
                setChromaRenderOverlayDims={setChromaRenderOverlayDims}
                setChromaRenderOverlayTimeMs={setChromaRenderOverlayTimeMs}
                setIsComposing={setIsComposing}
                setIsPropertyPanelOpen={setIsPropertyPanelOpen}
                setLocalTextContent={setLocalTextContent}
                setNewKeyframeInput={setNewKeyframeInput}
                setSelectedKeyframeIndex={setSelectedKeyframeIndex}
                textDebounceRef={textDebounceRef}
                timelineData={timelineData}
                videoRefsMap={videoRefsMap}
              />
            </Suspense>
          ) : (
            <div
              data-testid="editor-property-rail"
              onClick={() => setIsPropertyPanelOpen(true)}
              className="bg-gray-800 border-l border-gray-700 w-11 flex flex-col items-center py-3 cursor-pointer group transition-colors hover:bg-gray-700/50"
            >
              <svg className="w-5 h-5 text-gray-500 group-hover:text-gray-300 transition-colors mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors" style={{ writingMode: 'vertical-rl' }}>
                {t('editor.propertyPanel')}
              </span>
            </div>
          )}

          {/* AI Chat Panel */}
          {isAIChatOpen ? (
            <Suspense fallback={<div className="bg-gray-800 border-l border-gray-700" style={{ width: aiPanelWidth }} />}>
              <LazyAIChatPanel
                projectId={currentProject.id}
                aiProvider={currentProject.ai_provider}
                isOpen={isAIChatOpen}
                onActionsApplied={refreshTimelineAfterAiApply}
                onToggle={() => setIsAIChatOpen(false)}
                mode="inline"
                width={aiPanelWidth}
                onResizeStart={handleAiPanelResizeStart}
              />
            </Suspense>
          ) : (
            <div
              data-testid="editor-ai-rail"
              onClick={() => setIsAIChatOpen(true)}
              className="bg-gray-800 border-l border-gray-700 w-10 flex flex-col items-center py-3 cursor-pointer hover:bg-gray-700 transition-colors"
            >
              <svg className="w-4 h-4 text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>AI</span>
            </div>
          )}

          {/* Activity Panel */}
          {isActivityPanelOpen ? (
            <Suspense fallback={<div className="bg-gray-800 border-l border-gray-700" style={{ width: activityPanelWidth }} />}>
              <LazyActivityPanel
                isOpen={isActivityPanelOpen}
                onOpenChange={setIsActivityPanelOpen}
                width={activityPanelWidth}
                onResizeStart={handleActivityPanelResizeStart}
                operations={operationHistory}
              />
            </Suspense>
          ) : (
            <div
              data-testid="editor-activity-rail"
              onClick={() => setIsActivityPanelOpen(true)}
              className="bg-gray-800 border-l border-gray-700 w-11 flex flex-col items-center py-3 cursor-pointer group transition-colors hover:bg-gray-700/50"
              title="Activity Panel"
            >
              <svg className="w-5 h-5 text-gray-500 group-hover:text-gray-300 transition-colors mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors" style={{ writingMode: 'vertical-rl' }}>Activity</span>
              {operationHistory.length > 0 && (
                <span className="mt-2 bg-primary-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
                  {operationHistory.length > 99 ? '99+' : operationHistory.length}
                </span>
              )}
            </div>
          )}
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
                title={t('editor.closePanel')}
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
                {isTransparent && <span className="ml-2 text-xs text-gray-400">{t('editor.transparentPng')}</span>}
              </div>
              {/* Navigation hint */}
              <div className="absolute bottom-4 right-4 bg-black/70 text-gray-400 text-xs px-3 py-1.5 rounded">
                {t('editor.closeLightbox')}
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
              title={t('editor.closePanel')}
            >
              x
            </button>
            {/* Eyedropper mode indicator */}
            <div className="absolute -top-10 left-0 bg-yellow-600 text-white text-sm px-3 py-1.5 rounded flex items-center gap-2">
              <span>{t('editor.dropperMode')}</span>
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
              {t('editor.rawFrame', { time: (chromaRawFrame.time_ms / 1000).toFixed(2) })}
            </div>
            {/* Navigation hint */}
            <div className="absolute bottom-4 right-4 bg-black/70 text-gray-400 text-xs px-3 py-1.5 rounded">
              {t('editor.clickToSelect')}
            </div>
          </div>
        </div>
      )}

      {/* Composite Frame Lightbox */}
      {compositeLightbox && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-[10000]"
          onClick={(e) => {
            if (e.target === e.currentTarget) setCompositeLightbox(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setCompositeLightbox(null)
          }}
          tabIndex={0}
          ref={(el) => el?.focus()}
        >
          <div className="relative max-w-[95vw] max-h-[95vh] flex flex-col">
            <div className="flex items-center justify-between bg-gray-900/90 px-4 py-2 rounded-t-lg">
              <span className="text-white text-sm font-medium">
                {t('editor.compositeAt', { time: `${Math.floor(compositeLightbox.timeMs / 1000 / 60).toString().padStart(2, '0')}:${Math.floor(compositeLightbox.timeMs / 1000 % 60).toString().padStart(2, '0')}.${(compositeLightbox.timeMs % 1000).toString().padStart(3, '0')}` })}
              </span>
              <button
                className="text-gray-400 hover:text-white text-xl leading-none px-2"
                onClick={() => setCompositeLightbox(null)}
                title={t('editor.closeLightboxTitle')}
              >
                &times;
              </button>
            </div>
            <CompositePreviewViewer
              src={compositeLightbox.src}
              onClose={() => setCompositeLightbox(null)}
            />
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <Toast
          key={toastMessage.text}
          message={toastMessage.text}
          type={toastMessage.type}
          duration={toastMessage.duration}
          onClose={() => setToastMessage(null)}
        />
      )}

      {/* Members Manager Modal */}
      {showMembersModal && (
        <Suspense fallback={null}>
          <LazyMembersManager
            isOpen={showMembersModal}
            onClose={() => setShowMembersModal(false)}
            projectId={projectId || ''}
            isOwner={true}
          />
        </Suspense>
      )}
      {isConflictDialogOpen && (
        <Suspense fallback={null}>
          <LazyConflictResolutionDialog />
        </Suspense>
      )}
      {syncResumeDialog && (
        <Suspense fallback={null}>
          <LazySyncResumeDialog
            remoteOpCount={syncResumeDialog.remoteOpCount}
            onAction={handleSyncResumeAction}
            onCancel={() => setSyncResumeDialog(null)}
          />
        </Suspense>
      )}
    </div>
  )
}
