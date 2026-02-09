import { useState } from 'react'

export type SyncResumeAction = 'load_remote' | 'apply_diff' | 'overwrite_remote'

interface SyncResumeDialogProps {
  remoteOpCount: number
  onAction: (action: SyncResumeAction) => void
  onCancel: () => void
}

export function SyncResumeDialog({ remoteOpCount, onAction, onCancel }: SyncResumeDialogProps) {
  const [loading, setLoading] = useState(false)

  const handleAction = (action: SyncResumeAction) => {
    setLoading(true)
    onAction(action)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md mx-4 shadow-xl border border-gray-700">
        <h3 className="text-lg font-semibold text-white mb-2">
          Sync再開
        </h3>
        <p className="text-gray-300 text-sm mb-1">
          Sync停止中に <span className="text-yellow-400 font-medium">{remoteOpCount}件</span> のリモート変更がありました。
        </p>
        <p className="text-gray-400 text-xs mb-6">
          どのように同期を再開しますか？
        </p>
        <div className="flex flex-col gap-3">
          <button
            onClick={() => handleAction('load_remote')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            サーバーの最新状態をロード
            <span className="block text-xs text-blue-200 mt-0.5">
              ローカルの未保存の変更は破棄されます
            </span>
          </button>
          <button
            onClick={() => handleAction('apply_diff')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-gray-600 hover:bg-gray-500 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            差分を適用して統合
            <span className="block text-xs text-gray-300 mt-0.5">
              リモートの変更をローカルに差分適用します（競合の可能性あり）
            </span>
          </button>
          <button
            onClick={() => handleAction('overwrite_remote')}
            disabled={loading}
            className="w-full px-4 py-2.5 bg-red-700/80 hover:bg-red-700 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors text-left"
          >
            ローカルで上書き保存
            <span className="block text-xs text-red-200 mt-0.5">
              ローカルの状態でサーバーを上書きします（他ユーザーの変更は失われます）
            </span>
          </button>
          <button
            onClick={onCancel}
            disabled={loading}
            className="w-full px-4 py-2 text-gray-400 hover:text-white text-sm transition-colors"
          >
            キャンセル（Syncは無効のまま）
          </button>
        </div>
      </div>
    </div>
  )
}
