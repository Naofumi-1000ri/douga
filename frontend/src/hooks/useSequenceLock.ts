import { useState, useEffect, useRef, useCallback } from 'react'
import { sequencesApi, type LockResponse } from '@/api/sequences'

const HEARTBEAT_INTERVAL = 30_000 // 30 seconds
const RETRY_LOCK_INTERVAL = 15_000 // 15 seconds - poll to re-acquire when read-only

interface UseSequenceLockResult {
  isLocked: boolean        // Is someone holding the lock?
  isLockedByMe: boolean    // Am I the lock holder?
  isReadOnly: boolean      // !isLockedByMe
  lockHolder: string | null // Lock holder's name
  acquireLock: () => Promise<boolean>
  releaseLock: () => Promise<void>
}

export function useSequenceLock(
  projectId: string | undefined,
  sequenceId: string | undefined
): UseSequenceLockResult {
  const [lockState, setLockState] = useState<LockResponse | null>(null)
  const [isLockedByMe, setIsLockedByMe] = useState(false)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const retryRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isLockedByMeRef = useRef(false)

  // Keep ref in sync with state
  isLockedByMeRef.current = isLockedByMe

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
    }
  }, [])

  const stopRetry = useCallback(() => {
    if (retryRef.current) {
      clearInterval(retryRef.current)
      retryRef.current = null
    }
  }, [])

  const startHeartbeat = useCallback((pid: string, sid: string) => {
    stopHeartbeat()
    stopRetry()
    heartbeatRef.current = setInterval(async () => {
      try {
        const hb = await sequencesApi.heartbeat(pid, sid)
        setLockState(hb)
        if (!hb.locked) {
          setIsLockedByMe(false)
          stopHeartbeat()
        }
      } catch {
        setIsLockedByMe(false)
        stopHeartbeat()
      }
    }, HEARTBEAT_INTERVAL)
  }, [stopHeartbeat, stopRetry])

  const startRetryPolling = useCallback((pid: string, sid: string) => {
    stopRetry()
    stopHeartbeat()
    retryRef.current = setInterval(async () => {
      // Don't retry if we already hold the lock
      if (isLockedByMeRef.current) {
        stopRetry()
        return
      }
      try {
        const result = await sequencesApi.lock(pid, sid)
        setLockState(result)
        if (result.locked) {
          // Successfully acquired lock
          setIsLockedByMe(true)
          stopRetry()
          startHeartbeat(pid, sid)
        }
      } catch {
        // Lock attempt failed, keep polling
      }
    }, RETRY_LOCK_INTERVAL)
  }, [stopRetry, stopHeartbeat, startHeartbeat])

  const acquireLock = useCallback(async (): Promise<boolean> => {
    if (!projectId || !sequenceId) return false
    try {
      const result = await sequencesApi.lock(projectId, sequenceId)
      setLockState(result)
      if (result.locked) {
        setIsLockedByMe(true)
        startHeartbeat(projectId, sequenceId)
        return true
      }
      // Failed to acquire — start polling to retry
      startRetryPolling(projectId, sequenceId)
      return false
    } catch {
      return false
    }
  }, [projectId, sequenceId, startHeartbeat, startRetryPolling])

  const releaseLock = useCallback(async () => {
    stopHeartbeat()
    stopRetry()
    if (!projectId || !sequenceId || !isLockedByMe) return
    try {
      await sequencesApi.unlock(projectId, sequenceId)
    } catch {
      // Best-effort - lock will expire after 2 minutes
    }
    setIsLockedByMe(false)
    setLockState(null)
  }, [projectId, sequenceId, isLockedByMe, stopHeartbeat, stopRetry])

  // Auto-acquire lock on mount
  useEffect(() => {
    if (projectId && sequenceId) {
      acquireLock()
    }
    return () => {
      // Release on unmount (best-effort)
      stopHeartbeat()
      stopRetry()
      if (projectId && sequenceId && isLockedByMeRef.current) {
        sequencesApi.unlock(projectId, sequenceId).catch(() => {})
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, sequenceId])

  return {
    isLocked: lockState?.locked ?? false,
    isLockedByMe,
    isReadOnly: !isLockedByMe,
    lockHolder: lockState?.lock_holder_name ?? null,
    acquireLock,
    releaseLock,
  }
}
