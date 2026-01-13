import { create } from 'zustand'
import { projectsApi } from '@/api/projects'

export interface Project {
  id: string
  name: string
  description: string | null
  status: string
  duration_ms: number
  thumbnail_url: string | null
  created_at: string
  updated_at: string
}

export interface ProjectDetail extends Project {
  user_id: string
  width: number
  height: number
  fps: number
  timeline_data: TimelineData
}

export interface ClipGroup {
  id: string
  name: string
  color: string  // Visual identifier for the group
}

export interface TimelineData {
  version: string
  duration_ms: number
  layers: Layer[]
  audio_tracks: AudioTrack[]
  groups?: ClipGroup[]  // Optional for backward compatibility
}

export interface Layer {
  id: string
  name: string
  order: number
  visible: boolean
  locked: boolean
  clips: Clip[]
}

export interface Keyframe {
  time_ms: number  // Time relative to clip start (0 = clip start)
  transform: {
    x: number
    y: number
    scale: number
    rotation: number
  }
  opacity?: number
}

// Shape types for drawing primitives
export type ShapeType = 'rectangle' | 'circle' | 'line'

export interface Shape {
  type: ShapeType
  width: number        // Width for rectangle, diameter for circle, length for line
  height: number       // Height for rectangle, same as width for circle, thickness for line
  fillColor: string    // Fill color (hex or rgba)
  strokeColor: string  // Stroke/border color
  strokeWidth: number  // Stroke/border width in pixels
  filled: boolean      // Whether to fill the shape
}

// Text style for text clips
export interface TextStyle {
  fontFamily: string       // Font family name
  fontSize: number         // Font size in pixels
  fontWeight: 'normal' | 'bold'
  fontStyle: 'normal' | 'italic'
  color: string            // Text color (hex or rgba)
  backgroundColor: string  // Background color (hex or rgba, can be transparent)
  textAlign: 'left' | 'center' | 'right'
  verticalAlign: 'top' | 'middle' | 'bottom'
  lineHeight: number       // Line height multiplier (e.g., 1.2)
  letterSpacing: number    // Letter spacing in pixels
  strokeColor: string      // Text stroke/outline color
  strokeWidth: number      // Text stroke width in pixels
}

export interface Clip {
  id: string
  asset_id: string | null  // null for shape clips or text clips
  shape?: Shape            // Shape data (if this is a shape clip)
  text_content?: string    // Text content (if this is a text clip)
  text_style?: TextStyle   // Text styling (if this is a text clip)
  start_ms: number
  duration_ms: number
  in_point_ms: number
  out_point_ms: number | null
  linked_audio_clip_id?: string | null  // Link to an audio clip (moves together) - legacy, use group_id instead
  linked_audio_track_id?: string | null // The track containing the linked audio clip - legacy
  group_id?: string | null  // Group this clip belongs to (clips in same group move together)
  keyframes?: Keyframe[]  // Animation keyframes for transform interpolation
  transform: {
    x: number
    y: number
    width: number | null
    height: number | null
    scale: number
    rotation: number
  }
  effects: {
    chroma_key?: {
      enabled: boolean
      color: string
      similarity: number
      blend: number
    }
    opacity: number
    fade_in_ms?: number   // Fade in duration in milliseconds
    fade_out_ms?: number  // Fade out duration in milliseconds
  }
}

export interface AudioTrack {
  id: string
  name: string
  type: 'narration' | 'bgm' | 'se' | 'video'  // 'video' for audio extracted from video
  volume: number
  muted: boolean
  linkedVideoLayerId?: string  // If set, this track is linked to a video layer and renders below it
  ducking?: {
    enabled: boolean
    duck_to: number
    attack_ms: number
    release_ms: number
  }
  clips: AudioClip[]
}

export interface AudioClip {
  id: string
  asset_id: string
  start_ms: number
  duration_ms: number
  in_point_ms: number
  out_point_ms: number | null
  volume: number
  fade_in_ms: number
  fade_out_ms: number
  linked_video_clip_id?: string | null  // Link to a video clip (moves together) - legacy, use group_id instead
  linked_video_layer_id?: string | null // The layer containing the linked video clip - legacy
  group_id?: string | null  // Group this clip belongs to (clips in same group move together)
}

