import { type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import NumericInput from '@/components/common/NumericInput'

type UpdateHandler = (updates: Record<string, unknown>) => void

interface InterpolatedValues {
  opacity?: number
  rotation: number
  scale: number
  x: number
  y: number
}

interface EditorVideoClipTransformSectionProps {
  currentKeyframeExists: () => boolean
  getCurrentInterpolatedValues: () => InterpolatedValues | null
  handleAddKeyframe: () => void
  handleFitFillStretch: (mode: 'fit' | 'fill' | 'stretch') => void
  handleRemoveKeyframe: () => void
  handleUpdateVideoClip: UpdateHandler
  handleUpdateVideoClipLocal: UpdateHandler
  selectedKeyframeIndex: number | null
  selectedVideoClip: SelectedVideoClipInfo
  setSelectedKeyframeIndex: Dispatch<SetStateAction<number | null>>
}

export default function EditorVideoClipTransformSection({
  currentKeyframeExists,
  getCurrentInterpolatedValues,
  handleAddKeyframe,
  handleFitFillStretch,
  handleRemoveKeyframe,
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
  selectedKeyframeIndex,
  selectedVideoClip,
  setSelectedKeyframeIndex,
}: EditorVideoClipTransformSectionProps) {
  const { t } = useTranslation('editor')
  const isArrowShape = selectedVideoClip.shape?.type === 'arrow'
  const hasKeyframes = Boolean(selectedVideoClip.keyframes?.length)
  const interpolated = getCurrentInterpolatedValues()
  const selectedKeyframe = selectedKeyframeIndex !== null
    ? selectedVideoClip.keyframes?.[selectedKeyframeIndex] ?? null
    : null
  const displayX = hasKeyframes && interpolated ? Math.round(interpolated.x) : Math.round(selectedVideoClip.transform.x)
  const displayY = hasKeyframes && interpolated ? Math.round(interpolated.y) : Math.round(selectedVideoClip.transform.y)
  const displayScale = hasKeyframes && interpolated ? interpolated.scale : selectedVideoClip.transform.scale
  const displayRotation = hasKeyframes && interpolated ? Math.round(interpolated.rotation) : selectedVideoClip.transform.rotation

  return (
    <>
      <div className="pt-4 border-t border-gray-700">
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs text-gray-500">{t('editor.keyframes')}</label>
          <span className="text-xs text-gray-400">{selectedVideoClip.keyframes?.length || 0}</span>
        </div>
        {selectedKeyframe && (
          <div className="mb-2 p-2 bg-yellow-900/30 border border-yellow-600/50 rounded text-xs">
            <div className="flex items-center justify-between">
              <span className="text-yellow-400 font-medium">
                KF {selectedKeyframeIndex! + 1} ({(selectedKeyframe.time_ms / 1000).toFixed(2)}s)
              </span>
              <button
                onClick={() => setSelectedKeyframeIndex(null)}
                className="text-gray-400 hover:text-white text-xs"
              >
                {t('editor.kfRelease')}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-1 text-gray-300 mt-1">
              <span>X: {Math.round(selectedKeyframe.transform.x)}</span>
              <span>Y: {Math.round(selectedKeyframe.transform.y)}</span>
              {!isArrowShape && <span>{t('editor.scaleValue', { value: (selectedKeyframe.transform.scale * 100).toFixed(0) })}</span>}
              <span>{t('editor.rotation', { value: Math.round(selectedKeyframe.transform.rotation) })}</span>
            </div>
          </div>
        )}
        <div className="flex gap-2">
          {currentKeyframeExists() ? (
            <button
              onClick={handleRemoveKeyframe}
              className="flex-1 px-3 py-1.5 text-xs bg-red-600 hover:bg-red-700 text-white rounded transition-colors flex items-center justify-center gap-1"
            >
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2L2 12l10 10 10-10L12 2z" />
              </svg>
              {t('editor.deleteKeyframe')}
            </button>
          ) : (
            <button
              onClick={handleAddKeyframe}
              className="flex-1 px-3 py-1.5 text-xs bg-yellow-600 hover:bg-yellow-700 text-white rounded transition-colors flex items-center justify-center gap-1"
            >
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2L2 12l10 10 10-10L12 2z" />
              </svg>
              {t('editor.addKeyframe')}
            </button>
          )}
        </div>
        {hasKeyframes && (
          <div className="mt-2 text-xs text-gray-400">
            <p>{t('editor.animEnabled')}</p>
          </div>
        )}
        {interpolated && hasKeyframes && (
          <div className="mt-2 p-2 bg-gray-700/50 rounded text-xs">
            <p className="text-gray-400 mb-1">{t('editor.currentInterpolated')}</p>
            <div className="grid grid-cols-2 gap-1 text-gray-300">
              <span>X: {Math.round(interpolated.x)}</span>
              <span>Y: {Math.round(interpolated.y)}</span>
              {!isArrowShape && <span>{t('editor.scaleValue', { value: (interpolated.scale * 100).toFixed(0) })}</span>}
              <span>{t('editor.rotation', { value: Math.round(interpolated.rotation) })}</span>
            </div>
          </div>
        )}
      </div>

      <div className="pt-4 border-t border-gray-700">
        <label className="block text-xs text-gray-500 mb-2">
          {t('editor.position', { kf: hasKeyframes ? t('editor.positionKF') : '' })}
        </label>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-xs text-gray-600">X</label>
            <NumericInput
              value={displayX}
              onCommit={(val) => handleUpdateVideoClip({ transform: { x: val } })}
              step={1}
              formatDisplay={(v) => String(Math.round(v))}
              className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600">Y</label>
            <NumericInput
              value={displayY}
              onCommit={(val) => handleUpdateVideoClip({ transform: { y: val } })}
              step={1}
              formatDisplay={(v) => String(Math.round(v))}
              className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
            />
          </div>
        </div>
      </div>

      <div>
        <div className={`grid gap-2 ${isArrowShape ? 'grid-cols-1' : 'grid-cols-2'}`}>
          {!isArrowShape && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">
                  {t('editor.scaleLabel', { kf: hasKeyframes ? ' (KF)' : '' })}
                </label>
                <div className="flex items-center">
                  <NumericInput
                    data-testid="video-scale-input"
                    value={Math.round(displayScale * 100)}
                    onCommit={(val) => {
                      const clamped = Math.max(10, Math.min(300, val)) / 100
                      if (clamped !== displayScale) {
                        handleUpdateVideoClip({ transform: { scale: clamped } })
                      }
                    }}
                    min={10}
                    max={300}
                    step={10}
                    formatDisplay={(v) => String(Math.round(v))}
                    className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                  />
                  <span className="text-xs text-gray-500 ml-1">%</span>
                </div>
              </div>
              <input
                data-testid="video-scale-slider"
                type="range"
                min="0.1"
                max="3"
                step="0.01"
                value={displayScale}
                onChange={(e) => handleUpdateVideoClipLocal({ transform: { scale: parseFloat(e.target.value) } })}
                onMouseUp={(e) => handleUpdateVideoClip({ transform: { scale: parseFloat(e.currentTarget.value) } })}
                onTouchEnd={(e) => handleUpdateVideoClip({ transform: { scale: parseFloat((e.target as HTMLInputElement).value) } })}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
              />
            </div>
          )}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-500">
                {t('editor.rotationLabel', { kf: hasKeyframes ? ' (KF)' : '' })}
              </label>
              <div className="flex items-center">
                <NumericInput
                  value={Math.round(displayRotation)}
                  onCommit={(val) => {
                    const clamped = Math.max(-180, Math.min(180, val))
                    if (clamped !== displayRotation) {
                      handleUpdateVideoClip({ transform: { rotation: clamped } })
                    }
                  }}
                  min={-180}
                  max={180}
                  step={1}
                  formatDisplay={(v) => String(Math.round(v))}
                  className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                />
                <span className="text-xs text-gray-500 ml-1">°</span>
              </div>
            </div>
            <input
              type="range"
              min="-180"
              max="180"
              step="1"
              value={displayRotation}
              onChange={(e) => handleUpdateVideoClipLocal({ transform: { rotation: parseInt(e.target.value) } })}
              onMouseUp={(e) => handleUpdateVideoClip({ transform: { rotation: parseInt(e.currentTarget.value) } })}
              onTouchEnd={(e) => handleUpdateVideoClip({ transform: { rotation: parseInt((e.target as HTMLInputElement).value) } })}
              className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
            />
          </div>
        </div>
        {isArrowShape && (
          <p data-testid="arrow-scale-locked-note" className="mt-2 text-xs text-gray-400">
            {t('editor.arrowScaleLocked')}
          </p>
        )}
      </div>

      {selectedVideoClip.assetId && (
        <div className="pt-4 border-t border-gray-700">
          <label className="block text-xs text-gray-500 mb-2">{t('editor.fitMode')}</label>
          <div className="grid grid-cols-3 gap-1">
            <button
              onClick={() => handleFitFillStretch('fit')}
              className="px-2 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
              title={t('timeline.handles.cropModeLeft')}
            >
              Fit
            </button>
            <button
              onClick={() => handleFitFillStretch('fill')}
              className="px-2 py-1.5 text-xs bg-green-600 hover:bg-green-700 text-white rounded transition-colors"
              title={t('timeline.handles.cropModeRight')}
            >
              Fill
            </button>
            <button
              onClick={() => handleFitFillStretch('stretch')}
              className="px-2 py-1.5 text-xs bg-purple-600 hover:bg-purple-700 text-white rounded transition-colors"
              title={t('timeline.handles.stretchModeLeft')}
            >
              Stretch
            </button>
          </div>
        </div>
      )}

      <div className="pt-4 border-t border-gray-700">
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500">{t('editor.opacity')}</label>
          <div className="flex items-center">
            <NumericInput
              data-testid="video-opacity-input"
              value={Math.round((selectedVideoClip.effects.opacity ?? 1) * 100)}
              onCommit={(val) => {
                const clamped = Math.max(0, Math.min(100, val)) / 100
                if (clamped !== (selectedVideoClip.effects.opacity ?? 1)) {
                  handleUpdateVideoClip({ effects: { opacity: clamped } })
                }
              }}
              min={0}
              max={100}
              step={1}
              formatDisplay={(v) => String(Math.round(v))}
              className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
            />
            <span className="text-xs text-gray-500 ml-1">%</span>
          </div>
        </div>
        <input
          data-testid="video-opacity-slider"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={selectedVideoClip.effects.opacity ?? 1}
          onChange={(e) => handleUpdateVideoClipLocal({ effects: { opacity: parseFloat(e.target.value) } })}
          onMouseUp={(e) => handleUpdateVideoClip({ effects: { opacity: parseFloat(e.currentTarget.value) } })}
          onTouchEnd={(e) => handleUpdateVideoClip({ effects: { opacity: parseFloat((e.target as HTMLInputElement).value) } })}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
        />
      </div>

      <div className="pt-4 border-t border-gray-700 space-y-3">
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-500">{t('editor.fadeIn')}</label>
            <div className="flex items-center">
              <NumericInput
                value={selectedVideoClip.fadeInMs ?? 0}
                onCommit={(val) => {
                  const clamped = Math.max(0, Math.min(3000, val))
                  if (clamped !== (selectedVideoClip.fadeInMs ?? 0)) {
                    handleUpdateVideoClip({ effects: { fade_in_ms: clamped } })
                  }
                }}
                min={0}
                max={3000}
                step={100}
                formatDisplay={(v) => String(Math.round(v))}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">ms</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="3000"
            step="100"
            value={selectedVideoClip.fadeInMs ?? 0}
            onChange={(e) => handleUpdateVideoClipLocal({ effects: { fade_in_ms: parseInt(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ effects: { fade_in_ms: parseInt(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ effects: { fade_in_ms: parseInt((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-500">{t('editor.fadeOut')}</label>
            <div className="flex items-center">
              <NumericInput
                value={selectedVideoClip.fadeOutMs ?? 0}
                onCommit={(val) => {
                  const clamped = Math.max(0, Math.min(3000, val))
                  if (clamped !== (selectedVideoClip.fadeOutMs ?? 0)) {
                    handleUpdateVideoClip({ effects: { fade_out_ms: clamped } })
                  }
                }}
                min={0}
                max={3000}
                step={100}
                formatDisplay={(v) => String(Math.round(v))}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">ms</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="3000"
            step="100"
            value={selectedVideoClip.fadeOutMs ?? 0}
            onChange={(e) => handleUpdateVideoClipLocal({ effects: { fade_out_ms: parseInt(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ effects: { fade_out_ms: parseInt(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ effects: { fade_out_ms: parseInt((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
      </div>
    </>
  )
}
