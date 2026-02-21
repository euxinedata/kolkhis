import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiFetch, API_URL } from '../api'

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
}

export function QueryEditor() {
  const [sql, setSql] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

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

  async function loadJob(id: string) {
    try {
      const job = await apiFetch<JobStatus>(`/api/queries/${id}`)
      setStatus(job.status)
      setError(job.error)
      if (job.status === 'completed') {
        await fetchResults(id, 0)
      } else if (job.status === 'pending' || job.status === 'running') {
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
        if (job.status === 'completed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(null)
          await fetchResults(id, 0)
        } else if (job.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(job.error)
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

  async function handleSubmit() {
    if (!sql.trim()) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setStatus(null)
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

  async function handlePage(page: number) {
    if (!jobId) return
    await fetchResults(jobId, page)
  }

  const totalPages = result ? Math.ceil(result.total / result.page_size) : 0

  return (
    <div>
      <h2>Query Editor</h2>
      <textarea
        value={sql}
        onChange={e => setSql(e.target.value)}
        placeholder="SELECT * FROM namespace.table"
        rows={6}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
      />
      <div style={{ display: 'flex', gap: '1em', marginTop: '0.5em', alignItems: 'center' }}>
        <button onClick={handleSubmit} disabled={submitting || !sql.trim()}>
          {submitting ? 'Submitting...' : 'Run Query'}
        </button>
        {status && (
          <span className={`status-${status}`}>
            {status}
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

      {error && (
        <pre style={{ color: '#f87171', marginTop: '1em', whiteSpace: 'pre-wrap', fontSize: '0.85em' }}>
          {error}
        </pre>
      )}

      {result && (
        <>
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
        </>
      )}
    </div>
  )
}
