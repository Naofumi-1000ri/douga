import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import AssetLibrary from './AssetLibrary'
import SequencePanel from './SequencePanel'
import type { Asset } from '@/api/assets'

type PanelTab = 'assets' | 'sequences'

interface LeftPanelProps {
  projectId: string
  currentSequenceId?: string
  onPreviewAsset?: (asset: Asset) => void
  onAssetsChange?: () => void
  refreshTrigger?: number
  onClose?: () => void
  onSnapshotRestored?: () => void
}

export default function LeftPanel({
  projectId,
  currentSequenceId,
  onPreviewAsset,
  onAssetsChange,
  refreshTrigger,
  onClose,
  onSnapshotRestored,
}: LeftPanelProps) {
  const [activeTab, setActiveTab] = useState<PanelTab>('assets')
  const { t } = useTranslation('assets')

  return (
    <div className="h-full flex flex-col bg-gray-800">
      {/* Main Tab Switcher */}
      <div className="flex border-b border-gray-700">
        <button
          onClick={() => setActiveTab('assets')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
            activeTab === 'assets'
              ? 'text-white border-b-2 border-primary-500 bg-gray-800'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          <div className="flex items-center justify-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            {t('tabs.assets')}
          </div>
        </button>
        <button
          onClick={() => setActiveTab('sequences')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
            activeTab === 'sequences'
              ? 'text-white border-b-2 border-primary-500 bg-gray-800'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          <div className="flex items-center justify-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
            </svg>
            {t('tabs.sequences')}
          </div>
        </button>
        {onClose && (
          <button
            onClick={onClose}
            className="px-2 text-gray-400 hover:text-white transition-colors"
            title={t('panel.close')}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Panel Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'assets' ? (
          <AssetLibrary
            projectId={projectId}
            onPreviewAsset={onPreviewAsset}
            onAssetsChange={onAssetsChange}
            refreshTrigger={refreshTrigger}
          />
        ) : (
          <SequencePanel
            projectId={projectId}
            currentSequenceId={currentSequenceId}
            onSnapshotRestored={onSnapshotRestored}
          />
        )}
      </div>
    </div>
  )
}
