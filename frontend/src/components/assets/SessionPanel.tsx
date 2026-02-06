import { useEffect, useState, useRef, useCallback } from 'react'
import { assetsApi, type Asset, type SessionData } from '@/api/assets'
import type { TimelineData } from '@/store/projectStore'

interface SessionPanelProps {
  projectId: string
  currentTimeline: TimelineData | null
  currentSessionId: string | null  // ID of currently loaded session (for overwrite)
  currentSessionName: string | null  // Name of currently loaded session
  assets: Asset[]  // Current project assets for reference extraction
  onOpenSession: (sessionData: SessionData, sessionId?: string, sessionName?: string) => void
  onSave: (sessionId: string | null, sessionName: string) => Promise<void>  // Called when saving
  onAssetsChange?: () => void
  refreshTrigger?: number
}

export default function SessionPanel({
  projectId,
  currentTimeline: _currentTimeline,
  currentSessionId,
  currentSessionName,
  assets: _assets,
  onOpenSession,
  onSave,
  onAssetsChange,
  refreshTrigger,
}: SessionPanelProps) {
  // Note: currentTimeline and assets are available for future use but not currently used
  void _currentTimeline
  void _assets
  const [sessions, setSessions] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingSession, setLoadingSession] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [showSaveAsDialog, setShowSaveAsDialog] = useState(false)
  const [newSessionName, setNewSessionName] = useState('')
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editingSessionName, setEditingSessionName] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)
  const saveAsInputRef = useRef<HTMLInputElement>(null)

  // Fetch sessions
  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true)
      const allAssets = await assetsApi.list(projectId)
      const sessionAssets = allAssets.filter(a => a.type === 'session')
      setSessions(sessionAssets)
    } catch (error) {
      console.error('Failed to fetch sessions:', error)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  // Refresh when trigger changes
  useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchSessions()
    }
  }, [refreshTrigger, fetchSessions])

  // Focus edit input when editing
  useEffect(() => {
    if (editingSessionId && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingSessionId])

  // Focus save-as input when dialog opens
  useEffect(() => {
    if (showSaveAsDialog && saveAsInputRef.current) {
      saveAsInputRef.current.focus()
    }
  }, [showSaveAsDialog])

  // Handle opening a session
  const handleOpenSessionClick = async (session: Asset) => {
    if (loadingSession) return

    setLoadingSession(session.id)
    try {
      const sessionData = await assetsApi.getSession(projectId, session.id)
      // Pass session ID and name along with the data
      onOpenSession(sessionData, session.id, session.name)
    } catch (error) {
      console.error('Failed to load session:', error)
      alert('セクションの読み込みに失敗しました')
    } finally {
      setLoadingSession(null)
    }
  }

  // Handle overwrite save
  const handleOverwriteSave = async () => {
    if (!currentSessionId || !currentSessionName || saving) return

    setSaving(true)
    try {
      await onSave(currentSessionId, currentSessionName)
      await fetchSessions()
    } catch (error) {
      console.error('Failed to save session:', error)
    } finally {
      setSaving(false)
    }
  }

  // Handle save as new
  const handleSaveAs = async () => {
    if (!newSessionName.trim() || saving) return

    setSaving(true)
    try {
      await onSave(null, newSessionName.trim())
      setShowSaveAsDialog(false)
      setNewSessionName('')
      await fetchSessions()
    } catch (error) {
      console.error('Failed to save session:', error)
    } finally {
      setSaving(false)
    }
  }

  // Handle delete
  const handleDelete = async (sessionId: string) => {
    if (!confirm('このセクションを削除しますか?')) return

    try {
      await assetsApi.delete(projectId, sessionId)
      setSessions(sessions.filter(s => s.id !== sessionId))
      onAssetsChange?.()
    } catch (error) {
      console.error('Failed to delete session:', error)
      alert('削除に失敗しました')
    }
  }

  // Handle rename
  const handleRename = async (sessionId: string) => {
    if (!editingSessionName.trim()) {
      setEditingSessionId(null)
      return
    }

    try {
      const updated = await assetsApi.rename(projectId, sessionId, editingSessionName.trim())
      setSessions(sessions.map(s => s.id === sessionId ? updated : s))
      setEditingSessionId(null)
      setEditingSessionName('')
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number } }
      if (axiosError.response?.status === 409) {
        alert('同じ名前のセクションが既に存在します')
      } else {
        console.error('Failed to rename session:', error)
        alert('名前の変更に失敗しました')
      }
    }
  }

  const formatDate = (isoString: string | undefined) => {
    if (!isoString) return ''
    try {
      const date = new Date(isoString)
      return date.toLocaleDateString('ja-JP', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return ''
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-white font-medium mb-3">セクション</h2>

        {/* Current Session Info */}
        <div className="bg-gray-700/50 rounded-lg p-2 mb-3">
          <div className="text-xs text-gray-400 mb-1">現在のセクション</div>
          {currentSessionName ? (
            <div className="text-sm text-white truncate">{currentSessionName}</div>
          ) : (
            <div className="text-sm text-gray-500 italic">未保存</div>
          )}
        </div>

        {/* Save Buttons */}
        <div className="flex gap-2">
          <button
            onClick={handleOverwriteSave}
            disabled={!currentSessionId || saving}
            className="flex-1 px-3 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            title={currentSessionId ? '現在のセクションを上書き保存 (Ctrl+S)' : 'セクションが選択されていません'}
          >
            {saving && !showSaveAsDialog ? (
              <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>
            ) : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
              </svg>
            )}
            上書き保存
          </button>
          <button
            onClick={() => {
              setNewSessionName(currentSessionName || '')
              setShowSaveAsDialog(true)
            }}
            disabled={saving}
            className="flex-1 px-3 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            名前をつけて保存
          </button>
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">
            保存されたセクションがありません
          </div>
        ) : (
          <div className="space-y-1">
            {sessions.map(session => {
              const isLoading = loadingSession === session.id
              const isCurrent = session.id === currentSessionId
              const createdAt = session.metadata?.created_at

              return (
                <div
                  key={session.id}
                  className={`bg-gray-700 rounded-lg p-2 cursor-pointer hover:bg-gray-600 transition-colors group ${
                    isLoading ? 'opacity-70' : ''
                  } ${isCurrent ? 'ring-1 ring-primary-500' : ''}`}
                  onDoubleClick={() => handleOpenSessionClick(session)}
                >
                  <div className="flex items-center gap-2">
                    {/* Session Icon */}
                    <div className={`w-10 h-10 rounded flex items-center justify-center flex-shrink-0 ${
                      isCurrent ? 'bg-primary-600/50' : 'bg-primary-900/50'
                    }`}>
                      {isLoading ? (
                        <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-primary-500"></div>
                      ) : (
                        <svg className="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      )}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      {editingSessionId === session.id ? (
                        <input
                          ref={editInputRef}
                          type="text"
                          value={editingSessionName}
                          onChange={(e) => setEditingSessionName(e.target.value)}
                          onBlur={() => handleRename(session.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleRename(session.id)
                            if (e.key === 'Escape') {
                              setEditingSessionId(null)
                              setEditingSessionName('')
                            }
                          }}
                          className="w-full px-1 py-0 bg-gray-600 border border-primary-500 rounded text-white text-sm focus:outline-none"
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <p className="text-sm text-white truncate">
                          {session.name}
                          {isCurrent && (
                            <span className="ml-2 text-xs text-primary-400">(現在)</span>
                          )}
                        </p>
                      )}
                      <p className="text-xs text-gray-400">
                        {createdAt && formatDate(createdAt)}
                      </p>
                    </div>

                    {/* Actions */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingSessionId(session.id)
                        setEditingSessionName(session.name)
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-primary-500 transition-all"
                      title="名前を変更"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(session.id)
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all"
                      title="削除"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Save As Dialog */}
      {showSaveAsDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
          <div className="bg-gray-800 rounded-lg p-6 w-96 max-w-[90vw]">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-medium text-lg">名前をつけて保存</h3>
              <button
                onClick={() => {
                  setShowSaveAsDialog(false)
                  setNewSessionName('')
                }}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">セクション名</label>
              <input
                ref={saveAsInputRef}
                type="text"
                value={newSessionName}
                onChange={(e) => setNewSessionName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newSessionName.trim()) {
                    handleSaveAs()
                  }
                  if (e.key === 'Escape') {
                    setShowSaveAsDialog(false)
                    setNewSessionName('')
                  }
                }}
                placeholder="例: intro_v1"
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:border-primary-500"
                disabled={saving}
              />
            </div>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowSaveAsDialog(false)
                  setNewSessionName('')
                }}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
                disabled={saving}
              >
                キャンセル
              </button>
              <button
                onClick={handleSaveAs}
                disabled={saving || !newSessionName.trim()}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {saving ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>
                    保存中...
                  </>
                ) : (
                  '保存'
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
