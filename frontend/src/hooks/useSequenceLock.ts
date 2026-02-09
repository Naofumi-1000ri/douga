import { useState, useEffect, useRef, useCallback } from 'react'
import { sequencesApi, type LockResponse } from '@/api/sequences'
import { useProjectStore } from '@/store/projectStore'

const HEARTBEAT_INTERVAL = 30_000 // 30 seconds
const RETRY_LOCK_INTERVAL = 5_000 // 5 seconds - poll to re-acquire when read-only

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
        if (hb.edit_token) {
          useProjectStore.getState().setEditToken(hb.edit_token)
        }
        if (!hb.locked) {
          console.log('[SequenceLock] Heartbeat returned locked=false, lost lock')
          setIsLockedByMe(false)
          useProjectStore.getState().setEditToken(null)
          stopHeartbeat()
          // Start retry polling to re-acquire
          startRetryPollingInner(pid, sid)
        }
      } catch (err) {
        console.warn('[SequenceLock] Heartbeat failed, lost lock:', err)
        setIsLockedByMe(false)
        useProjectStore.getState().setEditToken(null)
        stopHeartbeat()
        // Start retry polling to re-acquire
        startRetryPollingInner(pid, sid)
      }
    }, HEARTBEAT_INTERVAL)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopHeartbeat, stopRetry])

  // Inner function ref to avoid circular dependency
  const startRetryPollingInner = useCallback((pid: string, sid: string) => {
    // Clear any existing retry/heartbeat
    if (retryRef.current) {
      clearInterval(retryRef.current)
      retryRef.current = null
    }
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
    }

    console.log('[SequenceLock] Starting retry polling every', RETRY_LOCK_INTERVAL, 'ms')

    // Try immediately first
    ;(async () => {
      if (isLockedByMeRef.current) return
      try {
        const result = await sequencesApi.lock(pid, sid)
        setLockState(result)
        if (result.locked) {
          console.log('[SequenceLock] Immediate retry: lock acquired!')
          setIsLockedByMe(true)
          // Clear any pending retry interval since we got the lock
          if (retryRef.current) {
            clearInterval(retryRef.current)
            retryRef.current = null
          }
          startHeartbeat(pid, sid)
          return
        }
        console.log('[SequenceLock] Immediate retry: lock held by', result.lock_holder_name)
      } catch (err) {
        console.warn('[SequenceLock] Immediate retry failed:', err)
      }
    })()

    retryRef.current = setInterval(async () => {
      // Don't retry if we already hold the lock
      if (isLockedByMeRef.current) {
        if (retryRef.current) {
          clearInterval(retryRef.current)
          retryRef.current = null
        }
        return
      }
      try {
        const result = await sequencesApi.lock(pid, sid)
        setLockState(result)
        if (result.locked) {
          console.log('[SequenceLock] Retry poll: lock acquired!')
          setIsLockedByMe(true)
          if (result.edit_token) {
            useProjectStore.getState().setEditToken(result.edit_token)
          }
          if (retryRef.current) {
            clearInterval(retryRef.current)
            retryRef.current = null
          }
          startHeartbeat(pid, sid)
        } else {
          console.log('[SequenceLock] Retry poll: lock still held by', result.lock_holder_name)
        }
      } catch (err) {
        console.warn('[SequenceLock] Retry poll error:', err)
      }
    }, RETRY_LOCK_INTERVAL)
  }, [startHeartbeat])

  const acquireLock = useCallback(async (): Promise<boolean> => {
    if (!projectId || !sequenceId) return false
    try {
      console.log('[SequenceLock] Attempting to acquire lock...')
      const result = await sequencesApi.lock(projectId, sequenceId)
      setLockState(result)
      if (result.locked) {
        console.log('[SequenceLock] Lock acquired successfully')
        setIsLockedByMe(true)
        if (result.edit_token) {
          useProjectStore.getState().setEditToken(result.edit_token)
        }
        startHeartbeat(projectId, sequenceId)
        return true
      }
      console.log('[SequenceLock] Lock held by', result.lock_holder_name, '- starting retry polling')
      // Failed to acquire — start polling to retry
      startRetryPollingInner(projectId, sequenceId)
      return false
    } catch (err) {
      console.error('[SequenceLock] Lock acquisition error:', err)
      return false
    }
  }, [projectId, sequenceId, startHeartbeat, startRetryPollingInner])

  const releaseLock = useCallback(async () => {
    stopHeartbeat()
    stopRetry()
    if (!projectId || !sequenceId || !isLockedByMe) return
    try {
      console.log('[SequenceLock] Releasing lock...')
      await sequencesApi.unlock(projectId, sequenceId)
    } catch {
      // Best-effort - lock will expire after 2 minutes
    }
    setIsLockedByMe(false)
    useProjectStore.getState().setEditToken(null)
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
      useProjectStore.getState().setEditToken(null)
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
