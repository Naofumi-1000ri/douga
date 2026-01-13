import { create } from 'zustand'
import {
  getAuth,
  signInWithPopup,
  GoogleAuthProvider,
  signOut as firebaseSignOut,
  onAuthStateChanged,
  User as FirebaseUser,
  IdTokenResult
} from 'firebase/auth'
import { initializeApp } from 'firebase/app'

// Dev mode configuration
const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true'
const DEV_TOKEN = 'dev-token'

// Dev user mock (mimics FirebaseUser structure)
const DEV_USER = {
  uid: 'dev-user-123',
  email: 'dev@example.com',
  displayName: '開発ユーザー',
  photoURL: null,
  emailVerified: true,
  isAnonymous: false,
  metadata: {},
  providerData: [],
  refreshToken: '',
  tenantId: null,
  delete: async () => {},
  getIdToken: async () => DEV_TOKEN,
  getIdTokenResult: async () => ({ token: DEV_TOKEN } as unknown as IdTokenResult),
  reload: async () => {},
  toJSON: () => ({}),
  phoneNumber: null,
  providerId: 'dev',
} as unknown as FirebaseUser

// Firebase config - parse from env var (handles both object and string formats)
const parseFirebaseConfig = () => {
  const configValue = import.meta.env.VITE_FIREBASE_CONFIG

  if (configValue) {
    // If it's already an object, use it directly
    if (typeof configValue === 'object' && configValue.apiKey) {
      return configValue
    }

    // If it's a string (JS object literal or JSON), parse it
    if (typeof configValue === 'string') {
      try {
        // Try JSON.parse first
        return JSON.parse(configValue)
      } catch {
        // Vite may embed config as multi-line string without commas
        // Fix: add commas after property values before newlines
        // Use String.fromCharCode(44) for comma to prevent minifier issues
        const comma = String.fromCharCode(44)
        const fixedConfig = configValue
          .replace(/"\s*\n\s*(\w)/g, (_: string, p1: string) => '"' + comma + '\n  ' + p1)
          .replace(/}\s*$/, '}')

        try {
          return new Function('return ' + fixedConfig)()
        } catch (e) {
          console.error('Failed to parse VITE_FIREBASE_CONFIG:', e)
        }
      }
    }
  }

  // Fallback to individual env vars
  return {
    apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
    storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: import.meta.env.VITE_FIREBASE_APP_ID,
  }
}

const firebaseConfig = parseFirebaseConfig()

// Only initialize Firebase if not in dev mode or if config exists
let app: ReturnType<typeof initializeApp> | null = null
let auth: ReturnType<typeof getAuth> | null = null
let googleProvider: GoogleAuthProvider | null = null

if (!DEV_MODE && firebaseConfig.apiKey) {
  app = initializeApp(firebaseConfig)
  auth = getAuth(app)
  googleProvider = new GoogleAuthProvider()
}

interface AuthState {
  user: FirebaseUser | null
  token: string | null
  loading: boolean
  error: string | null
  isDevMode: boolean
  signInWithGoogle: () => Promise<void>
  signInAsDev: () => void
  signOut: () => Promise<void>
  initialize: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  // In DEV_MODE, initialize with DEV_USER immediately
  user: DEV_MODE ? DEV_USER : null,
  token: DEV_MODE ? DEV_TOKEN : null,
  loading: DEV_MODE ? false : true,
  error: null,
  isDevMode: DEV_MODE,

  initialize: () => {
    // In dev mode, user is already set in initial state, nothing to do
    if (DEV_MODE) {
      return
    }

    // Normal Firebase auth - only when not in DEV_MODE
    if (auth) {
      onAuthStateChanged(auth, async (user) => {
        if (user) {
          const token = await user.getIdToken()
          set({ user, token, loading: false })
        } else {
          set({ user: null, token: null, loading: false })
        }
      })
    } else {
      set({ loading: false })
    }
  },

  signInAsDev: () => {
    set({ user: DEV_USER, token: DEV_TOKEN, loading: false, error: null })
  },

  signInWithGoogle: async () => {
    // In dev mode, just sign in as dev user
    if (DEV_MODE) {
      set({ user: DEV_USER, token: DEV_TOKEN, loading: false })
      return
    }

    if (!auth || !googleProvider) {
      set({ error: 'Firebase not configured', loading: false })
      return
    }

    try {
      set({ loading: true, error: null })
      const result = await signInWithPopup(auth, googleProvider)
      const token = await result.user.getIdToken()
      set({ user: result.user, token, loading: false })
    } catch (error) {
      set({ error: (error as Error).message, loading: false })
    }
  },

  signOut: async () => {
    // In dev mode, just clear state
    if (DEV_MODE) {
      set({ user: null, token: null })
      return
    }

    if (!auth) return

    try {
      await firebaseSignOut(auth)
      set({ user: null, token: null })
    } catch (error) {
      set({ error: (error as Error).message })
    }
  },
}))

// Initialize auth listener
useAuthStore.getState().initialize()
