import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type RefObject, type WheelEvent as ReactWheelEvent } from 'react'

interface UsePreviewViewportParams {
  initialPreviewHeight: number
  initialPreviewZoom: number
}

interface UsePreviewViewportResult {
  effectivePreviewHeight: number
  effectivePreviewWidth: number
  handlePreviewMouseLeave: () => void
  handlePreviewMouseMove: () => void
  handlePreviewPanStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  handlePreviewWheel: (event: ReactWheelEvent<HTMLDivElement>) => void
  handlePreviewZoomFit: () => void
  handlePreviewZoomIn: () => void
  handlePreviewZoomOut: () => void
  handleResizeStart: (event: ReactMouseEvent<HTMLElement>) => void
  isPanningPreview: boolean
  isResizing: boolean
  previewAreaRef: RefObject<HTMLDivElement>
  previewContainerRef: RefObject<HTMLDivElement>
  previewHeight: number
  previewPan: { x: number; y: number }
  previewResizeSnap: boolean
  previewZoom: number
  recenterPreview: () => void
  showPreviewControls: boolean
  togglePreviewResizeSnap: () => void
}

export function usePreviewViewport({
  initialPreviewHeight,
  initialPreviewZoom,
}: UsePreviewViewportParams): UsePreviewViewportResult {
  const [previewHeight, setPreviewHeight] = useState(initialPreviewHeight)
  const [isResizing, setIsResizing] = useState(false)
  const [previewResizeSnap, setPreviewResizeSnap] = useState(true)
  const [previewZoom, setPreviewZoom] = useState(initialPreviewZoom)
  const [previewPan, setPreviewPan] = useState({ x: 0, y: 0 })
  const [isPanningPreview, setIsPanningPreview] = useState(false)
  const [showPreviewControls, setShowPreviewControls] = useState(false)
  const [previewAreaHeight, setPreviewAreaHeight] = useState(-1)
  const [previewAreaWidth, setPreviewAreaWidth] = useState(-1)

  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 })
  const previewControlsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const resizeStartY = useRef(0)
  const resizeStartHeight = useRef(0)
  const previewContainerRef = useRef<HTMLDivElement>(null!)
  const previewAreaRef = useRef<HTMLDivElement>(null!)

  const recenterPreview = useCallback(() => {
    const el = previewAreaRef.current
    if (!el) return
    el.scrollTop = Math.max(0, (el.scrollHeight - el.clientHeight) / 2)
    el.scrollLeft = Math.max(0, (el.scrollWidth - el.clientWidth) / 2)
  }, [])

  const clearPreviewControlsTimer = useCallback(() => {
    if (previewControlsTimerRef.current) {
      clearTimeout(previewControlsTimerRef.current)
      previewControlsTimerRef.current = null
    }
  }, [])

  const handlePreviewMouseMove = useCallback(() => {
    setShowPreviewControls(true)
    clearPreviewControlsTimer()
    previewControlsTimerRef.current = setTimeout(() => {
      setShowPreviewControls(false)
      previewControlsTimerRef.current = null
    }, 2000)
  }, [clearPreviewControlsTimer])

  const handlePreviewMouseLeave = useCallback(() => {
    clearPreviewControlsTimer()
    setShowPreviewControls(false)
  }, [clearPreviewControlsTimer])

  useEffect(() => {
    return () => {
      clearPreviewControlsTimer()
    }
  }, [clearPreviewControlsTimer])

  useEffect(() => {
    const element = previewAreaRef.current
    if (!element) return

    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      if (rect.height > 0) setPreviewAreaHeight(rect.height)
      if (rect.width > 0) setPreviewAreaWidth(rect.width)
      requestAnimationFrame(() => recenterPreview())
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [recenterPreview])

  const handleResizeStart = useCallback((event: ReactMouseEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setIsResizing(true)
    resizeStartY.current = event.clientY
    resizeStartHeight.current = previewHeight
  }, [previewHeight])

  useEffect(() => {
    if (!isResizing) return

    const handleMouseMove = (event: MouseEvent) => {
      const deltaY = event.clientY - resizeStartY.current
      const maxHeight = Math.floor(window.innerHeight * 0.9)
      let nextHeight = Math.max(150, Math.min(maxHeight, resizeStartHeight.current + deltaY))
      if (previewResizeSnap) {
        nextHeight = Math.round(nextHeight / 50) * 50
      }
      setPreviewHeight(nextHeight)
    }

    const handleMouseUp = () => {
      setIsResizing(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizing, previewResizeSnap])

  const handlePreviewZoomIn = useCallback(() => {
    setPreviewZoom((prev) => {
      const target = prev * 1.25
      if (prev < 1 && target > 1) return 1
      return Math.min(4, target)
    })
    requestAnimationFrame(() => recenterPreview())
  }, [recenterPreview])

  const handlePreviewZoomOut = useCallback(() => {
    setPreviewZoom((prev) => {
      const target = prev * 0.8
      if (prev > 1 && target < 1) return 1
      return Math.max(0.25, target)
    })
    requestAnimationFrame(() => recenterPreview())
  }, [recenterPreview])

  const handlePreviewZoomFit = useCallback(() => {
    setPreviewZoom(1)
    setPreviewPan({ x: 0, y: 0 })
    requestAnimationFrame(() => recenterPreview())
  }, [recenterPreview])

  const handlePreviewWheel = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()

    const zoomFactor = event.deltaY < 0 ? 1.1 : 0.9
    setPreviewZoom((prev) => {
      const nextZoom = prev * zoomFactor
      if ((prev < 1 && nextZoom > 1) || (prev > 1 && nextZoom < 1)) return 1
      return Math.max(0.25, Math.min(4, nextZoom))
    })
  }, [])

  const handlePreviewPanStart = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.button !== 1 && !event.altKey) return
    event.preventDefault()
    setIsPanningPreview(true)
    panStartRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: previewPan.x,
      panY: previewPan.y,
    }
  }, [previewPan])

  useEffect(() => {
    if (!isPanningPreview) return

    const handleMouseMove = (event: MouseEvent) => {
      const deltaX = event.clientX - panStartRef.current.x
      const deltaY = event.clientY - panStartRef.current.y
      setPreviewPan({
        x: panStartRef.current.panX + deltaX,
        y: panStartRef.current.panY + deltaY,
      })
    }

    const handleMouseUp = () => {
      setIsPanningPreview(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isPanningPreview])

  useEffect(() => {
    if (previewZoom <= 1) {
      setPreviewPan({ x: 0, y: 0 })
    }
  }, [previewZoom])

  const togglePreviewResizeSnap = useCallback(() => {
    setPreviewResizeSnap((prev) => !prev)
  }, [])

  useEffect(() => {
    recenterPreview()
  }, [recenterPreview])

  return {
    effectivePreviewHeight: previewAreaHeight > 0 ? previewAreaHeight : Math.max(previewHeight - 104, 50),
    effectivePreviewWidth: previewAreaWidth > 0 ? previewAreaWidth : 800,
    handlePreviewMouseLeave,
    handlePreviewMouseMove,
    handlePreviewPanStart,
    handlePreviewWheel,
    handlePreviewZoomFit,
    handlePreviewZoomIn,
    handlePreviewZoomOut,
    handleResizeStart,
    isPanningPreview,
    isResizing,
    previewAreaRef,
    previewContainerRef,
    previewHeight,
    previewPan,
    previewResizeSnap,
    previewZoom,
    recenterPreview,
    showPreviewControls,
    togglePreviewResizeSnap,
  }
}
