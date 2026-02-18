import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { sequencesApi, type SequenceListItem, type SnapshotItem } from '@/api/sequences'

interface SequencePanelProps {
  projectId: string
  currentSequenceId: string | undefined
  onSnapshotRestored?: () => void
}

export default function SequencePanel({
  projectId,
  currentSequenceId,
  onSnapshotRestored,
}: SequencePanelProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('assets')
  const [sequences, setSequences] = useState<SequenceListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateInput, setShowCreateInput] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const createInputRef = useRef<HTMLInputElement>(null)

  // Snapshot state
  const [snapshots, setSnapshots] = useState<SnapshotItem[]>([])
  const [snapshotsLoading, setSnapshotsLoading] = useState(false)
  const [showSnapshotInput, setShowSnapshotInput] = useState(false)
  const [snapshotName, setSnapshotName] = useState('')
  const [creatingSnapshot, setCreatingSnapshot] = useState(false)
  const snapshotInputRef = useRef<HTMLInputElement>(null)

  const fetchSequences = useCallback(async () => {
    try {
      setLoading(true)
      const list = await sequencesApi.list(projectId)
      setSequences(list)
    } catch (error) {
      console.error('Failed to fetch sequences:', error)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  const fetchSnapshots = useCallback(async () => {
    if (!currentSequenceId) return
    try {
      setSnapshotsLoading(true)
      const list = await sequencesApi.listSnapshots(projectId, currentSequenceId)
      setSnapshots(list)
    } catch (error) {
      console.error('Failed to fetch snapshots:', error)
    } finally {
      setSnapshotsLoading(false)
    }
  }, [projectId, currentSequenceId])

  useEffect(() => {
    fetchSequences()
  }, [fetchSequences])

  useEffect(() => {
    fetchSnapshots()
  }, [fetchSnapshots])

  useEffect(() => {
    if (showCreateInput && createInputRef.current) {
      createInputRef.current.focus()
    }
  }, [showCreateInput])

  useEffect(() => {
    if (showSnapshotInput && snapshotInputRef.current) {
      snapshotInputRef.current.focus()
    }
  }, [showSnapshotInput])

  const handleCreate = async () => {
    if (!newName.trim() || creating) return

    setCreating(true)
    try {
      await sequencesApi.create(projectId, newName.trim())
      setShowCreateInput(false)
      setNewName('')
      await fetchSequences()
    } catch (error) {
      console.error('Failed to create sequence:', error)
      alert(t('sequence.errors.createFailed'))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (seq: SequenceListItem) => {
    if (seq.is_default) return
    if (!confirm(t('sequence.errors.deleteConfirm', { name: seq.name }))) return

    try {
      await sequencesApi.delete(projectId, seq.id)
      setSequences(sequences.filter(s => s.id !== seq.id))
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number } }
      if (axiosError.response?.status === 403) {
        alert(t('sequence.errors.cannotDeleteDefault'))
      } else {
        console.error('Failed to delete sequence:', error)
        alert(t('sequence.errors.deleteFailed'))
      }
    }
  }

  const handleCopy = async (seq: SequenceListItem) => {
    const copyName = t('sequence.copy', { name: seq.name })
    try {
      await sequencesApi.copy(projectId, seq.id, copyName)
      await fetchSequences()
    } catch (error) {
      console.error('Failed to copy sequence:', error)
      alert(t('sequence.errors.copyFailed'))
    }
  }

  const handleSwitchSequence = (seq: SequenceListItem) => {
    if (seq.id === currentSequenceId) return
    navigate(`/project/${projectId}/sequence/${seq.id}`)
  }

  const handleCreateSnapshot = async () => {
    if (!snapshotName.trim() || creatingSnapshot || !currentSequenceId) return

    setCreatingSnapshot(true)
    try {
      await sequencesApi.createSnapshot(projectId, currentSequenceId, snapshotName.trim())
      setShowSnapshotInput(false)
      setSnapshotName('')
      await fetchSnapshots()
    } catch (error) {
      console.error('Failed to create snapshot:', error)
      alert(t('sequence.errors.snapshotCreateFailed'))
    } finally {
      setCreatingSnapshot(false)
    }
  }

  const handleRestoreSnapshot = async (snap: SnapshotItem) => {
    if (!currentSequenceId) return
    if (!confirm(t('sequence.history.restoreConfirm', { name: snap.name }))) return

    try {
      await sequencesApi.restoreSnapshot(projectId, currentSequenceId, snap.id)
      onSnapshotRestored?.()
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number; data?: { detail?: string } } }
      if (axiosError.response?.status === 403) {
        alert(t('sequence.errors.restoreLockRequired'))
      } else {
        console.error('Failed to restore snapshot:', error)
        alert(axiosError.response?.data?.detail || t('sequence.errors.restoreFailed'))
      }
    }
  }

  const handleDeleteSnapshot = async (snap: SnapshotItem) => {
    if (!currentSequenceId) return
    if (!confirm(t('sequence.history.deleteConfirm', { name: snap.name }))) return

    try {
      await sequencesApi.deleteSnapshot(projectId, currentSequenceId, snap.id)
      setSnapshots(snapshots.filter(s => s.id !== snap.id))
    } catch (error) {
      console.error('Failed to delete snapshot:', error)
      alert(t('sequence.errors.snapshotDeleteFailed'))
    }
  }

  const formatDuration = (ms: number) => {
    const totalSeconds = Math.floor(ms / 1000)
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleString('ja-JP', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // Find current sequence name for the history section header
  const currentSequenceName = sequences.find(s => s.id === currentSequenceId)?.name

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <button
          onClick={() => {
            if (showCreateInput) {
              setShowCreateInput(false)
              setNewName('')
            } else {
              setShowCreateInput(true)
            }
          }}
          className="w-full px-3 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {t('sequence.newSequence')}
        </button>

        {showCreateInput && (
          <div className="mt-3 bg-gray-700/50 rounded-lg p-3">
            <div className="flex gap-2">
              <input
                ref={createInputRef}
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newName.trim()) handleCreate()
                  if (e.key === 'Escape') {
                    setShowCreateInput(false)
                    setNewName('')
                  }
                }}
                placeholder={t('sequence.sequenceNamePlaceholder')}
                className="flex-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
                disabled={creating}
              />
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="px-3 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
              >
                {creating ? (
                  <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-white"></div>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </button>
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {t('sequence.createHint')}
            </div>
          </div>
        )}
      </div>

      {/* Sequence List */}
      <div className="overflow-y-auto p-2">
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : sequences.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">
            {t('sequence.empty')}
          </div>
        ) : (
          <div className="space-y-1">
            {sequences.map(seq => {
              const isCurrent = seq.id === currentSequenceId

              return (
                <div
                  key={seq.id}
                  className={`rounded-lg p-2 cursor-pointer transition-colors group ${
                    isCurrent
                      ? 'bg-primary-900/40 ring-1 ring-primary-500'
                      : 'bg-gray-700 hover:bg-gray-600'
                  }`}
                  onDoubleClick={() => handleSwitchSequence(seq)}
                >
                  <div className="flex items-center gap-2">
                    {/* Sequence Thumbnail */}
                    <div className={`w-14 h-8 rounded overflow-hidden flex items-center justify-center flex-shrink-0 ${
                      isCurrent ? 'ring-1 ring-primary-500' : ''
                    } bg-gray-900`}>
                      {seq.thumbnail_url ? (
                        <img
                          src={seq.thumbnail_url}
                          alt={seq.name}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <svg className="w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
                        </svg>
                      )}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">
                        {seq.name}
                        {isCurrent && (
                          <span className="ml-2 text-xs bg-primary-600/60 text-primary-200 px-1.5 py-0.5 rounded">{t('sequence.current')}</span>
                        )}
                      </p>
                      <p className="text-xs text-gray-400">
                        {formatDuration(seq.duration_ms)}
                        {seq.is_default && ` \u00B7 ${t('sequence.default')}`}
                      </p>
                    </div>

                    {/* Lock indicator */}
                    {seq.locked_by && (
                      <span className="text-xs text-yellow-400 flex items-center gap-1 flex-shrink-0" title={seq.lock_holder_name || ''}>
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                        {seq.lock_holder_name}
                      </span>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-all flex-shrink-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleCopy(seq)
                        }}
                        className="p-1 text-gray-400 hover:text-blue-400 transition-colors"
                        title={t('sequence.actions.copy')}
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                      </button>
                      {!seq.is_default && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDelete(seq)
                          }}
                          className="p-1 text-gray-400 hover:text-red-500 transition-colors"
                          title={t('sequence.actions.delete')}
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Snapshot History Section */}
      {currentSequenceId && (
        <div className="border-t border-gray-700 flex flex-col min-h-0 flex-1">
          <div className="p-3 pb-2">
            <h3 className="text-xs font-medium text-gray-400 mb-2">
              {t('sequence.history.title', { name: currentSequenceName || '...' })}
            </h3>
            <button
              onClick={() => {
                if (showSnapshotInput) {
                  setShowSnapshotInput(false)
                  setSnapshotName('')
                } else {
                  setShowSnapshotInput(true)
                }
              }}
              className="w-full px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded transition-colors flex items-center justify-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              {t('sequence.history.saveCheckpoint')}
            </button>

            {showSnapshotInput && (
              <div className="mt-2 bg-gray-700/50 rounded-lg p-2">
                <div className="flex gap-2">
                  <input
                    ref={snapshotInputRef}
                    type="text"
                    value={snapshotName}
                    onChange={(e) => setSnapshotName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && snapshotName.trim()) handleCreateSnapshot()
                      if (e.key === 'Escape') {
                        setShowSnapshotInput(false)
                        setSnapshotName('')
                      }
                    }}
                    placeholder={t('sequence.history.checkpointNamePlaceholder')}
                    className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-primary-500"
                    disabled={creatingSnapshot}
                  />
                  <button
                    onClick={handleCreateSnapshot}
                    disabled={creatingSnapshot || !snapshotName.trim()}
                    className="px-2 py-1.5 bg-primary-600 hover:bg-primary-700 text-white text-xs rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {creatingSnapshot ? (
                      <div className="animate-spin rounded-full h-3.5 w-3.5 border-t-2 border-b-2 border-white"></div>
                    ) : (
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Snapshot List */}
          <div className="flex-1 overflow-y-auto px-2 pb-2">
            {snapshotsLoading ? (
              <div className="flex justify-center py-4">
                <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-primary-500"></div>
              </div>
            ) : snapshots.length === 0 ? (
              <div className="text-center py-4 text-gray-500 text-xs">
                {t('sequence.history.empty')}
              </div>
            ) : (
              <div className="space-y-1">
                {snapshots.map(snap => (
                  <div
                    key={snap.id}
                    className="rounded p-2 bg-gray-750 hover:bg-gray-700 transition-colors group"
                  >
                    <div className="flex items-start gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-white truncate">{snap.name}</p>
                        <p className="text-[10px] text-gray-500">
                          {formatDate(snap.created_at)} · {formatDuration(snap.duration_ms)}
                        </p>
                      </div>
                      <div className="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => handleRestoreSnapshot(snap)}
                          className="p-1 text-gray-400 hover:text-blue-400 transition-colors"
                          title={t('sequence.history.restoreTitle')}
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                          </svg>
                        </button>
                        <button
                          onClick={() => handleDeleteSnapshot(snap)}
                          className="p-1 text-gray-400 hover:text-red-500 transition-colors"
                          title={t('library.action.delete')}
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
