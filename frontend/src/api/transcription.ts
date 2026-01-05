import apiClient from './client'

export type CutReason = 'silence' | 'mistake' | 'manual' | 'filler'

export interface TranscriptionWord {
  word: string
  start_ms: number
  end_ms: number
  confidence: number
}

export interface TranscriptionSegment {
  id: string
  start_ms: number
  end_ms: number
  text: string
  words: TranscriptionWord[]
  cut: boolean
  cut_reason: CutReason | null
  is_repetition: boolean
  is_filler: boolean
}

export interface TranscriptionStatistics {
  total_segments: number
  cut_segments: number
  total_duration_ms: number
  cut_duration_ms: number
}

export interface Transcription {
  asset_id: string
  language: string
  segments: TranscriptionSegment[]
  status: 'pending' | 'processing' | 'completed' | 'failed'
  error_message: string | null
  statistics: TranscriptionStatistics | null
}

export interface TranscribeRequest {
  asset_id: string
  language?: string
  model_name?: 'tiny' | 'base' | 'small' | 'medium' | 'large'
  detect_silences?: boolean
  detect_fillers?: boolean
  detect_repetitions?: boolean
}

export interface UpdateSegmentRequest {
  cut?: boolean
  cut_reason?: CutReason | null
}

export interface ApplyCutsResponse {
  clips_created: number
  total_duration_ms: number
  cut_duration_ms: number
}

export const transcriptionApi = {
  // Start transcription
  transcribe: async (request: TranscribeRequest): Promise<{ task_id: string }> => {
    const response = await apiClient.post('/transcription', request)
    return response.data
  },

  // Get transcription result
  get: async (assetId: string): Promise<Transcription> => {
    const response = await apiClient.get(`/transcription/${assetId}`)
    return response.data
  },

  // Update segment cut flag
  updateSegment: async (
    assetId: string,
    segmentId: string,
    update: UpdateSegmentRequest
  ): Promise<TranscriptionSegment> => {
    const response = await apiClient.put(
      `/transcription/${assetId}/segments/${segmentId}`,
      update
    )
    return response.data
  },

  // Apply cuts and generate clips
  applyCuts: async (assetId: string): Promise<ApplyCutsResponse> => {
    const response = await apiClient.post(`/transcription/${assetId}/apply-cuts`)
    return response.data
  },

  // Poll for transcription status
  waitForCompletion: async (
    assetId: string,
    maxWaitMs: number = 60000,
    intervalMs: number = 2000
  ): Promise<Transcription> => {
    const startTime = Date.now()

    while (Date.now() - startTime < maxWaitMs) {
      const transcription = await transcriptionApi.get(assetId)

      if (transcription.status === 'completed' || transcription.status === 'failed') {
        return transcription
      }

      await new Promise((resolve) => setTimeout(resolve, intervalMs))
    }

    throw new Error('Transcription timeout')
  },
}
