import { useState, useEffect, useRef } from 'react'
import { assetsApi, type WaveformData } from '@/api/assets'
import { RequestPriority, withPriority } from '@/utils/requestPriority'

// Global cache for waveform data - keyed by asset ID
const waveformCache = new Map<string, WaveformData>()

// Pending fetch tracking to prevent duplicate API calls
const pendingFetches = new Map<string, Promise<WaveformData>>()

// Samples per second for waveform requests (10 = 600 samples for 60 seconds)
const WAVEFORM_SAMPLES_PER_SECOND = 10

interface UseWaveformResult {
  peaks: number[] | null
  isLoading: boolean
  error: string | null
}

/**
 * Hook to fetch and cache waveform data for an audio asset.
 * Uses samples_per_second for consistent quality across all audio lengths.
 *
 * @param projectId - The project ID
 * @param assetId - The asset ID (or null)
 * @param priority - Request priority (MEDIUM for timeline waveforms, LOW for asset library)
 */
export function useWaveform(
  projectId: string,
  assetId: string | null,
  priority: RequestPriority = RequestPriority.LOW // Default to LOW for asset library usage
): UseWaveformResult {
  const [peaks, setPeaks] = useState<number[] | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchedRef = useRef<string | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false

    if (!projectId || !assetId) {
      setPeaks(null)
      return
    }

    // Cache key is projectId:assetId
    const cacheKey = `${projectId}:${assetId}`

    // Check cache first
    if (waveformCache.has(cacheKey)) {
      setPeaks(waveformCache.get(cacheKey)!.peaks)
      return
    }

    // Avoid duplicate fetches for same asset
    if (fetchedRef.current === cacheKey) {
      return
    }
    fetchedRef.current = cacheKey

    const fetchWaveform = async () => {
      // Check if there's already a pending fetch for this asset
      if (pendingFetches.has(cacheKey)) {
        try {
          const data = await pendingFetches.get(cacheKey)!
          if (!cancelledRef.current) {
            setPeaks(data.peaks)
          }
        } catch {
          // Error already handled by original fetch
        }
        return
      }

      setIsLoading(true)
      setError(null)

      // Use priority-aware fetching - waits for high priority requests to complete first
      try {
        const data = await withPriority(priority, async () => {
          // Check if cancelled during priority wait
          if (cancelledRef.current) {
            throw new Error('Cancelled')
          }

          // Create the fetch promise and store it
          const fetchPromise = assetsApi.getWaveform(projectId, assetId, WAVEFORM_SAMPLES_PER_SECOND)
          pendingFetches.set(cacheKey, fetchPromise)

          try {
            return await fetchPromise
          } finally {
            pendingFetches.delete(cacheKey)
          }
        })

        if (!cancelledRef.current) {
          waveformCache.set(cacheKey, data)
          setPeaks(data.peaks)
        }
      } catch (err) {
        if (!cancelledRef.current && (err as Error).message !== 'Cancelled') {
          console.error('Failed to fetch waveform:', err)
          setError('波形データの取得に失敗しました')
          setPeaks(null)
        }
      } finally {
        if (!cancelledRef.current) {
          setIsLoading(false)
        }
      }
    }

    fetchWaveform()

    return () => {
      cancelledRef.current = true
    }
  }, [projectId, assetId, priority])

  return { peaks, isLoading, error }
}

/**
 * Clears the waveform cache (useful for memory management).
 */
export function clearWaveformCache(): void {
  waveformCache.clear()
}
