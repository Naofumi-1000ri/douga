import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { listAPIKeys, createAPIKey, deleteAPIKey, APIKey } from '@/api/apiKeys'
import { formatDistanceToNow } from 'date-fns'
import { ja, enUS } from 'date-fns/locale'

interface Props {
  isOpen: boolean
  onClose: () => void
}

export default function APIKeyManager({ isOpen, onClose }: Props) {
  const { t, i18n } = useTranslation('settings')
  const [keys, setKeys] = useState<APIKey[]>([])
  const [loading, setLoading] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (isOpen) {
      fetchKeys()
    }
  }, [isOpen])

  const fetchKeys = async () => {
    setLoading(true)
    try {
      const data = await listAPIKeys()
      setKeys(data.filter(k => k.is_active))
    } catch (error) {
      console.error('Failed to fetch API keys:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async () => {
    if (!newKeyName.trim()) return
    try {
      const result = await createAPIKey({ name: newKeyName })
      setCreatedKey(result.key)
      setNewKeyName('')
      fetchKeys()
    } catch (error) {
      console.error('Failed to create API key:', error)
    }
  }

  const handleDelete = async (keyId: string) => {
    if (!confirm(t('apiKey.errors.deleteConfirm'))) return
    try {
      await deleteAPIKey(keyId)
      fetchKeys()
    } catch (error) {
      console.error('Failed to delete API key:', error)
    }
  }

  const handleCopy = async () => {
    if (createdKey) {
      await navigator.clipboard.writeText(createdKey)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
      <div className="bg-gray-800 rounded-lg w-full max-w-2xl max-h-[80vh] overflow-hidden">
        {/* Header */}
        <div className="flex justify-between items-center p-4 border-b border-gray-700">
          <h2 className="text-lg font-bold text-white">{t('apiKey.title')}</h2>
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
          {/* Created Key Alert */}
          {createdKey && (
            <div className="mb-4 p-4 bg-green-900/50 border border-green-700 rounded-lg">
              <p className="text-green-400 text-sm mb-2">
                {t('apiKey.createdAlert')}
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 p-2 bg-gray-900 rounded text-sm text-green-300 font-mono break-all">
                  {createdKey}
                </code>
                <button
                  onClick={handleCopy}
                  className="px-3 py-2 bg-green-700 hover:bg-green-600 text-white rounded transition-colors text-sm"
                >
                  {copied ? t('apiKey.copied') : t('apiKey.copy')}
                </button>
              </div>
              <button
                onClick={() => setCreatedKey(null)}
                className="mt-2 text-sm text-gray-400 hover:text-white"
              >
                {t('apiKey.dismiss')}
              </button>
            </div>
          )}

          {/* Create New Key */}
          <div className="mb-6">
            <h3 className="text-sm font-medium text-gray-400 mb-2">{t('apiKey.newKey.title')}</h3>
            <div className="flex gap-2">
              <input
                type="text"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                placeholder={t('apiKey.newKey.placeholder')}
                className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:border-primary-500"
              />
              <button
                onClick={handleCreate}
                disabled={!newKeyName.trim()}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {t('apiKey.newKey.create')}
              </button>
            </div>
          </div>

          {/* Key List */}
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-2">{t('apiKey.existingKeys.title')}</h3>
            {loading ? (
              <div className="flex justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
              </div>
            ) : keys.length === 0 ? (
              <p className="text-gray-500 text-center py-4">{t('apiKey.existingKeys.empty')}</p>
            ) : (
              <div className="space-y-2">
                {keys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center justify-between p-3 bg-gray-700 rounded-lg"
                  >
                    <div>
                      <div className="font-medium text-white">{key.name}</div>
                      <div className="text-sm text-gray-400">
                        <code className="text-gray-500">{key.key_prefix}...</code>
                        {' • '}
                        {t('apiKey.existingKeys.created')}{formatDistanceToNow(new Date(key.created_at), { addSuffix: true, locale: i18n.language === 'ja' ? ja : enUS })}
                        {key.last_used_at && (
                          <>
                            {' • '}
                            {t('apiKey.existingKeys.lastUsed')}{formatDistanceToNow(new Date(key.last_used_at), { addSuffix: true, locale: i18n.language === 'ja' ? ja : enUS })}
                          </>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => handleDelete(key.id)}
                      className="p-2 text-gray-400 hover:text-red-500 transition-colors"
                      title={t('apiKey.existingKeys.delete')}
                    >
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Usage Instructions */}
          <div className="mt-6 p-4 bg-gray-700/50 rounded-lg">
            <h3 className="text-sm font-medium text-gray-300 mb-2">{t('apiKey.usage.title')}</h3>
            <p className="text-sm text-gray-400 mb-2">
              {t('apiKey.usage.description')}
            </p>
            <pre className="p-2 bg-gray-900 rounded text-xs text-gray-300 overflow-x-auto">
{`{
  "mcpServers": {
    "douga": {
      "command": "uv",
      "args": ["run", "..."],
      "env": {
        "DOUGA_API_KEY": "<your-api-key>"
      }
    }
  }
}`}
            </pre>
          </div>
        </div>
      </div>
    </div>
  )
}
