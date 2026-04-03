import apiClient, { API_BASE_URL, getEditTokenForClient } from './client'
import type { TimelineData } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'

export interface SequenceListItem {
  id: string
  name: string
  version: number
  duration_ms: number
  is_default: boolean
  locked_by: string | null
  lock_holder_name: string | null
  thumbnail_url: string | null
  created_at: string
  updated_at: string
}

export interface SequenceDetail {
  id: string
  project_id: string
  name: string
  timeline_data: TimelineData
  version: number
  duration_ms: number
  is_default: boolean
  locked_by: string | null
  lock_holder_name: string | null
  locked_at: string | null
  created_at: string
  updated_at: string
}

export interface LockResponse {
  locked: boolean
  locked_by: string | null
  lock_holder_name: string | null
  locked_at: string | null
  edit_token?: string
}

export interface SnapshotItem {
  id: string
  sequence_id: string
  name: string
  duration_ms: number
  created_at: string
  updated_at: string
}

function buildUnlockHeaders(): HeadersInit {
  const headers: Record<string, string> = {}
  const token = useAuthStore.getState().token
  const editToken = getEditTokenForClient()

  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  if (editToken) {
    headers['X-Edit-Session'] = editToken
  }

  return headers
}

export const sequencesApi = {
  list: async (projectId: string): Promise<SequenceListItem[]> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences`)
    return res.data
  },

  get: async (projectId: string, sequenceId: string): Promise<SequenceDetail> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences/${sequenceId}`)
    return res.data
  },

  getDefault: async (projectId: string): Promise<{ id: string }> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences/default`)
    return res.data
  },

  create: async (projectId: string, name: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences`, { name })
    return res.data
  },

  update: async (projectId: string, sequenceId: string, timelineData: TimelineData, version: number): Promise<SequenceDetail> => {
    const res = await apiClient.put(`/projects/${projectId}/sequences/${sequenceId}`, {
      timeline_data: timelineData,
      version,
    })
    return res.data
  },

  delete: async (projectId: string, sequenceId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/sequences/${sequenceId}`)
  },

  rename: async (projectId: string, sequenceId: string, name: string): Promise<SequenceListItem> => {
    const res = await apiClient.patch(`/projects/${projectId}/sequences/${sequenceId}`, { name })
    return res.data
  },

  copy: async (projectId: string, sequenceId: string, name: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/copy`, { name })
    return res.data
  },

  lock: async (projectId: string, sequenceId: string): Promise<LockResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/lock`)
    return res.data
  },

  heartbeat: async (projectId: string, sequenceId: string): Promise<LockResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/heartbeat`)
    return res.data
  },

  unlock: async (projectId: string, sequenceId: string): Promise<void> => {
    await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/unlock`)
  },

  unlockBestEffort: async (
    projectId: string,
    sequenceId: string,
    options?: { keepalive?: boolean }
  ): Promise<void> => {
    if (options?.keepalive && typeof fetch === 'function') {
      try {
        await fetch(`${API_BASE_URL}/projects/${projectId}/sequences/${sequenceId}/unlock`, {
          method: 'POST',
          headers: buildUnlockHeaders(),
          keepalive: true,
        })
        return
      } catch {
        // Fall back to the normal client request below.
      }
    }

    await sequencesApi.unlock(projectId, sequenceId)
  },

  listSnapshots: async (projectId: string, sequenceId: string): Promise<SnapshotItem[]> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences/${sequenceId}/snapshots`)
    return res.data
  },

  createSnapshot: async (projectId: string, sequenceId: string, name: string): Promise<SnapshotItem> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/snapshots`, { name })
    return res.data
  },

  restoreSnapshot: async (projectId: string, sequenceId: string, snapshotId: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/snapshots/${snapshotId}/restore`)
    return res.data
  },

  deleteSnapshot: async (projectId: string, sequenceId: string, snapshotId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/sequences/${sequenceId}/snapshots/${snapshotId}`)
  },

  uploadThumbnail: async (projectId: string, sequenceId: string, imageData: string): Promise<{ thumbnail_url: string }> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/thumbnail`, { image_data: imageData })
    return res.data
  },
}
