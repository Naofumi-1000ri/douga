/**
 * projectStore.undo.test.ts — undo/redo 機能のリグレッションテスト (Issue #276)
 *
 * テスト観点:
 *   (a) canUndo/canRedo は history/future の有無で正しく変わる
 *   (b) clearHistory で history/future がリセットされる
 *   (c) getUndoLabel / getRedoLabel が正しいラベルを返す
 *   (d) undo: sequence モードで sequencesApi.update が呼ばれ、historyVersion が増える
 *   (e) redo: sequence モードで sequencesApi.update が呼ばれ、historyVersion が増える
 *   (f) undo: 409 エラー時は conflictState にセットされ history は変わらない
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// API モック (hoisted)
// ---------------------------------------------------------------------------
const { sequencesApiUpdateMock, projectsApiUpdateTimelineMock } = vi.hoisted(() => ({
  sequencesApiUpdateMock: vi.fn(),
  projectsApiUpdateTimelineMock: vi.fn(),
}))

vi.mock('@/api/sequences', () => ({
  sequencesApi: {
    get: vi.fn(),
    update: sequencesApiUpdateMock,
  },
}))

vi.mock('@/api/projects', () => ({
  projectsApi: {
    list: vi.fn(async () => []),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    updateTimeline: projectsApiUpdateTimelineMock,
  },
}))

vi.mock('@/api/operations', () => ({
  operationsApi: {
    list: vi.fn(async () => []),
  },
}))

vi.mock('@/utils/timelineDiff', () => ({
  diffTimeline: vi.fn(() => []),
}))

vi.mock('@/utils/applyRemoteOperations', () => ({
  applyRemoteOperations: vi.fn((t: unknown) => t),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    post: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    put: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    patch: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    delete: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
  },
  getEditTokenForClient: vi.fn(() => null),
  setEditTokenForClient: vi.fn(),
}))

// ---------------------------------------------------------------------------
// テストデータ helpers
// ---------------------------------------------------------------------------
import type { TimelineData } from './projectStore'

function makeTimeline(durationMs = 5000): TimelineData {
  return {
    version: '1.0',
    duration_ms: durationMs,
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
  }
}

// ---------------------------------------------------------------------------
// テスト
// ---------------------------------------------------------------------------
let useProjectStore: (typeof import('./projectStore'))['useProjectStore']

beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  const mod = await import('./projectStore')
  useProjectStore = mod.useProjectStore
})

describe('projectStore: canUndo / canRedo', () => {
  it('(a) 初期状態では canUndo=false, canRedo=false', () => {
    const store = useProjectStore.getState()
    expect(store.canUndo()).toBe(false)
    expect(store.canRedo()).toBe(false)
  })

  it('(a) history に1件追加すると canUndo=true', () => {
    const tl = makeTimeline()
    useProjectStore.setState({
      timelineHistory: [{ timeline: tl, label: 'action1', timestamp: Date.now() }],
    })
    expect(useProjectStore.getState().canUndo()).toBe(true)
    expect(useProjectStore.getState().canRedo()).toBe(false)
  })

  it('(a) future に1件追加すると canRedo=true', () => {
    const tl = makeTimeline()
    useProjectStore.setState({
      timelineFuture: [{ timeline: tl, label: 'action1', timestamp: Date.now() }],
    })
    expect(useProjectStore.getState().canUndo()).toBe(false)
    expect(useProjectStore.getState().canRedo()).toBe(true)
  })
})

describe('projectStore: clearHistory', () => {
  it('(b) clearHistory で history/future/pendingBaseline がリセットされる', () => {
    const tl = makeTimeline()
    useProjectStore.setState({
      timelineHistory: [{ timeline: tl, label: 'a', timestamp: Date.now() }],
      timelineFuture: [{ timeline: tl, label: 'b', timestamp: Date.now() }],
      pendingInteractionBaseline: tl,
    })
    useProjectStore.getState().clearHistory()
    const state = useProjectStore.getState()
    expect(state.timelineHistory).toHaveLength(0)
    expect(state.timelineFuture).toHaveLength(0)
    expect(state.pendingInteractionBaseline).toBeNull()
  })
})

describe('projectStore: getUndoLabel / getRedoLabel', () => {
  it('(c) history が空なら getUndoLabel は null', () => {
    expect(useProjectStore.getState().getUndoLabel()).toBeNull()
  })

  it('(c) history の最後の label を返す', () => {
    const tl = makeTimeline()
    useProjectStore.setState({
      timelineHistory: [
        { timeline: tl, label: 'first', timestamp: 1 },
        { timeline: tl, label: 'last', timestamp: 2 },
      ],
    })
    expect(useProjectStore.getState().getUndoLabel()).toBe('last')
  })

  it('(c) future が空なら getRedoLabel は null', () => {
    expect(useProjectStore.getState().getRedoLabel()).toBeNull()
  })

  it('(c) future の先頭の label を返す', () => {
    const tl = makeTimeline()
    useProjectStore.setState({
      timelineFuture: [
        { timeline: tl, label: 'next', timestamp: 1 },
        { timeline: tl, label: 'further', timestamp: 2 },
      ],
    })
    expect(useProjectStore.getState().getRedoLabel()).toBe('next')
  })
})

describe('projectStore: undo (sequence mode)', () => {
  it('(d) undo が sequencesApi.update を呼び historyVersion が増える', async () => {
    const timelineBefore = makeTimeline(3000)
    const timelineCurrent = makeTimeline(5000)
    const seqId = 'seq-abc'
    const projectId = 'proj-abc'

    sequencesApiUpdateMock.mockResolvedValueOnce({
      id: seqId,
      project_id: projectId,
      timeline_data: timelineBefore,
      version: 2,
      duration_ms: timelineBefore.duration_ms,
    })

    useProjectStore.setState({
      currentSequence: {
        id: seqId,
        project_id: projectId,
        timeline_data: timelineCurrent,
        version: 1,
        duration_ms: timelineCurrent.duration_ms,
        name: 'Main',
        is_default: true,
        locked_by: null,
        lock_holder_name: null,
        locked_at: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      timelineHistory: [{ timeline: timelineBefore, label: 'clip-add', timestamp: Date.now() }],
      timelineFuture: [],
      historyVersion: 0,
    })

    await useProjectStore.getState().undo(projectId)

    expect(sequencesApiUpdateMock).toHaveBeenCalledOnce()
    const state = useProjectStore.getState()
    expect(state.timelineHistory).toHaveLength(0)
    expect(state.timelineFuture).toHaveLength(1)
    expect(state.historyVersion).toBe(1)
    expect(state.currentSequence?.timeline_data.duration_ms).toBe(timelineBefore.duration_ms)
  })

  it('(f) undo: 409 エラー時は conflictState にセット、history はそのまま', async () => {
    const timelineBefore = makeTimeline(3000)
    const timelineCurrent = makeTimeline(5000)
    const seqId = 'seq-409'
    const projectId = 'proj-409'

    const error = {
      response: {
        status: 409,
        data: { detail: { code: 'VERSION_CONFLICT', server_version: 99 } },
      },
    }
    sequencesApiUpdateMock.mockRejectedValueOnce(error)

    useProjectStore.setState({
      currentSequence: {
        id: seqId,
        project_id: projectId,
        timeline_data: timelineCurrent,
        version: 1,
        duration_ms: timelineCurrent.duration_ms,
        name: 'Main',
        is_default: true,
        locked_by: null,
        lock_holder_name: null,
        locked_at: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      timelineHistory: [{ timeline: timelineBefore, label: 'before', timestamp: Date.now() }],
      timelineFuture: [],
      historyVersion: 0,
    })

    await useProjectStore.getState().undo(projectId)

    const state = useProjectStore.getState()
    expect(state.conflictState?.isConflicting).toBe(true)
    expect(state.conflictState?.serverVersion).toBe(99)
    // history must be unchanged (not popped)
    expect(state.timelineHistory).toHaveLength(1)
  })
})

describe('projectStore: redo (sequence mode)', () => {
  it('(e) redo が sequencesApi.update を呼び historyVersion が増える', async () => {
    const timelinePast = makeTimeline(3000)
    const timelineCurrent = makeTimeline(5000)
    const timelineNext = makeTimeline(8000)
    const seqId = 'seq-redo'
    const projectId = 'proj-redo'

    sequencesApiUpdateMock.mockResolvedValueOnce({
      id: seqId,
      project_id: projectId,
      timeline_data: timelineNext,
      version: 3,
      duration_ms: timelineNext.duration_ms,
    })

    useProjectStore.setState({
      currentSequence: {
        id: seqId,
        project_id: projectId,
        timeline_data: timelineCurrent,
        version: 2,
        duration_ms: timelineCurrent.duration_ms,
        name: 'Main',
        is_default: true,
        locked_by: null,
        lock_holder_name: null,
        locked_at: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      timelineHistory: [{ timeline: timelinePast, label: 'past', timestamp: Date.now() }],
      timelineFuture: [{ timeline: timelineNext, label: 'redo-me', timestamp: Date.now() }],
      historyVersion: 5,
    })

    await useProjectStore.getState().redo(projectId)

    expect(sequencesApiUpdateMock).toHaveBeenCalledOnce()
    const state = useProjectStore.getState()
    expect(state.timelineFuture).toHaveLength(0)
    expect(state.timelineHistory).toHaveLength(2)
    expect(state.historyVersion).toBe(6)
    expect(state.currentSequence?.timeline_data.duration_ms).toBe(timelineNext.duration_ms)
  })
})
