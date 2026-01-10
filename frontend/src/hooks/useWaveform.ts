import { useState, useEffect, useRef } from 'react'
import { assetsApi, type WaveformData } from '@/api/assets'

// Global cache for waveform data
const waveformCache = new Map<string, WaveformData>()

interface UseWaveformResult {
  peaks: number[] | null
  isLoading: boolean
  error: string | null
}

/**
 * Hook to fetch and cache waveform data for an audio asset.
 * Uses a global cache to avoid re-fetching the same waveform.
 */
export function useWaveform(
  projectId: string,
  assetId: string | null,
  samples: number = 200
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

    const cacheKey = `${projectId}:${assetId}:${samples}`

    // Check cache first
    if (waveformCache.has(cacheKey)) {
      setPeaks(waveformCache.get(cacheKey)!.peaks)
      return
    }

    // Avoid duplicate fetches
    if (fetchedRef.current === cacheKey) {
      return
    }
    fetchedRef.current = cacheKey

    const fetchWaveform = async () => {
      setIsLoading(true)
      setError(null)

      try {
        const data = await assetsApi.getWaveform(projectId, assetId, samples)
        waveformCache.set(cacheKey, data)
        setPeaks(data.peaks)
      } catch (err) {
        console.error('Failed to fetch waveform:', err)
        setError('波形データの取得に失敗しました')
        setPeaks(null)
      } finally {
        setIsLoading(false)
      }
    }

    fetchWaveform()
  }, [projectId, assetId, samples])

  return { peaks, isLoading, error }
}

/**
 * Clears the waveform cache (useful for memory management).
 */
export function clearWaveformCache(): void {
  waveformCache.clear()
}
