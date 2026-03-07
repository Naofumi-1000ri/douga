import { useCallback, useEffect, useRef, useState } from 'react'
import { useProjectStore, type TimelineData } from '@/store/projectStore'

export type SequenceSaveState = 'saved' | 'saving' | 'failed'

type SaveSequenceFn = (
  projectId: string,
  sequenceId: string,
  timeline: TimelineData,
  labelOrOptions?: string | { label?: string; skipHistory?: boolean }
) => Promise<void>

interface UseSequenceSaveStateParams {
  currentSequenceId?: string | null
  currentSequenceUpdatedAt?: string | null
  getErrorMessage: (error: unknown, fallback: string) => string
  projectId?: string
  saveConflictMessage: string
  saveFailedMessage: string
  saveSequence: SaveSequenceFn
  sequenceId?: string
  timelineData?: TimelineData
}

interface UseSequenceSaveStateResult {
  lastSequenceSaveAt: string | null
  retrySequenceSave: () => Promise<void>
  runTrackedSequenceSave: (operation: () => Promise<void>) => Promise<void>
  sequenceSaveError: string | null
  sequenceSaveState: SequenceSaveState
}

export function useSequenceSaveState({
  currentSequenceId,
  currentSequenceUpdatedAt,
  getErrorMessage,
  projectId,
  saveConflictMessage,
  saveFailedMessage,
  saveSequence,
  sequenceId,
  timelineData,
}: UseSequenceSaveStateParams): UseSequenceSaveStateResult {
  const pendingSaveCountRef = useRef(0)
  const [sequenceSaveState, setSequenceSaveState] = useState<SequenceSaveState>('saved')
  const [lastSequenceSaveAt, setLastSequenceSaveAt] = useState<string | null>(null)
  const [sequenceSaveError, setSequenceSaveError] = useState<string | null>(null)

  const runTrackedSequenceSave = useCallback(async (operation: () => Promise<void>) => {
    pendingSaveCountRef.current += 1
    setSequenceSaveState('saving')
    setSequenceSaveError(null)

    try {
      const hadConflictBeforeSave = useProjectStore.getState().conflictState?.isConflicting ?? false
      await operation()
      const hasConflictAfterSave = useProjectStore.getState().conflictState?.isConflicting ?? false
      if (!hadConflictBeforeSave && hasConflictAfterSave) {
        throw new Error(saveConflictMessage)
      }

      pendingSaveCountRef.current = Math.max(0, pendingSaveCountRef.current - 1)
      if (pendingSaveCountRef.current === 0) {
        setSequenceSaveState('saved')
        setLastSequenceSaveAt(new Date().toISOString())
      }
    } catch (error) {
      pendingSaveCountRef.current = Math.max(0, pendingSaveCountRef.current - 1)
      if (pendingSaveCountRef.current === 0) {
        setSequenceSaveState('failed')
        setSequenceSaveError(getErrorMessage(error, saveFailedMessage))
      }
      throw error
    }
  }, [getErrorMessage, saveConflictMessage, saveFailedMessage])

  const retrySequenceSave = useCallback(async () => {
    if (!projectId || !sequenceId || !timelineData) return
    try {
      await runTrackedSequenceSave(() => saveSequence(projectId, sequenceId, timelineData, { skipHistory: true }))
    } catch (error) {
      console.error('Failed to retry sequence save:', error)
    }
  }, [projectId, runTrackedSequenceSave, saveSequence, sequenceId, timelineData])

  useEffect(() => {
    pendingSaveCountRef.current = 0
    setSequenceSaveState('saved')
    setSequenceSaveError(null)
    setLastSequenceSaveAt(currentSequenceUpdatedAt ?? null)
  }, [currentSequenceId, currentSequenceUpdatedAt])

  useEffect(() => {
    const shouldWarnBeforeUnload = sequenceSaveState === 'saving' || sequenceSaveState === 'failed'
    if (!shouldWarnBeforeUnload) return

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [sequenceSaveState])

  return {
    lastSequenceSaveAt,
    retrySequenceSave,
    runTrackedSequenceSave,
    sequenceSaveError,
    sequenceSaveState,
  }
}
