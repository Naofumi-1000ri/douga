import { createContext, useContext, useState, ReactNode } from 'react'
import type { Asset } from '@/api/assets'

interface DragData {
  asset: Asset | null
  isDragging: boolean
}

interface DndContextType {
  dragData: DragData
  startDrag: (asset: Asset) => void
  endDrag: () => void
}

const DndContext = createContext<DndContextType | null>(null)

export function DndProvider({ children }: { children: ReactNode }) {
  const [dragData, setDragData] = useState<DragData>({
    asset: null,
    isDragging: false,
  })

  const startDrag = (asset: Asset) => {
    setDragData({ asset, isDragging: true })
  }

  const endDrag = () => {
    setDragData({ asset: null, isDragging: false })
  }

  return (
    <DndContext.Provider value={{ dragData, startDrag, endDrag }}>
      {children}
    </DndContext.Provider>
  )
}

export function useDnd() {
  const context = useContext(DndContext)
  if (!context) {
    throw new Error('useDnd must be used within a DndProvider')
  }
  return context
}
