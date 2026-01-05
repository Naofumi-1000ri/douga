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
    return response.data
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

  exportAudio: async (id: string): Promise<{ download_url: string; filename: string }> => {
    const response = await apiClient.post(`/projects/${id}/render/audio`)
    return response.data
  },

  // Video rendering
  startRender: async (id: string): Promise<RenderJob> => {
    const response = await apiClient.post(`/projects/${id}/render`, {})
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
}

export interface RenderJob {
  id: string
  project_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled'
  progress: number
  current_stage: string
  output_url: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}
