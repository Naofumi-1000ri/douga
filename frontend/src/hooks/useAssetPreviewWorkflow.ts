import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { assetsApi, type Asset } from '@/api/assets'
import type { TimelineData } from '@/store/projectStore'

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

  const clearPreview = useCallback(() => {
    setPreview({ asset: null, url: null, loading: false })
  }, [])

  const replaceAssets = useCallback((nextAssets: Asset[]) => {
    setAssets(nextAssets)
  }, [])

  const fetchAssets = useCallback(async () => {
    if (!projectId) return
    try {
      const data = await assetsApi.list(projectId)
      setAssets(data)
    } catch (error) {
      console.error('Failed to fetch assets:', error)
    }
  }, [projectId])

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

  const refreshAssetUrls = useCallback(async (forceRefresh = false) => {
    if (!projectId || assets.length === 0) return

    const mediaAssets = assets.filter(asset => asset.type === 'video' || asset.type === 'image' || asset.type === 'audio')

    await Promise.all(
      mediaAssets.map(async (asset) => {
        if (!forceRefresh && assetUrlCache.has(asset.id)) {
          if (asset.type === 'image' && !preloadedImages.has(asset.id)) {
            const cachedUrl = assetUrlCache.get(asset.id)
            if (!cachedUrl) return
            const image = new Image()
            try {
              image.src = cachedUrl
              await image.decode()
              setPreloadedImages(prev => new Set(prev).add(asset.id))
            } catch {
              console.error('Failed to decode image:', asset.id)
            }
          }
          return
        }

        try {
          const { url } = await assetsApi.getSignedUrl(projectId, asset.id)
          setAssetUrlCache(prev => new Map(prev).set(asset.id, url))

          if (asset.type === 'audio') {
            const audio = new Audio()
            audio.preload = 'auto'
            audio.src = url
          }

          if (asset.type === 'image') {
            const image = new Image()
            try {
              image.src = url
              await image.decode()
              setPreloadedImages(prev => new Set(prev).add(asset.id))
            } catch {
              console.error('Failed to decode image:', asset.id)
            }
          }
        } catch (error) {
          console.error('Failed to preload asset URL:', asset.id, error)
        }
      }),
    )
  }, [assetUrlCache, assets, preloadedImages, projectId])

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
