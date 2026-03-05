import { useEffect, useRef } from 'react'
import { doc, onSnapshot } from 'firebase/firestore'
import { db, DEV_MODE } from '@/lib/firebase'
import { useAuthStore } from '@/store/authStore'
import { useProjectStore, waitForSaveChain } from '@/store/projectStore'
import { sequencesApi } from '@/api/sequences'

export function useRemoteSync(projectId: string | undefined, sequenceId: string | undefined): void {
  const user = useAuthStore(s => s.user)
  const fetchPendingRef = useRef(false)
  const initialSnapshotRef = useRef(true)

  useEffect(() => {
    if (!projectId || !sequenceId || DEV_MODE || !db) return

    // Reset flags on mount / dependency change
    initialSnapshotRef.current = true
    fetchPendingRef.current = false

    const docRef = doc(db, 'project_updates', projectId)
    const unsubscribe = onSnapshot(docRef, (snapshot) => {
      // Skip the initial snapshot (existing document loaded on subscribe)
      if (initialSnapshotRef.current) {
        initialSnapshotRef.current = false
        return
      }

      const data = snapshot.data()
      if (!data) return

      // Layer 1: Skip if user_id matches (self-change)
      if (data.user_id && user?.uid && data.user_id === user.uid) {
        console.log('[RemoteSync] Skipping own change (user_id match)')
        return
      }

      // Layer 2: Skip if lastLocalChangeMs is within 3 seconds
      const { lastLocalChangeMs } = useProjectStore.getState()
      if (Date.now() - lastLocalChangeMs < 3000) {
        console.log('[RemoteSync] Skipping - recent local change')
        return
      }

      // Deduplicate: if a fetch is already pending, skip
      if (fetchPendingRef.current) {
        console.log('[RemoteSync] Fetch already pending, skipping')
        return
      }

      fetchPendingRef.current = true
      console.log('[RemoteSync] Remote change detected:', data.source, data.operation)

      // Wait 500ms for DB commit to complete, then fetch
      setTimeout(async () => {
        try {
          // Wait for any in-flight saves to complete
          await waitForSaveChain()

          // Re-check: another local change may have happened during the wait
          const { lastLocalChangeMs: recheck } = useProjectStore.getState()
          if (Date.now() - recheck < 2000) {
            console.log('[RemoteSync] Skipping - local change during wait')
            return
          }

          const result = await sequencesApi.get(projectId, sequenceId)

          // Layer 3: Version comparison
          const { currentSequence } = useProjectStore.getState()
          if (!currentSequence || currentSequence.id !== sequenceId) {
            console.log('[RemoteSync] Sequence changed, skipping apply')
            return
          }
          if (result.version <= currentSequence.version) {
            console.log('[RemoteSync] Version unchanged, skipping (server:', result.version, 'local:', currentSequence.version, ')')
            return
          }

          // Apply remote sequence to store
          useProjectStore.getState().applyRemoteSequence(result)
          console.log('[RemoteSync] Applied remote update, version:', result.version)
        } catch (error) {
          console.error('[RemoteSync] Failed to fetch sequence:', error)
        } finally {
          fetchPendingRef.current = false
        }
      }, 500)
    })

    return () => {
      unsubscribe()
    }
  }, [projectId, sequenceId, user])
}
