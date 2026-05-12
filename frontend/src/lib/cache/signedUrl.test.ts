/**
 * isSignedUrlValid ユニットテスト (Vitest)
 */
import { describe, it, expect } from 'vitest'
import { isSignedUrlValid } from './signedUrl'

// テスト用ヘルパー: GCS v4 署名 URL を組み立てる
function makeUrl(date: string, expires: number): string {
  return `https://storage.googleapis.com/bucket/file.png?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Date=${date}&X-Goog-Expires=${expires}&X-Goog-Credential=sa%40proj.iam.gserviceaccount.com&X-Goog-Signature=abc`
}

// 2026-05-12T02:37:10Z = 1747016230000 ms epoch
const SIGNED_AT_MS = Date.UTC(2026, 4, 12, 2, 37, 10) // month is 0-indexed
const DATE_STRING = '20260512T023710Z'

describe('isSignedUrlValid', () => {
  it('有効な署名 URL で期限内なら true を返す', () => {
    // expires = 3600s, signedAt + 3600s = 1747019830000
    // now = signedAt + 1800s (30分後), margin = 60s → now + margin < expiresAt → true
    const url = makeUrl(DATE_STRING, 3600)
    const now = SIGNED_AT_MS + 1800 * 1000 // 30分後
    expect(isSignedUrlValid(url, now)).toBe(true)
  })

  it('期限切れの署名 URL なら false を返す', () => {
    // expires = 3600s, now = signedAt + 3601s (期限後)
    const url = makeUrl(DATE_STRING, 3600)
    const now = SIGNED_AT_MS + 3601 * 1000
    expect(isSignedUrlValid(url, now)).toBe(false)
  })

  it('X-Goog-Date/Expires がない URL (非署名 URL) は true を返す', () => {
    const url = 'https://storage.googleapis.com/bucket/file.png'
    expect(isSignedUrlValid(url, Date.now())).toBe(true)
  })

  it('空文字列は true を返す', () => {
    expect(isSignedUrlValid('', Date.now())).toBe(true)
  })

  it('malformed な X-Goog-Date (パースエラー) は true を返す (誤検知防止)', () => {
    const url = 'https://storage.googleapis.com/bucket/file.png?X-Goog-Date=INVALID&X-Goog-Expires=3600'
    // parseInt("INV") は NaN なので true になる
    expect(isSignedUrlValid(url, Date.now())).toBe(true)
  })

  it('margin 境界: now + margin === expiresAt → false (期限切れ扱い)', () => {
    // expires = 3600s, expiresAt = signedAt + 3600000ms
    const url = makeUrl(DATE_STRING, 3600)
    const expiresAt = SIGNED_AT_MS + 3600 * 1000
    const margin = 60_000
    // now + margin === expiresAt → now = expiresAt - margin
    const now = expiresAt - margin
    // now + margin < expiresAt → false (now + margin === expiresAt は false ではない)
    expect(isSignedUrlValid(url, now, margin)).toBe(false)
  })

  it('margin 境界: now + margin === expiresAt - 1 → true (ギリギリ有効)', () => {
    const url = makeUrl(DATE_STRING, 3600)
    const expiresAt = SIGNED_AT_MS + 3600 * 1000
    const margin = 60_000
    // now + margin = expiresAt - 1 → now = expiresAt - margin - 1
    const now = expiresAt - margin - 1
    expect(isSignedUrlValid(url, now, margin)).toBe(true)
  })

  it('X-Goog-Date はあるが X-Goog-Expires がない URL は true を返す', () => {
    const url = `https://storage.googleapis.com/bucket/file.png?X-Goog-Date=${DATE_STRING}`
    expect(isSignedUrlValid(url, Date.now())).toBe(true)
  })

  it('X-Goog-Expires はあるが X-Goog-Date がない URL は true を返す', () => {
    const url = 'https://storage.googleapis.com/bucket/file.png?X-Goog-Expires=3600'
    expect(isSignedUrlValid(url, Date.now())).toBe(true)
  })
})
