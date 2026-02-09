import apiClient from './client'
import heic2any from 'heic2any'

export interface Asset {
  id: string
  project_id: string
  name: string
  type: 'video' | 'audio' | 'image' | 'session'
  subtype?: string
  storage_key: string
  storage_url: string
  thumbnail_url: string | null
  duration_ms: number | null
  width: number | null
  height: number | null
  file_size: number
  mime_type: string
  chroma_key_color?: string | null
  hash?: string | null  // SHA-256 hash for fingerprint matching
  folder_id: string | null
  created_at: string
  metadata?: {
    app_version?: string
    created_at?: string
  } | null
}

export interface AssetFolder {
  id: string
  project_id: string
  name: string
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

export interface BatchThumbnailRequest {
  times_ms: number[]
  width: number
  height: number
}

export interface BatchThumbnailResponse {
  thumbnails: ThumbnailResponse[]
  width: number
  height: number
}

export interface GridThumbnailsResponse {
  thumbnails: Record<number, string>  // time_ms -> signed URL
  interval_ms: number
  duration_ms: number
  width: number
  height: number
}

// Session-related types
export interface Fingerprint {
  hash: string | null  // SHA-256 hash "sha256:..."
  file_size: number | null
  duration_ms: number | null  // 0 for images, null if unknown
}

export interface AssetMetadata {
  codec?: string | null
  width?: number | null
  height?: number | null
}

export interface AssetReference {
  id: string  // Original asset UUID
  name: string
  type: string  // video, audio, image
  fingerprint: Fingerprint
  metadata?: AssetMetadata | null
}

export interface SessionData {
  schema_version: string
  created_at?: string | null
  app_version?: string | null
  timeline_data: unknown  // The actual timeline JSON
  asset_references: AssetReference[]
}

export interface SessionSaveRequest {
  session_name: string
  session_data: SessionData
}

/**
 * Get dimensions from an image file using browser APIs
 */
function getImageDimensions(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()

    img.onload = () => {
      const width = img.naturalWidth
      const height = img.naturalHeight
      URL.revokeObjectURL(url)
      resolve({ width, height })
    }

    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Failed to load image'))
    }

    img.src = url
  })
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

/**
 * Check if a file is HEIC/HEIF format
 */
function isHeicFile(file: File): boolean {
  const mimeType = file.type.toLowerCase()
  const fileName = file.name.toLowerCase()
  return (
    mimeType === 'image/heic' ||
    mimeType === 'image/heif' ||
    fileName.endsWith('.heic') ||
    fileName.endsWith('.heif')
  )
}

/**
 * Convert HEIC/HEIF file to JPEG
 * Returns the original file if not HEIC/HEIF format
 */
async function convertHeicToJpeg(file: File): Promise<File> {
  if (!isHeicFile(file)) {
    return file
  }

  console.log('[HEIC] Converting HEIC/HEIF to JPEG:', file.name)

  try {
    const blob = await heic2any({
      blob: file,
      toType: 'image/jpeg',
      quality: 0.9,
    })

    // heic2any can return a single blob or an array of blobs
    const resultBlob = Array.isArray(blob) ? blob[0] : blob

    // Create new filename with .jpg extension
    const newName = file.name.replace(/\.(heic|heif)$/i, '.jpg')

    const convertedFile = new File([resultBlob], newName, {
      type: 'image/jpeg',
      lastModified: Date.now(),
    })

    console.log('[HEIC] Conversion complete:', {
      originalName: file.name,
      newName,
      originalSize: file.size,
      newSize: convertedFile.size,
    })

    return convertedFile
  } catch (error) {
    console.error('[HEIC] Conversion failed:', error)
    throw new Error(`HEIC変換に失敗しました: ${file.name}`)
  }
}

