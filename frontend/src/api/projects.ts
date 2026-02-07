import apiClient from './client'
import type { Project, ProjectDetail, TimelineData } from '@/store/projectStore'

interface CreateProjectData {
  name: string
  description?: string
  width?: number
  height?: number
  fps?: number
}

export const projectsApi = {
  list: async (): Promise<Project[]> => {
    const response = await apiClient.get('/projects')
    // Ensure we always return an array (defensive coding)
    const data = response.data
    if (Array.isArray(data)) {
      return data
    }
    // If response is wrapped (e.g., { projects: [...] }), try to extract
    if (data && Array.isArray(data.projects)) {
      return data.projects
    }
    console.warn('Unexpected projects API response:', data)
    return []
  },

  get: async (id: string): Promise<ProjectDetail> => {
    const response = await apiClient.get(`/projects/${id}`)
    return response.data
  },

  create: async (data: CreateProjectData): Promise<Project> => {
    const response = await apiClient.post('/projects', data)
    return response.data
  },

  update: async (id: string, data: Partial<ProjectDetail>): Promise<ProjectDetail> => {
    const response = await apiClient.put(`/projects/${id}`, data)
    return response.data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/projects/${id}`)
  },

  updateTimeline: async (id: string, timeline: TimelineData): Promise<ProjectDetail> => {
    const response = await apiClient.put(`/projects/${id}/timeline`, timeline)
    return response.data
  },

  // Video rendering
  startRender: async (
    id: string,
    options: { force?: boolean; start_ms?: number; end_ms?: number } = {}
  ): Promise<RenderJob> => {
    const { force = false, start_ms, end_ms } = options
    const response = await apiClient.post(`/projects/${id}/render`, {
      force,
      ...(start_ms !== undefined && { start_ms }),
      ...(end_ms !== undefined && { end_ms }),
    })
    return response.data
  },

  getRenderStatus: async (id: string): Promise<RenderJob | null> => {
    const response = await apiClient.get(`/projects/${id}/render/status`)
    return response.data
  },

  cancelRender: async (id: string): Promise<void> => {
    await apiClient.delete(`/projects/${id}/render`)
  },

  getDownloadUrl: async (id: string): Promise<{ download_url: string }> => {
    const response = await apiClient.get(`/projects/${id}/render/download`)
    return response.data
  },

  getRenderHistory: async (id: string): Promise<RenderJob[]> => {
    const response = await apiClient.get(`/projects/${id}/render/history`)
    return response.data
  },

  uploadThumbnail: async (id: string, imageData: string): Promise<{ thumbnail_url: string }> => {
    const response = await apiClient.post(`/projects/${id}/thumbnail`, { image_data: imageData })
    return response.data
  },

  // Single-frame composite preview
  sampleFrame: async (
    id: string,
    options: { time_ms: number; resolution?: string }
  ): Promise<{ time_ms: number; resolution: string; frame_base64: string; size_bytes: number }> => {
    const response = await apiClient.post(`/projects/${id}/preview/sample-frame`, {
      time_ms: options.time_ms,
      resolution: options.resolution || '1920x1080',
    })
    return response.data
  },
}

export interface RenderJob {
  id: string
  project_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled'
  progress: number
  current_stage: string
  output_url: string | null
  output_size: number | null
  error_message: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}
