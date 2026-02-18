import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  transcriptionApi,
  type Transcription,
  type TranscriptionSegment,
} from '@/api/transcription'

interface TranscriptionPanelProps {
  assetId: string
  assetName: string
  onClose: () => void
}

export default function TranscriptionPanel({
  assetId,
  assetName,
  onClose,
}: TranscriptionPanelProps) {
  const { t } = useTranslation('editor')
  const [transcription, setTranscription] = useState<Transcription | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startTranscription = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      // Start transcription
      await transcriptionApi.transcribe({
        asset_id: assetId,
        language: 'ja',
        model_name: 'base',
        detect_silences: true,
        detect_fillers: true,
        detect_repetitions: true,
      })

      // Wait for completion
      const result = await transcriptionApi.waitForCompletion(assetId, 120000)
      setTranscription(result)

      if (result.status === 'failed') {
        setError(result.error_message || 'Transcription failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to transcribe')
    } finally {
      setLoading(false)
    }
  }, [assetId])

  const toggleCut = useCallback(
    async (segment: TranscriptionSegment) => {
      if (!transcription) return

      try {
        const updated = await transcriptionApi.updateSegment(
          assetId,
          segment.id,
          {
            cut: !segment.cut,
            cut_reason: !segment.cut ? 'manual' : null,
          }
        )

        // Update local state
        setTranscription({
          ...transcription,
          segments: transcription.segments.map((s) =>
            s.id === segment.id ? updated : s
          ),
        })
      } catch (err) {
        console.error('Failed to update segment:', err)
      }
    },
    [assetId, transcription]
  )

  const formatTime = (ms: number) => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    const milliseconds = Math.floor((ms % 1000) / 10)
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}.${milliseconds
      .toString()
      .padStart(2, '0')}`
  }

  const getCutReasonLabel = (reason: string | null) => {
    switch (reason) {
      case 'silence':
        return t('transcription.cutReason.silence')
      case 'filler':
        return t('transcription.cutReason.filler')
      case 'mistake':
        return t('transcription.cutReason.mistake')
      case 'manual':
        return t('transcription.cutReason.manual')
      default:
        return ''
    }
  }

  const getCutReasonColor = (reason: string | null) => {
    switch (reason) {
      case 'silence':
        return 'bg-gray-500'
      case 'filler':
        return 'bg-yellow-500'
      case 'mistake':
        return 'bg-red-500'
      case 'manual':
        return 'bg-blue-500'
      default:
        return 'bg-gray-500'
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[10000]">
      <div className="bg-gray-800 rounded-lg w-full max-w-3xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h2 className="text-lg font-medium text-white">{t('transcription.title')}</h2>
            <p className="text-sm text-gray-400">{assetName}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {!transcription && !loading && (
            <div className="text-center py-12">
              <p className="text-gray-400 mb-4">
                {t('transcription.description')}
              </p>
              <button
                onClick={startTranscription}
                className="px-6 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors"
              >
                {t('transcription.startButton')}
              </button>
            </div>
          )}

          {loading && (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500 mx-auto mb-4"></div>
              <p className="text-gray-400">{t('transcription.processing')}</p>
              <p className="text-xs text-gray-500 mt-2">
                {t('transcription.processingNote')}
              </p>
            </div>
          )}

          {error && (
            <div className="text-center py-12">
              <p className="text-red-500 mb-4">{error}</p>
              <button
                onClick={startTranscription}
                className="px-4 py-2 text-primary-500 hover:text-primary-400"
              >
                {t('transcription.retry')}
              </button>
            </div>
          )}

          {transcription && transcription.status === 'completed' && (
            <div className="space-y-2">
              {/* Statistics */}
              {transcription.statistics && (
                <div className="bg-gray-700 rounded-lg p-3 mb-4 flex items-center gap-6 text-sm">
                  <div>
                    <span className="text-gray-400">{t('transcription.stats.segments')}</span>
                    <span className="text-white">
                      {transcription.statistics.total_segments}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400">{t('transcription.stats.cutCandidates')}</span>
                    <span className="text-yellow-400">
                      {transcription.statistics.cut_segments}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400">{t('transcription.stats.cutDuration')}</span>
                    <span className="text-yellow-400">
                      {formatTime(transcription.statistics.cut_duration_ms)}
                    </span>
                  </div>
                </div>
              )}

              {/* Segments */}
              {transcription.segments.map((segment) => (
                <div
                  key={segment.id}
                  className={`rounded-lg p-3 transition-colors cursor-pointer ${
                    segment.cut
                      ? 'bg-red-900/30 border border-red-500/50'
                      : 'bg-gray-700 hover:bg-gray-600'
                  }`}
                  onClick={() => toggleCut(segment)}
                >
                  <div className="flex items-start gap-3">
                    {/* Checkbox */}
                    <div className="mt-1">
                      <input
                        type="checkbox"
                        checked={segment.cut}
                        onChange={() => toggleCut(segment)}
                        className="w-4 h-4 rounded border-gray-500 bg-gray-600 text-red-500 focus:ring-red-500"
                      />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-gray-400 font-mono">
                          {formatTime(segment.start_ms)} - {formatTime(segment.end_ms)}
                        </span>
                        {segment.cut_reason && (
                          <span
                            className={`text-xs px-1.5 py-0.5 rounded ${getCutReasonColor(
                              segment.cut_reason
                            )} text-white`}
                          >
                            {getCutReasonLabel(segment.cut_reason)}
                          </span>
                        )}
                        {segment.is_filler && !segment.cut_reason && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-500 text-white">
                            {t('transcription.filler')}
                          </span>
                        )}
                      </div>
                      <p
                        className={`text-sm ${
                          segment.cut ? 'text-gray-500 line-through' : 'text-white'
                        }`}
                      >
                        {segment.text || t('transcription.silence')}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        {transcription && transcription.status === 'completed' && (
          <div className="p-4 border-t border-gray-700 flex justify-end gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-400 hover:text-white transition-colors"
            >
              {t('transcription.cancel')}
            </button>
            <button
              onClick={async () => {
                try {
                  await transcriptionApi.applyCuts(assetId)
                  onClose()
                } catch (err) {
                  console.error('Failed to apply cuts:', err)
                }
              }}
              className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors"
            >
              {t('transcription.applyButton')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
