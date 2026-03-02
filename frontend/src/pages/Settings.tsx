import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import './Settings.css'

interface UserSettings {
  idle_timeout: number
  worker_size: string
}

interface WorkerVM {
  id: number
  status: string
  server_type: string
}

const WORKER_SIZES = [
  { value: 'cpx42', label: 'XS', specs: '8 vCPU, 16 GB' },
  { value: 'cpx62', label: 'S', specs: '16 vCPU, 32 GB' },
  { value: 'ccx43', label: 'M', specs: '16 vCPU, 64 GB' },
  { value: 'ccx53', label: 'L', specs: '32 vCPU, 128 GB' },
]

export function Settings() {
  const [idleMinutes, setIdleMinutes] = useState(15)
  const [workerSize, setWorkerSize] = useState('cpx42')
  const [savedWorkerSize, setSavedWorkerSize] = useState('cpx42')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [confirm, setConfirm] = useState<{ vmId: number } | null>(null)

  useEffect(() => {
    apiFetch<UserSettings>('/api/settings')
      .then(s => {
        setIdleMinutes(Math.round(s.idle_timeout / 60))
        setWorkerSize(s.worker_size)
        setSavedWorkerSize(s.worker_size)
      })
      .catch(() => setMessage({ text: 'Failed to load settings', error: true }))
      .finally(() => setLoading(false))
  }, [])

  async function saveSettings() {
    setSaving(true)
    setMessage(null)
    try {
      await apiFetch('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ idle_timeout: idleMinutes * 60, worker_size: workerSize }),
      })
      setSavedWorkerSize(workerSize)
      setMessage({ text: 'Settings saved', error: false })
    } catch {
      setMessage({ text: 'Failed to save settings', error: true })
    } finally {
      setSaving(false)
    }
  }

  async function handleSave() {
    setMessage(null)
    if (workerSize !== savedWorkerSize) {
      try {
        const vms = await apiFetch<WorkerVM[]>('/api/workers')
        const activeVm = vms.find(
          vm => vm.server_type !== workerSize && ['provisioning', 'ready'].includes(vm.status)
        )
        if (activeVm) {
          setConfirm({ vmId: activeVm.id })
          return
        }
      } catch {
        // If we can't check workers, just save
      }
    }
    await saveSettings()
  }

  async function handleDestroyAndSave() {
    if (!confirm) return
    setSaving(true)
    setMessage(null)
    try {
      await apiFetch(`/api/workers/${confirm.vmId}`, { method: 'DELETE' })
    } catch {
      setMessage({ text: 'Failed to destroy worker', error: true })
      setSaving(false)
      setConfirm(null)
      return
    }
    setConfirm(null)
    await saveSettings()
  }

  async function handleKeepAndSave() {
    setConfirm(null)
    await saveSettings()
  }

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  return (
    <div className="settings-page">
      <h2>Settings</h2>

      <div className="settings-section">
        <label className="settings-label" htmlFor="worker-size">
          Worker size
        </label>
        <p className="settings-hint">
          Compute size for new worker VMs.
        </p>
        <div className="settings-row">
          <select
            id="worker-size"
            value={workerSize}
            onChange={e => setWorkerSize(e.target.value)}
            className="settings-select"
          >
            {WORKER_SIZES.map(s => (
              <option key={s.value} value={s.value}>
                {s.label} — {s.specs}
              </option>
            ))}
          </select>
        </div>
      </div>

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
        </div>
      </div>

      <div className="settings-actions">
        <button onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      {message && (
        <p className={message.error ? 'settings-error' : 'settings-success'}>
          {message.text}
        </p>
      )}

      {confirm && (
        <div className="settings-modal-overlay">
          <div className="settings-modal">
            <p className="settings-modal-title">Active worker has a different size</p>
            <p className="settings-modal-text">
              You have a running worker that doesn't match the new size. What would you like to do?
            </p>
            <div className="settings-modal-actions">
              <button onClick={handleDestroyAndSave} disabled={saving}>
                Destroy now
              </button>
              <button className="secondary" onClick={handleKeepAndSave} disabled={saving}>
                Keep until idle
              </button>
              <button className="secondary" onClick={() => setConfirm(null)} disabled={saving}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