interface ProjectState {
  projects: Project[]
  currentProject: ProjectDetail | null
  loading: boolean
  error: string | null
  // Undo/Redo history
  timelineHistory: TimelineData[]
  timelineFuture: TimelineData[]
  maxHistorySize: number

  fetchProjects: () => Promise<void>
  fetchProject: (id: string) => Promise<void>
  createProject: (name: string, description?: string) => Promise<Project>
  updateProject: (id: string, data: Partial<ProjectDetail>) => Promise<void>
  deleteProject: (id: string) => Promise<void>
  updateTimeline: (id: string, timeline: TimelineData) => Promise<void>
  updateTimelineLocal: (id: string, timeline: TimelineData) => void  // Local only, no API call
  undo: (id: string) => Promise<void>
  redo: (id: string) => Promise<void>
  canUndo: () => boolean
  canRedo: () => boolean
  clearHistory: () => void
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  error: null,
  timelineHistory: [],
  timelineFuture: [],
  maxHistorySize: 50,

  fetchProjects: async () => {
    set({ loading: true, error: null })
    try {
      const projects = await projectsApi.list()
      set({ projects, loading: false })
    } catch (error) {
      set({ error: (error as Error).message, loading: false })
    }
  },

  fetchProject: async (id: string) => {
    set({ loading: true, error: null })
    try {
      const project = await projectsApi.get(id)
      // Ensure layers have visible/locked properties with defaults
      if (project.timeline_data?.layers) {
        project.timeline_data.layers = project.timeline_data.layers.map(layer => ({
          ...layer,
          visible: layer.visible ?? true,
          locked: layer.locked ?? false,
        }))
      }
      // Ensure audio tracks have muted property with default
      if (project.timeline_data?.audio_tracks) {
        project.timeline_data.audio_tracks = project.timeline_data.audio_tracks.map(track => ({
          ...track,
          muted: track.muted ?? false,
        }))
      }
      set({ currentProject: project, loading: false })
    } catch (error) {
      set({ error: (error as Error).message, loading: false })
    }
  },

  createProject: async (name: string, description?: string) => {
    set({ loading: true, error: null })
    try {
      const project = await projectsApi.create({ name, description })
      set((state) => ({
        projects: [project, ...state.projects],
        loading: false,
      }))
      return project
    } catch (error) {
      set({ error: (error as Error).message, loading: false })
      throw error
    }
  },

  updateProject: async (id: string, data: Partial<ProjectDetail>) => {
    try {
      const updated = await projectsApi.update(id, data)
      // Normalize layers with default values for visible/locked
      if (updated.timeline_data?.layers) {
        updated.timeline_data.layers = updated.timeline_data.layers.map(layer => ({
          ...layer,
          visible: layer.visible ?? true,
          locked: layer.locked ?? false,
        }))
      }
      if (updated.timeline_data?.audio_tracks) {
        updated.timeline_data.audio_tracks = updated.timeline_data.audio_tracks.map(track => ({
          ...track,
          muted: track.muted ?? false,
        }))
      }
      set((state) => ({
        currentProject: state.currentProject?.id === id ? updated : state.currentProject,
        projects: state.projects.map((p) => (p.id === id ? { ...p, ...updated } : p)),
      }))
    } catch (error) {
      set({ error: (error as Error).message })
      throw error
    }
  },

  deleteProject: async (id: string) => {
    try {
      await projectsApi.delete(id)
      set((state) => ({
        projects: state.projects.filter((p) => p.id !== id),
        currentProject: state.currentProject?.id === id ? null : state.currentProject,
      }))
    } catch (error) {
      set({ error: (error as Error).message })
      throw error
    }
  },

