import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import { formatDistanceToNow } from 'date-fns'
import { ja, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import APIKeyManager from '@/components/settings/APIKeyManager'
import { membersApi, type Invitation } from '@/api/members'

export default function Dashboard() {
  const { projects, loading, fetchProjects, createProject, deleteProject } = useProjectStore()
  const { user, signOut } = useAuthStore()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation('dashboard')
  const [showNewProject, setShowNewProject] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [showAPIKeys, setShowAPIKeys] = useState(false)
  const [invitations, setInvitations] = useState<Invitation[]>([])

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    fetchInvitations()
  }, [])

  const fetchInvitations = async () => {
    try {
      const data = await membersApi.listInvitations()
      setInvitations(data)
    } catch (error) {
      console.error('Failed to fetch invitations:', error)
    }
  }

  const handleAcceptInvitation = async (invitation: Invitation) => {
    try {
      await membersApi.acceptInvitation(invitation.project_id, invitation.id)
      setInvitations(prev => prev.filter(i => i.id !== invitation.id))
      fetchProjects() // Refresh project list
    } catch (error) {
      console.error('Failed to accept invitation:', error)
    }
  }

  const handleDeclineInvitation = async (invitation: Invitation) => {
    try {
      await membersApi.removeMember(invitation.project_id, invitation.id)
      setInvitations(prev => prev.filter(i => i.id !== invitation.id))
    } catch (error) {
      console.error('Failed to decline invitation:', error)
    }
  }

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return
    try {
      const project = await createProject(newProjectName)
      navigate(`/project/${project.id}`)
    } catch (error) {
      console.error('Failed to create project:', error)
    }
  }

  const handleDeleteProject = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (confirm(t('projects.deleteConfirm'))) {
      await deleteProject(id)
    }
  }

  const formatDuration = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  const dateFnsLocale = i18n.language === 'ja' ? ja : enUS

  return (
    <div className="min-h-screen bg-gray-900">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <h1 className="text-xl font-bold text-white">{t('title')}</h1>
            <div className="flex items-center gap-4">
              <span className="text-gray-400">{user?.email}</span>
              <button
                onClick={() => setShowAPIKeys(true)}
                className="p-2 text-gray-400 hover:text-white transition-colors"
                title={t('header.apiKeyManagement')}
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
              </button>
              <button
                onClick={signOut}
                className="text-gray-400 hover:text-white transition-colors"
              >
                {t('header.signOut')}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Invitations Section */}
        {invitations.length > 0 && (
          <div className="mb-8">
            <h3 className="text-lg font-medium text-white mb-4">{t('invitations.sectionTitle')}</h3>
            <div className="space-y-3">
              {invitations.map((inv) => (
                <div
                  key={inv.id}
                  className="flex items-center justify-between p-4 bg-gray-800 rounded-lg border border-gray-700"
                >
                  <div>
                    <span className="text-white font-medium">{inv.project_name}</span>
                    {inv.invited_by_name && (
                      <span className="text-gray-400 text-sm ml-2">
                        {t('invitations.invitedBy', { name: inv.invited_by_name })}
                      </span>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleAcceptInvitation(inv)}
                      className="px-3 py-1.5 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors text-sm"
                    >
                      {t('invitations.accept')}
                    </button>
                    <button
                      onClick={() => handleDeclineInvitation(inv)}
                      className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg transition-colors text-sm"
                    >
                      {t('invitations.decline')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex justify-between items-center mb-8">
          <h2 className="text-2xl font-bold text-white">{t('projects.sectionTitle')}</h2>
          <button
            onClick={() => setShowNewProject(true)}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors"
          >
            {t('projects.newProject')}
          </button>
        </div>

        {/* New Project Modal */}
        {showNewProject && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
            <div className="bg-gray-800 rounded-lg p-6 w-full max-w-md">
              <h3 className="text-lg font-bold text-white mb-4">{t('newProjectModal.title')}</h3>
              <input
                type="text"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                placeholder={t('newProjectModal.namePlaceholder')}
                className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:border-primary-500"
                autoFocus
              />
              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={() => {
                    setShowNewProject(false)
                    setNewProjectName('')
                  }}
                  className="px-4 py-2 text-gray-400 hover:text-white transition-colors"
                >
                  {t('newProjectModal.cancel')}
                </button>
                <button
                  onClick={handleCreateProject}
                  disabled={!newProjectName.trim()}
                  className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('newProjectModal.create')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Project Grid */}
        {loading ? (
          <div className="flex justify-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center py-20">
            {/* Video icon */}
            <div className="mx-auto mb-6 w-20 h-20 rounded-2xl bg-gray-800 flex items-center justify-center">
              <svg className="w-10 h-10 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-gray-300 mb-2">{t('projects.emptyTitle')}</h3>
            <p className="text-gray-500 text-sm mb-6">{t('projects.emptyMessage')}</p>
            <button
              onClick={() => setShowNewProject(true)}
              className="px-6 py-2.5 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors inline-flex items-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              {t('projects.newProjectCreate')}
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project) => (
              <div
                key={project.id}
                onClick={() => navigate(`/project/${project.id}`)}
                className="bg-gray-800 rounded-lg overflow-hidden cursor-pointer hover:ring-2 hover:ring-primary-500/50 hover:bg-gray-800/80 transition-all duration-200 group"
              >
                {/* Thumbnail */}
                <div className="aspect-video bg-gray-700 flex items-center justify-center relative overflow-hidden">
                  {project.thumbnail_url ? (
                    <img
                      src={project.thumbnail_url}
                      alt={project.name}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <svg
                      className="w-12 h-12 text-gray-600"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
                      />
                    </svg>
                  )}
                  {/* Hover overlay with play icon */}
                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center">
                    <svg className="w-12 h-12 text-white opacity-0 group-hover:opacity-80 transition-opacity" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </div>
                </div>

                {/* Info */}
                <div className="p-4">
                  <div className="flex justify-between items-start">
                    <div>
                      <h3 className="font-medium text-white truncate">{project.name}</h3>
                      {project.is_shared && (
                        <div className="flex items-center gap-1.5 mt-1">
                          <span className="px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded text-xs">
                            {t('projects.shared')}
                          </span>
                          {project.owner_name && (
                            <span className="text-gray-500 text-xs">
                              {project.owner_name}
                            </span>
                          )}
                        </div>
                      )}
                      <p className="text-sm text-gray-400 mt-1">
                        {formatDuration(project.duration_ms)} •{' '}
                        {formatDistanceToNow(new Date(project.updated_at), {
                          addSuffix: true,
                          locale: dateFnsLocale,
                        })}
                      </p>
                    </div>
                    {(!project.is_shared || project.role === 'owner') && (
                      <button
                        onClick={(e) => handleDeleteProject(project.id, e)}
                        className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                      >
                        <svg
                          className="w-5 h-5"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                          />
                        </svg>
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* API Key Manager Modal */}
      <APIKeyManager isOpen={showAPIKeys} onClose={() => setShowAPIKeys(false)} />
    </div>
  )
}
