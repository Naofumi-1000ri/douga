import { create } from 'zustand'

// Activity event types
export type ActivityEventType =
  | 'clip.add'
  | 'clip.move'
  | 'clip.delete'
  | 'clip.trim'
  | 'text.add'
  | 'text.update'
  | 'layer.add'
  | 'layer.delete'
  | 'track.add'
  | 'track.delete'
  | 'marker.add'
  | 'marker.delete'
  | 'project.update'

// Activity event interface
export interface ActivityEvent {
  id: string
  timestamp: number
  actor: string  // User name or AI name
  actorType: 'user' | 'ai'
  eventType: ActivityEventType
  details: string  // Human-readable description
  target?: string  // Target element name (e.g., clip name, layer name)
  targetLocation?: string  // Location info (e.g., "Track1 at 00:05.000")
}

// Activity settings stored per project
export interface ActivitySettings {
  userName: string
  aiName: string
}

// Format time in mm:ss.SSS format
export function formatTimeMs(ms: number): string {
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  const milliseconds = Math.floor(ms % 1000)
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}

// Generate unique ID for events
function generateEventId(): string {
  return `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
}

interface ActivityState {
  events: ActivityEvent[]
  maxEvents: number
  settings: ActivitySettings
  isPanelOpen: boolean

  // Actions
  addEvent: (event: Omit<ActivityEvent, 'id' | 'timestamp'>) => void
  clearEvents: () => void
  setSettings: (settings: Partial<ActivitySettings>) => void
  togglePanel: () => void
  setPanelOpen: (isOpen: boolean) => void
}

// Default settings
const DEFAULT_SETTINGS: ActivitySettings = {
  userName: 'User',
  aiName: 'AI',
}

// Load settings from localStorage
function loadSettings(): ActivitySettings {
  try {
    const saved = localStorage.getItem('activity-settings')
    if (saved) {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) }
    }
  } catch (e) {
    console.error('Failed to load activity settings:', e)
  }
  return DEFAULT_SETTINGS
}

// Save settings to localStorage
function saveSettings(settings: ActivitySettings): void {
  try {
    localStorage.setItem('activity-settings', JSON.stringify(settings))
  } catch (e) {
    console.error('Failed to save activity settings:', e)
  }
}

export const useActivityStore = create<ActivityState>((set) => ({
  events: [],
  maxEvents: 100,
  settings: loadSettings(),
  isPanelOpen: false,

  addEvent: (eventData) => {
    const event: ActivityEvent = {
      ...eventData,
      id: generateEventId(),
      timestamp: Date.now(),
    }

    set((state) => {
      const newEvents = [event, ...state.events]
      // Limit the number of events
      if (newEvents.length > state.maxEvents) {
        newEvents.pop()
      }
      return { events: newEvents }
    })
  },

  clearEvents: () => {
    set({ events: [] })
  },

  setSettings: (newSettings) => {
    set((state) => {
      const settings = { ...state.settings, ...newSettings }
      saveSettings(settings)
      return { settings }
    })
  },

  togglePanel: () => {
    set((state) => ({ isPanelOpen: !state.isPanelOpen }))
  },

  setPanelOpen: (isOpen) => {
    set({ isPanelOpen: isOpen })
  },
}))

// Helper hook to log user activities
export function useLogActivity() {
  const { addEvent, settings } = useActivityStore()

  return {
    logUserActivity: (
      eventType: ActivityEventType,
      details: string,
      options?: { target?: string; targetLocation?: string }
    ) => {
      addEvent({
        actor: settings.userName,
        actorType: 'user',
        eventType,
        details,
        target: options?.target,
        targetLocation: options?.targetLocation,
      })
    },

    logAIActivity: (
      eventType: ActivityEventType,
      details: string,
      options?: { target?: string; targetLocation?: string }
    ) => {
      addEvent({
        actor: settings.aiName,
        actorType: 'ai',
        eventType,
        details,
        target: options?.target,
        targetLocation: options?.targetLocation,
      })
    },
  }
}
