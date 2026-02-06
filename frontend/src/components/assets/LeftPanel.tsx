import { useState } from 'react'
import AssetLibrary from './AssetLibrary'
import SessionPanel from './SessionPanel'
import type { Asset, SessionData } from '@/api/assets'
import type { TimelineData } from '@/store/projectStore'

type PanelTab = 'assets' | 'sessions'

interface LeftPanelProps {
  projectId: string
  currentTimeline: TimelineData | null
  currentSessionId: string | null
  currentSessionName: string | null
  assets: Asset[]
  onPreviewAsset?: (asset: Asset) => void
  onAssetsChange?: () => void
  onOpenSession: (sessionData: SessionData, sessionId?: string, sessionName?: string) => void
  onSaveSession: (sessionId: string | null, sessionName: string) => Promise<void>
  refreshTrigger?: number
}

export default function LeftPanel({
  projectId,
  currentTimeline,
  currentSessionId,
  currentSessionName,
  assets,
  onPreviewAsset,
  onAssetsChange,
  onOpenSession,
  onSaveSession,
  refreshTrigger,
}: LeftPanelProps) {
  const [activeTab, setActiveTab] = useState<PanelTab>('assets')

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
            アセット
          </div>
        </button>
        <button
          onClick={() => setActiveTab('sessions')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
            activeTab === 'sessions'
              ? 'text-white border-b-2 border-primary-500 bg-gray-800'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          <div className="flex items-center justify-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            セクション
          </div>
        </button>
      </div>

      {/* Panel Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'assets' ? (
          <AssetLibrary
            projectId={projectId}
            onPreviewAsset={onPreviewAsset}
            onAssetsChange={onAssetsChange}
            onOpenSession={onOpenSession}
            refreshTrigger={refreshTrigger}
          />
        ) : (
          <SessionPanel
            projectId={projectId}
            currentTimeline={currentTimeline}
            currentSessionId={currentSessionId}
            currentSessionName={currentSessionName}
            assets={assets}
            onOpenSession={onOpenSession}
            onSave={onSaveSession}
            onAssetsChange={onAssetsChange}
            refreshTrigger={refreshTrigger}
          />
        )}
      </div>
    </div>
  )
}
