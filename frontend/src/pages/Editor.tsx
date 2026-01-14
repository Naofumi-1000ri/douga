import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useProjectStore, type Shape } from '@/store/projectStore'
import Timeline, { type SelectedClipInfo, type SelectedVideoClipInfo } from '@/components/editor/Timeline'
import AssetLibrary from '@/components/assets/AssetLibrary'
import { assetsApi, type Asset } from '@/api/assets'
import { projectsApi, type RenderJob } from '@/api/projects'
import { addKeyframe, removeKeyframe, hasKeyframeAt, getInterpolatedTransform } from '@/utils/keyframes'

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

export default function Editor() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const { currentProject, loading, error, fetchProject, updateTimeline, undo, redo, canUndo, canRedo } = useProjectStore()
  const [assets, setAssets] = useState<Asset[]>([])
  const [exporting, setExporting] = useState(false)
  const [renderJob, setRenderJob] = useState<RenderJob | null>(null)
  const [showRenderModal, setShowRenderModal] = useState(false)
  const [showSettingsModal, setShowSettingsModal] = useState(false)
  const renderPollRef = useRef<number | null>(null)
  const [selectedClip, setSelectedClip] = useState<SelectedClipInfo | null>(null)
  const [selectedVideoClip, setSelectedVideoClip] = useState<SelectedVideoClipInfo | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [preview, setPreview] = useState<PreviewState>({ asset: null, url: null, loading: false })
  const [assetUrlCache, setAssetUrlCache] = useState<Map<string, string>>(new Map())
  const [previewHeight, setPreviewHeight] = useState(400) // Resizable preview height
  const [isResizing, setIsResizing] = useState(false)
  // Preview drag state with anchor-based resizing
  // 'resize' = uniform scale (for images/videos), corner/edge types for shape width/height
  const [previewDrag, setPreviewDrag] = useState<{
    type: 'move' | 'resize' | 'resize-tl' | 'resize-tr' | 'resize-bl' | 'resize-br' | 'resize-t' | 'resize-b' | 'resize-l' | 'resize-r'
    layerId: string
    clipId: string
    startX: number  // Mouse position at drag start (screen coords)
    startY: number
    initialX: number  // Shape center at drag start (logical coords)
    initialY: number
    initialScale: number
    initialShapeWidth?: number
    initialShapeHeight?: number
    // Anchor-based resizing: fixed point that doesn't move
    anchorX?: number  // Anchor position (logical coords)
    anchorY?: number
    handleOffsetX?: number  // Offset from mouse to handle (screen coords)
    handleOffsetY?: number
  } | null>(null)
  // Current transform during drag (local state, not saved until drag ends)
  const [dragTransform, setDragTransform] = useState<{
    x: number
    y: number
    scale: number
    shapeWidth?: number
    shapeHeight?: number
  } | null>(null)
  const previewContainerRef = useRef<HTMLDivElement>(null)
  const audioRefs = useRef<Map<string, HTMLAudioElement>>(new Map())
  // Store clip timing info for each audio element to know when to stop playback and apply fades
  const audioClipTimingRefs = useRef<Map<string, {
    start_ms: number,
    end_ms: number,
    in_point_ms: number,
    fade_in_ms: number,
    fade_out_ms: number,
    base_volume: number
  }>>(new Map())
  const videoRef = useRef<HTMLVideoElement>(null)
  const playbackTimerRef = useRef<number | null>(null)
  const startTimeRef = useRef<number>(0)
  const isPlayingRef = useRef(false)
  const resizeStartY = useRef(0)
  const resizeStartHeight = useRef(0)

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

  // Preload all video/image asset URLs for instant preview switching
  useEffect(() => {
    if (!projectId || assets.length === 0) return

    const preloadUrls = async () => {
      const videoImageAssets = assets.filter(a => a.type === 'video' || a.type === 'image')
      const newCache = new Map<string, string>()

      await Promise.all(
        videoImageAssets.map(async (asset) => {
          // Skip if already cached
          if (assetUrlCache.has(asset.id)) {
            newCache.set(asset.id, assetUrlCache.get(asset.id)!)
            return
          }
          try {
            const { url } = await assetsApi.getSignedUrl(projectId, asset.id)
            newCache.set(asset.id, url)
          } catch (error) {
            console.error('Failed to preload asset URL:', asset.id, error)
          }
        })
      )

      setAssetUrlCache(newCache)
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

  const handleExportAudio = async () => {
    if (!currentProject || exporting) return
    setExporting(true)
    try {
      const result = await projectsApi.exportAudio(currentProject.id)
      // Open download URL in new tab
      window.open(result.download_url, '_blank')
    } catch (error) {
      console.error('Export failed:', error)
      alert('音声エクスポートに失敗しました。タイムラインに音声クリップがあることを確認してください。')
    } finally {
      setExporting(false)
    }
  }

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

  // Video render handlers
  const pollRenderStatus = useCallback(async () => {
    if (!currentProject) return

    try {
      const status = await projectsApi.getRenderStatus(currentProject.id)
      if (status) {
        setRenderJob(status)

        // Continue polling if still processing
        if (status.status === 'queued' || status.status === 'processing') {
          renderPollRef.current = window.setTimeout(pollRenderStatus, 2000)
        }
      }
    } catch (error) {
      console.error('Failed to poll render status:', error)
    }
  }, [currentProject])

  const handleStartRender = async () => {
    if (!currentProject) return

    try {
      const job = await projectsApi.startRender(currentProject.id)
      setRenderJob(job)
      setShowRenderModal(true)

      // Start polling for status
      renderPollRef.current = window.setTimeout(pollRenderStatus, 2000)
    } catch (error) {
      console.error('Failed to start render:', error)
      alert('レンダリングの開始に失敗しました。')
    }
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

  // Helper to calculate volume with fade applied
  const calculateFadeVolume = useCallback((
    timeMs: number,
    timing: { start_ms: number; end_ms: number; fade_in_ms: number; fade_out_ms: number; base_volume: number }
  ) => {
    const positionInClip = timeMs - timing.start_ms
    const clipDuration = timing.end_ms - timing.start_ms
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
    // Pause video preview
    if (videoRef.current) {
      videoRef.current.pause()
    }
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

    // Load audio clips asynchronously (non-blocking)
    // The updatePlayhead callback will start each audio when it comes into range
    const loadAudioClips = async () => {
      for (const track of currentProject.timeline_data.audio_tracks) {
        if (track.muted) continue

        for (const clip of track.clips) {
          // Skip if playback was stopped
          if (!isPlayingRef.current) return

          try {
            const { url } = await assetsApi.getSignedUrl(projectId, clip.asset_id)
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
              base_volume: baseVolume
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

    // Helper to find clip at a given time (TOP layer first)
    const findClipAtTime = (timeMs: number) => {
      if (!currentProject) return null
      const layers = currentProject.timeline_data.layers
      for (let i = layers.length - 1; i >= 0; i--) {
        const layer = layers[i]
        if (layer.visible === false) continue
        for (const clip of layer.clips) {
          if (timeMs >= clip.start_ms && timeMs < clip.start_ms + clip.duration_ms) {
            return clip
          }
        }
      }
      return null
    }

    // Start video playback if there's a video clip at current time
    if (videoRef.current) {
      const clip = findClipAtTime(currentTime)

      if (clip && currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
        // Video time = in_point + (timeline position - clip start)
        const videoTimeMs = clip.in_point_ms + (currentTime - clip.start_ms)
        videoRef.current.currentTime = videoTimeMs / 1000
        videoRef.current.play().catch(console.error)
      }
    }

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

      // Sync video playback with timeline - find clip at current elapsed time
      if (videoRef.current) {
        const clip = findClipAtTime(elapsed)

        if (clip && elapsed >= clip.start_ms && elapsed < clip.start_ms + clip.duration_ms) {
          // Video should be playing
          if (videoRef.current.paused) {
            // Video time = in_point + (timeline position - clip start)
            const videoTimeMs = clip.in_point_ms + (elapsed - clip.start_ms)
            videoRef.current.currentTime = videoTimeMs / 1000
            videoRef.current.play().catch(console.error)
          }
        } else {
          // Video should be paused (outside clip range)
          if (!videoRef.current.paused) {
            videoRef.current.pause()
          }
        }
      }

      if (elapsed < (currentProject?.duration_ms || 0)) {
        playbackTimerRef.current = requestAnimationFrame(updatePlayhead)
      } else {
        stopPlayback()
        setCurrentTime(0)
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
    }>
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
            transform: updates.transform ? { ...clip.transform, ...updates.transform } : clip.transform,
            effects: updates.effects ? {
              ...clip.effects,
              opacity: updates.effects.opacity ?? clip.effects.opacity,
              fade_in_ms: updates.effects.fade_in_ms ?? clip.effects.fade_in_ms,
              fade_out_ms: updates.effects.fade_out_ms ?? clip.effects.fade_out_ms,
              chroma_key: updates.effects.chroma_key ? {
                enabled: updates.effects.chroma_key.enabled ?? clip.effects.chroma_key?.enabled ?? false,
                color: updates.effects.chroma_key.color ?? clip.effects.chroma_key?.color ?? '#00ff00',
                similarity: updates.effects.chroma_key.similarity ?? clip.effects.chroma_key?.similarity ?? 0.4,
                blend: updates.effects.chroma_key.blend ?? clip.effects.chroma_key?.blend ?? 0.1,
              } : clip.effects.chroma_key,
            } : clip.effects,
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
        transform: clip.transform,
        effects: clip.effects,
        fadeInMs: clip.effects.fade_in_ms ?? 0,
        fadeOutMs: clip.effects.fade_out_ms ?? 0,
      })
    }
  }, [selectedVideoClip, currentProject, projectId, updateTimeline])

  // Fit or Fill video/image to canvas
  const handleFitOrFill = useCallback((mode: 'fit' | 'fill') => {
    if (!selectedVideoClip || !currentProject) return

    // Find the asset to get original dimensions
    const asset = assets.find(a => a.id === selectedVideoClip.assetId)
    if (!asset || !asset.width || !asset.height) return

    const canvasWidth = currentProject.width || 1920
    const canvasHeight = currentProject.height || 1080

    // Calculate scale to fit or fill
    const scaleX = canvasWidth / asset.width
    const scaleY = canvasHeight / asset.height
    const newScale = mode === 'fit' ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY)

    // Center the clip and apply scale
    handleUpdateVideoClip({
      transform: {
        x: 0,
        y: 0,
        scale: newScale,
        rotation: 0,
      }
    })
  }, [selectedVideoClip, currentProject, assets, handleUpdateVideoClip])

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

  // Simplified preview drag handlers
  const handlePreviewDragStart = useCallback((
    e: React.MouseEvent,
    type: 'move' | 'resize' | 'resize-tl' | 'resize-tr' | 'resize-bl' | 'resize-br' | 'resize-t' | 'resize-b' | 'resize-l' | 'resize-r',
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

    // Get current transform
    const timeInClipMs = currentTime - clip.start_ms
    const currentTransform = clip.keyframes && clip.keyframes.length > 0
      ? getInterpolatedTransform(clip, timeInClipMs)
      : { x: clip.transform.x, y: clip.transform.y, scale: clip.transform.scale }

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
    const w = clip.shape?.width || 100
    const h = clip.shape?.height || 100
    const scale = currentTransform.scale

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
      initialShapeWidth: clip.shape?.width,
      initialShapeHeight: clip.shape?.height,
      anchorX,
      anchorY,
    })

    // Initialize dragTransform with current values
    setDragTransform({
      x: currentTransform.x,
      y: currentTransform.y,
      scale: currentTransform.scale,
      shapeWidth: clip.shape?.width,
      shapeHeight: clip.shape?.height,
    })

    // Set cursor based on resize direction
    document.body.classList.add('dragging-preview')
    const cursorMap: Record<string, string> = {
      'move': 'grabbing',
      'resize': 'nwse-resize',
      'resize-tl': 'nwse-resize',
      'resize-br': 'nwse-resize',
      'resize-tr': 'nesw-resize',
      'resize-bl': 'nesw-resize',
      'resize-t': 'ns-resize',
      'resize-b': 'ns-resize',
      'resize-l': 'ew-resize',
      'resize-r': 'ew-resize',
    }
    document.body.dataset.dragCursor = cursorMap[type] || 'default'

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

    const deltaX = e.clientX - previewDrag.startX
    const deltaY = e.clientY - previewDrag.startY

    // Calculate preview scale
    const containerHeight = previewHeight - 80
    const previewScale = containerHeight / currentProject.height

    // Convert screen delta to logical pixels
    const logicalDeltaX = deltaX / previewScale
    const logicalDeltaY = deltaY / previewScale

    let newX = previewDrag.initialX
    let newY = previewDrag.initialY
    let newScale = previewDrag.initialScale
    let newShapeWidth = previewDrag.initialShapeWidth
    let newShapeHeight = previewDrag.initialShapeHeight

    const { type } = previewDrag
    const initW = previewDrag.initialShapeWidth || 100
    const initH = previewDrag.initialShapeHeight || 100
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
      const scaleFactor = 1 + (deltaX + deltaY) / 200
      newScale = Math.max(0.1, Math.min(5, previewDrag.initialScale * scaleFactor))
    } else if (type === 'resize-br') {
      // Bottom-right handle: anchor at top-left
      newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
      newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
      // Center = anchor + (size/2 * scale), using scaled dimensions
      newX = anchorX + (newShapeWidth / 2) * scale
      newY = anchorY + (newShapeHeight / 2) * scale
    } else if (type === 'resize-tl') {
      // Top-left handle: anchor at bottom-right
      newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
      newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
      newX = anchorX - (newShapeWidth / 2) * scale
      newY = anchorY - (newShapeHeight / 2) * scale
    } else if (type === 'resize-tr') {
      // Top-right handle: anchor at bottom-left
      newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
      newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
      newX = anchorX + (newShapeWidth / 2) * scale
      newY = anchorY - (newShapeHeight / 2) * scale
    } else if (type === 'resize-bl') {
      // Bottom-left handle: anchor at top-right
      newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
      newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
      newX = anchorX - (newShapeWidth / 2) * scale
      newY = anchorY + (newShapeHeight / 2) * scale
    } else if (type === 'resize-r') {
      // Right edge: anchor at left edge center
      newShapeWidth = Math.max(10, initW + logicalDeltaX / scale)
      newX = anchorX + (newShapeWidth / 2) * scale
    } else if (type === 'resize-l') {
      // Left edge: anchor at right edge center
      newShapeWidth = Math.max(10, initW - logicalDeltaX / scale)
      newX = anchorX - (newShapeWidth / 2) * scale
    } else if (type === 'resize-b') {
      // Bottom edge: anchor at top edge center
      newShapeHeight = Math.max(10, initH + logicalDeltaY / scale)
      newY = anchorY + (newShapeHeight / 2) * scale
    } else if (type === 'resize-t') {
      // Top edge: anchor at bottom edge center
      newShapeHeight = Math.max(10, initH - logicalDeltaY / scale)
      newY = anchorY - (newShapeHeight / 2) * scale
    }

    // Update local drag transform only (no network call)
    setDragTransform({
      x: newX,
      y: newY,
      scale: newScale,
      shapeWidth: newShapeWidth,
      shapeHeight: newShapeHeight,
    })
  }, [previewDrag, currentProject, previewHeight])

  // Save changes on drag end
  const handlePreviewDragEnd = useCallback(() => {
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

            return {
              ...c,
              transform: {
                ...c.transform,
                x: dragTransform.x,
                y: dragTransform.y,
                scale: dragTransform.scale,
              },
              shape: updatedShape,
            }
          }),
        }
      })

      updateTimeline(projectId, { ...currentProject.timeline_data, layers: updatedLayers })
    }

    setPreviewDrag(null)
    setDragTransform(null)
    document.body.classList.remove('dragging-preview')
    delete document.body.dataset.dragCursor
  }, [previewDrag, dragTransform, currentProject, projectId, updateTimeline])

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

  // Sync video frame with timeline when not playing
  // Compute clip position directly for better accuracy
  useEffect(() => {
    if (isPlaying || !videoRef.current || !currentProject) return

    // Find clip at current time directly (not using derived state)
    // Iterate from TOP to BOTTOM (reverse order) to find topmost layer
    let foundClip: { start_ms: number; in_point_ms: number; duration_ms: number; asset_id: string | null } | null = null
    const layers = currentProject.timeline_data.layers
    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i]
      if (layer.visible === false) continue
      for (const clip of layer.clips) {
        if (currentTime >= clip.start_ms && currentTime < clip.start_ms + clip.duration_ms) {
          foundClip = clip
          break
        }
      }
      if (foundClip) break
    }

    if (!foundClip) return

    // Video time = in_point + (timeline position - clip start)
    const videoTimeMs = foundClip.in_point_ms + (currentTime - foundClip.start_ms)
    const targetTime = videoTimeMs / 1000

    // Only seek if difference is significant (avoid micro-seeks)
    if (Math.abs(videoRef.current.currentTime - targetTime) > 0.05) {
      videoRef.current.currentTime = targetTime
    }
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
      const newHeight = Math.max(200, Math.min(800, resizeStartHeight.current + deltaY))
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

  // Keyboard shortcuts for undo/redo
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in an input field
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
        if (e.shiftKey) {
          // Redo: Ctrl/Cmd + Shift + Z
          e.preventDefault()
          if (projectId && canRedo()) {
            redo(projectId)
          }
        } else {
          // Undo: Ctrl/Cmd + Z
          e.preventDefault()
          if (projectId && canUndo()) {
            undo(projectId)
          }
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'y') {
        // Redo: Ctrl/Cmd + Y (alternative)
        e.preventDefault()
        if (projectId && canRedo()) {
          redo(projectId)
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [projectId, undo, redo, canUndo, canRedo])

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
    <div className="min-h-screen bg-gray-900 flex flex-col">
      {/* Header */}
      <header className="h-14 bg-gray-800 border-b border-gray-700 flex items-center px-4 flex-shrink-0 sticky top-0 z-50">
        <button
          onClick={() => navigate('/')}
          className="text-gray-400 hover:text-white mr-4"
        >
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-white font-medium">{currentProject.name}</h1>
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
        {/* Undo/Redo buttons */}
        <div className="flex items-center gap-1 ml-4">
          <button
            onClick={() => projectId && undo(projectId)}
            disabled={!canUndo()}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title="元に戻す (Ctrl+Z)"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
            </svg>
          </button>
          <button
            onClick={() => projectId && redo(projectId)}
            disabled={!canRedo()}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title="やり直す (Ctrl+Shift+Z)"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 10h-10a8 8 0 00-8 8v2M21 10l-6 6m6-6l-6-6" />
            </svg>
          </button>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <span className="text-gray-400 text-sm">
            {Math.floor(currentProject.duration_ms / 60000)}:
            {Math.floor((currentProject.duration_ms % 60000) / 1000).toString().padStart(2, '0')}
          </span>
          <button
            onClick={handleExportAudio}
            disabled={exporting}
            className="px-4 py-1.5 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {exporting ? (
              <>
                <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>
                <span>処理中...</span>
              </>
            ) : (
              '音声エクスポート'
            )}
          </button>
          <button
            onClick={handleStartRender}
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

      {/* Render Progress Modal */}
      {showRenderModal && renderJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">動画エクスポート</h3>
              {(renderJob.status === 'completed' || renderJob.status === 'failed' || renderJob.status === 'cancelled') && (
                <button
                  onClick={() => setShowRenderModal(false)}
                  className="text-gray-400 hover:text-white"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>

            {/* Status */}
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                {renderJob.status === 'queued' && (
                  <>
                    <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                    <span className="text-yellow-400 text-sm">キュー待機中...</span>
                  </>
                )}
                {renderJob.status === 'processing' && (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-primary-500"></div>
                    <span className="text-primary-400 text-sm">レンダリング中...</span>
                  </>
                )}
                {renderJob.status === 'completed' && (
                  <>
                    <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    <span className="text-green-400 text-sm">完了</span>
                  </>
                )}
                {renderJob.status === 'failed' && (
                  <>
                    <svg className="w-5 h-5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                    <span className="text-red-400 text-sm">エラー</span>
                  </>
                )}
                {renderJob.status === 'cancelled' && (
                  <>
                    <svg className="w-5 h-5 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                    </svg>
                    <span className="text-gray-400 text-sm">キャンセル済み</span>
                  </>
                )}
              </div>

              {/* Current stage */}
              {renderJob.current_stage && (renderJob.status === 'queued' || renderJob.status === 'processing') && (
                <p className="text-gray-400 text-xs">{renderJob.current_stage}</p>
              )}

              {/* Error message */}
              {renderJob.status === 'failed' && renderJob.error_message && (
                <p className="text-red-400 text-xs mt-1">{renderJob.error_message}</p>
              )}
            </div>

            {/* Progress bar */}
            {(renderJob.status === 'queued' || renderJob.status === 'processing') && (
              <div className="mb-4">
                <div className="flex justify-between text-xs text-gray-400 mb-1">
                  <span>進行状況</span>
                  <span>{Math.round(renderJob.progress)}%</span>
                </div>
                <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary-500 transition-all duration-300"
                    style={{ width: `${renderJob.progress}%` }}
                  />
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2">
              {(renderJob.status === 'queued' || renderJob.status === 'processing') && (
                <button
                  onClick={handleCancelRender}
                  className="flex-1 px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
                >
                  キャンセル
                </button>
              )}
              {renderJob.status === 'completed' && (
                <>
                  <button
                    onClick={handleDownloadVideo}
                    className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors flex items-center justify-center gap-2"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    ダウンロード
                  </button>
                  <button
                    onClick={() => setShowRenderModal(false)}
                    className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
                  >
                    閉じる
                  </button>
                </>
              )}
              {(renderJob.status === 'failed' || renderJob.status === 'cancelled') && (
                <>
                  <button
                    onClick={handleStartRender}
                    className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors"
                  >
                    再試行
                  </button>
                  <button
                    onClick={() => setShowRenderModal(false)}
                    className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
                  >
                    閉じる
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Project Settings Modal */}
      {showSettingsModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
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

      {/* Main Editor Area */}
      <div className="flex-1 flex min-h-0">
        {/* Left Sidebar - Asset Library */}
        <aside className="w-72 bg-gray-800 border-r border-gray-700 flex flex-col overflow-y-auto" style={{ scrollbarGutter: 'stable' }}>
          <AssetLibrary projectId={currentProject.id} onPreviewAsset={handlePreviewAsset} onAssetsChange={fetchAssets} />
        </aside>

        {/* Center - Preview */}
        <main className="flex-1 flex flex-col min-h-0 min-w-0 overflow-hidden">
          {/* Preview Canvas - Resizable */}
          <div
            className="bg-gray-900 flex flex-col items-center justify-center p-4 flex-shrink-0"
            style={{ height: previewHeight }}
            onClick={(e) => {
              // Deselect when clicking on the outer gray area
              if (e.target === e.currentTarget) {
                setSelectedVideoClip(null)
                setSelectedClip(null)
              }
            }}
          >
            <div
              ref={previewContainerRef}
              className="bg-black rounded-lg overflow-hidden relative"
              style={{
                // Container maintains aspect ratio based on previewHeight only (stable sizing)
                width: (previewHeight - 80) * currentProject.width / currentProject.height,
                height: previewHeight - 80,
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
                // The container width is constrained by previewHeight
                const containerHeight = previewHeight - 80
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
                          ? { ...interpolated, x: dragTransform.x, y: dragTransform.y, scale: dragTransform.scale, opacity: fadeOpacity }
                          : { ...interpolated, opacity: fadeOpacity }
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
                        })
                      }
                    }
                  }
                }

                // Find the video clip in activeClips (for proper z-index)
                const videoClipIndex = activeClips.findIndex(c => c.assetType === 'video')
                const videoClip = videoClipIndex >= 0 ? activeClips[videoClipIndex] : null

                // Get active video URL
                const activeVideoUrl = videoClip?.assetId
                  ? assetUrlCache.get(videoClip.assetId)
                  : null

                // Get transform style for video
                const videoTransform = videoClip?.transform

                // Check if we need to show loading (video asset exists but not cached yet)
                const needsLoading = videoClip?.assetId && !assetUrlCache.has(videoClip.assetId)

                return (
                  <div
                    className="absolute inset-0 origin-top-left"
                    style={{
                      width: currentProject.width,
                      height: currentProject.height,
                      transform: `scale(${previewScale})`,
                    }}
                  >
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

                    {/* Render all active clips with transforms - interactive */}
                    {activeClips.map((activeClip, index) => {
                      const isSelected = selectedVideoClip?.clipId === activeClip.clip.id
                      const isDragging = previewDrag?.clipId === activeClip.clip.id

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
                              zIndex: index + 10,
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
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                    style={{ top: 0, left: 0, transform: 'translate(-50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                    style={{ top: 0, right: 0, transform: 'translate(50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-tr', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                    style={{ bottom: 0, left: 0, transform: 'translate(-50%, 50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-bl', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                    style={{ bottom: 0, right: 0, transform: 'translate(50%, 50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-br', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  {/* Edge handles - anchor at opposite edge */}
                                  <div
                                    className="absolute w-3 h-2 bg-green-500 border-2 border-white rounded-sm cursor-ns-resize"
                                    style={{ top: 0, left: '50%', transform: 'translate(-50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-t', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-2 bg-green-500 border-2 border-white rounded-sm cursor-ns-resize"
                                    style={{ bottom: 0, left: '50%', transform: 'translate(-50%, 50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-b', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-2 h-3 bg-green-500 border-2 border-white rounded-sm cursor-ew-resize"
                                    style={{ left: 0, top: '50%', transform: 'translate(-50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-l', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-2 h-3 bg-green-500 border-2 border-white rounded-sm cursor-ew-resize"
                                    style={{ right: 0, top: '50%', transform: 'translate(50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize-r', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                </>
                              )}
                            </div>
                          </div>
                        )
                      }

                      // Render asset-based clips (images)
                      if (!activeClip.assetId) return null
                      const url = assetUrlCache.get(activeClip.assetId)
                      if (!url) return null

                      if (activeClip.assetType === 'image') {
                        return (
                          <div
                            key={`${activeClip.clip.id}-${activeClip.assetId}`}
                            className="absolute"
                            style={{
                              top: '50%',
                              left: '50%',
                              transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
                              opacity: activeClip.transform.opacity,
                              zIndex: index + 10,
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
                              <img
                                src={url}
                                alt=""
                                className="block max-w-none pointer-events-none"
                                style={{
                                  maxHeight: '80vh',
                                }}
                                draggable={false}
                              />
                              {/* Resize handles when selected and not locked */}
                              {isSelected && !activeClip.locked && (
                                <>
                                  {/* Corner resize handles - positioned at image corners */}
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                    style={{ top: 0, left: 0, transform: 'translate(-50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                    style={{ top: 0, right: 0, transform: 'translate(50%, -50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                    style={{ bottom: 0, left: 0, transform: 'translate(-50%, 50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                  <div
                                    className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                    style={{ bottom: 0, right: 0, transform: 'translate(50%, 50%)' }}
                                    onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', activeClip.layerId, activeClip.clip.id) }}
                                  />
                                </>
                              )}
                            </div>
                          </div>
                        )
                      }
                      return null
                    })}

                    {/* Single video element - controlled via videoRef - with interactive wrapper */}
                    {activeVideoUrl && videoClip && (() => {
                      const videoIsSelected = selectedVideoClip?.clipId === videoClip.clip.id
                      const videoIsDragging = previewDrag?.clipId === videoClip.clip.id
                      const videoIsLocked = videoClip.locked
                      return (
                        <div
                          className="absolute"
                          style={{
                            top: '50%',
                            left: '50%',
                            transform: videoTransform
                              ? `translate(-50%, -50%) translate(${videoTransform.x}px, ${videoTransform.y}px) scale(${videoTransform.scale}) rotate(${videoTransform.rotation}deg)`
                              : 'translate(-50%, -50%)',
                            opacity: videoTransform?.opacity ?? 1,
                            transformOrigin: 'center center',
                            zIndex: videoClipIndex + 10,
                          }}
                        >
                          <div
                            className={`relative ${videoIsSelected && !videoIsLocked ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-transparent' : ''}`}
                            style={{
                              cursor: videoIsLocked ? 'not-allowed' : videoIsDragging ? 'grabbing' : 'grab',
                              userSelect: 'none',
                            }}
                            onMouseDown={(e) => handlePreviewDragStart(e, 'move', videoClip.layerId, videoClip.clip.id)}
                          >
                            <video
                              ref={videoRef}
                              src={activeVideoUrl}
                              className="block max-w-none pointer-events-none"
                              style={{
                                maxHeight: '80vh',
                              }}
                              muted
                              playsInline
                              preload="auto"
                            />
                            {/* Resize handles when selected and not locked */}
                            {videoIsSelected && !videoIsLocked && (
                              <>
                                <div
                                  className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                  style={{ top: 0, left: 0, transform: 'translate(-50%, -50%)' }}
                                  onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', videoClip.layerId, videoClip.clip.id) }}
                                />
                                <div
                                  className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                  style={{ top: 0, right: 0, transform: 'translate(50%, -50%)' }}
                                  onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', videoClip.layerId, videoClip.clip.id) }}
                                />
                                <div
                                  className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nesw-resize"
                                  style={{ bottom: 0, left: 0, transform: 'translate(-50%, 50%)' }}
                                  onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', videoClip.layerId, videoClip.clip.id) }}
                                />
                                <div
                                  className="absolute w-3 h-3 bg-primary-500 border-2 border-white rounded-sm cursor-nwse-resize"
                                  style={{ bottom: 0, right: 0, transform: 'translate(50%, 50%)' }}
                                  onMouseDown={(e) => { e.stopPropagation(); handlePreviewDragStart(e, 'resize', videoClip.layerId, videoClip.clip.id) }}
                                />
                              </>
                            )}
                          </div>
                        </div>
                      )
                    })()}

                    {/* Loading indicator for video (non-blocking - video will appear when ready) */}
                    {needsLoading && (
                      <div
                        className="absolute flex items-center justify-center pointer-events-none"
                        style={{
                          top: '50%',
                          left: '50%',
                          transform: 'translate(-50%, -50%)',
                          zIndex: videoClipIndex >= 0 ? videoClipIndex + 10 : 1,
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

              {/* Close preview button */}
              {preview.asset && (
                <button
                  onClick={() => setPreview({ asset: null, url: null, loading: false })}
                  className="absolute top-2 right-2 p-1 bg-black/50 hover:bg-black/70 rounded-full text-white transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>

            {/* Playback Controls */}
            <div className="mt-4 flex items-center gap-4">
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
            style={{ zIndex: 100 }}
            onMouseDown={handleResizeStart}
          >
            <div className="w-12 h-1 bg-gray-500 rounded"></div>
          </div>

          {/* Timeline - scrollable */}
          <div className="flex-1 border-t border-gray-700 bg-gray-800 overflow-y-auto" style={{ scrollbarGutter: 'stable' }}>
            <Timeline
              timeline={currentProject.timeline_data}
              projectId={currentProject.id}
              assets={assets}
              currentTimeMs={currentTime}
              isPlaying={isPlaying}
              onClipSelect={setSelectedClip}
              onVideoClipSelect={setSelectedVideoClip}
              onSeek={handleSeek}
            />
          </div>
        </main>

        {/* Right Sidebar - Properties */}
        <aside className="w-72 bg-gray-800 border-l border-gray-700 p-4 overflow-y-auto" style={{ scrollbarGutter: 'stable' }}>
          <h2 className="text-white font-medium mb-4">プロパティ</h2>
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

              {/* Duration */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">長さ</label>
                <p className="text-white text-sm">
                  {Math.floor(selectedVideoClip.durationMs / 60000)}:
                  {Math.floor((selectedVideoClip.durationMs % 60000) / 1000).toString().padStart(2, '0')}
                  .{Math.floor((selectedVideoClip.durationMs % 1000) / 10).toString().padStart(2, '0')}
                </p>
              </div>

              {/* Start Time */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">開始位置</label>
                <p className="text-white text-sm">
                  {Math.floor(selectedVideoClip.startMs / 60000)}:
                  {Math.floor((selectedVideoClip.startMs % 60000) / 1000).toString().padStart(2, '0')}
                  .{Math.floor((selectedVideoClip.startMs % 1000) / 10).toString().padStart(2, '0')}
                </p>
              </div>

              {/* Keyframes Section */}
              <div className="pt-4 border-t border-gray-700">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-gray-500">キーフレーム</label>
                  <span className="text-xs text-gray-400">
                    {selectedVideoClip.keyframes?.length || 0}個
                  </span>
                </div>
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
              <div className="pt-4 border-t border-gray-700">
                <label className="block text-xs text-gray-500 mb-2">位置</label>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs text-gray-600">X</label>
                    <input
                      type="number"
                      value={selectedVideoClip.transform.x}
                      onChange={(e) => handleUpdateVideoClip({ transform: { x: parseInt(e.target.value) || 0 } })}
                      className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-600">Y</label>
                    <input
                      type="number"
                      value={selectedVideoClip.transform.y}
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
                    <label className="block text-xs text-gray-500 mb-1">
                      スケール: {(selectedVideoClip.transform.scale * 100).toFixed(0)}%
                    </label>
                    <input
                      type="range"
                      min="0.1"
                      max="3"
                      step="0.1"
                      value={selectedVideoClip.transform.scale}
                      onChange={(e) => handleUpdateVideoClip({ transform: { scale: parseFloat(e.target.value) } })}
                      className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">
                      回転: {selectedVideoClip.transform.rotation}°
                    </label>
                    <input
                      type="range"
                      min="-180"
                      max="180"
                      step="1"
                      value={selectedVideoClip.transform.rotation}
                      onChange={(e) => handleUpdateVideoClip({ transform: { rotation: parseInt(e.target.value) } })}
                      className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                    />
                  </div>
                </div>
              </div>

              {/* Fit/Fill to Screen Buttons - Only show for video/image clips with asset */}
              {selectedVideoClip.assetId && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-2">画面サイズ調整</label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      onClick={() => handleFitOrFill('fit')}
                      className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                      title="アスペクト比を維持して画面内に収める"
                    >
                      Fit（収める）
                    </button>
                    <button
                      onClick={() => handleFitOrFill('fill')}
                      className="px-3 py-1.5 text-xs bg-green-600 hover:bg-green-700 text-white rounded transition-colors"
                      title="アスペクト比を維持して画面を埋める"
                    >
                      Fill（埋める）
                    </button>
                  </div>
                </div>
              )}

              {/* Effects - Opacity */}
              <div className="pt-4 border-t border-gray-700">
                <label className="block text-xs text-gray-500 mb-1">
                  不透明度: {Math.round((selectedVideoClip.effects.opacity ?? 1) * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={selectedVideoClip.effects.opacity ?? 1}
                  onChange={(e) => handleUpdateVideoClip({ effects: { opacity: parseFloat(e.target.value) } })}
                  className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                />
              </div>

              {/* Fade In / Fade Out */}
              <div className="pt-4 border-t border-gray-700 space-y-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">
                    フェードイン: {((selectedVideoClip.fadeInMs ?? 0) / 1000).toFixed(1)}s
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="3000"
                    step="100"
                    value={selectedVideoClip.fadeInMs ?? 0}
                    onChange={(e) => handleUpdateVideoClip({ effects: { fade_in_ms: parseInt(e.target.value) } })}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">
                    フェードアウト: {((selectedVideoClip.fadeOutMs ?? 0) / 1000).toFixed(1)}s
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="3000"
                    step="100"
                    value={selectedVideoClip.fadeOutMs ?? 0}
                    onChange={(e) => handleUpdateVideoClip({ effects: { fade_out_ms: parseInt(e.target.value) } })}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                  />
                </div>
              </div>

              {/* Chroma Key */}
              {selectedVideoClip.effects.chroma_key && (
                <div className="pt-4 border-t border-gray-700">
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs text-gray-500">クロマキー</label>
                    <button
                      onClick={() => handleUpdateVideoClip({
                        effects: {
                          chroma_key: { enabled: !selectedVideoClip.effects.chroma_key?.enabled }
                        }
                      })}
                      className={`px-2 py-0.5 text-xs rounded cursor-pointer transition-colors ${
                        selectedVideoClip.effects.chroma_key.enabled
                          ? 'bg-green-600 text-white hover:bg-green-700'
                          : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
                      }`}
                    >
                      {selectedVideoClip.effects.chroma_key.enabled ? 'ON' : 'OFF'}
                    </button>
                  </div>
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <label className="text-xs text-gray-600 w-16">色</label>
                      <input
                        type="color"
                        value={selectedVideoClip.effects.chroma_key.color}
                        onChange={(e) => handleUpdateVideoClip({
                          effects: { chroma_key: { color: e.target.value } }
                        })}
                        className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
                      />
                      <span className="text-xs text-gray-400">{selectedVideoClip.effects.chroma_key.color}</span>
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs text-gray-600">類似度</label>
                        <span className="text-xs text-white">{(selectedVideoClip.effects.chroma_key.similarity * 100).toFixed(0)}%</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={selectedVideoClip.effects.chroma_key.similarity}
                        onChange={(e) => handleUpdateVideoClip({
                          effects: { chroma_key: { similarity: parseFloat(e.target.value) } }
                        })}
                        className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs text-gray-600">ブレンド</label>
                        <span className="text-xs text-white">{(selectedVideoClip.effects.chroma_key.blend * 100).toFixed(0)}%</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={selectedVideoClip.effects.chroma_key.blend}
                        onChange={(e) => handleUpdateVideoClip({
                          effects: { chroma_key: { blend: parseFloat(e.target.value) } }
                        })}
                        className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                  </div>
                </div>
              )}

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
                        <span className="text-xs text-white">{selectedVideoClip.shape.strokeWidth}px</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="20"
                        step="1"
                        value={selectedVideoClip.shape.strokeWidth}
                        onChange={(e) => handleUpdateShape({ strokeWidth: parseInt(e.target.value) })}
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
                            <span className="text-xs text-white">{((selectedVideoClip.fadeInMs || 0) / 1000).toFixed(1)}s</span>
                          </div>
                          <input
                            type="range"
                            min="0"
                            max="3000"
                            step="100"
                            value={selectedVideoClip.fadeInMs || 0}
                            onChange={(e) => handleUpdateShapeFade({ fadeInMs: parseInt(e.target.value) })}
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
                            <span className="text-xs text-white">{((selectedVideoClip.fadeOutMs || 0) / 1000).toFixed(1)}s</span>
                          </div>
                          <input
                            type="range"
                            min="0"
                            max="3000"
                            step="100"
                            value={selectedVideoClip.fadeOutMs || 0}
                            onChange={(e) => handleUpdateShapeFade({ fadeOutMs: parseInt(e.target.value) })}
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

              {/* Asset ID */}
              {selectedVideoClip.assetId && (
                <div className="pt-4 border-t border-gray-700">
                  <label className="block text-xs text-gray-500 mb-1">アセットID</label>
                  <p className="text-gray-400 text-xs font-mono break-all">{selectedVideoClip.assetId}</p>
                </div>
              )}
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
                <label className="block text-xs text-gray-500 mb-1">開始位置</label>
                <p className="text-white text-sm">
                  {Math.floor(selectedClip.startMs / 60000)}:
                  {Math.floor((selectedClip.startMs % 60000) / 1000).toString().padStart(2, '0')}
                  .{Math.floor((selectedClip.startMs % 1000) / 10).toString().padStart(2, '0')}
                </p>
              </div>

              {/* Volume */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  音量: {Math.round(selectedClip.volume * 100)}%
                </label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={selectedClip.volume}
                  readOnly
                  className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                />
              </div>

              {/* Fade In */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  フェードイン: {(selectedClip.fadeInMs / 1000).toFixed(1)}s
                </label>
                <div className="w-full h-2 bg-gray-700 rounded-lg overflow-hidden">
                  <div
                    className="h-full bg-green-500 transition-all"
                    style={{ width: `${Math.min(100, (selectedClip.fadeInMs / 3000) * 100)}%` }}
                  />
                </div>
              </div>

              {/* Fade Out */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  フェードアウト: {(selectedClip.fadeOutMs / 1000).toFixed(1)}s
                </label>
                <div className="w-full h-2 bg-gray-700 rounded-lg overflow-hidden">
                  <div
                    className="h-full bg-red-500 transition-all"
                    style={{ width: `${Math.min(100, (selectedClip.fadeOutMs / 3000) * 100)}%` }}
                  />
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
        </aside>
      </div>
    </div>
  )
}
