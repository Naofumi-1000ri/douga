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
  releaseLock: (options?: { keepalive?: boolean }) => Promise<void>
}

export function useSequenceLock(
  projectId: string | undefined,
  sequenceId: string | undefined
): UseSequenceLockResult {
  const [lockState, setLockState] = useState<LockResponse | null>(null)
  const [isLockedByMe, setIsLockedByMe] = useState(false)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const heartbeatGenRef = useRef(0) // generation counter to discard stale heartbeat responses
  const retryRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isLockedByMeRef = useRef(false)

  // Keep ref in sync with state
  isLockedByMeRef.current = isLockedByMe

  const stopHeartbeat = useCallback(() => {
    heartbeatGenRef.current++ // invalidate any in-flight heartbeat responses
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
    const gen = ++heartbeatGenRef.current // capture generation for stale detection
    let consecutiveFailures = 0
    const MAX_CONSECUTIVE_FAILURES = 3
    heartbeatRef.current = setInterval(async () => {
      try {
        const hb = await sequencesApi.heartbeat(pid, sid)
        if (heartbeatGenRef.current !== gen) return // stale — heartbeat was restarted or stopped
        setLockState(hb)
        consecutiveFailures = 0
        if (hb.edit_token) {
          useProjectStore.getState().setEditToken(hb.edit_token)
        }
        if (!hb.locked) {
          console.log('[SequenceLock] Heartbeat returned locked=false, lost lock')
          setIsLockedByMe(false)
          useProjectStore.getState().setEditToken(null)
          stopHeartbeat()
          startRetryPollingInner(pid, sid)
        }
      } catch (err) {
        if (heartbeatGenRef.current !== gen) return // stale — heartbeat was restarted or stopped
        const httpStatus = (err as { response?: { status?: number } })?.response?.status
        if (httpStatus === 404) {
          // Resource gone — give up permanently, no retry
          console.warn('[SequenceLock] Heartbeat returned 404 — sequence deleted, giving up')
          setIsLockedByMe(false)
          useProjectStore.getState().setEditToken(null)
          stopHeartbeat()
          return
        }
        if (httpStatus === 403) {
          // Lock lost — give up and retry to re-acquire
          console.warn('[SequenceLock] Heartbeat returned 403 — lock lost, starting retry')
          setIsLockedByMe(false)
          useProjectStore.getState().setEditToken(null)
          stopHeartbeat()
          startRetryPollingInner(pid, sid)
          return
        }
        consecutiveFailures++
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          console.warn('[SequenceLock] Heartbeat failed', MAX_CONSECUTIVE_FAILURES, 'times consecutively, giving up lock:', err)
          setIsLockedByMe(false)
          useProjectStore.getState().setEditToken(null)
          stopHeartbeat()
          startRetryPollingInner(pid, sid)
        } else {
          console.warn('[SequenceLock] Heartbeat failed (attempt', consecutiveFailures, '/', MAX_CONSECUTIVE_FAILURES, '), will retry:', err)
        }
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
          isLockedByMeRef.current = true
          if (result.edit_token) {
            useProjectStore.getState().setEditToken(result.edit_token)
          }
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
        const httpStatus = (err as { response?: { status?: number } })?.response?.status
        if (httpStatus === 404) {
          console.warn('[SequenceLock] Immediate retry: 404 — sequence deleted, stopping retry')
          if (retryRef.current) { clearInterval(retryRef.current); retryRef.current = null }
          return
        }
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
          isLockedByMeRef.current = true
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
        const httpStatus = (err as { response?: { status?: number } })?.response?.status
        if (httpStatus === 404) {
          console.warn('[SequenceLock] Retry poll: 404 — sequence deleted, stopping retry')
          if (retryRef.current) { clearInterval(retryRef.current); retryRef.current = null }
          return
        }
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
        isLockedByMeRef.current = true
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
      // Only retry on transient errors (network issues), not permanent ones (403/404)
      const httpStatus = (err as { response?: { status?: number } })?.response?.status
      if (!httpStatus || httpStatus >= 500 || httpStatus === 409) {
        startRetryPollingInner(projectId, sequenceId)
      }
      return false
    }
  }, [projectId, sequenceId, startHeartbeat, startRetryPollingInner])

  const releaseLock = useCallback(async (options?: { keepalive?: boolean }) => {
    stopHeartbeat()
    stopRetry()
    if (!projectId || !sequenceId || !isLockedByMeRef.current) return
    isLockedByMeRef.current = false
    setIsLockedByMe(false)
    useProjectStore.getState().setEditToken(null)
    setLockState(null)
    try {
      console.log('[SequenceLock] Releasing lock...')
      await sequencesApi.unlockBestEffort(projectId, sequenceId, options)
    } catch {
      // Best-effort - lock will expire after 2 minutes
    }
  }, [projectId, sequenceId, stopHeartbeat, stopRetry])

  // Send immediate heartbeat when tab becomes visible (Chrome throttles setInterval in background tabs)
  useEffect(() => {
    let cancelled = false
    let latestHbId = 0
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && isLockedByMeRef.current && projectId && sequenceId) {
        const hbId = ++latestHbId
        console.log('[SequenceLock] Tab became visible, sending immediate heartbeat')
        sequencesApi.heartbeat(projectId, sequenceId).then(hb => {
          if (cancelled || latestHbId !== hbId) return
          setLockState(hb)
          if (hb.edit_token) {
            useProjectStore.getState().setEditToken(hb.edit_token)
          }
          if (!hb.locked) {
            console.log('[SequenceLock] Visibility HB: lost lock')
            setIsLockedByMe(false)
            useProjectStore.getState().setEditToken(null)
            stopHeartbeat()
            startRetryPollingInner(projectId, sequenceId)
          } else if (isLockedByMeRef.current) {
            // Restart heartbeat to reset consecutive failure counter
            // Only if we still believe we hold the lock (avoid overwriting a concurrent lock-loss)
            startHeartbeat(projectId, sequenceId)
          }
        }).catch(err => {
          if (cancelled || latestHbId !== hbId) return
          const httpStatus = (err as { response?: { status?: number } })?.response?.status
          if (httpStatus === 404) {
            console.warn('[SequenceLock] Visibility HB returned 404 — sequence deleted')
            setIsLockedByMe(false)
            useProjectStore.getState().setEditToken(null)
            stopHeartbeat()
          } else if (httpStatus === 403) {
            console.warn('[SequenceLock] Visibility HB returned 403 — lock lost, starting retry')
            setIsLockedByMe(false)
            useProjectStore.getState().setEditToken(null)
            stopHeartbeat()
            startRetryPollingInner(projectId!, sequenceId!)
          } else {
            console.warn('[SequenceLock] Visibility HB failed, will retry at next interval:', err)
          }
        })
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [projectId, sequenceId, stopHeartbeat, startHeartbeat, startRetryPollingInner])

  useEffect(() => {
    const handlePageHide = () => {
      if (!isLockedByMeRef.current) return
      void releaseLock({ keepalive: true })
    }

    window.addEventListener('pagehide', handlePageHide)
    return () => window.removeEventListener('pagehide', handlePageHide)
  }, [releaseLock])

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
        void releaseLock({ keepalive: true })
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
