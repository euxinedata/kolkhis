import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import './Billing.css'

interface LineItem {
  server_type: string
  display_name: string
  seconds: number
  hours: number
  hourly_rate_cents: number
  cost_cents: number
}

interface BillingSummary {
  period_start: string
  period_end: string
  compute_seconds: number
  compute_cost_cents: number
  storage_cost_cents: number
  total_cost_cents: number
  line_items: LineItem[]
}

function centsToEur(cents: number): string {
  return (cents / 100).toFixed(2)
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function formatMonth(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    month: 'long',
    year: 'numeric',
  })
}

export function Billing() {
  const [current, setCurrent] = useState<BillingSummary | null>(null)
  const [history, setHistory] = useState<BillingSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<BillingSummary>('/api/billing/current'),
      apiFetch<BillingSummary[]>('/api/billing/history'),
    ])
      .then(([cur, hist]) => {
        setCurrent(cur)
        setHistory(hist)
      })
      .catch(() => setError('Failed to load billing data'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>
  if (error) return <p style={{ color: '#f87171' }}>{error}</p>
  if (!current) return null

  return (
    <div className="billing-page">
      <h2>Billing</h2>
      <p className="billing-period-label">
        {formatDate(current.period_start)} – {formatDate(current.period_end)}
      </p>

      <p className="billing-total">€{centsToEur(current.total_cost_cents)}</p>
      <p className="billing-total-label">Current period total</p>

      {current.line_items.length > 0 && (
        <table className="billing-table">
          <thead>
            <tr>
              <th>Server type</th>
              <th className="num">Hours</th>
              <th className="num">Rate (€/hr)</th>
              <th className="num">Subtotal</th>
            </tr>
          </thead>
          <tbody>
            {current.line_items.map((item) => (
              <tr key={item.server_type}>
                <td>{item.display_name} ({item.server_type})</td>
                <td className="num">{item.hours}</td>
                <td className="num">€{centsToEur(item.hourly_rate_cents)}</td>
                <td className="num">€{centsToEur(item.cost_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {current.line_items.length === 0 && (
        <p style={{ color: '#8888bb', fontSize: '0.85em' }}>No compute usage this period.</p>
      )}

      <div className="billing-section">
        <h3>Past months</h3>
        {history.length === 0 && (
          <p style={{ color: '#8888bb', fontSize: '0.85em' }}>No prior billing history.</p>
        )}
        {history.map((period) => (
          <div key={period.period_start} className="billing-history-row">
            <span className="billing-history-period">{formatMonth(period.period_start)}</span>
            <span className="billing-history-cost">€{centsToEur(period.total_cost_cents)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
