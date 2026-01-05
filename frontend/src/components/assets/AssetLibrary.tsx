import { useEffect, useState, useCallback } from 'react'
import { assetsApi, type Asset } from '@/api/assets'

interface AssetLibraryProps {
  projectId: string
  onPreviewAsset?: (asset: Asset) => void
  onAssetsChange?: () => void
}

const ASSET_SUBTYPES = {
  audio: [
    { value: 'narration', label: 'ナレーション' },
    { value: 'bgm', label: 'BGM' },
    { value: 'se', label: '効果音' },
  ],
  video: [
    { value: 'avatar', label: 'アバター' },
    { value: 'background', label: '背景' },
    { value: 'slide', label: 'スライド' },
    { value: 'other', label: 'その他' },
  ],
  image: [
    { value: 'background', label: '背景' },
    { value: 'slide', label: 'スライド' },
    { value: 'effect', label: 'エフェクト' },
    { value: 'other', label: 'その他' },
  ],
}

export default function AssetLibrary({ projectId, onPreviewAsset, onAssetsChange }: AssetLibraryProps) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [extracting, setExtracting] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'audio' | 'video' | 'image'>('audio')
  const [selectedSubtype, setSelectedSubtype] = useState<string>('narration')

  const fetchAssets = useCallback(async () => {
    try {
      const data = await assetsApi.list(projectId)
      setAssets(data)
    } catch (error) {
      console.error('Failed to fetch assets:', error)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchAssets()
  }, [fetchAssets])

  useEffect(() => {
    // Update selected subtype when tab changes
    const subtypes = ASSET_SUBTYPES[activeTab]
    if (subtypes.length > 0) {
      setSelectedSubtype(subtypes[0].value)
    }
  }, [activeTab])

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    const duplicates: string[] = []
    const errors: string[] = []

    try {
      for (const file of Array.from(files)) {
        try {
          await assetsApi.uploadFile(projectId, file, selectedSubtype)
        } catch (error: unknown) {
          const axiosError = error as { response?: { status?: number } }
          if (axiosError.response?.status === 409) {
            duplicates.push(file.name)
          } else {
            errors.push(file.name)
          }
        }
      }

      if (duplicates.length > 0) {
        alert(`以下のファイルは既に存在します:\n${duplicates.join('\n')}`)
      }
      if (errors.length > 0) {
        console.error('Failed to upload files:', errors)
        alert(`以下のファイルのアップロードに失敗しました:\n${errors.join('\n')}`)
      }

      await fetchAssets()
      onAssetsChange?.()
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  const handleDeleteAsset = async (assetId: string) => {
    if (!confirm('このアセットを削除しますか？')) return
    try {
      await assetsApi.delete(projectId, assetId)
      setAssets(assets.filter((a) => a.id !== assetId))
      onAssetsChange?.()
    } catch (error) {
      console.error('Failed to delete asset:', error)
    }
  }

  const handleExtractAudio = async (assetId: string) => {
    setExtracting(assetId)
    try {
      await assetsApi.extractAudio(projectId, assetId)
      await fetchAssets()
      onAssetsChange?.()
      setActiveTab('audio')
    } catch (error) {
      console.error('Failed to extract audio:', error)
      alert('音声の抽出に失敗しました')
    } finally {
      setExtracting(null)
    }
  }

  const filteredAssets = assets.filter((asset) => asset.type === activeTab)

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const formatDuration = (ms: number | null) => {
    if (!ms) return ''
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-white font-medium mb-3">アセット</h2>

        {/* Tabs */}
        <div className="flex gap-1 bg-gray-900 rounded-lg p-1">
          {(['audio', 'video', 'image'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 px-3 py-1.5 text-sm rounded-md transition-colors ${
                activeTab === tab
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {tab === 'audio' ? '音声' : tab === 'video' ? '動画' : '画像'}
            </button>
          ))}
        </div>
      </div>

      {/* Subtype Filter */}
      <div className="px-4 py-2 border-b border-gray-700">
        <select
          value={selectedSubtype}
          onChange={(e) => setSelectedSubtype(e.target.value)}
          className="w-full px-3 py-1.5 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-primary-500"
        >
          {ASSET_SUBTYPES[activeTab].map((subtype) => (
            <option key={subtype.value} value={subtype.value}>
              {subtype.label}
            </option>
          ))}
        </select>
      </div>

      {/* Upload Button */}
      <div className="px-4 py-2 border-b border-gray-700">
        <label className="block">
          <input
            type="file"
            multiple
            accept={
              activeTab === 'audio'
                ? 'audio/*'
                : activeTab === 'video'
                ? 'video/*'
                : 'image/*'
            }
            onChange={handleFileUpload}
            className="hidden"
            disabled={uploading}
          />
          <span
            className={`flex items-center justify-center gap-2 px-4 py-2 border border-dashed border-gray-600 rounded-lg cursor-pointer hover:border-primary-500 hover:bg-gray-700/50 transition-colors ${
              uploading ? 'opacity-50 cursor-not-allowed' : ''
            }`}
          >
            {uploading ? (
              <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-primary-500"></div>
            ) : (
              <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            )}
            <span className="text-sm text-gray-400">
              {uploading ? 'アップロード中...' : 'ファイルを追加'}
            </span>
          </span>
        </label>
      </div>

      {/* Asset List */}
      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : filteredAssets.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">
            アセットがありません
          </div>
        ) : (
          <div className="space-y-2">
            {filteredAssets.map((asset) => (
              <div
                key={asset.id}
                className="bg-gray-700 rounded-lg p-2 cursor-grab hover:bg-gray-600 transition-colors group active:cursor-grabbing"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/x-asset-id', asset.id)
                  e.dataTransfer.setData('application/x-asset-type', asset.type)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                onDoubleClick={() => onPreviewAsset?.(asset)}
              >
                <div className="flex items-center gap-2">
                  {/* Thumbnail */}
                  <div className="w-12 h-12 bg-gray-600 rounded flex items-center justify-center flex-shrink-0">
                    {asset.thumbnail_url ? (
                      <img
                        src={asset.thumbnail_url}
                        alt={asset.name}
                        className="w-full h-full object-cover rounded"
                      />
                    ) : (
                      <svg
                        className="w-6 h-6 text-gray-400"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        {asset.type === 'audio' ? (
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                        ) : asset.type === 'video' ? (
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        ) : (
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                        )}
                      </svg>
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white truncate">{asset.name}</p>
                    <p className="text-xs text-gray-400">
                      {formatFileSize(asset.file_size)}
                      {asset.duration_ms && ` • ${formatDuration(asset.duration_ms)}`}
                    </p>
                  </div>

                  {/* Extract Audio Button (video only) */}
                  {asset.type === 'video' && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleExtractAudio(asset.id)
                      }}
                      disabled={extracting === asset.id}
                      className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-primary-500 transition-all disabled:opacity-50"
                      title="音声を抽出"
                    >
                      {extracting === asset.id ? (
                        <div className="animate-spin rounded-full h-4 w-4 border-t-2 border-b-2 border-primary-500"></div>
                      ) : (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                        </svg>
                      )}
                    </button>
                  )}

                  {/* Delete Button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteAsset(asset.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
