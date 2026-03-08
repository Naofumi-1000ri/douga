import type { Page, Route } from '@playwright/test'
import type { Asset, AssetFolder } from '../../src/api/assets'
import type { OperationHistoryItem } from '../../src/api/operations'
import type { SequenceDetail, SequenceListItem, SnapshotItem } from '../../src/api/sequences'
import type { Project, ProjectDetail, TimelineData } from '../../src/store/projectStore'

const FIXED_NOW = '2026-03-07T00:00:00.000Z'

const MOCK_IMAGE_URL = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect width="1280" height="720" fill="#0f172a"/>
  <rect x="80" y="80" width="1120" height="560" rx="36" fill="#2563eb"/>
  <circle cx="240" cy="220" r="72" fill="#38bdf8"/>
  <rect x="360" y="180" width="520" height="84" rx="16" fill="#dbeafe"/>
  <rect x="360" y="300" width="380" height="48" rx="12" fill="#bfdbfe"/>
  <rect x="360" y="384" width="260" height="48" rx="12" fill="#93c5fd"/>
  <rect x="900" y="180" width="180" height="180" rx="24" fill="#1d4ed8"/>
  <text x="360" y="510" font-family="sans-serif" font-size="58" font-weight="700" fill="#eff6ff">Mock Preview Asset</text>
</svg>
`)}`

type MockLayoutSettings = {
  activityPanelWidth: number
  aiPanelWidth: number
  isAIChatOpen: boolean
  isAssetPanelOpen: boolean
  isPropertyPanelOpen: boolean
  isSyncEnabled: boolean
  leftPanelWidth: number
  playheadPosition: number
  previewHeight: number
  previewZoom: number
  rightPanelWidth: number
}

