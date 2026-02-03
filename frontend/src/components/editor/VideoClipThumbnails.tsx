import { useState, useEffect, memo, useRef, useMemo } from 'react'
import { assetsApi } from '@/api/assets'

// Request queue to limit concurrent thumbnail requests
class ThumbnailRequestQueue {
  private queue: (() => Promise<void>)[] = []
  private running = 0
  private maxConcurrent = 5

  async enqueue<T>(fn: () => Promise<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      const run = async () => {
        this.running++
        try {
          const result = await fn()
          resolve(result)
        } catch (error) {
          reject(error)
        } finally {
          this.running--
          this.processNext()
        }
      }

      if (this.running < this.maxConcurrent) {
        run()
      } else {
        this.queue.push(run)
      }
    })
  }

  private processNext() {
    if (this.queue.length > 0 && this.running < this.maxConcurrent) {
      const next = this.queue.shift()
      next?.()
    }
  }
}

const thumbnailQueue = new ThumbnailRequestQueue()

interface VideoClipThumbnailsProps {
  projectId: string
  assetId: string
  clipWidth: number
  inPointMs: number
  durationMs: number   // Timeline duration (ms)
  speed: number        // Playback speed
  clipHeight?: number  // Optional: height of the clip container (defaults to 40)
}

// Global cache for thumbnail URLs
const thumbnailCache = new Map<string, string>()

// Single thumbnail component
interface ThumbnailProps {
  projectId: string
  assetId: string
  timeMs: number
  width: number
  height: number
  delay?: number  // Delay before fetching (for progressive loading)
}

const Thumbnail = memo(function Thumbnail({
  projectId,
  assetId,
  timeMs,
  width,
  height,
  delay = 0,
}: ThumbnailProps) {
  const [url, setUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const lastFetchKey = useRef<string | null>(null)

  useEffect(() => {
    const cacheKey = `${projectId}:${assetId}:${timeMs}:${width}:${height}`

    // Skip if already fetched with same params
    if (lastFetchKey.current === cacheKey) return
    lastFetchKey.current = cacheKey

    // Check cache first
    if (thumbnailCache.has(cacheKey)) {
      setUrl(thumbnailCache.get(cacheKey)!)
      setIsLoading(false)
      return
    }

    setIsLoading(true)
    setHasError(false)

    const fetchThumbnail = async () => {
      // Wait for delay before fetching (progressive loading)
      if (delay > 0) {
        await new Promise(resolve => setTimeout(resolve, delay))
      }

      try {
        // Use queue to limit concurrent requests (prevents DB pool exhaustion)
        const response = await thumbnailQueue.enqueue(() =>
          assetsApi.getThumbnail(projectId, assetId, timeMs, width, height)
        )
        thumbnailCache.set(cacheKey, response.url)
        setUrl(response.url)
        setIsLoading(false)
      } catch {
        setHasError(true)
        setIsLoading(false)
      }
    }

    fetchThumbnail()
  }, [projectId, assetId, timeMs, width, height, delay])

  // Show loading placeholder
  if (isLoading) {
    return (
      <div
        className="animate-pulse rounded-sm bg-gray-700/50"
        style={{
          width,
          height,
        }}
      />
    )
  }

  // Show error state with film icon
  if (hasError || !url) {
    return (
      <div
        className="bg-gray-600/40 flex items-center justify-center rounded-sm"
        style={{
          width,
          height,
        }}
      >
        <svg
          className="w-4 h-4 text-gray-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z"
          />
        </svg>
      </div>
    )
  }

  return (
    <img
      src={url}
      alt=""
      className="object-cover rounded-sm"
      style={{
        width,
        height,
      }}
      loading="lazy"
    />
  )
})

/**
 * Displays tiled thumbnails from a video clip, filling the entire clip width.
 * Thumbnails are positioned side-by-side like a filmstrip.
 * Limited to max 20 thumbnails for performance.
 *
 * Each thumbnail shows the frame at its LEFT EDGE position on the timeline.
 */
const VideoClipThumbnails = memo(function VideoClipThumbnails({
  projectId,
  assetId,
  clipWidth,
  inPointMs,
  durationMs,
  speed,
  clipHeight = 40,
}: VideoClipThumbnailsProps) {
  // Calculate thumbnail dimensions based on clip height
  const thumbHeight = Math.max(24, clipHeight - 4)
  const thumbWidth = Math.round(thumbHeight * (16 / 9))
  const thumbTop = 2

  // Generate thumbnails - each shows frame at its left edge position
  // Using same formula as playback: sourceTime = in_point_ms + timelineOffset * speed
  const thumbnails = useMemo(() => {
    const thumbCount = Math.min(20, Math.max(1, Math.floor(clipWidth / thumbWidth)))
    const result: { timeMs: number; delay: number; position: number }[] = []

    for (let i = 0; i < thumbCount; i++) {
      const position = i * thumbWidth
      // timelineOffset = how far into the clip (in timeline ms)
      // sourceTime = in_point + timelineOffset * speed
      const timelineOffsetMs = (position / clipWidth) * durationMs
      const timeMs = clipWidth > 0
        ? Math.round(inPointMs + timelineOffsetMs * speed)
        : inPointMs
      const delay = i < 3 ? 0 : Math.min((i - 2) * 100, 500)
      result.push({ timeMs, delay, position })
    }

    return result
  }, [clipWidth, thumbWidth, inPointMs, durationMs, speed])

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden">
      {thumbnails.map(({ timeMs, delay, position }, index) => (
        <div
          key={`${assetId}-${index}`}
          className="absolute"
          style={{
            left: position,
            top: thumbTop,
            height: thumbHeight,
            width: thumbWidth,
          }}
        >
          <Thumbnail
            projectId={projectId}
            assetId={assetId}
            timeMs={timeMs}
            width={thumbWidth}
            height={thumbHeight}
            delay={delay}
          />
        </div>
      ))}
    </div>
  )
})

export default VideoClipThumbnails

/**
 * Clears the thumbnail cache (useful for memory management).
 */
export function clearThumbnailCache(): void {
  thumbnailCache.clear()
}
