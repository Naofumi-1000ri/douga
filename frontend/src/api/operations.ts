import apiClient from './client'

export interface Operation {
  type: string
  clip_id?: string
  layer_id?: string
  track_id?: string
  marker_id?: string
  data: Record<string, unknown>
}

export interface ApplyOperationsResponse {
  version: number
  timeline_data: Record<string, unknown>
}

export interface OperationHistoryItem {
  id: string
  version: number
  type: string
  user_id: string | null
  user_name: string | null
  data: Record<string, unknown>
  created_at: string
}

export interface OperationHistoryResponse {
  current_version: number
  operations: OperationHistoryItem[]
}

export const operationsApi = {
  apply: async (projectId: string, version: number, operations: Operation[]): Promise<ApplyOperationsResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/operations`, {
      version,
      operations,
    })
    return res.data
  },

  poll: async (projectId: string, sinceVersion: number, limit: number = 50): Promise<OperationHistoryResponse> => {
    const res = await apiClient.get(`/projects/${projectId}/operations`, {
      params: { since_version: sinceVersion, limit },
    })
    return res.data
  },
}
