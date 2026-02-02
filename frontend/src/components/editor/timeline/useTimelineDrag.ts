import { useCallback, useEffect, useRef, useState } from 'react'
import type { TimelineData, AudioTrack, Layer } from '@/store/projectStore'

import type { DragState, VideoDragState } from './types'

interface UseTimelineDragParams {
  timeline: TimelineData
  assets: Array<{
    id: string
    type: string
    duration_ms: number | null
  }>
  pixelsPerSecond: number
  isSnapEnabled: boolean
  snapThresholdMs: number
  getSnapPoints: (excludeClipIds: Set<string>) => number[]
  findNearestSnapPoint: (timeMs: number, snapPoints: number[], threshold: number) => number | null
  updateTimeline: (projectId: string, data: TimelineData) => Promise<void> | void
  projectId: string
  calculateMaxDuration: (layers: Layer[], audioTracks: AudioTrack[]) => number
  selectedClip: { trackId: string; clipId: string } | null
  selectedVideoClip: { layerId: string; clipId: string } | null
  selectedAudioClips: Set<string>
  selectedVideoClips: Set<string>
  handleClipSelect: (trackId: string, clipId: string, e?: React.MouseEvent) => void
  handleVideoClipSelect: (layerId: string, clipId: string, e?: React.MouseEvent) => void
  setSnapLineMs: (ms: number | null) => void
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
}: UseTimelineDragParams) {
  const [dragState, setDragState] = useState<DragState | null>(null)
  const [videoDragState, setVideoDragState] = useState<VideoDragState | null>(null)

  const dragRafRef = useRef<number | null>(null)
  const videoDragRafRef = useRef<number | null>(null)
  const pendingDragDeltaRef = useRef<number>(0)
  const pendingVideoDragDeltaRef = useRef<number>(0)

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
    const isClickedClipInSelection = selectedAudioClips.has(clipId) || selectedClip?.clipId === clipId

    if (isClickedClipInSelection) {
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
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
    })

    if (!e.shiftKey && !selectedAudioClips.has(clipId) && selectedClip?.clipId !== clipId) {
      handleClipSelect(trackId, clipId)
    }
  }, [assets, handleClipSelect, selectedAudioClips, selectedClip, selectedVideoClips, timeline.audio_tracks, timeline.layers])

  const handleClipDragMove = useCallback((e: MouseEvent) => {
    if (!dragState) return

    const deltaX = e.clientX - dragState.startX
    let deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    if (dragState.type === 'move') {
      const draggingClipIds = new Set([dragState.clipId])
      dragState.groupVideoClips?.forEach(gc => draggingClipIds.add(gc.clipId))
      dragState.groupAudioClips?.forEach(gc => draggingClipIds.add(gc.clipId))

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
    } else {
      setSnapLineMs(null)
    }

    pendingDragDeltaRef.current = deltaMs

    if (dragRafRef.current === null) {
      dragRafRef.current = requestAnimationFrame(() => {
        setDragState(prev => (prev ? { ...prev, currentDeltaMs: pendingDragDeltaRef.current } : null))
        dragRafRef.current = null
      })
    }
  }, [dragState, pixelsPerSecond, isSnapEnabled, getSnapPoints, findNearestSnapPoint, snapThresholdMs, setSnapLineMs])

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
    const groupAudioClipIds = new Set(dragState.groupAudioClips?.map(c => c.clipId) || [])

    const updatedTracks = timeline.audio_tracks.map((t) => {
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

    updateTimeline(projectId, { ...timeline, audio_tracks: updatedTracks, layers: updatedLayers, duration_ms: newDuration })
    setDragState(null)
    setSnapLineMs(null)
    pendingDragDeltaRef.current = 0
  }, [dragState, timeline, calculateMaxDuration, updateTimeline, projectId, setSnapLineMs])

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
    type: 'move' | 'trim-start' | 'trim-end'
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

    // First, collect group_id based clips (this takes priority for group sync)
    // Group sync should work even without multi-selection
    if (clip.group_id) {
      for (const l of timeline.layers) {
        if (l.locked) continue
        for (const c of l.clips) {
          if (c.group_id === clip.group_id && c.id !== clipId) {
            groupVideoClips.push({ clipId: c.id, layerOrTrackId: l.id, initialStartMs: c.start_ms })
          }
        }
      }
      for (const t of timeline.audio_tracks) {
        for (const c of t.clips) {
          if (c.group_id === clip.group_id) {
            groupAudioClips.push({ clipId: c.id, layerOrTrackId: t.id, initialStartMs: c.start_ms })
          }
        }
      }
    }

    // Then, add multi-selected clips that are not already in the group
    const isClickedClipInSelection = selectedVideoClips.has(clipId) || selectedVideoClip?.clipId === clipId

    if (isClickedClipInSelection) {
      const addedVideoIds = new Set(groupVideoClips.map(g => g.clipId))
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

    setVideoDragState({
      type,
      layerId,
      clipId,
      startX: e.clientX,
      initialStartMs: clip.start_ms,
      initialDurationMs: clip.duration_ms,
      initialInPointMs: clip.in_point_ms,
      initialOutPointMs: clip.out_point_ms ?? (clip.in_point_ms + clip.duration_ms * (clip.speed || 1)),
      initialSpeed: clip.speed || 1,
      assetDurationMs,
      currentDeltaMs: 0,
      isResizableClip,
      isVideoAsset: isVideoAsset ?? false,
      groupId: clip.group_id,
      groupVideoClips: groupVideoClips.length > 0 ? groupVideoClips : undefined,
      groupAudioClips: groupAudioClips.length > 0 ? groupAudioClips : undefined,
    })

    if (!e.shiftKey && !selectedVideoClips.has(clipId) && selectedVideoClip?.clipId !== clipId) {
      handleVideoClipSelect(layerId, clipId)
    }
  }, [assets, handleVideoClipSelect, selectedAudioClips, selectedVideoClips, selectedVideoClip, timeline.layers, timeline.audio_tracks])

  const handleVideoClipDragMove = useCallback((e: MouseEvent) => {
    if (!videoDragState) return

    const deltaX = e.clientX - videoDragState.startX
    let deltaMs = Math.round((deltaX / pixelsPerSecond) * 1000)

    if (videoDragState.type === 'move') {
      const draggingClipIds = new Set([videoDragState.clipId])
      videoDragState.groupVideoClips?.forEach(gc => draggingClipIds.add(gc.clipId))
      videoDragState.groupAudioClips?.forEach(gc => draggingClipIds.add(gc.clipId))

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
    } else {
      setSnapLineMs(null)
    }

    pendingVideoDragDeltaRef.current = deltaMs

    if (videoDragRafRef.current === null) {
      videoDragRafRef.current = requestAnimationFrame(() => {
        setVideoDragState(prev => (prev ? { ...prev, currentDeltaMs: pendingVideoDragDeltaRef.current } : null))
        videoDragRafRef.current = null
      })
    }
  }, [videoDragState, pixelsPerSecond, isSnapEnabled, getSnapPoints, findNearestSnapPoint, snapThresholdMs, setSnapLineMs])

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
    const groupVideoClipIds = new Set(videoDragState.groupVideoClips?.map(c => c.clipId) || [])

    const updatedLayers = timeline.layers.map((layer) => {
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
              if (videoDragState.isVideoAsset) {
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
              } else {
                const maxTrim = videoDragState.initialDurationMs - 100
                const minTrim = videoDragState.isResizableClip ? -Infinity : -videoDragState.initialInPointMs
                const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                const newStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
                const effectiveTrim = newStartMs - videoDragState.initialStartMs
                const newInPointMs = videoDragState.isResizableClip ? 0 : videoDragState.initialInPointMs + effectiveTrim
                const newDurationMs = videoDragState.initialDurationMs - effectiveTrim
                const newOutPointMs = newInPointMs + newDurationMs
                return { ...clip, start_ms: newStartMs, in_point_ms: newInPointMs, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
              }
            } else if (videoDragState.type === 'trim-end') {
              if (videoDragState.isVideoAsset) {
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
              } else {
                const maxDuration = videoDragState.isResizableClip ? Infinity : videoDragState.assetDurationMs - videoDragState.initialInPointMs
                const newDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
                const newOutPointMs = videoDragState.initialInPointMs + newDurationMs
                return { ...clip, duration_ms: newDurationMs, out_point_ms: newOutPointMs }
              }
            }
          }

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

    const groupAudioClipIds = new Set(videoDragState.groupAudioClips?.map(c => c.clipId) || [])

    let updatedTracks = timeline.audio_tracks
    if (videoDragState.type === 'move' && groupAudioClipIds.size > 0) {
      updatedTracks = timeline.audio_tracks.map((track) => {
        const hasGroupClips = track.clips.some(c => groupAudioClipIds.has(c.id))
        if (!hasGroupClips) return track

        return {
          ...track,
          clips: track.clips.map((audioClip) => {
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

    const newDuration = calculateMaxDuration(updatedLayers, updatedTracks)

    updateTimeline(projectId, { ...timeline, layers: updatedLayers, audio_tracks: updatedTracks, duration_ms: newDuration })
    setVideoDragState(null)
    setSnapLineMs(null)
    pendingVideoDragDeltaRef.current = 0
  }, [videoDragState, timeline, calculateMaxDuration, updateTimeline, projectId, setSnapLineMs])

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
    handleClipDragStart,
    handleVideoClipDragStart,
  }
}
