import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export type SyncResumeAction = 'load_remote' | 'apply_diff' | 'overwrite_remote'

interface SyncResumeDialogProps {
  remoteOpCount: number
  onAction: (action: SyncResumeAction) => void
  onCancel: () => void
}

export function SyncResumeDialog({ remoteOpCount, onAction, onCancel }: SyncResumeDialogProps) {
  const { t } = useTranslation('editor')
  const [loading, setLoading] = useState(false)

  const handleAction = (action: SyncResumeAction) => {
    setLoading(true)
    onAction(action)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md mx-4 shadow-xl border border-gray-700">
        <h3 className="text-lg font-semibold text-white mb-2">
          {t('syncResume.title')}
        </h3>
        <p className="text-gray-300 text-sm mb-1">
          {t('syncResume.messagePre')} <span className="text-yellow-400 font-medium">{remoteOpCount}{t('syncResume.messageUnit')}</span> {t('syncResume.messagePost')}
        </p>
        <p className="text-gray-400 text-xs mb-6">
          {t('syncResume.question')}
        </p>
        <div className="flex flex-col gap-3">
          <button
            onClick={() => handleAction('load_remote')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            {t('syncResume.loadRemote')}
            <span className="block text-xs text-blue-200 mt-0.5">
              {t('syncResume.loadRemoteDesc')}
            </span>
          </button>
          <button
            onClick={() => handleAction('apply_diff')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-gray-600 hover:bg-gray-500 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            {t('syncResume.applyDiff')}
            <span className="block text-xs text-gray-300 mt-0.5">
              {t('syncResume.applyDiffDesc')}
            </span>
          </button>
          <button
            onClick={() => handleAction('overwrite_remote')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-red-700/80 hover:bg-red-700 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            {t('syncResume.overwriteRemote')}
            <span className="block text-xs text-red-200 mt-0.5">
              {t('syncResume.overwriteRemoteDesc')}
            </span>
          </button>
          <button
            onClick={onCancel}
            disabled={loading}
            className="w-full px-4 py-2 text-gray-400 hover:text-white text-sm transition-colors"
          >
            {t('syncResume.cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}
