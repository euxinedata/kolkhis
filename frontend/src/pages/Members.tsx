import { useState, useEffect } from 'react'
import { useAuth } from '../auth'
import { apiFetch } from '../api'
import './Members.css'

interface Member {
  user_id: number
  name: string
  email: string
  role: string
  status: string
}

export function Members() {
  const { user } = useAuth()
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const isAdmin = user?.org_role === 'admin'

  const loadMembers = () => {
    if (!user?.org_id) return
    apiFetch<Member[]>(`/api/orgs/${user.org_id}/members`)
      .then(setMembers)
      .catch(() => setError('Failed to load members'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadMembers() }, [user?.org_id])

  const handleApprove = async (userId: number) => {
    try {
      await apiFetch(`/api/orgs/${user!.org_id}/members/${userId}/approve`, {
        method: 'POST',
      })
      loadMembers()
    } catch {
      setError('Failed to approve member')
    }
  }

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  const pending = members.filter(m => m.status === 'pending')
  const active = members.filter(m => m.status === 'active')

  return (
    <div className="members-page">
      <h2>Members</h2>

      {error && <p className="members-error">{error}</p>}

      {isAdmin && pending.length > 0 && (
        <div className="members-section">
          <h3 className="members-section-title">Pending Approval</h3>
          <div className="members-list">
            {pending.map(m => (
              <div key={m.user_id} className="member-row pending">
                <div className="member-info">
                  <span className="member-name">{m.name}</span>
                  <span className="member-email">{m.email}</span>
                </div>
                <button className="btn-approve" onClick={() => handleApprove(m.user_id)}>
                  Approve
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="members-section">
        <h3 className="members-section-title">Active</h3>
        <div className="members-list">
          {active.map(m => (
            <div key={m.user_id} className="member-row">
              <div className="member-info">
                <span className="member-name">{m.name}</span>
                <span className="member-email">{m.email}</span>
              </div>
              <span className="member-role">{m.role}</span>
            </div>
          ))}
          {active.length === 0 && (
            <p className="members-empty">No active members yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}
