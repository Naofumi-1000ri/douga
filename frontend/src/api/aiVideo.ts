import apiClient from './client'

export interface SkillResponse {
  project_id: string
  skill: string
  success: boolean
  message: string
  changes: Record<string, any>
  duration_ms: number
}

export const aiVideoApi = {
  applyPlan: (projectId: string) =>
    apiClient.post(`/ai-video/projects/${projectId}/plan/apply`),

  trimSilence: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/trim-silence`)
      .then((r) => r.data),

  addTelop: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/add-telop`)
      .then((r) => r.data),

  layout: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/layout`)
      .then((r) => r.data),

  syncContent: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/sync-content`)
      .then((r) => r.data),

  clickHighlight: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/click-highlight`)
      .then((r) => r.data),

  avatarDodge: (projectId: string): Promise<SkillResponse> =>
    apiClient
      .post(`/ai-video/projects/${projectId}/skills/avatar-dodge`)
      .then((r) => r.data),
}
