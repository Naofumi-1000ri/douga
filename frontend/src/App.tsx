import React, { useEffect, useState } from 'react'
import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import Dashboard from '@/pages/Dashboard'
import Editor from '@/pages/Editor'
import Login from '@/pages/Login'
import { sequencesApi } from '@/api/sequences'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { user, loading, isDevMode } = useAuthStore()

  // In dev mode, always allow access (bypass auth check)
  if (isDevMode) {
    return <>{children}</>
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500"></div>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function ProjectRedirect() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId) return
    sequencesApi.getDefault(projectId)
      .then(({ id }) => {
        navigate(`/project/${projectId}/sequence/${id}`, { replace: true })
      })
      .catch((err) => {
        console.error('Failed to get default sequence:', err)
        setError('デフォルトシーケンスの取得に失敗しました')
      })
  }, [projectId, navigate])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center text-red-400">
        {error}
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500"></div>
    </div>
  )
}

function App() {
  // Prevent browser navigating away when files are dropped outside our drop zones.
  useEffect(() => {
    const handleDragOver = (e: DragEvent) => {
      // Only guard top-level drags; inner handlers can still stopPropagation if needed.
      e.preventDefault()
    }
    const handleDrop = (e: DragEvent) => {
      // Avoid accidental navigation/reload when dropping media onto the page background.
      e.preventDefault()
    }
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('drop', handleDrop)
    return () => {
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('drop', handleDrop)
    }
  }, [])

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <PrivateRoute>
            <Dashboard />
          </PrivateRoute>
        }
      />
      <Route
        path="/project/:projectId"
        element={
          <PrivateRoute>
            <ProjectRedirect />
          </PrivateRoute>
        }
      />
      <Route
        path="/project/:projectId/sequence/:sequenceId"
        element={
          <PrivateRoute>
            <Editor />
          </PrivateRoute>
        }
      />
    </Routes>
  )
}

export default App
