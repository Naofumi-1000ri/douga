import React, { useState, useCallback, useRef, useEffect, memo, useMemo } from 'react'
import type { TimelineData, AudioClip, AudioTrack, Clip, Keyframe, ShapeType, Shape, ClipGroup, TextStyle } from '@/store/projectStore'
import { useProjectStore } from '@/store/projectStore'
import { v4 as uuidv4 } from 'uuid'
import { transcriptionApi, type Transcription } from '@/api/transcription'
import { assetsApi } from '@/api/assets'
import WaveformDisplay from './WaveformDisplay'
import VideoClipThumbnails from './VideoClipThumbnails'
import { useWaveform } from '@/hooks/useWaveform'

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
    subtype: string
    storage_url: string
    duration_ms: number | null
    width?: number | null
    height?: number | null
  }>
  currentTimeMs?: number
  isPlaying?: boolean
  onClipSelect?: (clip: SelectedClipInfo | null) => void
  onVideoClipSelect?: (clip: SelectedVideoClipInfo | null) => void
  onSeek?: (timeMs: number) => void
}

// Initial positions for group clips during drag
interface GroupClipInitialPosition {
  clipId: string
  layerOrTrackId: string
  initialStartMs: number
}

interface DragState {
  type: 'move' | 'trim-start' | 'trim-end'
  trackId: string
  clipId: string
  startX: number
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  assetDurationMs: number // Original asset duration for trim limits
  currentDeltaMs: number // Current drag delta in ms (for rendering without store update)
  // Linked video clip info (for synchronized movement) - legacy
  linkedVideoClipId?: string | null
  linkedVideoLayerId?: string | null
  linkedVideoInitialStartMs?: number
  // Group clip info (for synchronized movement)
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
}

interface VideoDragState {
  type: 'move' | 'trim-start' | 'trim-end'
  layerId: string
  clipId: string
  startX: number
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  assetDurationMs: number // Original asset duration for trim limits
  currentDeltaMs: number // Current drag delta in ms (for rendering without store update)
  isResizableClip: boolean // Shape/text clips can be resized freely (no asset duration limit)
  // Linked audio clip info (for synchronized movement) - legacy
  linkedAudioClipId?: string | null
  linkedAudioTrackId?: string | null
  linkedAudioInitialStartMs?: number
  // Group clip info (for synchronized movement)
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
}

// Wrapper component for audio clip waveform (needed to use hooks in map)
interface AudioClipWaveformProps {
  projectId: string
  assetId: string
  width: number
  height: number
  color: string
  inPointMs: number      // Where in the source the clip starts
  clipDurationMs: number // Duration of the clip on timeline
  assetDurationMs: number // Total duration of the source asset
}

