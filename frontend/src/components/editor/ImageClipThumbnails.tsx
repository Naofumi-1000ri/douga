import { memo, useMemo } from 'react'

interface ImageClipThumbnailsProps {
  imageUrl: string
  assetId: string
  clipWidth: number
  clipHeight?: number
}

/**
 * Displays tiled thumbnails from an image, filling the entire clip width.
 * Thumbnails are positioned side-by-side like a filmstrip.
 */
const ImageClipThumbnails = memo(function ImageClipThumbnails({
  imageUrl,
  assetId,
  clipWidth,
  clipHeight = 40,
}: ImageClipThumbnailsProps) {
  // Calculate thumbnail dimensions based on clip height
  // Fill the entire clip area without padding
  const thumbHeight = Math.max(24, clipHeight)
  const thumbWidth = Math.round(thumbHeight * (16 / 9))
  const thumbTop = 0

  // Calculate how many thumbnails fit
  const thumbnails = useMemo(() => {
    const count = Math.max(1, Math.ceil(clipWidth / thumbWidth))
    return Array.from({ length: count }, (_, i) => ({
      position: i * thumbWidth,
      key: i,
    }))
  }, [clipWidth, thumbWidth])

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden">
      {thumbnails.map(({ position, key }) => (
        <div
          key={key}
          className="absolute"
          style={{
            left: position,
            top: thumbTop,
            height: thumbHeight,
            width: thumbWidth,
          }}
        >
          <img
            src={imageUrl}
            alt=""
            className="object-cover rounded-sm opacity-70"
            style={{
              width: thumbWidth,
              height: thumbHeight,
            }}
            loading="lazy"
            onError={(e) => {
              const img = e.currentTarget
              // 1 サムネイルあたり 1 回までリトライ (無限ループ防止) — #252
              if (img.dataset.retried === '1') return
              img.dataset.retried = '1'
              const goog_date = img.src.match(/X-Goog-Date=(\d{8}T\d{6}Z)/)?.[1]
              console.warn('[ImageClipThumbnails] thumbnail load failed, invalidating cache', {
                assetId,
                X_Goog_Date: goog_date,
                url_head: img.src.substring(0, 200),
              })
              // Trigger global refresh; AssetLibrary single-flight guard prevents multi-fetch
              window.dispatchEvent(new CustomEvent('douga-assets-changed'))
            }}
          />
        </div>
      ))}
    </div>
  )
})

export default ImageClipThumbnails
