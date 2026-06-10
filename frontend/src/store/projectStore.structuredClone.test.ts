/**
 * projectStore.structuredClone.test.ts
 *
 * JSON.parse(JSON.stringify()) → structuredClone 移行のリグレッションテスト
 *
 * 確認観点:
 *   (a) structuredClone は undefined フィールドを保持する（JSON 方式は落とす）
 *   (b) updateTimeline が undo 履歴にスナップショットを積む
 *   (c) undo で直前の timeline が復元される
 *   (d) redo で取り消しを再適用できる
 *   (e) beginInteraction / updateTimelineLocal / updateTimeline ドラッグサイクル
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// API モック (fetchProject などを使わないテストでも vi.mock は先に宣言する)
// ---------------------------------------------------------------------------
vi.mock('@/api/projects', () => ({
  projectsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    updateTimeline: vi.fn(),
  },
}))
vi.mock('@/api/operations', () => ({
  operationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    apply: vi.fn(() => Promise.resolve({ version: 2, timeline_data: null })),
  },
}))
vi.mock('@/api/sequences', () => ({
  sequencesApi: {
    get: vi.fn(),
    update: vi.fn(),
  },
}))
vi.mock('@/utils/timelineDiff', () => ({
  // Return a dummy op so updateTimeline does not early-return
  diffTimeline: vi.fn(() => [{ type: 'NOOP' }]),
}))
vi.mock('@/utils/applyRemoteOperations', () => ({
  applyRemoteOperations: vi.fn((t: unknown) => t),
}))
vi.mock('@/api/client', () => ({
  setEditTokenForClient: vi.fn(),
}))

import type { TimelineData, ProjectDetail } from './projectStore'
import { useProjectStore } from './projectStore'
import { projectsApi } from '@/api/projects'

// Type augment to include updateTimeline
type ProjectsApiWithUpdateTimeline = typeof projectsApi & {
  updateTimeline: ReturnType<typeof vi.fn>
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTimeline(durationMs = 10000): TimelineData {
  return {
    version: '1',
    duration_ms: durationMs,
    layers: [],
    audio_tracks: [],
  }
}

function makeProject(timeline: TimelineData): ProjectDetail {
  return {
    id: 'proj-1',
    name: 'Test',
    description: null,
    status: 'active',
    duration_ms: timeline.duration_ms,
    thumbnail_url: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    user_id: 'u1',
    width: 1920,
    height: 1080,
    fps: 30,
    timeline_data: timeline,
    ai_provider: null,
    version: 1,
  }
}

// ---------------------------------------------------------------------------
// (a) structuredClone は undefined フィールドを保持する
// ---------------------------------------------------------------------------
describe('structuredClone vs JSON.parse/JSON.stringify', () => {
  it('preserves undefined fields (unlike JSON round-trip)', () => {
    const obj = { a: 1, b: undefined, c: 'hello' }

    // JSON 方式は undefined を落とす
    const jsonClone = JSON.parse(JSON.stringify(obj)) as typeof obj
    expect((jsonClone as Record<string, unknown>).b).toBeUndefined()
    expect('b' in jsonClone).toBe(false) // JSON はキーごと消える

    // structuredClone は undefined を保持する
    const scClone = structuredClone(obj)
    expect(scClone.b).toBeUndefined()
    expect('b' in scClone).toBe(true) // キーは残る
  })
})

// ---------------------------------------------------------------------------
// (b)(c)(d) undo/redo サイクル
// ---------------------------------------------------------------------------
describe('projectStore undo/redo with structuredClone', () => {
  beforeEach(() => {
    // ストアをリセット
    useProjectStore.setState({
      currentProject: null,
      currentSequence: null,
      timelineHistory: [],
      timelineFuture: [],
      historyVersion: 0,
      pendingInteractionBaseline: null,
    })
  })

  it('updateTimeline pushes snapshot to history', async () => {
    const timeline0 = makeTimeline(10000)
    const project = makeProject(timeline0)

    // fetchProject が返す値をセット
    vi.mocked(projectsApi.get).mockResolvedValue(project)
    vi.mocked(projectsApi.update).mockResolvedValue(project)

    // 直接ストアにプロジェクトを設定（fetchProject を呼ばずに）
    useProjectStore.setState({ currentProject: project })

    const timeline1 = makeTimeline(20000)

    // updateTimeline を呼ぶ（API call はモック）
    vi.mocked(projectsApi.update).mockResolvedValue({
      ...project,
      timeline_data: timeline1,
    })
    await useProjectStore.getState().updateTimeline('proj-1', timeline1, 'first change')

    const state = useProjectStore.getState()
    expect(state.timelineHistory).toHaveLength(1)
    expect(state.timelineHistory[0].label).toBe('first change')
    // スナップショットは timeline0 のコピー
    expect(state.timelineHistory[0].timeline.duration_ms).toBe(10000)
  })

  it('undo restores previous timeline', async () => {
    const timeline0 = makeTimeline(10000)
    const timeline1 = makeTimeline(20000)
    const project = makeProject(timeline0)

    useProjectStore.setState({ currentProject: project })

    // timeline1 に更新
    vi.mocked(projectsApi.update).mockResolvedValue({ ...project, timeline_data: timeline1, version: 2 })
    await useProjectStore.getState().updateTimeline('proj-1', timeline1, 'change to 20s')

    // undo (undo は projectsApi.updateTimeline を呼ぶ)
    const api = projectsApi as unknown as ProjectsApiWithUpdateTimeline
    api.updateTimeline.mockResolvedValue({ ...project, timeline_data: timeline0, version: 3 })
    await useProjectStore.getState().undo('proj-1')

    const state = useProjectStore.getState()
    const restoredDuration = state.currentProject?.timeline_data.duration_ms
    expect(restoredDuration).toBe(10000)
    expect(state.timelineHistory).toHaveLength(0)
    expect(state.timelineFuture).toHaveLength(1)
    expect(state.timelineFuture[0].timeline.duration_ms).toBe(20000)
  })

  it('redo re-applies undone change', async () => {
    const timeline0 = makeTimeline(10000)
    const timeline1 = makeTimeline(20000)
    const project = makeProject(timeline0)

    useProjectStore.setState({ currentProject: project })
    vi.mocked(projectsApi.update).mockResolvedValue({ ...project, timeline_data: timeline1, version: 2 })
    await useProjectStore.getState().updateTimeline('proj-1', timeline1, 'change to 20s')

    const api = projectsApi as unknown as ProjectsApiWithUpdateTimeline
    api.updateTimeline.mockResolvedValue({ ...project, timeline_data: timeline0, version: 3 })
    await useProjectStore.getState().undo('proj-1')

    api.updateTimeline.mockResolvedValue({ ...project, timeline_data: timeline1, version: 4 })
    await useProjectStore.getState().redo('proj-1')

    const state = useProjectStore.getState()
    expect(state.currentProject?.timeline_data.duration_ms).toBe(20000)
    expect(state.timelineHistory).toHaveLength(1)
    expect(state.timelineFuture).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// (e) drag cycle: beginInteraction → updateTimelineLocal → updateTimeline
// ---------------------------------------------------------------------------
describe('drag interaction baseline', () => {
  beforeEach(() => {
    useProjectStore.setState({
      currentProject: null,
      currentSequence: null,
      timelineHistory: [],
      timelineFuture: [],
      historyVersion: 0,
      pendingInteractionBaseline: null,
    })
  })

  it('captures pre-drag baseline and uses it for undo entry', async () => {
    const timeline0 = makeTimeline(10000)
    const project = makeProject(timeline0)

    useProjectStore.setState({ currentProject: project })

    // ドラッグ開始
    useProjectStore.getState().beginInteraction()

    const stateAfterBegin = useProjectStore.getState()
    expect(stateAfterBegin.pendingInteractionBaseline).not.toBeNull()
    expect(stateAfterBegin.pendingInteractionBaseline?.duration_ms).toBe(10000)

    // ドラッグ中（ローカル更新）
    const timelineMid = makeTimeline(15000)
    useProjectStore.getState().updateTimelineLocal('proj-1', timelineMid)

    // ローカル更新後もベースラインは 10000ms
    expect(useProjectStore.getState().pendingInteractionBaseline?.duration_ms).toBe(10000)

    // ドラッグ完了（API 保存）
    const timeline1 = makeTimeline(15000)
    vi.mocked(projectsApi.update).mockResolvedValue({ ...project, timeline_data: timeline1, version: 2 })
    await useProjectStore.getState().updateTimeline('proj-1', timeline1, 'drag end')

    const state = useProjectStore.getState()
    // undo 履歴にはドラッグ前の 10000ms が積まれているはず
    expect(state.timelineHistory).toHaveLength(1)
    expect(state.timelineHistory[0].timeline.duration_ms).toBe(10000)
    expect(state.pendingInteractionBaseline).toBeNull()
  })
})
