import { useEffect, useState, useCallback, useRef, useLayoutEffect, useMemo, memo } from 'react'
import { assetsApi, foldersApi, type Asset, type AssetFolder, type SessionData } from '@/api/assets'
import { RequestPriority, withPriority } from '@/utils/requestPriority'
import AudioWaveformThumbnail from './AudioWaveformThumbnail'

// Lazy video thumbnail component with IntersectionObserver
// Only fetches thumbnail when visible (for assets without thumbnail_url)
interface LazyVideoThumbnailProps {
  projectId: string
  assetId: string
  assetName: string
  size: number
}

const LazyVideoThumbnail = memo(function LazyVideoThumbnail({
  projectId,
  assetId,
  assetName,
  size
}: LazyVideoThumbnailProps) {
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(() =>
    thumbnailCache.get(assetId) || null
  )
  const [isLoading, setIsLoading] = useState(false)
  const [hasError, setHasError] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const fetchedRef = useRef(false)

  useEffect(() => {
    // Already have thumbnail or already fetched
    if (thumbnailUrl || fetchedRef.current) return

    const element = containerRef.current
    if (!element) return

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0]
        if (entry.isIntersecting && !fetchedRef.current) {
          fetchedRef.current = true
          observer.disconnect()

          // Fetch thumbnail with low priority
          setIsLoading(true)
          withPriority(RequestPriority.LOW, async () => {
            try {
              const response = await assetsApi.getThumbnail(projectId, assetId, 0, 64, 36)
              thumbnailCache.set(assetId, response.url)
              setThumbnailUrl(response.url)
            } catch (err) {
              console.error(`Failed to fetch thumbnail for ${assetId}:`, err)
              setHasError(true)
            } finally {
              setIsLoading(false)
            }
          })
        }
      },
      {
        rootMargin: '50px', // Start loading slightly before visible
        threshold: 0
      }
    )

    observer.observe(element)
    return () => observer.disconnect()
  }, [projectId, assetId, thumbnailUrl])

  return (
    <div ref={containerRef} className="w-full h-full flex items-center justify-center">
      {thumbnailUrl ? (
        <img
          src={thumbnailUrl}
          alt={assetName}
          className="w-full h-full object-cover"
        />
      ) : isLoading ? (
        <div className="animate-pulse bg-gray-500/30 w-full h-full" />
      ) : hasError ? (
        <svg
          className={`${size <= 32 ? 'w-4 h-4' : 'w-5 h-5'} text-gray-400`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
      ) : (
        // Placeholder while waiting for intersection
        <div className="bg-gray-600 w-full h-full flex items-center justify-center">
          <svg
            className={`${size <= 32 ? 'w-4 h-4' : 'w-5 h-5'} text-gray-400`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        </div>
      )}
    </div>
  )
})

// Sort types
type SortBy = 'name' | 'type' | 'created_at'
type SortOrder = 'asc' | 'desc'
type FilterType = 'all' | 'audio' | 'video' | 'image' | 'session'
type ViewMode = 'list' | 'compact'

// Cache for video thumbnails
const thumbnailCache = new Map<string, string>()

// localStorage key for view preferences
const ASSET_VIEW_PREFS_KEY = 'douga-asset-view-prefs'

interface ViewPrefs {
  viewMode: ViewMode
  sortBy: SortBy
  sortOrder: SortOrder
}

function loadViewPrefs(): ViewPrefs {
  try {
    const stored = localStorage.getItem(ASSET_VIEW_PREFS_KEY)
    if (stored) {
      return JSON.parse(stored)
    }
  } catch {
    // Ignore parse errors
  }
  return { viewMode: 'list', sortBy: 'created_at', sortOrder: 'desc' }
}

function saveViewPrefs(prefs: ViewPrefs): void {
  try {
    localStorage.setItem(ASSET_VIEW_PREFS_KEY, JSON.stringify(prefs))
  } catch {
    // Ignore storage errors
  }
}

interface AssetLibraryProps {
  projectId: string
  onPreviewAsset?: (asset: Asset) => void
  onAssetsChange?: () => void
  onOpenSession?: (sessionData: SessionData, sessionId?: string, sessionName?: string) => void  // Called when user opens a session
  refreshTrigger?: number  // Increment this to force a refresh of the asset list
}

