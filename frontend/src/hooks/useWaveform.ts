import { useState, useEffect, useRef } from 'react'
import { assetsApi, type WaveformData } from '@/api/assets'

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
 */
export function useWaveform(
  projectId: string,
  assetId: string | null,
  _samplesPerSecond: number = WAVEFORM_SAMPLES_PER_SECOND // kept for API compatibility
): UseWaveformResult {
  const [peaks, setPeaks] = useState<number[] | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchedRef = useRef<string | null>(null)

  useEffect(() => {
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
          setPeaks(data.peaks)
        } catch {
          // Error already handled by original fetch
        }
        return
      }

      setIsLoading(true)
      setError(null)

      // Create the fetch promise and store it
      const fetchPromise = assetsApi.getWaveform(projectId, assetId, WAVEFORM_SAMPLES_PER_SECOND)
      pendingFetches.set(cacheKey, fetchPromise)

      try {
        const data = await fetchPromise
        waveformCache.set(cacheKey, data)
        setPeaks(data.peaks)
      } catch (err) {
        console.error('Failed to fetch waveform:', err)
        setError('波形データの取得に失敗しました')
        setPeaks(null)
      } finally {
        pendingFetches.delete(cacheKey)
        setIsLoading(false)
      }
    }

    fetchWaveform()
  }, [projectId, assetId])

  return { peaks, isLoading, error }
}

/**
 * Clears the waveform cache (useful for memory management).
 */
export function clearWaveformCache(): void {
  waveformCache.clear()
}
