import { create } from 'zustand'
import { projectsApi } from '@/api/projects'
import { operationsApi, type Operation } from '@/api/operations'
import { sequencesApi, type SequenceDetail } from '@/api/sequences'
import { diffTimeline } from '@/utils/timelineDiff'
import { applyRemoteOperations } from '@/utils/applyRemoteOperations'
import { setEditTokenForClient } from '@/api/client'

export type AIProvider = 'openai' | 'gemini' | 'anthropic'

export interface Project {
  id: string
  name: string
  description: string | null
  status: string
  duration_ms: number
  thumbnail_url: string | null
  created_at: string
  updated_at: string
  is_shared?: boolean
  role?: string
  owner_name?: string
}

export interface ProjectDetail extends Project {
  user_id: string
  width: number
  height: number
  fps: number
  timeline_data: TimelineData
  ai_provider: AIProvider | null
  ai_api_key?: string | null
  version: number
}

export interface ClipGroup {
  id: string
  name: string
  color: string  // Visual identifier for the group
}

export interface Marker {
  id: string
  time_ms: number
  name: string
  color?: string  // Optional marker color (defaults to orange)
}

export interface TimelineData {
  version: string
  duration_ms: number
  layers: Layer[]
  audio_tracks: AudioTrack[]
  groups?: ClipGroup[]  // Optional for backward compatibility
  markers?: Marker[]    // Optional for backward compatibility
}

export type LayerType = 'background' | 'content' | 'avatar' | 'effects' | 'text'

export interface Layer {
  id: string
  name: string
  type?: LayerType  // Optional for backward compatibility
  order: number
  visible: boolean
  locked: boolean
  clips: Clip[]
  color?: string // Optional layer color for identification
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
  name?: string        // Optional name for the shape (displayed on hover)
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
  backgroundOpacity: number // Background opacity (0-1)
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
  speed?: number             // Playback speed multiplier (1.0 = normal, 2.0 = 2x fast)
  freeze_frame_ms?: number   // Freeze frame duration at end of clip (milliseconds)
  group_id?: string | null  // Group this clip belongs to (clips in same group move together)
  keyframes?: Keyframe[]  // Animation keyframes for transform interpolation
  fade_in_ms?: number      // Fade in duration for shapes (opacity 0 to 1)
  fade_out_ms?: number     // Fade out duration for shapes (opacity 1 to 0)
  transform: {
    x: number
    y: number
    width: number | null
    height: number | null
    scale: number
    rotation: number
  }
  crop?: {
    top: number     // Crop from top (0-1, percentage)
    right: number   // Crop from right (0-1, percentage)
    bottom: number  // Crop from bottom (0-1, percentage)
    left: number    // Crop from left (0-1, percentage)
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
  type: 'narration' | 'bgm' | 'se'  // Track type for audio
  volume: number
  muted: boolean
  visible: boolean  // Whether track is visible in preview (default: true)
  ducking?: {
    enabled: boolean
    duck_to: number
    attack_ms: number
    release_ms: number
  }
  clips: AudioClip[]
}

// Volume keyframe for automation (used for ducking, etc.)
export interface VolumeKeyframe {
  time_ms: number  // Relative time within the clip (0 = clip start)
  value: number    // Volume value (0.0 - 1.0)
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
  group_id?: string | null  // Group this clip belongs to (clips in same group move together)
  volume_keyframes?: VolumeKeyframe[]  // Volume automation keyframes
}

export interface HistoryEntry {
  timeline: TimelineData
  label: string
  timestamp: number
}

interface ConflictState {
  isConflicting: boolean
  localTimeline: TimelineData | null
  serverVersion: number | null
}

interface ProjectState {
  projects: Project[]
  currentProject: ProjectDetail | null
  loading: boolean
  error: string | null
  lastLocalChangeMs: number
  conflictState: ConflictState | null
  // Undo/Redo history
  timelineHistory: HistoryEntry[]
  timelineFuture: HistoryEntry[]
  maxHistorySize: number
  historyVersion: number
  // Sequence support
  currentSequence: SequenceDetail | null
  sequenceLoading: boolean
  editToken: string | null

