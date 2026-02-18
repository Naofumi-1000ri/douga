import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { aiApi, type AIChatMessage, type AIProvider, type ChatAction } from '@/services/aiApi'

interface AIChatPanelProps {
  projectId: string
  aiProvider: AIProvider | null
  isOpen: boolean
  onToggle: () => void
  mode?: 'floating' | 'inline'
  className?: string
  width?: number
  onResizeStart?: (e: React.MouseEvent) => void
}

// Session type
interface ChatSession {
  id: string
  name: string
  createdAt: number
}

// localStorage keys
const getSessionsStorageKey = (projectId: string) => `ai-chat-sessions-${projectId}`
const getMessagesStorageKey = (projectId: string, sessionId: string) =>
  `ai-chat-messages-${projectId}-${sessionId}`
const getCurrentSessionKey = (projectId: string) => `ai-chat-current-session-${projectId}`

// Legacy key for migration
const getLegacyChatStorageKey = (projectId: string) => `ai-chat-messages-${projectId}`

// Generate unique session ID
const generateSessionId = () => `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

// Load sessions from localStorage
function loadSessions(projectId: string): ChatSession[] {
  try {
    const saved = localStorage.getItem(getSessionsStorageKey(projectId))
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.error('Failed to load chat sessions:', e)
  }
  return []
}

// Save sessions to localStorage
function saveSessions(projectId: string, sessions: ChatSession[]): void {
  try {
    localStorage.setItem(getSessionsStorageKey(projectId), JSON.stringify(sessions))
  } catch (e) {
    console.error('Failed to save chat sessions:', e)
  }
}

// Load current session ID
function loadCurrentSessionId(projectId: string): string | null {
  try {
    return localStorage.getItem(getCurrentSessionKey(projectId))
  } catch (e) {
    console.error('Failed to load current session:', e)
  }
  return null
}

// Save current session ID
function saveCurrentSessionId(projectId: string, sessionId: string): void {
  try {
    localStorage.setItem(getCurrentSessionKey(projectId), sessionId)
  } catch (e) {
    console.error('Failed to save current session:', e)
  }
}

// Load messages from localStorage
function loadMessages(projectId: string, sessionId: string): AIChatMessage[] {
  try {
    const saved = localStorage.getItem(getMessagesStorageKey(projectId, sessionId))
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.error('Failed to load chat messages:', e)
  }
  return []
}

// Save messages to localStorage
function saveMessages(projectId: string, sessionId: string, messages: AIChatMessage[]): void {
  try {
    localStorage.setItem(getMessagesStorageKey(projectId, sessionId), JSON.stringify(messages))
  } catch (e) {
    console.error('Failed to save chat messages:', e)
  }
}

// Migrate legacy messages to new session format
function migrateLegacyMessages(projectId: string): { sessions: ChatSession[], currentSessionId: string } {
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
      const messages: AIChatMessage[] = JSON.parse(legacyMessages)
      if (messages.length > 0) {
        // Create a session for legacy messages
        const sessionId = generateSessionId()
        const session: ChatSession = {
          id: sessionId,
          name: 'Conversation 1',
          createdAt: messages[0].timestamp || Date.now(),
        }

        // Save migrated data
        saveSessions(projectId, [session])
        saveMessages(projectId, sessionId, messages)
        saveCurrentSessionId(projectId, sessionId)

        // Remove legacy key
        localStorage.removeItem(legacyKey)

        return { sessions: [session], currentSessionId: sessionId }
      }
    }
  } catch (e) {
    console.error('Failed to migrate legacy messages:', e)
  }

  // No legacy messages, create a default session
  const sessionId = generateSessionId()
  const session: ChatSession = {
    id: sessionId,
    name: 'Conversation 1',
    createdAt: Date.now(),
  }
  saveSessions(projectId, [session])
  saveCurrentSessionId(projectId, sessionId)

  return { sessions: [session], currentSessionId: sessionId }
}

// Format date for display
function formatSessionDate(timestamp: number): string {
  const date = new Date(timestamp)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()

  if (isToday) {
    return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function AIChatPanel({ projectId, aiProvider, isOpen, onToggle, mode = 'floating', className = '', width = 320, onResizeStart }: AIChatPanelProps) {
  const { t } = useTranslation('editor')
  // Initialize session state
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [currentSessionId, setCurrentSessionId] = useState<string>('')
  const [isSessionMenuOpen, setIsSessionMenuOpen] = useState(false)
  const [messages, setMessages] = useState<AIChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const sessionMenuRef = useRef<HTMLDivElement>(null)
  const streamControllerRef = useRef<AbortController | null>(null)

  // Initialize sessions and migrate legacy data
  useEffect(() => {
    const { sessions: loadedSessions, currentSessionId: loadedCurrentId } = migrateLegacyMessages(projectId)
    setSessions(loadedSessions)
    setCurrentSessionId(loadedCurrentId)
    setMessages(loadMessages(projectId, loadedCurrentId))
  }, [projectId])

  // Save messages when they change (with session ID)
  useEffect(() => {
    if (currentSessionId) {
      saveMessages(projectId, currentSessionId, messages)
    }
  }, [projectId, currentSessionId, messages])

  // Close session menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (sessionMenuRef.current && !sessionMenuRef.current.contains(e.target as Node)) {
        setIsSessionMenuOpen(false)
      }
    }
    if (isSessionMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isSessionMenuOpen])

  // Initialize position to bottom-right
  useEffect(() => {
    setPosition({
      x: window.innerWidth - 420,
      y: window.innerHeight - 560,
    })
  }, [])

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  // Session management callbacks
  const createNewSession = useCallback(() => {
    const sessionId = generateSessionId()
    const newSession: ChatSession = {
      id: sessionId,
      name: t('aiChat.conversation', { num: sessions.length + 1 }),
      createdAt: Date.now(),
    }
    const updatedSessions = [...sessions, newSession]
    setSessions(updatedSessions)
    saveSessions(projectId, updatedSessions)
    setCurrentSessionId(sessionId)
    saveCurrentSessionId(projectId, sessionId)
    setMessages([])
    setIsSessionMenuOpen(false)
  }, [sessions, projectId])

  const switchSession = useCallback(
    (sessionId: string) => {
      setCurrentSessionId(sessionId)
      saveCurrentSessionId(projectId, sessionId)
      setMessages(loadMessages(projectId, sessionId))
      setIsSessionMenuOpen(false)
    },
    [projectId]
  )

  const deleteSession = useCallback(
    (sessionId: string, e: React.MouseEvent) => {
      e.stopPropagation()
      if (sessions.length <= 1) return // Keep at least one session

      const updatedSessions = sessions.filter((s) => s.id !== sessionId)
      setSessions(updatedSessions)
      saveSessions(projectId, updatedSessions)

      // Remove messages for deleted session
      localStorage.removeItem(getMessagesStorageKey(projectId, sessionId))

      // Switch to another session if current was deleted
      if (currentSessionId === sessionId) {
        const newCurrentSession = updatedSessions[updatedSessions.length - 1]
        setCurrentSessionId(newCurrentSession.id)
        saveCurrentSessionId(projectId, newCurrentSession.id)
        setMessages(loadMessages(projectId, newCurrentSession.id))
      }
    },
    [sessions, projectId, currentSessionId]
  )

  // Get current session
  const currentSession = sessions.find((s) => s.id === currentSessionId)

  // Drag handlers
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('button, textarea, input, [data-session-menu]')) return
    setIsDragging(true)
    dragOffset.current = {
      x: e.clientX - position.x,
      y: e.clientY - position.y,
    }
  }, [position])

  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (e: MouseEvent) => {
      setPosition({
        x: Math.max(0, Math.min(window.innerWidth - 400, e.clientX - dragOffset.current.x)),
        y: Math.max(0, Math.min(window.innerHeight - 100, e.clientY - dragOffset.current.y)),
      })
    }

    const handleMouseUp = () => setIsDragging(false)

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  // Use refs to access current values without adding them to dependencies
  // This prevents handleSend from being recreated on every keystroke
  const inputValueRef = useRef('')
  inputValueRef.current = input
  const messagesRef = useRef<AIChatMessage[]>([])
  messagesRef.current = messages
  const streamingContentRef = useRef('')

  // Format action results as text
  const formatActions = useCallback((actions: ChatAction[]) => {
    if (!actions || actions.length === 0) return ''
    return '\n\n' + actions
      .map(a => `${a.applied ? '[OK]' : '[NG]'} ${a.description}`)
      .join('\n')
  }, [])

  const handleSend = useCallback(() => {
    const trimmed = inputValueRef.current.trim()
    if (!trimmed || isLoading) return

    const userMessage: AIChatMessage = {
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    }
    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsLoading(true)
    setStreamingContent('')
    streamingContentRef.current = ''

    // Build conversation history for context using ref
    const history = messagesRef.current.map(m => ({ role: m.role, content: m.content }))
    let accumulatedActions: ChatAction[] = []

    // Start streaming
    streamControllerRef.current = aiApi.chatStream(
      projectId,
      trimmed,
      history,
      {
        onChunk: (text) => {
          streamingContentRef.current += text
          setStreamingContent(streamingContentRef.current)
        },
        onActions: (actions) => {
          accumulatedActions = actions
        },
        onDone: () => {
          // Create final message with accumulated content and actions
          let finalContent = streamingContentRef.current

          // Remove operations JSON block from display (it's for internal use)
          finalContent = finalContent.replace(/```operations\s*\n[\s\S]*?\n```/g, '').trim()

          // Append action results
          finalContent += formatActions(accumulatedActions)

          const aiMessage: AIChatMessage = {
            role: 'assistant',
            content: finalContent,
            timestamp: Date.now(),
          }
          setMessages(prev => [...prev, aiMessage])
          setStreamingContent('')
          streamingContentRef.current = ''
          setIsLoading(false)
          streamControllerRef.current = null
        },
        onError: (error) => {
          // If we have some content, show it with error
          let errorContent = streamingContentRef.current
          if (errorContent) {
            errorContent += `\n\n${t('aiChat.errorPrefix', { error })}`
          } else {
            errorContent = t('aiChat.communicationFailed', { error })
          }

          const errorMessage: AIChatMessage = {
            role: 'assistant',
            content: errorContent,
            timestamp: Date.now(),
          }
          setMessages(prev => [...prev, errorMessage])
          setStreamingContent('')
          streamingContentRef.current = ''
          setIsLoading(false)
          streamControllerRef.current = null
        },
      },
      aiProvider ?? undefined
    )
  }, [isLoading, projectId, aiProvider, formatActions])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      streamControllerRef.current?.abort()
    }
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  // Inline mode: show collapsed vertical bar when closed
  if (mode === 'inline' && !isOpen) {
    return (
      <div
        onClick={onToggle}
        className={`bg-gray-800 border-l border-gray-700 w-10 flex flex-col items-center py-3 cursor-pointer hover:bg-gray-700 transition-colors ${className}`}
      >
        <svg className="w-4 h-4 text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>AI</span>
      </div>
    )
  }

  // Floating mode: hide completely when closed
  if (mode === 'floating' && !isOpen) return null

  // Determine container classes based on mode
  const containerClasses = mode === 'inline'
    ? `flex flex-col bg-gray-800 border-l border-gray-700 relative ${className}`
    : 'fixed z-50 shadow-2xl rounded-lg overflow-hidden flex flex-col'

  const containerStyle = mode === 'inline'
    ? { width }
    : {
        left: position.x,
        top: position.y,
        width: 400,
        height: 500,
        cursor: isDragging ? 'grabbing' : 'default',
      }

  return (
    <div
      ref={panelRef}
      className={containerClasses}
      style={containerStyle}
    >
      {/* Resize handle for inline mode */}
      {mode === 'inline' && onResizeStart && (
        <div
          className="absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-blue-500/50 active:bg-blue-500 transition-colors z-10"
          onMouseDown={onResizeStart}
        />
      )}
      {/* Header - draggable only in floating mode */}
      <div
        className={`bg-gray-800 ${mode === 'floating' ? 'border border-gray-600 rounded-t-lg cursor-grab' : 'border-b border-gray-700'} px-3 py-2 flex items-center justify-between select-none flex-shrink-0`}
        onMouseDown={mode === 'floating' ? handleDragStart : undefined}
      >
        <span className="text-white text-sm font-medium">{t('aiChat.assistant')}</span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); setMessages([]); }}
              className="text-xs text-gray-400 hover:text-white px-2 py-1 rounded hover:bg-gray-700 transition-colors"
              title={t('aiChat.clearHistory')}
            >
              {t('aiChat.clearHistory')}
            </button>
          )}
          <button
            onClick={onToggle}
            className="text-gray-400 hover:text-white transition-colors"
            title={t('aiChat.close')}
          >
            {mode === 'inline' ? (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Session selector */}
      <div
        className={`bg-gray-800 ${mode === 'floating' ? 'border-x border-gray-600' : ''} px-3 py-2 flex items-center gap-2 flex-shrink-0`}
        data-session-menu
      >
        <div className="relative flex-1" ref={sessionMenuRef}>
          <button
            onClick={() => setIsSessionMenuOpen(!isSessionMenuOpen)}
            className="w-full flex items-center justify-between px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs text-white transition-colors"
          >
            <span className="truncate">{currentSession?.name || t('aiChat.selectConversation')}</span>
            <svg
              className={`w-3 h-3 ml-1 transition-transform ${isSessionMenuOpen ? 'rotate-180' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {/* Dropdown menu */}
          {isSessionMenuOpen && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-gray-700 border border-gray-600 rounded shadow-lg z-10 max-h-48 overflow-y-auto">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  onClick={() => switchSession(session.id)}
                  className={`flex items-center justify-between px-2 py-1.5 cursor-pointer hover:bg-gray-600 ${
                    session.id === currentSessionId ? 'bg-gray-600' : ''
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-white truncate">{session.name}</div>
                    <div className="text-xs text-gray-400">{formatSessionDate(session.createdAt)}</div>
                  </div>
                  {sessions.length > 1 && (
                    <button
                      onClick={(e) => deleteSession(session.id, e)}
                      className="ml-2 p-0.5 text-gray-400 hover:text-red-400 transition-colors"
                      title={t('aiChat.conversationDelete')}
                    >
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              ))}
              {/* New session button */}
              <div
                onClick={createNewSession}
                className="flex items-center gap-1 px-2 py-1.5 cursor-pointer hover:bg-gray-600 border-t border-gray-600"
              >
                <svg className="w-3 h-3 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                <span className="text-xs text-primary-400">{t('aiChat.newConversation')}</span>
              </div>
            </div>
          )}
        </div>

        {/* Quick new session button */}
        <button
          onClick={createNewSession}
          className="p-1 text-gray-400 hover:text-primary-400 transition-colors"
          title={t('aiChat.newConversation')}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
        </button>
      </div>

      {/* Provider display (read-only) */}
      <div className={`bg-gray-800 ${mode === 'floating' ? 'border-x border-gray-600' : ''} px-3 py-1.5 flex items-center gap-2 flex-shrink-0`}>
        <span className="text-xs text-gray-400">AI:</span>
        <span className="text-xs text-white">
          {aiProvider === 'openai' && t('aiChat.providerOpenAI')}
          {aiProvider === 'gemini' && t('aiChat.providerGemini')}
          {aiProvider === 'anthropic' && t('aiChat.providerAnthropic')}
          {!aiProvider && t('aiChat.providerNotSet')}
        </span>
      </div>

      {/* Messages area */}
      <div className={`bg-gray-900 ${mode === 'floating' ? 'border-x border-gray-600' : ''} flex-1 min-h-0 overflow-y-auto p-3 space-y-3`}>
        {messages.length === 0 && (
          <div className="text-center text-gray-500 text-sm py-8">
            <p>{t('aiChat.emptyMessage')}</p>
            <p className="mt-2 text-xs text-gray-600">{t('aiChat.emptyExample')}</p>
          </div>
        )}
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-700 text-gray-200'
              }`}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 max-w-[80%]">
              {streamingContent ? (
                <p className="whitespace-pre-wrap">{streamingContent.replace(/```operations\s*\n[\s\S]*?\n```/g, '').trim()}<span className="inline-block w-2 h-4 bg-primary-400 animate-pulse ml-0.5"></span></p>
              ) : (
                <div className="flex items-center gap-2 text-gray-400">
                  <div className="animate-spin rounded-full h-3 w-3 border-t border-b border-primary-400"></div>
                  {t('aiChat.thinking')}
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className={`bg-gray-800 ${mode === 'floating' ? 'border border-gray-600 rounded-b-lg' : 'border-t border-gray-700'} p-2 flex gap-2 flex-shrink-0`}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('aiChat.inputPlaceholder')}
          className="flex-1 bg-gray-700 text-white text-sm px-3 py-2 rounded resize-none outline-none focus:ring-1 focus:ring-primary-500 overflow-y-auto"
          rows={2}
          disabled={isLoading}
        />
        <button
          onClick={handleSend}
          disabled={isLoading || !input.trim()}
          className="px-3 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed self-end"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
        </button>
      </div>
    </div>
  )
}
