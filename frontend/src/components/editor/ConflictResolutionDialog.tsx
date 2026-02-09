import { useProjectStore } from '@/store/projectStore'

export function ConflictResolutionDialog() {
  const conflictState = useProjectStore(s => s.conflictState)
  const resolveConflict = useProjectStore(s => s.resolveConflict)

  if (!conflictState?.isConflicting) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md mx-4 shadow-xl border border-gray-700">
        <h3 className="text-lg font-semibold text-white mb-2">
          編集の競合が発生しました
        </h3>
        <p className="text-gray-300 text-sm mb-6">
          他のユーザーがこのプロジェクトを更新したため、あなたの変更を保存できませんでした。
        </p>
        <div className="flex flex-col gap-3">
          <button
            onClick={() => resolveConflict('reload')}
            className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors"
          >
            最新データを読み込む
            <span className="block text-xs text-blue-200 mt-0.5">
              サーバーの最新版に更新します（ローカルの変更は破棄されます）
            </span>
          </button>
          <button
            onClick={() => resolveConflict('force')}
            className="w-full px-4 py-2.5 bg-gray-600 hover:bg-gray-500 text-white rounded-md text-sm font-medium transition-colors"
          >
            強制保存
            <span className="block text-xs text-gray-300 mt-0.5">
              あなたの変更で上書きします（他のユーザーの変更は失われます）
            </span>
          </button>
        </div>
      </div>
    </div>
  )
}
