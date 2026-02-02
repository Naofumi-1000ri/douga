import { useState, useEffect, useCallback } from 'react'
import type { RenderJob } from '@/api/projects'

interface ExportDialogProps {
  isOpen: boolean
  onClose: () => void
  onStartExport: (options: { start_ms?: number; end_ms?: number }) => void
  onCancelExport: () => void
  onDownload: () => void
  renderJob: RenderJob | null
  totalDurationMs: number
}

// Format milliseconds to mm:ss.SSS
function formatTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const milliseconds = ms % 1000
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}

// Parse mm:ss.SSS or mm:ss to milliseconds
function parseTime(timeStr: string): number | null {
  const match = timeStr.match(/^(\d+):(\d{2})(?:\.(\d{1,3}))?$/)
  if (!match) return null

  const minutes = parseInt(match[1], 10)
  const seconds = parseInt(match[2], 10)
  const milliseconds = match[3] ? parseInt(match[3].padEnd(3, '0'), 10) : 0

  if (seconds >= 60) return null

  return minutes * 60 * 1000 + seconds * 1000 + milliseconds
}

export default function ExportDialog({
  isOpen,
  onClose,
  onStartExport,
  onCancelExport,
  onDownload,
  renderJob,
  totalDurationMs,
}: ExportDialogProps) {
  const [useRange, setUseRange] = useState(false)
  const [startTimeInput, setStartTimeInput] = useState('00:00.000')
  const [endTimeInput, setEndTimeInput] = useState('')
  const [startTimeError, setStartTimeError] = useState<string | null>(null)
  const [endTimeError, setEndTimeError] = useState<string | null>(null)

  // Reset to full range when dialog opens or total duration changes
  useEffect(() => {
    if (isOpen) {
      setStartTimeInput('00:00.000')
      setEndTimeInput(formatTime(totalDurationMs))
      setStartTimeError(null)
      setEndTimeError(null)
    }
  }, [isOpen, totalDurationMs])

  const validateInputs = useCallback((): { start_ms?: number; end_ms?: number } | null => {
    if (!useRange) {
      return {} // No range specified, use defaults
    }

    const startMs = parseTime(startTimeInput)
    const endMs = parseTime(endTimeInput)

    let hasError = false

    if (startMs === null) {
      setStartTimeError('Invalid format. Use mm:ss.SSS')
      hasError = true
    } else if (startMs < 0) {
      setStartTimeError('Start time cannot be negative')
      hasError = true
    } else {
      setStartTimeError(null)
    }

    if (endMs === null) {
      setEndTimeError('Invalid format. Use mm:ss.SSS')
      hasError = true
    } else if (endMs > totalDurationMs) {
      setEndTimeError(`End time exceeds duration (${formatTime(totalDurationMs)})`)
      hasError = true
    } else {
      setEndTimeError(null)
    }

    if (!hasError && startMs !== null && endMs !== null) {
      if (startMs >= endMs) {
        setStartTimeError('Start must be before end')
        hasError = true
      }
    }

    if (hasError) return null

    return { start_ms: startMs!, end_ms: endMs! }
  }, [useRange, startTimeInput, endTimeInput, totalDurationMs])

  const handleStartExport = () => {
    const options = validateInputs()
    if (options !== null) {
      onStartExport(options)
    }
  }

  const isProcessing = renderJob?.status === 'queued' || renderJob?.status === 'processing'
  const isCompleted = renderJob?.status === 'completed'
  const isFailed = renderJob?.status === 'failed' || renderJob?.status === 'cancelled'

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 w-[480px] max-w-[90vw]">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-medium text-lg">動画エクスポート</h3>
          {!isProcessing && (
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-white"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Export Range Settings - only show before starting */}
        {!renderJob && (
          <div className="mb-6">
            <div className="flex items-center gap-3 mb-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  checked={!useRange}
                  onChange={() => setUseRange(false)}
                  className="w-4 h-4 text-primary-600"
                />
                <span className="text-white text-sm">全体をエクスポート</span>
              </label>
              <span className="text-gray-500 text-sm">({formatTime(totalDurationMs)})</span>
            </div>

            <div className="flex items-center gap-3 mb-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  checked={useRange}
                  onChange={() => setUseRange(true)}
                  className="w-4 h-4 text-primary-600"
                />
                <span className="text-white text-sm">範囲を指定</span>
              </label>
            </div>

            {useRange && (
              <div className="ml-6 space-y-3">
                <div className="flex items-center gap-4">
                  <div className="flex-1">
                    <label className="block text-xs text-gray-400 mb-1">開始 (mm:ss.SSS)</label>
                    <input
                      type="text"
                      value={startTimeInput}
                      onChange={(e) => {
                        setStartTimeInput(e.target.value)
                        setStartTimeError(null)
                      }}
                      placeholder="00:00.000"
                      className={`w-full px-3 py-2 bg-gray-700 border rounded text-white text-sm font-mono ${
                        startTimeError ? 'border-red-500' : 'border-gray-600'
                      } focus:outline-none focus:border-primary-500`}
                    />
                    {startTimeError && (
                      <p className="text-red-400 text-xs mt-1">{startTimeError}</p>
                    )}
                  </div>
                  <div className="flex-1">
                    <label className="block text-xs text-gray-400 mb-1">終了 (mm:ss.SSS)</label>
                    <input
                      type="text"
                      value={endTimeInput}
                      onChange={(e) => {
                        setEndTimeInput(e.target.value)
                        setEndTimeError(null)
                      }}
                      placeholder={formatTime(totalDurationMs)}
                      className={`w-full px-3 py-2 bg-gray-700 border rounded text-white text-sm font-mono ${
                        endTimeError ? 'border-red-500' : 'border-gray-600'
                      } focus:outline-none focus:border-primary-500`}
                    />
                    {endTimeError && (
                      <p className="text-red-400 text-xs mt-1">{endTimeError}</p>
                    )}
                  </div>
                </div>
                <p className="text-gray-500 text-xs">
                  タイムライン全長: {formatTime(totalDurationMs)}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Status - show when processing */}
        {renderJob && (
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-2">
              {renderJob.status === 'queued' && (
                <>
                  <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                  <span className="text-yellow-400 text-sm">キュー待機中...</span>
                </>
              )}
              {renderJob.status === 'processing' && (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-primary-500"></div>
                  <span className="text-primary-400 text-sm">レンダリング中...</span>
                </>
              )}
              {renderJob.status === 'completed' && (
                <>
                  <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="text-green-400 text-sm">完了</span>
                </>
              )}
              {renderJob.status === 'failed' && (
                <>
                  <svg className="w-5 h-5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  <span className="text-red-400 text-sm">エラー</span>
                </>
              )}
              {renderJob.status === 'cancelled' && (
                <>
                  <svg className="w-5 h-5 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                  </svg>
                  <span className="text-gray-400 text-sm">キャンセル済み</span>
                </>
              )}
            </div>

            {/* Current stage */}
            {renderJob.current_stage && isProcessing && (
              <p className="text-gray-400 text-xs">{renderJob.current_stage}</p>
            )}

            {/* Error message */}
            {renderJob.status === 'failed' && renderJob.error_message && (
              <p className="text-red-400 text-xs mt-1">{renderJob.error_message}</p>
            )}
          </div>
        )}

        {/* Progress bar */}
        {isProcessing && (
          <div className="mb-4">
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              <span>進行状況</span>
              <span>{Math.round(renderJob?.progress || 0)}%</span>
            </div>
            <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-500 transition-all duration-300"
                style={{ width: `${renderJob?.progress || 0}%` }}
              />
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2">
          {!renderJob && (
            <>
              <button
                onClick={handleStartExport}
                className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                エクスポート開始
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                キャンセル
              </button>
            </>
          )}

          {isProcessing && (
            <button
              onClick={onCancelExport}
              className="flex-1 px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
            >
              キャンセル
            </button>
          )}

          {isCompleted && (
            <>
              <button
                onClick={onDownload}
                className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                ダウンロード
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                閉じる
              </button>
            </>
          )}

          {isFailed && (
            <>
              <button
                onClick={() => onStartExport({})}
                className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm rounded transition-colors"
              >
                再試行
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white text-sm rounded transition-colors"
              >
                閉じる
              </button>
            </>
          )}
        </div>

      </div>
    </div>
  )
}
