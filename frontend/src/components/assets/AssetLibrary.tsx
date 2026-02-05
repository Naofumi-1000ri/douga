import { useEffect, useState, useCallback, useRef, useLayoutEffect } from 'react'
import { assetsApi, foldersApi, type Asset, type AssetFolder, type SessionData } from '@/api/assets'

// Cache for video thumbnails
const thumbnailCache = new Map<string, string>()

interface AssetLibraryProps {
  projectId: string
  onPreviewAsset?: (asset: Asset) => void
  onAssetsChange?: () => void
  onOpenSession?: (sessionData: SessionData) => void  // Called when user opens a session
  refreshTrigger?: number  // Increment this to force a refresh of the asset list
}

export default function AssetLibrary({ projectId, onPreviewAsset, onAssetsChange, onOpenSession, refreshTrigger }: AssetLibraryProps) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [folders, setFolders] = useState<AssetFolder[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [extracting, setExtracting] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'all' | 'audio' | 'video' | 'image' | 'session'>('all')
  const [loadingSession, setLoadingSession] = useState<string | null>(null)
  const [videoThumbnails, setVideoThumbnails] = useState<Map<string, string>>(new Map())
  const [tooltip, setTooltip] = useState({
    visible: false,
    text: '',
    top: 0,
    left: 0,
    placement: 'top' as 'top' | 'bottom',
  })
  const [tooltipAnchor, setTooltipAnchor] = useState<{
    top: number
    bottom: number
    left: number
    width: number
  } | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  // Search state
  const [searchQuery, setSearchQuery] = useState('')

  // Folder state
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null)
  const [editingFolderName, setEditingFolderName] = useState('')
  const [showNewFolderInput, setShowNewFolderInput] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null)
  const newFolderInputRef = useRef<HTMLInputElement>(null)
  const editFolderInputRef = useRef<HTMLInputElement>(null)

  // Asset rename state
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null)
  const [editingAssetName, setEditingAssetName] = useState('')
  const editAssetInputRef = useRef<HTMLInputElement>(null)

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

  const fetchFolders = useCallback(async () => {
    try {
      const data = await foldersApi.list(projectId)
      setFolders(data)
    } catch (error) {
      console.error('Failed to fetch folders:', error)
    }
  }, [projectId])

  useEffect(() => {
    fetchAssets()
    fetchFolders()
  }, [fetchAssets, fetchFolders])

  useLayoutEffect(() => {
    if (!tooltip.visible || !tooltipAnchor || !tooltipRef.current) return

    const tooltipRect = tooltipRef.current.getBoundingClientRect()
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    const margin = 8
    const gap = 6

    let placement: 'top' | 'bottom' = 'top'
    let top = tooltipAnchor.top - tooltipRect.height - gap
    if (top < margin) {
      placement = 'bottom'
      top = tooltipAnchor.bottom + gap
    }

    if (placement === 'bottom' && top + tooltipRect.height > viewportHeight - margin) {
      const fallbackTop = tooltipAnchor.top - tooltipRect.height - gap
      if (fallbackTop >= margin) {
        placement = 'top'
        top = fallbackTop
      } else {
        top = Math.max(margin, viewportHeight - tooltipRect.height - margin)
      }
    }

    let left = tooltipAnchor.left + tooltipAnchor.width / 2 - tooltipRect.width / 2
    left = Math.min(Math.max(left, margin), viewportWidth - tooltipRect.width - margin)

    if (top !== tooltip.top || left !== tooltip.left || placement !== tooltip.placement) {
      setTooltip((prev) => ({
        ...prev,
        top,
        left,
        placement,
      }))
    }
  }, [tooltip.visible, tooltip.text, tooltipAnchor, tooltip.top, tooltip.left, tooltip.placement])

  const showTooltip = useCallback((event: React.MouseEvent<HTMLElement>, text: string) => {
    if (!text) return
    const rect = event.currentTarget.getBoundingClientRect()
    setTooltipAnchor({
      top: rect.top,
      bottom: rect.bottom,
      left: rect.left,
      width: rect.width,
    })
    setTooltip((prev) => ({
      ...prev,
      visible: true,
      text,
    }))
  }, [])

  const hideTooltip = useCallback(() => {
    setTooltipAnchor(null)
    setTooltip((prev) => ({ ...prev, visible: false }))
  }, [])

  // Refresh assets when refreshTrigger changes (from parent component)
  useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchAssets()
    }
  }, [refreshTrigger, fetchAssets])

  // Focus new folder input when shown
  useEffect(() => {
    if (showNewFolderInput && newFolderInputRef.current) {
      newFolderInputRef.current.focus()
    }
  }, [showNewFolderInput])

  // Focus edit folder input when editing
  useEffect(() => {
    if (editingFolderId && editFolderInputRef.current) {
      editFolderInputRef.current.focus()
      editFolderInputRef.current.select()
    }
  }, [editingFolderId])

  // Focus edit asset input when editing
  useEffect(() => {
    if (editingAssetId && editAssetInputRef.current) {
      editAssetInputRef.current.focus()
      editAssetInputRef.current.select()
    }
  }, [editingAssetId])

  // Fetch thumbnails for videos that don't have one
  useEffect(() => {
    const videoAssets = assets.filter(a => a.type === 'video' && !a.thumbnail_url && !videoThumbnails.has(a.id) && !thumbnailCache.has(a.id))

    if (videoAssets.length === 0) return

    const fetchThumbnails = async () => {
      for (const asset of videoAssets) {
        try {
          // Check cache first
          if (thumbnailCache.has(asset.id)) {
            setVideoThumbnails(prev => new Map(prev).set(asset.id, thumbnailCache.get(asset.id)!))
            continue
          }

          const response = await assetsApi.getThumbnail(projectId, asset.id, 0, 64, 36)
          thumbnailCache.set(asset.id, response.url)
          setVideoThumbnails(prev => new Map(prev).set(asset.id, response.url))
        } catch (err) {
          console.error(`Failed to fetch thumbnail for ${asset.id}:`, err)
        }
      }
    }

    fetchThumbnails()
  }, [assets, projectId, videoThumbnails])

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    const duplicates: string[] = []
    const errors: string[] = []

    // Determine target folder: use the first (and typically only) expanded folder
    const expandedFolderIds = Array.from(expandedFolders)
    const targetFolderId = expandedFolderIds.length === 1 ? expandedFolderIds[0] : undefined

    try {
      for (const file of Array.from(files)) {
        try {
          await assetsApi.uploadFile(projectId, file, undefined, undefined, targetFolderId)
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

  const handleRenameAsset = async (assetId: string) => {
    if (!editingAssetName.trim()) {
      setEditingAssetId(null)
      return
    }
    try {
      const updated = await assetsApi.rename(projectId, assetId, editingAssetName.trim())
      setAssets(assets.map(a => a.id === assetId ? updated : a))
      setEditingAssetId(null)
      setEditingAssetName('')
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number } }
      if (axiosError.response?.status === 409) {
        alert('同じ名前のアセットが既に存在します')
      } else {
        console.error('Failed to rename asset:', error)
        alert('名前の変更に失敗しました')
      }
    }
  }

  const startEditingAsset = (asset: Asset) => {
    setEditingAssetId(asset.id)
    setEditingAssetName(asset.name)
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

  // Folder handlers
  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) {
      setShowNewFolderInput(false)
      return
    }
    try {
      const folder = await foldersApi.create(projectId, newFolderName.trim())
      setFolders([...folders, folder])
      setNewFolderName('')
      setShowNewFolderInput(false)
      // Auto expand newly created folder
      setExpandedFolders(prev => new Set(prev).add(folder.id))
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number } }
      if (axiosError.response?.status === 409) {
        alert('同じ名前のフォルダが既に存在します')
      } else {
        console.error('Failed to create folder:', error)
        alert('フォルダの作成に失敗しました')
      }
    }
  }

  const handleRenameFolder = async (folderId: string) => {
    if (!editingFolderName.trim()) {
      setEditingFolderId(null)
      return
    }
    try {
      const updated = await foldersApi.update(projectId, folderId, editingFolderName.trim())
      setFolders(folders.map(f => f.id === folderId ? updated : f))
      setEditingFolderId(null)
      setEditingFolderName('')
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number } }
      if (axiosError.response?.status === 409) {
        alert('同じ名前のフォルダが既に存在します')
      } else {
        console.error('Failed to rename folder:', error)
        alert('フォルダ名の変更に失敗しました')
      }
    }
  }

  const handleDeleteFolder = async (folderId: string) => {
    const folderAssets = assets.filter(a => a.folder_id === folderId)
    const confirmMsg = folderAssets.length > 0
      ? `このフォルダを削除しますか？\nフォルダ内の${folderAssets.length}件のアセットはルートに移動されます。`
      : 'このフォルダを削除しますか？'
    if (!confirm(confirmMsg)) return

    try {
      await foldersApi.delete(projectId, folderId)
      setFolders(folders.filter(f => f.id !== folderId))
      // Update assets that were in this folder
      setAssets(assets.map(a => a.folder_id === folderId ? { ...a, folder_id: null } : a))
    } catch (error) {
      console.error('Failed to delete folder:', error)
      alert('フォルダの削除に失敗しました')
    }
  }

  const toggleFolder = (folderId: string) => {
    setExpandedFolders(prev => {
      const next = new Set(prev)
      if (next.has(folderId)) {
        next.delete(folderId)
      } else {
        next.add(folderId)
      }
      return next
    })
  }

  const handleMoveAssetToFolder = async (assetId: string, folderId: string | null) => {
    try {
      const updated = await assetsApi.moveToFolder(projectId, assetId, folderId)
      setAssets(assets.map(a => a.id === assetId ? updated : a))
    } catch (error) {
      console.error('Failed to move asset:', error)
      alert('アセットの移動に失敗しました')
    }
  }

  // Asset drag handlers for folder drop
  const handleAssetDragStart = (e: React.DragEvent, asset: Asset) => {
    e.dataTransfer.setData('application/x-asset-id', asset.id)
    e.dataTransfer.setData('application/x-asset-type', asset.type)
    e.dataTransfer.setData('application/x-asset-folder-move', 'true')
    e.dataTransfer.effectAllowed = 'copyMove'
  }

  const handleFolderDragOver = (e: React.DragEvent, folderId: string | null) => {
    if (e.dataTransfer.types.includes('application/x-asset-folder-move')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setDragOverFolderId(folderId)
    }
  }

  const handleFolderDragLeave = () => {
    setDragOverFolderId(null)
  }

  const handleFolderDrop = async (e: React.DragEvent, folderId: string | null) => {
    e.preventDefault()
    setDragOverFolderId(null)

    const assetId = e.dataTransfer.getData('application/x-asset-id')
    if (!assetId) return

    const asset = assets.find(a => a.id === assetId)
    if (!asset || asset.folder_id === folderId) return

    await handleMoveAssetToFolder(assetId, folderId)
  }

  // Handle opening a session
  const handleOpenSession = async (asset: Asset) => {
    if (!onOpenSession) return
    if (loadingSession) return

    setLoadingSession(asset.id)
    try {
      const sessionData = await assetsApi.getSession(projectId, asset.id)
      onOpenSession(sessionData)
    } catch (error) {
      console.error('Failed to load session:', error)
      alert('セッションの読み込みに失敗しました')
    } finally {
      setLoadingSession(null)
    }
  }

  // Helper to get folder name by id
  const getFolderName = (folderId: string | null): string | null => {
    if (!folderId) return null
    const folder = folders.find(f => f.id === folderId)
    return folder?.name ?? null
  }

  // Filter assets by tab (session is a separate type) and search query
  const filteredAssets = (() => {
    // First filter by type
    let result = activeTab === 'all'
      ? assets.filter(a => a.type !== 'session')  // 'all' excludes sessions
      : activeTab === 'session'
      ? assets.filter(a => a.type === 'session')
      : assets.filter(a => a.type === activeTab)

    // Then filter by search query (searches all assets including those in folders)
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase().trim()
      result = result.filter(a => a.name.toLowerCase().includes(query))
    }

    return result
  })()

  // Check if search is active (used to determine display mode)
  const isSearchActive = searchQuery.trim().length > 0

  // Separate assets by folder (only when not searching)
  const rootAssets = filteredAssets.filter(a => !a.folder_id)
  const getAssetsInFolder = (folderId: string) => filteredAssets.filter(a => a.folder_id === folderId)

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

  const formatDate = (isoString: string | undefined) => {
    if (!isoString) return ''
    try {
      const date = new Date(isoString)
      return date.toLocaleDateString('ja-JP', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return ''
    }
  }

  // Special renderer for session items
  const renderSessionItem = (asset: Asset) => {
    const isLoading = loadingSession === asset.id
    const createdAt = asset.metadata?.created_at
    const appVersion = asset.metadata?.app_version

    return (
      <div
        key={asset.id}
        className={`bg-gray-700 rounded-lg p-2 cursor-pointer hover:bg-gray-600 transition-colors group ${
          isLoading ? 'opacity-70' : ''
        }`}
        onDoubleClick={() => handleOpenSession(asset)}
      >
        <div className="flex items-center gap-2">
          {/* Session Icon */}
          <div className="w-10 h-10 bg-primary-900/50 rounded flex items-center justify-center flex-shrink-0">
            {isLoading ? (
              <div className="animate-spin rounded-full h-5 w-5 border-t-2 border-b-2 border-primary-500"></div>
            ) : (
              <svg className="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            )}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            {editingAssetId === asset.id ? (
              <input
                ref={editAssetInputRef}
                type="text"
                value={editingAssetName}
                onChange={(e) => setEditingAssetName(e.target.value)}
                onBlur={() => handleRenameAsset(asset.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleRenameAsset(asset.id)
                  if (e.key === 'Escape') {
                    setEditingAssetId(null)
                    setEditingAssetName('')
                  }
                }}
                className="w-full px-1 py-0 bg-gray-600 border border-primary-500 rounded text-white text-sm focus:outline-none"
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <p
                className="text-sm text-white truncate"
                onMouseEnter={(e) => showTooltip(e, asset.name)}
                onMouseLeave={hideTooltip}
              >
                {asset.name}
              </p>
            )}
            <p className="text-xs text-gray-400">
              {createdAt && formatDate(createdAt)}
              {appVersion && ` • v${appVersion}`}
            </p>
          </div>

          {/* Rename Button */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              startEditingAsset(asset)
            }}
            className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-primary-500 transition-all"
            title="名前を変更"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>

          {/* Delete Button */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              handleDeleteAsset(asset.id)
            }}
            className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all"
            title="削除"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-1 pl-12">ダブルクリックで開く</p>
      </div>
    )
  }

  const renderAssetItem = (asset: Asset) => (
    <div
      key={asset.id}
      className="bg-gray-700 rounded-lg p-2 cursor-grab hover:bg-gray-600 transition-colors group active:cursor-grabbing relative"
      draggable
      onDragStart={(e) => handleAssetDragStart(e, asset)}
      onDoubleClick={() => onPreviewAsset?.(asset)}
    >
      <div className="flex items-center gap-2">
        {/* Thumbnail */}
        <div className="w-12 h-12 bg-gray-600 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
          {/* Image assets: show the image itself as thumbnail */}
          {asset.type === 'image' ? (
            <img
              src={asset.storage_url}
              alt={asset.name}
              className="w-full h-full object-cover"
              loading="lazy"
            />
          ) : asset.thumbnail_url ? (
            <img
              src={asset.thumbnail_url}
              alt={asset.name}
              className="w-full h-full object-cover"
            />
          ) : asset.type === 'video' && videoThumbnails.has(asset.id) ? (
            <img
              src={videoThumbnails.get(asset.id)!}
              alt={asset.name}
              className="w-full h-full object-cover"
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
          {editingAssetId === asset.id ? (
            <input
              ref={editAssetInputRef}
              type="text"
              value={editingAssetName}
              onChange={(e) => setEditingAssetName(e.target.value)}
              onBlur={() => handleRenameAsset(asset.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRenameAsset(asset.id)
                if (e.key === 'Escape') {
                  setEditingAssetId(null)
                  setEditingAssetName('')
                }
              }}
              className="w-full px-1 py-0 bg-gray-600 border border-primary-500 rounded text-white text-sm focus:outline-none"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <p
              className="text-sm text-white truncate"
              onMouseEnter={(e) => showTooltip(e, asset.name)}
              onMouseLeave={hideTooltip}
            >
              {asset.name}
            </p>
          )}
          <p className="text-xs text-gray-400">
            {formatFileSize(asset.file_size)}
            {asset.duration_ms && ` - ${formatDuration(asset.duration_ms)}`}
          </p>
          {/* Show folder path when searching or filtering by type */}
          {(isSearchActive || activeTab !== 'all') && asset.folder_id && (
            <p className="text-xs text-primary-400 truncate flex items-center gap-1">
              <svg className="w-3 h-3 text-yellow-500 flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z" />
              </svg>
              {getFolderName(asset.folder_id)}
            </p>
          )}
        </div>

        {/* Rename Button */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            startEditingAsset(asset)
          }}
          className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-primary-500 transition-all"
          title="名前を変更"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
        </button>

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
  )

  const renderFolder = (folder: AssetFolder) => {
    const isExpanded = expandedFolders.has(folder.id)
    const folderAssets = getAssetsInFolder(folder.id)
    const isDragOver = dragOverFolderId === folder.id

    return (
      <div key={folder.id} className="mb-1">
        {/* Folder header */}
        <div
          className={`flex items-center gap-1 px-2 py-1.5 rounded-lg cursor-pointer hover:bg-gray-700 transition-colors group ${
            isDragOver ? 'bg-primary-900/50 ring-1 ring-primary-500' : ''
          }`}
          onDragOver={(e) => handleFolderDragOver(e, folder.id)}
          onDragLeave={handleFolderDragLeave}
          onDrop={(e) => handleFolderDrop(e, folder.id)}
        >
          {/* Expand/collapse icon */}
          <button
            onClick={() => toggleFolder(folder.id)}
            className="p-0.5 text-gray-400 hover:text-white"
          >
            <svg
              className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>

          {/* Folder icon */}
          <svg className="w-4 h-4 text-yellow-500" fill="currentColor" viewBox="0 0 24 24">
            <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z" />
          </svg>

          {/* Folder name */}
          {editingFolderId === folder.id ? (
            <input
              ref={editFolderInputRef}
              type="text"
              value={editingFolderName}
              onChange={(e) => setEditingFolderName(e.target.value)}
              onBlur={() => handleRenameFolder(folder.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRenameFolder(folder.id)
                if (e.key === 'Escape') {
                  setEditingFolderId(null)
                  setEditingFolderName('')
                }
              }}
              className="flex-1 px-1 py-0 bg-gray-700 border border-primary-500 rounded text-white text-sm focus:outline-none"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span
              className="flex-1 text-sm text-white truncate"
              onClick={() => toggleFolder(folder.id)}
              onDoubleClick={(e) => {
                e.stopPropagation()
                setEditingFolderId(folder.id)
                setEditingFolderName(folder.name)
              }}
            >
              {folder.name}
            </span>
          )}

          {/* Asset count */}
          <span className="text-xs text-gray-500">
            {folderAssets.length}
          </span>

          {/* Folder actions */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              setEditingFolderId(folder.id)
              setEditingFolderName(folder.name)
            }}
            className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-400 hover:text-white transition-all"
            title="名前を変更"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              handleDeleteFolder(folder.id)
            }}
            className="opacity-0 group-hover:opacity-100 p-0.5 text-gray-400 hover:text-red-500 transition-all"
            title="削除"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>

        {/* Folder contents */}
        {isExpanded && (
          <div className="ml-5 mt-1 space-y-1">
            {folderAssets.length === 0 ? (
              <div className="text-xs text-gray-500 px-2 py-1">
                フォルダが空です
              </div>
            ) : (
              folderAssets.map(asset =>
                asset.type === 'session' ? renderSessionItem(asset) : renderAssetItem(asset)
              )
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-white font-medium mb-3">アセット</h2>

        {/* Tabs */}
        <div className="flex gap-1 bg-gray-900 rounded-lg p-1">
          {(['all', 'audio', 'video', 'image', 'session'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 px-2 py-1.5 text-sm rounded-md transition-colors ${
                activeTab === tab
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {tab === 'all' ? '全て' : tab === 'audio' ? '音声' : tab === 'video' ? '動画' : tab === 'image' ? '画像' : 'セッション'}
            </button>
          ))}
        </div>

        {/* Search Input */}
        <div className="mt-3 relative">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="アセットを検索..."
            className="w-full px-3 py-2 pl-9 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm placeholder-gray-400 focus:outline-none focus:border-primary-500"
          />
          <svg
            className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-white"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Upload Button & New Folder Button */}
      <div className="px-4 py-2 border-b border-gray-700 flex gap-2">
        <label className="flex-1">
          <input
            type="file"
            multiple
            accept={
              activeTab === 'audio'
                ? 'audio/*'
                : activeTab === 'video'
                ? 'video/*'
                : activeTab === 'image'
                ? 'image/*'
                : 'audio/*,video/*,image/*'
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
              {uploading ? 'アップロード中...' : 'ファイル'}
            </span>
          </span>
        </label>

        {/* New Folder Button */}
        <button
          onClick={() => setShowNewFolderInput(true)}
          className="flex items-center justify-center gap-1 px-3 py-2 border border-dashed border-gray-600 rounded-lg cursor-pointer hover:border-primary-500 hover:bg-gray-700/50 transition-colors"
          title="新しいフォルダを作成"
        >
          <svg className="w-4 h-4 text-yellow-500" fill="currentColor" viewBox="0 0 24 24">
            <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z" />
          </svg>
          <span className="text-sm text-gray-400">フォルダ</span>
        </button>
      </div>

      {/* New Folder Input */}
      {showNewFolderInput && (
        <div className="px-4 py-2 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-yellow-500 flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
              <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z" />
            </svg>
            <input
              ref={newFolderInputRef}
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onBlur={handleCreateFolder}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateFolder()
                if (e.key === 'Escape') {
                  setShowNewFolderInput(false)
                  setNewFolderName('')
                }
              }}
              placeholder="フォルダ名"
              className="flex-1 px-2 py-1 bg-gray-700 border border-primary-500 rounded text-white text-sm focus:outline-none"
            />
          </div>
        </div>
      )}

      {/* Asset List */}
      <div className="flex-1 overflow-y-auto p-2" onScroll={hideTooltip}>
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : filteredAssets.length === 0 && folders.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">
            アセットがありません
          </div>
        ) : isSearchActive || activeTab !== 'all' ? (
          /* Search mode OR type filter mode: flat list showing all matching assets with folder info */
          <div className="space-y-1">
            {filteredAssets.length === 0 ? (
              <div className="text-center py-8 text-gray-400 text-sm">
                {isSearchActive
                  ? `「${searchQuery}」に一致するアセットはありません`
                  : 'アセットがありません'}
              </div>
            ) : (
              <>
                {isSearchActive && (
                  <div className="text-xs text-gray-500 px-2 py-1">
                    {filteredAssets.length}件の検索結果
                  </div>
                )}
                {filteredAssets.map(asset =>
                  asset.type === 'session' ? renderSessionItem(asset) : renderAssetItem(asset)
                )}
              </>
            )}
          </div>
        ) : (
          <div className="space-y-1">
            {/* Folders - only show when no filter is applied (activeTab === 'all') and not searching */}
            {folders.map(folder => renderFolder(folder))}

            {/* Root assets drop zone */}
            <div
              className={`min-h-[40px] rounded-lg transition-colors ${
                dragOverFolderId === 'root' ? 'bg-primary-900/30 ring-1 ring-primary-500' : ''
              }`}
              onDragOver={(e) => handleFolderDragOver(e, null)}
              onDragLeave={handleFolderDragLeave}
              onDrop={(e) => handleFolderDrop(e, null)}
            >
              {/* Root level header when there are folders (only show when no filter applied) */}
              {folders.length > 0 && rootAssets.length > 0 && (
                <div className="text-xs text-gray-500 px-2 py-1 mb-1">
                  未分類
                </div>
              )}

              {/* Root assets */}
              <div className="space-y-1">
                {rootAssets.map(asset =>
                  asset.type === 'session' ? renderSessionItem(asset) : renderAssetItem(asset)
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {tooltip.visible && (
        <div
          ref={tooltipRef}
          className="fixed z-50 max-w-xs rounded bg-gray-900/95 px-2 py-1 text-xs text-white shadow-lg pointer-events-none break-all"
          style={{ top: tooltip.top, left: tooltip.left }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  )
}
