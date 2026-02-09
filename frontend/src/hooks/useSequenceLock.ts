import { useState, useEffect, useRef, useCallback } from 'react'
import { sequencesApi, type LockResponse } from '@/api/sequences'

const HEARTBEAT_INTERVAL = 30_000 // 30 seconds

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

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
    }
  }, [])

  const acquireLock = useCallback(async (): Promise<boolean> => {
    if (!projectId || !sequenceId) return false
    try {
      const result = await sequencesApi.lock(projectId, sequenceId)
      setLockState(result)
      if (result.locked) {
        setIsLockedByMe(true)
        // Start heartbeat
        stopHeartbeat()
        heartbeatRef.current = setInterval(async () => {
          try {
            const hb = await sequencesApi.heartbeat(projectId, sequenceId)
            setLockState(hb)
            if (!hb.locked) {
              // Lost lock
              setIsLockedByMe(false)
              stopHeartbeat()
            }
          } catch {
            // Heartbeat failed - assume lock lost
            setIsLockedByMe(false)
            stopHeartbeat()
          }
        }, HEARTBEAT_INTERVAL)
        return true
      }
      return false
    } catch {
      return false
    }
  }, [projectId, sequenceId, stopHeartbeat])

  const releaseLock = useCallback(async () => {
    stopHeartbeat()
    if (!projectId || !sequenceId || !isLockedByMe) return
    try {
      await sequencesApi.unlock(projectId, sequenceId)
    } catch {
      // Best-effort - lock will expire after 2 minutes
    }
    setIsLockedByMe(false)
    setLockState(null)
  }, [projectId, sequenceId, isLockedByMe, stopHeartbeat])

  // Auto-acquire lock on mount
  useEffect(() => {
    if (projectId && sequenceId) {
      acquireLock()
    }
    return () => {
      // Release on unmount (best-effort)
      stopHeartbeat()
      if (projectId && sequenceId && isLockedByMe) {
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
