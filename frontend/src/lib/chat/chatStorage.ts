/**
 * AI Chat localStorage ストレージユーティリティ
 *
 * - SCHEMA_VERSION による互換性管理
 * - 読み込み時のバリデーション（不正/古いデータを破棄してクラッシュを防ぐ）
 * - 履歴スライス定数 AI_HISTORY_LIMIT
 */

import type { AIChatMessage } from '@/services/aiApi'

// ---------------------------------------------------------------------------
// Session type (AIChatPanel と共有)
// ---------------------------------------------------------------------------
export interface ChatSession {
  id: string
  name: string
  createdAt: number
}

// ---------------------------------------------------------------------------
// Schema versioning
// ---------------------------------------------------------------------------

/**
 * Chat storage schema version. Bump when message/session shape changes
 * incompatibly so that stale localStorage entries are discarded automatically.
 *
 * - v1: initial (sessions + per-session messages)
 */
export const CHAT_SCHEMA_VERSION = 1

interface VersionedMessages {
  schemaVersion: number
  messages: AIChatMessage[]
}

interface VersionedSessions {
  schemaVersion: number
  sessions: ChatSession[]
}

// ---------------------------------------------------------------------------
// Validators
// ---------------------------------------------------------------------------

/**
 * Validate and parse a raw AIChatMessage.
 * Returns true only when the object has the required shape.
 */
export function isValidAIChatMessage(v: unknown): v is AIChatMessage {
  if (typeof v !== 'object' || v === null) return false
  const m = v as Record<string, unknown>
  return (
    (m.role === 'user' || m.role === 'assistant') &&
    typeof m.content === 'string' &&
    typeof m.timestamp === 'number'
  )
}

/**
 * Validate and parse a raw ChatSession.
 * Returns true only when the object has the required shape.
 */
export function isValidChatSession(v: unknown): v is ChatSession {
  if (typeof v !== 'object' || v === null) return false
  const s = v as Record<string, unknown>
  return (
    typeof s.id === 'string' &&
    typeof s.name === 'string' &&
    typeof s.createdAt === 'number'
  )
}

// ---------------------------------------------------------------------------
// History limit
// ---------------------------------------------------------------------------

/**
 * Maximum number of history messages sent to the AI backend.
 * The backend already trims at history[-10:] (ai_service.py:4550);
 * we align the client-side limit to avoid unnecessary payload size.
 */
export const AI_HISTORY_LIMIT = 10

// ---------------------------------------------------------------------------
// localStorage keys
// ---------------------------------------------------------------------------
export const getSessionsStorageKey = (projectId: string) => `ai-chat-sessions-${projectId}`
export const getMessagesStorageKey = (projectId: string, sessionId: string) =>
  `ai-chat-messages-${projectId}-${sessionId}`
export const getCurrentSessionKey = (projectId: string) => `ai-chat-current-session-${projectId}`

// Legacy key for migration (pre-session-management)
export const getLegacyChatStorageKey = (projectId: string) => `ai-chat-messages-${projectId}`

// ---------------------------------------------------------------------------
// Session helpers
// ---------------------------------------------------------------------------

/** Load sessions from localStorage with schema validation */
export function loadSessions(projectId: string): ChatSession[] {
  try {
    const saved = localStorage.getItem(getSessionsStorageKey(projectId))
    if (!saved) return []

    const parsed = JSON.parse(saved) as unknown

    // Versioned envelope (new format)
    if (typeof parsed === 'object' && parsed !== null && 'schemaVersion' in parsed) {
      const versioned = parsed as VersionedSessions
      if (versioned.schemaVersion !== CHAT_SCHEMA_VERSION) {
        // Schema mismatch – discard stale data
        try { localStorage.removeItem(getSessionsStorageKey(projectId)) } catch { /* ignore */ }
        return []
      }
      if (!Array.isArray(versioned.sessions)) return []
      return versioned.sessions.filter(isValidChatSession)
    }

    // Legacy plain array (written before versioning was introduced)
    if (Array.isArray(parsed)) {
      const validSessions = parsed.filter(isValidChatSession)
      // Upgrade to versioned format transparently
      saveSessions(projectId, validSessions)
      return validSessions
    }

    // Unrecognised shape – discard
    try { localStorage.removeItem(getSessionsStorageKey(projectId)) } catch { /* ignore */ }
    return []
  } catch (e) {
    console.error('Failed to load chat sessions:', e)
  }
  return []
}

/** Save sessions to localStorage with schema version envelope */
export function saveSessions(projectId: string, sessions: ChatSession[]): void {
  try {
    const versioned: VersionedSessions = { schemaVersion: CHAT_SCHEMA_VERSION, sessions }
    localStorage.setItem(getSessionsStorageKey(projectId), JSON.stringify(versioned))
  } catch (e) {
    console.error('Failed to save chat sessions:', e)
  }
}

