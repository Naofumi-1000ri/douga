import type { TimelineData } from '@/store/projectStore'

import type { TimelineContextMenuState } from './types'

interface TimelineContextMenuProps {
  contextMenu: TimelineContextMenuState | null
  timeline: TimelineData
  selectedVideoClips: Set<string>
  selectedAudioClips: Set<string>
  onGroupClips: () => void
  onUngroupClip: (clipId: string, type: 'video' | 'audio') => void
  onVideoClipSelect: (layerId: string, clipId: string) => void
  onAudioClipSelect: (trackId: string, clipId: string) => void
  onClose: () => void
}

function TimelineContextMenu({
  contextMenu,
  timeline,
  selectedVideoClips,
  selectedAudioClips,
  onGroupClips,
  onUngroupClip,
  onVideoClipSelect,
  onAudioClipSelect,
  onClose,
}: TimelineContextMenuProps) {
  if (!contextMenu) return null

  // Check if clip has a group
  const hasGroup = (() => {
    if (contextMenu.type === 'video' && contextMenu.layerId) {
      const layer = timeline.layers.find((l) => l.id === contextMenu.layerId)
      const clip = layer?.clips.find((c) => c.id === contextMenu.clipId)
      return !!clip?.group_id
    }
    if (contextMenu.type === 'audio' && contextMenu.trackId) {
      const track = timeline.audio_tracks.find((t) => t.id === contextMenu.trackId)
      const clip = track?.clips.find((c) => c.id === contextMenu.clipId)
      return !!clip?.group_id
    }
    return false
  })()

  // Check if there are any menu items to show
  const hasSelection = selectedVideoClips.size > 0 || selectedAudioClips.size > 0
  const hasOverlappingClips = contextMenu.overlappingClips && contextMenu.overlappingClips.length > 1

  // Don't show menu if there are no items
  if (!hasSelection && !hasGroup && !hasOverlappingClips) {
    return null
  }

  return (
    <>
      {/* Backdrop to close menu */}
      <div className="fixed inset-0 z-40" onClick={onClose} />

      {/* Menu */}
      <div
        className="fixed z-50 bg-gray-800 border border-gray-600 rounded-lg shadow-xl py-1 min-w-[160px]"
        style={{ left: contextMenu.x, top: contextMenu.y }}
      >
        {(selectedVideoClips.size > 0 || selectedAudioClips.size > 0) && (
          <button
            className="w-full px-4 py-2 text-left text-sm text-gray-200 hover:bg-gray-700 flex items-center gap-2"
            onClick={onGroupClips}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 14v6m-3-3h6M6 10h2a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v2a2 2 0 002 2zm10 0h2a2 2 0 002-2V6a2 2 0 00-2-2h-2a2 2 0 00-2 2v2a2 2 0 002 2zM6 20h2a2 2 0 002-2v-2a2 2 0 00-2-2H6a2 2 0 00-2 2v2a2 2 0 002 2z" />
            </svg>
            グループ化
          </button>
        )}

        {hasGroup && (
          <button
            className="w-full px-4 py-2 text-left text-sm text-gray-200 hover:bg-gray-700 flex items-center gap-2"
            onClick={() => onUngroupClip(contextMenu.clipId, contextMenu.type)}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
            グループ解除
          </button>
        )}

        {contextMenu.overlappingClips && contextMenu.overlappingClips.length > 1 && (
          <>
            <div className="border-t border-gray-600 my-1" />
            <div className="px-4 py-1 text-xs text-gray-400">重なっているクリップ</div>
            {contextMenu.overlappingClips.map((clip) => (
              <button
                key={clip.clipId}
                className={`w-full px-4 py-2 text-left text-sm hover:bg-gray-700 flex items-center gap-2 ${
                  clip.clipId === contextMenu.clipId ? 'text-blue-400 bg-gray-700/50' : 'text-gray-200'
                }`}
                onClick={() => {
                  if (contextMenu.type === 'video' && contextMenu.layerId) {
                    onVideoClipSelect(contextMenu.layerId, clip.clipId)
                  } else if (contextMenu.type === 'audio' && contextMenu.trackId) {
                    onAudioClipSelect(contextMenu.trackId, clip.clipId)
                  }
                  onClose()
                }}
              >
                <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                <span className="truncate">{clip.name}</span>
                {clip.clipId === contextMenu.clipId && (
                  <svg className="w-4 h-4 ml-auto flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                )}
              </button>
            ))}
          </>
        )}
      </div>
    </>
  )
}

export default TimelineContextMenu
