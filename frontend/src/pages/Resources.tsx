import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api'

interface WorkerVM {
  id: number
  status: string
  server_type: string
  created_at: string | null
  last_query_at: string | null
}

export default function Resources() {
  const [vms, setVms] = useState<WorkerVM[]>([])
  const [loading, setLoading] = useState(true)
  const [confirmVmId, setConfirmVmId] = useState<number | null>(null)
  const [destroying, setDestroying] = useState<number | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function fetchWorkers() {
    return apiFetch<WorkerVM[]>('/api/workers').then(setVms)
  }

  useEffect(() => {
    fetchWorkers().finally(() => setLoading(false))
    pollRef.current = setInterval(fetchWorkers, 5000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  async function handleShutdown(vmId: number) {
    setConfirmVmId(null)
    setDestroying(vmId)
    try {
      await apiFetch(`/api/workers/${vmId}`, { method: 'DELETE' })
      await fetchWorkers()
    } finally {
      setDestroying(null)
    }
  }

  if (loading) {
    return <div className="resources-page"><p style={{ color: '#8888bb' }}>Loading...</p></div>
  }

  if (vms.length === 0) {
    return <div className="resources-page"><p style={{ color: '#8888bb' }}>No active resources</p></div>
  }

  return (
    <div className="resources-page">
      <table>
        <thead>
          <tr>
            <th>Server Type</th>
            <th>Status</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {vms.map(vm => (
            <tr key={vm.id}>
              <td style={{ fontFamily: 'monospace', fontSize: '0.85em' }}>{vm.server_type}</td>
              <td><span className={`status-${vm.status}`}>{vm.status}</span></td>
              <td style={{ fontSize: '0.85em', color: '#8888bb' }}>
                {vm.created_at ? new Date(vm.created_at).toLocaleString() : '-'}
              </td>
              <td>
                {destroying === vm.id ? (
                  <span style={{ color: '#8888bb', fontSize: '0.85em' }}>shutting down...</span>
                ) : (
                  <button className="vm-shutdown-btn" onClick={() => setConfirmVmId(vm.id)}>
                    Shutdown
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {confirmVmId !== null && (
        <div className="modal-overlay" onClick={() => setConfirmVmId(null)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <p>Shut down this worker VM?</p>
            <div className="modal-actions">
              <button className="modal-btn-cancel" onClick={() => setConfirmVmId(null)}>Cancel</button>
              <button className="modal-btn-confirm" onClick={() => handleShutdown(confirmVmId)}>Shutdown</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
