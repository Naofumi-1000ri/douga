/**
 * aiV1.test.ts — aiV1Api クライアントの単体テスト (Issue #276)
 *
 * テスト観点:
 *   (a) chromaKeyPreview が正しいエンドポイントに POST し data を返す
 *   (b) chromaKeyApply が Idempotency-Key ヘッダーを付けて POST し data を返す
 *   (c) 失敗時にエラーが伝播する
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// apiClient モック
// ---------------------------------------------------------------------------
const { apiClientPostMock } = vi.hoisted(() => ({
  apiClientPostMock: vi.fn(),
}))

vi.mock('./client', () => ({
  default: {
    post: apiClientPostMock,
  },
}))

import { aiV1Api } from './aiV1'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('aiV1Api.chromaKeyPreview', () => {
  it('(a) 正しいエンドポイントに POST し data を返す', async () => {
    const mockResult = {
      resolved_key_color: '#00ff00',
      frames: [
        {
          time_ms: 0,
          resolution: '1280x720',
          frame_base64: 'base64data',
          size_bytes: 1024,
          image_format: 'jpeg',
        },
      ],
    }
    apiClientPostMock.mockResolvedValueOnce({ data: { data: mockResult } })

    const result = await aiV1Api.chromaKeyPreview('proj-1', 'clip-1', {
      key_color: '#00ff00',
      similarity: 0.3,
      blend: 0.1,
    })

    expect(apiClientPostMock).toHaveBeenCalledOnce()
    const [url, payload] = apiClientPostMock.mock.calls[0]
    expect(url).toBe('/ai/v1/projects/proj-1/clips/clip-1/chroma-key/preview')
    expect(payload).toMatchObject({ key_color: '#00ff00', similarity: 0.3, blend: 0.1 })
    expect(result).toEqual(mockResult)
  })

  it('(a) optional パラメータが payload に含まれる', async () => {
    apiClientPostMock.mockResolvedValueOnce({
      data: { data: { resolved_key_color: '#00ff00', frames: [] } },
    })

    await aiV1Api.chromaKeyPreview('proj-2', 'clip-2', {
      key_color: '#0000ff',
      similarity: 0.5,
      blend: 0.2,
      resolution: '640x360',
      time_ms: 1000,
      skip_chroma_key: true,
      return_transparent_png: false,
    })

    const [, payload] = apiClientPostMock.mock.calls[0]
    expect(payload.resolution).toBe('640x360')
    expect(payload.time_ms).toBe(1000)
    expect(payload.skip_chroma_key).toBe(true)
  })

  it('(c) API エラー時に例外が伝播する', async () => {
    apiClientPostMock.mockRejectedValueOnce(new Error('Network error'))

    await expect(
      aiV1Api.chromaKeyPreview('proj-err', 'clip-err', {
        key_color: '#ffffff',
        similarity: 0.1,
        blend: 0.1,
      })
    ).rejects.toThrow('Network error')
  })
})

describe('aiV1Api.chromaKeyApply', () => {
  it('(b) Idempotency-Key ヘッダーを付けて POST し data を返す', async () => {
    const mockResult = {
      resolved_key_color: '#00ff00',
      asset_id: 'asset-abc',
      asset: {
        id: 'asset-abc',
        name: 'video.webm',
        url: 'http://example.com/video.webm',
        type: 'video',
        project_id: 'proj-1',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        metadata: null,
        folder_id: null,
        gcs_path: null,
        thumbnail_url: null,
        duration_ms: null,
        width: null,
        height: null,
        file_size: null,
        original_name: null,
        mime_type: null,
      },
    }
    apiClientPostMock.mockResolvedValueOnce({ data: { data: mockResult } })

    const idempotencyKey = 'idem-key-123'
    const result = await aiV1Api.chromaKeyApply(
      'proj-1',
      'clip-1',
      { key_color: '#00ff00', similarity: 0.3, blend: 0.1 },
      idempotencyKey
    )

    expect(apiClientPostMock).toHaveBeenCalledOnce()
    const [url, payload, config] = apiClientPostMock.mock.calls[0]
    expect(url).toBe('/ai/v1/projects/proj-1/clips/clip-1/chroma-key/apply')
    expect(payload).toMatchObject({ key_color: '#00ff00', similarity: 0.3, blend: 0.1 })
    expect(config.headers['Idempotency-Key']).toBe(idempotencyKey)
    expect(result).toEqual(mockResult)
  })

  it('(c) apply エラー時に例外が伝播する', async () => {
    apiClientPostMock.mockRejectedValueOnce(new Error('500 Internal'))

    await expect(
      aiV1Api.chromaKeyApply(
        'proj-err',
        'clip-err',
        { key_color: '#ffffff', similarity: 0.1, blend: 0.1 },
        'idem-key-err'
      )
    ).rejects.toThrow('500 Internal')
  })
})
