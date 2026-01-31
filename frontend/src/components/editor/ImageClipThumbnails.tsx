import { memo, useMemo } from 'react'

interface ImageClipThumbnailsProps {
  imageUrl: string
  clipWidth: number
  clipHeight?: number
}

/**
 * Displays tiled thumbnails from an image, filling the entire clip width.
 * Thumbnails are positioned side-by-side like a filmstrip.
 */
const ImageClipThumbnails = memo(function ImageClipThumbnails({
  imageUrl,
  clipWidth,
  clipHeight = 40,
}: ImageClipThumbnailsProps) {
  // Calculate thumbnail dimensions based on clip height
  // Leave 4px padding (2px top + 2px bottom) for visual balance
  const thumbHeight = Math.max(24, clipHeight - 4)
  const thumbWidth = Math.round(thumbHeight * (16 / 9))
  const thumbTop = 2

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
          />
        </div>
      ))}
    </div>
  )
})

export default ImageClipThumbnails
