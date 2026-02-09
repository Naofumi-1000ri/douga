import apiClient from './client'

export interface Member {
  id: string
  project_id: string
  user_id: string
  role: string
  email: string
  name: string
  avatar_url: string | null
  invited_at: string
  accepted_at: string | null
}

export interface Invitation {
  id: string
  project_id: string
  project_name: string
  role: string
  invited_by_name: string | null
  invited_at: string
}

export const membersApi = {
  listMembers: async (projectId: string): Promise<Member[]> => {
    const response = await apiClient.get(`/projects/${projectId}/members`)
    return response.data
  },

  inviteMember: async (projectId: string, email: string): Promise<Member> => {
    const response = await apiClient.post(`/projects/${projectId}/members`, { email })
    return response.data
  },

  removeMember: async (projectId: string, memberId: string): Promise<void> => {
    await apiClient.delete(`/projects/${projectId}/members/${memberId}`)
  },

  acceptInvitation: async (projectId: string, memberId: string): Promise<Member> => {
    const response = await apiClient.post(`/projects/${projectId}/members/${memberId}/accept`)
    return response.data
  },

  listInvitations: async (): Promise<Invitation[]> => {
    const response = await apiClient.get('/members/invitations')
    return response.data
  },
}
