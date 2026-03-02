import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import './Settings.css'

interface UserSettings {
  idle_timeout: number
}

export function Settings() {
  const [idleMinutes, setIdleMinutes] = useState(15)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    apiFetch<UserSettings>('/api/settings')
      .then(s => setIdleMinutes(Math.round(s.idle_timeout / 60)))
      .catch(() => setMessage({ text: 'Failed to load settings', error: true }))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaving(true)
    setMessage(null)
    try {
      await apiFetch('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ idle_timeout: idleMinutes * 60 }),
      })
      setMessage({ text: 'Settings saved', error: false })
    } catch {
      setMessage({ text: 'Failed to save settings', error: true })
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  return (
    <div className="settings-page">
      <h2>Settings</h2>
      <div className="settings-section">
        <label className="settings-label" htmlFor="idle-timeout">
          Worker VM idle timeout (minutes)
        </label>
        <p className="settings-hint">
          VMs with no query activity will be shut down after this period.
        </p>
        <div className="settings-row">
          <input
            id="idle-timeout"
            type="number"
            min={1}
            max={120}
            value={idleMinutes}
            onChange={e => setIdleMinutes(Number(e.target.value))}
            className="settings-input"
          />
          <button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
        {message && (
          <p className={message.error ? 'settings-error' : 'settings-success'}>
            {message.text}
          </p>
        )}
      </div>
    </div>
  )
}
