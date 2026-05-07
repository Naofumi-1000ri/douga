import { useEffect, useRef, useState } from 'react'

export interface NumericInputProps {
  value: number
  onCommit: (value: number) => void
  min?: number
  max?: number
  step?: number
  /** 入力値 → store値の変換 (例: % → 比率 val/100) */
  transform?: (raw: number) => number
  /** 表示用フォーマット (例: Math.round, toFixed(2)) */
  formatDisplay?: (value: number) => string
  className?: string
  'data-testid'?: string
  placeholder?: string
  'aria-label'?: string
  id?: string
}

/**
 * 数値入力コンポーネント。
 * - フォーカス中は外部 value の変更を無視する（編集中の値を上書きしない）
 * - Enter または blur で onCommit を呼ぶ
 * - 空文字 / NaN の場合は commit せず、直前の value に戻す
 * - Esc でキャンセル（直前の value に戻す）
 * - onKeyDown で stopPropagation（Editor ショートカット干渉防止）
 */
export default function NumericInput({
  value,
  onCommit,
  min,
  max,
  step,
  transform,
  formatDisplay,
  className,
  'data-testid': testId,
  placeholder,
  'aria-label': ariaLabel,
  id,
}: NumericInputProps) {
  const format = (v: number): string => (formatDisplay ? formatDisplay(v) : String(v))

  const [displayValue, setDisplayValue] = useState<string>(format(value))
  const focusedRef = useRef(false)
  // Enter / Escape で処理済みの場合、onBlur 内での commit を skip するフラグ
  const justHandledRef = useRef(false)

  // フォーカス中でなければ外部値の変化を同期
  useEffect(() => {
    if (!focusedRef.current) {
      setDisplayValue(format(value))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const commit = () => {
    const raw = parseFloat(displayValue)
    if (displayValue.trim() === '' || Number.isNaN(raw)) {
      // 不正値は元に戻す
      setDisplayValue(format(value))
      return
    }

    let clamped = raw
    if (min !== undefined) clamped = Math.max(min, clamped)
    if (max !== undefined) clamped = Math.min(max, clamped)

    const committed = transform ? transform(clamped) : clamped
    setDisplayValue(format(clamped))
    onCommit(committed)
  }

  const cancel = () => {
    setDisplayValue(format(value))
  }

  return (
    <input
      type="number"
      id={id}
      aria-label={ariaLabel}
      data-testid={testId}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      value={displayValue}
      className={className}
      onChange={(e) => setDisplayValue(e.target.value)}
      onFocus={() => {
        focusedRef.current = true
      }}
      onBlur={() => {
        // Enter / Escape で既に処理済みの場合は commit をスキップ
        if (justHandledRef.current) {
          justHandledRef.current = false
          focusedRef.current = false
          return
        }
        focusedRef.current = false
        commit()
      }}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') {
          justHandledRef.current = true
          commit()
          e.currentTarget.blur()
        } else if (e.key === 'Escape') {
          justHandledRef.current = true
          cancel()
          e.currentTarget.blur()
        }
      }}
    />
  )
}
