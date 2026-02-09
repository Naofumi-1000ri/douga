import { useEffect, useRef, useState } from 'react'
import { doc, setDoc, deleteDoc, onSnapshot, collection, Timestamp, serverTimestamp } from 'firebase/firestore'
import { db, DEV_MODE } from '@/lib/firebase'
import { useAuthStore } from '@/store/authStore'

export interface PresenceUser {
  userId: string
  displayName: string
  photoURL: string | null
  lastSeen: Timestamp
}

export function useProjectPresence(projectId: string | undefined) {
  const [users, setUsers] = useState<PresenceUser[]>([])
  const user = useAuthStore(s => s.user)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!projectId || !user || DEV_MODE || !db) return

    const visitorId = user.uid
    const presenceDocRef = doc(db, 'project_presence', projectId, 'users', visitorId)

    // Write initial presence
    const presenceData = {
      userId: visitorId,
      displayName: user.displayName || 'Unknown',
      photoURL: user.photoURL || null,
      lastSeen: serverTimestamp(),
    }
    setDoc(presenceDocRef, presenceData)

    // Heartbeat every 30 seconds
    heartbeatRef.current = setInterval(() => {
      setDoc(presenceDocRef, { lastSeen: serverTimestamp() }, { merge: true })
    }, 30000)

    // Listen to all presence docs
    const collectionRef = collection(db, 'project_presence', projectId, 'users')
    const unsubscribe = onSnapshot(collectionRef, (snapshot) => {
      const now = Date.now()
      const activeUsers: PresenceUser[] = []
      snapshot.forEach((doc) => {
        const data = doc.data() as PresenceUser
        if (data.lastSeen) {
          const lastSeenMs = data.lastSeen instanceof Timestamp
            ? data.lastSeen.toMillis()
            : Date.now()
          if (now - lastSeenMs < 60000) {
            if (data.userId !== visitorId) {
              activeUsers.push(data)
            }
          }
        }
      })
      setUsers(activeUsers)
    })

    return () => {
      if (heartbeatRef.current) {
        clearInterval(heartbeatRef.current)
      }
      unsubscribe()
      deleteDoc(presenceDocRef).catch(() => {})
    }
  }, [projectId, user])

  return { users }
}
