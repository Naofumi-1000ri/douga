import { useState, useEffect } from 'react'
import { membersApi, type Member } from '@/api/members'

interface Props {
  isOpen: boolean
  onClose: () => void
  projectId: string
  isOwner: boolean
}

export default function MembersManager({ isOpen, onClose, projectId, isOwner }: Props) {
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviting, setInviting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (isOpen && projectId) {
      fetchMembers()
    }
  }, [isOpen, projectId])

  const fetchMembers = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await membersApi.listMembers(projectId)
      setMembers(data)
    } catch (err) {
      console.error('Failed to fetch members:', err)
      setError('メンバーの取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return
    setInviting(true)
    setError(null)
    try {
      await membersApi.inviteMember(projectId, inviteEmail.trim())
      setInviteEmail('')
      fetchMembers()
    } catch (err: any) {
      const detail = err.response?.data?.detail || '招待に失敗しました'
      setError(detail)
    } finally {
      setInviting(false)
    }
  }

  const handleRemove = async (memberId: string) => {
    if (!confirm('このメンバーを削除しますか？')) return
    try {
      await membersApi.removeMember(projectId, memberId)
      fetchMembers()
    } catch (err: any) {
      const detail = err.response?.data?.detail || '削除に失敗しました'
      setError(detail)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
      <div className="bg-gray-800 rounded-lg w-full max-w-2xl max-h-[80vh] overflow-hidden">
        {/* Header */}
        <div className="flex justify-between items-center p-4 border-b border-gray-700">
          <h2 className="text-lg font-bold text-white">メンバー管理</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-[calc(80vh-120px)]">
          {/* Error */}
          {error && (
            <div className="mb-4 p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Invite form (owner only) */}
          {isOwner && (
            <div className="mb-6">
              <label className="block text-sm text-gray-400 mb-2">メンバーを招待</label>
              <div className="flex gap-2">
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleInvite()}
                  placeholder="メールアドレス"
                  className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:border-primary-500 text-sm"
                />
                <button
                  onClick={handleInvite}
                  disabled={!inviteEmail.trim() || inviting}
                  className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                >
                  {inviting ? '招待中...' : '招待'}
                </button>
              </div>
            </div>
          )}

          {/* Members list */}
          {loading ? (
            <div className="flex justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
            </div>
          ) : members.length === 0 ? (
            <p className="text-gray-500 text-center py-8">メンバーがいません</p>
          ) : (
            <div className="space-y-2">
              {members.map((member) => (
                <div
                  key={member.id}
                  className="flex items-center justify-between p-3 bg-gray-700/50 rounded-lg"
                >
                  <div className="flex items-center gap-3">
                    {/* Avatar */}
                    <div className="w-8 h-8 rounded-full bg-gray-600 flex items-center justify-center text-sm text-white font-medium">
                      {member.avatar_url ? (
                        <img src={member.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                      ) : (
                        member.name.charAt(0).toUpperCase()
                      )}
                    </div>
                    {/* Info */}
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-white text-sm">{member.name}</span>
                        <span className={`px-1.5 py-0.5 rounded text-xs ${
                          member.role === 'owner'
                            ? 'bg-amber-500/20 text-amber-400'
                            : 'bg-blue-500/20 text-blue-400'
                        }`}>
                          {member.role === 'owner' ? 'オーナー' : 'エディター'}
                        </span>
                        {!member.accepted_at && (
                          <span className="px-1.5 py-0.5 rounded text-xs bg-gray-500/20 text-gray-400">
                            招待中
                          </span>
                        )}
                      </div>
                      <span className="text-gray-400 text-xs">{member.email}</span>
                    </div>
                  </div>
                  {/* Remove button (owner only, can't remove owner) */}
                  {isOwner && member.role !== 'owner' && (
                    <button
                      onClick={() => handleRemove(member.id)}
                      className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                      title="メンバーを削除"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
