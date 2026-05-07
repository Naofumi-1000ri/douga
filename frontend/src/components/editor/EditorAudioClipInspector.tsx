import { type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { SelectedClipInfo } from '@/components/editor/Timeline'
import type { TimelineData } from '@/store/projectStore'
import NumericInput from '@/components/common/NumericInput'

interface NewVolumeKeyframeInput {
  timeMs: string
  volume: string
}

interface EditorAudioClipInspectorProps {
  currentTime: number
  handleAddVolumeKeyframeAtCurrent: (volume: number) => void
  handleAddVolumeKeyframeManual: (timeMs: number, volume: number) => void
  handleClearVolumeKeyframes: () => void
  handleRemoveVolumeKeyframe: (index: number) => void
  handleUpdateAudioClip: (updates: Record<string, unknown>) => void
  handleUpdateVolumeKeyframe: (index: number, timeMs: number, value: number) => void
  newKeyframeInput: NewVolumeKeyframeInput
  selectedClip: SelectedClipInfo
  setNewKeyframeInput: Dispatch<SetStateAction<NewVolumeKeyframeInput>>
  timelineData?: TimelineData
}

export default function EditorAudioClipInspector({
  currentTime,
  handleAddVolumeKeyframeAtCurrent,
  handleAddVolumeKeyframeManual,
  handleClearVolumeKeyframes,
  handleRemoveVolumeKeyframe,
  handleUpdateAudioClip,
  handleUpdateVolumeKeyframe,
  newKeyframeInput,
  selectedClip,
  setNewKeyframeInput,
  timelineData,
}: EditorAudioClipInspectorProps) {
  const { t } = useTranslation('editor')

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.clipName')}</label>
        <p className="text-white text-sm truncate">{selectedClip.assetName}</p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.trackType')}</label>
        <span className={`inline-block px-2 py-0.5 text-xs rounded ${
          selectedClip.trackType === 'narration'
            ? 'bg-green-600 text-white'
            : selectedClip.trackType === 'bgm'
              ? 'bg-blue-600 text-white'
              : 'bg-yellow-600 text-white'
        }`}>
          {selectedClip.trackType === 'narration' ? t('editor.narration') :
            selectedClip.trackType === 'bgm' ? 'BGM' : 'SE'}
        </span>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.duration')}</label>
        <p className="text-white text-sm">
          {Math.floor(selectedClip.durationMs / 60000)}:
          {Math.floor((selectedClip.durationMs % 60000) / 1000).toString().padStart(2, '0')}
          .{Math.floor((selectedClip.durationMs % 1000) / 10).toString().padStart(2, '0')}
        </p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.startPosition')}</label>
        <NumericInput
          value={selectedClip.startMs}
          onCommit={(val) => {
            const clamped = Math.max(0, val)
            handleUpdateAudioClip({ start_ms: clamped })
          }}
          min={0}
          step={100}
          formatDisplay={(v) => String(Math.round(v))}
          className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
        />
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.volumePercent')}</label>
        <NumericInput
          data-testid="audio-clip-volume-input"
          value={Math.round(selectedClip.volume * 100)}
          onCommit={(val) => {
            const clamped = Math.max(0, Math.min(100, val))
            handleUpdateAudioClip({ volume: clamped / 100 })
          }}
          min={0}
          max={100}
          step={1}
          formatDisplay={(v) => String(Math.round(v))}
          className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
        />
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.fadeInMs')}</label>
        <NumericInput
          value={selectedClip.fadeInMs}
          onCommit={(val) => {
            const clamped = Math.max(0, val)
            handleUpdateAudioClip({ fade_in_ms: clamped })
          }}
          min={0}
          max={10000}
          step={100}
          formatDisplay={(v) => String(Math.round(v))}
          className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
        />
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.fadeOutMs')}</label>
        <NumericInput
          value={selectedClip.fadeOutMs}
          onCommit={(val) => {
            const clamped = Math.max(0, val)
            handleUpdateAudioClip({ fade_out_ms: clamped })
          }}
          min={0}
          max={10000}
          step={100}
          formatDisplay={(v) => String(Math.round(v))}
          className="w-full px-2 py-1 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
        />
      </div>

      <div className="pt-4 border-t border-gray-700">
        <label className="block text-xs text-gray-500 mb-2">{t('editor.volumeEnvelope')}</label>
        <div className="space-y-2">
          <div className="flex gap-1 items-end">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-0.5">{t('editor.timeMs')}</label>
              <input
                type="number"
                min="0"
                step="100"
                value={newKeyframeInput.timeMs}
                onChange={(e) => setNewKeyframeInput(prev => ({ ...prev, timeMs: e.target.value }))}
                onKeyDown={(e) => e.stopPropagation()}
                placeholder="0"
                className="w-full px-1.5 py-1 bg-gray-700 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-orange-500"
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-0.5">{t('editor.volumePercent')}</label>
              <input
                type="number"
                min="0"
                max="100"
                step="10"
                value={newKeyframeInput.volume}
                onChange={(e) => setNewKeyframeInput(prev => ({ ...prev, volume: e.target.value }))}
                onKeyDown={(e) => e.stopPropagation()}
                className="w-full px-1.5 py-1 bg-gray-700 border border-gray-600 rounded text-white text-xs focus:outline-none focus:border-orange-500"
              />
            </div>
            <button
              onClick={() => {
                const timeMs = parseInt(newKeyframeInput.timeMs) || 0
                const volume = (parseInt(newKeyframeInput.volume) || 100) / 100
                handleAddVolumeKeyframeManual(timeMs, volume)
                setNewKeyframeInput({ timeMs: '', volume: '100' })
              }}
              className="px-2 py-1 text-xs text-orange-400 hover:text-white hover:bg-orange-600 border border-orange-600 rounded transition-colors"
              title={t('editor.addKeyframe')}
            >
              {t('editor.addKF')}
            </button>
          </div>

          <div className="flex gap-1">
            <button
              onClick={() => handleAddVolumeKeyframeAtCurrent(1.0)}
              className="flex-1 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-600 border border-gray-600 rounded transition-colors"
              title={t('editor.addCurrentPlus100')}
            >
              {t('editor.addCurrentPlus100')}
            </button>
            <button
              onClick={() => handleAddVolumeKeyframeAtCurrent(0)}
              className="flex-1 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-600 border border-gray-600 rounded transition-colors"
              title={t('editor.addCurrentPlus0')}
            >
              {t('editor.addCurrentPlus0')}
            </button>
          </div>

          {(() => {
            const track = timelineData?.audio_tracks.find((candidate) => candidate.id === selectedClip.trackId)
            const clip = track?.clips.find((candidate) => candidate.id === selectedClip.clipId)
            const keyframes = clip?.volume_keyframes || []
            const clipStartMs = clip?.start_ms ?? selectedClip.startMs
            const timeInClipMs = currentTime - clipStartMs
            const isWithinClip = clip && timeInClipMs >= 0 && timeInClipMs <= clip.duration_ms

            if (keyframes.length === 0) {
              return (
                <p className="text-gray-500 text-xs py-2">
                  {t('editor.noKeyframes')}
                  <br />
                  <span className="text-gray-600">{t('editor.currentTime', { time: (timeInClipMs / 1000).toFixed(2), warn: !isWithinClip ? '⚠️' : '' })}</span>
                </p>
              )
            }

            return (
              <>
                <div className="text-xs text-gray-400 mb-1">
                  {t('editor.keyframesCount', { count: keyframes.length, time: (timeInClipMs / 1000).toFixed(2), warn: !isWithinClip ? '⚠️' : '' })}
                </div>
                <div className="max-h-40 overflow-y-auto space-y-1">
                  {[...keyframes].sort((a, b) => a.time_ms - b.time_ms).map((keyframe, index) => (
                    <div key={index} className="flex items-center gap-1 text-xs bg-gray-700/50 px-1.5 py-1 rounded">
                      <input
                        type="number"
                        min="0"
                        step="100"
                        value={keyframe.time_ms}
                        onChange={(e) => handleUpdateVolumeKeyframe(index, parseInt(e.target.value) || 0, keyframe.value)}
                        onKeyDown={(e) => e.stopPropagation()}
                        className="w-16 px-1 py-0.5 bg-gray-600 border border-gray-500 rounded text-white text-xs"
                        title={t('editor.timeMs')}
                      />
                      <span className="text-gray-500">ms</span>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="10"
                        value={Math.round(keyframe.value * 100)}
                        onChange={(e) => handleUpdateVolumeKeyframe(index, keyframe.time_ms, (parseInt(e.target.value) || 0) / 100)}
                        onKeyDown={(e) => e.stopPropagation()}
                        className="w-12 px-1 py-0.5 bg-gray-600 border border-gray-500 rounded text-orange-400 text-xs"
                        title={t('editor.volumePercent')}
                      />
                      <span className="text-gray-500">%</span>
                      <button
                        onClick={() => handleRemoveVolumeKeyframe(index)}
                        className="ml-auto px-1.5 py-0.5 text-red-400 hover:text-white hover:bg-red-600 rounded transition-colors"
                        title={t('editor.deleteKeyframe')}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  onClick={handleClearVolumeKeyframes}
                  className="w-full px-3 py-1 text-xs text-red-400 hover:text-white hover:bg-red-600 border border-red-600 rounded transition-colors"
                >
                  {t('editor.deleteAllKF')}
                </button>
              </>
            )
          })()}
        </div>
      </div>

      <div className="pt-4 border-t border-gray-700">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.assetId')}</label>
        <p className="text-gray-400 text-xs font-mono break-all">{selectedClip.assetId}</p>
      </div>
    </div>
  )
}
