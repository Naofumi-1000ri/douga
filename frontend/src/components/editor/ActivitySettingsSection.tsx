import { useState, useEffect } from 'react'
import { useActivityStore } from '@/store/activityStore'

export default function ActivitySettingsSection() {
  const { settings, setSettings } = useActivityStore()
  const [localUserName, setLocalUserName] = useState(settings.userName)
  const [localAIName, setLocalAIName] = useState(settings.aiName)

  // Sync local state when settings change from outside
  useEffect(() => {
    setLocalUserName(settings.userName)
    setLocalAIName(settings.aiName)
  }, [settings.userName, settings.aiName])

  const handleUserNameBlur = () => {
    const trimmed = localUserName.trim()
    if (trimmed && trimmed !== settings.userName) {
      setSettings({ userName: trimmed })
    } else if (!trimmed) {
      setLocalUserName(settings.userName)
    }
  }

  const handleAINameBlur = () => {
    const trimmed = localAIName.trim()
    if (trimmed && trimmed !== settings.aiName) {
      setSettings({ aiName: trimmed })
    } else if (!trimmed) {
      setLocalAIName(settings.aiName)
    }
  }

  return (
    <div className="mb-4 pt-4 border-t border-gray-700">
      <label className="block text-sm text-gray-400 mb-2">Activity Panel Settings</label>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 w-20">User Name:</span>
          <input
            type="text"
            value={localUserName}
            onChange={(e) => setLocalUserName(e.target.value)}
            onBlur={handleUserNameBlur}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.currentTarget.blur()
              }
            }}
            placeholder="User"
            className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 w-20">AI Name:</span>
          <input
            type="text"
            value={localAIName}
            onChange={(e) => setLocalAIName(e.target.value)}
            onBlur={handleAINameBlur}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.currentTarget.blur()
              }
            }}
            placeholder="AI"
            className="flex-1 px-2 py-1 bg-gray-700 text-white text-sm rounded border border-gray-600 focus:border-primary-500 focus:outline-none"
          />
        </div>
      </div>
      <p className="text-xs text-gray-500 mt-1">Activity Panel displays the operation log</p>
    </div>
  )
}
