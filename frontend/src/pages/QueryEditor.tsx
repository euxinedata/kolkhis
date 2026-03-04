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

interface OpenTab {
  id: string
  label: string
  type: 'query' | 'object' | 'database' | 'schema'
  db?: string
  schema?: string
  name?: string
  objectType?: string
}

const QUERY_TAB: OpenTab = { id: '__query__', label: 'Query', type: 'query' }

function parseUTC(ts: string): number {
  return new Date(ts.endsWith('Z') ? ts : ts + 'Z').getTime()
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function QueryEditor() {
  const [sql, setSql] = useState(() => localStorage.getItem('kolkhis_sql') ?? '')
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [startedAt, setStartedAt] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState<string | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(true)
  const [catalogWidth, setCatalogWidth] = useState(280)
  const [resultsHeight, setResultsHeight] = useState(250)
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const submitRef = useRef<() => void>(() => {})

  // Tab state
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([QUERY_TAB])
  const [activeTab, setActiveTab] = useState('__query__')
  const [tabContextMenu, setTabContextMenu] = useState<{ x: number; y: number; tabId: string } | null>(null)
  const [tabOverflowOpen, setTabOverflowOpen] = useState(false)

  const statusBarLabel = openTabs.find(t => t.id === activeTab)?.id === '__query__' ? 'Query' : activeTab
  const statusBarLeft = useMemo(() => <span className="status-bar-item">{statusBarLabel}</span>, [statusBarLabel])
  const statusBarRight = useMemo(() => status ? <span className={`status-bar-item status-${status}`}>{status}</span> : null, [status])
  useStatusBarEffect(statusBarLeft, statusBarRight)

  const [searchParams, setSearchParams] = useSearchParams()

  // Load job from URL params
  useEffect(() => {
    const urlJobId = searchParams.get('job_id')
    if (urlJobId && urlJobId !== jobId) {
      setJobId(urlJobId)
      loadJob(urlJobId)
    }
  }, [searchParams])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  // Tick elapsed time while query is active
  useEffect(() => {
    if (!(status === 'pending' || status === 'provisioning' || status === 'running') || !startedAt) {
      return
    }
    const tick = () => setElapsed(formatElapsed(Date.now() - parseUTC(startedAt)))
    tick()
    const id = setInterval(tick, 100)
    return () => clearInterval(id)
  }, [status, startedAt])

  async function loadJob(id: string) {
    try {
      const job = await apiFetch<JobStatus>(`/api/queries/${id}`)
      setStatus(job.status)
      setError(job.error)
      if (job.started_at) setStartedAt(job.started_at)
      if (job.status === 'completed') {
        await fetchResults(id, 0)
      } else if (job.status === 'pending' || job.status === 'provisioning' || job.status === 'running') {
        startPolling(id)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load query')
    }
  }

  function startPolling(id: string) {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const job = await apiFetch<JobStatus>(`/api/queries/${id}`)
        setStatus(job.status)
        if (job.started_at) setStartedAt(job.started_at)
        if (job.status === 'completed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(null)
          setCatalogRefreshKey(k => k + 1)
          await fetchResults(id, 0)
        } else if (job.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(job.error)
        } else if (job.status === 'cancelled') {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(null)
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current)
      }
    }, 1000)
  }

  async function fetchResults(id: string, page: number) {
    const data = await apiFetch<QueryResult>(`/api/queries/${id}/results?page=${page}`)
    setResult(data)
  }

  function handleEditorMount(editorInstance: editor.IStandaloneCodeEditor, monaco: Monaco) {
    editorInstance.addAction({
      id: 'run-query',
      label: 'Run Query',
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
      run: () => submitRef.current(),
    })
  }

  async function handleSubmit() {
    if (!sql.trim()) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setStatus(null)
    setStartedAt(null)
    setElapsed(null)
    try {
      const { job_id } = await apiFetch<{ job_id: string }>('/api/queries', {
        method: 'POST',
        body: JSON.stringify({ sql }),
      })
      setJobId(job_id)
      setSearchParams({ job_id })
      setStatus('pending')
      startPolling(job_id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Submit failed')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleCancel() {
    if (!jobId) return
    try {
      await apiFetch(`/api/queries/${jobId}/cancel`, { method: 'POST' })
      if (pollRef.current) clearInterval(pollRef.current)
      setStatus('cancelled')
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Cancel failed')
    }
  }

  submitRef.current = handleSubmit

  async function handlePage(page: number) {
    if (!jobId) return
    await fetchResults(jobId, page)
  }

  function handleSelectObject(db: string, schema: string, name: string, objectType: string) {
    let tabId: string
    let label: string
    let tabType: OpenTab['type']
    if (objectType === 'database') {
      tabId = db
      label = db
      tabType = 'database'
    } else if (objectType === 'schema') {
      tabId = `${db}.${schema}`
      label = schema
      tabType = 'schema'
    } else {
      tabId = `${db}.${schema}.${name}`
      label = name
      tabType = 'object'
    }
    const existing = openTabs.find(t => t.id === tabId)
    if (existing) {
      setActiveTab(tabId)
      return
    }
    const newTab: OpenTab = { id: tabId, label, type: tabType, db, schema, name, objectType }
    setOpenTabs(prev => [...prev, newTab])
    setActiveTab(tabId)
  }

  function closeTab(tabId: string) {
    if (tabId === '__query__') return
    setOpenTabs(prev => prev.filter(t => t.id !== tabId))
    if (activeTab === tabId) setActiveTab('__query__')
  }

  // Tab context menu dismiss
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

  // Tab overflow dropdown dismiss
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

  function closeTabs(filter: (tab: OpenTab, idx: number) => boolean) {
    setOpenTabs(prev => {
      const next = prev.filter((t, i) => t.id === '__query__' || !filter(t, i))
      if (!next.find(t => t.id === activeTab)) {
        setActiveTab('__query__')
      }
      return next
    })
    setTabContextMenu(null)
  }

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

  const totalPages = result ? Math.ceil(result.total / result.page_size) : 0

  return (
    <div className="query-outer">
    <div className="query-layout">
      {catalogOpen && (
        <>
          <aside className="catalog-sidebar" style={{ width: catalogWidth, minWidth: catalogWidth }}>
            <div className="catalog-sidebar-header">
              <span className="catalog-sidebar-title">Catalog</span>
              <button
                className="catalog-toggle"
                onClick={() => setCatalogOpen(false)}
                title="Hide catalog"
              >
                ✕
              </button>
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
          {openTabs.map(tab => (
            <div
              key={tab.id}
              className={`query-tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
              onContextMenu={e => { e.preventDefault(); setTabContextMenu({ x: e.clientX, y: e.clientY, tabId: tab.id }) }}
            >
              <span className={`query-tab-badge query-tab-badge-${tab.type === 'object' ? tab.objectType : tab.type}`}>
                {{ query: 'Q', database: 'DB', schema: 'S', object: tab.objectType === 'view' ? 'V' : 'T' }[tab.type]}
              </span>
              <span className="query-tab-name">{tab.label}</span>
              {tab.type !== 'query' && (
                <span
                  className="query-tab-close"
                  onClick={e => { e.stopPropagation(); closeTab(tab.id) }}
                >
                  ✕
                </span>
              )}
            </div>
          ))}
          </div>
          <div className="tab-overflow-wrapper">
            <button className="tab-overflow-btn" onClick={() => setTabOverflowOpen(v => !v)} title="Open tabs">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </button>
            {tabOverflowOpen && (
              <div className="tab-overflow-dropdown">
                {openTabs.map(tab => (
                  <div
                    key={tab.id}
                    className={`tab-overflow-item${activeTab === tab.id ? ' active' : ''}`}
                    onClick={() => { setActiveTab(tab.id); setTabOverflowOpen(false) }}
                  >
                    <span className={`query-tab-badge query-tab-badge-${tab.type === 'object' ? tab.objectType : tab.type}`}>
                      {{ query: 'Q', database: 'DB', schema: 'S', object: tab.objectType === 'view' ? 'V' : 'T' }[tab.type]}
                    </span>
                    <span className="tab-overflow-item-name">{tab.label}</span>
                    {tab.type !== 'query' && (
                      <span className="tab-overflow-item-close" onClick={e => { e.stopPropagation(); closeTab(tab.id) }}>✕</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Query tab content */}
        {activeTab === '__query__' && (
          <>
            <div className="editor-area">
              {!catalogOpen && (
                <div style={{ flexShrink: 0 }}>
                  <button
                    className="catalog-toggle"
                    onClick={() => setCatalogOpen(true)}
                    title="Show catalog"
                  >
                    ☰
                  </button>
                </div>
              )}
              <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Editor
                  height="100%"
                  language="sql"
                  beforeMount={defineKolkhisTheme}
                  theme={THEME_NAME}
                  value={sql}
                  onChange={(value) => { const v = value || ''; setSql(v); localStorage.setItem('kolkhis_sql', v) }}
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
                {status === 'pending' || status === 'provisioning' || status === 'running' ? (
                  <button onClick={handleCancel}>Cancel</button>
                ) : (
                  <button onClick={handleSubmit} disabled={submitting || !sql.trim()}>
                    {submitting ? 'Submitting...' : 'Run Query'}
                  </button>
                )}
                {status && (
                  <span className={`status-${status}`}>
                    {status}{elapsed && (status === 'pending' || status === 'provisioning' || status === 'running') ? `  ${elapsed}` : ''}
                  </span>
                )}
                {jobId && status === 'completed' && (
                  <>
                    <a
                      href={`${API_URL}/api/queries/${jobId}/export`}
                      style={{ fontSize: '0.85em' }}
                    >
                      Download CSV
                    </a>
                    {!result && (
                      <a
                        href="#"
                        style={{ fontSize: '0.85em' }}
                        onClick={e => { e.preventDefault(); fetchResults(jobId, 0) }}
                      >
                        Show Results
                      </a>
                    )}
                  </>
                )}
              </div>
            </div>

            {error && (
              <pre style={{ color: '#f87171', marginTop: '1em', whiteSpace: 'pre-wrap', fontSize: '0.85em', flexShrink: 0 }}>
                {error}
              </pre>
            )}

            {result && (
              <>
              <div className="results-resize-handle" onMouseDown={handleResultsResizeMouseDown} />
              <div className="results-panel" style={{ height: resultsHeight }}>
                <div className="results-header">
                  <span>{result.total} rows</span>
                  <button className="results-close" onClick={() => setResult(null)} title="Close results">✕</button>
                </div>
                <div className="table-container">
                  <table>
                    <thead>
                      <tr>
                        {result.columns.map(col => (
                          <th key={col}>{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.map((row, i) => (
                        <tr key={i}>
                          {result.columns.map(col => (
                            <td key={col}>{String(row[col] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="pagination">
                  <button disabled={result.page === 0} onClick={() => handlePage(result.page - 1)}>
                    Prev
                  </button>
                  <span>Page {result.page + 1} of {totalPages} ({result.total} rows)</span>
                  <button disabled={result.page + 1 >= totalPages} onClick={() => handlePage(result.page + 1)}>
                    Next
                  </button>
                </div>
              </div>
              </>
            )}
          </>
        )}

        {/* Detail tabs */}
        {openTabs.filter(t => t.type !== 'query').map(tab => (
          activeTab === tab.id && (
            tab.type === 'database' ? (
              <DatabaseDetail key={tab.id} db={tab.db!} />
            ) : tab.type === 'schema' ? (
              <SchemaDetail key={tab.id} db={tab.db!} schema={tab.schema!} />
            ) : (
              <ObjectDetail key={tab.id} db={tab.db!} schema={tab.schema!} name={tab.name!} objectType={tab.objectType!} />
            )
          )
        ))}
      </div>
    </div>

    {tabContextMenu && (() => {
      const idx = openTabs.findIndex(t => t.id === tabContextMenu.tabId)
      const isQuery = tabContextMenu.tabId === '__query__'
      return (
        <div className="context-menu" style={{ left: tabContextMenu.x, top: tabContextMenu.y }}>
          {!isQuery && (
            <div className="context-menu-item" onClick={() => { closeTabs(t => t.id === tabContextMenu.tabId) }}>Close</div>
          )}
          <div className="context-menu-item" onClick={() => { closeTabs(t => t.id !== tabContextMenu.tabId) }}>Close Other Tabs</div>
          <div className="context-menu-item" onClick={() => { closeTabs(() => true) }}>Close All Tabs</div>
          <div className="context-menu-item" onClick={() => { closeTabs(() => true) }}>Close Unmodified Tabs</div>
          <div className="context-menu-separator" />
          <div className="context-menu-item" onClick={() => { closeTabs((_t, i) => i < idx) }}>Close Tabs to the Left</div>
          <div className="context-menu-item" onClick={() => { closeTabs((_t, i) => i > idx) }}>Close Tabs to the Right</div>
        </div>
      )
    })()}

    </div>
  )
}
