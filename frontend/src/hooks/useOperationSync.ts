import { useEffect, useRef, useCallback } from 'react'
import { operationsApi, type OperationHistoryItem } from '@/api/operations'
import { useProjectStore } from '@/store/projectStore'

const POLL_INTERVAL = 3000

interface UseOperationSyncOptions {
  enabled?: boolean
  onRemoteOperation?: (ops: OperationHistoryItem[]) => void
}

export function useOperationSync(
  projectId: string | undefined,
  options: UseOperationSyncOptions = {}
) {
  const { enabled = true, onRemoteOperation } = options
  const currentProject = useProjectStore((s) => s.currentProject)
  const fetchProject = useProjectStore((s) => s.fetchProject)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const operationHistoryRef = useRef<OperationHistoryItem[]>([])

  const poll = useCallback(async () => {
    if (!projectId || !currentProject || currentProject.id !== projectId) return

    // Skip polling if we just made a local change (debounce)
    const lastLocalChangeMs = useProjectStore.getState().lastLocalChangeMs
    const timeSinceLastChange = Date.now() - (lastLocalChangeMs || 0)
    if (timeSinceLastChange < 1500) return

    try {
      const version = currentProject.version ?? 0
      const result = await operationsApi.poll(projectId, version)

      if (result.operations.length > 0) {
        operationHistoryRef.current = [
          ...result.operations,
          ...operationHistoryRef.current,
        ].slice(0, 100)

        onRemoteOperation?.(result.operations)

        // Re-fetch full project to get latest state
        await fetchProject(projectId)
      }
    } catch (error) {
      console.warn('[OperationSync] Poll failed:', error)
    }
  }, [projectId, currentProject, fetchProject, onRemoteOperation])

  useEffect(() => {
    if (!enabled || !projectId) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      return
    }

    intervalRef.current = setInterval(poll, POLL_INTERVAL)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [enabled, projectId, poll])

  return {
    operationHistory: operationHistoryRef.current,
  }
}
