import { useCallback, useEffect, useRef, useState } from 'react'
import { projectsApi, type RenderJob } from '@/api/projects'

interface UseRenderWorkflowParams {
  projectId?: string
  renderErrorTitle: string
}

interface UseRenderWorkflowResult {
  cancelRender: () => Promise<void>
  clearRenderJob: () => void
  downloadVideo: () => Promise<void>
  loadRenderHistory: () => Promise<void>
  renderHistory: RenderJob[]
  renderJob: RenderJob | null
  startRender: (options?: { start_ms?: number; end_ms?: number }) => Promise<void>
}

export function useRenderWorkflow({
  projectId,
  renderErrorTitle,
}: UseRenderWorkflowParams): UseRenderWorkflowResult {
  const [renderJob, setRenderJob] = useState<RenderJob | null>(null)
  const [renderHistory, setRenderHistory] = useState<RenderJob[]>([])
  const renderPollRef = useRef<number | null>(null)
  const lastUpdatedAtRef = useRef<string | null>(null)
  const staleCountRef = useRef(0)

  const clearRenderPolling = useCallback(() => {
    if (renderPollRef.current) {
      clearTimeout(renderPollRef.current)
      renderPollRef.current = null
    }
  }, [])

  const loadRenderHistory = useCallback(async () => {
    if (!projectId) return
    try {
      const history = await projectsApi.getRenderHistory(projectId)
      setRenderHistory(history)
    } catch (error) {
      console.error('Failed to load render history:', error)
    }
  }, [projectId])

  const pollRenderStatus = useCallback(async () => {
    if (!projectId) return

    try {
      const status = await projectsApi.getRenderStatus(projectId)
      if (!status) return

      console.log(`[POLL] status=${status.status} progress=${status.progress}% stage=${status.current_stage} updated_at=${status.updated_at}`)

      if (status.status === 'processing' && status.updated_at) {
        if (lastUpdatedAtRef.current === status.updated_at) {
          staleCountRef.current++
          console.log(`[RENDER] Stale check: ${staleCountRef.current}/300 (updated_at: ${status.updated_at})`)
          if (staleCountRef.current >= 300) {
            console.error('[RENDER] Job appears stale, cancelling and marking as failed')
            try {
              await projectsApi.cancelRender(projectId)
            } catch (cancelError) {
              console.error('[RENDER] Failed to cancel stale job:', cancelError)
            }
            setRenderJob({ ...status, status: 'failed', error_message: renderErrorTitle })
            lastUpdatedAtRef.current = null
            staleCountRef.current = 0
            return
          }
        } else {
          lastUpdatedAtRef.current = status.updated_at
          staleCountRef.current = 0
        }
      }

      setRenderJob(status)

      if (status.status === 'queued' || status.status === 'processing') {
        renderPollRef.current = window.setTimeout(pollRenderStatus, 2000)
      } else {
        lastUpdatedAtRef.current = null
        staleCountRef.current = 0
        void loadRenderHistory()
      }
    } catch (error) {
      console.error('Failed to poll render status:', error)
    }
  }, [loadRenderHistory, projectId, renderErrorTitle])

  const startRender = useCallback(async (options: { start_ms?: number; end_ms?: number } = {}) => {
    if (!projectId) return

    lastUpdatedAtRef.current = null
    staleCountRef.current = 0
    setRenderJob({ status: 'processing', progress: 0 } as RenderJob)

    void loadRenderHistory()
    clearRenderPolling()
    renderPollRef.current = window.setTimeout(pollRenderStatus, 1000)

    projectsApi.startRender(projectId, { ...options })
      .then((job) => {
        console.log('[RENDER] POST completed:', job.status)
      })
      .catch((error: unknown) => {
        const axiosError = error as { response?: { status?: number } }
        if (axiosError.response?.status === 409) {
          console.log('409 Conflict - retrying with force=true')
          clearRenderPolling()
          projectsApi.startRender(projectId, { ...options, force: true })
            .then((job) => {
              console.log('[RENDER] Force retry POST completed:', job.status)
            })
            .catch((retryError) => {
              console.error('Failed to start render (force retry):', retryError)
              setRenderJob(null)
              clearRenderPolling()
              alert(renderErrorTitle)
            })
          return
        }

        console.error('Failed to start render:', error)
        setRenderJob(null)
        clearRenderPolling()
        alert(renderErrorTitle)
      })
  }, [clearRenderPolling, loadRenderHistory, pollRenderStatus, projectId, renderErrorTitle])

  const cancelRender = useCallback(async () => {
    if (!projectId) return

    try {
      await projectsApi.cancelRender(projectId)
      setRenderJob(prev => prev ? { ...prev, status: 'cancelled' } : null)
      clearRenderPolling()
    } catch (error) {
      console.error('Failed to cancel render:', error)
      alert(renderErrorTitle)
    }
  }, [clearRenderPolling, projectId, renderErrorTitle])

  const downloadVideo = useCallback(async () => {
    if (!projectId) return

    try {
      const { download_url } = await projectsApi.getDownloadUrl(projectId)
      window.open(download_url, '_blank')
    } catch (error) {
      console.error('Failed to get download URL:', error)
      alert(renderErrorTitle)
    }
  }, [projectId, renderErrorTitle])

  useEffect(() => {
    clearRenderPolling()
    lastUpdatedAtRef.current = null
    staleCountRef.current = 0

    if (!projectId) {
      setRenderJob(null)
      setRenderHistory([])
      return
    }

    void loadRenderHistory()
  }, [clearRenderPolling, loadRenderHistory, projectId])

  useEffect(() => () => clearRenderPolling(), [clearRenderPolling])

  return {
    cancelRender,
    clearRenderJob: () => setRenderJob(null),
    downloadVideo,
    loadRenderHistory,
    renderHistory,
    renderJob,
    startRender,
  }
}
