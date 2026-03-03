import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api'
import './Engineering.css'

interface Project {
  id: string
  name: string
  description: string
  created_at: string
}

export function Engineering() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [errorModal, setErrorModal] = useState<string | null>(null)

  // Create modal state
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [creating, setCreating] = useState(false)

  // Delete confirmation state
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  function fetchProjects() {
    return apiFetch<Project[]>('/api/projects').then(setProjects)
  }

  useEffect(() => {
    fetchProjects()
      .catch(() => setErrorModal('Failed to load projects'))
      .finally(() => setLoading(false))
  }, [])

  async function handleCreate() {
    if (!newName.trim()) return
    setCreating(true)
    try {
      await apiFetch('/api/projects', {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim(), description: newDesc.trim() }),
      })
      setShowCreate(false)
      setNewName('')
      setNewDesc('')
      await fetchProjects()
    } catch (e) {
      const msg = e instanceof Error ? e.message : ''
      if (msg.includes('409') || msg.includes('already exists')) {
        setErrorModal(`A project named "${newName.trim()}" already exists. Please choose a different name.`)
      } else {
        setErrorModal(msg || 'Failed to create project')
      }
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(id: string) {
    setConfirmDeleteId(null)
    setDeleting(id)
    try {
      await apiFetch(`/api/projects/${id}`, { method: 'DELETE' })
      await fetchProjects()
    } catch (e) {
      setErrorModal(e instanceof Error ? e.message : 'Failed to delete project')
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="engineering-page">
      <EngTabs active="Projects" />

      <div className="eng-content">
        {loading && <p style={{ color: '#8888bb' }}>Loading...</p>}

        {!loading && (
          <>
            <div className="eng-toolbar">
              <button className="eng-new-btn" onClick={() => setShowCreate(true)}>
                New Project
              </button>
            </div>

            {projects.length === 0 ? (
              <p style={{ color: '#8888bb', fontSize: '0.85em' }}>No projects yet.</p>
            ) : (
              <div className="project-grid">
                {projects.map(p => (
                  <div
                    key={p.id}
                    className="project-card"
                    onClick={() => navigate(`/engineering/editor/${p.id}`)}
                  >
                    <div className="project-card-header">
                      <span className="project-card-name">{p.name}</span>
                      {deleting === p.id ? (
                        <span style={{ color: '#8888bb', fontSize: '0.8em' }}>deleting...</span>
                      ) : (
                        <button
                          className="project-delete-btn"
                          onClick={e => { e.stopPropagation(); setConfirmDeleteId(p.id) }}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                    {p.description && (
                      <p className="project-card-desc">{p.description}</p>
                    )}
                    <span className="project-card-date">
                      {new Date(p.created_at).toLocaleDateString('en-GB', {
                        day: 'numeric', month: 'short', year: 'numeric',
                      })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 1em', color: '#ccc' }}>New Project</h3>
            <div className="eng-form-field">
              <label>Name</label>
              <input
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="my-project"
                autoFocus
              />
            </div>
            <div className="eng-form-field">
              <label>Description</label>
              <input
                value={newDesc}
                onChange={e => setNewDesc(e.target.value)}
                placeholder="Optional description"
              />
            </div>
            <div className="modal-actions">
              <button className="modal-btn-cancel" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button
                className="eng-create-btn"
                onClick={handleCreate}
                disabled={!newName.trim() || creating}
              >
                {creating ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Error modal */}
      {errorModal !== null && (
        <div className="modal-overlay" onClick={() => setErrorModal(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 0.75em', color: '#f87171', fontSize: '0.95em' }}>Error</h3>
            <p>{errorModal}</p>
            <div className="modal-actions">
              <button className="modal-btn-cancel" onClick={() => setErrorModal(null)}>
                OK
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {confirmDeleteId !== null && (
        <div className="modal-overlay" onClick={() => setConfirmDeleteId(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <p>Delete this project? This will also delete the Gitea repository.</p>
            <div className="modal-actions">
              <button className="modal-btn-cancel" onClick={() => setConfirmDeleteId(null)}>
                Cancel
              </button>
              <button className="modal-btn-confirm" onClick={() => handleDelete(confirmDeleteId)}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// Shared tab bar used by both Engineering and ProjectEditor
const TABS = ['Projects', 'Editor', 'Pipelines'] as const
type TabName = typeof TABS[number]

export function EngTabs({ active }: { active: TabName }) {
  return (
    <div className="eng-tabs">
      {TABS.map(tab => (
        <button
          key={tab}
          className={`eng-tab ${tab === active ? 'active' : ''}`}
          disabled={tab !== active && tab !== 'Projects'}
        >
          {tab}
        </button>
      ))}
    </div>
  )
}