  setEditToken: (token: string | null) => void
  fetchProjects: () => Promise<void>
  fetchProject: (id: string) => Promise<void>
  createProject: (name: string, description?: string) => Promise<Project>
  updateProject: (id: string, data: Partial<ProjectDetail>) => Promise<void>
  deleteProject: (id: string) => Promise<void>
  updateTimeline: (id: string, timeline: TimelineData, labelOrOptions?: string | { label?: string; skipHistory?: boolean }) => Promise<void>
  updateTimelineLocal: (id: string, timeline: TimelineData) => void  // Local only, no API call
  applyRemoteOps: (projectId: string, newVersion: number, operations: Operation[]) => void
  resolveConflict: (action: 'reload' | 'force') => Promise<void>
  undo: (id: string) => Promise<void>
  redo: (id: string) => Promise<void>
  canUndo: () => boolean
  canRedo: () => boolean
  getUndoLabel: () => string | null
  getRedoLabel: () => string | null
  clearHistory: () => void
  // Sequence methods
  fetchSequence: (projectId: string, sequenceId: string) => Promise<void>
  saveSequence: (projectId: string, sequenceId: string, timeline: TimelineData, labelOrOptions?: string | { label?: string; skipHistory?: boolean }) => Promise<void>
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  error: null,
  lastLocalChangeMs: 0,
  conflictState: null,
  timelineHistory: [],
  timelineFuture: [],
  maxHistorySize: 50,
  historyVersion: 0,
  currentSequence: null,
  sequenceLoading: false,
  editToken: null,

  setEditToken: (token: string | null) => {
    setEditTokenForClient(token)
    set({ editToken: token })
  },

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

      // Ensure timeline_data has required structure with defaults
      if (!project.timeline_data || Object.keys(project.timeline_data).length === 0) {
        project.timeline_data = {
          version: '1.0',
          duration_ms: 0,
          layers: [],
          audio_tracks: [],
        }
      }

      // Ensure layers array exists and has defaults
      if (!project.timeline_data.layers) {
        project.timeline_data.layers = []
      }
      project.timeline_data.layers = project.timeline_data.layers.map(layer => ({
        ...layer,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      }))

