import type { MutableRefObject } from 'react'
import type { Asset } from '@/api/assets'
import type { Clip } from '@/store/projectStore'

interface EditorPreviewPersistentAssetProps {
  asset: Asset
  clip: Clip
  invalidateAssetUrl: (assetId: string) => void
  syncVideoToTimelinePosition: (
    video: HTMLVideoElement,
    clip: Pick<Clip, 'start_ms' | 'duration_ms' | 'in_point_ms' | 'speed' | 'freeze_frame_ms'>,
  ) => void
  url: string
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorPreviewPersistentAsset({
  asset,
  clip,
  invalidateAssetUrl,
  syncVideoToTimelinePosition,
  url,
  videoRefsMap,
}: EditorPreviewPersistentAssetProps) {
  if (asset.type === 'image') {
    return (
      <div key={`persistent-${clip.id}`} className="absolute" style={{ opacity: 0, pointerEvents: 'none', zIndex: -1, top: 0, left: 0 }}>
        <div className="relative" style={{ userSelect: 'none' }}>
          <img src={url} alt="" className="block max-w-none" draggable={false} onError={() => clip.asset_id && invalidateAssetUrl(clip.asset_id)} />
        </div>
      </div>
    )
  }

  if (asset.type === 'video') {
    return (
      <div key={`persistent-${clip.id}`} className="absolute" style={{ opacity: 0, pointerEvents: 'none', zIndex: -1, top: 0, left: 0 }}>
        <div className="relative" style={{ userSelect: 'none' }}>
          <video
            ref={(element) => {
              if (element) videoRefsMap.current.set(clip.id, element)
              else videoRefsMap.current.delete(clip.id)
            }}
            src={url}
            crossOrigin="anonymous"
            className="block max-w-none"
            muted
            playsInline
            preload="auto"
            onError={() => clip.asset_id && invalidateAssetUrl(clip.asset_id)}
            onLoadedMetadata={(event) => {
              syncVideoToTimelinePosition(event.currentTarget, clip)
            }}
          />
        </div>
      </div>
    )
  }

  return null
}