export interface MockEditorApiState {
  assetsByProject: Record<string, Asset[]>
  calls: {
    projectCreates: string[]
    sequenceUpdates: Array<{
      projectId: string
      sequenceId: string
      timelineData: TimelineData
      version: number
    }>
  }
  defaultSequenceByProject: Record<string, string>
  foldersByProject: Record<string, AssetFolder[]>
  operationHistoryByProject: Record<string, OperationHistoryItem[]>
  primaryAssetId: string
  projectDetails: Record<string, ProjectDetail>
  projectId: string
  projects: Project[]
  projectSequences: Record<string, string[]>
  sequenceId: string
  sequences: Record<string, SequenceDetail>
  snapshotsBySequence: Record<string, SnapshotItem[]>
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function buildTimeline(): TimelineData {
  return {
    version: '1.0',
    duration_ms: 0,
    layers: [
      {
        id: 'layer-1',
        name: 'Layer 1',
        type: 'content',
        order: 0,
        visible: true,
        locked: false,
        clips: [],
      },
    ],
    audio_tracks: [],
    groups: [],
    markers: [],
  }
}

function buildProject(id: string, name: string, durationMs: number): Project {
  return {
    id,
    name,
    description: null,
    status: 'active',
    duration_ms: durationMs,
    thumbnail_url: null,
    created_at: FIXED_NOW,
    updated_at: FIXED_NOW,
    is_shared: false,
    role: 'owner',
    owner_name: 'Dev User',
  }
}

function buildProjectDetail(id: string, name: string, timelineData: TimelineData, version: number): ProjectDetail {
  return {
    ...buildProject(id, name, timelineData.duration_ms),
    user_id: 'dev-user-123',
    width: 1280,
    height: 720,
    fps: 30,
    timeline_data: clone(timelineData),
    ai_provider: 'openai',
    ai_api_key: null,
    version,
  }
}

function buildSequence(projectId: string, sequenceId: string, name: string, timelineData: TimelineData, version: number): SequenceDetail {
  return {
    id: sequenceId,
    project_id: projectId,
    name,
    timeline_data: clone(timelineData),
    version,
    duration_ms: timelineData.duration_ms,
    is_default: true,
    locked_by: null,
    lock_holder_name: null,
    locked_at: null,
    created_at: FIXED_NOW,
    updated_at: FIXED_NOW,
  }
}

function buildAsset(projectId: string): Asset {
  return {
    id: 'asset-image-1',
    project_id: projectId,
    name: 'Mock Hero',
    type: 'image',
    subtype: 'mock',
    storage_key: 'mock/hero.svg',
    storage_url: MOCK_IMAGE_URL,
    thumbnail_url: null,
    duration_ms: null,
    width: 1280,
    height: 720,
    file_size: MOCK_IMAGE_URL.length,
    mime_type: 'image/svg+xml',
    chroma_key_color: null,
    hash: null,
    folder_id: null,
    created_at: FIXED_NOW,
    metadata: null,
  }
}

export function createMockEditorApiState(): MockEditorApiState {
  const projectId = 'project-seeded'
  const sequenceId = 'sequence-seeded'
  const timelineData = buildTimeline()
  const project = buildProject(projectId, 'Seeded Project', timelineData.duration_ms)
  const projectDetail = buildProjectDetail(projectId, project.name, timelineData, 1)
  const sequence = buildSequence(projectId, sequenceId, 'Main Sequence', timelineData, 1)
  const asset = buildAsset(projectId)

  return {
    assetsByProject: {
      [projectId]: [asset],
    },
    calls: {
      projectCreates: [],
      sequenceUpdates: [],
    },
    defaultSequenceByProject: {
      [projectId]: sequenceId,
    },
    foldersByProject: {
      [projectId]: [],
    },
    operationHistoryByProject: {
      [projectId]: [],
    },
    primaryAssetId: asset.id,
    projectDetails: {
      [projectId]: projectDetail,
    },
    projectId,
    projects: [project],
    projectSequences: {
      [projectId]: [sequenceId],
    },
    sequenceId,
    sequences: {
      [sequenceId]: sequence,
    },
    snapshotsBySequence: {
      [sequenceId]: [],
    },
  }
}

function syncProjectFromSequence(state: MockEditorApiState, projectId: string, sequenceId: string) {
  const sequence = state.sequences[sequenceId]
  const projectDetail = state.projectDetails[projectId]
  if (!sequence || !projectDetail) return

  projectDetail.timeline_data = clone(sequence.timeline_data)
  projectDetail.duration_ms = sequence.duration_ms
  projectDetail.version = sequence.version
  projectDetail.updated_at = sequence.updated_at

  const project = state.projects.find((candidate) => candidate.id === projectId)
  if (project) {
    project.duration_ms = sequence.duration_ms
    project.updated_at = sequence.updated_at
  }
}

function listSequences(state: MockEditorApiState, projectId: string): SequenceListItem[] {
  return (state.projectSequences[projectId] ?? [])
    .map((sequenceId) => state.sequences[sequenceId])
    .filter((sequence): sequence is SequenceDetail => Boolean(sequence))
    .map((sequence) => ({
      id: sequence.id,
      name: sequence.name,
      version: sequence.version,
      duration_ms: sequence.duration_ms,
      is_default: sequence.is_default,
      locked_by: sequence.locked_by,
      lock_holder_name: sequence.lock_holder_name,
      thumbnail_url: null,
      created_at: sequence.created_at,
      updated_at: sequence.updated_at,
    }))
}

function createProjectFromRequest(state: MockEditorApiState, name: string): Project {
  const nextIndex = state.projects.length + 1
  const projectId = `project-created-${nextIndex}`
  const sequenceId = `sequence-created-${nextIndex}`
  const timelineData = buildTimeline()
  const project = buildProject(projectId, name, timelineData.duration_ms)
  const projectDetail = buildProjectDetail(projectId, name, timelineData, 1)
  const sequence = buildSequence(projectId, sequenceId, 'Main Sequence', timelineData, 1)

  state.projects.unshift(project)
  state.projectDetails[projectId] = projectDetail
  state.sequences[sequenceId] = sequence
  state.projectSequences[projectId] = [sequenceId]
  state.defaultSequenceByProject[projectId] = sequenceId
  state.assetsByProject[projectId] = []
  state.foldersByProject[projectId] = []
  state.operationHistoryByProject[projectId] = []
  state.snapshotsBySequence[sequenceId] = []
  state.calls.projectCreates.push(projectId)

  return project
}

function json(route: Route, data: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  })
}

