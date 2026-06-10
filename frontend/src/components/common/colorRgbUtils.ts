/** #RRGGBB → { r, g, b } (0-255). Returns null for invalid input. */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const m = /^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(hex)
  if (!m) return null
  return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) }
}

/** { r, g, b } (0-255) → #RRGGBB */
export function rgbToHex(r: number, g: number, b: number): string {
  const clamp = (v: number) => Math.min(255, Math.max(0, Math.round(v)))
  return `#${[clamp(r), clamp(g), clamp(b)].map((v) => v.toString(16).padStart(2, '0')).join('')}`
}

/**
 * チャンネル変更時の連動計算。
 *
 * linked=true のとき、変更量(delta)を他チャンネルにも加算してクランプ。
 * linked=false のとき、指定チャンネルのみ変更。
 */
export function applyLinkedDelta(
  current: { r: number; g: number; b: number },
  channel: 'r' | 'g' | 'b',
  newValue: number,
  linked: boolean,
): { r: number; g: number; b: number } {
  const clamp = (v: number) => Math.min(255, Math.max(0, Math.round(v)))
  if (!linked) {
    return { ...current, [channel]: clamp(newValue) }
  }
  const delta = newValue - current[channel]
  return {
    r: clamp(current.r + delta),
    g: clamp(current.g + delta),
    b: clamp(current.b + delta),
  }
}
