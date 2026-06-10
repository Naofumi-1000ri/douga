import apiClient, { API_BASE_URL, getEditTokenForClient } from './client'
import type { TimelineData } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import { fetchWithETag, clearCache, SEQUENCES_CACHE_TTL_MS } from '@/lib/cache/etagCache'
import { areSignedUrlsValid } from '@/lib/cache/signedUrl'

/** シーケンス一覧キャッシュキー */
export function sequenceListCacheKey(projectId: string): string {
  return `cache:v1:sequences:${projectId}`
}

/** シーケンス詳細キャッシュキー */
export function sequenceDetailCacheKey(projectId: string, sequenceId: string): string {
  return `cache:v1:sequence:${projectId}:${sequenceId}`
}

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
  list: async (
    projectId: string,
    onCacheHit?: (cached: SequenceListItem[]) => void
  ): Promise<SequenceListItem[]> => {
    const cacheKey = sequenceListCacheKey(projectId)
    return fetchWithETag<SequenceListItem[]>({
      cacheKey,
      fetcher: async (headers) => {
        const res = await apiClient.get(`/projects/${projectId}/sequences`, {
          headers,
          validateStatus: (s) => s === 304 || (s >= 200 && s < 300),
        })
        return {
          data: res.data as SequenceListItem[],
          etag: (res.headers['etag'] as string | undefined) ?? null,
          status: res.status,
        }
      },
      onCacheHit,
      ttlMs: SEQUENCES_CACHE_TTL_MS,
      conditionalRequests: false,
      validatePayload: (cached) => {
        const now = Date.now()
        return cached.every((seq) => areSignedUrlsValid([seq.thumbnail_url], now))
      },
    })
  },

  get: async (
    projectId: string,
    sequenceId: string,
    onCacheHit?: (cached: SequenceDetail) => void,
    /** 保存 in-flight 中は true を渡してキャッシュをバイパスする */
    bypassCache?: boolean,
  ): Promise<SequenceDetail> => {
    const cacheKey = sequenceDetailCacheKey(projectId, sequenceId)
    return fetchWithETag<SequenceDetail>({
      cacheKey,
      fetcher: async (headers) => {
        // 保存 in-flight 中はキャッシュを使わず非条件 GET にフォールバック (P1-1)
        const requestHeaders = bypassCache ? {} : headers
        const res = await apiClient.get(`/projects/${projectId}/sequences/${sequenceId}`, {
          headers: requestHeaders,
          validateStatus: (s) => s === 304 || (s >= 200 && s < 300),
        })
        return {
          data: res.data as SequenceDetail,
          etag: (res.headers['etag'] as string | undefined) ?? null,
          status: res.status,
        }
      },
      // 保存 in-flight 中は楽観表示もスキップ
      onCacheHit: bypassCache ? undefined : onCacheHit,
      ttlMs: SEQUENCES_CACHE_TTL_MS,
    })
  },

  getDefault: async (projectId: string): Promise<{ id: string }> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences/default`)
    return res.data
  },

  create: async (projectId: string, name: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences`, { name })
    clearCache(sequenceListCacheKey(projectId))
    return res.data
  },

  update: async (projectId: string, sequenceId: string, timelineData: TimelineData, version: number): Promise<SequenceDetail> => {
    const res = await apiClient.put(`/projects/${projectId}/sequences/${sequenceId}`, {
      timeline_data: timelineData,
      version,
    })
    // duration_ms など list に反映される属性も変わるため list key もクリア (A-2)
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },

  delete: async (projectId: string, sequenceId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/sequences/${sequenceId}`)
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
  },

  rename: async (projectId: string, sequenceId: string, name: string): Promise<SequenceListItem> => {
    const res = await apiClient.patch(`/projects/${projectId}/sequences/${sequenceId}`, { name })
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },

  copy: async (projectId: string, sequenceId: string, name: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/copy`, { name })
    clearCache(sequenceListCacheKey(projectId))
    return res.data
  },

  lock: async (projectId: string, sequenceId: string): Promise<LockResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/lock`)
    // ロック状態は sequences list (locked_by フィールド) に反映される
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },

  heartbeat: async (projectId: string, sequenceId: string): Promise<LockResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/heartbeat`)
    return res.data
  },

  unlock: async (projectId: string, sequenceId: string): Promise<void> => {
    await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/unlock`)
    // アンロック状態は sequences list (locked_by フィールド) に反映される
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
  },

  unlockBestEffort: async (
    projectId: string,
    sequenceId: string,
    options?: { keepalive?: boolean }
  ): Promise<void> => {
    if (options?.keepalive && typeof fetch === 'function') {
      // keepalive: true allows the request to outlive page navigation.
      // We do NOT fall back to the axios client on failure: if the keepalive
      // fetch fails (e.g. the page is already unloading), sending a second
      // request via axios would race with the navigation and cause the lock
      // release to fire twice (GitHub #313).  The lock expires in 2 minutes
      // on its own, so a silent failure is acceptable.
      try {
        await fetch(`${API_BASE_URL}/projects/${projectId}/sequences/${sequenceId}/unlock`, {
          method: 'POST',
          headers: buildUnlockHeaders(),
          keepalive: true,
        })
        // ベストエフォートでもキャッシュは必ずクリア
        clearCache(sequenceListCacheKey(projectId))
        clearCache(sequenceDetailCacheKey(projectId, sequenceId))
      } catch {
        // Best-effort — lock expires automatically after 2 minutes.
        // No fallback to avoid a double-release race (see GitHub #313).
      }
      return
    }

    await sequencesApi.unlock(projectId, sequenceId)
    // sequencesApi.unlock 内で clearCache を呼んでいるが念のため確認済み
  },

  listSnapshots: async (projectId: string, sequenceId: string): Promise<SnapshotItem[]> => {
    const res = await apiClient.get(`/projects/${projectId}/sequences/${sequenceId}/snapshots`)
    return res.data
  },

  createSnapshot: async (projectId: string, sequenceId: string, name: string): Promise<SnapshotItem> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/snapshots`, { name })
    // スナップショット作成後は sequence detail（version/updated_at 等）が変わる可能性がある
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },

  restoreSnapshot: async (projectId: string, sequenceId: string, snapshotId: string): Promise<SequenceDetail> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/snapshots/${snapshotId}/restore`)
    // スナップショット復元で timeline_data が変わる → キャッシュを必ず無効化
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },

  deleteSnapshot: async (projectId: string, sequenceId: string, snapshotId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/sequences/${sequenceId}/snapshots/${snapshotId}`)
    // スナップショット削除後も sequence detail キャッシュを無効化
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
  },

  uploadThumbnail: async (projectId: string, sequenceId: string, imageData: string): Promise<{ thumbnail_url: string }> => {
    const res = await apiClient.post(`/projects/${projectId}/sequences/${sequenceId}/thumbnail`, { image_data: imageData })
    clearCache(sequenceListCacheKey(projectId))
    clearCache(sequenceDetailCacheKey(projectId, sequenceId))
    return res.data
  },
}
