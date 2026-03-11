import { useState, useEffect } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { apiFetch } from '../api'
import './CIModal.css'

interface CITask {
  id: number
  name: string
  status: string
  head_branch: string
  head_sha: string
  display_title: string
  event: string
  started_at: string | null
  completed_at: string | null
  run_number: number
}

interface CIRun {
  run_number: number
  display_title: string
  status: string
  head_branch: string
  head_sha: string
  event: string
  started_at: string | null
  completed_at: string | null
  jobs: CITask[]
}

interface CIStep {
  name: string
  status: string
  duration: string
  lines: string[]
}

interface CIModalProps {
  onClose: () => void
}

function statusIcon(status: string): string {
  if (status === 'success') return '\u2713'
  if (status === 'failure') return '\u2717'
  if (status === 'running') return '\u25CC'
  return '?'
}

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function duration(start: string | null, end: string | null): string {
  if (!start || !end) return ''
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return '<1s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

/** Aggregate status: failure > running > success */
function worstStatus(jobs: CITask[]): string {
  if (jobs.some(j => j.status === 'failure')) return 'failure'
  if (jobs.some(j => j.status === 'running')) return 'running'
  if (jobs.every(j => j.status === 'success')) return 'success'
  return jobs[0]?.status ?? 'unknown'
}

/** Group flat task list into runs by run_number */
function groupRuns(tasks: CITask[]): CIRun[] {
  const map = new Map<number, CITask[]>()
  for (const t of tasks) {
    const group = map.get(t.run_number) || []
    group.push(t)
    map.set(t.run_number, group)
  }
  const runs: CIRun[] = []
  for (const [run_number, jobs] of map) {
    const first = jobs[0]
    const starts = jobs.map(j => j.started_at).filter(Boolean) as string[]
    const ends = jobs.map(j => j.completed_at).filter(Boolean) as string[]
    runs.push({
      run_number,
      display_title: first.display_title,
      status: worstStatus(jobs),
      head_branch: first.head_branch,
      head_sha: first.head_sha,
      event: first.event,
      started_at: starts.length ? starts.sort()[0] : null,
      completed_at: ends.length ? ends.sort().reverse()[0] : null,
      jobs,
    })
  }
  runs.sort((a, b) => b.run_number - a.run_number)
  return runs
}

function StepView({ step }: { step: CIStep }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="ci-step">
      <div className="ci-step-header" onClick={() => setExpanded(!expanded)}>
        <span className="ci-step-arrow">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className={`ci-step-status ${step.status}`}>
          {statusIcon(step.status)}
        </span>
        <span className="ci-step-name">{step.name}</span>
        {step.duration && (
          <span className="ci-step-duration">{step.duration}</span>
        )}
      </div>
      {expanded && step.lines.length > 0 && (
        <pre className="ci-step-log">{step.lines.join('\n')}</pre>
      )}
    </div>
  )
}

function ModalHeader({ title, onClose, onBack }: {
  title: React.ReactNode
  onClose: () => void
  onBack?: () => void
}) {
  return (
    <div className="ci-modal-header">
      {onBack && (
        <button className="ci-log-back" onClick={onBack}>&larr;</button>
      )}
      <div className="ci-modal-header-title">{title}</div>
      <button className="ci-modal-close" onClick={onClose}>&times;</button>
    </div>
  )
}