/** Load current session ID from localStorage */
export function loadCurrentSessionId(projectId: string): string | null {
  try {
    return localStorage.getItem(getCurrentSessionKey(projectId))
  } catch (e) {
    console.error('Failed to load current session:', e)
  }
  return null
}

/** Save current session ID to localStorage */
export function saveCurrentSessionId(projectId: string, sessionId: string): void {
  try {
    localStorage.setItem(getCurrentSessionKey(projectId), sessionId)
  } catch (e) {
    console.error('Failed to save current session:', e)
  }
}

// ---------------------------------------------------------------------------
// Message helpers
// ---------------------------------------------------------------------------

/** Load messages from localStorage with schema validation */
export function loadMessages(projectId: string, sessionId: string): AIChatMessage[] {
  try {
    const saved = localStorage.getItem(getMessagesStorageKey(projectId, sessionId))
    if (!saved) return []

    const parsed = JSON.parse(saved) as unknown

    // Versioned envelope (new format)
    if (typeof parsed === 'object' && parsed !== null && 'schemaVersion' in parsed) {
      const versioned = parsed as VersionedMessages
      if (versioned.schemaVersion !== CHAT_SCHEMA_VERSION) {
        // Schema mismatch – discard to prevent corrupted data from reaching AI
        try { localStorage.removeItem(getMessagesStorageKey(projectId, sessionId)) } catch { /* ignore */ }
        return []
      }
      if (!Array.isArray(versioned.messages)) return []
      return versioned.messages.filter(isValidAIChatMessage)
    }

    // Legacy plain array (written before versioning was introduced)
    if (Array.isArray(parsed)) {
      const validMessages = parsed.filter(isValidAIChatMessage)
      // Upgrade to versioned format transparently
      saveMessages(projectId, sessionId, validMessages)
      return validMessages
    }

    // Unrecognised shape – discard
    try { localStorage.removeItem(getMessagesStorageKey(projectId, sessionId)) } catch { /* ignore */ }
    return []
  } catch (e) {
    console.error('Failed to load chat messages:', e)
  }
  return []
}

/** Save messages to localStorage with schema version envelope */
export function saveMessages(projectId: string, sessionId: string, messages: AIChatMessage[]): void {
  try {
    const versioned: VersionedMessages = { schemaVersion: CHAT_SCHEMA_VERSION, messages }
    localStorage.setItem(getMessagesStorageKey(projectId, sessionId), JSON.stringify(versioned))
  } catch (e) {
    console.error('Failed to save chat messages:', e)
  }
}

// ---------------------------------------------------------------------------
// Session ID generator
// ---------------------------------------------------------------------------
export const generateSessionId = () => `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

// ---------------------------------------------------------------------------
// Legacy migration
// ---------------------------------------------------------------------------

/**
 * Migrate legacy messages (pre-session format) to the new session format.
 * Accepts a `defaultName` string (from i18n) for the initial session name.
 */
export function migrateLegacyMessages(
  projectId: string,
  defaultName: string,
): { sessions: ChatSession[], currentSessionId: string } {
  const sessions = loadSessions(projectId)

  // If sessions already exist, no migration needed
  if (sessions.length > 0) {
    const currentSessionId = loadCurrentSessionId(projectId) || sessions[0].id
    return { sessions, currentSessionId }
  }

  // Check for legacy messages
  try {
    const legacyKey = getLegacyChatStorageKey(projectId)
    const legacyMessages = localStorage.getItem(legacyKey)

    if (legacyMessages) {
      const rawMessages = JSON.parse(legacyMessages) as unknown
      const messages: AIChatMessage[] = Array.isArray(rawMessages)
        ? rawMessages.filter(isValidAIChatMessage)
        : []
      if (messages.length > 0) {
        const sessionId = generateSessionId()
        const session: ChatSession = {
          id: sessionId,
          name: defaultName,
          createdAt: messages[0].timestamp || Date.now(),
        }

        saveSessions(projectId, [session])
        saveMessages(projectId, sessionId, messages)
        saveCurrentSessionId(projectId, sessionId)
        localStorage.removeItem(legacyKey)

        return { sessions: [session], currentSessionId: sessionId }
      }
    }
  } catch (e) {
    console.error('Failed to migrate legacy messages:', e)
  }

  // No legacy messages – create a default session
  const sessionId = generateSessionId()
  const session: ChatSession = {
    id: sessionId,
    name: defaultName,
    createdAt: Date.now(),
  }
  saveSessions(projectId, [session])
  saveCurrentSessionId(projectId, sessionId)

  return { sessions: [session], currentSessionId: sessionId }
}
