import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api'

interface QueryJob {
  id: string
  sql: string
  status: string
  error: string | null
  row_count: number | null
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

function parseUTC(ts: string): number {
  return new Date(ts.endsWith('Z') ? ts : ts + 'Z').getTime()
}

function formatDuration(started: string | null, completed: string | null, status?: string): string {
  if (!started) return '-'
  const end = completed
    ? parseUTC(completed)
    : (status === 'pending' || status === 'running') ? Date.now() : null
  if (end === null) return '-'
  const ms = end - parseUTC(started)
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function truncateSql(sql: string, max = 80): string {
  const oneline = sql.replace(/\s+/g, ' ').trim()
  return oneline.length > max ? oneline.slice(0, max) + '...' : oneline
}

export function QueryHistory() {
  const [jobs, setJobs] = useState<QueryJob[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [, setTick] = useState(0)
  const navigate = useNavigate()

  function fetchJobs() {
    return apiFetch<QueryJob[]>('/api/queries').then(setJobs)
  }

  useEffect(() => {
    fetchJobs().finally(() => setLoading(false))
  }, [])

  function handleRefresh() {
    setRefreshing(true)
    fetchJobs().finally(() => setRefreshing(false))
  }

  // Re-render every second while any job is still active
  useEffect(() => {
    const hasActive = jobs.some(j => j.status === 'pending' || j.status === 'running')
    if (!hasActive) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [jobs])

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  if (jobs.length === 0) {
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1em' }}>
          <h2>Query History</h2>
          <button onClick={handleRefresh} disabled={refreshing}>
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <p style={{ color: '#8888bb' }}>No queries yet.</p>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1em' }}>
        <h2>Query History</h2>
        <button onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>SQL</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Rows</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(job => (
              <tr
                key={job.id}
                onClick={() => navigate(`/?job_id=${job.id}`)}
                style={{ cursor: 'pointer' }}
              >
                <td style={{ fontFamily: 'monospace', fontSize: '0.85em' }}>
                  {truncateSql(job.sql)}
                </td>
                <td>
                  <span className={`status-${job.status}`}>{job.status}</span>
                </td>
                <td>{formatDuration(job.started_at, job.completed_at, job.status)}</td>
                <td>{job.row_count ?? '-'}</td>
                <td style={{ fontSize: '0.85em', color: '#8888bb' }}>
                  {job.created_at ? new Date(job.created_at).toLocaleString() : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
