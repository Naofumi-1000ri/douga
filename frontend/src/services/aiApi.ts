import apiClient from '@/api/client'

export interface AIChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface ChatAction {
  type: string
  description: string
  applied: boolean
}

export interface ChatResponse {
  message: string
  actions: ChatAction[]
}

export const aiApi = {
  /**
   * Send a natural language instruction to the AI chat endpoint
   */
  async chat(
    projectId: string,
    message: string,
    history: Array<{ role: 'user' | 'assistant'; content: string }>
  ): Promise<ChatResponse> {
    const response = await apiClient.post(`/ai/project/${projectId}/chat`, {
      message,
      history,
    })
    return response.data
  },

  /**
   * Get project overview for AI context
   */
  async getOverview(projectId: string) {
    const response = await apiClient.get(`/ai/project/${projectId}/overview`)
    return response.data
  },

  /**
   * Analyze gaps in the timeline
   */
  async analyzeGaps(projectId: string) {
    const response = await apiClient.get(`/ai/project/${projectId}/analysis/gaps`)
    return response.data
  },

  /**
   * Analyze pacing of the timeline
   */
  async analyzePacing(projectId: string) {
    const response = await apiClient.get(`/ai/project/${projectId}/analysis/pacing`)
    return response.data
  },
}
