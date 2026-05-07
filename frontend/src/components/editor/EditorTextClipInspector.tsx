import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import NumericInput from '@/components/common/NumericInput'

interface EditorTextClipInspectorProps {
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipDebounced: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  isComposing: boolean
  localTextContent: string
  selectedVideoClip: SelectedVideoClipInfo
  setIsComposing: Dispatch<SetStateAction<boolean>>
  setLocalTextContent: Dispatch<SetStateAction<string>>
  textDebounceRef: MutableRefObject<ReturnType<typeof setTimeout> | null>
}

export default function EditorTextClipInspector({
  handleUpdateVideoClip,
  handleUpdateVideoClipDebounced,
  handleUpdateVideoClipLocal,
  isComposing,
  localTextContent,
  selectedVideoClip,
  setIsComposing,
  setLocalTextContent,
  textDebounceRef,
}: EditorTextClipInspectorProps) {
  const { t } = useTranslation('editor')

  return (
    <div className="pt-4 border-t border-gray-700">
      <label className="block text-xs text-gray-500 mb-3">{t('editor.captionSettings')}</label>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.textContent')}</label>
        <textarea
          value={localTextContent}
          onChange={(e) => {
            const value = e.target.value
            setLocalTextContent(value)
            if (!isComposing) {
              if (textDebounceRef.current) {
                clearTimeout(textDebounceRef.current)
              }
              textDebounceRef.current = setTimeout(() => {
                handleUpdateVideoClip({ text_content: value })
              }, 300)
            }
          }}
          onCompositionStart={() => setIsComposing(true)}
          onCompositionEnd={(e) => {
            setIsComposing(false)
            const value = (e.target as HTMLTextAreaElement).value
            if (textDebounceRef.current) {
              clearTimeout(textDebounceRef.current)
            }
            handleUpdateVideoClip({ text_content: value })
          }}
          onBlur={(e) => {
            if (textDebounceRef.current) {
              clearTimeout(textDebounceRef.current)
            }
            handleUpdateVideoClip({ text_content: e.target.value })
          }}
          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded resize-none"
          rows={3}
          placeholder={t('editor.textContent')}
        />
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.font')}</label>
        <select
          value={selectedVideoClip.textStyle?.fontFamily || 'Noto Sans JP'}
          onChange={(e) => {
            handleUpdateVideoClipLocal({ text_style: { fontFamily: e.target.value } })
            handleUpdateVideoClip({ text_style: { fontFamily: e.target.value } })
          }}
          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
        >
          <option value="Noto Sans JP">Noto Sans JP</option>
          <option value="Noto Serif JP">Noto Serif JP</option>
          <option value="M PLUS 1p">M PLUS 1p</option>
          <option value="M PLUS Rounded 1c">M PLUS Rounded 1c</option>
          <option value="Kosugi Maru">Kosugi Maru</option>
          <option value="Sawarabi Gothic">Sawarabi Gothic</option>
          <option value="Sawarabi Mincho">Sawarabi Mincho</option>
          <option value="BIZ UDPGothic">BIZ UDPGothic</option>
          <option value="Zen Maru Gothic">Zen Maru Gothic</option>
          <option value="Shippori Mincho">Shippori Mincho</option>
        </select>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.fontSize')}</label>
        <div className="flex gap-2 items-center">
          <input
            type="range"
            min="12"
            max="500"
            value={selectedVideoClip.textStyle?.fontSize || 48}
            onChange={(e) => handleUpdateVideoClipLocal({ text_style: { fontSize: parseInt(e.target.value) || 48 } })}
            onMouseUp={(e) => handleUpdateVideoClip({ text_style: { fontSize: parseInt(e.currentTarget.value) || 48 } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { fontSize: parseInt((e.target as HTMLInputElement).value) || 48 } })}
            className="flex-1 accent-primary-500"
          />
          <NumericInput
            value={selectedVideoClip.textStyle?.fontSize ?? 48}
            onCommit={(val) => handleUpdateVideoClip({ text_style: { fontSize: val } })}
            min={12}
            max={500}
            step={1}
            formatDisplay={(v) => String(Math.round(v))}
            className="w-16 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
          />
        </div>
      </div>

      <div className="mb-3 flex gap-2">
        <button
          onClick={() => {
            const newWeight = selectedVideoClip.textStyle?.fontWeight === 'bold' ? 'normal' : 'bold'
            handleUpdateVideoClipLocal({ text_style: { fontWeight: newWeight } })
            handleUpdateVideoClip({ text_style: { fontWeight: newWeight } })
          }}
          className={`flex-1 px-2 py-1 text-sm rounded ${
            selectedVideoClip.textStyle?.fontWeight === 'bold'
              ? 'bg-primary-600 text-white'
              : 'bg-gray-700 text-gray-400'
          }`}
        >
          <strong>B</strong>
        </button>
        <button
          onClick={() => {
            const newStyle = selectedVideoClip.textStyle?.fontStyle === 'italic' ? 'normal' : 'italic'
            handleUpdateVideoClipLocal({ text_style: { fontStyle: newStyle } })
            handleUpdateVideoClip({ text_style: { fontStyle: newStyle } })
          }}
          className={`flex-1 px-2 py-1 text-sm rounded ${
            selectedVideoClip.textStyle?.fontStyle === 'italic'
              ? 'bg-primary-600 text-white'
              : 'bg-gray-700 text-gray-400'
          }`}
        >
          <em>I</em>
        </button>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.textColor')}</label>
        <div className="flex gap-2 items-center">
          <input
            type="color"
            value={selectedVideoClip.textStyle?.color || '#ffffff'}
            onChange={(e) => {
              handleUpdateVideoClipLocal({ text_style: { color: e.target.value } })
              handleUpdateVideoClipDebounced({ text_style: { color: e.target.value } })
            }}
            className="w-8 h-8 rounded cursor-pointer border border-gray-600"
          />
          <input
            type="text"
            value={selectedVideoClip.textStyle?.color || '#ffffff'}
            onChange={(e) => handleUpdateVideoClipDebounced({ text_style: { color: e.target.value } })}
            className="flex-1 bg-gray-700 text-white text-xs px-2 py-1 rounded font-mono"
          />
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.bgColor')}</label>
        <div className="flex gap-2 items-center mb-2">
          <input
            type="color"
            value={selectedVideoClip.textStyle?.backgroundColor === 'transparent' ? '#000000' : (selectedVideoClip.textStyle?.backgroundColor || '#000000')}
            onChange={(e) => {
              handleUpdateVideoClipLocal({ text_style: { backgroundColor: e.target.value, backgroundOpacity: selectedVideoClip.textStyle?.backgroundOpacity ?? 1 } })
              handleUpdateVideoClipDebounced({ text_style: { backgroundColor: e.target.value, backgroundOpacity: selectedVideoClip.textStyle?.backgroundOpacity ?? 1 } })
            }}
            className="w-8 h-8 rounded cursor-pointer border border-gray-600"
          />
          <input
            type="text"
            value={selectedVideoClip.textStyle?.backgroundColor || 'transparent'}
            onChange={(e) => {
              handleUpdateVideoClipLocal({ text_style: { backgroundColor: e.target.value } })
              handleUpdateVideoClipDebounced({ text_style: { backgroundColor: e.target.value } })
            }}
            className="flex-1 bg-gray-700 text-white text-xs px-2 py-1 rounded font-mono"
            placeholder="#000000"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400 w-12">{t('editor.transparency')}</span>
          <input
            type="range"
            min="0"
            max="100"
            step="5"
            value={Math.round((selectedVideoClip.textStyle?.backgroundOpacity ?? 0.3) * 100)}
            onChange={(e) => handleUpdateVideoClipLocal({ text_style: { backgroundOpacity: parseInt(e.target.value) / 100 } })}
            onMouseUp={(e) => handleUpdateVideoClip({ text_style: { backgroundOpacity: parseInt(e.currentTarget.value) / 100 } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { backgroundOpacity: parseInt((e.target as HTMLInputElement).value) / 100 } })}
            className="flex-1 accent-primary-500"
          />
          <NumericInput
            value={Math.round((selectedVideoClip.textStyle?.backgroundOpacity ?? 0.3) * 100)}
            onCommit={(val) => handleUpdateVideoClip({ text_style: { backgroundOpacity: val / 100 } })}
            min={0}
            max={100}
            step={5}
            formatDisplay={(v) => String(Math.round(v))}
            className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
          />
          <span className="text-xs text-gray-400">%</span>
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.stroke')}</label>
        <div className="flex gap-2 items-center">
          <input
            type="color"
            value={selectedVideoClip.textStyle?.strokeColor || '#000000'}
            onChange={(e) => {
              handleUpdateVideoClipLocal({ text_style: { strokeColor: e.target.value } })
              handleUpdateVideoClipDebounced({ text_style: { strokeColor: e.target.value } })
            }}
            className="w-8 h-8 rounded cursor-pointer border border-gray-600"
          />
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value={selectedVideoClip.textStyle?.strokeWidth || 0}
            onChange={(e) => handleUpdateVideoClipLocal({ text_style: { strokeWidth: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ text_style: { strokeWidth: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { strokeWidth: parseFloat((e.target as HTMLInputElement).value) } })}
            className="flex-1 accent-primary-500"
          />
          <NumericInput
            value={selectedVideoClip.textStyle?.strokeWidth ?? 0}
            onCommit={(val) => handleUpdateVideoClip({ text_style: { strokeWidth: val } })}
            min={0}
            max={100}
            step={1}
            formatDisplay={(v) => String(Math.round(v))}
            className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
          />
          <span className="text-xs text-gray-400">px</span>
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.alignment')}</label>
        <div className="flex gap-1">
          {(['left', 'center', 'right'] as const).map((align) => (
            <button
              key={align}
              onClick={() => {
                handleUpdateVideoClipLocal({ text_style: { textAlign: align } })
                handleUpdateVideoClip({ text_style: { textAlign: align } })
              }}
              className={`flex-1 px-2 py-1 text-xs rounded ${
                (selectedVideoClip.textStyle?.textAlign || 'center') === align
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-700 text-gray-400'
              }`}
            >
              {align === 'left' ? t('editor.alignLeft') : align === 'center' ? t('editor.alignCenter') : t('editor.alignRight')}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.lineHeight')}</label>
        <div className="flex items-center gap-2">
          <input
            type="range"
            min="0.5"
            max="3"
            step="0.1"
            value={selectedVideoClip.textStyle?.lineHeight || 1.4}
            onChange={(e) => handleUpdateVideoClipLocal({ text_style: { lineHeight: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ text_style: { lineHeight: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { lineHeight: parseFloat((e.target as HTMLInputElement).value) } })}
            className="flex-1 accent-primary-500"
          />
          <NumericInput
            value={selectedVideoClip.textStyle?.lineHeight ?? 1.4}
            onCommit={(val) => handleUpdateVideoClip({ text_style: { lineHeight: val } })}
            min={0.5}
            max={5}
            step={0.1}
            formatDisplay={(v) => v.toFixed(1)}
            className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
          />
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1">{t('editor.letterSpacing')}</label>
        <div className="flex items-center gap-2">
          <input
            type="range"
            min="-5"
            max="20"
            step="1"
            value={selectedVideoClip.textStyle?.letterSpacing || 0}
            onChange={(e) => handleUpdateVideoClipLocal({ text_style: { letterSpacing: parseInt(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ text_style: { letterSpacing: parseInt(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ text_style: { letterSpacing: parseInt((e.target as HTMLInputElement).value) } })}
            className="flex-1 accent-primary-500"
          />
          <NumericInput
            value={selectedVideoClip.textStyle?.letterSpacing ?? 0}
            onCommit={(val) => handleUpdateVideoClip({ text_style: { letterSpacing: val } })}
            min={-10}
            max={50}
            step={1}
            formatDisplay={(v) => String(Math.round(v))}
            className="w-14 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center"
          />
          <span className="text-xs text-gray-400">px</span>
        </div>
      </div>
    </div>
  )
}
