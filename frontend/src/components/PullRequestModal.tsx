import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import './PullRequestModal.css'

interface BranchInfo {
  branch: string
  is_main: boolean
  pushed: boolean
}

interface OpenPR {
  number: number
  title: string
  head: string
  state: string
  user: string
  url: string
}

interface PullRequestModalProps {
  onClose: () => void
  branchInfo: BranchInfo | null
}

function titleFromBranch(branch: string): string {
  // feature/add-customer-model → Add customer model
  const name = branch.replace(/^(feature|fix|hotfix|bugfix|chore|refactor)\//, '')
  const words = name.replace(/[-_]/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function PullRequestModal({ onClose, branchInfo }: PullRequestModalProps) {
  const canCreate = branchInfo && !branchInfo.is_main && branchInfo.pushed
  const [tab, setTab] = useState<'new' | 'open'>(canCreate ? 'new' : 'open')
  const [title, setTitle] = useState(branchInfo ? titleFromBranch(branchInfo.branch) : '')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState<string | null>(null)
  const [prs, setPrs] = useState<OpenPR[]>([])
  const [loadingPrs, setLoadingPrs] = useState(true)

  useEffect(() => {
    apiFetch<OpenPR[]>('/api/pr/list')
      .then(setPrs)
      .catch(() => {})
      .finally(() => setLoadingPrs(false))
  }, [])

  const handleCreate = async () => {
    if (!title.trim()) return
    setCreating(true)
    setError('')
    try {
      const result = await apiFetch<{ number: number; title: string }>('/api/pr/create', {
        method: 'POST',
        body: JSON.stringify({ title: title.trim() }),
      })
      setSuccess(`PR #${result.number} created`)
      setTimeout(onClose, 1500)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create PR'
      // Try to parse JSON detail from ApiError
      try {
        const parsed = JSON.parse(msg)
        setError(parsed.detail || msg)
      } catch {
        setError(msg)
      }
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !creating && title.trim()) {
      handleCreate()
    }
  }

  return (
    <div className="pr-modal-overlay" onClick={onClose}>
      <div className="pr-modal" onClick={e => e.stopPropagation()}>
        {success ? (
          <div className="pr-success">{success}</div>
        ) : (
          <>
            <div className="pr-modal-tabs">
              <div
                className={`pr-modal-tab ${tab === 'new' ? 'active' : ''}`}
                onClick={() => setTab('new')}
              >New</div>
              <div
                className={`pr-modal-tab ${tab === 'open' ? 'active' : ''}`}
                onClick={() => setTab('open')}
              >Open PRs</div>
            </div>

            {tab === 'new' ? (
              <div>
                {branchInfo?.is_main ? (
                  <div className="pr-info">Switch to a feature branch first</div>
                ) : branchInfo && !branchInfo.pushed ? (
                  <>
                    <div className="pr-branch-line">
                      <span className="pr-branch-name">{branchInfo.branch}</span>
                      {' \u2192 main'}
                    </div>
                    <div className="pr-info">Push your branch first</div>
                  </>
                ) : branchInfo ? (
                  <>
                    <div className="pr-branch-line">
                      <span className="pr-branch-name">{branchInfo.branch}</span>
                      {' \u2192 main'}
                    </div>
                    <div className="pr-title-label">Title</div>
                    <input
                      className="pr-title-input"
                      value={title}
                      onChange={e => setTitle(e.target.value)}
                      onKeyDown={handleKeyDown}
                      autoFocus
                    />
                    {error && <div className="pr-error">{error}</div>}
                    <div className="pr-actions">
                      <button className="pr-btn-cancel" onClick={onClose}>Cancel</button>
                      <button
                        className="pr-btn-create"
                        onClick={handleCreate}
                        disabled={creating || !title.trim()}
                      >
                        {creating ? 'Creating...' : 'Create PR'}
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="pr-info">Loading branch info...</div>
                )}
              </div>
            ) : (
              <div>
                {loadingPrs ? (
                  <div className="pr-list-empty">Loading...</div>
                ) : prs.length === 0 ? (
                  <div className="pr-list-empty">No open pull requests</div>
                ) : (
                  <ul className="pr-list">
                    {prs.map(pr => (
                      <li
                        key={pr.number}
                        className="pr-list-item"
                        onClick={() => {
                          if (pr.url) window.open(pr.url, '_blank')
                        }}
                        title={`Open PR #${pr.number} in Gitea`}
                      >
                        <span className="pr-list-number">#{pr.number}</span>
                        <span className="pr-list-title">{pr.title}</span>
                        <span className="pr-list-branch">{pr.head}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
