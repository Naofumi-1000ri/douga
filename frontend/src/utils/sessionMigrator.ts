/**
 * Session file migration utilities
 * Handles schema version upgrades for session files
 */

import type { SessionData, AssetReference, Fingerprint } from '@/api/assets'

export const CURRENT_SCHEMA_VERSION = '1.0'

export interface MigrationResult {
  data: SessionData
  migrated: boolean
  warnings: string[]
}

interface LegacyAssetReference {
  id: string
  name: string
  type: string
  file_size?: number | null
  duration_ms?: number | null
  fingerprint?: Fingerprint
  metadata?: Record<string, unknown>
}

/**
 * Migrate session data from older schema versions to the current version.
 *
 * IMPORTANT: Unknown values use null (not 0) to prevent false matches.
 * - 0 is a valid duration_ms for images
 * - null indicates "unknown" and should not auto-match
 */
export function migrateSession(rawData: unknown): MigrationResult {
  const data = rawData as SessionData
  const warnings: string[] = []

  // Get schema version, default to "0.9" for legacy files without version
  const version = data.schema_version ?? '0.9'

  // No migration needed for current version
  if (version === CURRENT_SCHEMA_VERSION) {
    return { data, migrated: false, warnings }
  }

  // 0.9 → 1.0 migration: Convert flat structure to fingerprint structure
  if (version === '0.9') {
    data.asset_references = data.asset_references.map((ref: unknown) => {
      const legacyRef = ref as LegacyAssetReference

      // Already has fingerprint structure - skip
      if (legacyRef.fingerprint) {
        return ref as AssetReference
      }

      // Convert legacy format to new fingerprint structure
      // IMPORTANT: Unknown values use null, not 0
      const newRef: AssetReference = {
        id: legacyRef.id,
        name: legacyRef.name,
        type: legacyRef.type,
        fingerprint: {
          hash: null,  // Legacy files don't have hash
          file_size: legacyRef.file_size ?? null,  // null if unknown
          duration_ms: legacyRef.duration_ms ?? null,  // null if unknown (0 is valid for images)
        },
        metadata: legacyRef.metadata ? {
          codec: (legacyRef.metadata.codec as string) ?? null,
          width: (legacyRef.metadata.width as number) ?? null,
          height: (legacyRef.metadata.height as number) ?? null,
        } : null,
      }

      return newRef
    })

    warnings.push('古い形式のセッションです。一部のアセットは手動選択が必要になる場合があります。')
  }

  // Update schema version
  data.schema_version = CURRENT_SCHEMA_VERSION

  return { data, migrated: true, warnings }
}
