import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { projectsApi } from '@/api/projects'
import { sequencesApi, type SequenceListItem } from '@/api/sequences'
import type { Project } from '@/store/projectStore'

const STORAGE_KEY = 'douga-editor-debug-state'

const CHECKLIST_ITEMS = [
  {
    id: 'open-editor',
    title: 'Open the target editor sequence',
    description: 'Confirm the selected route loads without console or shell errors.',
  },
  {
    id: 'reproduce-fix',
    title: 'Run the issue-specific reproduction',
    description: 'Use the exact steps that failed before the fix.',
  },
  {
    id: 'save-and-reload',
    title: 'Save and reload',
    description: 'Verify the relevant state persists after a full page reload.',
  },
  {
    id: 'export-pass',
    title: 'Export or render pass',
    description: 'Run export if the issue could affect preview/render divergence.',
  },
] as const

type ChecklistState = Record<(typeof CHECKLIST_ITEMS)[number]['id'], boolean>

type DebugState = {
  issueReference: string
  notes: string
  projectId: string
  sequenceId: string
  verificationFocus: string
  checklist: ChecklistState
}

const DEFAULT_CHECKLIST: ChecklistState = {
  'open-editor': false,
  'reproduce-fix': false,
  'save-and-reload': false,
  'export-pass': false,
}

function loadDebugState(): DebugState {
  if (typeof window === 'undefined') {
    return {
      issueReference: '',
      notes: '',
      projectId: '',
      sequenceId: '',
      verificationFocus: '',
      checklist: DEFAULT_CHECKLIST,
    }
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return {
        issueReference: '',
        notes: '',
        projectId: '',
        sequenceId: '',
        verificationFocus: '',
        checklist: DEFAULT_CHECKLIST,
      }
    }

    const parsed = JSON.parse(raw) as Partial<DebugState>
    return {
      issueReference: parsed.issueReference ?? '',
      notes: parsed.notes ?? '',
      projectId: parsed.projectId ?? '',
      sequenceId: parsed.sequenceId ?? '',
      verificationFocus: parsed.verificationFocus ?? '',
      checklist: {
        ...DEFAULT_CHECKLIST,
        ...(parsed.checklist ?? {}),
      },
    }
  } catch {
    return {
      issueReference: '',
      notes: '',
      projectId: '',
      sequenceId: '',
      verificationFocus: '',
      checklist: DEFAULT_CHECKLIST,
    }
  }
}

function saveDebugState(state: DebugState) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
}

function sortProjects(projects: Project[]) {
  return [...projects].sort((left, right) => {
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()
  })
}

