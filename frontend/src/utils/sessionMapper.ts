/**
 * Session asset mapping utilities
 * Maps asset references in session files to current project assets using fingerprint matching
 */

import type { Asset, SessionData, AssetReference } from '@/api/assets'

export interface AssetCandidate {
  refId: string
  refName: string
  candidates: Asset[]
  matchType: 'fingerprint' | 'partial'  // Complete match or size+duration only
}

export interface MappingResult {
  assetMap: Map<string, string>  // oldId -> newId
  unmappedAssetIds: string[]
  warnings: string[]
  pendingSelections: AssetCandidate[]  // Requires user selection
  ready: boolean  // True if pendingSelections is empty
}

/**
 * Map session asset references to current project assets.
 *
 * Mapping priority:
 * 1. ID exact match (UUID complete match)
 * 2. Fingerprint complete match (hash + file_size + duration_ms, hash must be non-null)
 * 3. Partial match (file_size + duration_ms only, when hash is null)
 * 4. Unmapped (no match found)
 *
 * IMPORTANT:
 * - hash === null values never match each other (prevents false positives)
 * - file_size/duration_ms === null values skip partial matching
 * - Multiple candidates always require user selection
 *
 * @param sessionData - The session data containing asset references
 * @param projectAssets - Current project's assets
 * @param userSelections - Optional user selections from previous call (refId -> assetId or 'skip')
 */
export function mapSessionToProject(
  sessionData: SessionData,
  projectAssets: Asset[],
  userSelections?: Map<string, string>
): MappingResult {
  const assetMap = new Map<string, string>()
  const unmapped: string[] = []
  const warnings: string[] = []
  const pendingSelections: AssetCandidate[] = []

  for (const ref of sessionData.asset_references) {
    // Check user selection from previous dialog
    if (userSelections?.has(ref.id)) {
      const selected = userSelections.get(ref.id)
      if (selected === 'skip') {
        unmapped.push(ref.id)
      } else if (selected) {
        assetMap.set(ref.id, selected)
      }
      continue
    }

    // 1. ID exact match (highest priority)
    const byId = projectAssets.find(a => a.id === ref.id)
    if (byId) {
      assetMap.set(ref.id, byId.id)
      continue
    }

    const fp = ref.fingerprint

    // 2. Fingerprint complete match (hash must be non-null)
    // IMPORTANT: null === null should NOT match
    if (fp.hash !== null) {
      const byFingerprint = projectAssets.filter(a =>
        a.hash !== null &&
        a.hash === fp.hash &&
        a.file_size === fp.file_size &&
        a.duration_ms === fp.duration_ms
      )

      if (byFingerprint.length === 1) {
        assetMap.set(ref.id, byFingerprint[0].id)
        continue
      }

      if (byFingerprint.length > 1) {
        // Multiple candidates - require user selection
        pendingSelections.push({
          refId: ref.id,
          refName: ref.name,
          candidates: byFingerprint,
          matchType: 'fingerprint',
        })
        continue
      }
    }

    // 3. Partial match (file_size + duration_ms only)
    // Only attempt if both values are non-null
    if (fp.file_size !== null && fp.duration_ms !== null) {
      const byPartial = projectAssets.filter(a =>
        a.file_size !== null &&
        a.duration_ms !== null &&
        a.file_size === fp.file_size &&
        a.duration_ms === fp.duration_ms
      )

      if (byPartial.length === 1) {
        assetMap.set(ref.id, byPartial[0].id)
        warnings.push(`"${ref.name}" はハッシュ無しで部分一致しました（サイズ+長さ）。`)
        continue
      }

      if (byPartial.length > 1) {
        // Multiple candidates - require user selection
        pendingSelections.push({
          refId: ref.id,
          refName: ref.name,
          candidates: byPartial,
          matchType: 'partial',
        })
        continue
      }
    }

    // 4. No match found
    unmapped.push(ref.id)
  }

  return {
    assetMap,
    unmappedAssetIds: unmapped,
    warnings,
    pendingSelections,
    ready: pendingSelections.length === 0,
  }
}

/**
 * Apply asset ID mapping to timeline data.
 * Replaces old asset IDs with new ones in all clips.
 *
 * @param timeline - The timeline data to update
 * @param assetMap - Map of old asset ID -> new asset ID
 * @returns Updated timeline with mapped asset IDs
 */
export function applyMappingToTimeline(
  timeline: unknown,
  assetMap: Map<string, string>
): unknown {
  // Deep clone to avoid mutations
  const updated = JSON.parse(JSON.stringify(timeline))

  // Remap video layer clips
  if (updated.layers && Array.isArray(updated.layers)) {
    for (const layer of updated.layers) {
      if (layer.clips && Array.isArray(layer.clips)) {
        for (const clip of layer.clips) {
          if (clip.asset_id && assetMap.has(clip.asset_id)) {
            clip.asset_id = assetMap.get(clip.asset_id)
          }
        }
      }
    }
  }

  // Remap audio track clips
  if (updated.audio_tracks && Array.isArray(updated.audio_tracks)) {
    for (const track of updated.audio_tracks) {
      if (track.clips && Array.isArray(track.clips)) {
        for (const clip of track.clips) {
          if (clip.asset_id && assetMap.has(clip.asset_id)) {
            clip.asset_id = assetMap.get(clip.asset_id)
          }
        }
      }
    }
  }

  return updated
}

/**
 * Extract asset IDs used in a timeline.
 *
 * @param timeline - The timeline data to extract from
 * @returns Set of asset IDs used in the timeline
 */
export function extractUsedAssetIds(timeline: unknown): Set<string> {
  const usedIds = new Set<string>()
  const t = timeline as {
    layers?: Array<{ clips?: Array<{ asset_id?: string | null }> }>
    audio_tracks?: Array<{ clips?: Array<{ asset_id?: string | null }> }>
  }

  // Video layer clips
  if (t.layers && Array.isArray(t.layers)) {
    for (const layer of t.layers) {
      if (layer.clips && Array.isArray(layer.clips)) {
        for (const clip of layer.clips) {
          if (clip.asset_id) {
            usedIds.add(clip.asset_id)
          }
        }
      }
    }
  }

  // Audio track clips
  if (t.audio_tracks && Array.isArray(t.audio_tracks)) {
    for (const track of t.audio_tracks) {
      if (track.clips && Array.isArray(track.clips)) {
        for (const clip of track.clips) {
          if (clip.asset_id) {
            usedIds.add(clip.asset_id)
          }
        }
      }
    }
  }

  return usedIds
}

/**
 * Extract asset references from timeline for session saving.
 * Creates fingerprint data for each referenced asset.
 *
 * IMPORTANT: Unknown values use null (not 0) to prevent false matches.
 *
 * @param timeline - The timeline data
 * @param projectAssets - All assets in the project
 * @returns Array of asset references with fingerprints
 */
export function extractAssetReferences(
  timeline: unknown,
  projectAssets: Asset[]
): AssetReference[] {
  const usedIds = extractUsedAssetIds(timeline)

  return projectAssets
    .filter(a => usedIds.has(a.id))
    .map(a => ({
      id: a.id,
      name: a.name,
      type: a.type,
      fingerprint: {
        hash: a.hash ?? null,
        file_size: a.file_size ?? null,
        // IMPORTANT: null for unknown, 0 is valid for images
        duration_ms: a.duration_ms ?? null,
      },
      metadata: {
        codec: null,  // Not stored in current Asset model
        width: a.width ?? null,
        height: a.height ?? null,
      },
    }))
}
