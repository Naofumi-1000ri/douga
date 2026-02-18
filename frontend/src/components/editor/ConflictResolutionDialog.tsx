import { useTranslation } from 'react-i18next'
import { useProjectStore } from '@/store/projectStore'

export function ConflictResolutionDialog() {
  const { t } = useTranslation('editor')
  const conflictState = useProjectStore(s => s.conflictState)
  const resolveConflict = useProjectStore(s => s.resolveConflict)

  if (!conflictState?.isConflicting) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md mx-4 shadow-xl border border-gray-700">
        <h3 className="text-lg font-semibold text-white mb-2">
          {t('conflict.title')}
        </h3>
        <p className="text-gray-300 text-sm mb-6">
          {t('conflict.message')}
        </p>
        <div className="flex flex-col gap-3">
          <button
            onClick={() => resolveConflict('reload')}
            className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors"
          >
            {t('conflict.loadLatest')}
            <span className="block text-xs text-blue-200 mt-0.5">
              {t('conflict.loadLatestDesc')}
            </span>
          </button>
          <button
            onClick={() => resolveConflict('force')}
            className="w-full px-4 py-2.5 bg-gray-600 hover:bg-gray-500 text-white rounded-md text-sm font-medium transition-colors"
          >
            {t('conflict.forceSave')}
            <span className="block text-xs text-gray-300 mt-0.5">
              {t('conflict.forceSaveDesc')}
            </span>
          </button>
        </div>
      </div>
    </div>
  )
}