export const assetsApi = {
  list: async (projectId: string, includeInternal: boolean = false): Promise<Asset[]> => {
    const response = await apiClient.get(`/projects/${projectId}/assets`, {
      params: includeInternal ? { include_internal: true } : undefined
    })
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
    subtype?: string,
    _onProgress?: (progress: number) => void,
    folderId?: string | null
  ): Promise<Asset> => {
    console.log('[uploadFile] START - file:', file.name, 'type:', file.type, 'size:', file.size)

    // 0. Convert HEIC/HEIF to JPEG if needed
    let processedFile = file
    if (isHeicFile(file)) {
      console.log('[uploadFile] Detected HEIC/HEIF file, converting to JPEG...')
      processedFile = await convertHeicToJpeg(file)
    }

    // 1. Get upload URL
    const { upload_url, storage_key } = await assetsApi.getUploadUrl(
      projectId,
      processedFile.name,
      processedFile.type
    )

    // 2. Upload to GCS
    await fetch(upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': processedFile.type },
      body: processedFile,
    })

    // 3. Get media duration for audio/video files
    const assetType: 'video' | 'audio' | 'image' = processedFile.type.startsWith('video/')
      ? 'video'
      : processedFile.type.startsWith('audio/')
      ? 'audio'
      : 'image'

    let durationMs: number | undefined
    let width: number | undefined
    let height: number | undefined

    if (assetType === 'audio' || assetType === 'video') {
      try {
        const mediaInfo = await getMediaDuration(processedFile, assetType)
        durationMs = mediaInfo.durationMs
        width = mediaInfo.width
        height = mediaInfo.height
        console.log('[Upload] Media duration detected:', { durationMs, width, height, fileName: processedFile.name })
      } catch (err) {
        console.error('[Upload] Failed to get media duration:', err)
      }
    } else if (assetType === 'image') {
      // Get image dimensions
      try {
        const imageInfo = await getImageDimensions(processedFile)
        width = imageInfo.width
        height = imageInfo.height
        console.log('[Upload] Image dimensions detected:', { width, height, fileName: processedFile.name })
      } catch (err) {
        console.error('[Upload] Failed to get image dimensions:', err)
      }
    }

    // 4. Register asset (use 'other' as default subtype if not specified)
    const createData: CreateAssetData = {
      name: processedFile.name,
      type: assetType,
      subtype: subtype || 'other',
      storage_key,
      storage_url: `https://storage.googleapis.com/${import.meta.env.VITE_GCS_BUCKET}/${storage_key}`,
      file_size: processedFile.size,
      mime_type: processedFile.type,
      duration_ms: durationMs,
      width,
      height,
    }
    console.log('[Upload] Creating asset with data:', createData)
    const asset = await assetsApi.create(projectId, createData)

    // 5. Move to folder if specified
    if (folderId) {
      return await assetsApi.moveToFolder(projectId, asset.id, folderId)
    }

    return asset
  },

  // Get waveform data for audio visualization
  getWaveform: async (
    projectId: string,
    assetId: string,
    samplesPerSecond: number = 10
  ): Promise<WaveformData> => {
    const response = await apiClient.get(
      `/projects/${projectId}/assets/${assetId}/waveform`,
      { params: { samples_per_second: samplesPerSecond } }
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

  // Get multiple video thumbnails in a single request (more efficient)
  getBatchThumbnails: async (
    projectId: string,
    assetId: string,
    timesMs: number[],
    width: number = 160,
    height: number = 90
  ): Promise<BatchThumbnailResponse> => {
    const response = await apiClient.post(
      `/projects/${projectId}/assets/${assetId}/thumbnails/batch`,
      { times_ms: timesMs, width, height }
    )
    return response.data
  },

  // Get pre-generated grid thumbnails (1-second intervals)
  // If times provided, only fetch those specific times (fast!)
  getGridThumbnails: async (
    projectId: string,
    assetId: string,
    times?: number[]
  ): Promise<GridThumbnailsResponse> => {
    const params = times ? { times: times.join(',') } : undefined
    const response = await apiClient.get(
      `/projects/${projectId}/assets/${assetId}/grid-thumbnails`,
      { params }
    )
    return response.data
  },

  // Hint backend to prioritize generating specific thumbnail times (fire-and-forget)
  generatePriorityThumbnails: async (
    projectId: string,
    assetId: string,
    times: number[]
  ): Promise<void> => {
    await apiClient.post(
      `/projects/${projectId}/assets/${assetId}/generate-priority-thumbnails`,
      { times }
    )
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

  // Move asset to a folder
  moveToFolder: async (
    projectId: string,
    assetId: string,
    folderId: string | null
  ): Promise<Asset> => {
    const response = await apiClient.patch(
      `/projects/${projectId}/assets/${assetId}/folder`,
      { folder_id: folderId }
    )
    return response.data
  },

  // Rename an asset
  rename: async (
    projectId: string,
    assetId: string,
    name: string
  ): Promise<Asset> => {
    const response = await apiClient.patch(
      `/projects/${projectId}/assets/${assetId}/rename`,
      { name }
    )
    return response.data
  },

  // Save a session (new creation; returns 409 if name exists, unless auto_rename=true)
  saveSession: async (
    projectId: string,
    sessionName: string,
    sessionData: SessionData,
    autoRename: boolean = false
  ): Promise<Asset> => {
    const response = await apiClient.post(
      `/projects/${projectId}/sessions`,
      {
        session_name: sessionName,
        session_data: sessionData,
      } as SessionSaveRequest,
      {
        params: autoRename ? { auto_rename: true } : undefined,
      }
    )
    return response.data
  },

  // Update (overwrite) an existing session
  updateSession: async (
    projectId: string,
    sessionId: string,
    sessionName: string,
    sessionData: SessionData
  ): Promise<Asset> => {
    const response = await apiClient.put(
      `/projects/${projectId}/sessions/${sessionId}`,
      {
        session_name: sessionName,
        session_data: sessionData,
      } as SessionSaveRequest
    )
    return response.data
  },

  // Get session data by ID
  getSession: async (
    projectId: string,
    sessionId: string
  ): Promise<SessionData> => {
    const response = await apiClient.get(
      `/projects/${projectId}/sessions/${sessionId}`
    )
    return response.data
  },
}

// Folder API
export const foldersApi = {
  list: async (projectId: string): Promise<AssetFolder[]> => {
    const response = await apiClient.get(`/projects/${projectId}/folders`)
    return response.data
  },

  create: async (projectId: string, name: string): Promise<AssetFolder> => {
    const response = await apiClient.post(`/projects/${projectId}/folders`, { name })
    return response.data
  },

  update: async (projectId: string, folderId: string, name: string): Promise<AssetFolder> => {
    const response = await apiClient.patch(`/projects/${projectId}/folders/${folderId}`, { name })
    return response.data
  },

  delete: async (projectId: string, folderId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/folders/${folderId}`)
  },
}
