import apiClient from './client'
import type { Asset } from './assets'

export interface ChromaKeyPreviewFrame {
  time_ms: number
  resolution: string
  frame_base64: string
  size_bytes: number
  image_format?: 'jpeg' | 'png'
}

export interface ChromaKeyPreviewResult {
  resolved_key_color: string
  frames: ChromaKeyPreviewFrame[]
}

export interface ChromaKeyApplyResult {
  resolved_key_color: string
  asset_id: string
  asset: Asset
}

export const aiV1Api = {
  chromaKeyPreview: async (
    projectId: string,
    clipId: string,
    data: {
      key_color: string
      similarity: number
      blend: number
      resolution?: string
      time_ms?: number
      skip_chroma_key?: boolean
      return_transparent_png?: boolean
    }
  ): Promise<ChromaKeyPreviewResult> => {
    const response = await apiClient.post(
      `/ai/v1/projects/${projectId}/clips/${clipId}/chroma-key/preview`,
      data
    )
    return response.data.data as ChromaKeyPreviewResult
  },

  chromaKeyApply: async (
    projectId: string,
    clipId: string,
    data: {
      key_color: string
      similarity: number
      blend: number
    },
    idempotencyKey: string
  ): Promise<ChromaKeyApplyResult> => {
    const response = await apiClient.post(
      `/ai/v1/projects/${projectId}/clips/${clipId}/chroma-key/apply`,
      data,
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
        },
      }
    )
    return response.data.data as ChromaKeyApplyResult
  },
}
