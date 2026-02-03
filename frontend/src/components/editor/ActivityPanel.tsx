import React, { useMemo } from 'react'
import { useActivityStore, type ActivityEventType } from '@/store/activityStore'

// Get event type display text
function getEventTypeText(eventType: ActivityEventType): string {
  const typeMap: Record<ActivityEventType, string> = {
    'clip.add': 'clip.add',
    'clip.move': 'clip.move',
    'clip.delete': 'clip.delete',
    'clip.trim': 'clip.trim',
    'text.add': 'text.add',
    'text.update': 'text.update',
    'layer.add': 'layer.add',
    'layer.delete': 'layer.delete',
    'track.add': 'track.add',
    'track.delete': 'track.delete',
    'marker.add': 'marker.add',
    'marker.delete': 'marker.delete',
    'project.update': 'project.update',
  }
  return typeMap[eventType] || eventType
}

// Get event type color class
function getEventTypeColor(eventType: ActivityEventType): string {
  if (eventType.startsWith('clip.')) return 'text-blue-400'
  if (eventType.startsWith('text.')) return 'text-purple-400'
  if (eventType.startsWith('layer.')) return 'text-green-400'
  if (eventType.startsWith('track.')) return 'text-yellow-400'
  if (eventType.startsWith('marker.')) return 'text-orange-400'
  return 'text-gray-400'
}

// Format timestamp for display
function formatTimestamp(timestamp: number): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('ja-JP', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

interface ActivityPanelProps {
  className?: string
  width?: number
  onResizeStart?: (e: React.MouseEvent) => void
}

export default function ActivityPanel({ className = '', width = 320, onResizeStart }: ActivityPanelProps) {
  const { events, isPanelOpen, togglePanel, clearEvents } = useActivityStore()

  // Group events by time (within 1 minute)
  const groupedEvents = useMemo(() => {
    if (events.length === 0) return []
    return events
  }, [events])

  if (!isPanelOpen) {
    return (
      <div
        onClick={togglePanel}
        className={`bg-gray-800 border-l border-gray-700 w-10 flex flex-col items-center py-3 cursor-pointer hover:bg-gray-700 transition-colors ${className}`}
        title="Activity Panel"
      >
        <svg className="w-4 h-4 text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-xs text-gray-400" style={{ writingMode: 'vertical-rl' }}>Activity</span>
        {events.length > 0 && (
          <span className="mt-2 bg-primary-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
            {events.length > 99 ? '99+' : events.length}
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
        <div className="flex items-center gap-2">
          {events.length > 0 && (
            <button
              onClick={clearEvents}
              className="text-xs text-gray-400 hover:text-white px-2 py-1 rounded hover:bg-gray-700 transition-colors"
              title="Clear all"
            >
              Clear
            </button>
          )}
          <button
            onClick={togglePanel}
            className="text-gray-400 hover:text-white transition-colors"
            title="Close panel"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        </div>
      </div>

      {/* Event List */}
      <div className="flex-1 overflow-y-auto">
        {groupedEvents.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">
            No activity yet
          </div>
        ) : (
          <div className="divide-y divide-gray-700/50">
            {groupedEvents.map((event) => (
              <div key={event.id} className="p-3 hover:bg-gray-700/30 transition-colors">
                {/* Actor and Event Type */}
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`text-sm font-medium ${
                      event.actorType === 'ai' ? 'text-purple-400' : 'text-green-400'
                    }`}
                  >
                    {event.actor}
                  </span>
                  <span className={`text-xs font-mono ${getEventTypeColor(event.eventType)}`}>
                    {getEventTypeText(event.eventType)}
                  </span>
                </div>

                {/* Details */}
                <div className="text-gray-300 text-sm">
                  {event.target && (
                    <>
                      <span className="text-white">"{event.target}"</span>
                      {event.targetId && (
                        <span className="text-gray-500 text-xs ml-1">({event.targetId})</span>
                      )}
                    </>
                  )}
                  {event.target && event.targetLocation && ' '}
                  {event.targetLocation && (
                    <span className="text-gray-400">{event.targetLocation}</span>
                  )}
                  {!event.target && !event.targetLocation && event.details}
                </div>

                {/* Timestamp */}
                <div className="text-xs text-gray-500 mt-1">
                  {formatTimestamp(event.timestamp)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