const AudioClipWaveform = memo(function AudioClipWaveform({
  projectId,
  assetId,
  width,
  height,
  color,
  inPointMs,
  clipDurationMs,
  assetDurationMs,
}: AudioClipWaveformProps) {
  // Calculate samples based on clip width (1 sample per 2 pixels)
  const samples = Math.max(50, Math.min(400, Math.floor(width / 2)))
  const { peaks: fullPeaks, isLoading } = useWaveform(projectId, assetId, samples)

  // Slice peaks array to only show the trimmed portion
  const peaks = useMemo(() => {
    if (!fullPeaks || fullPeaks.length === 0 || assetDurationMs <= 0) return fullPeaks

    // Calculate which portion of the peaks array represents the visible clip
    const startRatio = inPointMs / assetDurationMs
    const endRatio = (inPointMs + clipDurationMs) / assetDurationMs

    const startIdx = Math.floor(startRatio * fullPeaks.length)
    const endIdx = Math.ceil(endRatio * fullPeaks.length)

    // Clamp indices to valid range
    const clampedStart = Math.max(0, Math.min(startIdx, fullPeaks.length - 1))
    const clampedEnd = Math.max(clampedStart + 1, Math.min(endIdx, fullPeaks.length))

    return fullPeaks.slice(clampedStart, clampedEnd)
  }, [fullPeaks, inPointMs, clipDurationMs, assetDurationMs])

  // Show placeholder while loading waveform (doesn't block playback)
  if (!peaks) {
    return (
      <div className="absolute inset-0 overflow-hidden pointer-events-none flex items-center justify-center">
        {isLoading ? (
          <div className="flex items-center gap-1">
            {/* Simple loading bar animation */}
            {[...Array(5)].map((_, i) => (
              <div
                key={i}
                className="w-1 bg-current opacity-40 rounded-full animate-pulse"
                style={{
                  height: `${20 + (i % 3) * 10}%`,
                  animationDelay: `${i * 100}ms`,
                  color,
                }}
              />
            ))}
          </div>
        ) : (
          // Error or no data - show simple line
          <div
            className="w-full h-px opacity-30"
            style={{ backgroundColor: color }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <WaveformDisplay peaks={peaks} width={width} height={height} color={color} />
    </div>
  )
})

export default function Timeline({ timeline, projectId, assets, currentTimeMs = 0, isPlaying = false, onClipSelect, onVideoClipSelect, onSeek }: TimelineProps) {
  const [zoom, setZoom] = useState(1)
  const [selectedClip, setSelectedClip] = useState<{ trackId: string; clipId: string } | null>(null)
  const [selectedVideoClip, setSelectedVideoClip] = useState<{ layerId: string; clipId: string } | null>(null)
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null) // Selected layer (for shape placement)
  const [dragOverTrack, setDragOverTrack] = useState<string | null>(null)
  const [dragOverLayer, setDragOverLayer] = useState<string | null>(null)
  const [dragState, setDragState] = useState<DragState | null>(null)
  const [videoDragState, setVideoDragState] = useState<VideoDragState | null>(null)
  const [isDraggingPlayhead, setIsDraggingPlayhead] = useState(false)
  const [isLinkingMode, setIsLinkingMode] = useState(false) // True when waiting for user to click an audio clip to link
  const [editingLayerId, setEditingLayerId] = useState<string | null>(null) // Layer being renamed
  const [editingLayerName, setEditingLayerName] = useState('')
  const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null) // Layer being dragged for reorder
  const [dropTargetIndex, setDropTargetIndex] = useState<number | null>(null) // Index where layer will be dropped
  // Multi-selection state
  const [selectedVideoClips, setSelectedVideoClips] = useState<Set<string>>(new Set())
  const [selectedAudioClips, setSelectedAudioClips] = useState<Set<string>>(new Set())
  // State for dragging asset over new layer drop zone
  const [isDraggingNewLayer, setIsDraggingNewLayer] = useState(false)
  // Transcription / AI analysis state
  const [transcription, setTranscription] = useState<Transcription | null>(null)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [showTranscriptionPanel, setShowTranscriptionPanel] = useState(false)
  const { updateTimeline } = useProjectStore()
  const trackRefs = useRef<{ [trackId: string]: HTMLDivElement | null }>({})
  const layerRefs = useRef<{ [layerId: string]: HTMLDivElement | null }>({})
  const labelsScrollRef = useRef<HTMLDivElement>(null)
  const tracksScrollRef = useRef<HTMLDivElement>(null)
  const timelineContainerRef = useRef<HTMLDivElement>(null)
  // Refs for requestAnimationFrame drag throttling
  const dragRafRef = useRef<number | null>(null)
  const videoDragRafRef = useRef<number | null>(null)
  const pendingDragDeltaRef = useRef<number>(0)
  const pendingVideoDragDeltaRef = useRef<number>(0)
  const isScrollSyncing = useRef(false)

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
    isScrollSyncing.current = false
  }, [])

  const pixelsPerSecond = 10 * zoom
  const totalWidth = (timeline.duration_ms / 1000) * pixelsPerSecond

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

  // Separate audio tracks into linked (to video layers) and standalone
  const { linkedAudioTracksByLayerId, standaloneAudioTracks } = useMemo(() => {
    const linked = new Map<string, typeof timeline.audio_tracks[0]>()
    const standalone: typeof timeline.audio_tracks = []

    for (const track of timeline.audio_tracks) {
      if (track.linkedVideoLayerId) {
        linked.set(track.linkedVideoLayerId, track)
      } else {
        standalone.push(track)
      }
    }

    return { linkedAudioTracksByLayerId: linked, standaloneAudioTracks: standalone }
  }, [timeline.audio_tracks])

  const formatTime = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

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

    // Create a new layer for shapes
    const newLayer = {
      id: uuidv4(),
      name: `シェイプレイヤー ${timeline.layers.length + 1}`,
      order: 0,
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

    // Create a new layer for video
    const newLayer = {
      id: uuidv4(),
      name: `ビデオレイヤー ${timeline.layers.length + 1}`,
      order: 0,
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

  const handleDuckingToggle = async (trackId: string, enabled: boolean) => {
    const updatedTracks = timeline.audio_tracks.map((track) =>
      track.id === trackId && track.ducking
        ? { ...track, ducking: { ...track.ducking, enabled } }
        : track
    )
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  const handleMuteToggle = async (trackId: string) => {
    const updatedTracks = timeline.audio_tracks.map((track) =>
      track.id === trackId ? { ...track, muted: !track.muted } : track
    )
    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }

  // Layer management
  const handleAddLayer = async () => {
    const newLayer = {
      id: uuidv4(),
      name: `レイヤー ${timeline.layers.length + 1}`,
      order: timeline.layers.length,
      visible: true,
      locked: false,
      clips: [],
    }
    await updateTimeline(projectId, { ...timeline, layers: [...timeline.layers, newLayer] })
  }

  const handleDeleteLayer = async (layerId: string) => {
    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) return
    if (layer.clips.length > 0) {
      if (!confirm('このレイヤーにはクリップが含まれています。削除しますか？')) return
    }

    // Collect all clip IDs and group IDs from the layer being deleted
    const clipIdsToDelete = new Set(layer.clips.map(c => c.id))
    const groupIdsToDelete = new Set(layer.clips.map(c => c.group_id).filter(Boolean) as string[])

    const updatedLayers = timeline.layers.filter(l => l.id !== layerId)

    // Also remove linked audio clips
    const updatedTracks = timeline.audio_tracks.map((track) => ({
      ...track,
      clips: track.clips.filter((c) => {
        // Remove if linked directly to any video clip in the deleted layer
        if (c.linked_video_clip_id && clipIdsToDelete.has(c.linked_video_clip_id)) {
          console.log('[handleDeleteLayer] Removing linked audio clip:', c.id)
          return false
        }
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
  const handleAddShape = async (shapeType: ShapeType) => {
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
      const newLayer = {
        id: uuidv4(),
        name: 'シェイプレイヤー 1',
        order: 0,
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
      width: shapeType === 'circle' ? 100 : 150,
      height: shapeType === 'circle' ? 100 : (shapeType === 'line' ? 4 : 100),
      fillColor: shapeType === 'line' ? 'transparent' : '#3b82f6',
      strokeColor: '#ffffff',
      strokeWidth: shapeType === 'line' ? 4 : 2,
      filled: shapeType !== 'line',
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
      const newLayer = {
        id: uuidv4(),
        name: 'レイヤー 1',
        order: 0,
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
      backgroundColor: 'transparent',
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

  // Link a video clip to an audio clip
  const handleLinkClips = async (videoLayerId: string, videoClipId: string, audioTrackId: string, audioClipId: string) => {
    // Update video clip with linked audio info
    const updatedLayers = timeline.layers.map(layer => {
      if (layer.id !== videoLayerId) return layer
      return {
        ...layer,
        clips: layer.clips.map(clip => {
          if (clip.id !== videoClipId) return clip
          return { ...clip, linked_audio_clip_id: audioClipId, linked_audio_track_id: audioTrackId }
        }),
      }
    })

    // Update audio clip with linked video info
    const updatedTracks = timeline.audio_tracks.map(track => {
      if (track.id !== audioTrackId) return track
      return {
        ...track,
        clips: track.clips.map(clip => {
          if (clip.id !== audioClipId) return clip
          return { ...clip, linked_video_clip_id: videoClipId, linked_video_layer_id: videoLayerId }
        }),
      }
    })

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }

  // Unlink a video clip from its audio clip
  const handleUnlinkVideoClip = async (layerId: string, clipId: string) => {
    const layer = timeline.layers.find(l => l.id === layerId)
    const clip = layer?.clips.find(c => c.id === clipId)
    if (!clip?.linked_audio_clip_id || !clip?.linked_audio_track_id) return

    // Remove link from video clip
    const updatedLayers = timeline.layers.map(l => {
      if (l.id !== layerId) return l
      return {
        ...l,
        clips: l.clips.map(c => {
          if (c.id !== clipId) return c
          return { ...c, linked_audio_clip_id: null, linked_audio_track_id: null }
        }),
      }
    })

    // Remove link from audio clip
    const updatedTracks = timeline.audio_tracks.map(track => {
      if (track.id !== clip.linked_audio_track_id) return track
      return {
        ...track,
        clips: track.clips.map(c => {
          if (c.id !== clip.linked_audio_clip_id) return c
          return { ...c, linked_video_clip_id: null, linked_video_layer_id: null }
        }),
      }
    })

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }

  // Group management - color palette for groups
  const groupColors = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899']

  // Create a new group from currently selected video and/or audio clip
  const handleCreateGroup = async () => {
    if (!selectedVideoClip && !selectedClip) return

    const groupId = uuidv4()
    const groupColor = groupColors[(timeline.groups?.length || 0) % groupColors.length]
    const newGroup: ClipGroup = {
      id: groupId,
      name: `グループ ${(timeline.groups?.length || 0) + 1}`,
      color: groupColor,
    }

    let updatedLayers = timeline.layers
    let updatedTracks = timeline.audio_tracks

    // Add video clip to group
    if (selectedVideoClip) {
      updatedLayers = timeline.layers.map(layer => {
        if (layer.id !== selectedVideoClip.layerId) return layer
        return {
          ...layer,
          clips: layer.clips.map(clip => {
            if (clip.id !== selectedVideoClip.clipId) return clip
            return { ...clip, group_id: groupId }
          }),
        }
      })
    }

    // Add audio clip to group
    if (selectedClip) {
      updatedTracks = timeline.audio_tracks.map(track => {
        if (track.id !== selectedClip.trackId) return track
        return {
          ...track,
          clips: track.clips.map(clip => {
            if (clip.id !== selectedClip.clipId) return clip
            return { ...clip, group_id: groupId }
          }),
        }
      })
    }

    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedTracks,
      groups: [...(timeline.groups || []), newGroup],
    })
  }

  // Add selected clip to an existing group
  const handleAddToGroup = async (groupId: string) => {
    let updatedLayers = timeline.layers
    let updatedTracks = timeline.audio_tracks

    if (selectedVideoClip) {
      updatedLayers = timeline.layers.map(layer => {
        if (layer.id !== selectedVideoClip.layerId) return layer
        return {
          ...layer,
          clips: layer.clips.map(clip => {
            if (clip.id !== selectedVideoClip.clipId) return clip
            return { ...clip, group_id: groupId }
          }),
        }
      })
    }

    if (selectedClip) {
      updatedTracks = timeline.audio_tracks.map(track => {
        if (track.id !== selectedClip.trackId) return track
        return {
          ...track,
          clips: track.clips.map(clip => {
            if (clip.id !== selectedClip.clipId) return clip
            return { ...clip, group_id: groupId }
          }),
        }
      })
    }

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }

  // Remove clip from its group
  const handleRemoveFromGroup = async (clipType: 'video' | 'audio', layerOrTrackId: string, clipId: string) => {
    if (clipType === 'video') {
      const updatedLayers = timeline.layers.map(layer => {
        if (layer.id !== layerOrTrackId) return layer
        return {
          ...layer,
          clips: layer.clips.map(clip => {
            if (clip.id !== clipId) return clip
            return { ...clip, group_id: null }
          }),
        }
      })
      await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
    } else {
      const updatedTracks = timeline.audio_tracks.map(track => {
        if (track.id !== layerOrTrackId) return track
        return {
          ...track,
          clips: track.clips.map(clip => {
            if (clip.id !== clipId) return clip
            return { ...clip, group_id: null }
          }),
        }
      })
      await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
    }
  }

  // Unlink video and audio clips that share the same group_id
  const handleUnlinkVideoAudioGroup = async (groupId: string) => {
    if (!groupId) return

    // Remove group_id from all video clips in this group
    const updatedLayers = timeline.layers.map(layer => ({
      ...layer,
      clips: layer.clips.map(clip => {
        if (clip.group_id !== groupId) return clip
        return { ...clip, group_id: null }
      }),
    }))

    // Remove group_id from all audio clips in this group
    const updatedTracks = timeline.audio_tracks.map(track => ({
      ...track,
      clips: track.clips.map(clip => {
        if (clip.group_id !== groupId) return clip
        return { ...clip, group_id: null }
      }),
    }))

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }


  // Get group info for a clip
  const getClipGroup = useCallback((groupId: string | null | undefined): ClipGroup | null => {
    if (!groupId) return null
    return timeline.groups?.find(g => g.id === groupId) || null
  }, [timeline.groups])

  // Transcription / AI analysis functions
  const handleStartTranscription = useCallback(async (assetId: string) => {
    if (isTranscribing) return

    setIsTranscribing(true)
    setTranscription(null)
    setShowTranscriptionPanel(true)

    try {
      // Start transcription
      await transcriptionApi.transcribe({
        asset_id: assetId,
        language: 'ja',
        model_name: 'base',
        detect_silences: true,
        detect_fillers: true,
        detect_repetitions: true,
      })

      // Poll for completion
      const result = await transcriptionApi.waitForCompletion(assetId, 120000, 2000)
      setTranscription(result)
    } catch (error) {
      console.error('Transcription failed:', error)
      alert('音声分析に失敗しました')
    } finally {
      setIsTranscribing(false)
    }
  }, [isTranscribing])

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

    // Use playhead position (currentTimeMs) for drop position
    const startMs = Math.max(0, currentTimeMs)

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
  }, [assets, timeline, projectId, currentTimeMs, updateTimeline])

  // Video layer drag handlers
  const handleLayerDragOver = useCallback((e: React.DragEvent, layerId: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDragOverLayer(layerId)
  }, [])

  const handleLayerDragLeave = useCallback(() => {
    setDragOverLayer(null)
  }, [])

  const handleLayerDrop = useCallback(async (e: React.DragEvent, layerId: string) => {
    e.preventDefault()
    setDragOverLayer(null)
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

    // Use playhead position (currentTimeMs) for drop position
    const startMs = Math.max(0, currentTimeMs)

    // Generate group_id for linking video and audio clips
    const groupId = uuidv4()

    // Create new video clip with default transform and effects
    const newClip: Clip = {
      id: uuidv4(),
      asset_id: assetId,
      start_ms: startMs,
      duration_ms: asset.duration_ms || 5000,
      in_point_ms: 0,
      out_point_ms: null,
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
      },
    }
    console.log('[handleLayerDrop] Creating clip:', newClip)

    // Add clip to target layer (may be a new layer if shape conflict was detected)
    updatedLayers = updatedLayers.map((l) =>
      l.id === targetLayerId ? { ...l, clips: [...l.clips, newClip] } : l
    )

    // Update duration if needed
    const newDuration = Math.max(
      timeline.duration_ms,
      startMs + (asset.duration_ms || 5000)
    )

    // For video assets, create audio track immediately (with empty clips)
    // This gives instant visual feedback while audio extraction happens in background
    const newAudioTrackId = assetType === 'video' ? uuidv4() : null
    let updatedAudioTracks = timeline.audio_tracks

    if (assetType === 'video' && newAudioTrackId) {
      const newAudioTrack: AudioTrack = {
        id: newAudioTrackId,
        name: `${asset.name} - 音声 (抽出中...)`,
        type: 'video',
        volume: 1,
        muted: false,
        linkedVideoLayerId: targetLayerId,  // Use targetLayerId (may have changed due to shape conflict)
        clips: [],  // Empty for now, will be filled when extraction completes
      }
      updatedAudioTracks = [...timeline.audio_tracks, newAudioTrack]
    }

    // Update timeline immediately with video clip AND empty audio track
    console.log('[handleLayerDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      audio_tracks: updatedAudioTracks,
      duration_ms: newDuration,
    })
    console.log('[handleLayerDrop] Video clip and audio track added immediately')

    // For video assets, extract audio in background and add clip when ready
    if (assetType === 'video' && newAudioTrackId) {
      console.log('[handleLayerDrop] Starting background audio extraction...')
      assetsApi.extractAudio(projectId, assetId)
        .then(audioAsset => {
          console.log('[handleLayerDrop] Audio extracted in background:', audioAsset)

          // Get fresh timeline state from store
          const currentState = useProjectStore.getState()
          const currentTimeline = currentState.currentProject?.timeline_data
          if (!currentTimeline) return

          // Find the audio track we created and add the clip
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

          // Update the audio track with the clip and rename
          const updatedTracks = currentTimeline.audio_tracks.map(track =>
            track.id === newAudioTrackId
              ? { ...track, name: `${asset.name} - 音声`, clips: [...track.clips, audioClip] }
              : track
          )

          updateTimeline(projectId, {
            ...currentTimeline,
            audio_tracks: updatedTracks,
          })
          console.log('[handleLayerDrop] Audio clip added to track')
        })
        .catch(err => {
          console.log('[handleLayerDrop] Audio extraction failed:', err)
          // Remove the empty audio track on failure
          const currentState = useProjectStore.getState()
          const currentTimeline = currentState.currentProject?.timeline_data
          if (!currentTimeline) return

          updateTimeline(projectId, {
            ...currentTimeline,
            audio_tracks: currentTimeline.audio_tracks.filter(t => t.id !== newAudioTrackId),
          })
        })
    }
    console.log('[handleLayerDrop] DONE')
  }, [assets, timeline, projectId, currentTimeMs, updateTimeline, layerHasShapeClips, findOrCreateVideoCompatibleLayer])

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

    // Create new layer
    const newLayerId = uuidv4()
    const layerCount = timeline.layers.length
    const newLayer = {
      id: newLayerId,
      name: `レイヤー ${layerCount + 1}`,
      order: layerCount,
      visible: true,
      locked: false,
      clips: [] as Clip[],
    }

    // Create clip at current playhead position
    const startMs = currentTimeMs

    // Generate group_id for linking video and audio clips
    const groupId = uuidv4()

    const newClip: Clip = {
      id: uuidv4(),
      asset_id: asset.id,
      start_ms: startMs,
      duration_ms: asset.duration_ms || 5000,
      in_point_ms: 0,
      out_point_ms: asset.duration_ms || null,
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
      },
    }

    // Add clip to new layer
    newLayer.clips.push(newClip)
    console.log('[handleNewLayerDrop] Creating layer:', newLayer)

    // Update timeline duration if needed
    const newDuration = Math.max(
      timeline.duration_ms,
      startMs + (asset.duration_ms || 5000)
    )

    // For video assets, create audio track immediately (with empty clips)
    const newAudioTrackId = assetType === 'video' ? uuidv4() : null
    let updatedAudioTracks = timeline.audio_tracks

    if (assetType === 'video' && newAudioTrackId) {
      const newAudioTrack: AudioTrack = {
        id: newAudioTrackId,
        name: `${asset.name} - 音声 (抽出中...)`,
        type: 'video',
        volume: 1,
        muted: false,
        linkedVideoLayerId: newLayerId,
        clips: [],
      }
      updatedAudioTracks = [...timeline.audio_tracks, newAudioTrack]
    }

    // Update timeline immediately with video clip AND empty audio track
    console.log('[handleNewLayerDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      layers: [...timeline.layers, newLayer],
      audio_tracks: updatedAudioTracks,
      duration_ms: newDuration,
    })
    console.log('[handleNewLayerDrop] Video clip and audio track added immediately')

    // For video assets, extract audio in background and add clip when ready
    if (assetType === 'video' && newAudioTrackId) {
      console.log('[handleNewLayerDrop] Starting background audio extraction...')
      assetsApi.extractAudio(projectId, assetId)
        .then(audioAsset => {
          console.log('[handleNewLayerDrop] Audio extracted in background:', audioAsset)

          // Get fresh timeline state from store
          const currentState = useProjectStore.getState()
          const currentTimeline = currentState.currentProject?.timeline_data
          if (!currentTimeline) return

          // Create audio clip
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

          // Update the audio track with the clip and rename
          const updatedTracks = currentTimeline.audio_tracks.map(track =>
            track.id === newAudioTrackId
              ? { ...track, name: `${asset.name} - 音声`, clips: [...track.clips, audioClip] }
              : track
          )

          updateTimeline(projectId, {
            ...currentTimeline,
            audio_tracks: updatedTracks,
          })
          console.log('[handleNewLayerDrop] Audio clip added to track')
        })
        .catch(err => {
          console.log('[handleNewLayerDrop] Audio extraction failed:', err)
          // Remove the empty audio track on failure
          const currentState = useProjectStore.getState()
          const currentTimeline = currentState.currentProject?.timeline_data
          if (!currentTimeline) return

          updateTimeline(projectId, {
            ...currentTimeline,
            audio_tracks: currentTimeline.audio_tracks.filter(t => t.id !== newAudioTrackId),
          })
        })
    }
    console.log('[handleNewLayerDrop] DONE')
  }, [assets, timeline, projectId, currentTimeMs, updateTimeline])

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
    // If in linking mode, link the audio clip to the selected video clip
    if (isLinkingMode && selectedVideoClip) {
      handleLinkClips(selectedVideoClip.layerId, selectedVideoClip.clipId, trackId, clipId)
      setIsLinkingMode(false)
      return
    }

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

    // If the clip belongs to a group, select all clips in the group
    if (groupId) {
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
          return
        }
      }
      onClipSelect(null)
    }
    if (onVideoClipSelect) {
      onVideoClipSelect(null)
    }
  }, [timeline, assets, onClipSelect, onVideoClipSelect, isLinkingMode, selectedVideoClip, selectedClip, handleLinkClips, findGroupClips])

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

    // If the clip belongs to a group, select all clips in the group
    if (groupId) {
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
            textContent: clip.text_content,
            textStyle: clip.text_style,
            fadeInMs: clip.fade_in_ms ?? clip.effects?.fade_in_ms ?? 0,
            fadeOutMs: clip.fade_out_ms ?? clip.effects?.fade_out_ms ?? 0,
          })
          return
        }
      }
      onVideoClipSelect(null)
    }
    if (onClipSelect) {
      onClipSelect(null)
    }
  }, [timeline, assets, onClipSelect, onVideoClipSelect, selectedVideoClip, findGroupClips])

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

      // Also remove linked audio clips (same group_id or linked_video_clip_id)
      const updatedTracks = timeline.audio_tracks.map((track) => ({
        ...track,
        clips: track.clips.filter((c) => {
          // Remove if linked directly to this video clip
          if (c.linked_video_clip_id === selectedVideoClip.clipId) {
            console.log('[handleDeleteClip] Removing linked audio clip:', c.id)
            return false
          }
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
        linked_audio_clip_id: null,
        linked_audio_track_id: null,
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
        linked_audio_clip_id: null,
        linked_audio_track_id: null,
        keyframes: clip.keyframes?.map(kf => ({
          ...kf,
          time_ms: kf.time_ms - timeIntoClip,
        })).filter(kf => kf.time_ms >= 0),
      }

      return { clip1, clip2 }
    }

    // Helper function to cut an audio clip
    const cutAudioClip = (clip: AudioClip, cutTimeMs: number, newGroupId1: string | null, newGroupId2: string | null): { clip1: AudioClip, clip2: AudioClip } | null => {
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
        linked_video_clip_id: null,
        linked_video_layer_id: null,
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
        linked_video_clip_id: null,
        linked_video_layer_id: null,
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

  const handleFadeChange = useCallback(async (type: 'in' | 'out', valueMs: number) => {
    if (!selectedClip) return

    const updatedTracks = timeline.audio_tracks.map((track) =>
      track.id === selectedClip.trackId
        ? {
            ...track,
            clips: track.clips.map((clip) =>
              clip.id === selectedClip.clipId
                ? type === 'in'
                  ? { ...clip, fade_in_ms: valueMs }
                  : { ...clip, fade_out_ms: valueMs }
                : clip
            ),
          }
        : track
    )

    await updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks })
  }, [selectedClip, timeline, projectId, updateTimeline])

  const handleVideoFadeChange = useCallback(async (type: 'in' | 'out', valueMs: number) => {
    if (!selectedVideoClip) return

    const updatedLayers = timeline.layers.map((layer) =>
      layer.id === selectedVideoClip.layerId
        ? {
            ...layer,
            clips: layer.clips.map((clip) =>
              clip.id === selectedVideoClip.clipId
                ? {
                    ...clip,
                    effects: {
                      ...clip.effects,
                      ...(type === 'in' ? { fade_in_ms: valueMs } : { fade_out_ms: valueMs })
                    }
                  }
                : clip
            ),
          }
        : layer
    )

    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
  }, [selectedVideoClip, timeline, projectId, updateTimeline])

  // Clip drag handlers for move and trim
  const handleClipDragStart = useCallback((
    e: React.MouseEvent,
    trackId: string,
    clipId: string,
    type: 'move' | 'trim-start' | 'trim-end'
  ) => {
    e.preventDefault()
    e.stopPropagation()

    const track = timeline.audio_tracks.find(t => t.id === trackId)
    const clip = track?.clips.find(c => c.id === clipId)
    if (!clip) return

    // Get original asset duration for trim limits
    const asset = assets.find(a => a.id === clip.asset_id)
    const assetDurationMs = asset?.duration_ms || clip.in_point_ms + clip.duration_ms

    // Find linked video clip's initial position (legacy support)
    let linkedVideoInitialStartMs: number | undefined
    if (clip.linked_video_clip_id && clip.linked_video_layer_id) {
      const linkedLayer = timeline.layers.find(l => l.id === clip.linked_video_layer_id)
      const linkedClip = linkedLayer?.clips.find(c => c.id === clip.linked_video_clip_id)
      linkedVideoInitialStartMs = linkedClip?.start_ms
    }

    // Get group clips initial positions
    const groupVideoClips: GroupClipInitialPosition[] = []
    const groupAudioClips: GroupClipInitialPosition[] = []

    // Check if clicked clip is part of current multi-selection
    const isClickedClipInSelection = selectedAudioClips.has(clipId) || selectedClip?.clipId === clipId

    // Only include multi-selected clips if the clicked clip is part of the selection
    if (isClickedClipInSelection) {
      // Collect all multi-selected video clips (SHIFT+click selection)
      // Skip clips on locked layers
      if (selectedVideoClips.size > 0) {
        for (const l of timeline.layers) {
          if (l.locked) continue
          for (const c of l.clips) {
            if (selectedVideoClips.has(c.id)) {
              groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
            }
          }
        }
      }

      // Collect all multi-selected audio clips (except the dragged clip)
      if (selectedAudioClips.size > 0) {
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (selectedAudioClips.has(c.id) && c.id !== clipId) {
              groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
            }
          }
        }
      }
    }

    // Also add group_id linked clips if clip is in a group
    if (clip.group_id) {
      // Collect all video clips in the group (except already added)
      // Skip clips on locked layers
      const addedVideoIds = new Set(groupVideoClips.map(g => g.clipId))
      for (const l of timeline.layers) {
        if (l.locked) continue
        for (const c of l.clips) {
          if (c.group_id === clip.group_id && !addedVideoIds.has(c.id)) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      // Collect all audio clips in the group (except the dragged clip and already added)
      const addedAudioIds = new Set(groupAudioClips.map(g => g.clipId))
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId && !addedAudioIds.has(c.id)) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    // Reset pending drag delta from previous drag operation
    pendingDragDeltaRef.current = 0

    setDragState({
      type,
      trackId,
      clipId,
      startX: e.clientX,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      assetDurationMs,
      currentDeltaMs: 0,
      linkedVideoClipId: clip.linked_video_clip_id,
      linkedVideoLayerId: clip.linked_video_layer_id,
      linkedVideoInitialStartMs,
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
    })

    // Don't change selection if:
    // - SHIFT is held (let click handler deal with multi-select)
    // - Clip is already in multi-selection
    // - Clip is already the primary selection
    if (!e.shiftKey && !selectedAudioClips.has(clipId) && selectedClip?.clipId !== clipId) {
      handleClipSelect(trackId, clipId)
    }
  }, [timeline, assets, handleClipSelect, selectedVideoClips, selectedAudioClips, selectedClip])

  const handleClipDragMove = useCallback((e: MouseEvent) => {
    if (!dragState) return

    const deltaX = e.clientX - dragState.startX
    const deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    // Store pending delta for next animation frame
    pendingDragDeltaRef.current = deltaMs

    // Throttle updates with requestAnimationFrame for 60fps performance
    if (dragRafRef.current === null) {
      dragRafRef.current = requestAnimationFrame(() => {
        setDragState(prev => prev ? { ...prev, currentDeltaMs: pendingDragDeltaRef.current } : null)
        dragRafRef.current = null
      })
    }
  }, [dragState, pixelsPerSecond])

  const handleClipDragEnd = useCallback(() => {
    // Cancel any pending animation frame
    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current)
      dragRafRef.current = null
    }

    if (!dragState) {
      setDragState(null)
      return
    }

    // Use the latest pending delta for final position
    const deltaMs = pendingDragDeltaRef.current || dragState.currentDeltaMs

    // Build set of group audio clip IDs for quick lookup
    const groupAudioClipIds = new Set(dragState.groupAudioClips?.map(c => c.clipId) || [])

    const updatedTracks = timeline.audio_tracks.map((t) => {
      // Check if this track has the primary clip or any group clips
      const hasPrimaryClip = t.id === dragState.trackId
      const hasGroupClips = t.clips.some(c => groupAudioClipIds.has(c.id))
      if (!hasPrimaryClip && !hasGroupClips) return t

      return {
        ...t,
        clips: t.clips.map((clip) => {
          // Handle primary clip
          if (clip.id === dragState.clipId) {
            if (dragState.type === 'move') {
              const newStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
              return { ...clip, start_ms: newStartMs }
            } else if (dragState.type === 'trim-start') {
              const maxTrim = dragState.initialDurationMs - 100
              const minTrim = -dragState.initialInPointMs
              const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
              const newStartMs = Math.max(0, dragState.initialStartMs + trimAmount)
              const newInPointMs = dragState.initialInPointMs + trimAmount
              const newDurationMs = dragState.initialDurationMs - trimAmount
              const newOutPointMs = newInPointMs + newDurationMs
              return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
            } else if (dragState.type === 'trim-end') {
              const maxDuration = dragState.assetDurationMs - dragState.initialInPointMs
              const newDurationMs = Math.min(Math.max(100, dragState.initialDurationMs + deltaMs), maxDuration)
              const newOutPointMs = dragState.initialInPointMs + newDurationMs
              return { ...clip, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
            }
          }

          // Handle group audio clips (only for move, not trim)
          if (dragState.type === 'move' && groupAudioClipIds.has(clip.id)) {
            const groupClip = dragState.groupAudioClips?.find(c => c.clipId === clip.id)
            if (groupClip) {
              const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
              return { ...clip, start_ms: newStartMs }
            }
          }

          return clip
        }),
      }
    })

    // Build set of group video clip IDs for quick lookup
    const groupVideoClipIds = new Set(dragState.groupVideoClips?.map(c => c.clipId) || [])

    // Also update linked video clip (legacy) and group video clips if moving
    let updatedLayers = timeline.layers
    if (dragState.type === 'move') {
      const hasLegacyLink = dragState.linkedVideoClipId && dragState.linkedVideoLayerId && dragState.linkedVideoInitialStartMs !== undefined
      const hasGroupVideo = groupVideoClipIds.size > 0

      if (hasLegacyLink || hasGroupVideo) {
        updatedLayers = timeline.layers.map((layer) => {
          const hasLegacyClip = hasLegacyLink && layer.id === dragState.linkedVideoLayerId
          const hasGroupClips = layer.clips.some(c => groupVideoClipIds.has(c.id))
          if (!hasLegacyClip && !hasGroupClips) return layer

          return {
            ...layer,
            clips: layer.clips.map((videoClip) => {
              // Handle legacy linked clip
              if (hasLegacyLink && videoClip.id === dragState.linkedVideoClipId) {
                const newStartMs = Math.max(0, dragState.linkedVideoInitialStartMs! + deltaMs)
                return { ...videoClip, start_ms: newStartMs }
              }
              // Handle group video clips
              if (groupVideoClipIds.has(videoClip.id)) {
                const groupClip = dragState.groupVideoClips?.find(c => c.clipId === videoClip.id)
                if (groupClip) {
                  const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                  return { ...videoClip, start_ms: newStartMs }
                }
              }
              return videoClip
            }),
          }
        })
      }
    }

    // Save final state to server
    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks, layers: updatedLayers })
    setDragState(null)
    // Reset pending drag delta to prevent stale value affecting next drag
    pendingDragDeltaRef.current = 0
  }, [dragState, projectId, timeline, updateTimeline])

  // Add global mouse listeners for clip drag (audio clips)
  useEffect(() => {
    if (dragState) {
      window.addEventListener('mousemove', handleClipDragMove)
      window.addEventListener('mouseup', handleClipDragEnd)

      // Set cursor on body during drag for consistent UX
      const cursorStyle = dragState.type === 'move' ? 'grabbing' : 'ew-resize'
      document.body.style.cursor = cursorStyle
      document.body.style.userSelect = 'none'

      return () => {
        window.removeEventListener('mousemove', handleClipDragMove)
        window.removeEventListener('mouseup', handleClipDragEnd)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
  }, [dragState, handleClipDragMove, handleClipDragEnd])

  // Video clip drag handlers for move and trim
  const handleVideoClipDragStart = useCallback((
    e: React.MouseEvent,
    layerId: string,
    clipId: string,
    type: 'move' | 'trim-start' | 'trim-end'
  ) => {
    e.preventDefault()
    e.stopPropagation()

    const layer = timeline.layers.find(l => l.id === layerId)
    const clip = layer?.clips.find(c => c.id === clipId)
    if (!clip || layer?.locked) return

    // Get original asset duration for trim limits
    // For shape/text/image clips, allow unlimited resize (images don't have natural duration)
    const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    const isImageAsset = asset?.type === 'image'
    const isResizableClip = !!(clip.shape || clip.text_content || !clip.asset_id || isImageAsset)
    const assetDurationMs = isResizableClip ? Infinity : (asset?.duration_ms || clip.in_point_ms + clip.duration_ms)

    // Find linked audio clip's initial position (legacy support)
    let linkedAudioInitialStartMs: number | undefined
    if (clip.linked_audio_clip_id && clip.linked_audio_track_id) {
      const linkedTrack = timeline.audio_tracks.find(t => t.id === clip.linked_audio_track_id)
      const linkedClip = linkedTrack?.clips.find(c => c.id === clip.linked_audio_clip_id)
      linkedAudioInitialStartMs = linkedClip?.start_ms
    }

    // Get group clips initial positions (if in a group)
    const groupVideoClips: GroupClipInitialPosition[] = []
    const groupAudioClips: GroupClipInitialPosition[] = []

    // Check if clicked clip is part of current multi-selection
    const isClickedClipInSelection = selectedVideoClips.has(clipId) || selectedVideoClip?.clipId === clipId

    // Only include multi-selected clips if the clicked clip is part of the selection
    if (isClickedClipInSelection) {
      // Collect all multi-selected video clips (SHIFT+click selection)
      // Skip clips on locked layers
      if (selectedVideoClips.size > 0) {
        for (const l of timeline.layers) {
          if (l.locked) continue
          for (const c of l.clips) {
            if (selectedVideoClips.has(c.id) && c.id !== clipId) {
              groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
            }
          }
        }
      }

      // Collect all multi-selected audio clips
      if (selectedAudioClips.size > 0) {
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (selectedAudioClips.has(c.id)) {
              groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
            }
          }
        }
      }
    }

    // Also add group_id linked clips if clip is in a group
    if (clip.group_id) {
      // Collect all video clips in the group (except the dragged clip and already added)
      // Skip clips on locked layers
      const addedVideoIds = new Set(groupVideoClips.map(g => g.clipId))
      for (const l of timeline.layers) {
        if (l.locked) continue
        for (const c of l.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId && !addedVideoIds.has(c.id)) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      // Collect all audio clips in the group (except already added)
      const addedAudioIds = new Set(groupAudioClips.map(g => g.clipId))
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id && !addedAudioIds.has(c.id)) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    // Reset pending drag delta from previous drag operation
    pendingVideoDragDeltaRef.current = 0

    setVideoDragState({
      type,
      layerId,
      clipId,
      startX: e.clientX,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      assetDurationMs,
      currentDeltaMs: 0,
      isResizableClip,
      linkedAudioClipId: clip.linked_audio_clip_id,
      linkedAudioTrackId: clip.linked_audio_track_id,
      linkedAudioInitialStartMs,
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
    })

    // Don't change selection if:
    // - SHIFT is held (let click handler deal with multi-select)
    // - Clip is already in multi-selection
    // - Clip is already the primary selection
    if (!e.shiftKey && !selectedVideoClips.has(clipId) && selectedVideoClip?.clipId !== clipId) {
      handleVideoClipSelect(layerId, clipId)
    }
  }, [timeline, assets, handleVideoClipSelect, selectedVideoClips, selectedAudioClips, selectedVideoClip])

  const handleVideoClipDragMove = useCallback((e: MouseEvent) => {
    if (!videoDragState) return

    const deltaX = e.clientX - videoDragState.startX
    const deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    // Store pending delta for next animation frame
    pendingVideoDragDeltaRef.current = deltaMs

    // Throttle updates with requestAnimationFrame for 60fps performance
    if (videoDragRafRef.current === null) {
      videoDragRafRef.current = requestAnimationFrame(() => {
        setVideoDragState(prev => prev ? { ...prev, currentDeltaMs: pendingVideoDragDeltaRef.current } : null)
        videoDragRafRef.current = null
      })
    }
  }, [videoDragState, pixelsPerSecond])

  const handleVideoClipDragEnd = useCallback(() => {
    // Cancel any pending animation frame
    if (videoDragRafRef.current !== null) {
      cancelAnimationFrame(videoDragRafRef.current)
      videoDragRafRef.current = null
    }

    if (!videoDragState) {
      setVideoDragState(null)
      return
    }

    // Use the latest pending delta for final position
    const deltaMs = pendingVideoDragDeltaRef.current || videoDragState.currentDeltaMs

    // Build set of group video clip IDs for quick lookup
    const groupVideoClipIds = new Set(videoDragState.groupVideoClips?.map(c => c.clipId) || [])

    const updatedLayers = timeline.layers.map((layer) => {
      // Check if this layer has the primary clip or any group clips
      const hasPrimaryClip = layer.id === videoDragState.layerId
      const hasGroupClips = layer.clips.some(c => groupVideoClipIds.has(c.id))
      if (!hasPrimaryClip && !hasGroupClips) return layer

      return {
        ...layer,
        clips: layer.clips.map((clip) => {
          // Handle primary clip
          if (clip.id === videoDragState.clipId) {
            if (videoDragState.type === 'move') {
              const newStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
              return { ...clip, start_ms: newStartMs }
            } else if (videoDragState.type === 'trim-start') {
              const maxTrim = videoDragState.initialDurationMs - 100
              // For resizable clips (shape/text), allow unlimited left extension
              const minTrim = videoDragState.isResizableClip ? -Infinity : -videoDragState.initialInPointMs
              const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
              const newStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
              // Calculate effective trim based on actual start position change (accounts for clamping at 0)
              const effectiveTrim = newStartMs - videoDragState.initialStartMs
              const newInPointMs = videoDragState.isResizableClip ? 0 : videoDragState.initialInPointMs + effectiveTrim
              const newDurationMs = videoDragState.initialDurationMs - effectiveTrim
              const newOutPointMs = newInPointMs + newDurationMs
              return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
            } else if (videoDragState.type === 'trim-end') {
              // For resizable clips (shape/text), allow unlimited right extension
              const maxDuration = videoDragState.isResizableClip ? Infinity : videoDragState.assetDurationMs - videoDragState.initialInPointMs
              const newDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
              const newOutPointMs = videoDragState.initialInPointMs + newDurationMs
              return { ...clip, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
            }
          }

          // Handle group video clips (only for move, not trim)
          if (videoDragState.type === 'move' && groupVideoClipIds.has(clip.id)) {
            const groupClip = videoDragState.groupVideoClips?.find(c => c.clipId === clip.id)
            if (groupClip) {
              const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
              return { ...clip, start_ms: newStartMs }
            }
          }

          return clip
        }),
      }
    })

    // Build set of group audio clip IDs for quick lookup
    const groupAudioClipIds = new Set(videoDragState.groupAudioClips?.map(c => c.clipId) || [])

    // Also update linked audio clip (legacy) and group audio clips if moving
    let updatedTracks = timeline.audio_tracks
    if (videoDragState.type === 'move') {
      const hasLegacyLink = videoDragState.linkedAudioClipId && videoDragState.linkedAudioTrackId && videoDragState.linkedAudioInitialStartMs !== undefined
      const hasGroupAudio = groupAudioClipIds.size > 0

      if (hasLegacyLink || hasGroupAudio) {
        updatedTracks = timeline.audio_tracks.map((track) => {
          const hasLegacyClip = hasLegacyLink && track.id === videoDragState.linkedAudioTrackId
          const hasGroupClips = track.clips.some(c => groupAudioClipIds.has(c.id))
          if (!hasLegacyClip && !hasGroupClips) return track

          return {
            ...track,
            clips: track.clips.map((audioClip) => {
              // Handle legacy linked clip
              if (hasLegacyLink && audioClip.id === videoDragState.linkedAudioClipId) {
                const newStartMs = Math.max(0, videoDragState.linkedAudioInitialStartMs! + deltaMs)
                return { ...audioClip, start_ms: newStartMs }
              }
              // Handle group audio clips
              if (groupAudioClipIds.has(audioClip.id)) {
                const groupClip = videoDragState.groupAudioClips?.find(c => c.clipId === audioClip.id)
                if (groupClip) {
                  const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                  return { ...audioClip, start_ms: newStartMs }
                }
              }
              return audioClip
            }),
          }
        })
      }
    }

    // Save final state to server
    updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
    setVideoDragState(null)
    // Reset pending drag delta to prevent stale value affecting next drag
    pendingVideoDragDeltaRef.current = 0
  }, [videoDragState, projectId, timeline, updateTimeline])

  // Add global mouse listeners for video clip drag
  useEffect(() => {
    if (videoDragState) {
      window.addEventListener('mousemove', handleVideoClipDragMove)
      window.addEventListener('mouseup', handleVideoClipDragEnd)

      // Set cursor on body during drag for consistent UX
      const cursorStyle = videoDragState.type === 'move' ? 'grabbing' : 'ew-resize'
      document.body.style.cursor = cursorStyle
      document.body.style.userSelect = 'none'

      return () => {
        window.removeEventListener('mousemove', handleVideoClipDragMove)
        window.removeEventListener('mouseup', handleVideoClipDragEnd)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
  }, [videoDragState, handleVideoClipDragMove, handleVideoClipDragEnd])

  // Playhead drag handlers
  const handlePlayheadDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingPlayhead(true)
  }, [])

  const handlePlayheadDragMove = useCallback((e: MouseEvent) => {
    if (!isDraggingPlayhead || !timelineContainerRef.current || !onSeek) return

    const rect = timelineContainerRef.current.getBoundingClientRect()
    // Note: rect.left already accounts for scroll position (getBoundingClientRect is viewport-relative)
    // so we don't need to add scrollLeft here
    const offsetX = e.clientX - rect.left
    const timeMs = Math.max(0, Math.min(timeline.duration_ms, Math.round((offsetX / pixelsPerSecond) * 1000)))
    onSeek(timeMs)
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

  const getSelectedClipData = useCallback(() => {
    if (!selectedClip) return null
    const track = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
    if (!track) return null
    return track.clips.find(c => c.id === selectedClip.clipId) || null
  }, [selectedClip, timeline])

  const getSelectedVideoClipData = useCallback(() => {
    if (!selectedVideoClip) return null
    const layer = timeline.layers.find(l => l.id === selectedVideoClip.layerId)
    if (!layer) return null
    return layer.clips.find(c => c.id === selectedVideoClip.clipId) || null
  }, [selectedVideoClip, timeline])

  const selectedClipData = getSelectedClipData()
  const selectedVideoClipData = getSelectedVideoClipData()

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
      if (e.key === 'Escape' && isLinkingMode) {
        setIsLinkingMode(false)
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if ((selectedClip || selectedVideoClip) && document.activeElement?.tagName !== 'INPUT') {
          console.log('[handleKeyDown] Deleting clip...')
          e.preventDefault()
          handleDeleteClip()
        } else {
          console.log('[handleKeyDown] No clip selected or input focused')
        }
      }
      // Snap to previous shortcut (S key)
      if (e.key === 's' && !e.metaKey && !e.ctrlKey && document.activeElement?.tagName !== 'INPUT') {
        if (selectedClip || selectedVideoClip) {
          console.log('[handleKeyDown] Snapping clip to previous...')
          e.preventDefault()
          handleSnapToPrevious()
        }
      }
      // Forward select shortcut (A key)
      if (e.key === 'a' && !e.metaKey && !e.ctrlKey && document.activeElement?.tagName !== 'INPUT') {
        console.log('[handleKeyDown] Selecting forward...')
        e.preventDefault()
        handleSelectForward()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedClip, selectedVideoClip, handleDeleteClip, handleCutClip, handleSnapToPrevious, handleSelectForward, isLinkingMode])

  return (
    <div className="flex flex-col">
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
            title="カット"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.121 14.121L19 19m-7-7l7-7m-7 7l-2.879 2.879M12 12L9.121 9.121m0 5.758a3 3 0 10-4.243 4.243 3 3 0 004.243-4.243zm0-5.758a3 3 0 10-4.243-4.243 3 3 0 004.243 4.243z" />
            </svg>
            カット
          </button>
          {/* Snap to previous button */}
          <button
            onClick={handleSnapToPrevious}
            disabled={!selectedClip && !selectedVideoClip}
            className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            title="前のクリップにスナップ (S)"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
            スナップ
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
        </div>
      </div>

      {/* Timeline Content */}
      <div className="flex">
        {/* Track Labels */}
        <div
          ref={labelsScrollRef}
          onScroll={handleLabelsScroll}
          className="w-48 flex-shrink-0 border-r border-gray-700"
        >
          {/* Header spacer to align with Time Ruler */}
          <div className="h-6 border-b border-gray-700 flex items-center px-2">
            <span className="text-xs text-gray-500">トラック</span>
          </div>

          {/* Video Layers with linked audio tracks */}
          {timeline.layers.map((layer, layerIndex) => {
            const isLayerSelected = selectedLayerId === layer.id
            const canMoveUp = layerIndex > 0
            const canMoveDown = layerIndex < timeline.layers.length - 1
            const isDragging = draggingLayerId === layer.id
            const isDropTarget = dropTargetIndex === layerIndex
            return (
            <React.Fragment key={layer.id}>
            <div
              className={`h-12 border-b border-gray-700 flex items-center group cursor-pointer transition-colors ${
                isLayerSelected ? 'bg-primary-900/50 border-l-2 border-l-primary-500' : 'hover:bg-gray-700/50'
              } ${isDragging ? 'opacity-50' : ''} ${isDropTarget ? 'border-t-2 border-t-primary-500' : ''}`}
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
              <div className="flex-1 flex items-center justify-between px-2 py-1 min-w-0">
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
                  className="text-sm text-white bg-gray-700 border border-gray-600 rounded px-1 flex-1 outline-none focus:border-primary-500"
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span
                  className={`text-sm truncate flex-1 ${isLayerSelected ? 'text-primary-300' : 'text-white hover:text-primary-400'}`}
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    handleStartRenameLayer(layer.id, layer.name)
                  }}
                  title="クリックで選択、ダブルクリックで名前変更"
                >
                  {layer.name}
                </span>
              )}
              <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={() => handleToggleLayerVisibility(layer.id)}
                  className={`text-xs hover:text-white ${layer.visible ? 'text-gray-400' : 'text-gray-600'}`}
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
                <button
                  onClick={() => handleToggleLayerLock(layer.id)}
                  className={`text-xs hover:text-white ${layer.locked ? 'text-yellow-500' : 'text-gray-400'}`}
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
                <button
                  onClick={() => handleMoveLayerUp(layer.id)}
                  disabled={!canMoveUp}
                  className={`text-xs transition-opacity ${canMoveUp ? 'text-gray-400 hover:text-white' : 'text-gray-600 cursor-not-allowed'}`}
                  title="上へ移動"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                  </svg>
                </button>
                <button
                  onClick={() => handleMoveLayerDown(layer.id)}
                  disabled={!canMoveDown}
                  className={`text-xs transition-opacity ${canMoveDown ? 'text-gray-400 hover:text-white' : 'text-gray-600 cursor-not-allowed'}`}
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
            </div>
            {/* Linked Audio Track (rendered immediately below video layer) */}
            {linkedAudioTracksByLayerId.get(layer.id) && (() => {
              const linkedTrack = linkedAudioTracksByLayerId.get(layer.id)!
              return (
                <div
                  key={`linked-audio-${linkedTrack.id}`}
                  className="h-16 px-2 py-1 border-b border-gray-700 flex flex-col justify-center group bg-gray-800/30"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <svg className="w-3 h-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                      </svg>
                      <span className="text-sm text-white truncate flex-1">{linkedTrack.name}</span>
                      {linkedTrack.name.includes('抽出中') && (
                        <svg className="w-4 h-4 text-blue-400 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => handleMuteToggle(linkedTrack.id)}
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          linkedTrack.muted
                            ? 'bg-red-600 text-white'
                            : 'bg-gray-700 text-gray-400'
                        }`}
                        title="ミュート"
                      >
                        M
                      </button>
                      <button
                        onClick={() => handleDeleteAudioTrack(linkedTrack.id)}
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
                    value={linkedTrack.volume}
                    onChange={(e) => handleTrackVolumeChange(linkedTrack.id, parseFloat(e.target.value))}
                    className="w-full h-1 mt-1"
                  />
                </div>
              )
            })()}
            </React.Fragment>
            )
          })}

          {/* Standalone Audio Tracks (BGM, SE, Narration - not linked to video) */}
          {standaloneAudioTracks.map((track) => (
            <div
              key={track.id}
              className="h-16 px-2 py-1 border-b border-gray-700 flex flex-col justify-center group"
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
                  {track.ducking && (
                    <button
                      onClick={() => handleDuckingToggle(track.id, !track.ducking?.enabled)}
                      className={`text-xs px-1.5 py-0.5 rounded ${
                        track.ducking.enabled
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-700 text-gray-400'
                      }`}
                      title="ダッキング"
                    >
                      D
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

          {/* New Layer Drop Zone - Label Side */}
          <div
            className={`h-10 border-b border-dashed flex items-center px-4 transition-colors ${
              isDraggingNewLayer ? 'border-blue-500 bg-blue-900/30' : 'border-gray-600 bg-gray-800/50'
            }`}
            onDragOver={(e) => { e.preventDefault(); setIsDraggingNewLayer(true) }}
            onDragLeave={() => setIsDraggingNewLayer(false)}
            onDrop={handleNewLayerDrop}
          >
            <div className="flex items-center gap-2 text-gray-400 text-xs">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
              </svg>
              <span>{isDraggingNewLayer ? 'ドロップして新規レイヤー作成' : '新規レイヤーにドロップ'}</span>
            </div>
          </div>
        </div>

        {/* Timeline Tracks */}
        <div
          ref={tracksScrollRef}
          onScroll={handleTracksScroll}
          className="flex-1 overflow-x-auto"
        >
          <div ref={timelineContainerRef} className="relative" style={{ minWidth: Math.max(totalWidth, 800) }}>
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

            {/* Video Layers with linked audio tracks */}
            {timeline.layers.map((layer) => {
              // Color palette for layers
              const colorPalette = [
                'bg-purple-600/80 border-purple-500',
                'bg-blue-600/80 border-blue-500',
                'bg-teal-600/80 border-teal-500',
                'bg-pink-600/80 border-pink-500',
                'bg-yellow-600/80 border-yellow-500',
              ]
              // Use hash of layer.id to determine color (stable across reordering)
              const hashCode = (str: string): number => {
                let hash = 0
                for (let i = 0; i < str.length; i++) {
                  hash = ((hash << 5) - hash) + str.charCodeAt(i)
                  hash |= 0 // Convert to 32bit integer
                }
                return Math.abs(hash)
              }
              const clipColorClass = colorPalette[hashCode(layer.id) % colorPalette.length]
              const linkedAudioTrack = linkedAudioTracksByLayerId.get(layer.id)

              const isLayerSelected = selectedLayerId === layer.id
              return (
                <React.Fragment key={layer.id}>
                <div
                  ref={(el) => { layerRefs.current[layer.id] = el }}
                  className={`h-12 border-b border-gray-700 relative transition-colors cursor-pointer ${
                    dragOverLayer === layer.id
                      ? 'bg-purple-900/30 border-purple-500'
                      : isLayerSelected
                        ? 'bg-primary-900/30'
                        : 'bg-gray-800/50 hover:bg-gray-700/50'
                  } ${layer.locked ? 'opacity-50' : ''}`}
                  onClick={() => {
                    setSelectedLayerId(layer.id)
                    setSelectedVideoClip(null)
                    setSelectedClip(null)
                    if (onVideoClipSelect) onVideoClipSelect(null)
                    if (onClipSelect) onClipSelect(null)
                  }}
                  onDragOver={(e) => handleLayerDragOver(e, layer.id)}
                  onDragLeave={handleLayerDragLeave}
                  onDrop={(e) => handleLayerDrop(e, layer.id)}
                >
                  {layer.clips.map((clip) => {
                    const isSelected = selectedVideoClip?.layerId === layer.id && selectedVideoClip?.clipId === clip.id
                    const isMultiSelected = selectedVideoClips.has(clip.id)
                    const isDragging = videoDragState?.clipId === clip.id
                    const clipGroup = getClipGroup(clip.group_id)

                    // Calculate visual position/width during drag (without updating store)
                    let visualStartMs = clip.start_ms
                    let visualDurationMs = clip.duration_ms
                    if (isDragging && videoDragState) {
                      const deltaMs = videoDragState.currentDeltaMs
                      if (videoDragState.type === 'move') {
                        visualStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
                      } else if (videoDragState.type === 'trim-start') {
                        const maxTrim = videoDragState.initialDurationMs - 100
                        // For resizable clips (shape/text), allow unlimited left extension
                        const minTrim = videoDragState.isResizableClip ? -Infinity : -videoDragState.initialInPointMs
                        const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                        visualStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
                        // Calculate effective trim based on actual start position change (accounts for clamping at 0)
                        const effectiveTrim = visualStartMs - videoDragState.initialStartMs
                        visualDurationMs = videoDragState.initialDurationMs - effectiveTrim
                      } else if (videoDragState.type === 'trim-end') {
                        // For resizable clips (shape/text), allow unlimited right extension
                        const maxDuration = videoDragState.isResizableClip ? Infinity : videoDragState.assetDurationMs - videoDragState.initialInPointMs
                        visualDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
                      }
                    } else if (videoDragState?.type === 'move' && videoDragGroupVideoClipIds.has(clip.id)) {
                      const groupClip = videoDragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                      if (groupClip) {
                        visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
                      }
                    } else if (dragState?.type === 'move') {
                      if (dragState.linkedVideoClipId === clip.id && dragState.linkedVideoInitialStartMs !== undefined) {
                        visualStartMs = Math.max(0, dragState.linkedVideoInitialStartMs + dragState.currentDeltaMs)
                      } else if (dragGroupVideoClipIds.has(clip.id)) {
                        const groupClip = dragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                        if (groupClip) {
                          visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
                        }
                      }
                    }
                    const clipWidth = Math.max((visualDurationMs / 1000) * pixelsPerSecond, 40)

                    return (
                      <div
                        key={clip.id}
                        className={`absolute top-1 bottom-1 rounded select-none ${clipColorClass} ${
                          isSelected ? 'ring-2 ring-white z-10' : ''
                        } ${isMultiSelected ? 'ring-2 ring-blue-400 z-10' : ''} ${isDragging ? 'opacity-80' : ''} ${layer.locked ? 'cursor-not-allowed' : ''}`}
                        style={{
                          left: 0,
                          transform: `translateX(${(visualStartMs / 1000) * pixelsPerSecond}px)`,
                          width: clipWidth,
                          borderWidth: 1,
                          cursor: layer.locked
                            ? 'not-allowed'
                            : videoDragState?.type === 'move'
                              ? 'grabbing'
                              : videoDragState?.type === 'trim-start' || videoDragState?.type === 'trim-end'
                                ? 'ew-resize'
                                : 'grab',
                          willChange: isDragging ? 'transform, width' : 'auto',
                        }}
                        onClick={(e) => {
                          e.stopPropagation()
                          if (!layer.locked) handleVideoClipSelect(layer.id, clip.id, e)
                        }}
                        onMouseDown={(e) => !layer.locked && handleVideoClipDragStart(e, layer.id, clip.id, 'move')}
                      >
                        {/* Group indicator */}
                        {clipGroup && (
                          <div
                            className="absolute top-0 left-0 right-0 h-1 rounded-t"
                            style={{ backgroundColor: clipGroup.color }}
                            title={clipGroup.name}
                          />
                        )}
                        {/* Thumbnails for video clips */}
                        {clip.asset_id && assets.find(a => a.id === clip.asset_id)?.type === 'video' && (
                          <VideoClipThumbnails
                            projectId={projectId}
                            assetId={clip.asset_id}
                            clipWidth={clipWidth}
                            durationMs={clip.duration_ms}
                            inPointMs={clip.in_point_ms}
                          />
                        )}
                        {/* Trim handles - wider clickable area for easier resize */}
                        {!layer.locked && (
                          <>
                            <div
                              className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                              onMouseDown={(e) => {
                                e.stopPropagation()
                                handleVideoClipDragStart(e, layer.id, clip.id, 'trim-start')
                              }}
                            />
                            <div
                              className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                              onMouseDown={(e) => {
                                e.stopPropagation()
                                handleVideoClipDragStart(e, layer.id, clip.id, 'trim-end')
                              }}
                            />
                          </>
                        )}
                        {/* Fade envelope SVG overlay */}
                        {((clip.effects.fade_in_ms ?? 0) > 0 || (clip.effects.fade_out_ms ?? 0) > 0) && (() => {
                          const fadeInPx = ((clip.effects.fade_in_ms ?? 0) / 1000) * pixelsPerSecond
                          const fadeOutPx = ((clip.effects.fade_out_ms ?? 0) / 1000) * pixelsPerSecond
                          const w = clipWidth
                          const h = 32
                          return (
                            <svg
                              className="absolute inset-0 w-full h-full pointer-events-none z-30"
                              preserveAspectRatio="none"
                              viewBox={`0 0 ${w} ${h}`}
                            >
                              {/* Fade-in dark triangle */}
                              {(clip.effects.fade_in_ms ?? 0) > 0 && (
                                <polygon
                                  points={`0,${h} ${fadeInPx},0 0,0`}
                                  fill="rgba(0,0,0,0.5)"
                                />
                              )}
                              {/* Fade-out dark triangle */}
                              {(clip.effects.fade_out_ms ?? 0) > 0 && (
                                <polygon
                                  points={`${w},${h} ${w - fadeOutPx},0 ${w},0`}
                                  fill="rgba(0,0,0,0.5)"
                                />
                              )}
                              {/* Envelope line (white) */}
                              <polyline
                                points={`0,${h} ${fadeInPx},2 ${w - fadeOutPx},2 ${w},${h}`}
                                fill="none"
                                stroke="rgba(255,255,255,0.9)"
                                strokeWidth="2"
                                vectorEffect="non-scaling-stroke"
                              />
                            </svg>
                          )
                        })()}
                        <span className="text-xs text-white px-2 truncate block leading-[2.5rem] pointer-events-none">
                          {clip.asset_id ? getAssetName(clip.asset_id) : clip.text_content ? clip.text_content.slice(0, 10) : clip.shape ? clip.shape.type : 'Clip'}
                        </span>
                      </div>
                    )
                  })}
                </div>
                {/* Linked Audio Track (rendered immediately below video layer) */}
                {linkedAudioTrack && (
                  <div
                    ref={(el) => { trackRefs.current[linkedAudioTrack.id] = el }}
                    className={`h-16 border-b border-gray-700 relative transition-colors bg-gray-800/30 ${
                      dragOverTrack === linkedAudioTrack.id
                        ? 'bg-green-900/30 border-green-500'
                        : ''
                    }`}
                    onDragOver={(e) => handleDragOver(e, linkedAudioTrack.id)}
                    onDragLeave={handleDragLeave}
                    onDrop={(e) => handleDrop(e, linkedAudioTrack.id)}
                  >
                    {linkedAudioTrack.clips.map((clip) => {
                      const isSelected = selectedClip?.trackId === linkedAudioTrack.id && selectedClip?.clipId === clip.id
                      const isMultiSelected = selectedAudioClips.has(clip.id)
                      const isDragging = dragState?.clipId === clip.id
                      const clipColor = '#22c55e'  // Green for linked audio
                      const audioClipGroup = getClipGroup(clip.group_id)

                      let visualStartMs = clip.start_ms
                      let visualDurationMs = clip.duration_ms
                      if (isDragging && dragState) {
                        const deltaMs = dragState.currentDeltaMs
                        if (dragState.type === 'move') {
                          visualStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
                        } else if (dragState.type === 'trim-start') {
                          const maxTrim = dragState.initialDurationMs - 100
                          const minTrim = -dragState.initialInPointMs
                          const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                          visualStartMs = Math.max(0, dragState.initialStartMs + trimAmount)
                          visualDurationMs = dragState.initialDurationMs - trimAmount
                        } else if (dragState.type === 'trim-end') {
                          const maxDuration = dragState.assetDurationMs - dragState.initialInPointMs
                          visualDurationMs = Math.min(Math.max(100, dragState.initialDurationMs + deltaMs), maxDuration)
                        }
                      } else if (dragState?.type === 'move' && dragGroupAudioClipIds.has(clip.id)) {
                        // This clip is in a group being dragged (audio drag) - O(1) lookup
                        const groupClip = dragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
                        if (groupClip) {
                          visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
                        }
                      } else if (videoDragState?.type === 'move') {
                        if (videoDragState.linkedAudioClipId === clip.id && videoDragState.linkedAudioInitialStartMs !== undefined) {
                          visualStartMs = Math.max(0, videoDragState.linkedAudioInitialStartMs + videoDragState.currentDeltaMs)
                        } else if (videoDragGroupAudioClipIds.has(clip.id)) {
                          const groupClip = videoDragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
                          if (groupClip) {
                            visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
                          }
                        }
                      }
                      const clipWidth = Math.max((visualDurationMs / 1000) * pixelsPerSecond, 40)

                      return (
                        <div
                          key={clip.id}
                          className={`absolute top-1 bottom-1 rounded select-none ${
                            isSelected ? 'ring-2 ring-white z-10' : ''
                          } ${isMultiSelected ? 'ring-2 ring-blue-400 z-10' : ''} ${isDragging ? 'opacity-80' : ''}`}
                          style={{
                            left: 0,
                            transform: `translateX(${(visualStartMs / 1000) * pixelsPerSecond}px)`,
                            width: clipWidth,
                            backgroundColor: `${clipColor}33`,
                            borderWidth: 1,
                            borderColor: clipColor,
                            cursor: dragState?.type === 'move' ? 'grabbing' : 'grab',
                            willChange: isDragging ? 'transform, width' : 'auto',
                          }}
                          onClick={(e) => {
                            e.stopPropagation()
                            handleClipSelect(linkedAudioTrack.id, clip.id, e)
                          }}
                          onMouseDown={(e) => handleClipDragStart(e, linkedAudioTrack.id, clip.id, 'move')}
                        >
                          {/* Group indicator */}
                          {audioClipGroup && (
                            <div
                              className="absolute top-0 left-0 right-0 h-1 rounded-t"
                              style={{ backgroundColor: audioClipGroup.color }}
                              title={audioClipGroup.name}
                            />
                          )}
                          {/* Waveform */}
                          <AudioClipWaveform
                            projectId={projectId}
                            assetId={clip.asset_id}
                            width={clipWidth}
                            height={56}
                            color={clipColor}
                            inPointMs={clip.in_point_ms}
                            clipDurationMs={clip.duration_ms}
                            assetDurationMs={assets.find(a => a.id === clip.asset_id)?.duration_ms || clip.duration_ms}
                          />
                          {/* Trim handles - wider clickable area for easier resize */}
                          <div
                            className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleClipDragStart(e, linkedAudioTrack.id, clip.id, 'trim-start')
                            }}
                          />
                          <div
                            className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleClipDragStart(e, linkedAudioTrack.id, clip.id, 'trim-end')
                            }}
                          />
                          {/* Fade envelope SVG overlay */}
                          {(clip.fade_in_ms > 0 || clip.fade_out_ms > 0) && (() => {
                            const fadeInPx = (clip.fade_in_ms / 1000) * pixelsPerSecond
                            const fadeOutPx = (clip.fade_out_ms / 1000) * pixelsPerSecond
                            const w = clipWidth
                            const h = 48
                            return (
                              <svg
                                className="absolute inset-0 w-full h-full pointer-events-none z-30"
                                preserveAspectRatio="none"
                                viewBox={`0 0 ${w} ${h}`}
                              >
                                {/* Fade-in dark triangle */}
                                {clip.fade_in_ms > 0 && (
                                  <polygon
                                    points={`0,${h} ${fadeInPx},0 0,0`}
                                    fill="rgba(0,0,0,0.5)"
                                  />
                                )}
                                {/* Fade-out dark triangle */}
                                {clip.fade_out_ms > 0 && (
                                  <polygon
                                    points={`${w},${h} ${w - fadeOutPx},0 ${w},0`}
                                    fill="rgba(0,0,0,0.5)"
                                  />
                                )}
                                {/* Envelope line (white) */}
                                <polyline
                                  points={`0,${h} ${fadeInPx},2 ${w - fadeOutPx},2 ${w},${h}`}
                                  fill="none"
                                  stroke="rgba(255,255,255,0.9)"
                                  strokeWidth="2"
                                  vectorEffect="non-scaling-stroke"
                                />
                              </svg>
                            )
                          })()}
                          <span className="text-xs text-white px-3 truncate block leading-[3.5rem] pointer-events-none">
                            {getAssetName(clip.asset_id)}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}
                </React.Fragment>
              )
            })}

            {/* Standalone Audio Tracks (BGM, SE, Narration - not linked to video) */}
            {standaloneAudioTracks.map((track) => (
              <div
                key={track.id}
                ref={(el) => { trackRefs.current[track.id] = el }}
                className={`h-16 border-b border-gray-700 relative transition-colors ${
                  dragOverTrack === track.id
                    ? 'bg-green-900/30 border-green-500'
                    : 'bg-gray-800/50'
                }`}
                onDragOver={(e) => handleDragOver(e, track.id)}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, track.id)}
              >
                {track.clips.map((clip) => {
                  const isSelected = selectedClip?.trackId === track.id && selectedClip?.clipId === clip.id
                  const isMultiSelected = selectedAudioClips.has(clip.id)
                  const isDragging = dragState?.clipId === clip.id
                  const clipColor = track.type === 'narration' ? '#22c55e' : track.type === 'bgm' ? '#3b82f6' : '#f59e0b'
                  const isLinkTarget = isLinkingMode && !clip.linked_video_clip_id // Can be linked if not already linked
                  const audioClipGroup = getClipGroup(clip.group_id)

                  // Calculate visual position/width during drag (without updating store)
                  let visualStartMs = clip.start_ms
                  let visualDurationMs = clip.duration_ms
                  if (isDragging && dragState) {
                    const deltaMs = dragState.currentDeltaMs
                    if (dragState.type === 'move') {
                      visualStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
                    } else if (dragState.type === 'trim-start') {
                      const maxTrim = dragState.initialDurationMs - 100
                      const minTrim = -dragState.initialInPointMs
                      const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                      visualStartMs = Math.max(0, dragState.initialStartMs + trimAmount)
                      visualDurationMs = dragState.initialDurationMs - trimAmount
                    } else if (dragState.type === 'trim-end') {
                      const maxDuration = dragState.assetDurationMs - dragState.initialInPointMs
                      visualDurationMs = Math.min(Math.max(100, dragState.initialDurationMs + deltaMs), maxDuration)
                    }
                  } else if (dragState?.type === 'move' && dragGroupAudioClipIds.has(clip.id)) {
                    // This clip is in a group being dragged (audio drag) - O(1) lookup
                    const groupClip = dragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
                    if (groupClip) {
                      visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
                    }
                  } else if (videoDragState?.type === 'move') {
                    // Check if this audio clip is linked/grouped to the video being dragged
                    if (videoDragState.linkedAudioClipId === clip.id && videoDragState.linkedAudioInitialStartMs !== undefined) {
                      visualStartMs = Math.max(0, videoDragState.linkedAudioInitialStartMs + videoDragState.currentDeltaMs)
                    } else if (videoDragGroupAudioClipIds.has(clip.id)) {
                      // O(1) lookup using memoized Set
                      const groupClip = videoDragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
                      if (groupClip) {
                        visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
                      }
                    }
                  }
                  const clipWidth = Math.max((visualDurationMs / 1000) * pixelsPerSecond, 40)

                  return (
                    <div
                      key={clip.id}
                      className={`absolute top-1 bottom-1 rounded select-none ${
                        isSelected ? 'ring-2 ring-white z-10' : ''
                      } ${isMultiSelected ? 'ring-2 ring-blue-400 z-10' : ''} ${isDragging ? 'opacity-80' : ''} ${isLinkTarget ? 'ring-2 ring-blue-400 ring-opacity-75 animate-pulse' : ''}`}
                      style={{
                        left: 0,
                        transform: `translateX(${(visualStartMs / 1000) * pixelsPerSecond}px)`,
                        width: clipWidth,
                        backgroundColor: `${clipColor}33`,
                        borderWidth: 1,
                        borderColor: clipColor,
                        cursor: isLinkTarget ? 'pointer' : dragState?.type === 'move' ? 'grabbing' : 'grab',
                        willChange: isDragging ? 'transform, width' : 'auto',
                      }}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (isLinkTarget || e.shiftKey) {
                          handleClipSelect(track.id, clip.id, e)
                        }
                      }}
                      onMouseDown={(e) => !isLinkingMode && handleClipDragStart(e, track.id, clip.id, 'move')}
                    >
                      {/* Group indicator - colored bar at top */}
                      {audioClipGroup && (
                        <div
                          className="absolute top-0 left-0 right-0 h-1 rounded-t"
                          style={{ backgroundColor: audioClipGroup.color }}
                          title={audioClipGroup.name}
                        />
                      )}
                      {/* Waveform display */}
                      <AudioClipWaveform
                        projectId={projectId}
                        assetId={clip.asset_id}
                        width={clipWidth}
                        height={56}
                        color={clipColor}
                        inPointMs={clip.in_point_ms}
                        clipDurationMs={clip.duration_ms}
                        assetDurationMs={assets.find(a => a.id === clip.asset_id)?.duration_ms || clip.duration_ms}
                      />
                      {/* Trim handle - left (wider clickable area for easier resize) */}
                      <div
                        className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          handleClipDragStart(e, track.id, clip.id, 'trim-start')
                        }}
                      />
                      {/* Trim handle - right (wider clickable area for easier resize) */}
                      <div
                        className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          handleClipDragStart(e, track.id, clip.id, 'trim-end')
                        }}
                      />
                      {/* Fade envelope SVG overlay */}
                      {(clip.fade_in_ms > 0 || clip.fade_out_ms > 0) && (() => {
                        const fadeInPx = (clip.fade_in_ms / 1000) * pixelsPerSecond
                        const fadeOutPx = (clip.fade_out_ms / 1000) * pixelsPerSecond
                        const w = clipWidth
                        const h = 48
                        return (
                          <svg
                            className="absolute inset-0 w-full h-full pointer-events-none z-30"
                            preserveAspectRatio="none"
                            viewBox={`0 0 ${w} ${h}`}
                          >
                            {/* Fade-in dark triangle */}
                            {clip.fade_in_ms > 0 && (
                              <polygon
                                points={`0,${h} ${fadeInPx},0 0,0`}
                                fill="rgba(0,0,0,0.5)"
                              />
                            )}
                            {/* Fade-out dark triangle */}
                            {clip.fade_out_ms > 0 && (
                              <polygon
                                points={`${w},${h} ${w - fadeOutPx},0 ${w},0`}
                                fill="rgba(0,0,0,0.5)"
                              />
                            )}
                            {/* Envelope line (white) */}
                            <polyline
                              points={`0,${h} ${fadeInPx},2 ${w - fadeOutPx},2 ${w},${h}`}
                              fill="none"
                              stroke="rgba(255,255,255,0.9)"
                              strokeWidth="2"
                              vectorEffect="non-scaling-stroke"
                            />
                          </svg>
                        )
                      })()}
                      <span className="text-xs text-white px-3 truncate block leading-[3.5rem] pointer-events-none">
                        {getAssetName(clip.asset_id)}
                      </span>
                      {/* Link indicator */}
                      {clip.linked_video_clip_id && (
                        <div className="absolute top-0.5 right-1 pointer-events-none" title="映像とリンク済み">
                          <svg className="w-3 h-3 text-white/70" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                          </svg>
                        </div>
                      )}
                    </div>
                  )
                })}
                {/* Drop indicator */}
                {dragOverTrack === track.id && (
                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <span className="text-green-400 text-sm">ここにドロップ</span>
                  </div>
                )}
              </div>
            ))}

            {/* New Layer Drop Zone - Clip Area Side */}
            <div
              className={`h-10 border-b border-dashed relative transition-colors ${
                isDraggingNewLayer ? 'border-blue-500 bg-blue-900/30' : 'border-gray-600 bg-gray-800/30'
              }`}
              onDragOver={(e) => { e.preventDefault(); setIsDraggingNewLayer(true) }}
              onDragLeave={() => setIsDraggingNewLayer(false)}
              onDrop={handleNewLayerDrop}
            >
              {isDraggingNewLayer && (
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <span className="text-blue-400 text-sm">ドロップして新規レイヤー作成</span>
                </div>
              )}
            </div>

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
      </div>

      {/* Audio Clip Properties Panel */}
      {selectedClipData && selectedClip && (
        <div className="h-20 border-t border-gray-700 bg-gray-800 px-4 py-2 flex items-center gap-4">
          <div className="text-sm text-white">
            {getAssetName(selectedClipData.asset_id)}
          </div>

          {/* Group indicator and controls */}
          {(() => {
            const clipGroup = getClipGroup(selectedClipData.group_id)
            return (
              <div className="flex items-center gap-2">
                {clipGroup ? (
                  <>
                    <span
                      className="px-2 py-0.5 text-xs rounded text-white"
                      style={{ backgroundColor: clipGroup.color }}
                    >
                      {clipGroup.name}
                    </span>
                    <button
                      onClick={() => handleRemoveFromGroup('audio', selectedClip.trackId, selectedClip.clipId)}
                      className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded"
                      title="グループから外す"
                    >
                      外す
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={handleCreateGroup}
                      className="px-2 py-1 text-xs text-green-400 hover:text-green-300 hover:bg-green-900/30 rounded"
                      title="新規グループ作成"
                    >
                      + グループ
                    </button>
                    {timeline.groups && timeline.groups.length > 0 && (
                      <div className="relative group">
                        <button className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded">
                          追加...
                        </button>
                        <div className="absolute bottom-full left-0 mb-1 bg-gray-700 rounded shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-20 min-w-[100px]">
                          {timeline.groups.map(g => (
                            <button
                              key={g.id}
                              onClick={() => handleAddToGroup(g.id)}
                              className="w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600 flex items-center gap-2"
                            >
                              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: g.color }} />
                              {g.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )
          })()}

          {/* AI Analysis button - only for narration tracks */}
          {(() => {
            const track = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
            return track?.type === 'narration' && (
              <button
                onClick={() => handleStartTranscription(selectedClipData.asset_id)}
                disabled={isTranscribing}
                className={`px-2 py-1 text-xs rounded flex items-center gap-1 ${
                  isTranscribing
                    ? 'bg-purple-900/50 text-purple-300 cursor-wait'
                    : 'text-purple-400 hover:text-purple-300 hover:bg-purple-900/30'
                }`}
                title="AIで無音・言い間違いを検出"
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
                {isTranscribing ? '分析中...' : 'AI分析'}
              </button>
            )
          })()}

          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">フェードイン</span>
            <input
              type="range"
              min="0"
              max="3000"
              step="100"
              value={selectedClipData.fade_in_ms}
              onChange={(e) => handleFadeChange('in', parseInt(e.target.value))}
              className="w-24 h-1"
            />
            <span className="text-xs text-gray-400 w-12">{(selectedClipData.fade_in_ms / 1000).toFixed(1)}s</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">フェードアウト</span>
            <input
              type="range"
              min="0"
              max="3000"
              step="100"
              value={selectedClipData.fade_out_ms}
              onChange={(e) => handleFadeChange('out', parseInt(e.target.value))}
              className="w-24 h-1"
            />
            <span className="text-xs text-gray-400 w-12">{(selectedClipData.fade_out_ms / 1000).toFixed(1)}s</span>
          </div>
          <button
            onClick={handleDeleteClip}
            className="ml-auto px-3 py-1 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded"
          >
            削除
          </button>
        </div>
      )}

      {/* Video Clip Properties Panel */}
      {selectedVideoClipData && selectedVideoClip && (
        <div className="h-14 border-t border-gray-700 bg-gray-800 px-4 py-2 flex items-center gap-4">
          <div className="text-sm text-white">
            {selectedVideoClipData.asset_id ? getAssetName(selectedVideoClipData.asset_id) : selectedVideoClipData.text_content ? (
              <span className="flex items-center gap-1">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
                テキスト: {selectedVideoClipData.text_content.slice(0, 15)}{selectedVideoClipData.text_content.length > 15 ? '...' : ''}
              </span>
            ) : selectedVideoClipData.shape ? (
              <span className="flex items-center gap-1">
                {selectedVideoClipData.shape.type === 'rectangle' && '四角形'}
                {selectedVideoClipData.shape.type === 'circle' && '円'}
                {selectedVideoClipData.shape.type === 'line' && '線'}
              </span>
            ) : 'Clip'}
          </div>

          {/* Group indicator and controls */}
          {(() => {
            const clipGroup = getClipGroup(selectedVideoClipData.group_id)
            return (
              <div className="flex items-center gap-2">
                {clipGroup ? (
                  <>
                    <span
                      className="px-2 py-0.5 text-xs rounded text-white"
                      style={{ backgroundColor: clipGroup.color }}
                    >
                      {clipGroup.name}
                    </span>
                    <button
                      onClick={() => handleRemoveFromGroup('video', selectedVideoClip.layerId, selectedVideoClip.clipId)}
                      className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded"
                      title="グループから外す"
                    >
                      外す
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={handleCreateGroup}
                      className="px-2 py-1 text-xs text-green-400 hover:text-green-300 hover:bg-green-900/30 rounded"
                      title="新規グループ作成"
                    >
                      + グループ
                    </button>
                    {timeline.groups && timeline.groups.length > 0 && (
                      <div className="relative group">
                        <button className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded">
                          追加...
                        </button>
                        <div className="absolute bottom-full left-0 mb-1 bg-gray-700 rounded shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-20 min-w-[100px]">
                          {timeline.groups.map(g => (
                            <button
                              key={g.id}
                              onClick={() => handleAddToGroup(g.id)}
                              className="w-full px-3 py-1.5 text-xs text-left text-white hover:bg-gray-600 flex items-center gap-2"
                            >
                              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: g.color }} />
                              {g.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )
          })()}

          {/* Link/Unlink buttons */}
          {selectedVideoClipData.linked_audio_clip_id ? (
            // Legacy link system - unlink button
            <button
              onClick={() => handleUnlinkVideoClip(selectedVideoClip.layerId, selectedVideoClip.clipId)}
              className="px-2 py-1 text-xs text-yellow-400 hover:text-yellow-300 hover:bg-yellow-900/30 rounded flex items-center gap-1"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              リンク解除
            </button>
          ) : selectedVideoClipData.group_id ? (
            // Group-based link system - unlink button
            <button
              onClick={() => handleUnlinkVideoAudioGroup(selectedVideoClipData.group_id!)}
              className="px-2 py-1 text-xs text-yellow-400 hover:text-yellow-300 hover:bg-yellow-900/30 rounded flex items-center gap-1"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              リンク解除
            </button>
          ) : (
            // No link - show link button
            <button
              onClick={() => setIsLinkingMode(true)}
              className={`px-2 py-1 text-xs rounded flex items-center gap-1 ${
                isLinkingMode
                  ? 'bg-blue-600 text-white'
                  : 'text-blue-400 hover:text-blue-300 hover:bg-blue-900/30'
              }`}
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              {isLinkingMode ? '選択...' : 'リンク'}
            </button>
          )}

          {isLinkingMode && (
            <span className="text-xs text-gray-400">ESCでキャンセル</span>
          )}

          {/* Video Fade Controls */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">フェードイン</span>
            <input
              type="range"
              min="0"
              max="3000"
              step="100"
              value={selectedVideoClipData.effects.fade_in_ms || 0}
              onChange={(e) => handleVideoFadeChange('in', parseInt(e.target.value))}
              className="w-24 h-1"
            />
            <span className="text-xs text-gray-400 w-12">{((selectedVideoClipData.effects.fade_in_ms || 0) / 1000).toFixed(1)}s</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">フェードアウト</span>
            <input
              type="range"
              min="0"
              max="3000"
              step="100"
              value={selectedVideoClipData.effects.fade_out_ms || 0}
              onChange={(e) => handleVideoFadeChange('out', parseInt(e.target.value))}
              className="w-24 h-1"
            />
            <span className="text-xs text-gray-400 w-12">{((selectedVideoClipData.effects.fade_out_ms || 0) / 1000).toFixed(1)}s</span>
          </div>

          <button
            onClick={handleDeleteClip}
            className="ml-auto px-3 py-1 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded"
          >
            削除
          </button>
        </div>
      )}

      {/* Linking mode indicator */}
      {isLinkingMode && (
        <div className="h-8 bg-blue-900/50 border-t border-blue-500 px-4 flex items-center">
          <span className="text-sm text-blue-300">リンクする音声クリップをクリックしてください</span>
        </div>
      )}

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
    </div>
  )
}
