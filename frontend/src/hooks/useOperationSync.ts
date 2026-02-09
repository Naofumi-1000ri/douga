import { useEffect, useRef, useCallback } from 'react'
import { operationsApi, type OperationHistoryItem, type Operation } from '@/api/operations'
import { useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'

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
  const onRemoteOperationRef = useRef(onRemoteOperation)
  onRemoteOperationRef.current = onRemoteOperation
  const operationHistoryRef = useRef<OperationHistoryItem[]>([])

  // Debug: log hook state on mount and when deps change
  useEffect(() => {
    console.log('[OperationSync] Hook mounted/updated: enabled=', enabled, 'projectId=', projectId)
  }, [enabled, projectId])

  const poll = useCallback(async () => {
    if (!projectId) return

    // Read current state directly from store (avoid stale closures)
    const state = useProjectStore.getState()
    const currentProject = state.currentProject
    if (!currentProject || currentProject.id !== projectId) return

    // Skip polling if we just made a local change (debounce)
    const timeSinceLastChange = Date.now() - (state.lastLocalChangeMs || 0)
    if (timeSinceLastChange < 1500) {
      console.log('[OperationSync] Skipped: recent local change', timeSinceLastChange, 'ms ago')
      return
    }

    try {
      const version = currentProject.version ?? 0
      console.log('[OperationSync] Polling since version', version)
      const result = await operationsApi.poll(projectId, version)
      console.log('[OperationSync] Got', result.operations.length, 'operations, server version:', result.current_version)

      if (result.operations.length > 0) {
        operationHistoryRef.current = [
          ...result.operations,
          ...operationHistoryRef.current,
        ].slice(0, 100)

        onRemoteOperationRef.current?.(result.operations)

        // Filter out own operations (self-filtering)
        const currentUserId = useAuthStore.getState().user?.uid
        const remoteItems = currentUserId
          ? result.operations.filter(item => item.user_id !== currentUserId)
          : result.operations

        if (remoteItems.length > 0) {
          // Extract individual operations from each OperationHistoryItem
          const allOps = remoteItems.flatMap(item =>
            (item.data?.operations as Operation[]) || []
          )

          if (allOps.length > 0) {
            console.log('[OperationSync] Applied', allOps.length, 'remote operations')
            useProjectStore.getState().applyRemoteOps(projectId, result.current_version, allOps)
          } else {
            // No granular operations in data — update version only
            console.log('[OperationSync] No granular ops in remote items, updating version to', result.current_version)
            useProjectStore.getState().applyRemoteOps(projectId, result.current_version, [])
          }
        } else {
          // All operations were our own — just update the version
          console.log('[OperationSync] All operations were local, updating version to', result.current_version)
          useProjectStore.getState().applyRemoteOps(projectId, result.current_version, [])
        }
      }
    } catch (error) {
      console.warn('[OperationSync] Poll failed:', error)
    }
  }, [projectId])

  useEffect(() => {
    console.log('[OperationSync] useEffect: enabled=', enabled, 'projectId=', projectId)
    if (!enabled || !projectId) {
      console.log('[OperationSync] useEffect: NOT starting interval (enabled=' + enabled + ', projectId=' + projectId + ')')
      return
    }

    console.log('[OperationSync] Starting interval, polling every', POLL_INTERVAL, 'ms')
    const id = setInterval(poll, POLL_INTERVAL)

    return () => {
      clearInterval(id)
    }
  }, [enabled, projectId, poll])

  return {
    operationHistory: operationHistoryRef.current,
  }
}
