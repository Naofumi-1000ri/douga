import { useEffect, useRef, useCallback } from 'react'
import { doc, onSnapshot, Timestamp } from 'firebase/firestore'
import { db, DEV_MODE } from '@/lib/firebase'
import { useProjectStore } from '@/store/projectStore'

/**
 * Firestore update event structure
 */
interface ProjectUpdateEvent {
  updated_at: Timestamp
  source: 'api' | 'mcp'
  operation?: string
}

/**
 * Hook to sync project changes via Firestore real-time listener.
 *
 * This hook listens to Firestore document changes for the project.
 * When a change is detected (e.g., from MCP tools or API updates),
 * it automatically refreshes the project data from the backend.
 *
 * Firestore collection: project_updates/{projectId}
 *
 * @param projectId - The project ID to subscribe to
 * @param options - Configuration options
 */
export function useProjectSync(
  projectId: string | undefined,
  options: {
    /** Whether the sync should be enabled (default: true) */
    enabled?: boolean
    /** Callback when a sync event is received */
    onSync?: (event: ProjectUpdateEvent) => void
    /** Debounce delay in ms to prevent rapid re-fetches (default: 500) */
    debounceMs?: number
  } = {}
) {
  const { enabled = true, onSync, debounceMs = 500 } = options

  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastUpdateRef = useRef<number>(0)

  const { fetchProject } = useProjectStore()

  /**
   * Handle incoming Firestore update events
   */
  const handleUpdate = useCallback(
    (event: ProjectUpdateEvent) => {
      const eventTime = event.updated_at.toMillis()

      // Ignore if this update is older than what we've already processed
      if (eventTime <= lastUpdateRef.current) {
        return
      }
      lastUpdateRef.current = eventTime

      console.log('[ProjectSync] Update detected:', event.source, event.operation)

      // Call optional callback
      onSync?.(event)

      // Debounce the fetch to prevent rapid re-fetches
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        if (projectId) {
          console.log('[ProjectSync] Refreshing project data:', projectId)
          fetchProject(projectId)
        }
      }, debounceMs)
    },
    [projectId, fetchProject, onSync, debounceMs]
  )

  /**
   * Effect to manage Firestore listener lifecycle
   */
  useEffect(() => {
    // Skip if disabled, no projectId, dev mode, or no Firestore instance
    if (!enabled || !projectId || DEV_MODE || !db) {
      return
    }

    console.log('[ProjectSync] Subscribing to updates for project:', projectId)

    // Subscribe to Firestore document
    const docRef = doc(db, 'project_updates', projectId)
    const unsubscribe = onSnapshot(
      docRef,
      (snapshot) => {
        if (snapshot.exists()) {
          const data = snapshot.data() as ProjectUpdateEvent
          handleUpdate(data)
        }
      },
      (error) => {
        console.error('[ProjectSync] Firestore error:', error)
      }
    )

    // Cleanup on unmount or when dependencies change
    return () => {
      console.log('[ProjectSync] Unsubscribing from project:', projectId)

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      unsubscribe()
    }
  }, [enabled, projectId, handleUpdate])
}
