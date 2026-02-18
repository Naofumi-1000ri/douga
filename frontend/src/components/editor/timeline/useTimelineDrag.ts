import { useCallback, useEffect, useRef, useState } from 'react'
import type { TimelineData, AudioTrack, Layer } from '@/store/projectStore'

import type { DragState, VideoDragState, CrossLayerDropPreview, CrossTrackDropPreview } from './types'

interface UseTimelineDragParams {
  timeline: TimelineData
  assets: Array<{
    id: string
    name: string
    type: string
    duration_ms: number | null
  }>
  pixelsPerSecond: number
  isSnapEnabled: boolean
  snapThresholdMs: number
  getSnapPoints: (excludeClipIds: Set<string>) => number[]
  findNearestSnapPoint: (timeMs: number, snapPoints: number[], threshold: number) => number | null
  updateTimeline: (projectId: string, data: TimelineData, label?: string) => Promise<void> | void
  projectId: string
  calculateMaxDuration: (layers: Layer[], audioTracks: AudioTrack[]) => number
  selectedClip: { trackId: string; clipId: string } | null
  selectedVideoClip: { layerId: string; clipId: string } | null
  selectedAudioClips: Set<string>
  selectedVideoClips: Set<string>
  handleClipSelect: (trackId: string, clipId: string, e?: React.MouseEvent) => void
  handleVideoClipSelect: (layerId: string, clipId: string, e?: React.MouseEvent) => void
  setSnapLineMs: (ms: number | null) => void
  // For cross-layer drag detection
  layerRefs: React.MutableRefObject<{ [layerId: string]: HTMLDivElement | null }>
  sortedLayers: Layer[]
  // For cross-track drag detection (audio clips)
  trackRefs: React.MutableRefObject<{ [trackId: string]: HTMLDivElement | null }>
  audioTracks: AudioTrack[]
}