function text(route: Route, body: string, status = 200) {
  return route.fulfill({
    status,
    contentType: 'text/plain',
    body,
  })
}

function matches(pathname: string, pattern: RegExp) {
  return pathname.match(pattern)
}

export async function bootstrapMockEditorPage(
  page: Page,
  options?: {
    layout?: Partial<MockLayoutSettings>
  }
): Promise<MockEditorApiState> {
  const defaultLayout: MockLayoutSettings = {
    activityPanelWidth: 320,
    aiPanelWidth: 320,
    isAIChatOpen: false,
    isAssetPanelOpen: true,
    isPropertyPanelOpen: true,
    isSyncEnabled: true,
    leftPanelWidth: 288,
    playheadPosition: 0,
    previewHeight: 360,
    previewZoom: 1,
    rightPanelWidth: 288,
  }

  await page.addInitScript((layout) => {
    localStorage.setItem('douga-editor-layout', JSON.stringify(layout))
  }, { ...defaultLayout, ...options?.layout })

  const state = createMockEditorApiState()

  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname
    const method = request.method()

    if (!pathname.startsWith('/api/')) {
      return route.continue()
    }

    if (pathname === '/api/members/invitations' && method === 'GET') {
      return json(route, [])
    }

    if (pathname === '/api/projects' && method === 'GET') {
      return json(route, clone(state.projects))
    }

    if (pathname === '/api/projects' && method === 'POST') {
      const body = request.postDataJSON() as { name?: string }
      const project = createProjectFromRequest(state, body.name?.trim() || 'Untitled Project')
      return json(route, clone(project), 201)
    }

    const projectMatch = matches(pathname, /^\/api\/projects\/([^/]+)$/)
    if (projectMatch && method === 'GET') {
      const [, projectId] = projectMatch
      const project = state.projectDetails[projectId]
      if (!project) return text(route, 'Not Found', 404)
      return json(route, clone(project))
    }

    const projectDefaultSequenceMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/default$/)
    if (projectDefaultSequenceMatch && method === 'GET') {
      const [, projectId] = projectDefaultSequenceMatch
      const sequenceId = state.defaultSequenceByProject[projectId]
      if (!sequenceId) return text(route, 'Not Found', 404)
      return json(route, { id: sequenceId })
    }

    const projectSequencesMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences$/)
    if (projectSequencesMatch && method === 'GET') {
      const [, projectId] = projectSequencesMatch
      return json(route, listSequences(state, projectId))
    }

    if (projectSequencesMatch && method === 'POST') {
      const [, projectId] = projectSequencesMatch
      const body = request.postDataJSON() as { name?: string }
      const nextIndex = (state.projectSequences[projectId]?.length ?? 0) + 1
      const sequenceId = `${projectId}-sequence-${nextIndex}`
      const timelineData = buildTimeline()
      const sequence = buildSequence(projectId, sequenceId, body.name?.trim() || `Sequence ${nextIndex}`, timelineData, 1)
      sequence.is_default = false
      state.sequences[sequenceId] = sequence
      state.projectSequences[projectId] = [...(state.projectSequences[projectId] ?? []), sequenceId]
      state.snapshotsBySequence[sequenceId] = []
      return json(route, clone(sequence), 201)
    }

    const sequenceDetailMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/([^/]+)$/)
    if (sequenceDetailMatch && method === 'GET') {
      const [, , sequenceId] = sequenceDetailMatch
      const sequence = state.sequences[sequenceId]
      if (!sequence) return text(route, 'Not Found', 404)
      return json(route, clone(sequence))
    }

    if (sequenceDetailMatch && method === 'PUT') {
      const [, projectId, sequenceId] = sequenceDetailMatch
      const body = request.postDataJSON() as { timeline_data: TimelineData; version: number }
      const sequence = state.sequences[sequenceId]
      if (!sequence) return text(route, 'Not Found', 404)

      sequence.timeline_data = clone(body.timeline_data)
      sequence.duration_ms = body.timeline_data.duration_ms
      sequence.version += 1
      sequence.updated_at = new Date().toISOString()

      state.calls.sequenceUpdates.push({
        projectId,
        sequenceId,
        timelineData: clone(body.timeline_data),
        version: sequence.version,
      })

      syncProjectFromSequence(state, projectId, sequenceId)
      return json(route, clone(sequence))
    }

    const sequenceLockMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/([^/]+)\/(lock|heartbeat)$/)
    if (sequenceLockMatch && method === 'POST') {
      return json(route, {
        locked: true,
        locked_by: 'dev-user-123',
        lock_holder_name: 'Dev User',
        locked_at: FIXED_NOW,
        edit_token: 'mock-edit-token',
      })
    }

    const sequenceUnlockMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/([^/]+)\/unlock$/)
    if (sequenceUnlockMatch && method === 'POST') {
      return json(route, {})
    }

    const sequenceThumbnailMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/([^/]+)\/thumbnail$/)
    if (sequenceThumbnailMatch && method === 'POST') {
      return json(route, { thumbnail_url: MOCK_IMAGE_URL })
    }

    const sequenceSnapshotsMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/sequences\/([^/]+)\/snapshots$/)
    if (sequenceSnapshotsMatch && method === 'GET') {
      const [, , sequenceId] = sequenceSnapshotsMatch
      return json(route, clone(state.snapshotsBySequence[sequenceId] ?? []))
    }

    const assetsMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/assets$/)
    if (assetsMatch && method === 'GET') {
      const [, projectId] = assetsMatch
      return json(route, clone(state.assetsByProject[projectId] ?? []))
    }

    const foldersMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/folders$/)
    if (foldersMatch && method === 'GET') {
      const [, projectId] = foldersMatch
      return json(route, clone(state.foldersByProject[projectId] ?? []))
    }

    const signedUrlMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/assets\/([^/]+)\/signed-url$/)
    if (signedUrlMatch && method === 'GET') {
      const [, projectId, assetId] = signedUrlMatch
      const asset = (state.assetsByProject[projectId] ?? []).find((candidate) => candidate.id === assetId)
      if (!asset) return text(route, 'Not Found', 404)
      return json(route, { url: asset.storage_url, expires_in_seconds: 3600 })
    }

    const operationsMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/operations$/)
    if (operationsMatch && method === 'GET') {
      const [, projectId] = operationsMatch
      const currentVersion = state.projectDetails[projectId]?.version ?? 1
      const operations = state.operationHistoryByProject[projectId] ?? []
      return json(route, { current_version: currentVersion, operations })
    }

    const projectThumbnailMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/thumbnail$/)
    if (projectThumbnailMatch && method === 'POST') {
      return json(route, { thumbnail_url: MOCK_IMAGE_URL })
    }

    const renderStatusMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/render\/status$/)
    if (renderStatusMatch && method === 'GET') {
      return json(route, null)
    }

    const renderHistoryMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/render\/history$/)
    if (renderHistoryMatch && method === 'GET') {
      return json(route, [])
    }

    const renderDownloadMatch = matches(pathname, /^\/api\/projects\/([^/]+)\/render\/download$/)
    if (renderDownloadMatch && method === 'GET') {
      return json(route, { download_url: MOCK_IMAGE_URL })
    }

    return json(route, {
      detail: `Unhandled mock API route: ${method} ${pathname}`,
    }, 501)
  })

  return state
}
