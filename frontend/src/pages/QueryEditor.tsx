import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Editor, type Monaco } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import { apiFetch, API_URL } from '../api'
import { CatalogPanel } from '../components/CatalogPanel'
import { DatabaseDetail, SchemaDetail, ObjectDetail } from '../components/CatalogDetails'
import { useStatusBarEffect } from '../StatusBarContext'
import { defineKolkhisTheme, THEME_NAME } from '../monacoTheme'

interface QueryResult {
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  page: number
  page_size: number
}

interface JobStatus {
  id: string
  status: string
  error: string | null
  row_count: number | null
  started_at: string | null
}

interface QueryTab {
  id: number
  name: string
  sql: string
  jobId: string | null
  status: string | null
  error: string | null
  result: QueryResult | null
  submitting: boolean
  startedAt: string | null
  elapsed: string | null
}

interface CatalogTab {
  id: string
  label: string
  type: 'object' | 'database' | 'schema'
  db?: string
  schema?: string
  name?: string
  objectType?: string
}

const SESSION_KEY = 'kolkhis-query-session'

interface SavedSession {
  queryTabs: { id: number; name: string; sql: string }[]
  activeQueryTab: number | null
  nextQueryId: number
}

function loadSession(): SavedSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch { return null }
}

function saveSession(tabs: QueryTab[], activeId: number | null, nextId: number) {
  const data: SavedSession = {
    queryTabs: tabs.map(t => ({ id: t.id, name: t.name, sql: t.sql })),
    activeQueryTab: activeId,
    nextQueryId: nextId,
  }
  localStorage.setItem(SESSION_KEY, JSON.stringify(data))
}

