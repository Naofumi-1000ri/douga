import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import NumericInput from '@/components/common/NumericInput'
import { applyLinkedDelta, hexToRgb, rgbToHex } from '@/components/common/colorRgbUtils'

const DEBOUNCE_MS = 300

export interface ColorRgbInputProps {
  /** hex string (#RRGGBB) */
  value: string
  /** 変更中の値の通知。本コンポーネント内部で 300ms debounce してから呼ばれる。 */
  onChangeDebounced: (hex: string) => void
  /** 確定時(blur/Enter)に呼ばれる */
  onCommit: (hex: string) => void
  /** ローカル状態更新(即時反映)用 */
  onChangeLocal?: (hex: string) => void
}

/**
 * カラーピッカー + RGB数値入力 + 連動チェックボックス。
 *
 * - 連動 ON: どれか1チャンネルを変えると同じ差分を他チャンネルにも適用(明暗一括調整)。
 * - 連動 OFF: 各チャンネル独立編集。
 * - カラーピッカーと相互同期。
 * - onChangeDebounced は内部 debounce タイマー経由で呼ばれ、onCommit 前に必ずキャンセルされる
 *   (commit 後にタイマーが遅延発火して undo 履歴が同値で二重に積まれる退行の防止 — PR #333 レビュー対応)。
 */
export default function ColorRgbInput({
  value,
  onChangeDebounced,
  onCommit,
  onChangeLocal,
}: ColorRgbInputProps) {
  const { t } = useTranslation('editor')
  const [linked, setLinked] = useState(false)

  // onChangeDebounced の発火予約タイマー。
  // onCommit 前に必ずキャンセルし、commit 後の遅延発火(undo 履歴の同値二重積み)を防ぐ。
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** 未発火の debounce タイマーを破棄する。pending があった場合 true を返す。 */
  const cancelPendingDebounce = (): boolean => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
      return true
    }
    return false
  }

  /** onChangeDebounced の発火を予約する(連続呼び出しでタイマーをリセット)。 */
  const scheduleDebounced = (hex: string) => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
    }
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null
      onChangeDebounced(hex)
    }, DEBOUNCE_MS)
  }

  // アンマウント時に未発火タイマーを破棄(unmount 後のコールバック発火防止)
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current)
        debounceRef.current = null
      }
    }
  }, [])

  const rgb = hexToRgb(value) ?? { r: 255, g: 255, b: 255 }

  const handleChannelCommit = (channel: 'r' | 'g' | 'b', newVal: number) => {
    const next = applyLinkedDelta(rgb, channel, newVal, linked)
    const hex = rgbToHex(next.r, next.g, next.b)
    cancelPendingDebounce()
    onChangeLocal?.(hex)
    onCommit(hex)
  }

  const handleChannelChange = (channel: 'r' | 'g' | 'b', newVal: number) => {
    const next = applyLinkedDelta(rgb, channel, newVal, linked)
    const hex = rgbToHex(next.r, next.g, next.b)
    onChangeLocal?.(hex)
    scheduleDebounced(hex)
  }

  const channelClass =
    'w-12 px-1 py-0.5 bg-gray-700 text-white text-xs rounded border border-gray-600 focus:border-primary-500 focus:outline-none text-center'

  return (
    <div className="space-y-1" data-testid="color-rgb-input">
      {/* row 1: picker + hex */}
      <div className="flex gap-2 items-center">
        <input
          type="color"
          value={value.startsWith('#') && value.length === 7 ? value : '#000000'}
          onChange={(e) => {
            onChangeLocal?.(e.target.value)
            scheduleDebounced(e.target.value)
          }}
          onBlur={(e) => {
            // pending 中の変更があるときだけ確定する。
            // pending が無い場合は (a) 変更なし、または (b) debounce 発火済みで commit 経路に乗っている
            // のいずれかなので、ここで onCommit すると undo 履歴が同値で二重に積まれる。
            if (cancelPendingDebounce()) {
              onCommit(e.target.value)
            }
          }}
          className="w-8 h-8 rounded cursor-pointer border border-gray-600"
          aria-label={t('editor.colorPickerLabel')}
          data-testid="color-rgb-picker"
        />
        <input
          type="text"
          value={value}
          onChange={(e) => {
            const v = e.target.value
            if (/^#[0-9a-fA-F]{6}$/.test(v)) {
              onChangeLocal?.(v)
              scheduleDebounced(v)
            }
          }}
          onBlur={(e) => {
            // ピッカーと同じく、pending 中の変更のみ確定して二重コミットを防ぐ
            if (/^#[0-9a-fA-F]{6}$/.test(e.target.value) && cancelPendingDebounce()) {
              onCommit(e.target.value)
            }
          }}
          onKeyDown={(e) => e.stopPropagation()}
          className="flex-1 bg-gray-700 text-white text-xs px-2 py-1 rounded font-mono"
          data-testid="color-rgb-hex"
        />
      </div>

      {/* row 2: RGB inputs + linked checkbox */}
      <div className="flex items-center gap-1">
        {(['r', 'g', 'b'] as const).map((ch) => (
          <div key={ch} className="flex flex-col items-center gap-0.5">
            <span className="text-xs text-gray-500 select-none">{ch.toUpperCase()}</span>
            <NumericInput
              value={rgb[ch]}
              min={0}
              max={255}
              step={1}
              formatDisplay={(v) => String(Math.round(v))}
              onChange={(v) => handleChannelChange(ch, v)}
              onCommit={(v) => handleChannelCommit(ch, v)}
              className={channelClass}
              aria-label={t('editor.rgbChannelLabel', { channel: ch.toUpperCase() })}
              data-testid={`color-rgb-${ch}`}
            />
          </div>
        ))}

        {/* linked checkbox */}
        <label
          className="flex flex-col items-center gap-0.5 cursor-pointer ml-1"
          title={t('editor.colorLinkedTooltip')}
        >
          <span className="text-xs text-gray-500 select-none">
            {t('editor.colorLinked')}
          </span>
          <input
            type="checkbox"
            checked={linked}
            onChange={(e) => setLinked(e.target.checked)}
            className="w-4 h-4 accent-primary-500 cursor-pointer"
            data-testid="color-linked-checkbox"
            aria-label={t('editor.colorLinkedTooltip')}
          />
        </label>
      </div>
    </div>
  )
}