      // Ensure audio_tracks array exists and has defaults
      if (!project.timeline_data.audio_tracks) {
        project.timeline_data.audio_tracks = []
      }
      project.timeline_data.audio_tracks = project.timeline_data.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      }))

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
    // Set lastLocalChangeMs BEFORE API call to prevent ProjectSync from refetching
    set({ lastLocalChangeMs: Date.now() })
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
          visible: track.visible ?? true,
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

  updateTimeline: async (id: string, timeline: TimelineData, labelOrOptions?: string | { label?: string; skipHistory?: boolean }) => {
    const state = get()

    // If in sequence mode, route through saveSequence instead
    if (state.currentSequence) {
      return get().saveSequence(id, state.currentSequence.id, timeline, labelOrOptions)
    }

    const currentTimeline = state.currentProject?.timeline_data

    // Parse options: support both string label and options object for backwards compatibility
    const options = typeof labelOrOptions === 'string'
      ? { label: labelOrOptions, skipHistory: false }
      : { label: labelOrOptions?.label, skipHistory: labelOrOptions?.skipHistory ?? false }

    // Save current state to history before update (deep copy to prevent reference issues)
    if (!options.skipHistory && currentTimeline && state.currentProject?.id === id) {
      const timelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
      const entry: HistoryEntry = {
        timeline: timelineCopy,
        label: options.label ?? 'タイムライン更新',
        timestamp: Date.now(),
      }
      const newHistory = [...state.timelineHistory, entry]
      // Limit history size
      if (newHistory.length > state.maxHistorySize) {
        newHistory.shift()
      }
      set({ timelineHistory: newHistory, timelineFuture: [] })
    }

    // Normalize layers with default values for visible/locked
    const normalizedTimeline: TimelineData = {
      ...timeline,
      layers: timeline.layers.map((layer, index) => ({
        ...layer,
        order: timeline.layers.length - 1 - index,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: timeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      })),
    }

    // OPTIMISTIC UPDATE: Update store immediately to prevent flicker
    set((state) => ({
      currentProject: state.currentProject?.id === id
        ? { ...state.currentProject, timeline_data: normalizedTimeline }
        : state.currentProject,
      lastLocalChangeMs: Date.now(),
    }))

    try {
      // Compute operations diff
      const ops = diffTimeline(currentTimeline!, normalizedTimeline)
      if (ops.length === 0) return // No real changes

      const currentVersion = state.currentProject?.version ?? 0
      const result = await operationsApi.apply(id, currentVersion, ops)

      // Update version and timeline from server response
      set((state) => ({
        currentProject: state.currentProject?.id === id
          ? {
              ...state.currentProject,
              timeline_data: result.timeline_data as unknown as TimelineData,
              version: result.version,
            }
          : state.currentProject,
        conflictState: null,
      }))
    } catch (error) {
      // 409 Conflict handling
      const axiosError = error as { response?: { status?: number; data?: { detail?: { code?: string; server_version?: number } } } }
      if (axiosError.response?.status === 409 && axiosError.response?.data?.detail?.code === 'CONCURRENT_MODIFICATION') {
        set({
          conflictState: {
            isConflicting: true,
            localTimeline: normalizedTimeline,
            serverVersion: axiosError.response.data.detail.server_version ?? null,
          }
        })
        return
      }
      set({ error: (error as Error).message })
      throw error
    }
  },

  // Local-only update (no API call) - for use during drag operations
  updateTimelineLocal: (id: string, timeline: TimelineData) => {
    // Normalize layers with default values for visible/locked
    const normalizedLayers = timeline.layers.map((layer, index) => ({
      ...layer,
      order: timeline.layers.length - 1 - index,
      visible: layer.visible ?? true,
      locked: layer.locked ?? false,
    }))

    const normalizedTimeline: TimelineData = {
      ...timeline,
      layers: normalizedLayers,
    }

    // Update store only (no API call)
    // Must update currentSequence (primary source for sequence-based editing)
    set((state) => ({
      currentSequence: state.currentSequence
        ? { ...state.currentSequence, timeline_data: normalizedTimeline }
        : state.currentSequence,
      currentProject: state.currentProject?.id === id
        ? { ...state.currentProject, timeline_data: normalizedTimeline }
        : state.currentProject,
      lastLocalChangeMs: Date.now(),
    }))
  },

  // Apply remote operations granularly (no full project reload)
  applyRemoteOps: (projectId: string, newVersion: number, operations: Operation[]) => {
    const state = get()
    if (!state.currentProject || state.currentProject.id !== projectId) return

    const currentTimeline = state.currentProject.timeline_data
    if (!currentTimeline) return

    if (operations.length > 0) {
      const updatedTimeline = applyRemoteOperations(currentTimeline, operations)
      set({
        currentProject: {
          ...state.currentProject,
          timeline_data: updatedTimeline,
          version: newVersion,
        },
        // Preserve: timelineHistory, timelineFuture (Undo/Redo)
        // Do NOT update lastLocalChangeMs (this is a remote change, should not block polling)
      })
    } else {
      // No operations to apply, just update version
      set({
        currentProject: {
          ...state.currentProject,
          version: newVersion,
        },
      })
    }
  },

  resolveConflict: async (action: 'reload' | 'force') => {
    const state = get()
    const projectId = state.currentProject?.id
    if (!projectId) return

    if (action === 'reload') {
      await get().fetchProject(projectId)
      set({ conflictState: null, timelineHistory: [], timelineFuture: [] })
    } else if (action === 'force') {
      const localTimeline = state.conflictState?.localTimeline
      if (localTimeline) {
        const updated = await projectsApi.updateTimeline(projectId, localTimeline, undefined, true)
        set((state) => ({
          currentProject: state.currentProject?.id === projectId
            ? { ...state.currentProject, duration_ms: updated.duration_ms, version: updated.version }
            : state.currentProject,
          conflictState: null,
        }))
      }
    }
  },

  undo: async (id: string) => {
    const state = get()
    if (state.timelineHistory.length === 0) return

    // Use sequence if available, fall back to project
    const useSequenceMode = !!state.currentSequence
    const currentTimeline = useSequenceMode
      ? state.currentSequence!.timeline_data
      : state.currentProject?.timeline_data
    if (!currentTimeline) return

    const previousEntry = state.timelineHistory[state.timelineHistory.length - 1]
    const newHistory = state.timelineHistory.slice(0, -1)
    // Deep copy current timeline before saving to future
    const currentTimelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
    const futureEntry: HistoryEntry = {
      timeline: currentTimelineCopy,
      label: previousEntry.label,
      timestamp: Date.now(),
    }
    const newFuture = [futureEntry, ...state.timelineFuture]

    // Normalize layers with default values
    const normalizedPreviousTimeline: TimelineData = {
      ...previousEntry.timeline,
      layers: previousEntry.timeline.layers.map((layer, index) => ({
        ...layer,
        order: previousEntry.timeline.layers.length - 1 - index,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: previousEntry.timeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      })),
    }

    try {
      if (useSequenceMode) {
        const seq = state.currentSequence!
        const result = await sequencesApi.update(id, seq.id, normalizedPreviousTimeline, seq.version)
        set({
          currentSequence: {
            ...seq,
            timeline_data: normalizedPreviousTimeline,
            version: result.version,
            duration_ms: result.duration_ms,
          },
          timelineHistory: newHistory,
          timelineFuture: newFuture,
          historyVersion: state.historyVersion + 1,
        })
      } else {
        if (!state.currentProject) return
        const currentVersion = state.currentProject.version
        const updated = await projectsApi.updateTimeline(id, normalizedPreviousTimeline, currentVersion)
        set({
          currentProject: {
            ...state.currentProject,
            timeline_data: normalizedPreviousTimeline,
            duration_ms: updated.duration_ms,
            version: updated.version,
          },
          timelineHistory: newHistory,
          timelineFuture: newFuture,
          historyVersion: state.historyVersion + 1,
        })
      }
    } catch (error) {
      const axiosError = error as { response?: { status?: number; data?: { detail?: { code?: string; server_version?: number } } } }
      if (axiosError.response?.status === 409) {
        set({
          conflictState: {
            isConflicting: true,
            localTimeline: normalizedPreviousTimeline,
            serverVersion: axiosError.response?.data?.detail?.server_version ?? null,
          },
          timelineHistory: state.timelineHistory,
          timelineFuture: state.timelineFuture,
        })
        return
      }
      set({ error: (error as Error).message })
      throw error
    }
  },

  redo: async (id: string) => {
    const state = get()
    if (state.timelineFuture.length === 0) return

    // Use sequence if available, fall back to project
    const useSequenceMode = !!state.currentSequence
    const currentTimeline = useSequenceMode
      ? state.currentSequence!.timeline_data
      : state.currentProject?.timeline_data
    if (!currentTimeline) return

    const nextEntry = state.timelineFuture[0]
    const newFuture = state.timelineFuture.slice(1)
    // Deep copy current timeline before saving to history
    const currentTimelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
    const historyEntry: HistoryEntry = {
      timeline: currentTimelineCopy,
      label: nextEntry.label,
      timestamp: Date.now(),
    }
    const newHistory = [...state.timelineHistory, historyEntry]

    // Normalize layers with default values
    const normalizedNextTimeline: TimelineData = {
      ...nextEntry.timeline,
      layers: nextEntry.timeline.layers.map((layer, index) => ({
        ...layer,
        order: nextEntry.timeline.layers.length - 1 - index,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: nextEntry.timeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      })),
    }

    try {
      if (useSequenceMode) {
        const seq = state.currentSequence!
        const result = await sequencesApi.update(id, seq.id, normalizedNextTimeline, seq.version)
        set({
          currentSequence: {
            ...seq,
            timeline_data: normalizedNextTimeline,
            version: result.version,
            duration_ms: result.duration_ms,
          },
          timelineHistory: newHistory,
          timelineFuture: newFuture,
          historyVersion: state.historyVersion + 1,
        })
      } else {
        if (!state.currentProject) return
        const currentVersion = state.currentProject.version
        const updated = await projectsApi.updateTimeline(id, normalizedNextTimeline, currentVersion)
        set({
          currentProject: {
            ...state.currentProject,
            timeline_data: normalizedNextTimeline,
            duration_ms: updated.duration_ms,
            version: updated.version,
          },
          timelineHistory: newHistory,
          timelineFuture: newFuture,
          historyVersion: state.historyVersion + 1,
        })
      }
    } catch (error) {
      const axiosError = error as { response?: { status?: number; data?: { detail?: { code?: string; server_version?: number } } } }
      if (axiosError.response?.status === 409) {
        set({
          conflictState: {
            isConflicting: true,
            localTimeline: normalizedNextTimeline,
            serverVersion: axiosError.response?.data?.detail?.server_version ?? null,
          },
          timelineHistory: state.timelineHistory,
          timelineFuture: state.timelineFuture,
        })
        return
      }
      set({ error: (error as Error).message })
      throw error
    }
  },

  canUndo: () => get().timelineHistory.length > 0,
  canRedo: () => get().timelineFuture.length > 0,
  getUndoLabel: () => {
    const history = get().timelineHistory
    return history.length > 0 ? history[history.length - 1].label : null
  },
  getRedoLabel: () => {
    const future = get().timelineFuture
    return future.length > 0 ? future[0].label : null
  },

  clearHistory: () => set({ timelineHistory: [], timelineFuture: [] }),

  fetchSequence: async (projectId: string, sequenceId: string) => {
    set({ sequenceLoading: true, error: null })
    try {
      const result = await sequencesApi.get(projectId, sequenceId)

      // Ensure timeline_data has required structure with defaults
      if (!result.timeline_data || Object.keys(result.timeline_data).length === 0) {
        result.timeline_data = {
          version: '1.0',
          duration_ms: 0,
          layers: [],
          audio_tracks: [],
        }
      }

      // Ensure layers array exists and has defaults
      if (!result.timeline_data.layers) {
        result.timeline_data.layers = []
      }
      result.timeline_data.layers = result.timeline_data.layers.map(layer => ({
        ...layer,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      }))

      // Ensure audio_tracks array exists and has defaults
      if (!result.timeline_data.audio_tracks) {
        result.timeline_data.audio_tracks = []
      }
      result.timeline_data.audio_tracks = result.timeline_data.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      }))

      set({ currentSequence: result, sequenceLoading: false })
    } catch (error) {
      set({ error: (error as Error).message, sequenceLoading: false })
    }
  },

  saveSequence: async (projectId: string, sequenceId: string, timeline: TimelineData, labelOrOptions?: string | { label?: string; skipHistory?: boolean }) => {
    const state = get()
    const currentTimeline = state.currentSequence?.timeline_data

    const options = typeof labelOrOptions === 'string'
      ? { label: labelOrOptions, skipHistory: false }
      : { label: labelOrOptions?.label, skipHistory: labelOrOptions?.skipHistory ?? false }

    // Save current state to history before update
    if (!options.skipHistory && currentTimeline && state.currentSequence?.id === sequenceId) {
      const timelineCopy = JSON.parse(JSON.stringify(currentTimeline)) as TimelineData
      const entry: HistoryEntry = {
        timeline: timelineCopy,
        label: options.label ?? 'タイムライン更新',
        timestamp: Date.now(),
      }
      const newHistory = [...state.timelineHistory, entry]
      if (newHistory.length > state.maxHistorySize) newHistory.shift()
      set({ timelineHistory: newHistory, timelineFuture: [] })
    }

    // Normalize
    const normalizedTimeline: TimelineData = {
      ...timeline,
      layers: timeline.layers.map((layer, index) => ({
        ...layer,
        order: timeline.layers.length - 1 - index,
        visible: layer.visible ?? true,
        locked: layer.locked ?? false,
      })),
      audio_tracks: timeline.audio_tracks.map(track => ({
        ...track,
        muted: track.muted ?? false,
        visible: track.visible ?? true,
      })),
    }

    // Optimistic update
    set((state) => ({
      currentSequence: state.currentSequence?.id === sequenceId
        ? { ...state.currentSequence, timeline_data: normalizedTimeline }
        : state.currentSequence,
      lastLocalChangeMs: Date.now(),
    }))

    try {
      const currentVersion = state.currentSequence?.version ?? 0
      const result = await sequencesApi.update(projectId, sequenceId, normalizedTimeline, currentVersion)
      set((state) => ({
        currentSequence: state.currentSequence?.id === sequenceId
          ? { ...state.currentSequence, version: result.version, duration_ms: result.duration_ms }
          : state.currentSequence,
      }))
    } catch (error) {
      set({ error: (error as Error).message })
      throw error
    }
  },
}))
