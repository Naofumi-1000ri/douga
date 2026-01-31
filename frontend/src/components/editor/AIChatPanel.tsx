import { useState, useRef, useEffect, useCallback } from 'react'
import { aiApi, type AIChatMessage, type AIProvider } from '@/services/aiApi'

interface AIChatPanelProps {
  projectId: string
  isOpen: boolean
  onToggle: () => void
}

export default function AIChatPanel({ projectId, isOpen, onToggle }: AIChatPanelProps) {
  const [messages, setMessages] = useState<AIChatMessage[]>([])
  const [provider, setProvider] = useState<AIProvider>('openai')
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

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

  // Drag handlers
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('button, textarea, input')) return
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

  const handleSend = useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return

    const userMessage: AIChatMessage = {
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    }
    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsLoading(true)

    try {
      // Build conversation history for context
      const history = messages.map(m => ({ role: m.role, content: m.content }))
      const response = await aiApi.chat(projectId, trimmed, history, provider)

      let content = response.message
      // Append action results if any
      if (response.actions && response.actions.length > 0) {
        const actionSummary = response.actions
          .map(a => `${a.applied ? '[OK]' : '[NG]'} ${a.description}`)
          .join('\n')
        content += '\n\n' + actionSummary
      }

      const aiMessage: AIChatMessage = {
        role: 'assistant',
        content,
        timestamp: Date.now(),
      }
      setMessages(prev => [...prev, aiMessage])
    } catch (error) {
      const errorMessage: AIChatMessage = {
        role: 'assistant',
        content: 'AIとの通信に失敗しました。ネットワーク接続とAPIキー設定を確認してください。',
        timestamp: Date.now(),
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
    }
  }, [input, isLoading, projectId, messages])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  if (!isOpen) return null

  return (
    <div
      ref={panelRef}
      className="fixed z-50 shadow-2xl rounded-lg overflow-hidden flex flex-col"
      style={{
        left: position.x,
        top: position.y,
        width: 400,
        height: 500,
        cursor: isDragging ? 'grabbing' : 'default',
      }}
    >
      {/* Header - draggable */}
      <div
        className="bg-gray-800 border border-gray-600 rounded-t-lg px-4 py-2 flex items-center justify-between cursor-grab select-none flex-shrink-0"
        onMouseDown={handleDragStart}
      >
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
          </svg>
          <span className="text-white text-sm font-medium">AI アシスタント</span>
        </div>
        <button
          onClick={onToggle}
          className="text-gray-400 hover:text-white transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Provider selector */}
      <div className="bg-gray-800 border-x border-gray-600 px-3 py-2 flex items-center gap-2 flex-shrink-0">
        <span className="text-xs text-gray-400">AI:</span>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as AIProvider)}
          className="bg-gray-700 text-white text-xs px-2 py-1 rounded border border-gray-600 outline-none focus:ring-1 focus:ring-primary-500"
        >
          <option value="openai">OpenAI (GPT-4o)</option>
          <option value="gemini">Google Gemini</option>
          <option value="anthropic">Anthropic Claude</option>
        </select>
      </div>

      {/* Messages area */}
      <div className="bg-gray-900 border-x border-gray-600 flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 text-sm py-8">
            <p>タイムラインの編集指示を入力してください</p>
            <p className="mt-2 text-xs text-gray-600">例: 「クリップを前に詰めて」「フェードインを追加」</p>
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
            <div className="bg-gray-700 rounded-lg px-3 py-2 text-sm text-gray-400">
              <div className="flex items-center gap-2">
                <div className="animate-spin rounded-full h-3 w-3 border-t border-b border-primary-400"></div>
                考え中...
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="bg-gray-800 border border-gray-600 rounded-b-lg p-2 flex gap-2 flex-shrink-0">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="AIへの指示を入力..."
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
