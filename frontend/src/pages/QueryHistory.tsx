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

function formatDuration(started: string | null, completed: string | null): string {
  if (!started || !completed) return '-'
  const ms = new Date(completed).getTime() - new Date(started).getTime()
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
  const navigate = useNavigate()

  useEffect(() => {
    apiFetch<QueryJob[]>('/api/queries')
      .then(setJobs)
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  if (jobs.length === 0) {
    return (
      <div>
        <h2>Query History</h2>
        <p style={{ color: '#8888bb' }}>No queries yet.</p>
      </div>
    )
  }

  return (
    <div>
      <h2>Query History</h2>
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
                <td>{formatDuration(job.started_at, job.completed_at)}</td>
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
