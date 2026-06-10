/**
 * AIChatPanel - localStorage スキーマ検証ユニットテスト (Vitest)
 *
 * Issue #275: チャット保存データに SCHEMA_VERSION + バリデーションを導入し、
 * 古い/不正なデータがクラッシュや AI への汚染データ送信を引き起こさないことを検証する。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  CHAT_SCHEMA_VERSION,
  isValidAIChatMessage,
  isValidChatSession,
  loadMessages,
  AI_HISTORY_LIMIT,
} from '@/lib/chat/chatStorage'

// ---------------------------------------------------------------------------
// localStorage モック
// ---------------------------------------------------------------------------
const store: Record<string, string> = {}

const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value },
  removeItem: (key: string) => { delete store[key] },
  clear: () => { Object.keys(store).forEach(k => delete store[k]) },
  get length() { return Object.keys(store).length },
  key: (index: number) => Object.keys(store)[index] ?? null,
}

Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

beforeEach(() => {
  localStorageMock.clear()
})

// ---------------------------------------------------------------------------
// 1. isValidAIChatMessage
// ---------------------------------------------------------------------------
describe('isValidAIChatMessage', () => {
  it('正しい形状のメッセージを有効と判定する', () => {
    expect(isValidAIChatMessage({ role: 'user', content: 'hello', timestamp: 1 })).toBe(true)
    expect(isValidAIChatMessage({ role: 'assistant', content: 'hi', timestamp: 2 })).toBe(true)
  })

  it('role が不正な場合は無効と判定する', () => {
    expect(isValidAIChatMessage({ role: 'system', content: 'x', timestamp: 1 })).toBe(false)
    expect(isValidAIChatMessage({ role: '', content: 'x', timestamp: 1 })).toBe(false)
    expect(isValidAIChatMessage({ role: 123, content: 'x', timestamp: 1 })).toBe(false)
  })

  it('content が文字列でない場合は無効と判定する', () => {
    expect(isValidAIChatMessage({ role: 'user', content: null, timestamp: 1 })).toBe(false)
    expect(isValidAIChatMessage({ role: 'user', content: 123, timestamp: 1 })).toBe(false)
  })

  it('timestamp が数値でない場合は無効と判定する', () => {
    expect(isValidAIChatMessage({ role: 'user', content: 'x', timestamp: 'now' })).toBe(false)
    expect(isValidAIChatMessage({ role: 'user', content: 'x', timestamp: null })).toBe(false)
  })

  it('null / プリミティブは無効と判定する', () => {
    expect(isValidAIChatMessage(null)).toBe(false)
    expect(isValidAIChatMessage('string')).toBe(false)
    expect(isValidAIChatMessage(42)).toBe(false)
    expect(isValidAIChatMessage(undefined)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// 2. isValidChatSession
// ---------------------------------------------------------------------------
describe('isValidChatSession', () => {
  it('正しい形状のセッションを有効と判定する', () => {
    expect(isValidChatSession({ id: 'session-1', name: 'Conversation 1', createdAt: 1 })).toBe(true)
  })

  it('id / name が文字列でない場合は無効と判定する', () => {
    expect(isValidChatSession({ id: 1, name: 'x', createdAt: 1 })).toBe(false)
    expect(isValidChatSession({ id: 's', name: null, createdAt: 1 })).toBe(false)
  })

  it('createdAt が数値でない場合は無効と判定する', () => {
    expect(isValidChatSession({ id: 's', name: 'n', createdAt: 'now' })).toBe(false)
  })

  it('null / プリミティブは無効と判定する', () => {
    expect(isValidChatSession(null)).toBe(false)
    expect(isValidChatSession('string')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// 3. loadMessages: スキーマバージョン検証
// ---------------------------------------------------------------------------
describe('loadMessages: スキーマバージョン検証', () => {
  const projectId = 'proj-test'
  const sessionId = 'session-test'
  const key = `ai-chat-messages-${projectId}-${sessionId}`

  it('現在バージョンのエンベロープから正常に読み込める', () => {
    const versioned = {
      schemaVersion: CHAT_SCHEMA_VERSION,
      messages: [
        { role: 'user', content: 'hello', timestamp: 1 },
        { role: 'assistant', content: 'hi', timestamp: 2 },
      ],
    }
    store[key] = JSON.stringify(versioned)

    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(2)
    expect(result[0].content).toBe('hello')
  })

  it('スキーマバージョンが異なるエンベロープは空配列を返し、ストレージから削除する', () => {
    const stale = {
      schemaVersion: CHAT_SCHEMA_VERSION + 1,  // 未来バージョン
      messages: [{ role: 'user', content: 'old', timestamp: 1 }],
    }
    store[key] = JSON.stringify(stale)

    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(0)
    expect(store[key]).toBeUndefined()
  })

  it('レガシー平配列（バージョンなし）は有効なメッセージだけを返す', () => {
    // バージョニング導入前の古い形式
    const legacyArray = [
      { role: 'user', content: 'legacy message', timestamp: 100 },
      { role: 'INVALID_ROLE', content: 'bad', timestamp: 200 },  // 無効 → フィルタされる
    ]
    store[key] = JSON.stringify(legacyArray)

    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('legacy message')
  })

  it('不正な JSON は空配列を返す（クラッシュしない）', () => {
    store[key] = 'NOT_JSON{'
    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(0)
  })

  it('不正なオブジェクト形状は空配列を返し、ストレージから削除する', () => {
    store[key] = JSON.stringify({ unexpected: 'shape' })
    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(0)
    expect(store[key]).toBeUndefined()
  })

  it('エンベロープ内の不正なメッセージはフィルタされる', () => {
    const versioned = {
      schemaVersion: CHAT_SCHEMA_VERSION,
      messages: [
        { role: 'user', content: 'valid', timestamp: 1 },
        { role: 'invalid', content: 'bad', timestamp: 2 },    // 無効
        { role: 'assistant', content: null, timestamp: 3 },   // 無効 (content=null)
        { role: 'assistant', content: 'also valid', timestamp: 4 },
      ],
    }
    store[key] = JSON.stringify(versioned)

    const result = loadMessages(projectId, sessionId)
    expect(result).toHaveLength(2)
    expect(result[0].content).toBe('valid')
    expect(result[1].content).toBe('also valid')
  })

  it('キーが存在しない場合は空配列を返す', () => {
    const result = loadMessages(projectId, 'nonexistent-session')
    expect(result).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// 4. AI_HISTORY_LIMIT
// ---------------------------------------------------------------------------
describe('AI_HISTORY_LIMIT', () => {
  it('AI_HISTORY_LIMIT は 10 である（バックエンドの history[-10:] と一致）', () => {
    expect(AI_HISTORY_LIMIT).toBe(10)
  })
})
