import apiClient from './client'

export interface Asset {
  id: string
  project_id: string
  name: string
  type: 'video' | 'audio' | 'image'
  subtype: string
  storage_key: string
  storage_url: string
  thumbnail_url: string | null
  duration_ms: number | null
  width: number | null
  height: number | null
  file_size: number
  mime_type: string
  created_at: string
}

export interface UploadUrlResponse {
  upload_url: string
  storage_key: string
  expires_at: string
}

export interface CreateAssetData {
  name: string
  type: 'video' | 'audio' | 'image'
  subtype: string
  storage_key: string
  storage_url: string
  file_size: number
  mime_type: string
  duration_ms?: number
  width?: number
  height?: number
  sample_rate?: number
  channels?: number
  has_alpha?: boolean
  chroma_key_color?: string
}

export interface WaveformData {
  peaks: number[]
  duration_ms: number
  sample_rate: number
}

export interface SignedUrlResponse {
  url: string
  expires_in_seconds: number
}

export interface ThumbnailResponse {
  url: string
  time_ms: number
  width: number
  height: number
}

/**
 * Get duration (and dimensions for video) from a media file using browser APIs
 */
function getMediaDuration(
  file: File,
  type: 'audio' | 'video'
): Promise<{ durationMs: number; width?: number; height?: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)

    if (type === 'audio') {
      const audio = new Audio()
      audio.preload = 'metadata'

      audio.onloadedmetadata = () => {
        const durationMs = Math.round(audio.duration * 1000)
        URL.revokeObjectURL(url)
        resolve({ durationMs })
      }

      audio.onerror = () => {
        URL.revokeObjectURL(url)
        reject(new Error('Failed to load audio metadata'))
      }

      audio.src = url
    } else {
      const video = document.createElement('video')
      video.preload = 'metadata'

      video.onloadedmetadata = () => {
        const durationMs = Math.round(video.duration * 1000)
        const width = video.videoWidth
        const height = video.videoHeight
        URL.revokeObjectURL(url)
        resolve({ durationMs, width, height })
      }

      video.onerror = () => {
        URL.revokeObjectURL(url)
        reject(new Error('Failed to load video metadata'))
      }

      video.src = url
    }
  })
}

export const assetsApi = {
  list: async (projectId: string): Promise<Asset[]> => {
    const response = await apiClient.get(`/projects/${projectId}/assets`)
    return response.data
  },

  getUploadUrl: async (
    projectId: string,
    filename: string,
    contentType: string
  ): Promise<UploadUrlResponse> => {
    const response = await apiClient.post(
      `/projects/${projectId}/assets/upload-url`,
      null,
      { params: { filename, content_type: contentType } }
    )
    return response.data
  },

  create: async (projectId: string, data: CreateAssetData): Promise<Asset> => {
    const response = await apiClient.post(`/projects/${projectId}/assets`, data)
    return response.data
  },

  delete: async (projectId: string, assetId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/assets/${assetId}`)
  },

  extractAudio: async (projectId: string, assetId: string): Promise<Asset> => {
    const response = await apiClient.post(
      `/projects/${projectId}/assets/${assetId}/extract-audio`
    )
    return response.data
  },

  uploadFile: async (
    projectId: string,
    file: File,
    subtype: string,
    _onProgress?: (progress: number) => void
  ): Promise<Asset> => {
    console.log('[uploadFile] START - file:', file.name, 'type:', file.type, 'size:', file.size)
    // 1. Get upload URL
    const { upload_url, storage_key } = await assetsApi.getUploadUrl(
      projectId,
      file.name,
      file.type
    )

    // 2. Upload to GCS
    await fetch(upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': file.type },
      body: file,
    })

    // 3. Get media duration for audio/video files
    const assetType: 'video' | 'audio' | 'image' = file.type.startsWith('video/')
      ? 'video'
      : file.type.startsWith('audio/')
      ? 'audio'
      : 'image'

    let durationMs: number | undefined
    let width: number | undefined
    let height: number | undefined

    if (assetType === 'audio' || assetType === 'video') {
      try {
        const mediaInfo = await getMediaDuration(file, assetType)
        durationMs = mediaInfo.durationMs
        width = mediaInfo.width
        height = mediaInfo.height
        console.log('[Upload] Media duration detected:', { durationMs, width, height, fileName: file.name })
      } catch (err) {
        console.error('[Upload] Failed to get media duration:', err)
      }
    }

    // 4. Register asset
    const createData = {
      name: file.name,
      type: assetType,
      subtype,
      storage_key,
      storage_url: `https://storage.googleapis.com/${import.meta.env.VITE_GCS_BUCKET}/${storage_key}`,
      file_size: file.size,
      mime_type: file.type,
      duration_ms: durationMs,
      width,
      height,
    }
    console.log('[Upload] Creating asset with data:', createData)
    return await assetsApi.create(projectId, createData)
  },

  // Get waveform data for audio visualization
  getWaveform: async (
    projectId: string,
    assetId: string,
    samples: number = 200
  ): Promise<WaveformData> => {
    const response = await apiClient.get(
      `/projects/${projectId}/assets/${assetId}/waveform`,
      { params: { samples } }
    )
    return response.data
  },

  // Get video thumbnail at specific time position
  getThumbnail: async (
    projectId: string,
    assetId: string,
    timeMs: number = 0,
    width: number = 160,
    height: number = 90
  ): Promise<ThumbnailResponse> => {
    const response = await apiClient.get(
      `/projects/${projectId}/assets/${assetId}/thumbnail`,
      { params: { time_ms: timeMs, width, height } }
    )
    return response.data
  },

  // Get signed URL for streaming/playback
  getSignedUrl: async (
    projectId: string,
    assetId: string,
    expirationMinutes: number = 15
  ): Promise<SignedUrlResponse> => {
    const response = await apiClient.get(
      `/projects/${projectId}/assets/${assetId}/signed-url`,
      { params: { expiration_minutes: expirationMinutes } }
    )
    return response.data
  },
}