export default function AssetLibrary({ projectId, onPreviewAsset, onAssetsChange, onOpenSession, refreshTrigger }: AssetLibraryProps) {
  const [assets, setAssets] = useState<Asset[]>([])
  const [folders, setFolders] = useState<AssetFolder[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [converting, setConverting] = useState(false)
  const [extracting, setExtracting] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<FilterType>('all')
  const [loadingSession, setLoadingSession] = useState<string | null>(null)
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
  const [isSearchFocused, setIsSearchFocused] = useState(false)

  // View preferences (persisted)
  const savedPrefs = loadViewPrefs()
  const [viewMode, setViewMode] = useState<ViewMode>(savedPrefs.viewMode)
  const [sortBy, setSortBy] = useState<SortBy>(savedPrefs.sortBy)
  const [sortOrder, setSortOrder] = useState<SortOrder>(savedPrefs.sortOrder)
  const [showSortOptions, setShowSortOptions] = useState(false)

  // Folder state
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null)
  const [editingFolderName, setEditingFolderName] = useState('')
  const [showNewFolderInput, setShowNewFolderInput] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null)
  const newFolderInputRef = useRef<HTMLInputElement>(null)
  const editFolderInputRef = useRef<HTMLInputElement>(null)

  // Multi-select state
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set())
  const lastClickedAssetIdRef = useRef<string | null>(null)

  // Asset rename state
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null)
  const [editingAssetName, setEditingAssetName] = useState('')
  const editAssetInputRef = useRef<HTMLInputElement>(null)

  // File drop upload state
  const [isDraggingFiles, setIsDraggingFiles] = useState(false)
  const [dropTargetFolderId, setDropTargetFolderId] = useState<string | null | undefined>(undefined)
  const dragCounterRef = useRef(0)

  // Dropdown refs
  const filterDropdownRef = useRef<HTMLDivElement>(null)
  const sortDropdownRef = useRef<HTMLDivElement>(null)
  const [showFilterDropdown, setShowFilterDropdown] = useState(false)

  // Save view preferences when they change
  useEffect(() => {
    saveViewPrefs({ viewMode, sortBy, sortOrder })
  }, [viewMode, sortBy, sortOrder])

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (filterDropdownRef.current && !filterDropdownRef.current.contains(e.target as Node)) {
        setShowFilterDropdown(false)
      }
      if (sortDropdownRef.current && !sortDropdownRef.current.contains(e.target as Node)) {
        setShowSortOptions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

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

  // Listen for global asset-changed events (reliable cross-component refresh)
  useEffect(() => {
    const handler = () => fetchAssets()
    window.addEventListener('douga-assets-changed', handler)
    return () => window.removeEventListener('douga-assets-changed', handler)
  }, [fetchAssets])

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

  // Refresh assets when refreshTrigger changes
  useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      fetchAssets()
    }
  }, [refreshTrigger, fetchAssets])

  // Focus inputs when shown
  useEffect(() => {
    if (showNewFolderInput && newFolderInputRef.current) {
      newFolderInputRef.current.focus()
    }
  }, [showNewFolderInput])

  useEffect(() => {
    if (editingFolderId && editFolderInputRef.current) {
      editFolderInputRef.current.focus()
      editFolderInputRef.current.select()
    }
  }, [editingFolderId])

  useEffect(() => {
    if (editingAssetId && editAssetInputRef.current) {
      editAssetInputRef.current.focus()
      editAssetInputRef.current.select()
    }
  }, [editingAssetId])

  // Shared upload logic for both file input and drag & drop
  const uploadFiles = async (files: FileList | File[], targetFolderId?: string | null) => {
    if (files.length === 0) return

    // Check if any files are HEIC/HEIF
    const hasHeicFiles = Array.from(files).some(file => {
      const name = file.name.toLowerCase()
      return name.endsWith('.heic') || name.endsWith('.heif') ||
             file.type === 'image/heic' || file.type === 'image/heif'
    })

    if (hasHeicFiles) {
      setConverting(true)
    }
    setUploading(true)
    const duplicates: string[] = []
    const errors: string[] = []
    const unsupported: string[] = []

    // Supported MIME types
    const supportedTypes = ['video/', 'audio/', 'image/']
    const fileArray = Array.from(files)

    try {
      for (const file of fileArray) {
        // Check if file type is supported
        const isSupported = supportedTypes.some(type => file.type.startsWith(type))
        if (!isSupported) {
          unsupported.push(file.name)
          continue
        }

        try {
          await assetsApi.uploadFile(projectId, file, undefined, undefined, targetFolderId)
        } catch (error: unknown) {
          const axiosError = error as { response?: { status?: number }; message?: string }
          if (axiosError.response?.status === 409) {
            duplicates.push(file.name)
          } else if (error instanceof Error && error.message.includes('HEIC変換')) {
            errors.push(`${file.name} (HEIC変換失敗)`)
          } else {
            errors.push(file.name)
          }
        }
      }

      if (unsupported.length > 0) {
        alert(`以下のファイル形式は対応していません（video/audio/imageのみ）:\n${unsupported.join('\n')}`)
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
      setConverting(false)
    }
  }

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files
    if (!files || files.length === 0) return

    const expandedFolderIds = Array.from(expandedFolders)
    const targetFolderId = expandedFolderIds.length === 1 ? expandedFolderIds[0] : undefined

    await uploadFiles(files, targetFolderId)
    event.target.value = ''
  }

  // File drag & drop handlers for upload
  const isFileDrag = (e: React.DragEvent): boolean => {
    // Check if the drag contains files from the file system
    return e.dataTransfer.types.includes('Files') &&
      !e.dataTransfer.types.includes('application/x-asset-folder-move')
  }

  const handleFileDragEnter = (e: React.DragEvent, folderId?: string | null) => {
    e.preventDefault()
    e.stopPropagation()

    if (!isFileDrag(e)) return

    dragCounterRef.current++
    setIsDraggingFiles(true)
    if (folderId !== undefined) {
      setDropTargetFolderId(folderId)
    }
  }

  const handleFileDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (!isFileDrag(e)) return

    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setIsDraggingFiles(false)
      setDropTargetFolderId(undefined)
    }
  }

  const handleFileDragOver = (e: React.DragEvent, folderId?: string | null) => {
    e.preventDefault()
    e.stopPropagation()

    if (!isFileDrag(e)) return

    e.dataTransfer.dropEffect = 'copy'
    if (folderId !== undefined) {
      setDropTargetFolderId(folderId)
    }
  }

  const handleFileDrop = async (e: React.DragEvent, folderId?: string | null) => {
    e.preventDefault()
    e.stopPropagation()

    // Reset drag state
    dragCounterRef.current = 0
    setIsDraggingFiles(false)
    setDropTargetFolderId(undefined)

    // Check if this is a file drop (not an internal asset move)
    if (!isFileDrag(e)) return

    const files = e.dataTransfer.files
    if (files.length === 0) return

    await uploadFiles(files, folderId)
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
      setActiveFilter('audio')
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

  // Move multiple assets to a folder
  const handleMoveAssetsToFolder = async (assetIds: string[], folderId: string | null) => {
    try {
      const results = await Promise.all(
        assetIds.map(id => assetsApi.moveToFolder(projectId, id, folderId))
      )
      setAssets(prev => {
        const updatedMap = new Map(results.map(a => [a.id, a]))
        return prev.map(a => updatedMap.get(a.id) ?? a)
      })
      // Clear selection after move
      setSelectedAssetIds(new Set())
    } catch (error) {
      console.error('Failed to move assets:', error)
      alert('アセットの移動に失敗しました')
    }
  }

  // Multi-select click handler
  const handleAssetClick = (e: React.MouseEvent, asset: Asset, visibleAssets: Asset[]) => {
    // Don't interfere with editing
    if (editingAssetId) return

    const isMetaKey = e.metaKey || e.ctrlKey
    const isShiftKey = e.shiftKey

    if (isMetaKey) {
      // Cmd/Ctrl+Click: toggle individual selection
      setSelectedAssetIds(prev => {
        const next = new Set(prev)
        if (next.has(asset.id)) {
          next.delete(asset.id)
        } else {
          next.add(asset.id)
        }
        return next
      })
      lastClickedAssetIdRef.current = asset.id
    } else if (isShiftKey && lastClickedAssetIdRef.current) {
      // Shift+Click: range selection
      const lastIdx = visibleAssets.findIndex(a => a.id === lastClickedAssetIdRef.current)
      const currentIdx = visibleAssets.findIndex(a => a.id === asset.id)
      if (lastIdx !== -1 && currentIdx !== -1) {
        const start = Math.min(lastIdx, currentIdx)
        const end = Math.max(lastIdx, currentIdx)
        const rangeIds = visibleAssets.slice(start, end + 1).map(a => a.id)
        setSelectedAssetIds(prev => {
          const next = new Set(prev)
          for (const id of rangeIds) {
            next.add(id)
          }
          return next
        })
      }
    } else {
      // Plain click: clear selection (let normal behavior happen)
      setSelectedAssetIds(new Set())
      lastClickedAssetIdRef.current = asset.id
    }
  }

  // Clear selection when clicking empty areas
  const handleBackgroundClick = (e: React.MouseEvent) => {
    // Only clear if clicking directly on the container, not on an asset
    if (e.target === e.currentTarget) {
      setSelectedAssetIds(new Set())
    }
  }

  // Asset drag handlers for folder drop
  const handleAssetDragStart = (e: React.DragEvent, asset: Asset) => {
    // If the dragged asset is part of a multi-selection, drag all selected
    // Otherwise, drag only this single asset
    let dragIds: string[]
    if (selectedAssetIds.has(asset.id) && selectedAssetIds.size > 1) {
      dragIds = Array.from(selectedAssetIds)
    } else {
      dragIds = [asset.id]
    }
    e.dataTransfer.setData('application/x-asset-id', asset.id)
    e.dataTransfer.setData('application/x-asset-ids', JSON.stringify(dragIds))
    e.dataTransfer.setData('application/x-asset-type', asset.type)
    e.dataTransfer.setData('application/x-asset-folder-move', 'true')
    e.dataTransfer.effectAllowed = 'copyMove'

    // Custom drag image showing count when multiple selected
    if (dragIds.length > 1) {
      const dragEl = document.createElement('div')
      dragEl.style.cssText = 'position:absolute;top:-9999px;left:-9999px;padding:4px 12px;background:#3b82f6;color:white;border-radius:6px;font-size:13px;font-weight:500;white-space:nowrap;'
      dragEl.textContent = `${dragIds.length}件のアセット`
      document.body.appendChild(dragEl)
      e.dataTransfer.setDragImage(dragEl, 0, 0)
      requestAnimationFrame(() => document.body.removeChild(dragEl))
    }
  }

  const handleFolderDragOver = (e: React.DragEvent, folderId: string | null) => {
    if (e.dataTransfer.types.includes('application/x-asset-folder-move') || e.dataTransfer.types.includes('application/x-asset-ids')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setDragOverFolderId(folderId ?? 'root')
    }
  }

  const handleFolderDragLeave = () => {
    setDragOverFolderId(null)
  }

  const handleFolderDrop = async (e: React.DragEvent, folderId: string | null) => {
    e.preventDefault()
    setDragOverFolderId(null)

    // Try to get multiple asset IDs first (bulk move)
    const assetIdsJson = e.dataTransfer.getData('application/x-asset-ids')
    if (assetIdsJson) {
      try {
        const assetIds = JSON.parse(assetIdsJson) as string[]
        // Filter out assets already in the target folder
        const idsToMove = assetIds.filter(id => {
          const asset = assets.find(a => a.id === id)
          return asset && asset.folder_id !== folderId
        })
        if (idsToMove.length === 0) return

        if (idsToMove.length === 1) {
          await handleMoveAssetToFolder(idsToMove[0], folderId)
        } else {
          await handleMoveAssetsToFolder(idsToMove, folderId)
        }
        return
      } catch {
        // Fall through to single asset handling
      }
    }

    // Fallback: single asset (backward compatibility)
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
      // Pass session ID and name along with the data
      onOpenSession(sessionData, asset.id, asset.name)
    } catch (error) {
      console.error('Failed to load session:', error)
      alert('セクションの読み込みに失敗しました')
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

  // Sort function
  const sortAssets = useCallback((assetsToSort: Asset[]): Asset[] => {
    return [...assetsToSort].sort((a, b) => {
      let compare = 0
      switch (sortBy) {
        case 'name':
          compare = a.name.localeCompare(b.name, 'ja')
          break
        case 'type':
          compare = a.type.localeCompare(b.type)
          break
        case 'created_at':
          compare = new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
          break
      }
      return sortOrder === 'asc' ? compare : -compare
    })
  }, [sortBy, sortOrder])

  // Filter and sort assets
  const filteredAssets = useMemo(() => {
    let result = activeFilter === 'all'
      ? assets.filter(a => a.type !== 'session')
      : activeFilter === 'session'
      ? assets.filter(a => a.type === 'session')
      : assets.filter(a => a.type === activeFilter)

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase().trim()
      result = result.filter(a => a.name.toLowerCase().includes(query))
    }

    return sortAssets(result)
  }, [assets, activeFilter, searchQuery, sortAssets])

  const isSearchActive = searchQuery.trim().length > 0
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

  // Filter label
  const filterLabels: Record<FilterType, string> = {
    all: '全て',
    audio: '音声',
    video: '動画',
    image: '画像',
    session: 'セクション',
  }

  // Type icon for filter dropdown
  const getTypeIcon = (type: FilterType) => {
    switch (type) {
      case 'audio':
        return (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
          </svg>
        )
      case 'video':
        return (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        )
      case 'image':
        return (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        )
      case 'session':
        return (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        )
      default:
        return (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
        )
    }
  }

  // Session item renderer (compact)
  const renderSessionItem = (asset: Asset, isCompact: boolean) => {
    const isLoading = loadingSession === asset.id
    const createdAt = asset.metadata?.created_at
    const appVersion = asset.metadata?.app_version

    return (
      <div
        key={asset.id}
        className={`bg-gray-700/50 rounded ${isCompact ? 'p-1.5' : 'p-2'} cursor-pointer hover:bg-gray-600/50 transition-colors group`}
        onDoubleClick={() => handleOpenSession(asset)}
      >
        <div className="flex items-center gap-2">
          {/* Session Icon */}
          <div className={`${isCompact ? 'w-7 h-7' : 'w-9 h-9'} bg-primary-900/50 rounded flex items-center justify-center flex-shrink-0`}>
            {isLoading ? (
              <div className={`animate-spin rounded-full ${isCompact ? 'h-3 w-3' : 'h-4 w-4'} border-t-2 border-b-2 border-primary-500`}></div>
            ) : (
              <svg className={`${isCompact ? 'w-3.5 h-3.5' : 'w-4 h-4'} text-primary-400`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
                  // IME変換中はEnterを無視
                  if (e.nativeEvent.isComposing || e.key === 'Process') return
                  if (e.key === 'Enter') handleRenameAsset(asset.id)
                  if (e.key === 'Escape') {
                    setEditingAssetId(null)
                    setEditingAssetName('')
                  }
                }}
                className="w-full px-1 py-0 bg-gray-600 border border-primary-500 rounded text-white text-xs focus:outline-none"
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <p
                className={`${isCompact ? 'text-xs' : 'text-sm'} text-white truncate`}
                onMouseEnter={(e) => showTooltip(e, asset.name)}
                onMouseLeave={hideTooltip}
              >
                {asset.name}
              </p>
            )}
            {!isCompact && (
              <p className="text-xs text-gray-400">
                {createdAt && formatDate(createdAt)}
                {appVersion && ` v${appVersion}`}
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={(e) => {
                e.stopPropagation()
                startEditingAsset(asset)
              }}
              className="p-1 text-gray-400 hover:text-primary-400"
              title="名前を変更"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleDeleteAsset(asset.id)
              }}
              className="p-1 text-gray-400 hover:text-red-400"
              title="削除"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Asset item renderer
  const renderAssetItem = (asset: Asset, isCompact: boolean, visibleAssets: Asset[] = []) => {
    const isSelected = selectedAssetIds.has(asset.id)
    return (
    <div
      key={asset.id}
      className={`bg-gray-700/50 rounded ${isCompact ? 'p-1.5' : 'p-2'} cursor-grab hover:bg-gray-600/50 transition-colors group active:cursor-grabbing ${
        isSelected ? 'ring-2 ring-blue-500 bg-blue-900/30' : ''
      }`}
      draggable
      onDragStart={(e) => handleAssetDragStart(e, asset)}
      onClick={(e) => handleAssetClick(e, asset, visibleAssets)}
      onDoubleClick={() => onPreviewAsset?.(asset)}
    >
      <div className="flex items-center gap-2">
        {/* Thumbnail */}
        <div className={`${isCompact ? 'w-8 h-8' : 'w-10 h-10'} bg-gray-600 rounded flex items-center justify-center flex-shrink-0 overflow-hidden relative`}>
          {isSelected && (
            <div className="absolute top-0 left-0 w-4 h-4 bg-blue-500 rounded-br-sm flex items-center justify-center z-10">
              <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
          )}
          {asset.type === 'image' ? (
            <img
              src={asset.storage_url}
              alt={asset.name}
              className="w-full h-full object-cover"
              loading="lazy"
            />
          ) : asset.type === 'audio' ? (
            <AudioWaveformThumbnail
              projectId={projectId}
              assetId={asset.id}
              width={isCompact ? 32 : 40}
              height={isCompact ? 32 : 40}
              color="#22c55e"
              backgroundColor="#4b5563"
            />
          ) : asset.thumbnail_url ? (
            // Video with thumbnail_url from API (new uploads)
            <img
              src={asset.thumbnail_url}
              alt={asset.name}
              className="w-full h-full object-cover"
            />
          ) : asset.type === 'video' ? (
            // Video without thumbnail_url (old assets) - lazy load on visibility
            <LazyVideoThumbnail
              projectId={projectId}
              assetId={asset.id}
              assetName={asset.name}
              size={isCompact ? 32 : 40}
            />
          ) : (
            // Other types (fallback)
            <svg
              className={`${isCompact ? 'w-4 h-4' : 'w-5 h-5'} text-gray-400`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
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
                // IME変換中はEnterを無視
                if (e.nativeEvent.isComposing || e.key === 'Process') return
                if (e.key === 'Enter') handleRenameAsset(asset.id)
                if (e.key === 'Escape') {
                  setEditingAssetId(null)
                  setEditingAssetName('')
                }
              }}
              className="w-full px-1 py-0 bg-gray-600 border border-primary-500 rounded text-white text-xs focus:outline-none"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <p
              className={`${isCompact ? 'text-xs' : 'text-sm'} text-white truncate`}
              onMouseEnter={(e) => showTooltip(e, asset.name)}
              onMouseLeave={hideTooltip}
            >
              {asset.name}
            </p>
          )}
          {!isCompact && (
            <p className="text-xs text-gray-400">
              {formatFileSize(asset.file_size)}
              {asset.duration_ms && ` ${formatDuration(asset.duration_ms)}`}
            </p>
          )}
          {(isSearchActive || activeFilter !== 'all') && asset.folder_id && (
            <p className="text-xs text-primary-400/70 truncate flex items-center gap-0.5">
              <svg className="w-2.5 h-2.5 text-yellow-500/70 flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z" />
              </svg>
              <span className="truncate">{getFolderName(asset.folder_id)}</span>
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => {
              e.stopPropagation()
              startEditingAsset(asset)
            }}
            className="p-1 text-gray-400 hover:text-primary-400"
            title="名前を変更"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>

          {asset.type === 'video' && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleExtractAudio(asset.id)
              }}
              disabled={extracting === asset.id}
              className="p-1 text-gray-400 hover:text-primary-400 disabled:opacity-50"
              title="音声を抽出"
            >
              {extracting === asset.id ? (
                <div className="animate-spin rounded-full h-3.5 w-3.5 border-t-2 border-b-2 border-primary-500"></div>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                </svg>
              )}
            </button>
          )}

          <button
            onClick={(e) => {
              e.stopPropagation()
              handleDeleteAsset(asset.id)
            }}
            className="p-1 text-gray-400 hover:text-red-400"
            title="削除"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
  }

  // Folder renderer
  const renderFolder = (folder: AssetFolder, isCompact: boolean) => {
    const isExpanded = expandedFolders.has(folder.id)
    const folderAssets = getAssetsInFolder(folder.id)
    const isDragOver = dragOverFolderId === folder.id
    const isFileDropTarget = isDraggingFiles && dropTargetFolderId === folder.id

    return (
      <div key={folder.id} className="mb-0.5">
        {/* Folder header */}
        <div
          className={`flex items-center gap-1 px-1.5 py-1 rounded cursor-pointer hover:bg-gray-700/50 transition-colors group ${
            isDragOver || isFileDropTarget ? 'bg-primary-900/30 ring-1 ring-primary-500' : ''
          }`}
          onDragOver={(e) => {
            handleFolderDragOver(e, folder.id)
            handleFileDragOver(e, folder.id)
          }}
          onDragEnter={(e) => handleFileDragEnter(e, folder.id)}
          onDragLeave={(e) => {
            handleFolderDragLeave()
            handleFileDragLeave(e)
          }}
          onDrop={(e) => {
            // Handle file drop first, then internal asset move
            if (isFileDrag(e)) {
              handleFileDrop(e, folder.id)
            } else {
              handleFolderDrop(e, folder.id)
            }
          }}
        >
          {/* Expand/collapse icon */}
          <button
            onClick={() => toggleFolder(folder.id)}
            className="p-0.5 text-gray-400 hover:text-white"
          >
            <svg
              className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>

          {/* Folder icon */}
          <svg className={`${isExpanded ? 'text-yellow-400' : 'text-yellow-500/70'} w-4 h-4`} fill="currentColor" viewBox="0 0 24 24">
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
                // IME変換中はEnterを無視
                if (e.nativeEvent.isComposing || e.key === 'Process') return
                if (e.key === 'Enter') handleRenameFolder(folder.id)
                if (e.key === 'Escape') {
                  setEditingFolderId(null)
                  setEditingFolderName('')
                }
              }}
              className="flex-1 px-1 py-0 bg-gray-700 border border-primary-500 rounded text-white text-xs focus:outline-none"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span
              className="flex-1 text-xs text-white truncate"
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
          <span className="text-xs text-gray-500 tabular-nums">
            {folderAssets.length}
          </span>

          {/* Folder actions */}
          <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={(e) => {
                e.stopPropagation()
                setEditingFolderId(folder.id)
                setEditingFolderName(folder.name)
              }}
              className="p-0.5 text-gray-400 hover:text-white"
              title="名前を変更"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                handleDeleteFolder(folder.id)
              }}
              className="p-0.5 text-gray-400 hover:text-red-400"
              title="削除"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>

        {/* Folder contents */}
        {isExpanded && (
          <div className="ml-4 mt-0.5 space-y-0.5">
            {folderAssets.length === 0 ? (
              <div className="text-xs text-gray-500 px-2 py-1 italic">
                空のフォルダ
              </div>
            ) : (
              <>
                {folderAssets.map(asset =>
                  asset.type === 'session' ? renderSessionItem(asset, isCompact) : renderAssetItem(asset, isCompact, folderAssets)
                )}
                {/* Move selected assets out of folder */}
                {selectedAssetIds.size > 0 && [...selectedAssetIds].some(id => folderAssets.find(a => a.id === id)) && (
                  <button
                    onClick={() => {
                      const idsInFolder = [...selectedAssetIds].filter(id => folderAssets.find(a => a.id === id))
                      if (idsInFolder.length > 0) handleMoveAssetsToFolder(idsInFolder, null)
                    }}
                    className="w-full text-xs text-gray-400 hover:text-white hover:bg-gray-700/50 px-2 py-1 rounded flex items-center gap-1 mt-1"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                    </svg>
                    選択中のアセットをルートに移動
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div
      className={`h-full flex flex-col bg-gray-800 relative ${
        isDraggingFiles ? 'ring-2 ring-primary-500 ring-inset' : ''
      }`}
      onDragEnter={(e) => handleFileDragEnter(e)}
      onDragLeave={handleFileDragLeave}
      onDragOver={(e) => handleFileDragOver(e)}
      onDrop={(e) => handleFileDrop(e, null)}
    >
      {/* Drag overlay */}
      {isDraggingFiles && (
        <div className="absolute inset-0 bg-primary-900/20 z-10 pointer-events-none flex items-center justify-center">
          <div className="bg-gray-800/95 border-2 border-dashed border-primary-500 rounded-lg px-6 py-4 text-center">
            <svg className="w-8 h-8 mx-auto text-primary-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <p className="text-primary-300 text-sm font-medium">
              {dropTargetFolderId !== undefined && dropTargetFolderId !== null
                ? `フォルダにドロップしてアップロード`
                : 'ファイルをドロップしてアップロード'}
            </p>
            <p className="text-gray-400 text-xs mt-1">
              動画・音声・画像ファイル対応
            </p>
          </div>
        </div>
      )}
      {/* Compact Header */}
      <div className="px-3 py-2 border-b border-gray-700/50">
        {/* Title row with upload button */}
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-medium text-white">アセット</h2>
          <div className="flex items-center gap-1">
            {/* View mode toggle */}
            <button
              onClick={() => setViewMode(viewMode === 'list' ? 'compact' : 'list')}
              className={`p-1.5 rounded hover:bg-gray-700 transition-colors ${viewMode === 'compact' ? 'text-primary-400' : 'text-gray-400'}`}
              title={viewMode === 'list' ? 'コンパクト表示' : 'リスト表示'}
            >
              {viewMode === 'compact' ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                </svg>
              )}
            </button>

            {/* New folder */}
            <button
              onClick={() => setShowNewFolderInput(true)}
              className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-yellow-400 transition-colors"
              title="新規フォルダ"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z" />
              </svg>
            </button>

            {/* Upload */}
            <label className="cursor-pointer">
              <input
                type="file"
                multiple
                accept={
                  activeFilter === 'audio'
                    ? 'audio/*'
                    : activeFilter === 'video'
                    ? 'video/*'
                    : activeFilter === 'image'
                    ? 'image/*,.heic,.heif'
                    : 'audio/*,video/*,image/*,.heic,.heif'
                }
                onChange={handleFileUpload}
                className="hidden"
                disabled={uploading || converting}
              />
              <span
                className={`flex items-center gap-1 px-2 py-1 rounded bg-primary-600 hover:bg-primary-500 text-white text-xs transition-colors ${
                  (uploading || converting) ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              >
                {converting ? (
                  <>
                    <div className="animate-spin rounded-full h-3 w-3 border-t-2 border-b-2 border-white"></div>
                    <span>変換中...</span>
                  </>
                ) : uploading ? (
                  <>
                    <div className="animate-spin rounded-full h-3 w-3 border-t-2 border-b-2 border-white"></div>
                    <span>追加</span>
                  </>
                ) : (
                  <>
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    <span>追加</span>
                  </>
                )}
              </span>
            </label>
          </div>
        </div>

        {/* Search and Filter row */}
        <div className="flex items-center gap-2">
          {/* Search */}
          <div className="flex-1 relative">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setIsSearchFocused(true)}
              onBlur={() => setIsSearchFocused(false)}
              placeholder="検索..."
              className={`w-full pl-7 pr-7 py-1.5 bg-gray-700/50 border rounded text-xs text-white placeholder-gray-500 focus:outline-none transition-colors ${
                isSearchFocused ? 'border-primary-500' : 'border-transparent'
              }`}
            />
            <svg
              className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>

          {/* Filter dropdown */}
          <div className="relative" ref={filterDropdownRef}>
            <button
              onClick={() => setShowFilterDropdown(!showFilterDropdown)}
              className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs transition-colors ${
                activeFilter !== 'all'
                  ? 'bg-primary-600/30 text-primary-300 border border-primary-500/50'
                  : 'bg-gray-700/50 text-gray-300 border border-transparent hover:bg-gray-700'
              }`}
            >
              {getTypeIcon(activeFilter)}
              <span className="hidden sm:inline">{filterLabels[activeFilter]}</span>
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showFilterDropdown && (
              <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-20 py-1 min-w-[120px]">
                {(['all', 'video', 'audio', 'image', 'session'] as FilterType[]).map((filter) => (
                  <button
                    key={filter}
                    onClick={() => {
                      setActiveFilter(filter)
                      setShowFilterDropdown(false)
                    }}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-gray-700 transition-colors ${
                      activeFilter === filter ? 'text-primary-400' : 'text-gray-300'
                    }`}
                  >
                    {getTypeIcon(filter)}
                    <span>{filterLabels[filter]}</span>
                    {activeFilter === filter && (
                      <svg className="w-3 h-3 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Sort dropdown */}
          <div className="relative" ref={sortDropdownRef}>
            <button
              onClick={() => setShowSortOptions(!showSortOptions)}
              className="p-1.5 rounded bg-gray-700/50 text-gray-400 hover:text-white hover:bg-gray-700 transition-colors"
              title="並べ替え"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                {sortOrder === 'asc' ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4h13M3 8h9m-9 4h9m5-4v12m0 0l-4-4m4 4l4-4" />
                )}
              </svg>
            </button>
            {showSortOptions && (
              <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-20 py-1 min-w-[140px]">
                <div className="px-2 py-1 text-xs text-gray-500 border-b border-gray-700">並べ替え</div>
                {([
                  { value: 'created_at', label: '作成日時' },
                  { value: 'name', label: '名前' },
                  { value: 'type', label: '種類' },
                ] as { value: SortBy; label: string }[]).map((option) => (
                  <button
                    key={option.value}
                    onClick={() => {
                      if (sortBy === option.value) {
                        setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
                      } else {
                        setSortBy(option.value)
                      }
                    }}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-gray-700 transition-colors ${
                      sortBy === option.value ? 'text-primary-400' : 'text-gray-300'
                    }`}
                  >
                    <span>{option.label}</span>
                    {sortBy === option.value && (
                      <svg className="w-3 h-3 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        {sortOrder === 'asc' ? (
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                        ) : (
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        )}
                      </svg>
                    )}
                  </button>
                ))}
                <div className="border-t border-gray-700 mt-1 pt-1">
                  <button
                    onClick={() => {
                      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
                      setShowSortOptions(false)
                    }}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 transition-colors"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                    </svg>
                    <span>{sortOrder === 'asc' ? '降順に切替' : '昇順に切替'}</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* New Folder Input */}
      {showNewFolderInput && (
        <div className="px-3 py-2 border-b border-gray-700/50 bg-gray-700/30">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-yellow-400 flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
              <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z" />
            </svg>
            <input
              ref={newFolderInputRef}
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onBlur={handleCreateFolder}
              onKeyDown={(e) => {
                // IME変換中はEnterを無視
                if (e.nativeEvent.isComposing || e.key === 'Process') return
                if (e.key === 'Enter') handleCreateFolder()
                if (e.key === 'Escape') {
                  setShowNewFolderInput(false)
                  setNewFolderName('')
                }
              }}
              placeholder="フォルダ名を入力"
              className="flex-1 px-2 py-1 bg-gray-700 border border-primary-500 rounded text-white text-xs focus:outline-none"
            />
          </div>
        </div>
      )}

      {/* Selection count bar */}
      {selectedAssetIds.size > 0 && (
        <div className="px-3 py-1.5 bg-blue-900/40 border-b border-blue-700/50 flex items-center justify-between">
          <span className="text-xs text-blue-300">
            {selectedAssetIds.size}件を選択中
          </span>
          <button
            onClick={() => setSelectedAssetIds(new Set())}
            className="text-xs text-blue-400 hover:text-blue-200 transition-colors"
          >
            選択解除
          </button>
        </div>
      )}

      {/* Asset List */}
      <div className="flex-1 overflow-y-auto px-2 py-1" onScroll={hideTooltip} onClick={handleBackgroundClick}>
        {loading ? (
          <div className="flex justify-center py-8">
            <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-primary-500"></div>
          </div>
        ) : filteredAssets.length === 0 && folders.length === 0 ? (
          <div className="text-center py-8">
            <svg className="w-12 h-12 mx-auto text-gray-600 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <p className="text-gray-500 text-sm">アセットがありません</p>
            <p className="text-gray-600 text-xs mt-1">ファイルをアップロードしてください</p>
          </div>
        ) : isSearchActive || activeFilter !== 'all' ? (
          /* Search/filter mode: flat list */
          <div className="space-y-0.5">
            {filteredAssets.length === 0 ? (
              <div className="text-center py-6">
                <p className="text-gray-500 text-sm">
                  {isSearchActive
                    ? `"${searchQuery}" に一致するアセットがありません`
                    : `${filterLabels[activeFilter]}がありません`}
                </p>
              </div>
            ) : (
              <>
                {isSearchActive && (
                  <div className="text-xs text-gray-500 px-1 py-1">
                    {filteredAssets.length}件
                  </div>
                )}
                {filteredAssets.map(asset =>
                  asset.type === 'session' ? renderSessionItem(asset, viewMode === 'compact') : renderAssetItem(asset, viewMode === 'compact', filteredAssets)
                )}
              </>
            )}
          </div>
        ) : (
          <div className="space-y-0.5">
            {/* Folders */}
            {folders.map(folder => renderFolder(folder, viewMode === 'compact'))}

            {/* Root assets drop zone */}
            <div
              className={`min-h-[40px] rounded transition-colors ${
                dragOverFolderId === 'root' ? 'bg-primary-900/30 ring-2 ring-primary-500 ring-dashed' : ''
              }`}
              onDragOver={(e) => {
                handleFolderDragOver(e, null)
                handleFileDragOver(e, null)
              }}
              onDragEnter={(e) => handleFileDragEnter(e, null)}
              onDragLeave={(e) => {
                handleFolderDragLeave()
                handleFileDragLeave(e)
              }}
              onDrop={(e) => {
                if (isFileDrag(e)) {
                  handleFileDrop(e, null)
                } else {
                  handleFolderDrop(e, null)
                }
              }}
            >
              {/* Root level header when there are folders */}
              {folders.length > 0 && rootAssets.length > 0 && (
                <div className="text-xs text-gray-500 px-1 py-1 flex items-center gap-1">
                  <span className="w-3 h-px bg-gray-600"></span>
                  <span>未分類</span>
                  <span className="flex-1 h-px bg-gray-600"></span>
                </div>
              )}

              {/* Root assets */}
              <div className="space-y-0.5">
                {rootAssets.map(asset =>
                  asset.type === 'session' ? renderSessionItem(asset, viewMode === 'compact') : renderAssetItem(asset, viewMode === 'compact', rootAssets)
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Tooltip */}
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
