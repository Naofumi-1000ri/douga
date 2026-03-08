import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { assetsApi, type Asset } from '@/api/assets'
import type { TimelineData } from '@/store/projectStore'

const BACKGROUND_ASSET_HYDRATION_DELAY_MS = 750
const MAX_CONCURRENT_ASSET_REFRESHES = 4

async function runWithConcurrencyLimit<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let nextIndex = 0
  const workerCount = Math.min(limit, items.length)

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (nextIndex < items.length) {
        const currentIndex = nextIndex
        nextIndex += 1
        await worker(items[currentIndex])
      }
    }),
  )
}

export interface PreviewState {
  asset: Asset | null
  url: string | null
  loading: boolean
}

interface UseAssetPreviewWorkflowParams {
  currentTime: number
  projectId?: string
  timelineData?: TimelineData
}

interface UseAssetPreviewWorkflowResult {
  assets: Asset[]
  assetUrlCache: Map<string, string>
  clearPreview: () => void
  fetchAssets: () => Promise<void>
  invalidateAssetUrl: (assetId: string) => void
  preview: PreviewState
  previewAsset: (asset: Asset) => Promise<void>
  replaceAssets: (assets: Asset[]) => void
}

export function useAssetPreviewWorkflow({
  currentTime,
  projectId,
  timelineData,
}: UseAssetPreviewWorkflowParams): UseAssetPreviewWorkflowResult {
  const [assets, setAssets] = useState<Asset[]>([])
  const [preview, setPreview] = useState<PreviewState>({ asset: null, url: null, loading: false })
  const [assetUrlCache, setAssetUrlCache] = useState<Map<string, string>>(new Map())
  const [preloadedImages, setPreloadedImages] = useState<Set<string>>(new Set())
  const assetUrlGenRef = useRef(new Map<string, number>())
  const assetUrlCacheRef = useRef(new Map<string, string>())
  const backgroundHydrationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const preloadedImagesRef = useRef(new Set<string>())

  const clearPreview = useCallback(() => {
    setPreview({ asset: null, url: null, loading: false })
  }, [])

  const replaceAssets = useCallback((nextAssets: Asset[]) => {
    setAssets(nextAssets)
  }, [])

  const timelineAssetIds = useMemo(() => {
    const next = new Set<string>()

    if (!timelineData) return next

    for (const layer of timelineData.layers) {
      for (const clip of layer.clips) {
        if (clip.asset_id) {
          next.add(clip.asset_id)
        }
      }
    }

    for (const track of timelineData.audio_tracks) {
      for (const clip of track.clips) {
        next.add(clip.asset_id)
      }
    }

    return next
  }, [timelineData])

  const fetchAssets = useCallback(async () => {
    if (!projectId) return
    try {
      const data = await assetsApi.list(projectId)
      if (backgroundHydrationTimerRef.current) {
        clearTimeout(backgroundHydrationTimerRef.current)
        backgroundHydrationTimerRef.current = null
      }

      if (timelineAssetIds.size === 0) {
        setAssets(data)
        return
      }

      const prioritizedAssets = data.filter((asset) => timelineAssetIds.has(asset.id))
      if (prioritizedAssets.length === 0 || prioritizedAssets.length === data.length) {
        setAssets(data)
        return
      }

      setAssets(prioritizedAssets)
      backgroundHydrationTimerRef.current = setTimeout(() => {
        startTransition(() => {
          setAssets(data)
        })
        backgroundHydrationTimerRef.current = null
      }, BACKGROUND_ASSET_HYDRATION_DELAY_MS)
    } catch (error) {
      console.error('Failed to fetch assets:', error)
    }
  }, [projectId, timelineAssetIds])

  const previewAsset = useCallback(async (asset: Asset) => {
    if (!projectId) return

    if (preview.asset?.id === asset.id) {
      clearPreview()
      return
    }

    setPreview({ asset, url: null, loading: true })

    try {
      const { url } = await assetsApi.getSignedUrl(projectId, asset.id)
      setPreview({ asset, url, loading: false })
    } catch (error) {
      console.error('Failed to get preview URL:', error)
      clearPreview()
    }
  }, [clearPreview, preview.asset?.id, projectId])

  useEffect(() => {
    assetUrlCacheRef.current = assetUrlCache
  }, [assetUrlCache])

  useEffect(() => {
    preloadedImagesRef.current = preloadedImages
  }, [preloadedImages])

  useEffect(() => () => {
    if (backgroundHydrationTimerRef.current) {
      clearTimeout(backgroundHydrationTimerRef.current)
      backgroundHydrationTimerRef.current = null
    }
  }, [])

  const refreshAssetUrls = useCallback(async (forceRefresh = false) => {
    if (!projectId || assets.length === 0 || timelineAssetIds.size === 0) return

    const mediaAssets = assets.filter(
      (asset) => timelineAssetIds.has(asset.id)
        && (asset.type === 'video' || asset.type === 'image' || asset.type === 'audio'),
    )

    const nextAssetUrlCache = new Map(assetUrlCacheRef.current)
    const nextPreloadedImages = new Set(preloadedImagesRef.current)
    let cacheChanged = false
    let preloadedImagesChanged = false

    await runWithConcurrencyLimit(mediaAssets, MAX_CONCURRENT_ASSET_REFRESHES, async (asset) => {
      let url = nextAssetUrlCache.get(asset.id) ?? null

      if (forceRefresh || !url) {
        try {
          const result = await assetsApi.getSignedUrl(projectId, asset.id)
          url = result.url
          nextAssetUrlCache.set(asset.id, url)
          cacheChanged = true
        } catch (error) {
          console.error('Failed to preload asset URL:', asset.id, error)
          return
        }
      }

      if (asset.type !== 'image' || nextPreloadedImages.has(asset.id) || !url) {
        return
      }

      const image = new Image()
      try {
        image.src = url
        await image.decode()
        nextPreloadedImages.add(asset.id)
        preloadedImagesChanged = true
      } catch {
        console.error('Failed to decode image:', asset.id)
      }
    })

    if (cacheChanged) {
      assetUrlCacheRef.current = nextAssetUrlCache
      setAssetUrlCache(new Map(nextAssetUrlCache))
    }

    if (preloadedImagesChanged) {
      preloadedImagesRef.current = nextPreloadedImages
      setPreloadedImages(new Set(nextPreloadedImages))
    }
  }, [assets, projectId, timelineAssetIds])

  useEffect(() => {
    void refreshAssetUrls()
  }, [refreshAssetUrls])

  useEffect(() => {
    if (!projectId || assets.length === 0) return

    const refreshIntervalMs = 10 * 60 * 1000
    const intervalId = setInterval(() => {
      console.log('[AssetURLRefresh] Refreshing all signed URLs')
      void refreshAssetUrls(true)
    }, refreshIntervalMs)

    return () => clearInterval(intervalId)
  }, [assets.length, projectId, refreshAssetUrls])

  useEffect(() => {
    assetUrlGenRef.current.clear()
  }, [projectId])

  const invalidateAssetUrl = useCallback((assetId: string) => {
    if (!projectId) return

    const generation = (assetUrlGenRef.current.get(assetId) ?? 0) + 1
    assetUrlGenRef.current.set(assetId, generation)
    console.log('[AssetURLRefresh] Refreshing URL for asset:', assetId, 'gen:', generation)

    assetsApi.getSignedUrl(projectId, assetId)
      .then(({ url }) => {
        if (assetUrlGenRef.current.get(assetId) !== generation) return
        setAssetUrlCache(prev => new Map(prev).set(assetId, url))
        setPreloadedImages(prev => {
          const next = new Set(prev)
          next.delete(assetId)
          return next
        })
      })
      .catch((error) => {
        if (assetUrlGenRef.current.get(assetId) !== generation) return
        console.error('[AssetURLRefresh] Re-fetch failed:', assetId, error)
        setAssetUrlCache(prev => {
          const next = new Map(prev)
          next.delete(assetId)
          return next
        })
        setPreloadedImages(prev => {
          const next = new Set(prev)
          next.delete(assetId)
          return next
        })
      })
  }, [projectId])

  const assetIdAtPlayhead = useMemo(() => {
    if (!timelineData) return null

    for (let index = timelineData.layers.length - 1; index >= 0; index -= 1) {
      const layer = timelineData.layers[index]
      if (layer.visible === false) continue

      for (const clip of layer.clips) {
        const clipEnd = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
        if (currentTime >= clip.start_ms && currentTime < clipEnd) {
          return clip.asset_id
        }
      }
    }

    return null
  }, [currentTime, timelineData])

  useEffect(() => {
    if (!projectId) return

    if (!assetIdAtPlayhead) {
      if (preview.asset) {
        clearPreview()
      }
      return
    }

    const asset = assets.find(candidate => candidate.id === assetIdAtPlayhead)
    if (!asset) return
    if (asset.type !== 'video' && asset.type !== 'image') return
    if (preview.asset?.id === asset.id) return

    const cachedUrl = assetUrlCache.get(assetIdAtPlayhead)
    if (cachedUrl) {
      setPreview({ asset, url: cachedUrl, loading: false })
      return
    }

    const loadPreview = async () => {
      setPreview({ asset, url: null, loading: true })
      try {
        const { url } = await assetsApi.getSignedUrl(projectId, assetIdAtPlayhead)
        setPreview({ asset, url, loading: false })
        setAssetUrlCache(prev => new Map(prev).set(assetIdAtPlayhead, url))
      } catch (error) {
        console.error('Failed to load video clip preview:', error)
        clearPreview()
      }
    }

    void loadPreview()
  }, [assetIdAtPlayhead, assetUrlCache, assets, clearPreview, preview.asset, projectId])

  return {
    assets,
    assetUrlCache,
    clearPreview,
    fetchAssets,
    invalidateAssetUrl,
    preview,
    previewAsset,
    replaceAssets,
  }
}
