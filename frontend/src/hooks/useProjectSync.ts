import { useEffect, useRef, useCallback, useState } from 'react'
import { useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'

/**
 * SSE Event data structure from the backend
 */
interface SSEEvent {
  type: string
  project_id: string
  timestamp: string
  data?: {
    source?: string
    operation?: string
  }
}

/**
 * Parse SSE stream data
 */
function parseSSEData(chunk: string): SSEEvent | null {
  const lines = chunk.split('\n')
  let data = ''

  for (const line of lines) {
    if (line.startsWith('data:')) {
      data = line.slice(5).trim()
    }
  }

  if (data) {
    try {
      return JSON.parse(data) as SSEEvent
    } catch {
      return null
    }
  }
  return null
}

/**
 * Hook to sync project changes via Server-Sent Events (SSE).
 *
 * This hook establishes an SSE connection to the backend and listens for
 * project update events. When a change is detected (e.g., from MCP tools),
 * it automatically refreshes the project data.
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
    onSync?: (event: SSEEvent) => void
    /** Debounce delay in ms to prevent rapid re-fetches (default: 500) */
    debounceMs?: number
  } = {}
) {
  const { enabled = true, onSync, debounceMs = 500 } = options

  const abortControllerRef = useRef<AbortController | null>(null)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastEventTimestampRef = useRef<string | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [isConnected, setIsConnected] = useState(false)

  const { fetchProject } = useProjectStore()
  const { token } = useAuthStore()

  /**
   * Handle incoming SSE events
   */
  const handleEvent = useCallback(
    (event: SSEEvent) => {
      // Ignore duplicate events (same timestamp)
      if (event.timestamp === lastEventTimestampRef.current) {
        return
      }
      lastEventTimestampRef.current = event.timestamp

      console.log('[SSE] Received event:', event)

      // Call optional callback
      onSync?.(event)

      // Debounce the fetch to prevent rapid re-fetches
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        if (projectId) {
          console.log('[SSE] Refreshing project data:', projectId)
          fetchProject(projectId)
        }
      }, debounceMs)
    },
    [projectId, fetchProject, onSync, debounceMs]
  )

  /**
   * Connect to SSE endpoint using fetch
   */
  const connect = useCallback(async () => {
    if (!projectId || !token) {
      return
    }

    // Cancel any existing connection
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    const apiBaseUrl = import.meta.env.VITE_API_URL || ''
    const sseUrl = `${apiBaseUrl}/api/projects/${projectId}/events`

    console.log('[SSE] Connecting to:', sseUrl)

    try {
      const response = await fetch(sseUrl, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`SSE connection failed: ${response.status}`)
      }

      if (!response.body) {
        throw new Error('SSE response body is null')
      }

      setIsConnected(true)
      console.log('[SSE] Connected to project:', projectId)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          console.log('[SSE] Stream ended')
          break
        }

        buffer += decoder.decode(value, { stream: true })

        // Split by double newline (SSE event separator)
        const events = buffer.split('\n\n')
        buffer = events.pop() || '' // Keep incomplete event in buffer

        for (const eventStr of events) {
          if (!eventStr.trim()) continue

          const event = parseSSEData(eventStr)
          if (event && event.type !== 'connected') {
            handleEvent(event)
          }
        }
      }
    } catch (error) {
      if ((error as Error).name === 'AbortError') {
        console.log('[SSE] Connection aborted')
        return
      }

      console.error('[SSE] Connection error:', error)
      setIsConnected(false)

      // Attempt to reconnect after a delay
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }

      reconnectTimeoutRef.current = setTimeout(() => {
        console.log('[SSE] Attempting to reconnect...')
        connect()
      }, 5000) // Reconnect after 5 seconds
    } finally {
      setIsConnected(false)
    }
  }, [projectId, token, handleEvent])

  /**
   * Effect to manage SSE connection lifecycle
   */
  useEffect(() => {
    if (!enabled || !projectId || !token) {
      return
    }

    connect()

    // Cleanup on unmount or when dependencies change
    return () => {
      console.log('[SSE] Cleaning up connection for project:', projectId)

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }

      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
        abortControllerRef.current = null
      }

      setIsConnected(false)
    }
  }, [enabled, projectId, token, connect])

  /**
   * Manual reconnect function
   */
  const reconnect = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    connect()
  }, [connect])

  return {
    reconnect,
    isConnected,
  }
}
