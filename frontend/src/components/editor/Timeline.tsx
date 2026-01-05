import { useState, useCallback, useRef, useEffect } from 'react'
import type { TimelineData, AudioClip, Clip, Keyframe, ShapeType, Shape, ClipGroup } from '@/store/projectStore'
import { useProjectStore } from '@/store/projectStore'
import { v4 as uuidv4 } from 'uuid'
import { transcriptionApi, type Transcription } from '@/api/transcription'

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
  // Linked audio clip info (for synchronized movement) - legacy
  linkedAudioClipId?: string | null
  linkedAudioTrackId?: string | null
  linkedAudioInitialStartMs?: number
  // Group clip info (for synchronized movement)
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
}

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

  const pixelsPerSecond = 100 * zoom
  const totalWidth = (timeline.duration_ms / 1000) * pixelsPerSecond

  const formatTime = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

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
    const updatedLayers = timeline.layers.filter(l => l.id !== layerId)
    await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
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
        name: 'レイヤー 1',
        order: 0,
        visible: true,
        locked: false,
        clips: [] as Clip[],
      }
      updatedLayers = [newLayer]
      targetLayerId = newLayer.id
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

    // Calculate drop position in ms
    const trackElement = trackRefs.current[trackId]
    if (!trackElement) {
      console.log('[handleDrop] SKIP - trackElement not found')
      return
    }

    const rect = trackElement.getBoundingClientRect()
    const offsetX = e.clientX - rect.left
    const startMs = Math.max(0, Math.round((offsetX / pixelsPerSecond) * 1000))

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
  }, [assets, timeline, projectId, pixelsPerSecond, updateTimeline])

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

    const layer = timeline.layers.find(l => l.id === layerId)
    if (!layer) {
      console.log('[handleLayerDrop] SKIP - layer not found')
      return
    }

    if (layer.locked) {
      console.log('[handleLayerDrop] SKIP - layer is locked')
      return
    }

    // Calculate drop position in ms
    const layerElement = layerRefs.current[layerId]
    if (!layerElement) {
      console.log('[handleLayerDrop] SKIP - layerElement not found')
      return
    }

    const rect = layerElement.getBoundingClientRect()
    const offsetX = e.clientX - rect.left
    const startMs = Math.max(0, Math.round((offsetX / pixelsPerSecond) * 1000))

    // Create new video clip with default transform and effects
    const newClip: Clip = {
      id: uuidv4(),
      asset_id: assetId,
      start_ms: startMs,
      duration_ms: asset.duration_ms || 5000,
      in_point_ms: 0,
      out_point_ms: null,
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

    const updatedLayers = timeline.layers.map((l) =>
      l.id === layerId ? { ...l, clips: [...l.clips, newClip] } : l
    )

    // Update duration if needed
    const newDuration = Math.max(
      timeline.duration_ms,
      startMs + (asset.duration_ms || 5000)
    )

    console.log('[handleLayerDrop] Calling updateTimeline with duration:', newDuration)
    await updateTimeline(projectId, {
      ...timeline,
      layers: updatedLayers,
      duration_ms: newDuration,
    })
    console.log('[handleLayerDrop] DONE')
  }, [assets, timeline, projectId, pixelsPerSecond, updateTimeline])

  const handleClipSelect = useCallback((trackId: string, clipId: string) => {
    // If in linking mode, link the audio clip to the selected video clip
    if (isLinkingMode && selectedVideoClip) {
      handleLinkClips(selectedVideoClip.layerId, selectedVideoClip.clipId, trackId, clipId)
      setIsLinkingMode(false)
      return
    }

    setSelectedClip({ trackId, clipId })
    setSelectedVideoClip(null) // Deselect video clip

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
  }, [timeline, assets, onClipSelect, onVideoClipSelect, isLinkingMode, selectedVideoClip, handleLinkClips])

  // Video clip selection handler
  const handleVideoClipSelect = useCallback((layerId: string, clipId: string) => {
    console.log('[handleVideoClipSelect] layerId:', layerId, 'clipId:', clipId)
    setSelectedVideoClip({ layerId, clipId })
    setSelectedLayerId(layerId) // Also select the layer
    setSelectedClip(null) // Deselect audio clip

    // Notify parent of selection
    if (onVideoClipSelect) {
      const layer = timeline.layers.find(l => l.id === layerId)
      if (layer) {
        const clip = layer.clips.find(c => c.id === clipId)
        if (clip) {
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
          })
          return
        }
      }
      onVideoClipSelect(null)
    }
    if (onClipSelect) {
      onClipSelect(null)
    }
  }, [timeline, assets, onClipSelect, onVideoClipSelect])

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
      const updatedLayers = timeline.layers.map((layer) =>
        layer.id === selectedVideoClip.layerId
          ? { ...layer, clips: layer.clips.filter((c) => c.id !== selectedVideoClip.clipId) }
          : layer
      )
      await updateTimeline(projectId, { ...timeline, layers: updatedLayers })
      setSelectedVideoClip(null)
      if (onVideoClipSelect) onVideoClipSelect(null)
    } else {
      console.log('[handleDeleteClip] No clip selected')
    }
  }, [selectedClip, selectedVideoClip, timeline, projectId, updateTimeline, onClipSelect, onVideoClipSelect])

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

    // Get group clips initial positions (if in a group)
    let groupVideoClips: GroupClipInitialPosition[] | undefined
    let groupAudioClips: GroupClipInitialPosition[] | undefined
    if (clip.group_id) {
      groupVideoClips = []
      groupAudioClips = []
      // Collect all video clips in the group
      for (const l of timeline.layers) {
        for (const c of l.clips) {
          if (c.group_id === clip.group_id) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      // Collect all audio clips in the group (except the dragged clip)
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    setDragState({
      type,
      trackId,
      clipId,
      startX: e.clientX,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      assetDurationMs,
      linkedVideoClipId: clip.linked_video_clip_id,
      linkedVideoLayerId: clip.linked_video_layer_id,
      linkedVideoInitialStartMs,
      groupId: clip.group_id,
      groupVideoClips,
      groupAudioClips,
    })

    // Select the clip
    handleClipSelect(trackId, clipId)
  }, [timeline, assets, handleClipSelect])

  const handleClipDragMove = useCallback((e: MouseEvent) => {
    if (!dragState) return

    const deltaX = e.clientX - dragState.startX
    const deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    // Build set of group audio clip IDs for quick lookup
    const groupAudioClipIds = new Set(dragState.groupAudioClips?.map(c => c.clipId) || [])

    let updatedTracks = timeline.audio_tracks.map((t) => {
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
              return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs }
            } else if (dragState.type === 'trim-end') {
              const maxDuration = dragState.assetDurationMs - dragState.initialInPointMs
              const newDurationMs = Math.min(Math.max(100, dragState.initialDurationMs + deltaMs), maxDuration)
              return { ...clip, duration_ms: newDurationMs }
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

    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks, layers: updatedLayers })
  }, [dragState, pixelsPerSecond, timeline, projectId, updateTimeline])

  const handleClipDragEnd = useCallback(() => {
    setDragState(null)
  }, [])

  // Add global mouse listeners for clip drag
  useEffect(() => {
    if (dragState) {
      window.addEventListener('mousemove', handleClipDragMove)
      window.addEventListener('mouseup', handleClipDragEnd)
      return () => {
        window.removeEventListener('mousemove', handleClipDragMove)
        window.removeEventListener('mouseup', handleClipDragEnd)
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
    const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    const assetDurationMs = asset?.duration_ms || clip.in_point_ms + clip.duration_ms

    // Find linked audio clip's initial position (legacy support)
    let linkedAudioInitialStartMs: number | undefined
    if (clip.linked_audio_clip_id && clip.linked_audio_track_id) {
      const linkedTrack = timeline.audio_tracks.find(t => t.id === clip.linked_audio_track_id)
      const linkedClip = linkedTrack?.clips.find(c => c.id === clip.linked_audio_clip_id)
      linkedAudioInitialStartMs = linkedClip?.start_ms
    }

    // Get group clips initial positions (if in a group)
    let groupVideoClips: GroupClipInitialPosition[] | undefined
    let groupAudioClips: GroupClipInitialPosition[] | undefined
    if (clip.group_id) {
      groupVideoClips = []
      groupAudioClips = []
      // Collect all video clips in the group (except the dragged clip)
      for (const l of timeline.layers) {
        for (const c of l.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      // Collect all audio clips in the group
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    setVideoDragState({
      type,
      layerId,
      clipId,
      startX: e.clientX,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      assetDurationMs,
      linkedAudioClipId: clip.linked_audio_clip_id,
      linkedAudioTrackId: clip.linked_audio_track_id,
      linkedAudioInitialStartMs,
      groupId: clip.group_id,
      groupVideoClips,
      groupAudioClips,
    })

    // Select the clip
    handleVideoClipSelect(layerId, clipId)
  }, [timeline, assets, handleVideoClipSelect])

  const handleVideoClipDragMove = useCallback((e: MouseEvent) => {
    if (!videoDragState) return

    const deltaX = e.clientX - videoDragState.startX
    const deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    // Build set of group video clip IDs for quick lookup
    const groupVideoClipIds = new Set(videoDragState.groupVideoClips?.map(c => c.clipId) || [])

    let updatedLayers = timeline.layers.map((layer) => {
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
              const minTrim = -videoDragState.initialInPointMs
              const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
              const newStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
              const newInPointMs = videoDragState.initialInPointMs + trimAmount
              const newDurationMs = videoDragState.initialDurationMs - trimAmount
              return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs }
            } else if (videoDragState.type === 'trim-end') {
              const maxDuration = videoDragState.assetDurationMs - videoDragState.initialInPointMs
              const newDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
              return { ...clip, duration_ms: newDurationMs }
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

    updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks })
  }, [videoDragState, pixelsPerSecond, timeline, projectId, updateTimeline])

  const handleVideoClipDragEnd = useCallback(() => {
    setVideoDragState(null)
  }, [])

  // Add global mouse listeners for video clip drag
  useEffect(() => {
    if (videoDragState) {
      window.addEventListener('mousemove', handleVideoClipDragMove)
      window.addEventListener('mouseup', handleVideoClipDragEnd)
      return () => {
        window.removeEventListener('mousemove', handleVideoClipDragMove)
        window.removeEventListener('mouseup', handleVideoClipDragEnd)
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
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedClip, selectedVideoClip, handleDeleteClip, isLinkingMode])

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
        </div>

        {/* Zoom Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setZoom(Math.max(0.25, zoom - 0.25))}
            className="text-gray-400 hover:text-white"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
            </svg>
          </button>
          <span className="text-gray-400 text-sm w-12 text-center">{Math.round(zoom * 100)}%</span>
          <button
            onClick={() => setZoom(Math.min(4, zoom + 0.25))}
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

          {/* Audio Tracks */}
          {timeline.audio_tracks.map((track) => (
            <div
              key={track.id}
              className="h-16 px-2 py-1 border-b border-gray-700 flex flex-col justify-center group"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm text-white truncate flex-1">{track.name}</span>
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

          {/* Video Layers */}
          {timeline.layers.map((layer, layerIndex) => {
            const isLayerSelected = selectedLayerId === layer.id
            const canMoveUp = layerIndex > 0
            const canMoveDown = layerIndex < timeline.layers.length - 1
            const isDragging = draggingLayerId === layer.id
            const isDropTarget = dropTargetIndex === layerIndex
            return (
            <div
              key={layer.id}
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
              <div className="flex-1 flex items-center justify-between px-2 py-1">
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
              <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
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
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
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
            )
          })}
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
              {/* Adaptive grid based on zoom level */}
              {(() => {
                // Determine interval based on zoom level for readable grid
                // zoom 0.25 = 25px/s, zoom 1 = 100px/s, zoom 4 = 400px/s
                let majorIntervalSec: number
                let minorIntervalSec: number
                let showMinor = true

                if (zoom < 0.35) {
                  // Very zoomed out: 30-second major marks, 10-second minor
                  majorIntervalSec = 30
                  minorIntervalSec = 10
                } else if (zoom < 0.6) {
                  // Zoomed out: 10-second major marks, 5-second minor
                  majorIntervalSec = 10
                  minorIntervalSec = 5
                } else if (zoom < 1.2) {
                  // Normal: 5-second major marks, 1-second minor
                  majorIntervalSec = 5
                  minorIntervalSec = 1
                } else if (zoom < 2.5) {
                  // Zoomed in: 1-second major marks, no minor
                  majorIntervalSec = 1
                  minorIntervalSec = 0.5
                  showMinor = false
                } else {
                  // Very zoomed in: 1-second major marks, 0.5-second minor
                  majorIntervalSec = 1
                  minorIntervalSec = 0.5
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

            {/* Audio Tracks */}
            {timeline.audio_tracks.map((track) => (
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
                  const isDragging = dragState?.clipId === clip.id
                  const clipColor = track.type === 'narration' ? '#22c55e' : track.type === 'bgm' ? '#3b82f6' : '#f59e0b'
                  const clipWidth = Math.max((clip.duration_ms / 1000) * pixelsPerSecond, 40)
                  const isLinkTarget = isLinkingMode && !clip.linked_video_clip_id // Can be linked if not already linked
                  const audioClipGroup = getClipGroup(clip.group_id)
                  return (
                    <div
                      key={clip.id}
                      className={`absolute top-1 bottom-1 rounded transition-all select-none ${
                        isSelected ? 'ring-2 ring-white z-10' : ''
                      } ${isDragging ? 'opacity-80' : ''} ${isLinkTarget ? 'ring-2 ring-blue-400 ring-opacity-75 animate-pulse' : ''}`}
                      style={{
                        left: (clip.start_ms / 1000) * pixelsPerSecond,
                        width: clipWidth,
                        backgroundColor: `${clipColor}33`,
                        borderWidth: 1,
                        borderColor: clipColor,
                        cursor: isLinkTarget ? 'pointer' : dragState?.type === 'move' ? 'grabbing' : 'grab',
                      }}
                      onClick={(e) => {
                        if (isLinkTarget) {
                          e.stopPropagation()
                          handleClipSelect(track.id, clip.id)
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
                      {/* Trim handle - left */}
                      <div
                        className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize hover:bg-white/30 z-10"
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          handleClipDragStart(e, track.id, clip.id, 'trim-start')
                        }}
                      />
                      {/* Trim handle - right */}
                      <div
                        className="absolute right-0 top-0 bottom-0 w-2 cursor-ew-resize hover:bg-white/30 z-10"
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          handleClipDragStart(e, track.id, clip.id, 'trim-end')
                        }}
                      />
                      {/* Fade in indicator */}
                      {clip.fade_in_ms > 0 && (
                        <div
                          className="absolute top-0 left-0 h-full bg-gradient-to-r from-black/50 to-transparent pointer-events-none"
                          style={{ width: (clip.fade_in_ms / 1000) * pixelsPerSecond }}
                        />
                      )}
                      {/* Fade out indicator */}
                      {clip.fade_out_ms > 0 && (
                        <div
                          className="absolute top-0 right-0 h-full bg-gradient-to-l from-black/50 to-transparent pointer-events-none"
                          style={{ width: (clip.fade_out_ms / 1000) * pixelsPerSecond }}
                        />
                      )}
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

            {/* Video Layers */}
            {timeline.layers.map((layer, layerIndex) => {
              // Cycle through colors based on layer index
              const colorPalette = [
                'bg-purple-600/80 border-purple-500',
                'bg-blue-600/80 border-blue-500',
                'bg-teal-600/80 border-teal-500',
                'bg-pink-600/80 border-pink-500',
                'bg-yellow-600/80 border-yellow-500',
              ]
              const clipColorClass = colorPalette[layerIndex % colorPalette.length]

              const isLayerSelected = selectedLayerId === layer.id
              return (
                <div
                  key={layer.id}
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
                    const isDragging = videoDragState?.clipId === clip.id
                    const clipWidth = Math.max((clip.duration_ms / 1000) * pixelsPerSecond, 40)
                    const clipGroup = getClipGroup(clip.group_id)

                    return (
                      <div
                        key={clip.id}
                        className={`absolute top-1 bottom-1 rounded transition-all select-none ${clipColorClass} ${
                          isSelected ? 'ring-2 ring-white z-10' : ''
                        } ${isDragging ? 'opacity-80' : ''} ${layer.locked ? 'cursor-not-allowed' : ''}`}
                        style={{
                          left: (clip.start_ms / 1000) * pixelsPerSecond,
                          width: clipWidth,
                          borderWidth: 1,
                          cursor: layer.locked ? 'not-allowed' : videoDragState?.type === 'move' ? 'grabbing' : 'grab',
                        }}
                        onClick={(e) => {
                          e.stopPropagation()
                          if (!layer.locked) handleVideoClipSelect(layer.id, clip.id)
                        }}
                        onMouseDown={(e) => !layer.locked && handleVideoClipDragStart(e, layer.id, clip.id, 'move')}
                      >
                        {/* Group indicator - colored bar at top */}
                        {clipGroup && (
                          <div
                            className="absolute top-0 left-0 right-0 h-1 rounded-t"
                            style={{ backgroundColor: clipGroup.color }}
                            title={clipGroup.name}
                          />
                        )}
                        {/* Trim handle - left */}
                        {!layer.locked && (
                          <div
                            className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize hover:bg-white/30 z-10"
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleVideoClipDragStart(e, layer.id, clip.id, 'trim-start')
                            }}
                          />
                        )}
                        {/* Trim handle - right */}
                        {!layer.locked && (
                          <div
                            className="absolute right-0 top-0 bottom-0 w-2 cursor-ew-resize hover:bg-white/30 z-10"
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleVideoClipDragStart(e, layer.id, clip.id, 'trim-end')
                            }}
                          />
                        )}
                        <span className="text-xs text-white px-2 truncate block leading-[2.5rem] pointer-events-none">
                          {clip.asset_id ? getAssetName(clip.asset_id) : clip.shape ? (
                            <span className="flex items-center gap-1">
                              {clip.shape.type === 'rectangle' && (
                                <>
                                  <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <rect x="3" y="3" width="18" height="18" rx="2" strokeWidth={2} />
                                  </svg>
                                  四角形
                                </>
                              )}
                              {clip.shape.type === 'circle' && (
                                <>
                                  <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <circle cx="12" cy="12" r="9" strokeWidth={2} />
                                  </svg>
                                  円
                                </>
                              )}
                              {clip.shape.type === 'line' && (
                                <>
                                  <svg className="w-3 h-3 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <line x1="4" y1="20" x2="20" y2="4" strokeWidth={2} />
                                  </svg>
                                  線
                                </>
                              )}
                            </span>
                          ) : 'Clip'}
                        </span>
                        {/* Keyframe markers */}
                        {clip.keyframes && clip.keyframes.length > 0 && (
                          <div className="absolute bottom-0 left-0 right-0 h-3 pointer-events-none">
                            {clip.keyframes.map((kf, idx) => {
                              const kfPosition = (kf.time_ms / clip.duration_ms) * 100
                              return (
                                <div
                                  key={idx}
                                  className="absolute bottom-0.5 w-2 h-2 bg-yellow-400 border border-yellow-600"
                                  style={{
                                    left: `calc(${kfPosition}% - 4px)`,
                                    transform: 'rotate(45deg)',
                                  }}
                                  title={`キーフレーム: ${(kf.time_ms / 1000).toFixed(1)}s`}
                                />
                              )
                            })}
                          </div>
                        )}
                        {/* Link indicator */}
                        {clip.linked_audio_clip_id && (
                          <div className="absolute top-0.5 right-1 pointer-events-none" title="音声とリンク済み">
                            <svg className="w-3 h-3 text-white/70" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                            </svg>
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {/* Drop indicator */}
                  {dragOverLayer === layer.id && !layer.locked && (
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                      <span className="text-purple-400 text-sm">ここにドロップ</span>
                    </div>
                  )}
                </div>
              )
            })}

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
            {selectedVideoClipData.asset_id ? getAssetName(selectedVideoClipData.asset_id) : selectedVideoClipData.shape ? (
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

          {/* Legacy Link/Unlink buttons */}
          {selectedVideoClipData.linked_audio_clip_id ? (
            <button
              onClick={() => handleUnlinkVideoClip(selectedVideoClip.layerId, selectedVideoClip.clipId)}
              className="px-2 py-1 text-xs text-yellow-400 hover:text-yellow-300 hover:bg-yellow-900/30 rounded flex items-center gap-1"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              リンク解除
            </button>
          ) : !selectedVideoClipData.group_id && (
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
