import { useCallback, useState } from 'react'
import { assetsApi, type Asset } from '@/api/assets'
import { extractAssetReferences } from '@/utils/sessionMapper'
import type { TimelineData } from '@/store/projectStore'

interface SessionSaveAttempt {
  sessionId: string | null
  sessionName: string
  autoRename: boolean
}

export interface SessionSaveFailure {
  attempt: SessionSaveAttempt
  message: string
}

interface ToastMessage {
  text: string
  type: 'success' | 'error' | 'info'
  duration?: number
}

interface UseSessionSaveWorkflowParams {
  assets: Asset[]
  getErrorMessage: (error: unknown, fallback: string) => string
  onAssetsUpdated: (assets: Asset[]) => void
  onToast: (toast: ToastMessage) => void
  projectId?: string
  saveFailedMessage: string
  saveFailedToast: string
  timelineData?: TimelineData
}

interface UseSessionSaveWorkflowResult {
  assetLibraryRefreshTrigger: number
  clearSessionSaveFailure: () => void
  captureSessionSaveFailure: (attempt: SessionSaveAttempt, error: unknown) => void
  currentSessionId: string | null
  currentSessionName: string | null
  lastSavedSessionName: string
  markSessionSaved: (sessionId: string, sessionName: string) => void
  markSessionUnsaved: (sessionName: string) => void
  retryFailedSessionSave: () => Promise<void>
  saveSession: (sessionId: string | null, sessionName: string, autoRename?: boolean) => Promise<void>
  savingSession: boolean
  sessionSaveFailure: SessionSaveFailure | null
  touchAssetLibraryRefresh: () => void
}

export function useSessionSaveWorkflow({
  assets,
  getErrorMessage,
  onAssetsUpdated,
  onToast,
  projectId,
  saveFailedMessage,
  saveFailedToast,
  timelineData,
}: UseSessionSaveWorkflowParams): UseSessionSaveWorkflowResult {
  const [lastSavedSessionName, setLastSavedSessionName] = useState('')
  const [savingSession, setSavingSession] = useState(false)
  const [assetLibraryRefreshTrigger, setAssetLibraryRefreshTrigger] = useState(0)
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [currentSessionName, setCurrentSessionName] = useState<string | null>(null)
  const [sessionSaveFailure, setSessionSaveFailure] = useState<SessionSaveFailure | null>(null)

  const clearSessionSaveFailure = useCallback(() => {
    setSessionSaveFailure(null)
  }, [])

  const captureSessionSaveFailure = useCallback((attempt: SessionSaveAttempt, error: unknown) => {
    setSessionSaveFailure({
      attempt,
      message: getErrorMessage(error, saveFailedMessage),
    })
  }, [getErrorMessage, saveFailedMessage])

  const touchAssetLibraryRefresh = useCallback(() => {
    setAssetLibraryRefreshTrigger(prev => prev + 1)
  }, [])

  const markSessionSaved = useCallback((sessionId: string, sessionName: string) => {
    setCurrentSessionId(sessionId)
    setCurrentSessionName(sessionName)
    setLastSavedSessionName(sessionName)
    setSessionSaveFailure(null)
  }, [])

  const markSessionUnsaved = useCallback((sessionName: string) => {
    setCurrentSessionId(null)
    setCurrentSessionName(sessionName)
    setLastSavedSessionName('')
  }, [])

  const saveSession = useCallback(async (sessionId: string | null, sessionName: string, autoRename: boolean = false) => {
    if (!projectId || !timelineData) return

    setSavingSession(true)
    try {
      const sessionData = {
        schema_version: '1.0' as const,
        timeline_data: timelineData,
        asset_references: extractAssetReferences(timelineData, assets),
      }

      let savedAsset: Asset
      if (sessionId) {
        savedAsset = await assetsApi.updateSession(projectId, sessionId, sessionName, sessionData)
      } else {
        savedAsset = await assetsApi.saveSession(projectId, sessionName, sessionData, autoRename)
      }

      markSessionSaved(savedAsset.id, sessionName)

      const updatedAssets = await assetsApi.list(projectId)
      onAssetsUpdated(updatedAssets)
      touchAssetLibraryRefresh()
      onToast({ text: sessionName, type: 'success' })
    } catch (error) {
      console.error('Failed to save session:', error)
      captureSessionSaveFailure({ sessionId, sessionName, autoRename }, error)
      onToast({ text: saveFailedToast, type: 'error' })
      throw error
    } finally {
      setSavingSession(false)
    }
  }, [assets, captureSessionSaveFailure, markSessionSaved, onAssetsUpdated, onToast, projectId, saveFailedToast, timelineData, touchAssetLibraryRefresh])

  const retryFailedSessionSave = useCallback(async () => {
    if (!sessionSaveFailure) return
    try {
      await saveSession(
        sessionSaveFailure.attempt.sessionId,
        sessionSaveFailure.attempt.sessionName,
        sessionSaveFailure.attempt.autoRename,
      )
    } catch (error) {
      console.error('Failed to retry session save:', error)
    }
  }, [saveSession, sessionSaveFailure])

  return {
    assetLibraryRefreshTrigger,
    clearSessionSaveFailure,
    captureSessionSaveFailure,
    currentSessionId,
    currentSessionName,
    lastSavedSessionName,
    markSessionSaved,
    markSessionUnsaved,
    retryFailedSessionSave,
    saveSession,
    savingSession,
    sessionSaveFailure,
    touchAssetLibraryRefresh,
  }
}