function sortSequences(sequences: SequenceListItem[]) {
  return [...sequences].sort((left, right) => {
    if (left.is_default !== right.is_default) {
      return left.is_default ? -1 : 1
    }
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()
  })
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('ja-JP', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function formatDuration(durationMs: number) {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function buildVerificationSummary(options: {
  issueReference: string
  project?: Project
  sequence?: SequenceListItem
  verificationFocus: string
  notes: string
  checklist: ChecklistState
}) {
  const checklistLines = CHECKLIST_ITEMS.map((item) => {
    const checked = options.checklist[item.id] ? 'x' : ' '
    return `- [${checked}] ${item.title}`
  })

  return [
    `Issue: ${options.issueReference || '(not set)'}`,
    `Project: ${options.project ? `${options.project.name} (${options.project.id})` : '(not selected)'}`,
    `Sequence: ${options.sequence ? `${options.sequence.name} (${options.sequence.id})` : '(not selected)'}`,
    `Focus: ${options.verificationFocus || '(not set)'}`,
    '',
    'Checklist:',
    ...checklistLines,
    '',
    'Notes:',
    options.notes || '(none)',
  ].join('\n')
}

export default function EditorDebug() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const initialState = useMemo(() => {
    const stored = loadDebugState()
    return {
      ...stored,
      issueReference: searchParams.get('issue') ?? stored.issueReference,
      projectId: searchParams.get('projectId') ?? stored.projectId,
      sequenceId: searchParams.get('sequenceId') ?? stored.sequenceId,
    }
  }, [searchParams])

  const [projects, setProjects] = useState<Project[]>([])
  const [sequences, setSequences] = useState<SequenceListItem[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState(initialState.projectId)
  const [selectedSequenceId, setSelectedSequenceId] = useState(initialState.sequenceId)
  const [issueReference, setIssueReference] = useState(initialState.issueReference)
  const [verificationFocus, setVerificationFocus] = useState(initialState.verificationFocus)
  const [notes, setNotes] = useState(initialState.notes)
  const [checklist, setChecklist] = useState<ChecklistState>(initialState.checklist)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [loadingSequences, setLoadingSequences] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null)

  const selectedProject = projects.find((project) => project.id === selectedProjectId)
  const selectedSequence = sequences.find((sequence) => sequence.id === selectedSequenceId)
  const editorPath = selectedProjectId && selectedSequenceId
    ? `/project/${selectedProjectId}/sequence/${selectedSequenceId}`
    : ''

  useEffect(() => {
    let cancelled = false

    const fetchProjects = async () => {
      setLoadingProjects(true)
      setError(null)

      try {
        const data = sortProjects(await projectsApi.list())
        if (cancelled) return

        setProjects(data)

        setSelectedProjectId((currentProjectId) => {
          if (data.some((project) => project.id === currentProjectId)) {
            return currentProjectId
          }
          return data[0]?.id ?? ''
        })
      } catch (err) {
        console.error('Failed to fetch projects for editor debug page:', err)
        if (!cancelled) {
          setError('プロジェクト一覧の取得に失敗しました。')
        }
      } finally {
        if (!cancelled) {
          setLoadingProjects(false)
        }
      }
    }

    void fetchProjects()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    if (!selectedProjectId) {
      setSequences([])
      setSelectedSequenceId('')
      return
    }

    const fetchSequences = async () => {
      setLoadingSequences(true)
      setError(null)

      try {
        const data = sortSequences(await sequencesApi.list(selectedProjectId))
        if (cancelled) return

        setSequences(data)

        setSelectedSequenceId((currentSequenceId) => {
          if (data.some((sequence) => sequence.id === currentSequenceId)) {
            return currentSequenceId
          }
          const defaultSequence = data.find((sequence) => sequence.is_default) ?? data[0]
          return defaultSequence?.id ?? ''
        })
      } catch (err) {
        console.error('Failed to fetch sequences for editor debug page:', err)
        if (!cancelled) {
          setError('シーケンス一覧の取得に失敗しました。')
        }
      } finally {
        if (!cancelled) {
          setLoadingSequences(false)
        }
      }
    }

    void fetchSequences()

    return () => {
      cancelled = true
    }
  }, [selectedProjectId])

  useEffect(() => {
    saveDebugState({
      issueReference,
      notes,
      projectId: selectedProjectId,
      sequenceId: selectedSequenceId,
      verificationFocus,
      checklist,
    })
  }, [checklist, issueReference, notes, selectedProjectId, selectedSequenceId, verificationFocus])

  const verificationSummary = useMemo(() => {
    return buildVerificationSummary({
      issueReference,
      project: selectedProject,
      sequence: selectedSequence,
      verificationFocus,
      notes,
      checklist,
    })
  }, [checklist, issueReference, notes, selectedProject, selectedSequence, verificationFocus])

  const handleToggleChecklist = (id: keyof ChecklistState) => {
    setChecklist((current) => ({
      ...current,
      [id]: !current[id],
    }))
  }

  const handleCopySummary = async () => {
    try {
      await navigator.clipboard.writeText(verificationSummary)
      setCopyFeedback('確認テンプレートをコピーしました。')
    } catch (err) {
      console.error('Failed to copy verification summary:', err)
      setCopyFeedback('コピーに失敗しました。')
    }
  }

  const handleResetChecklist = () => {
    setChecklist(DEFAULT_CHECKLIST)
    setNotes('')
    setCopyFeedback(null)
  }

  useEffect(() => {
    if (!copyFeedback) return

    const timeoutId = window.setTimeout(() => {
      setCopyFeedback(null)
    }, 2500)

    return () => {
      window.clearTimeout(timeoutId)
    }
  }, [copyFeedback])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100" data-testid="editor-debug-page">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-8 px-6 py-10 lg:px-10">
        <header className="flex flex-col gap-4 rounded-3xl border border-slate-800 bg-slate-900/80 p-8 shadow-2xl shadow-slate-950/40">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <p className="text-sm font-medium uppercase tracking-[0.24em] text-cyan-300">Internal Debug Route</p>
              <h1 className="text-3xl font-semibold text-white">Editor verification hub</h1>
              <p className="max-w-3xl text-sm leading-6 text-slate-300">
                Issue ごとの確認対象を固定して、既存の editor へ飛ぶための内部ページです。
                別サイトは増やさず、project / sequence の選択、確認チェック、メモをここで揃えます。
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Link
                to="/app"
                className="rounded-xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-slate-500 hover:bg-slate-800"
              >
                Dashboard
              </Link>
              <button
                type="button"
                onClick={handleCopySummary}
                className="rounded-xl bg-cyan-400 px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-cyan-300"
                data-testid="editor-debug-copy-summary"
              >
                Copy verification template
              </button>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Route</p>
              <p className="mt-2 font-mono text-sm text-cyan-200">/debug/editor</p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Selected issue</p>
              <p className="mt-2 text-sm text-slate-200">{issueReference || 'Not set'}</p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Open target</p>
              <p className="mt-2 font-mono text-sm text-slate-200">{editorPath || 'Select a project and sequence'}</p>
            </div>
          </div>
        </header>

        <main className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
          <section className="space-y-6 rounded-3xl border border-slate-800 bg-slate-900/80 p-8">
            <div className="grid gap-5 md:grid-cols-2">
              <label className="space-y-2">
                <span className="text-sm font-medium text-slate-200">Issue reference</span>
                <input
                  value={issueReference}
                  onChange={(event) => setIssueReference(event.target.value)}
                  placeholder="#5 or issue title"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400"
                  data-testid="editor-debug-issue-input"
                />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium text-slate-200">Verification focus</span>
                <input
                  value={verificationFocus}
                  onChange={(event) => setVerificationFocus(event.target.value)}
                  placeholder="e.g. cut boundary playback, save/reload, export parity"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400"
                  data-testid="editor-debug-focus-input"
                />
              </label>
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              <label className="space-y-2">
                <span className="text-sm font-medium text-slate-200">Project</span>
                <select
                  value={selectedProjectId}
                  onChange={(event) => setSelectedProjectId(event.target.value)}
                  disabled={loadingProjects || projects.length === 0}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
                  data-testid="editor-debug-project-select"
                >
                  {loadingProjects && <option value="">Loading projects...</option>}
                  {!loadingProjects && projects.length === 0 && <option value="">No projects</option>}
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="space-y-2">
                <span className="text-sm font-medium text-slate-200">Sequence</span>
                <select
                  value={selectedSequenceId}
                  onChange={(event) => setSelectedSequenceId(event.target.value)}
                  disabled={!selectedProjectId || loadingSequences || sequences.length === 0}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
                  data-testid="editor-debug-sequence-select"
                >
                  {!selectedProjectId && <option value="">Select a project first</option>}
                  {selectedProjectId && loadingSequences && <option value="">Loading sequences...</option>}
                  {selectedProjectId && !loadingSequences && sequences.length === 0 && <option value="">No sequences</option>}
                  {sequences.map((sequence) => (
                    <option key={sequence.id} value={sequence.id}>
                      {sequence.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {error && (
              <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                {error}
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-5" data-testid="editor-debug-project-card">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Project snapshot</p>
                {selectedProject ? (
                  <div className="mt-3 space-y-2 text-sm text-slate-200">
                    <p className="font-medium text-white">{selectedProject.name}</p>
                    <p className="font-mono text-xs text-slate-400">{selectedProject.id}</p>
                    <p>Updated: {formatDate(selectedProject.updated_at)}</p>
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-slate-400">Select a project.</p>
                )}
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-5" data-testid="editor-debug-sequence-card">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Sequence snapshot</p>
                {selectedSequence ? (
                  <div className="mt-3 space-y-2 text-sm text-slate-200">
                    <p className="font-medium text-white">{selectedSequence.name}</p>
                    <p className="font-mono text-xs text-slate-400">{selectedSequence.id}</p>
                    <p>Duration: {formatDuration(selectedSequence.duration_ms)}</p>
                    <p>Updated: {formatDate(selectedSequence.updated_at)}</p>
                    {selectedSequence.is_default && (
                      <span className="inline-flex rounded-full bg-cyan-400/15 px-2.5 py-1 text-xs font-medium text-cyan-200">
                        Default sequence
                      </span>
                    )}
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-slate-400">Select a sequence.</p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                disabled={!editorPath}
                onClick={() => navigate(editorPath)}
                className="rounded-xl bg-white px-4 py-2.5 text-sm font-medium text-slate-950 transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                data-testid="editor-debug-open-editor-button"
              >
                Open editor
              </button>
              <a
                href={editorPath || undefined}
                target="_blank"
                rel="noreferrer"
                aria-disabled={!editorPath}
                className={`rounded-xl border px-4 py-2.5 text-sm font-medium transition ${
                  editorPath
                    ? 'border-slate-700 text-slate-100 hover:border-slate-500 hover:bg-slate-800'
                    : 'pointer-events-none border-slate-800 text-slate-500'
                }`}
                data-testid="editor-debug-open-editor-link"
              >
                Open in new tab
              </a>
            </div>
          </section>

          <aside className="space-y-6">
            <section className="rounded-3xl border border-slate-800 bg-slate-900/80 p-8" data-testid="editor-debug-checklist">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-white">Standard verification checklist</p>
                  <p className="mt-1 text-sm leading-6 text-slate-300">
                    問題ごとの reproduction は変わっても、この枠だけは固定しておくと rollback の見落としが減ります。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleResetChecklist}
                  className="rounded-xl border border-slate-700 px-3 py-2 text-xs font-medium text-slate-200 transition hover:border-slate-500 hover:bg-slate-800"
                  data-testid="editor-debug-reset-checklist"
                >
                  Reset
                </button>
              </div>
              <div className="mt-6 space-y-3">
                {CHECKLIST_ITEMS.map((item) => (
                  <label
                    key={item.id}
                    className="flex gap-3 rounded-2xl border border-slate-800 bg-slate-950/70 p-4"
                  >
                    <input
                      type="checkbox"
                      checked={checklist[item.id]}
                      onChange={() => handleToggleChecklist(item.id)}
                      className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-900 text-cyan-400 focus:ring-cyan-400"
                      data-testid={`editor-debug-check-${item.id}`}
                    />
                    <div>
                      <p className="text-sm font-medium text-white">{item.title}</p>
                      <p className="mt-1 text-sm text-slate-400">{item.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </section>

            <section className="rounded-3xl border border-slate-800 bg-slate-900/80 p-8">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-white">Verification notes</p>
                  <p className="mt-1 text-sm text-slate-300">
                    Issue コメントに貼る前提のメモをここでまとめます。
                  </p>
                </div>
                {copyFeedback && (
                  <span className="text-xs font-medium text-cyan-200">{copyFeedback}</span>
                )}
              </div>
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Observed behavior, pass/fail notes, rollback concerns..."
                className="mt-5 min-h-40 w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm leading-6 text-white outline-none transition focus:border-cyan-400"
                data-testid="editor-debug-notes"
              />
              <pre
                className="mt-5 overflow-x-auto rounded-2xl border border-slate-800 bg-slate-950/90 p-4 text-xs leading-6 text-slate-300"
                data-testid="editor-debug-summary"
              >
                {verificationSummary}
              </pre>
            </section>
          </aside>
        </main>
      </div>
    </div>
  )
}
