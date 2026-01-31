import { initializeApp, FirebaseApp } from 'firebase/app'
import { getAuth, Auth, GoogleAuthProvider } from 'firebase/auth'
import { getFirestore, Firestore } from 'firebase/firestore'

// Dev mode configuration
export const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true'

// Firebase config - parse from env var (handles both object and string formats)
const parseFirebaseConfig = () => {
  const configValue = import.meta.env.VITE_FIREBASE_CONFIG

  if (configValue) {
    // If it's already an object, use it directly
    if (typeof configValue === 'object' && configValue.apiKey) {
      return configValue
    }

    // If it's a string, extract values directly using regex
    if (typeof configValue === 'string') {
      const extractValue = (key: string): string | undefined => {
        const regex = new RegExp(key + ':\\s*"([^"]*)"')
        const match = configValue.match(regex)
        return match ? match[1] : undefined
      }

      const extracted = {
        apiKey: extractValue('apiKey'),
        authDomain: extractValue('authDomain'),
        projectId: extractValue('projectId'),
        storageBucket: extractValue('storageBucket'),
        messagingSenderId: extractValue('messagingSenderId'),
        appId: extractValue('appId'),
      }

      if (extracted.apiKey) {
        return extracted
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

// Firebase instances
export let app: FirebaseApp | null = null
export let auth: Auth | null = null
export let db: Firestore | null = null
export let googleProvider: GoogleAuthProvider | null = null

// Only initialize Firebase if not in dev mode or if config exists
if (!DEV_MODE && firebaseConfig.apiKey) {
  app = initializeApp(firebaseConfig)
  auth = getAuth(app)
  db = getFirestore(app)
  googleProvider = new GoogleAuthProvider()
}