export function useTimelineDrag({
  timeline,
  assets,
  pixelsPerSecond,
  isSnapEnabled,
  snapThresholdMs,
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
  layerRefs,
  sortedLayers,
  trackRefs,
  audioTracks,
}: UseTimelineDragParams) {
  const [dragState, setDragState] = useState<DragState | null>(null)
  const [videoDragState, setVideoDragState] = useState<VideoDragState | null>(null)
  const [crossLayerDropPreview, setCrossLayerDropPreview] = useState<CrossLayerDropPreview | null>(null)
  const [crossTrackDropPreview, setCrossTrackDropPreview] = useState<CrossTrackDropPreview | null>(null)

  // Drag threshold: require 5px movement before activating drag (prevents accidental drag on click)
  const DRAG_THRESHOLD_PX = 5
  const isDragActivatedRef = useRef<boolean>(false)
  const isVideoDragActivatedRef = useRef<boolean>(false)

  const dragRafRef = useRef<number | null>(null)
  const videoDragRafRef = useRef<number | null>(null)
  const pendingDragDeltaRef = useRef<number>(0)
  const pendingVideoDragDeltaRef = useRef<number>(0)
  const pendingTargetLayerIdRef = useRef<string | null>(null)
  const pendingCrossLayerPreviewRef = useRef<CrossLayerDropPreview | null>(null)
  const pendingTargetTrackIdRef = useRef<string | null>(null)
  const pendingCrossTrackPreviewRef = useRef<CrossTrackDropPreview | null>(null)

  // -------------------------------------------------------------------------
  // Audio clip drag (move / trim)
  // -------------------------------------------------------------------------

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

    const asset = assets.find(a => a.id === clip.asset_id)
    const assetDurationMs = asset?.duration_ms || clip.in_point_ms + clip.duration_ms

    const groupVideoClips: DragState['groupVideoClips'] = []
    const groupAudioClips: DragState['groupAudioClips'] = []

    // First, collect group_id based clips (this takes priority for group sync)
    // Group sync should work even without multi-selection
    if (clip.group_id) {
      for (const l of timeline.layers) {
        if (l.locked) continue
        for (const c of l.clips) {
          if (c.group_id === clip.group_id) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    // Then, add multi-selected clips that are not already in the group
    // If shift is pressed, also include the previously selected clip (selectedClip)
    const isClickedClipInSelection = selectedAudioClips.has(clipId) || selectedClip?.clipId === clipId
    const shouldCollectMultiSelection = isClickedClipInSelection || e.shiftKey

    if (shouldCollectMultiSelection) {
      const addedVideoIds = new Set(groupVideoClips.map(g => g.clipId))
      if (selectedVideoClips.size > 0) {
        for (const l of timeline.layers) {
          if (l.locked) continue
          for (const c of l.clips) {
            if (selectedVideoClips.has(c.id) && !addedVideoIds.has(c.id)) {
              groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
            }
          }
        }
      }

      const addedAudioIds = new Set(groupAudioClips.map(g => g.clipId))

      // If shift is pressed and there's a previously selected audio clip, add it to the group
      if (e.shiftKey && selectedClip && selectedClip.clipId !== clipId && !addedAudioIds.has(selectedClip.clipId)) {
        const prevTrack = timeline.audio_tracks.find(t => t.id === selectedClip.trackId)
        const prevClip = prevTrack?.clips.find(c => c.id === selectedClip.clipId)
        if (prevClip) {
          groupAudioClips.push({ clipId: prevClip.id, layerOrTrackId: selectedClip.trackId, initialStartMs: prevClip.start_ms })
          addedAudioIds.add(prevClip.id)
        }
      }

      if (selectedAudioClips.size > 0) {
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (selectedAudioClips.has(c.id) && c.id !== clipId && !addedAudioIds.has(c.id)) {
              groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
            }
          }
        }
      }
    }

    pendingDragDeltaRef.current = 0

    // Calculate the offset from the clip's left edge to where the mouse clicked
    // This is used to keep the ghost aligned with the mouse during drag
    const clipElement = e.currentTarget as HTMLElement
    const clipRect = clipElement.getBoundingClientRect()
    const clickOffsetPx = e.clientX - clipRect.left
    const clickOffsetMs = Math.round((clickOffsetPx / pixelsPerSecond) * 1000)

    // Reset drag activation flag (require threshold movement before drag activates)
    isDragActivatedRef.current = false

    pendingTargetTrackIdRef.current = null

    setDragState({
      type,
      trackId,
      clipId,
      startX: e.clientX,
      startY: e.clientY,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      assetDurationMs,
      currentDeltaMs: 0,
      clickOffsetMs,
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
      targetTrackId: null,
    })

    if (!e.shiftKey && !selectedAudioClips.has(clipId) && selectedClip?.clipId !== clipId) {
      handleClipSelect(trackId, clipId, e)
    }
  }, [assets, handleClipSelect, pixelsPerSecond, selectedAudioClips, selectedClip, selectedVideoClips, timeline.audio_tracks, timeline.layers])

  const handleClipDragMove = useCallback((e: MouseEvent) => {
    if (!dragState) return

    const deltaX = e.clientX - dragState.startX
    const deltaY = e.clientY - dragState.startY

    // Check drag threshold before activating drag (prevents accidental drag on click)
    if (!isDragActivatedRef.current) {
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY)
      if (distance < DRAG_THRESHOLD_PX) {
        return // Don't process drag until threshold is exceeded
      }
      isDragActivatedRef.current = true
    }

    let deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    const draggingClipIds = new Set([dragState.clipId])
    dragState.groupVideoClips?.forEach(gc => draggingClipIds.add(gc.clipId))
    dragState.groupAudioClips?.forEach(gc => draggingClipIds.add(gc.clipId))

    if (dragState.type === 'move') {
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newStartMs = dragState.initialStartMs + deltaMs
        const newEndMs = newStartMs + dragState.initialDurationMs

        const snapStart = findNearestSnapPoint(newStartMs, snapPoints, snapThresholdMs)
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapStart !== null) {
          deltaMs = snapStart - dragState.initialStartMs
          setSnapLineMs(snapStart)
        } else if (snapEnd !== null) {
          deltaMs = snapEnd - dragState.initialDurationMs - dragState.initialStartMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (dragState.type === 'trim-start') {
      // Snap the new start position when trimming from the left
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
        const snapStart = findNearestSnapPoint(newStartMs, snapPoints, snapThresholdMs)

        if (snapStart !== null) {
          deltaMs = snapStart - dragState.initialStartMs
          setSnapLineMs(snapStart)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (dragState.type === 'trim-end') {
      // Snap the new end position when trimming from the right
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newEndMs = dragState.initialStartMs + dragState.initialDurationMs + deltaMs
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapEnd !== null) {
          deltaMs = snapEnd - dragState.initialStartMs - dragState.initialDurationMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else {
      setSnapLineMs(null)
    }

    // Detect target track for cross-track drag (only for 'move' type)
    let detectedTargetTrackId: string | null = null
    if (dragState.type === 'move') {
      for (const track of audioTracks) {
        const trackEl = trackRefs.current[track.id]
        if (trackEl) {
          const rect = trackEl.getBoundingClientRect()
          if (e.clientY >= rect.top && e.clientY <= rect.bottom) {
            detectedTargetTrackId = track.id
            break
          }
        }
      }
      // If target is same as origin, don't set targetTrackId
      if (detectedTargetTrackId === dragState.trackId) {
        detectedTargetTrackId = null
      }
    }
    pendingTargetTrackIdRef.current = detectedTargetTrackId

    // Calculate cross-track drop preview
    if (dragState.type === 'move' && detectedTargetTrackId) {
      const snappedStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
      pendingCrossTrackPreviewRef.current = {
        trackId: detectedTargetTrackId,
        timeMs: snappedStartMs,
        durationMs: dragState.initialDurationMs,
      }
    } else {
      pendingCrossTrackPreviewRef.current = null
    }

    pendingDragDeltaRef.current = deltaMs

    if (dragRafRef.current === null) {
      dragRafRef.current = requestAnimationFrame(() => {
        setDragState(prev => (prev ? {
          ...prev,
          currentDeltaMs: pendingDragDeltaRef.current,
          targetTrackId: pendingTargetTrackIdRef.current,
        } : null))
        setCrossTrackDropPreview(pendingCrossTrackPreviewRef.current)
        dragRafRef.current = null
      })
    }
  }, [dragState, pixelsPerSecond, isSnapEnabled, getSnapPoints, findNearestSnapPoint, snapThresholdMs, setSnapLineMs, audioTracks, trackRefs])

  const handleClipDragEnd = useCallback(() => {
    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current)
      dragRafRef.current = null
    }

    if (!dragState) {
      setDragState(null)
      return
    }

    const deltaMs = pendingDragDeltaRef.current || dragState.currentDeltaMs
    const targetTrackId = pendingTargetTrackIdRef.current || dragState.targetTrackId

    // No actual change (click without drag) - skip undo history creation
    if (!isDragActivatedRef.current || (deltaMs === 0 && !targetTrackId)) {
      setDragState(null)
      setSnapLineMs(null)
      setCrossTrackDropPreview(null)
      pendingDragDeltaRef.current = 0
      pendingTargetTrackIdRef.current = null
      pendingCrossTrackPreviewRef.current = null
      return
    }

    const groupAudioClipIds = new Set(dragState.groupAudioClips?.map(c => c.clipId) || [])

    // Check if this is a cross-track move
    const isCrossTrackMove = dragState.type === 'move' && targetTrackId && targetTrackId !== dragState.trackId

    let updatedTracks: AudioTrack[]

    if (isCrossTrackMove) {
      // Cross-track move: remove clip from source track and add to target track
      const sourceTrack = timeline.audio_tracks.find(t => t.id === dragState.trackId)
      const movingClip = sourceTrack?.clips.find(c => c.id === dragState.clipId)

      if (movingClip) {
        const newStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
        const updatedClip = { ...movingClip, start_ms: newStartMs }

        updatedTracks = timeline.audio_tracks.map((t) => {
          // Remove from source track
          if (t.id === dragState.trackId) {
            return {
              ...t,
              clips: t.clips.filter(c => c.id !== dragState.clipId),
            }
          }
          // Add to target track
          if (t.id === targetTrackId) {
            return {
              ...t,
              clips: [...t.clips, updatedClip],
            }
          }
          // Handle group clips on other tracks (move them too)
          if (groupAudioClipIds.size > 0) {
            const hasGroupClips = t.clips.some(c => groupAudioClipIds.has(c.id))
            if (hasGroupClips) {
              return {
                ...t,
                clips: t.clips.map((clip) => {
                  if (groupAudioClipIds.has(clip.id)) {
                    const groupClip = dragState.groupAudioClips?.find(c => c.clipId === clip.id)
                    if (groupClip) {
                      const groupNewStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                      return { ...clip, start_ms: groupNewStartMs }
                    }
                  }
                  return clip
                }),
              }
            }
          }
          return t
        })
      } else {
        updatedTracks = timeline.audio_tracks
      }
    } else {
      // Same-track move or trim
      updatedTracks = timeline.audio_tracks.map((t) => {
        const hasPrimaryClip = t.id === dragState.trackId
        const hasGroupClips = t.clips.some(c => groupAudioClipIds.has(c.id))
        if (!hasPrimaryClip && !hasGroupClips) return t

        return {
          ...t,
          clips: t.clips.map((clip) => {
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
    }

    const groupVideoClipIds = new Set(dragState.groupVideoClips?.map(c => c.clipId) || [])

    let updatedLayers = timeline.layers
    if (dragState.type === 'move' && groupVideoClipIds.size > 0) {
      updatedLayers = timeline.layers.map((layer) => {
        const hasGroupClips = layer.clips.some(c => groupVideoClipIds.has(c.id))
        if (!hasGroupClips) return layer

        return {
          ...layer,
          clips: layer.clips.map((videoClip) => {
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

    const newDuration = calculateMaxDuration(updatedLayers, updatedTracks)

    const dragLabel = isCrossTrackMove
      ? 'オーディオクリップを別トラックに移動'
      : dragState.type === 'move' ? 'オーディオクリップを移動' : 'オーディオクリップをトリム'
    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks, layers: updatedLayers, duration_ms: newDuration }, dragLabel)
    setDragState(null)
    setSnapLineMs(null)
    setCrossTrackDropPreview(null)
    pendingDragDeltaRef.current = 0
    pendingTargetTrackIdRef.current = null
    pendingCrossTrackPreviewRef.current = null
  }, [dragState, timeline, calculateMaxDuration, updateTimeline, projectId, setSnapLineMs, assets])

  useEffect(() => {
    if (dragState) {
      window.addEventListener('mousemove', handleClipDragMove)
      window.addEventListener('mouseup', handleClipDragEnd)
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

  // -------------------------------------------------------------------------
  // Video clip drag (move / trim)
  // -------------------------------------------------------------------------

  const handleVideoClipDragStart = useCallback((
    e: React.MouseEvent,
    layerId: string,
    clipId: string,
    type: 'move' | 'trim-start' | 'trim-end' | 'stretch-start' | 'stretch-end' | 'freeze-end'
  ) => {
    e.preventDefault()
    e.stopPropagation()

    const layer = timeline.layers.find(l => l.id === layerId)
    const clip = layer?.clips.find(c => c.id === clipId)
    if (!clip || layer?.locked) return

    const asset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
    const isImageAsset = asset?.type === 'image'
    const isVideoAsset = asset?.type === 'video'
    const isResizableClip = !!(clip.shape || clip.text_content || !clip.asset_id || isImageAsset)
    const assetDurationMs = isResizableClip ? Infinity : (asset?.duration_ms || clip.in_point_ms + clip.duration_ms)

    const groupVideoClips: VideoDragState['groupVideoClips'] = []
    const groupAudioClips: VideoDragState['groupAudioClips'] = []

    console.log('[handleVideoClipDragStart] clipId:', clipId, 'group_id:', clip.group_id, 'selectedVideoClips:', [...selectedVideoClips], 'selectedVideoClip:', selectedVideoClip)

    // First, collect group_id based clips (this takes priority for group sync)
    // Group sync should work even without multi-selection
    if (clip.group_id) {
      console.log('[handleVideoClipDragStart] Collecting group clips for group_id:', clip.group_id)
      for (const l of timeline.layers) {
        if (l.locked) continue
        for (const c of l.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId) {
            console.log('[handleVideoClipDragStart] Found group video clip:', c.id, 'in layer:', l.id)
            const groupAsset = c.asset_id ? assets.find(a => a.id === c.asset_id) : null
            const groupAssetDurationMs = groupAsset?.duration_ms || c.in_point_ms + c.duration_ms
            groupVideoClips.push({
              clipId: c.id,
              layerOrTrackId: l.id,
              initialStartMs: c.start_ms,
              initialDurationMs: c.duration_ms,
              initialInPointMs: c.in_point_ms,
              initialOutPointMs: c.out_point_ms ?? (c.in_point_ms + c.duration_ms * (c.speed || 1)),
              assetDurationMs: groupAssetDurationMs,
            })
          }
        }
      }
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id) {
            console.log('[handleVideoClipDragStart] Found group audio clip:', c.id, 'in track:', t.id)
            const groupAsset = assets.find(a => a.id === c.asset_id)
            const groupAssetDurationMs = groupAsset?.duration_ms || c.in_point_ms + c.duration_ms
            groupAudioClips.push({
              clipId: c.id,
              layerOrTrackId: t.id,
              initialStartMs: c.start_ms,
              initialDurationMs: c.duration_ms,
              initialInPointMs: c.in_point_ms,
              initialOutPointMs: c.out_point_ms ?? (c.in_point_ms + c.duration_ms),
              assetDurationMs: groupAssetDurationMs,
            })
          }
        }
      }
    }

    // Then, add multi-selected clips that are not already in the group
    // If shift is pressed, also include the previously selected clip (selectedVideoClip)
    const isClickedClipInSelection = selectedVideoClips.has(clipId) || selectedVideoClip?.clipId === clipId
    const shouldCollectMultiSelection = isClickedClipInSelection || e.shiftKey
    console.log('[handleVideoClipDragStart] isClickedClipInSelection:', isClickedClipInSelection, 'shiftKey:', e.shiftKey, 'selectedVideoClips.size:', selectedVideoClips.size, 'selectedAudioClips.size:', selectedAudioClips.size)

    if (shouldCollectMultiSelection) {
      const addedVideoIds = new Set(groupVideoClips.map(g => g.clipId))

      // If shift is pressed and there's a previously selected clip, add it to the group
      if (e.shiftKey && selectedVideoClip && selectedVideoClip.clipId !== clipId && !addedVideoIds.has(selectedVideoClip.clipId)) {
        const prevLayer = timeline.layers.find(l => l.id === selectedVideoClip.layerId)
        const prevClip = prevLayer?.clips.find(c => c.id === selectedVideoClip.clipId)
        if (prevClip && !prevLayer?.locked) {
          groupVideoClips.push({ clipId: prevClip.id, layerOrTrackId: selectedVideoClip.layerId, initialStartMs: prevClip.start_ms })
          addedVideoIds.add(prevClip.id)
        }
      }

      if (selectedVideoClips.size > 0) {
        for (const l of timeline.layers) {
          if (l.locked) continue
          for (const c of l.clips) {
            if (selectedVideoClips.has(c.id) && c.id !== clipId && !addedVideoIds.has(c.id)) {
              groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
            }
          }
        }
      }

      const addedAudioIds = new Set(groupAudioClips.map(g => g.clipId))
      if (selectedAudioClips.size > 0) {
        for (const t of timeline.audio_tracks) {
          for (const c of t.clips) {
            if (selectedAudioClips.has(c.id) && !addedAudioIds.has(c.id)) {
              groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
            }
          }
        }
      }
    }

    pendingVideoDragDeltaRef.current = 0

    console.log('[handleVideoClipDragStart] Final groupVideoClips:', groupVideoClips.length, groupVideoClips, 'groupAudioClips:', groupAudioClips.length, groupAudioClips)

    pendingTargetLayerIdRef.current = null

    // Reset drag activation flag (require threshold movement before drag activates)
    isVideoDragActivatedRef.current = false

    // Calculate the offset from the clip's left edge to where the mouse clicked
    // This is used to keep the ghost aligned with the mouse during drag
    const clipElement = e.currentTarget as HTMLElement
    const clipRect = clipElement.getBoundingClientRect()
    const clickOffsetPx = e.clientX - clipRect.left
    const clickOffsetMs = Math.round((clickOffsetPx / pixelsPerSecond) * 1000)

    setVideoDragState({
      type,
      layerId,
      clipId,
      startX: e.clientX,
      startY: e.clientY,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      initialOutPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
      initialSpeed: clip.speed || 1,
      assetDurationMs,
      currentDeltaMs: 0,
      clickOffsetMs,
      isResizableClip,
      isVideoAsset: isVideoAsset ?? false,
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
      targetLayerId: null,
      ...(type === 'freeze-end' ? { initialFreezeFrameMs: clip.freeze_frame_ms ?? 0 } : {}),
    })

    if (!e.shiftKey && !selectedVideoClips.has(clipId) && selectedVideoClip?.clipId !== clipId) {
      handleVideoClipSelect(layerId, clipId, e)
    }
  }, [assets, handleVideoClipSelect, pixelsPerSecond, selectedAudioClips, selectedVideoClips, selectedVideoClip, timeline.layers, timeline.audio_tracks])

  const handleVideoClipDragMove = useCallback((e: MouseEvent) => {
    if (!videoDragState) return

    const deltaX = e.clientX - videoDragState.startX
    const deltaY = e.clientY - videoDragState.startY

    // Check drag threshold before activating drag (prevents accidental drag on click)
    if (!isVideoDragActivatedRef.current) {
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY)
      if (distance < DRAG_THRESHOLD_PX) {
        return // Don't process drag until threshold is exceeded
      }
      isVideoDragActivatedRef.current = true
    }

    let deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    const draggingClipIds = new Set([videoDragState.clipId])
    videoDragState.groupVideoClips?.forEach(gc => draggingClipIds.add(gc.clipId))
    videoDragState.groupAudioClips?.forEach(gc => draggingClipIds.add(gc.clipId))

    // Detect target layer for cross-layer drag (only for 'move' type)
    let detectedTargetLayerId: string | null = null
    if (videoDragState.type === 'move') {
      // Find which layer the mouse is over
      for (const layer of sortedLayers) {
        if (layer.locked) continue
        const layerEl = layerRefs.current[layer.id]
        if (layerEl) {
          const rect = layerEl.getBoundingClientRect()
          if (e.clientY >= rect.top && e.clientY <= rect.bottom) {
            detectedTargetLayerId = layer.id
            break
          }
        }
      }
      // If target is same as origin, don't set targetLayerId
      if (detectedTargetLayerId === videoDragState.layerId) {
        detectedTargetLayerId = null
      }
    }
    pendingTargetLayerIdRef.current = detectedTargetLayerId

    if (videoDragState.type === 'move') {
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newStartMs = videoDragState.initialStartMs + deltaMs
        const newEndMs = newStartMs + videoDragState.initialDurationMs

        const snapStart = findNearestSnapPoint(newStartMs, snapPoints, snapThresholdMs)
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapStart !== null) {
          deltaMs = snapStart - videoDragState.initialStartMs
          setSnapLineMs(snapStart)
        } else if (snapEnd !== null) {
          deltaMs = snapEnd - videoDragState.initialDurationMs - videoDragState.initialStartMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }

      // Calculate cross-layer drop preview when dragging to a different layer (after snap calculation)
      if (detectedTargetLayerId) {
        const snappedStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
        pendingCrossLayerPreviewRef.current = {
          layerId: detectedTargetLayerId,
          timeMs: snappedStartMs,
          durationMs: videoDragState.initialDurationMs,
        }
      } else {
        pendingCrossLayerPreviewRef.current = null
      }
    } else if (videoDragState.type === 'trim-start') {
      // Snap the new start position when trimming from the left
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
        const snapStart = findNearestSnapPoint(newStartMs, snapPoints, snapThresholdMs)

        if (snapStart !== null) {
          deltaMs = snapStart - videoDragState.initialStartMs
          setSnapLineMs(snapStart)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (videoDragState.type === 'trim-end') {
      // Snap the new end position when trimming from the right
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newEndMs = videoDragState.initialStartMs + videoDragState.initialDurationMs + deltaMs
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapEnd !== null) {
          deltaMs = snapEnd - videoDragState.initialStartMs - videoDragState.initialDurationMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (videoDragState.type === 'stretch-start') {
      // Snap the new start position when stretching from the left
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
        const snapStart = findNearestSnapPoint(newStartMs, snapPoints, snapThresholdMs)

        if (snapStart !== null) {
          deltaMs = snapStart - videoDragState.initialStartMs
          setSnapLineMs(snapStart)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (videoDragState.type === 'stretch-end') {
      // Snap the new end position when stretching from the right
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const newEndMs = videoDragState.initialStartMs + videoDragState.initialDurationMs + deltaMs
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapEnd !== null) {
          deltaMs = snapEnd - videoDragState.initialStartMs - videoDragState.initialDurationMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else if (videoDragState.type === 'freeze-end') {
      // Snap the new end position when extending with freeze frame
      if (isSnapEnabled) {
        const snapPoints = getSnapPoints(draggingClipIds)
        const initialFreezeMs = videoDragState.initialFreezeFrameMs ?? 0
        const newFreezeMs = Math.max(0, initialFreezeMs + deltaMs)
        const newEndMs = videoDragState.initialStartMs + videoDragState.initialDurationMs + newFreezeMs
        const snapEnd = findNearestSnapPoint(newEndMs, snapPoints, snapThresholdMs)

        if (snapEnd !== null) {
          const snappedFreezeMs = snapEnd - videoDragState.initialStartMs - videoDragState.initialDurationMs
          deltaMs = snappedFreezeMs - initialFreezeMs
          setSnapLineMs(snapEnd)
        } else {
          setSnapLineMs(null)
        }
      } else {
        setSnapLineMs(null)
      }
    } else {
      setSnapLineMs(null)
    }

    pendingVideoDragDeltaRef.current = deltaMs

    if (videoDragRafRef.current === null) {
      videoDragRafRef.current = requestAnimationFrame(() => {
        setVideoDragState(prev => (prev ? {
          ...prev,
          currentDeltaMs: pendingVideoDragDeltaRef.current,
          targetLayerId: pendingTargetLayerIdRef.current,
        } : null))
        setCrossLayerDropPreview(pendingCrossLayerPreviewRef.current)
        videoDragRafRef.current = null
      })
    }
  }, [videoDragState, pixelsPerSecond, isSnapEnabled, getSnapPoints, findNearestSnapPoint, snapThresholdMs, setSnapLineMs, sortedLayers, layerRefs])

  const handleVideoClipDragEnd = useCallback(() => {
    if (videoDragRafRef.current !== null) {
      cancelAnimationFrame(videoDragRafRef.current)
      videoDragRafRef.current = null
    }

    if (!videoDragState) {
      setVideoDragState(null)
      return
    }

    const deltaMs = pendingVideoDragDeltaRef.current || videoDragState.currentDeltaMs
    const targetLayerId = pendingTargetLayerIdRef.current || videoDragState.targetLayerId

    // No actual change (click without drag) - skip undo history creation
    if (!isVideoDragActivatedRef.current || (deltaMs === 0 && !targetLayerId)) {
      setVideoDragState(null)
      setSnapLineMs(null)
      setCrossLayerDropPreview(null)
      pendingVideoDragDeltaRef.current = 0
      pendingTargetLayerIdRef.current = null
      pendingCrossLayerPreviewRef.current = null
      return
    }

    const groupVideoClipIds = new Set(videoDragState.groupVideoClips?.map(c => c.clipId) || [])

    // Check if this is a cross-layer move
    const isCrossLayerMove = videoDragState.type === 'move' && targetLayerId && targetLayerId !== videoDragState.layerId

    let updatedLayers: Layer[]

    if (isCrossLayerMove) {
      // Cross-layer move: remove clip from source layer and add to target layer
      const sourceLayer = timeline.layers.find(l => l.id === videoDragState.layerId)
      const movingClip = sourceLayer?.clips.find(c => c.id === videoDragState.clipId)

      if (movingClip) {
        const newStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
        const updatedClip = { ...movingClip, start_ms: newStartMs }

        updatedLayers = timeline.layers.map((layer) => {
          // Remove from source layer
          if (layer.id === videoDragState.layerId) {
            return {
              ...layer,
              clips: layer.clips.filter(c => c.id !== videoDragState.clipId),
            }
          }
          // Add to target layer
          if (layer.id === targetLayerId) {
            return {
              ...layer,
              clips: [...layer.clips, updatedClip],
            }
          }
          // Handle group clips on other layers (move them too)
          if (groupVideoClipIds.size > 0) {
            const hasGroupClips = layer.clips.some(c => groupVideoClipIds.has(c.id))
            if (hasGroupClips) {
              return {
                ...layer,
                clips: layer.clips.map((clip) => {
                  if (groupVideoClipIds.has(clip.id)) {
                    const groupClip = videoDragState.groupVideoClips?.find(c => c.clipId === clip.id)
                    if (groupClip) {
                      const groupNewStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                      return { ...clip, start_ms: groupNewStartMs }
                    }
                  }
                  return clip
                }),
              }
            }
          }
          return layer
        })
      } else {
        updatedLayers = timeline.layers
      }
    } else {
      // Same-layer move or trim
      updatedLayers = timeline.layers.map((layer) => {
        const hasPrimaryClip = layer.id === videoDragState.layerId
        const hasGroupClips = layer.clips.some(c => groupVideoClipIds.has(c.id))
        if (!hasPrimaryClip && !hasGroupClips) return layer

        return {
          ...layer,
          clips: layer.clips.map((clip) => {
            if (clip.id === videoDragState.clipId) {
              if (videoDragState.type === 'move') {
                const newStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
                return { ...clip, start_ms: newStartMs }
              } else if (videoDragState.type === 'trim-start') {
                // Crop mode: adjust in_point and duration (clip the start)
                const maxTrim = videoDragState.initialDurationMs - 100
                const speed = clip.speed ?? 1
                const minTrim = videoDragState.isResizableClip ? -Infinity : Math.ceil(-(videoDragState.initialInPointMs / speed))
                const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                const newStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
                const effectiveTrim = newStartMs - videoDragState.initialStartMs
                const sourceTrimMs = videoDragState.isResizableClip ? effectiveTrim : Math.round(effectiveTrim * speed)
                const newInPointMs = videoDragState.isResizableClip ? 0 : videoDragState.initialInPointMs + sourceTrimMs
                const newDurationMs = videoDragState.initialDurationMs - effectiveTrim
                const newOutPointMs = videoDragState.isResizableClip
                  ? newInPointMs + newDurationMs
                  : newInPointMs + Math.round(newDurationMs * speed)
                return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
              } else if (videoDragState.type === 'trim-end') {
                // Crop mode: adjust out_point and duration (clip the end)
                const speed = clip.speed ?? 1
                const maxDuration = videoDragState.isResizableClip
                  ? Infinity
                  : Math.floor((videoDragState.assetDurationMs - videoDragState.initialInPointMs) / speed)
                const newDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
                const newOutPointMs = videoDragState.isResizableClip
                  ? videoDragState.initialInPointMs + newDurationMs
                  : videoDragState.initialInPointMs + Math.round(newDurationMs * speed)
                return { ...clip, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
              } else if (videoDragState.type === 'stretch-start') {
                // Stretch mode: adjust speed to stretch/compress playback from start
                const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                const minDurationMs = 100
                const maxSpeed = 5.0
                const minSpeed = 0.2

                let newDurationMs = videoDragState.initialDurationMs - deltaMs
                newDurationMs = Math.max(minDurationMs, newDurationMs)

                let newSpeed = sourceDuration / newDurationMs
                newSpeed = Math.max(minSpeed, Math.min(maxSpeed, newSpeed))

                const finalDurationMs = Math.round(sourceDuration / newSpeed)
                const durationChange = finalDurationMs - videoDragState.initialDurationMs
                const newStartMs = Math.max(0, videoDragState.initialStartMs - durationChange)

                return {
                  ...clip,
                  start_ms: newStartMs,
                  duration_ms: finalDurationMs,
                  speed: Math.round(newSpeed * 1000) / 1000,
                }
              } else if (videoDragState.type === 'stretch-end') {
                // Stretch mode: adjust speed to stretch/compress playback from end
                const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                const minDurationMs = 100
                const maxSpeed = 5.0
                const minSpeed = 0.2

                let newDurationMs = videoDragState.initialDurationMs + deltaMs
                newDurationMs = Math.max(minDurationMs, newDurationMs)

                let newSpeed = sourceDuration / newDurationMs
                newSpeed = Math.max(minSpeed, Math.min(maxSpeed, newSpeed))

                const finalDurationMs = Math.round(sourceDuration / newSpeed)

                return {
                  ...clip,
                  duration_ms: finalDurationMs,
                  speed: Math.round(newSpeed * 1000) / 1000,
                }
              } else if (videoDragState.type === 'freeze-end') {
                // Freeze-end mode: adjust freeze_frame_ms (static frame extension at the end)
                const newFreezeMs = Math.max(0, (videoDragState.initialFreezeFrameMs ?? 0) + deltaMs)
                return { ...clip, freeze_frame_ms: newFreezeMs }
              }
            }

            // Handle group clips for move operation
            if (videoDragState.type === 'move' && groupVideoClipIds.has(clip.id)) {
              const groupClip = videoDragState.groupVideoClips?.find(c => c.clipId === clip.id)
              if (groupClip) {
                const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                return { ...clip, start_ms: newStartMs }
              }
            }

            // Handle group clips for trim-start operation (group crop)
            if (videoDragState.type === 'trim-start' && groupVideoClipIds.has(clip.id)) {
              const groupClip = videoDragState.groupVideoClips?.find(c => c.clipId === clip.id)
              if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                const speed = clip.speed ?? 1
                const maxTrim = groupClip.initialDurationMs - 100
                const minTrim = Math.ceil(-(groupClip.initialInPointMs / speed))
                const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                const newStartMs = Math.max(0, groupClip.initialStartMs + trimAmount)
                const effectiveTrim = newStartMs - groupClip.initialStartMs
                const sourceTrimMs = Math.round(effectiveTrim * speed)
                const newInPointMs = groupClip.initialInPointMs + sourceTrimMs
                const newDurationMs = groupClip.initialDurationMs - effectiveTrim
                const newOutPointMs = newInPointMs + Math.round(newDurationMs * speed)
                return {
                  ...clip,
                  start_ms: newStartMs,
                  in_point_ms: newInPointMs,
                  duration_ms: newDurationMs,
                  out_point_ms: newOutPointMs,
                }
              }
            }

            // Handle group clips for trim-end operation (group crop)
            if (videoDragState.type === 'trim-end' && groupVideoClipIds.has(clip.id)) {
              const groupClip = videoDragState.groupVideoClips?.find(c => c.clipId === clip.id)
              if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                const speed = clip.speed ?? 1
                const maxDuration = Math.floor(((groupClip.assetDurationMs ?? Infinity) - groupClip.initialInPointMs) / speed)
                const newDurationMs = Math.min(Math.max(100, groupClip.initialDurationMs + deltaMs), maxDuration)
                const newOutPointMs = groupClip.initialInPointMs + Math.round(newDurationMs * speed)
                return {
                  ...clip,
                  duration_ms: newDurationMs,
                  out_point_ms: newOutPointMs,
                }
              }
            }

            return clip
          }),
        }
      })
    }

    const groupAudioClipIds = new Set(videoDragState.groupAudioClips?.map(c => c.clipId) || [])

    let updatedTracks = timeline.audio_tracks
    // Handle group audio clips for move, trim-start, and trim-end operations
    const shouldUpdateAudioTracks = (videoDragState.type === 'move' || videoDragState.type === 'trim-start' || videoDragState.type === 'trim-end') && groupAudioClipIds.size > 0
    if (shouldUpdateAudioTracks) {
      updatedTracks = timeline.audio_tracks.map((track) => {
        const hasGroupClips = track.clips.some(c => groupAudioClipIds.has(c.id))
        if (!hasGroupClips) return track

        return {
          ...track,
          clips: track.clips.map((audioClip) => {
            if (groupAudioClipIds.has(audioClip.id)) {
              const groupClip = videoDragState.groupAudioClips?.find(c => c.clipId === audioClip.id)
              if (groupClip) {
                // Handle move
                if (videoDragState.type === 'move') {
                  const newStartMs = Math.max(0, groupClip.initialStartMs + deltaMs)
                  return { ...audioClip, start_ms: newStartMs }
                }
                // Handle trim-start (group crop)
                if (videoDragState.type === 'trim-start' && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                  const maxTrim = groupClip.initialDurationMs - 100
                  const minTrim = -groupClip.initialInPointMs
                  const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                  const newStartMs = Math.max(0, groupClip.initialStartMs + trimAmount)
                  const effectiveTrim = newStartMs - groupClip.initialStartMs
                  const newInPointMs = groupClip.initialInPointMs + effectiveTrim
                  const newDurationMs = groupClip.initialDurationMs - effectiveTrim
                  const newOutPointMs = newInPointMs + newDurationMs
                  return {
                    ...audioClip,
                    start_ms: newStartMs,
                    in_point_ms: newInPointMs,
                    duration_ms: newDurationMs,
                    out_point_ms: newOutPointMs,
                  }
                }
                // Handle trim-end (group crop)
                if (videoDragState.type === 'trim-end' && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                  const maxDuration = (groupClip.assetDurationMs ?? Infinity) - groupClip.initialInPointMs
                  const newDurationMs = Math.min(Math.max(100, groupClip.initialDurationMs + deltaMs), maxDuration)
                  const newOutPointMs = groupClip.initialInPointMs + newDurationMs
                  return {
                    ...audioClip,
                    duration_ms: newDurationMs,
                    out_point_ms: newOutPointMs,
                  }
                }
              }
            }
            return audioClip
          }),
        }
      })
    }

    const newDuration = calculateMaxDuration(updatedLayers, updatedTracks)

    const videoDragLabel = videoDragState.type === 'move'
      ? 'クリップを移動'
      : (videoDragState.type === 'trim-start' || videoDragState.type === 'trim-end')
        ? 'クリップをトリム'
        : (videoDragState.type === 'freeze-end')
          ? '静止画延長を変更'
          : 'クリップの速度を変更'
    updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks, duration_ms: newDuration }, videoDragLabel)
    setVideoDragState(null)
    setSnapLineMs(null)
    setCrossLayerDropPreview(null)
    pendingVideoDragDeltaRef.current = 0
    pendingTargetLayerIdRef.current = null
    pendingCrossLayerPreviewRef.current = null
  }, [videoDragState, timeline, calculateMaxDuration, updateTimeline, projectId, setSnapLineMs, assets])

  useEffect(() => {
    if (videoDragState) {
      window.addEventListener('mousemove', handleVideoClipDragMove)
      window.addEventListener('mouseup', handleVideoClipDragEnd)
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

  return {
    dragState,
    videoDragState,
    crossLayerDropPreview,
    crossTrackDropPreview,
    handleClipDragStart,
    handleVideoClipDragStart,
  }
}
