import apiClient from './client'

export interface APIKey {
  id: string
  name: string
  key_prefix: string
  is_active: boolean
  last_used_at: string | null
  expires_at: string | null
  created_at: string
}

export interface APIKeyCreated {
  id: string
  name: string
  key: string
  key_prefix: string
  created_at: string
  expires_at: string | null
}

export interface CreateAPIKeyRequest {
  name: string
  expires_in_days?: number
}

export async function listAPIKeys(): Promise<APIKey[]> {
  const response = await apiClient.get<APIKey[]>('/auth/api-keys')
  return response.data
}

export async function createAPIKey(request: CreateAPIKeyRequest): Promise<APIKeyCreated> {
  const response = await apiClient.post<APIKeyCreated>('/auth/api-keys', request)
  return response.data
}

export async function deleteAPIKey(keyId: string): Promise<void> {
  await apiClient.delete(`/auth/api-keys/${keyId}`)
}
