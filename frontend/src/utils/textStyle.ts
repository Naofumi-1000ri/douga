import type { Clip, TextStyle } from '@/store/projectStore'

type TextStyleSource = Partial<TextStyle> | Record<string, unknown>

export const DEFAULT_TEXT_STYLE: TextStyle = {
  fontFamily: 'Noto Sans JP',
  fontSize: 48,
  fontWeight: 'bold',
  fontStyle: 'normal',
  color: '#ffffff',
  backgroundColor: '#000000',
  backgroundOpacity: 0.4,
  textAlign: 'center',
  verticalAlign: 'middle',
  lineHeight: 1.4,
  letterSpacing: 0,
  strokeColor: '#000000',
  strokeWidth: 2,
}

function readTextStyleValue(
  sources: Array<TextStyleSource | null | undefined>,
  ...keys: string[]
): unknown {
  for (const source of sources) {
    if (!source) continue
    const style = source as Record<string, unknown>
    for (const key of keys) {
      const value = style[key]
      if (value !== undefined && value !== null) {
        return value
      }
    }
  }
  return undefined
}

function normalizeNumber(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function normalizeEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T,
): T {
  return typeof value === 'string' && allowed.includes(value as T) ? value as T : fallback
}

function normalizeFontWeight(value: unknown): TextStyle['fontWeight'] {
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'bold') return 'bold'
    if (normalized === 'normal') return 'normal'
    const parsed = Number(normalized)
    if (Number.isFinite(parsed)) {
      return parsed >= 600 ? 'bold' : 'normal'
    }
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    return value >= 600 ? 'bold' : 'normal'
  }

  return DEFAULT_TEXT_STYLE.fontWeight
}

function normalizeColor(value: unknown, fallback: string): string {
  if (typeof value !== 'string') return fallback
  const normalized = value.trim()
  if (normalized === '') return fallback
  return normalized
}

function normalizeBackgroundColor(value: unknown): string {
  if (typeof value !== 'string') return DEFAULT_TEXT_STYLE.backgroundColor
  const normalized = value.trim()
  if (normalized === '') return 'transparent'
  return normalized
}

export function normalizeTextStyle(
  ...sources: Array<TextStyleSource | null | undefined>
): TextStyle {
  return {
    fontFamily: normalizeColor(
      readTextStyleValue(sources, 'fontFamily', 'font_family'),
      DEFAULT_TEXT_STYLE.fontFamily,
    ),
    fontSize: normalizeNumber(
      readTextStyleValue(sources, 'fontSize', 'font_size'),
      DEFAULT_TEXT_STYLE.fontSize,
    ),
    fontWeight: normalizeFontWeight(
      readTextStyleValue(sources, 'fontWeight', 'font_weight'),
    ),
    fontStyle: normalizeEnum(
      readTextStyleValue(sources, 'fontStyle', 'font_style'),
      ['normal', 'italic'] as const,
      DEFAULT_TEXT_STYLE.fontStyle,
    ),
    color: normalizeColor(
      readTextStyleValue(sources, 'color'),
      DEFAULT_TEXT_STYLE.color,
    ),
    backgroundColor: normalizeBackgroundColor(
      readTextStyleValue(sources, 'backgroundColor', 'background_color'),
    ),
    backgroundOpacity: normalizeNumber(
      readTextStyleValue(sources, 'backgroundOpacity', 'background_opacity'),
      DEFAULT_TEXT_STYLE.backgroundOpacity,
    ),
    textAlign: normalizeEnum(
      readTextStyleValue(sources, 'textAlign', 'text_align'),
      ['left', 'center', 'right'] as const,
      DEFAULT_TEXT_STYLE.textAlign,
    ),
    verticalAlign: normalizeEnum(
      readTextStyleValue(sources, 'verticalAlign', 'vertical_align'),
      ['top', 'middle', 'bottom'] as const,
      DEFAULT_TEXT_STYLE.verticalAlign,
    ),
    lineHeight: normalizeNumber(
      readTextStyleValue(sources, 'lineHeight', 'line_height'),
      DEFAULT_TEXT_STYLE.lineHeight,
    ),
    letterSpacing: normalizeNumber(
      readTextStyleValue(sources, 'letterSpacing', 'letter_spacing'),
      DEFAULT_TEXT_STYLE.letterSpacing,
    ),
    strokeColor: normalizeColor(
      readTextStyleValue(sources, 'strokeColor', 'stroke_color'),
      DEFAULT_TEXT_STYLE.strokeColor,
    ),
    strokeWidth: normalizeNumber(
      readTextStyleValue(sources, 'strokeWidth', 'stroke_width'),
      DEFAULT_TEXT_STYLE.strokeWidth,
    ),
  }
}

export function mergeTextStyle(
  baseStyle?: TextStyleSource | null,
  patchStyle?: TextStyleSource | null,
): TextStyle {
  return normalizeTextStyle(patchStyle, baseStyle)
}

export function normalizeTextClip(clip: Clip): Clip {
  if (clip.text_content === undefined) return clip

  return {
    ...clip,
    text_style: normalizeTextStyle(clip.text_style as TextStyleSource | undefined),
  }
}

export function getTextBackgroundColor(
  backgroundColor?: string | null,
  backgroundOpacity?: number | null,
): string {
  const safeColor = normalizeBackgroundColor(backgroundColor)
  const safeOpacity = Math.max(0, Math.min(1, normalizeNumber(backgroundOpacity, DEFAULT_TEXT_STYLE.backgroundOpacity)))

  if (safeColor === 'transparent' || safeOpacity === 0) return 'transparent'

  const normalizedHex = safeColor.startsWith('#') ? safeColor.slice(1) : safeColor
  const expandedHex = normalizedHex.length === 3
    ? normalizedHex.split('').map((digit) => `${digit}${digit}`).join('')
    : normalizedHex

  if (!/^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(expandedHex)) {
    return 'transparent'
  }

  const rgbHex = expandedHex.slice(0, 6)
  const embeddedAlpha = expandedHex.length === 8 ? parseInt(expandedHex.slice(6, 8), 16) / 255 : 1
  const r = parseInt(rgbHex.slice(0, 2), 16)
  const g = parseInt(rgbHex.slice(2, 4), 16)
  const b = parseInt(rgbHex.slice(4, 6), 16)

  return `rgba(${r}, ${g}, ${b}, ${safeOpacity * embeddedAlpha})`
}
