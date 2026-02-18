import { useTranslation } from 'react-i18next'
import type { TrackHeaderContextMenuState } from './types'

interface TrackHeaderContextMenuProps {
  contextMenu: TrackHeaderContextMenuState | null
  onToggleVisibility: (id: string, type: 'layer' | 'audio_track') => void
  onClose: () => void
}

function TrackHeaderContextMenu({
  contextMenu,
  onToggleVisibility,
  onClose,
}: TrackHeaderContextMenuProps) {
  const { t } = useTranslation('editor')
  if (!contextMenu) return null

  return (
    <>
      {/* Backdrop to close menu */}
      <div className="fixed inset-0 z-40" onClick={onClose} />

      {/* Menu */}
      <div
        className="fixed z-50 bg-gray-800/95 backdrop-blur-sm border border-gray-600/50 rounded-lg shadow-2xl py-1.5 min-w-[180px]"
        style={{ left: contextMenu.x, top: contextMenu.y }}
      >
        {/* Track name header */}
        <div className="px-4 py-1 text-xs text-gray-400 border-b border-gray-600 truncate">
          {contextMenu.name}
        </div>

        {/* Visibility toggle */}
        <button
          className="w-full px-4 py-2.5 text-left text-sm text-gray-200 hover:bg-gray-700/70 flex items-center gap-2 transition-colors"
          onClick={() => {
            onToggleVisibility(contextMenu.id, contextMenu.type)
            onClose()
          }}
        >
          {contextMenu.isVisible ? (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
              </svg>
              {t('trackHeader.hide')}
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
              {t('trackHeader.show')}
            </>
          )}
        </button>
      </div>
    </>
  )
}

export default TrackHeaderContextMenu
