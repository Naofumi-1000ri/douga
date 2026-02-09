import React, { useState } from 'react'
import type { OperationHistoryItem } from '@/api/operations'

// Get operation type display text
function getOperationTypeText(type: string): string {
  return type
}

// Get operation type color class
function getOperationTypeColor(type: string): string {
  if (type.startsWith('clip.')) return 'text-blue-400'
  if (type.startsWith('audio_clip.')) return 'text-blue-300'
  if (type.startsWith('text.')) return 'text-purple-400'
  if (type.startsWith('layer.')) return 'text-green-400'
  if (type.startsWith('audio_track.')) return 'text-yellow-400'
  if (type.startsWith('track.')) return 'text-yellow-400'
  if (type.startsWith('marker.')) return 'text-orange-400'
  if (type.startsWith('timeline.')) return 'text-red-400'
  return 'text-gray-400'
}

// Format ISO timestamp for display
function formatTimestamp(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleTimeString('ja-JP', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

// Summarize operation data
function summarizeOperation(op: OperationHistoryItem): string {
  const data = op.data
  if (data.clip && typeof data.clip === 'object') {
    const clip = data.clip as Record<string, unknown>
    return clip.text_content ? String(clip.text_content).substring(0, 30) : ''
  }
  if (data.name) return String(data.name)
  if (data.text_content) return String(data.text_content).substring(0, 30)
  return ''
}

interface ActivityPanelProps {
  className?: string
  width?: number
  onResizeStart?: (e: React.MouseEvent) => void
  operations?: OperationHistoryItem[]
}

export default function ActivityPanel({ className = '', width = 320, onResizeStart, operations = [] }: ActivityPanelProps) {
  const [isPanelOpen, setIsPanelOpen] = useState(false)

  if (!isPanelOpen) {
    return (
      <div
        onClick={() => setIsPanelOpen(true)}
        className={`bg-gray-800 border-l border-gray-700 w-11 flex flex-col items-center py-3 cursor-pointer group transition-colors hover:bg-gray-700/50 ${className}`}
        title="Activity Panel"
      >
        <svg className="w-5 h-5 text-gray-500 group-hover:text-gray-300 transition-colors mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors" style={{ writingMode: 'vertical-rl' }}>Activity</span>
        {operations.length > 0 && (
          <span className="mt-2 bg-primary-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
            {operations.length > 99 ? '99+' : operations.length}
          </span>
        )}
      </div>
    )
  }

  return (
    <aside
      className={`bg-gray-800 border-l border-gray-700 flex flex-col relative ${className}`}
      style={{ width }}
    >
      {/* Resize handle */}
      {onResizeStart && (
        <div
          className="absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-blue-500/50 active:bg-blue-500 transition-colors z-10"
          onMouseDown={onResizeStart}
        />
      )}
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
        <span className="text-white font-medium text-sm">Activity</span>
        <button
          onClick={() => setIsPanelOpen(false)}
          className="text-gray-400 hover:text-white transition-colors"
          title="Close panel"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
      </div>

      {/* Operation List */}
      <div className="flex-1 overflow-y-auto">
        {operations.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">
            No activity yet
          </div>
        ) : (
          <div className="divide-y divide-gray-700/50">
            {operations.map((op) => (
              <div key={op.id} className="p-3 hover:bg-gray-700/30 transition-colors">
                {/* User and Operation Type */}
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium text-green-400">
                    {op.user_name || 'User'}
                  </span>
                  <span className={`text-xs font-mono ${getOperationTypeColor(op.type)}`}>
                    {getOperationTypeText(op.type)}
                  </span>
                  <span className="text-xs text-gray-600 ml-auto">
                    v{op.version}
                  </span>
                </div>

                {/* Details */}
                {summarizeOperation(op) && (
                  <div className="text-gray-300 text-sm">
                    {summarizeOperation(op)}
                  </div>
                )}

                {/* Timestamp */}
                <div className="text-xs text-gray-500 mt-1">
                  {formatTimestamp(op.created_at)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
