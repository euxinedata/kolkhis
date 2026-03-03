import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Editor, type Monaco } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import { apiFetch, API_URL } from '../api'
import { CatalogPanel } from '../components/CatalogPanel'

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

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

interface ObjectSchema {
  type: string
  columns: ColumnInfo[]
  sql?: string
}

interface OpenTab {
  id: string
  label: string
  type: 'query' | 'object'
  db?: string
  schema?: string
  name?: string
  objectType?: string
}

const QUERY_TAB: OpenTab = { id: '__query__', label: 'Query', type: 'query' }

function ObjectDetail({ db, schema, name, objectType }: { db: string; schema: string; name: string; objectType: string }) {
  const [data, setData] = useState<ObjectSchema | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<ObjectSchema>(
      `/api/catalog/databases/${db}/schemas/${schema}/objects/${name}/schema`
    ).then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [db, schema, name])

  if (loading) return <div className="object-detail"><span style={{ color: '#8888bb' }}>Loading...</span></div>
  if (!data) return <div className="object-detail"><span style={{ color: '#f87171' }}>Failed to load schema</span></div>

  const badge = objectType === 'view'
    ? { label: 'V', cls: 'tree-icon-view' }
    : { label: 'T', cls: 'tree-icon-table' }

  return (
    <div className="object-detail">
      <div className="object-detail-header">
        <span className={`tree-type-badge ${badge.cls}`}>{badge.label}</span>
        <span className="object-detail-name">{db}.{schema}.{name}</span>
      </div>
      {data.type === 'view' && data.sql && (
        <pre className="object-detail-sql">{data.sql}</pre>
      )}
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Column</th>
              <th className="tree-col-type">Type</th>
            </tr>
          </thead>
          <tbody>
            {data.columns.map(col => (
              <tr key={col.name}>
                <td className="tree-col-name">{col.name}</td>
                <td className="tree-col-type">{col.type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

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
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const submitRef = useRef<() => void>(() => {})

  // Tab state
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([QUERY_TAB])
  const [activeTab, setActiveTab] = useState('__query__')

  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches

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
    const tabId = `${db}.${schema}.${name}`
    const existing = openTabs.find(t => t.id === tabId)
    if (existing) {
      setActiveTab(tabId)
      return
    }
    const newTab: OpenTab = { id: tabId, label: name, type: 'object', db, schema, name, objectType }
    setOpenTabs(prev => [...prev, newTab])
    setActiveTab(tabId)
  }

  function closeTab(tabId: string) {
    if (tabId === '__query__') return
    setOpenTabs(prev => prev.filter(t => t.id !== tabId))
    if (activeTab === tabId) setActiveTab('__query__')
  }

  const totalPages = result ? Math.ceil(result.total / result.page_size) : 0

  return (
    <div className="query-layout">
      {catalogOpen && (
        <aside className="catalog-sidebar">
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
      )}
      <div className="query-main">
        {/* Tab bar */}
        <div className="query-tabs">
          {openTabs.map(tab => (
            <div
              key={tab.id}
              className={`query-tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="query-tab-name">{tab.label}</span>
              {tab.type === 'object' && (
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
              <div style={{ flex: 1, minHeight: 0, border: '1px solid #444', borderRadius: '4px', overflow: 'hidden' }}>
                <Editor
                  height="100%"
                  language="sql"
                  theme={prefersDark ? 'vs-dark' : 'vs'}
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
              <div style={{ display: 'flex', gap: '1em', padding: '1em 0', alignItems: 'center', flexShrink: 0 }}>
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
                  <a
                    href={`${API_URL}/api/queries/${jobId}/export`}
                    style={{ fontSize: '0.85em' }}
                  >
                    Download CSV
                  </a>
                )}
              </div>
            </div>

            {error && (
              <pre style={{ color: '#f87171', marginTop: '1em', whiteSpace: 'pre-wrap', fontSize: '0.85em', flexShrink: 0 }}>
                {error}
              </pre>
            )}

            {result && (
              <div className="results-panel">
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
            )}
          </>
        )}

        {/* Object detail tabs */}
        {openTabs.filter(t => t.type === 'object').map(tab => (
          activeTab === tab.id && (
            <ObjectDetail
              key={tab.id}
              db={tab.db!}
              schema={tab.schema!}
              name={tab.name!}
              objectType={tab.objectType!}
            />
          )
        ))}
      </div>
    </div>
  )
}
