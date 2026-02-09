import type { PresenceUser } from '@/hooks/useProjectPresence'

interface PresenceIndicatorProps {
  users: PresenceUser[]
}

export function PresenceIndicator({ users }: PresenceIndicatorProps) {
  if (users.length === 0) return null

  return (
    <div className="flex items-center gap-1">
      {users.map((user) => (
        <div
          key={user.userId}
          className="relative group"
          title={user.displayName}
        >
          {user.photoURL ? (
            <img
              src={user.photoURL}
              alt={user.displayName}
              className="w-7 h-7 rounded-full border-2 border-green-500 object-cover"
            />
          ) : (
            <div className="w-7 h-7 rounded-full border-2 border-green-500 bg-gray-600 flex items-center justify-center text-xs font-medium text-white">
              {user.displayName.charAt(0).toUpperCase()}
            </div>
          )}
          <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 bg-green-500 rounded-full border border-gray-800" />
          <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 px-2 py-1 bg-gray-900 text-white text-xs rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
            {user.displayName}
          </div>
        </div>
      ))}
    </div>
  )
}
