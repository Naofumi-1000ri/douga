/**
 * GCS v4 署名 URL の有効性を判定するユーティリティ。
 *
 * URL 例: https://storage.googleapis.com/.../foo.png?X-Goog-Date=20260512T023710Z&X-Goog-Expires=3600&...
 *
 * - 署名 URL でなければ (date/expires が無ければ) true を返す (検査対象外)
 * - 期限切れに近い (now + marginMs >= signedAt + expiresSec*1000) なら false
 * - パースエラー時は true を返す (検査対象外、誤検知防止)
 *
 * @param url 検査対象 URL
 * @param nowMs 現在時刻 (ms epoch)
 * @param marginMs 期限切れ判定の安全マージン (default 60_000 = 60秒)
 */
export const SIGNED_URL_REFRESH_MARGIN_MS = 60 * 60 * 1000

export function isSignedUrlValid(url: string, nowMs: number, marginMs: number = 60_000): boolean {
  if (!url) return true
  const dateMatch = url.match(/X-Goog-Date=(\d{8}T\d{6}Z)/)
  const expiresMatch = url.match(/X-Goog-Expires=(\d+)/)
  if (!dateMatch || !expiresMatch) return true
  // 20260512T023710Z → 2026, 05, 12, 02, 37, 10
  const s = dateMatch[1]
  try {
    const Y = parseInt(s.slice(0, 4), 10)
    const M = parseInt(s.slice(4, 6), 10) - 1
    const D = parseInt(s.slice(6, 8), 10)
    const h = parseInt(s.slice(9, 11), 10)
    const m = parseInt(s.slice(11, 13), 10)
    const sec = parseInt(s.slice(13, 15), 10)
    const signedAtMs = Date.UTC(Y, M, D, h, m, sec)
    if (Number.isNaN(signedAtMs)) return true
    const expiresSec = parseInt(expiresMatch[1], 10)
    if (Number.isNaN(expiresSec)) return true
    const expiresAtMs = signedAtMs + expiresSec * 1000
    return nowMs + marginMs < expiresAtMs
  } catch {
    return true
  }
}

export function areSignedUrlsValid(
  urls: Iterable<string | null | undefined>,
  nowMs: number = Date.now(),
  marginMs: number = SIGNED_URL_REFRESH_MARGIN_MS,
): boolean {
  for (const url of urls) {
    if (url && !isSignedUrlValid(url, nowMs, marginMs)) return false
  }
  return true
}

export function preferValidSignedUrl(
  preferredUrl: string | null | undefined,
  fallbackUrl: string,
  nowMs: number = Date.now(),
  marginMs: number = SIGNED_URL_REFRESH_MARGIN_MS,
): string {
  if (preferredUrl && isSignedUrlValid(preferredUrl, nowMs, marginMs)) {
    return preferredUrl
  }
  return fallbackUrl
}
