/**
 * authStore.test.ts — signOut 順序バグ (D-1) のリグレッションテスト
 *
 * テスト観点:
 *   (a) signOut 成功時に firebaseSignOut の後で clearAllUserData が呼ばれる
 *   (b) signOut 失敗時に clearAllUserData が呼ばれず、user state も残る
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// Firebase モック
// ---------------------------------------------------------------------------
const firebaseSignOutMock = vi.fn()

vi.mock('firebase/auth', () => ({
  signInWithPopup: vi.fn(),
  signOut: firebaseSignOutMock,
  onIdTokenChanged: vi.fn(),
}))

vi.mock('@/lib/firebase', () => ({
  auth: { name: 'mock-auth' },
  googleProvider: {},
  DEV_MODE: false,
}))

// ---------------------------------------------------------------------------
// etagCache モック
// ---------------------------------------------------------------------------
const clearAllUserDataMock = vi.fn()
const clearAllCacheMock = vi.fn()

vi.mock('@/lib/cache/etagCache', () => ({
  clearAllUserData: clearAllUserDataMock,
  clearAllCache: clearAllCacheMock,
}))

// ---------------------------------------------------------------------------
// テスト
// ---------------------------------------------------------------------------
// vi.mock はホイスト済みなので動的 import でストアを取得する
let useAuthStore: (typeof import('./authStore'))['useAuthStore']

beforeEach(async () => {
  vi.clearAllMocks()
  // ストアモジュールをリセット (各テストで新鮮なストアを得る)
  vi.resetModules()
  const mod = await import('./authStore')
  useAuthStore = mod.useAuthStore
})

describe('authStore: signOut 順序 (D-1)', () => {
  it('(a) firebaseSignOut 成功時 → clearAllUserData が呼ばれ、user/token が null になる', async () => {
    // firebaseSignOut を成功させる
    firebaseSignOutMock.mockResolvedValueOnce(undefined)

    // user/token を初期状態にセット
    useAuthStore.setState({ user: { uid: 'user-1' } as never, token: 'tok' })

    await useAuthStore.getState().signOut()

    // clearAllUserData が呼ばれた
    expect(clearAllUserDataMock).toHaveBeenCalledOnce()
    // user/token がクリアされた
    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('(b) firebaseSignOut 失敗時 → clearAllUserData は呼ばれず、user state も残る', async () => {
    const authError = new Error('Firebase auth failed')
    firebaseSignOutMock.mockRejectedValueOnce(authError)

    useAuthStore.setState({ user: { uid: 'user-1' } as never, token: 'tok' })

    // signOut は throw する
    await expect(useAuthStore.getState().signOut()).rejects.toThrow('Firebase auth failed')

    // clearAllUserData は呼ばれていない
    expect(clearAllUserDataMock).not.toHaveBeenCalled()
    // user/token は残っている
    expect(useAuthStore.getState().user).not.toBeNull()
    expect(useAuthStore.getState().token).toBe('tok')
  })
})
