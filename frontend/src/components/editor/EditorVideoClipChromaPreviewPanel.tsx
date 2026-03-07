import { type MouseEvent as ReactMouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChromaKeyPreviewFrame } from '@/api/aiV1'

interface EditorVideoClipChromaPreviewPanelProps {
  chromaPreviewError: string | null
  chromaPreviewFrames: ChromaKeyPreviewFrame[]
  chromaPreviewSelectedIndex: number | null
  chromaPreviewSize: number
  compositeLightboxLoading: boolean
  handleChromaPreviewResizeStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  handleCompositePreview: () => void
  setChromaPreviewSelectedIndex: (index: number | null) => void
}

export default function EditorVideoClipChromaPreviewPanel({
  chromaPreviewError,
  chromaPreviewFrames,
  chromaPreviewSelectedIndex,
  chromaPreviewSize,
  compositeLightboxLoading,
  handleChromaPreviewResizeStart,
  handleCompositePreview,
  setChromaPreviewSelectedIndex,
}: EditorVideoClipChromaPreviewPanelProps) {
  const { t } = useTranslation('editor')

  return (
    <div className="pt-2 space-y-2">
      <div className="flex items-center justify-end gap-2">
        <button
          className={`px-2 py-1 text-xs rounded transition-colors ${
            compositeLightboxLoading
              ? 'bg-gray-600 text-gray-400 cursor-wait'
              : 'bg-purple-600 hover:bg-purple-500 text-white'
          }`}
          onClick={handleCompositePreview}
          disabled={compositeLightboxLoading}
          title={t('editor.compositePreview')}
        >
          {compositeLightboxLoading ? t('editor.compositePreviewLoading') : t('editor.compositePreview')}
        </button>
      </div>
      {chromaPreviewError && (
        <div className="text-xs text-red-400">{chromaPreviewError}</div>
      )}
      {chromaPreviewFrames.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] text-gray-500">{t('editor.compositePreviewNote')}</div>
          <div className="relative">
            <div
              className="grid grid-cols-5 gap-1"
              style={{ gridTemplateColumns: `repeat(5, ${chromaPreviewSize}px)` }}
            >
              {chromaPreviewFrames.map((frame, index) => {
                const imageFormat = frame.image_format || 'jpeg'
                const mimeType = imageFormat === 'png' ? 'image/png' : 'image/jpeg'
                return (
                  <div
                    key={`${frame.time_ms}-${frame.resolution}`}
                    className="space-y-0.5 cursor-pointer"
                    onClick={() => setChromaPreviewSelectedIndex(index)}
                  >
                    <div
                      style={{
                        width: chromaPreviewSize,
                        background: imageFormat === 'png'
                          ? 'repeating-conic-gradient(#4a4a4a 0% 25%, #3a3a3a 0% 50%) 50% / 16px 16px'
                          : undefined,
                      }}
                      className={`rounded border-2 overflow-hidden transition-all ${
                        chromaPreviewSelectedIndex === index
                          ? 'border-blue-500 ring-1 ring-blue-400'
                          : 'border-gray-700 hover:border-gray-500'
                      }`}
                    >
                      <img
                        src={`data:${mimeType};base64,${frame.frame_base64}`}
                        alt={`preview-${frame.time_ms}`}
                        style={{ width: '100%', height: 'auto', display: 'block' }}
                      />
                    </div>
                    <div className="text-[9px] text-gray-500 text-center">
                      {(frame.time_ms / 1000).toFixed(2)}s
                    </div>
                  </div>
                )
              })}
            </div>
            <div
              className="absolute left-0 right-0 h-3 cursor-ns-resize flex items-center justify-center group hover:bg-gray-700/50 rounded-b"
              style={{ bottom: -12 }}
              onMouseDown={handleChromaPreviewResizeStart}
            >
              <div className="w-8 h-1 bg-gray-600 rounded group-hover:bg-blue-500 transition-colors" />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