export function CIModal({ onClose }: CIModalProps) {
  const [runs, setRuns] = useState<CIRun[]>([])
  const [loading, setLoading] = useState(true)

  const [selectedRun, setSelectedRun] = useState<CIRun | null>(null)
  const [selectedJob, setSelectedJob] = useState<CITask | null>(null)
  const [steps, setSteps] = useState<CIStep[]>([])
  const [logLoading, setLogLoading] = useState(false)
  const [logError, setLogError] = useState('')

  useEffect(() => {
    apiFetch<{ runs: CITask[] }>('/api/ci/status')
      .then(data => setRuns(groupRuns(data.runs || [])))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const openRun = (run: CIRun) => {
    if (run.jobs.length === 1) {
      openJob(run, run.jobs[0])
      return
    }
    setSelectedRun(run)
    setSelectedJob(null)
  }

  const openJob = async (run: CIRun, job: CITask) => {
    setSelectedRun(run)
    setSelectedJob(job)
    setSteps([])
    setLogError('')
    setLogLoading(true)
    try {
      const data = await apiFetch<{ steps: CIStep[] }>(`/api/ci/logs/${job.id}`)
      setSteps(data.steps || [])
    } catch {
      setLogError('Failed to load log.')
    } finally {
      setLogLoading(false)
    }
  }

  const goBack = () => {
    if (selectedJob) {
      if (selectedRun && selectedRun.jobs.length > 1) {
        setSelectedJob(null)
        setSteps([])
      } else {
        setSelectedRun(null)
        setSelectedJob(null)
        setSteps([])
      }
    } else {
      setSelectedRun(null)
    }
  }

  // Step view (level 3)
  if (selectedRun && selectedJob) {
    return (
      <div className="ci-modal-overlay" onClick={onClose}>
        <div className="ci-modal" onClick={e => e.stopPropagation()}>
          <ModalHeader
            onClose={onClose}
            onBack={goBack}
            title={<>
              <span className={`ci-run-status ${selectedJob.status}`}>
                {statusIcon(selectedJob.status)}
              </span>
              {' '}
              <span className="ci-run-name">{selectedJob.name}</span>
              {' \u2014 '}
              {selectedRun.display_title}
            </>}
          />
          <div className="ci-steps-container">
            {logLoading ? (
              <div className="ci-log-loading">Loading...</div>
            ) : logError ? (
              <div className="ci-log-loading">{logError}</div>
            ) : (
              steps.map((step, i) => <StepView key={i} step={step} />)
            )}
          </div>
        </div>
      </div>
    )
  }

  // Job list (level 2)
  if (selectedRun) {
    return (
      <div className="ci-modal-overlay" onClick={onClose}>
        <div className="ci-modal" onClick={e => e.stopPropagation()}>
          <ModalHeader
            onClose={onClose}
            onBack={goBack}
            title={<>
              <span className={`ci-run-status ${selectedRun.status}`}>
                {statusIcon(selectedRun.status)}
              </span>
              {' '}
              {selectedRun.display_title}
              {' '}
              <span className="ci-run-branch">{selectedRun.head_branch}</span>
            </>}
          />
          <ul className="ci-run-list">
            {selectedRun.jobs.map(job => (
              <li key={job.id} className="ci-run-item" onClick={() => openJob(selectedRun, job)}>
                <span className={`ci-run-status ${job.status}`}>
                  {statusIcon(job.status)}
                </span>
                <span className="ci-run-name">{job.name}</span>
                <span className="ci-run-time">
                  {duration(job.started_at, job.completed_at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    )
  }

  // Run list (level 1)
  return (
    <div className="ci-modal-overlay" onClick={onClose}>
      <div className="ci-modal" onClick={e => e.stopPropagation()}>
        <ModalHeader onClose={onClose} title="Actions" />
        {loading ? (
          <div className="ci-run-empty">Loading...</div>
        ) : runs.length === 0 ? (
          <div className="ci-run-empty">No workflow runs</div>
        ) : (
          <ul className="ci-run-list">
            {runs.map(run => (
              <li key={run.run_number} className="ci-run-item" onClick={() => openRun(run)}>
                <span className={`ci-run-status ${run.status}`}>
                  {statusIcon(run.status)}
                </span>
                <span className="ci-run-title">{run.display_title}</span>
                <span className="ci-run-branch">{run.head_branch}</span>
                <span className="ci-run-time">
                  {duration(run.started_at, run.completed_at)}
                </span>
                <span className="ci-run-time">{timeAgo(run.started_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
