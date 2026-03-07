export interface EditorLayoutSettings {
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

const EDITOR_LAYOUT_STORAGE_KEY = 'douga-editor-layout'

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

export function loadEditorLayoutSettings(): EditorLayoutSettings {
  try {
    const stored = localStorage.getItem(EDITOR_LAYOUT_STORAGE_KEY)
    if (stored) {
      return { ...DEFAULT_LAYOUT, ...JSON.parse(stored) }
    }
  } catch {
    // Fall back to defaults when the saved payload is invalid or unavailable.
  }

  return DEFAULT_LAYOUT
}

export function saveEditorLayoutSettings(settings: EditorLayoutSettings): void {
  try {
    localStorage.setItem(EDITOR_LAYOUT_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Ignore storage failures such as quota limits.
  }
}
