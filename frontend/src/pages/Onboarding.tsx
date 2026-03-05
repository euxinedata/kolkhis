import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import kolkhisLogo from '../assets/kolkhis-large.svg'
import './Onboarding.css'

interface Org {
  id: string
  name: string
}

interface OnboardingProps {
  onComplete: () => void
}

export function Onboarding({ onComplete }: OnboardingProps) {
  const [mode, setMode] = useState<'choose' | 'create' | 'join'>('choose')
  const [orgs, setOrgs] = useState<Org[]>([])
  const [orgName, setOrgName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [joinedOrgName, setJoinedOrgName] = useState('')

  useEffect(() => {
    if (mode === 'join') {
      apiFetch<Org[]>('/api/orgs').then(setOrgs).catch(() => setOrgs([]))
    }
  }, [mode])

  const handleCreate = async () => {
    if (!orgName.trim()) return
    setError('')
    setLoading(true)
    try {
      const org = await apiFetch<{ id: string }>('/api/orgs', {
        method: 'POST',
        body: JSON.stringify({ name: orgName.trim() }),
      })
      // Switch to the new org to get a token with org_id
      await apiFetch('/auth/switch-org', {
        method: 'POST',
        body: JSON.stringify({ org_id: org.id }),
      })
      onComplete()
    } catch (e: any) {
      const msg = e.message || 'Failed to create organization'
      try {
        const parsed = JSON.parse(msg)
        setError(parsed.detail || msg)
      } catch {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  const handleJoin = async (org: Org) => {
    setError('')
    setLoading(true)
    try {
      await apiFetch(`/api/orgs/${org.id}/join`, { method: 'POST' })
      setJoinedOrgName(org.name)
    } catch (e: any) {
      const msg = e.message || 'Failed to join'
      try {
        const parsed = JSON.parse(msg)
        setError(parsed.detail || msg)
      } catch {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  if (joinedOrgName) {
    return (
      <div className="onboarding">
        <img src={kolkhisLogo} alt="Kolkhis" className="onboarding-logo" />
        <h2>Request Submitted</h2>
        <p className="onboarding-muted">
          Your request to join <strong>{joinedOrgName}</strong> is pending approval.
          You'll get access once an admin approves your membership.
        </p>
      </div>
    )
  }

  if (mode === 'choose') {
    return (
      <div className="onboarding">
        <img src={kolkhisLogo} alt="Kolkhis" className="onboarding-logo" />
        <h2>Welcome to Kolkhis</h2>
        <p className="onboarding-muted">Get started by creating or joining an organization.</p>
        <div className="onboarding-choices">
          <button className="onboarding-choice" onClick={() => setMode('create')}>
            <span className="choice-title">Create Organization</span>
            <span className="choice-desc">Start a new team workspace</span>
          </button>
          <button className="onboarding-choice" onClick={() => setMode('join')}>
            <span className="choice-title">Join Organization</span>
            <span className="choice-desc">Request to join an existing team</span>
          </button>
        </div>
      </div>
    )
  }

  if (mode === 'create') {
    return (
      <div className="onboarding">
        <img src={kolkhisLogo} alt="Kolkhis" className="onboarding-logo" />
        <h2>Create Organization</h2>
        <div className="onboarding-form">
          <input
            type="text"
            placeholder="Organization name"
            value={orgName}
            onChange={e => setOrgName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleCreate()}
            autoFocus
          />
          {error && <p className="onboarding-error">{error}</p>}
          <div className="onboarding-actions">
            <button className="btn-secondary" onClick={() => { setMode('choose'); setError('') }}>
              Back
            </button>
            <button onClick={handleCreate} disabled={loading || !orgName.trim()}>
              {loading ? 'Creating...' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  // mode === 'join'
  return (
    <div className="onboarding">
      <img src={kolkhisLogo} alt="Kolkhis" className="onboarding-logo" />
      <h2>Join Organization</h2>
      {error && <p className="onboarding-error">{error}</p>}
      {orgs.length === 0 ? (
        <p className="onboarding-muted">No organizations available yet.</p>
      ) : (
        <div className="onboarding-org-list">
          {orgs.map(org => (
            <button
              key={org.id}
              className="onboarding-org-item"
              onClick={() => handleJoin(org)}
              disabled={loading}
            >
              {org.name}
            </button>
          ))}
        </div>
      )}
      <div className="onboarding-actions">
        <button className="btn-secondary" onClick={() => { setMode('choose'); setError('') }}>
          Back
        </button>
      </div>
    </div>
  )
}
