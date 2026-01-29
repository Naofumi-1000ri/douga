import { useState, useEffect, useRef, useMemo } from 'react'
import { assetsApi, type WaveformData } from '@/api/assets'

// Global cache for waveform data - keyed by asset ID only (fixed sample count)
const waveformCache = new Map<string, WaveformData>()

// Pending fetch tracking to prevent duplicate API calls
const pendingFetches = new Map<string, Promise<WaveformData>>()

// Fixed sample count for all waveform requests - prevents cache fragmentation
const WAVEFORM_SAMPLES = 200

interface UseWaveformResult {
  peaks: number[] | null
  isLoading: boolean
  error: string | null
}

/**
 * Resample peaks array to target length using linear interpolation.
 */
function resamplePeaks(peaks: number[], targetLength: number): number[] {
  if (peaks.length === 0 || targetLength <= 0) return []
  if (peaks.length === targetLength) return peaks

  const result: number[] = []
  const ratio = (peaks.length - 1) / (targetLength - 1)

  for (let i = 0; i < targetLength; i++) {
    const srcIndex = i * ratio
    const lowIndex = Math.floor(srcIndex)
    const highIndex = Math.min(lowIndex + 1, peaks.length - 1)
    const fraction = srcIndex - lowIndex

    // Linear interpolation between adjacent samples
    const value = peaks[lowIndex] * (1 - fraction) + peaks[highIndex] * fraction
    result.push(value)
  }

  return result
}

/**
 * Hook to fetch and cache waveform data for an audio asset.
 * Uses a global cache with a fixed sample count to avoid cache fragmentation.
 * Resamples on the frontend if a different sample count is needed.
 */
export function useWaveform(
  projectId: string,
  assetId: string | null,
  targetSamples: number = 200
): UseWaveformResult {
  const [rawPeaks, setRawPeaks] = useState<number[] | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchedRef = useRef<string | null>(null)

  useEffect(() => {
    if (!projectId || !assetId) {
      setRawPeaks(null)
      return
    }

    // Cache key is just projectId:assetId (fixed sample count)
    const cacheKey = `${projectId}:${assetId}`

    // Check cache first
    if (waveformCache.has(cacheKey)) {
      setRawPeaks(waveformCache.get(cacheKey)!.peaks)
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
          setRawPeaks(data.peaks)
        } catch {
          // Error already handled by original fetch
        }
        return
      }

      setIsLoading(true)
      setError(null)

      // Create the fetch promise and store it
      const fetchPromise = assetsApi.getWaveform(projectId, assetId, WAVEFORM_SAMPLES)
      pendingFetches.set(cacheKey, fetchPromise)

      try {
        const data = await fetchPromise
        waveformCache.set(cacheKey, data)
        setRawPeaks(data.peaks)
      } catch (err) {
        console.error('Failed to fetch waveform:', err)
        setError('波形データの取得に失敗しました')
        setRawPeaks(null)
      } finally {
        pendingFetches.delete(cacheKey)
        setIsLoading(false)
      }
    }

    fetchWaveform()
  }, [projectId, assetId])

  // Resample peaks to target length on frontend
  const peaks = useMemo(() => {
    if (!rawPeaks || rawPeaks.length === 0) return rawPeaks
    if (targetSamples === WAVEFORM_SAMPLES) return rawPeaks
    return resamplePeaks(rawPeaks, targetSamples)
  }, [rawPeaks, targetSamples])

  return { peaks, isLoading, error }
}

/**
 * Clears the waveform cache (useful for memory management).
 */
export function clearWaveformCache(): void {
  waveformCache.clear()
}