function parseUTC(ts: string): number {
  return new Date(ts.endsWith('Z') ? ts : ts + 'Z').getTime()
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function newQueryTab(id: number, name: string, sql = ''): QueryTab {
  return { id, name, sql, jobId: null, status: null, error: null, result: null, submitting: false, startedAt: null, elapsed: null }
}

export function QueryEditor() {
  // Load saved session or start empty
  const saved = useRef(loadSession())
  const nextQueryId = useRef(saved.current ? saved.current.nextQueryId : 1)

  const [queryTabs, setQueryTabs] = useState<QueryTab[]>(() => {
    if (saved.current && saved.current.queryTabs.length > 0) {
      return saved.current.queryTabs.map(s => newQueryTab(s.id, s.name, s.sql))
    }
    return []
  })
  const [activeQueryTab, setActiveQueryTab] = useState<number | null>(() => {
    return saved.current?.activeQueryTab ?? null
  })

  // Catalog (detail) tabs — unchanged
  const [catalogTabs, setCatalogTabs] = useState<CatalogTab[]>([])
  const [activeCatalogTab, setActiveCatalogTab] = useState<string | null>(null)

  // Which type is active: 'query' | 'catalog' | null
  const activeType = activeQueryTab !== null ? 'query' : activeCatalogTab !== null ? 'catalog' : null

  // Rename state
  const [renamingQueryId, setRenamingQueryId] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // UI state
  const [catalogOpen, setCatalogOpen] = useState(true)
  const [catalogWidth, setCatalogWidth] = useState(280)
  const [resultsHeight, setResultsHeight] = useState(250)
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0)
  const [tabContextMenu, setTabContextMenu] = useState<{ x: number; y: number; tabId: string; isQuery: boolean } | null>(null)
  const [tabOverflowOpen, setTabOverflowOpen] = useState(false)

  // Per-tab polling refs
  const pollRefs = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map())
  // Submit ref for Monaco keybinding
  const submitRef = useRef<() => void>(() => {})

  const [searchParams, setSearchParams] = useSearchParams()

  // --- Helpers ---

  function getActiveQueryTabObj(): QueryTab | undefined {
    return queryTabs.find(t => t.id === activeQueryTab)
  }

  function updateTab(id: number, partial: Partial<QueryTab>) {
    setQueryTabs(prev => prev.map(t => t.id === id ? { ...t, ...partial } : t))
  }

  // --- Tab management ---

  function addQueryTab() {
    const id = nextQueryId.current++
    const name = `Query ${id}`
    const tab = newQueryTab(id, name)
    setQueryTabs(prev => [...prev, tab])
    setActiveQueryTab(id)
    setActiveCatalogTab(null)
  }

  function closeQueryTab(id: number) {
    // Clear polling
    const poll = pollRefs.current.get(id)
    if (poll) { clearInterval(poll); pollRefs.current.delete(id) }

    setQueryTabs(prev => {
      const idx = prev.findIndex(t => t.id === id)
      const next = prev.filter(t => t.id !== id)
      if (activeQueryTab === id) {
        if (next.length > 0) {
          // Switch to adjacent tab
          const newIdx = Math.min(idx, next.length - 1)
          setActiveQueryTab(next[newIdx].id)
          setActiveCatalogTab(null)
        } else {
          setActiveQueryTab(null)
        }
      }
      return next
    })
  }

  function closeCatalogTab(tabId: string) {
    setCatalogTabs(prev => prev.filter(t => t.id !== tabId))
    if (activeCatalogTab === tabId) {
      setActiveCatalogTab(null)
      // Fall back to active query tab if any
      if (queryTabs.length > 0 && activeQueryTab === null) {
        setActiveQueryTab(queryTabs[queryTabs.length - 1].id)
      }
    }
  }

  function selectQueryTab(id: number) {
    setActiveQueryTab(id)
    setActiveCatalogTab(null)
  }

  function selectCatalogTab(id: string) {
    setActiveCatalogTab(id)
    setActiveQueryTab(null)
  }

  // --- Rename ---

  function startRenamingQuery(id: number) {
    const tab = queryTabs.find(t => t.id === id)
    if (!tab) return
    setRenamingQueryId(id)
    setRenameValue(tab.name)
  }

  function commitQueryRename() {
    if (renamingQueryId === null) return
    const name = renameValue.trim()
    if (name) {
      updateTab(renamingQueryId, { name })
    }
    setRenamingQueryId(null)
  }

  // --- Query execution (per-tab) ---

  function startPolling(tabId: number, jobId: string) {
    // Clear any existing poll for this tab
    const existing = pollRefs.current.get(tabId)
    if (existing) clearInterval(existing)

    const interval = setInterval(async () => {
      try {
        const job = await apiFetch<JobStatus>(`/api/queries/${jobId}`)
        updateTab(tabId, { status: job.status })
        if (job.started_at) updateTab(tabId, { startedAt: job.started_at })
        if (job.status === 'completed') {
          clearInterval(interval)
          pollRefs.current.delete(tabId)
          updateTab(tabId, { error: null })
          setCatalogRefreshKey(k => k + 1)
          await fetchResults(tabId, jobId, 0)
        } else if (job.status === 'failed') {
          clearInterval(interval)
          pollRefs.current.delete(tabId)
          updateTab(tabId, { error: job.error })
        } else if (job.status === 'cancelled') {
          clearInterval(interval)
          pollRefs.current.delete(tabId)
          updateTab(tabId, { error: null })
        }
      } catch {
        clearInterval(interval)
        pollRefs.current.delete(tabId)
      }
    }, 1000)
    pollRefs.current.set(tabId, interval)
  }

  async function fetchResults(tabId: number, jobId: string, page: number) {
    const data = await apiFetch<QueryResult>(`/api/queries/${jobId}/results?page=${page}`)
    updateTab(tabId, { result: data })
  }

  function handlePreview(sql: string) {
    const id = nextQueryId.current++
    const tab = newQueryTab(id, `Preview ${id}`, sql)
    setQueryTabs(prev => [...prev, tab])
    setActiveQueryTab(id)
    setActiveCatalogTab(null)
    submitSql(id, sql)
  }

  async function submitSql(tabId: number, sql: string) {
    updateTab(tabId, { submitting: true, error: null, result: null, status: null, startedAt: null, elapsed: null })
    try {
      const { job_id } = await apiFetch<{ job_id: string }>('/api/queries', {
        method: 'POST',
        body: JSON.stringify({ sql }),
      })
      updateTab(tabId, { jobId: job_id, status: 'pending', submitting: false })
      setSearchParams({ job_id })
      startPolling(tabId, job_id)
    } catch (e: unknown) {
      updateTab(tabId, { error: e instanceof Error ? e.message : 'Submit failed', submitting: false })
    }
  }

  function handleSubmit(tabId: number) {
    const tab = queryTabs.find(t => t.id === tabId)
    if (!tab || !tab.sql.trim()) return
    submitSql(tabId, tab.sql)
  }

  async function handleCancel(tabId: number) {
    const tab = queryTabs.find(t => t.id === tabId)
    if (!tab?.jobId) return
    try {
      await apiFetch(`/api/queries/${tab.jobId}/cancel`, { method: 'POST' })
      const poll = pollRefs.current.get(tabId)
      if (poll) { clearInterval(poll); pollRefs.current.delete(tabId) }
      updateTab(tabId, { status: 'cancelled', error: null })
    } catch (e: unknown) {
      updateTab(tabId, { error: e instanceof Error ? e.message : 'Cancel failed' })
    }
  }

  async function handlePage(tabId: number, page: number) {
    const tab = queryTabs.find(t => t.id === tabId)
    if (!tab?.jobId) return
    await fetchResults(tabId, tab.jobId, page)
  }

  // Keep submitRef pointed at current active tab's submit
  submitRef.current = () => {
    if (activeQueryTab !== null) handleSubmit(activeQueryTab)
  }

  // --- Catalog object selection ---

  function handleSelectObject(db: string, schema: string, name: string, objectType: string) {
    let tabId: string
    let label: string
    let tabType: CatalogTab['type']
    if (objectType === 'database') {
      tabId = db; label = db; tabType = 'database'
    } else if (objectType === 'schema') {
      tabId = `${db}.${schema}`; label = schema; tabType = 'schema'
    } else {
      tabId = `${db}.${schema}.${name}`; label = name; tabType = 'object'
    }
    const existing = catalogTabs.find(t => t.id === tabId)
    if (existing) {
      selectCatalogTab(tabId)
      return
    }
    const newTab: CatalogTab = { id: tabId, label, type: tabType, db, schema, name, objectType }
    setCatalogTabs(prev => [...prev, newTab])
    selectCatalogTab(tabId)
  }

  // --- Load job from URL ---

  async function loadJob(tabId: number, jobId: string) {
    try {
      const job = await apiFetch<JobStatus>(`/api/queries/${jobId}`)
      updateTab(tabId, { status: job.status, error: job.error, jobId })
      if (job.started_at) updateTab(tabId, { startedAt: job.started_at })
      if (job.status === 'completed') {
        await fetchResults(tabId, jobId, 0)
      } else if (job.status === 'pending' || job.status === 'provisioning' || job.status === 'running') {
        startPolling(tabId, jobId)
      }
    } catch (e: unknown) {
      updateTab(tabId, { error: e instanceof Error ? e.message : 'Failed to load query' })
    }
  }

  useEffect(() => {
    const urlJobId = searchParams.get('job_id')
    if (!urlJobId) return
    // Find an existing tab with this jobId, or create one
    const existing = queryTabs.find(t => t.jobId === urlJobId)
    if (existing) {
      selectQueryTab(existing.id)
    } else {
      const id = nextQueryId.current++
      const tab = newQueryTab(id, `Query ${id}`)
      tab.jobId = urlJobId
      setQueryTabs(prev => [...prev, tab])
      setActiveQueryTab(id)
      setActiveCatalogTab(null)
      loadJob(id, urlJobId)
    }
  }, []) // Only on mount

  // --- Cleanup polling on unmount ---
  useEffect(() => {
    return () => {
      pollRefs.current.forEach(interval => clearInterval(interval))
      pollRefs.current.clear()
    }
  }, [])

  // --- Elapsed time ticker for all active tabs ---
  useEffect(() => {
    const hasActive = queryTabs.some(t =>
      (t.status === 'pending' || t.status === 'provisioning' || t.status === 'running') && t.startedAt
    )
    if (!hasActive) return

    const tick = () => {
      setQueryTabs(prev => prev.map(t => {
        if ((t.status === 'pending' || t.status === 'provisioning' || t.status === 'running') && t.startedAt) {
          return { ...t, elapsed: formatElapsed(Date.now() - parseUTC(t.startedAt)) }
        }
        return t
      }))
    }
    tick()
    const id = setInterval(tick, 100)
    return () => clearInterval(id)
  }, [queryTabs.map(t => `${t.status}:${t.startedAt}`).join(',')])

  // --- Save session on state change ---
  useEffect(() => {
    saveSession(queryTabs, activeQueryTab, nextQueryId.current)
  }, [queryTabs, activeQueryTab])

  // --- Status bar ---
  const activeQTab = getActiveQueryTabObj()
  const activeCTab = catalogTabs.find(t => t.id === activeCatalogTab)
  const statusLabel = activeType === 'query' && activeQTab ? activeQTab.name
    : activeType === 'catalog' && activeCTab ? activeCTab.label
    : 'Analytics'
  const activeStatus = activeQTab?.status
  const statusBarLeft = useMemo(() => <span className="status-bar-item">{statusLabel}</span>, [statusLabel])
  const statusBarRight = useMemo(() => activeStatus ? <span className={`status-bar-item status-${activeStatus}`}>{activeStatus}</span> : null, [activeStatus])
  useStatusBarEffect(statusBarLeft, statusBarRight)

  // --- Tab context menu dismiss ---
  useEffect(() => {
    if (!tabContextMenu) return
    const handleClick = () => setTabContextMenu(null)
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setTabContextMenu(null) }
    document.addEventListener('click', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('click', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [tabContextMenu])

  // --- Tab overflow dropdown dismiss ---
  useEffect(() => {
    if (!tabOverflowOpen) return
    const handleClick = (e: MouseEvent) => {
      const wrapper = (e.target as HTMLElement).closest('.tab-overflow-wrapper')
      if (wrapper) return
      setTabOverflowOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setTabOverflowOpen(false) }
    requestAnimationFrame(() => {
      document.addEventListener('mousedown', handleClick)
      document.addEventListener('keydown', handleKey)
    })
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [tabOverflowOpen])

  // --- Close tabs (for context menu) ---
  // Build combined list for index-based operations
  type AnyTab = { key: string; isQuery: boolean; queryId?: number; catalogId?: string }
  const allTabs: AnyTab[] = [
    ...queryTabs.map(t => ({ key: `q-${t.id}`, isQuery: true, queryId: t.id })),
    ...catalogTabs.map(t => ({ key: `c-${t.id}`, isQuery: false, catalogId: t.id })),
  ]

  function closeTabs(filter: (tab: AnyTab, idx: number) => boolean) {
    const toClose = allTabs.filter((t, i) => filter(t, i))
    for (const t of toClose) {
      if (t.isQuery && t.queryId !== undefined) {
        const poll = pollRefs.current.get(t.queryId)
        if (poll) { clearInterval(poll); pollRefs.current.delete(t.queryId) }
      }
    }
    const closeQueryIds = new Set(toClose.filter(t => t.isQuery).map(t => t.queryId!))
    const closeCatalogIds = new Set(toClose.filter(t => !t.isQuery).map(t => t.catalogId!))

    setQueryTabs(prev => {
      const next = prev.filter(t => !closeQueryIds.has(t.id))
      if (activeQueryTab !== null && closeQueryIds.has(activeQueryTab)) {
        if (next.length > 0) {
          setActiveQueryTab(next[next.length - 1].id)
          setActiveCatalogTab(null)
        } else {
          setActiveQueryTab(null)
        }
      }
      return next
    })
    setCatalogTabs(prev => {
      const next = prev.filter(t => !closeCatalogIds.has(t.id))
      if (activeCatalogTab !== null && closeCatalogIds.has(activeCatalogTab)) {
        setActiveCatalogTab(null)
      }
      return next
    })
    setTabContextMenu(null)
  }

  // --- Editor mount ---
  function handleEditorMount(editorInstance: editor.IStandaloneCodeEditor, monaco: Monaco) {
    editorInstance.addAction({
      id: 'run-query',
      label: 'Run Query',
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
      run: () => submitRef.current(),
    })
  }

  // --- Resize handlers ---
  const handleResultsResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startHeight = resultsHeight
    const onMouseMove = (ev: MouseEvent) => {
      const delta = startY - ev.clientY
      setResultsHeight(Math.max(100, Math.min(600, startHeight + delta)))
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [resultsHeight])

  const handleCatalogResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = catalogWidth
    const onMouseMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      setCatalogWidth(Math.max(140, Math.min(500, startWidth + delta)))
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [catalogWidth])

  // --- Render ---

  const activeQ = getActiveQueryTabObj()
  const totalPages = activeQ?.result ? Math.ceil(activeQ.result.total / activeQ.result.page_size) : 0

  return (
    <div className="query-outer">
    <div className="query-layout">
      {catalogOpen && (
        <>
          <aside className="catalog-sidebar" style={{ width: catalogWidth, minWidth: catalogWidth }}>
            <div className="catalog-sidebar-header">
              <span className="catalog-sidebar-title">Catalog</span>
              <button className="catalog-toggle" onClick={() => setCatalogOpen(false)} title="Hide catalog">✕</button>
            </div>
            <CatalogPanel refreshKey={catalogRefreshKey} onSelectObject={handleSelectObject} />
          </aside>
          <div className="catalog-resize-handle" onMouseDown={handleCatalogResizeMouseDown} />
        </>
      )}
      <div className="query-main">
        {/* Tab bar */}
        <div className="query-tabs">
          <div className="query-tabs-inner">
            {/* Query tabs */}
            {queryTabs.map(tab => (
              <div
                key={`q-${tab.id}`}
                className={`query-tab ${activeQueryTab === tab.id && activeType === 'query' ? 'active' : ''}`}
                onClick={() => selectQueryTab(tab.id)}
                onDoubleClick={() => startRenamingQuery(tab.id)}
                onContextMenu={e => { e.preventDefault(); setTabContextMenu({ x: e.clientX, y: e.clientY, tabId: `q-${tab.id}`, isQuery: true }) }}
              >
                <span className="query-tab-badge query-tab-badge-query">Q</span>
                {renamingQueryId === tab.id ? (
                  <input
                    className="query-tab-rename"
                    value={renameValue}
                    onChange={e => setRenameValue(e.target.value)}
                    onBlur={commitQueryRename}
                    onKeyDown={e => {
                      if (e.key === 'Enter') commitQueryRename()
                      if (e.key === 'Escape') { setRenamingQueryId(null); setRenameValue('') }
                    }}
                    onClick={e => e.stopPropagation()}
                    autoFocus
                  />
                ) : (
                  <span className="query-tab-name">{tab.name}</span>
                )}
                <span
                  className="query-tab-close"
                  onClick={e => { e.stopPropagation(); closeQueryTab(tab.id) }}
                >✕</span>
              </div>
            ))}
            {/* Catalog tabs */}
            {catalogTabs.map(tab => (
              <div
                key={`c-${tab.id}`}
                className={`query-tab ${activeCatalogTab === tab.id && activeType === 'catalog' ? 'active' : ''}`}
                onClick={() => selectCatalogTab(tab.id)}
                onContextMenu={e => { e.preventDefault(); setTabContextMenu({ x: e.clientX, y: e.clientY, tabId: `c-${tab.id}`, isQuery: false }) }}
              >
                <span className={`query-tab-badge query-tab-badge-${tab.type === 'object' ? tab.objectType : tab.type}`}>
                  {{ database: 'DB', schema: 'S', object: tab.objectType === 'view' ? 'V' : 'T' }[tab.type]}
                </span>
                <span className="query-tab-name">{tab.label}</span>
                <span
                  className="query-tab-close"
                  onClick={e => { e.stopPropagation(); closeCatalogTab(tab.id) }}
                >✕</span>
              </div>
            ))}
            {/* Add query tab button */}
            <button className="query-tab-add" onClick={addQueryTab} title="New query tab">+</button>
          </div>
          <div className="tab-overflow-wrapper">
            <button className="tab-overflow-btn" onClick={() => setTabOverflowOpen(v => !v)} title="Open tabs">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
            {tabOverflowOpen && (
              <div className="tab-overflow-dropdown">
                {queryTabs.map(tab => (
                  <div
                    key={`q-${tab.id}`}
                    className={`tab-overflow-item${activeQueryTab === tab.id && activeType === 'query' ? ' active' : ''}`}
                    onClick={() => { selectQueryTab(tab.id); setTabOverflowOpen(false) }}
                  >
                    <span className="query-tab-badge query-tab-badge-query">Q</span>
                    <span className="tab-overflow-item-name">{tab.name}</span>
                    <span className="tab-overflow-item-close" onClick={e => { e.stopPropagation(); closeQueryTab(tab.id) }}>✕</span>
                  </div>
                ))}
                {catalogTabs.map(tab => (
                  <div
                    key={`c-${tab.id}`}
                    className={`tab-overflow-item${activeCatalogTab === tab.id && activeType === 'catalog' ? ' active' : ''}`}
                    onClick={() => { selectCatalogTab(tab.id); setTabOverflowOpen(false) }}
                  >
                    <span className={`query-tab-badge query-tab-badge-${tab.type === 'object' ? tab.objectType : tab.type}`}>
                      {{ database: 'DB', schema: 'S', object: tab.objectType === 'view' ? 'V' : 'T' }[tab.type]}
                    </span>
                    <span className="tab-overflow-item-name">{tab.label}</span>
                    <span className="tab-overflow-item-close" onClick={e => { e.stopPropagation(); closeCatalogTab(tab.id) }}>✕</span>
                  </div>
                ))}
                {/* Add query tab in overflow too */}
                <div
                  className="tab-overflow-item"
                  onClick={() => { addQueryTab(); setTabOverflowOpen(false) }}
                >
                  <span style={{ color: '#666' }}>+</span>
                  <span className="tab-overflow-item-name">New Query</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Query tab content */}
        {activeType === 'query' && activeQ && (
          <>
            <div className="editor-area">
              {!catalogOpen && (
                <div style={{ flexShrink: 0 }}>
                  <button className="catalog-toggle" onClick={() => setCatalogOpen(true)} title="Show catalog">☰</button>
                </div>
              )}
              <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Editor
                  path={`query-${activeQ.id}`}
                  height="100%"
                  language="sql"
                  beforeMount={defineKolkhisTheme}
                  theme={THEME_NAME}
                  value={activeQ.sql}
                  onChange={(value) => { updateTab(activeQ.id, { sql: value || '' }) }}
                  onMount={handleEditorMount}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 14,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                    padding: { top: 8 },
                  }}
                />
              </div>
              <div style={{ display: 'flex', gap: '1em', padding: '0.4em 0 0.4em 0.5em', alignItems: 'center', flexShrink: 0 }}>
                {activeQ.status === 'pending' || activeQ.status === 'provisioning' || activeQ.status === 'running' ? (
                  <button onClick={() => handleCancel(activeQ.id)}>Cancel</button>
                ) : (
                  <button onClick={() => handleSubmit(activeQ.id)} disabled={activeQ.submitting || !activeQ.sql.trim()}>
                    {activeQ.submitting ? 'Submitting...' : 'Run Query'}
                  </button>
                )}
                {activeQ.status && (
                  <span className={`status-${activeQ.status}`}>
                    {activeQ.status}{activeQ.elapsed && (activeQ.status === 'pending' || activeQ.status === 'provisioning' || activeQ.status === 'running') ? `  ${activeQ.elapsed}` : ''}
                  </span>
                )}
                {activeQ.jobId && activeQ.status === 'completed' && (
                  <>
                    <a href={`${API_URL}/api/queries/${activeQ.jobId}/export`} style={{ fontSize: '0.85em' }}>
                      Download CSV
                    </a>
                    {!activeQ.result && (
                      <a
                        href="#"
                        style={{ fontSize: '0.85em' }}
                        onClick={e => { e.preventDefault(); fetchResults(activeQ.id, activeQ.jobId!, 0) }}
                      >
                        Show Results
                      </a>
                    )}
                  </>
                )}
              </div>
            </div>

            {activeQ.error && (
              <pre style={{ color: '#f87171', marginTop: '1em', whiteSpace: 'pre-wrap', fontSize: '0.85em', flexShrink: 0 }}>
                {activeQ.error}
              </pre>
            )}

            {activeQ.result && (
              <>
              <div className="results-resize-handle" onMouseDown={handleResultsResizeMouseDown} />
              <div className="results-panel" style={{ height: resultsHeight }}>
                <div className="results-header">
                  <span>{activeQ.result.total} rows</span>
                  <button className="results-close" onClick={() => updateTab(activeQ.id, { result: null })} title="Close results">✕</button>
                </div>
                <div className="table-container">
                  <table>
                    <thead>
                      <tr>
                        {activeQ.result.columns.map(col => (
                          <th key={col}>{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {activeQ.result.rows.map((row, i) => (
                        <tr key={i}>
                          {activeQ.result!.columns.map(col => (
                            <td key={col}>{String(row[col] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="pagination">
                  <button disabled={activeQ.result.page === 0} onClick={() => handlePage(activeQ.id, activeQ.result!.page - 1)}>
                    Prev
                  </button>
                  <span>Page {activeQ.result.page + 1} of {totalPages} ({activeQ.result.total} rows)</span>
                  <button disabled={activeQ.result.page + 1 >= totalPages} onClick={() => handlePage(activeQ.id, activeQ.result!.page + 1)}>
                    Next
                  </button>
                </div>
              </div>
              </>
            )}
          </>
        )}

        {/* Catalog detail tabs */}
        {activeType === 'catalog' && catalogTabs.map(tab => (
          activeCatalogTab === tab.id && (
            tab.type === 'database' ? (
              <DatabaseDetail key={tab.id} db={tab.db!} onNavigate={handleSelectObject} />
            ) : tab.type === 'schema' ? (
              <SchemaDetail key={tab.id} db={tab.db!} schema={tab.schema!} onNavigate={handleSelectObject} />
            ) : (
              <ObjectDetail key={tab.id} db={tab.db!} schema={tab.schema!} name={tab.name!} objectType={tab.objectType!} onPreview={handlePreview} onNavigate={handleSelectObject} />
            )
          )
        ))}

        {/* Empty state */}
        {activeType === null && (
          <div className="query-empty-state">
            <button onClick={addQueryTab}>New SQL Query</button>
          </div>
        )}
      </div>
    </div>

    {/* Tab context menu */}
    {tabContextMenu && (() => {
      const idx = allTabs.findIndex(t => t.key === tabContextMenu.tabId)
      return (
        <div className="context-menu" style={{ left: tabContextMenu.x, top: tabContextMenu.y }}>
          <div className="context-menu-item" onClick={() => {
            if (tabContextMenu.isQuery) {
              const qId = parseInt(tabContextMenu.tabId.slice(2))
              closeQueryTab(qId)
            } else {
              closeCatalogTab(tabContextMenu.tabId.slice(2))
            }
            setTabContextMenu(null)
          }}>Close</div>
          {tabContextMenu.isQuery && (
            <div className="context-menu-item" onClick={() => {
              const qId = parseInt(tabContextMenu.tabId.slice(2))
              startRenamingQuery(qId)
              setTabContextMenu(null)
            }}>Rename</div>
          )}
          <div className="context-menu-item" onClick={() => { closeTabs(t => t.key !== tabContextMenu.tabId) }}>Close Other Tabs</div>
          <div className="context-menu-item" onClick={() => { closeTabs(() => true) }}>Close All Tabs</div>
          <div className="context-menu-separator" />
          <div className="context-menu-item" onClick={() => { closeTabs((_t, i) => i < idx) }}>Close Tabs to the Left</div>
          <div className="context-menu-item" onClick={() => { closeTabs((_t, i) => i > idx) }}>Close Tabs to the Right</div>
        </div>
      )
    })()}

    </div>
  )
}
