import apiClient from '@/api/client'
import { useAuthStore } from '@/store/authStore'

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

export type AIProvider = 'openai' | 'gemini' | 'anthropic'

// SSE Event types for streaming
export interface ChatStreamChunk {
  text: string
}

export interface ChatStreamActions {
  actions: ChatAction[]
}

export interface ChatStreamError {
  message: string
}

export type ChatStreamCallback = {
  onChunk?: (text: string) => void
  onActions?: (actions: ChatAction[]) => void
  onDone?: () => void
  onError?: (error: string) => void
}

export const aiApi = {
  /**
   * Send a natural language instruction to the AI chat endpoint
   * @param provider - Optional AI provider to use (openai, gemini, anthropic). Uses server default if not specified.
   */
  async chat(
    projectId: string,
    message: string,
    history: Array<{ role: 'user' | 'assistant'; content: string }>,
    provider?: AIProvider
  ): Promise<ChatResponse> {
    const response = await apiClient.post(
      `/ai/project/${projectId}/chat`,
      {
        message,
        history,
        provider,
      },
      {
        timeout: 180000, // 3 minutes for large operations
      }
    )
    return response.data
  },

  /**
   * Send a natural language instruction with streaming response (SSE)
   * @param projectId - Project ID
   * @param message - User message
   * @param history - Conversation history
   * @param callbacks - Callbacks for stream events
   * @param provider - Optional AI provider
   * @returns AbortController to cancel the request
   */
  chatStream(
    projectId: string,
    message: string,
    history: Array<{ role: 'user' | 'assistant'; content: string }>,
    callbacks: ChatStreamCallback,
    provider?: AIProvider
  ): AbortController {
    const controller = new AbortController()
    const token = useAuthStore.getState().token

    // Build the API URL
    const baseURL = import.meta.env.VITE_API_URL
      ? `${import.meta.env.VITE_API_URL}/api`
      : '/api'
    const url = `${baseURL}/ai/project/${projectId}/chat/stream`

    // Start the fetch request
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ message, history, provider }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const errorText = await response.text()
          callbacks.onError?.(`HTTP ${response.status}: ${errorText}`)
          callbacks.onDone?.()
          return
        }

        const reader = response.body?.getReader()
        if (!reader) {
          callbacks.onError?.('Response body is not readable')
          callbacks.onDone?.()
          return
        }

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })

          // Process complete SSE events
          const lines = buffer.split('\n')
          buffer = lines.pop() || '' // Keep incomplete line in buffer

          let currentEvent = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ') && currentEvent) {
              const data = line.slice(6)
              try {
                const parsed = JSON.parse(data)
                switch (currentEvent) {
                  case 'chunk':
                    if (parsed.text) {
                      callbacks.onChunk?.(parsed.text)
                    }
                    break
                  case 'actions':
                    if (Array.isArray(parsed)) {
                      callbacks.onActions?.(parsed)
                    }
                    break
                  case 'done':
                    callbacks.onDone?.()
                    break
                  case 'error':
                    callbacks.onError?.(parsed.message || 'Unknown error')
                    break
                }
              } catch {
                // Ignore JSON parse errors for malformed data
              }
              currentEvent = ''
            } else if (line === '') {
              currentEvent = ''
            }
          }
        }

        // Process any remaining buffer
        if (buffer.trim()) {
          const lines = buffer.split('\n')
          let currentEvent = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ') && currentEvent) {
              const data = line.slice(6)
              try {
                JSON.parse(data) // Validate JSON
                if (currentEvent === 'done') {
                  callbacks.onDone?.()
                }
              } catch {
                // Ignore malformed JSON
              }
            }
          }
        }
      })
      .catch((error) => {
        if (error.name !== 'AbortError') {
          callbacks.onError?.(error.message || 'Network error')
          callbacks.onDone?.()
        }
      })

    return controller
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
