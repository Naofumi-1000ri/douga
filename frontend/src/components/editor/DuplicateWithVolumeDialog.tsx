import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

interface DuplicateWithVolumeDialogProps {
  /** Initial volume in 0.0–1.0 range */
  initialVolume: number
  onConfirm: (volumePercent: number) => void
  onCancel: () => void
}

/**
 * Dialog for duplicating an audio clip with a different volume level.
 * Volume is displayed as 0–200% (internally 0.0–2.0).
 */
export function DuplicateWithVolumeDialog({
  initialVolume,
  onConfirm,
  onCancel,
}: DuplicateWithVolumeDialogProps) {
  const { t } = useTranslation('editor')
  const [volumePercent, setVolumePercent] = useState<number>(
    Math.round(initialVolume * 100)
  )

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCancel()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onCancel])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onConfirm(volumePercent)
  }

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseInt(e.target.value, 10)
    if (!isNaN(val)) {
      setVolumePercent(Math.min(200, Math.max(0, val)))
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="bg-gray-800 rounded-lg p-6 max-w-sm w-full mx-4 shadow-xl border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-white mb-2">
          {t('duplicateWithVolumeDialog.title')}
        </h3>
        <p className="text-gray-300 text-sm mb-4">
          {t('duplicateWithVolumeDialog.description')}
        </p>
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm text-gray-300 mb-1">
              {t('duplicateWithVolumeDialog.volumeLabel')}
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                data-testid="duplicate-with-volume-input"
                className="w-24 px-3 py-2 bg-gray-700 border border-gray-600 text-white rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={volumePercent}
                min={0}
                max={200}
                step={1}
                onChange={handleVolumeChange}
                autoFocus
              />
              <span className="text-gray-400 text-sm">%</span>
              <input
                type="range"
                className="flex-1"
                value={volumePercent}
                min={0}
                max={200}
                step={1}
                onChange={handleVolumeChange}
              />
            </div>
          </div>
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              className="px-4 py-2 text-sm text-gray-300 hover:text-white hover:bg-gray-700 rounded transition-colors"
              onClick={onCancel}
            >
              {t('duplicateWithVolumeDialog.cancel')}
            </button>
            <button
              type="submit"
              data-testid="duplicate-with-volume-confirm"
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded font-medium transition-colors"
            >
              {t('duplicateWithVolumeDialog.duplicate')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