  updateTimeline: async (id: string, timeline: TimelineData) => {
    const state = get()
    const currentTimeline = state.currentProject?.timeline_data

    // Save current state to history before update (deep copy to prevent reference issues)
    if (currentTimeline && state.currentProject?.id === id) {
      const timelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
      const newHistory = [...state.timelineHistory, timelineCopy]
      // Limit history size
      if (newHistory.length > state.maxHistorySize) {
        newHistory.shift()
      }
      set({ timelineHistory: newHistory, timelineFuture: [] })
    }

    // Normalize layers with default values for visible/locked
    const normalizedTimeline: TimelineData = {
      ...timeline,
      layers: timeline.layers.map(layer => ({
        ...layer,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: timeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
      })),
    }

    // OPTIMISTIC UPDATE: Update store immediately to prevent flicker
    set((state) => ({
      currentProject: state.currentProject?.id === id
        ? { ...state.currentProject, timeline_data: normalizedTimeline }
        : state.currentProject,
    }))

    try {
      // Then sync to backend (in background)
      const updated = await projectsApi.updateTimeline(id, normalizedTimeline)
      // Update duration from server response
      set((state) => ({
        currentProject: state.currentProject?.id === id
          ? { ...state.currentProject, duration_ms: updated.duration_ms }
          : state.currentProject,
      }))
    } catch (error) {
      // On error, we could rollback but for now just log
      // The optimistic update already happened, so UI stays consistent
      set({ error: (error as Error).message })
      throw error
    }
  },

  // Local-only update (no API call) - for use during drag operations
  updateTimelineLocal: (id: string, timeline: TimelineData) => {
    // Normalize layers with default values for visible/locked
    const normalizedLayers = timeline.layers.map(layer => ({
      ...layer,
      visible: layer.visible ?? true,
      locked: layer.locked ?? false,
    }))

    const normalizedTimeline: TimelineData = {
      ...timeline,
      layers: normalizedLayers,
    }

    // Update store only (no API call)
    set((state) => ({
      currentProject: state.currentProject?.id === id
        ? { ...state.currentProject, timeline_data: normalizedTimeline }
        : state.currentProject,
    }))
  },

  undo: async (id: string) => {
    const state = get()
    if (state.timelineHistory.length === 0 || !state.currentProject) return

    const currentTimeline = state.currentProject.timeline_data
    const previousTimeline = state.timelineHistory[state.timelineHistory.length - 1]
    const newHistory = state.timelineHistory.slice(0, -1)
    // Deep copy current timeline before saving to future
    const currentTimelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
    const newFuture = [currentTimelineCopy, ...state.timelineFuture]

    // Normalize layers with default values
    const normalizedPreviousTimeline: TimelineData = {
      ...previousTimeline,
      layers: previousTimeline.layers.map(layer => ({
        ...layer,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: previousTimeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
      })),
    }

    try {
      const updated = await projectsApi.updateTimeline(id, normalizedPreviousTimeline)
      set({
        currentProject: {
          ...state.currentProject,
          timeline_data: normalizedPreviousTimeline,
          duration_ms: updated.duration_ms,
        },
        timelineHistory: newHistory,
        timelineFuture: newFuture,
      })
    } catch (error) {
      set({ error: (error as Error).message })
      throw error
    }
  },

  redo: async (id: string) => {
    const state = get()
    if (state.timelineFuture.length === 0 || !state.currentProject) return

    const currentTimeline = state.currentProject.timeline_data
    const nextTimeline = state.timelineFuture[0]
    const newFuture = state.timelineFuture.slice(1)
    // Deep copy current timeline before saving to history
    const currentTimelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
    const newHistory = [...state.timelineHistory, currentTimelineCopy]

    // Normalize layers with default values
    const normalizedNextTimeline: TimelineData = {
      ...nextTimeline,
      layers: nextTimeline.layers.map(layer => ({
        ...layer,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: nextTimeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
      })),
    }

    try {
      const updated = await projectsApi.updateTimeline(id, normalizedNextTimeline)
      set({
        currentProject: {
          ...state.currentProject,
          timeline_data: normalizedNextTimeline,
          duration_ms: updated.duration_ms,
        },
        timelineHistory: newHistory,
        timelineFuture: newFuture,
      })
    } catch (error) {
      set({ error: (error as Error).message })
      throw error
    }
  },

  canUndo: () => get().timelineHistory.length > 0,
  canRedo: () => get().timelineFuture.length > 0,

  clearHistory: () => set({ timelineHistory: [], timelineFuture: [] }),
}))
