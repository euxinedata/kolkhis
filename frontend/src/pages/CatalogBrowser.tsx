import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

export function CatalogBrowser() {
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [selectedNs, setSelectedNs] = useState<string | null>(null)
  const [tables, setTables] = useState<string[]>([])
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [schema, setSchema] = useState<ColumnInfo[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch<string[]>('/api/catalog/namespaces')
      .then(setNamespaces)
      .finally(() => setLoading(false))
  }, [])

  async function selectNamespace(ns: string) {
    setSelectedNs(ns)
    setSelectedTable(null)
    setSchema(null)
    const tbl = await apiFetch<string[]>(`/api/catalog/namespaces/${ns}/tables`)
    setTables(tbl)
  }

  async function selectTable(table: string) {
    if (!selectedNs) return
    setSelectedTable(table)
    const data = await apiFetch<{ columns: ColumnInfo[] }>(
      `/api/catalog/tables/${selectedNs}/${table}/schema`
    )
    setSchema(data.columns)
  }

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  return (
    <div>
      <h2>Catalog Browser</h2>

      <div style={{ display: 'flex', gap: '2em', marginTop: '1em' }}>
        {/* Namespaces */}
        <div style={{ minWidth: '180px' }}>
          <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>Namespaces</h3>
          {namespaces.length === 0 ? (
            <p style={{ fontSize: '0.85em', color: '#666' }}>No namespaces</p>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {namespaces.map(ns => (
                <li
                  key={ns}
                  onClick={() => selectNamespace(ns)}
                  style={{
                    padding: '0.3em 0.6em',
                    cursor: 'pointer',
                    borderRadius: '4px',
                    backgroundColor: selectedNs === ns ? '#2a2a4a' : 'transparent',
                    color: selectedNs === ns ? '#646cff' : 'inherit',
                    fontSize: '0.9em',
                  }}
                >
                  {ns}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Tables */}
        {selectedNs && (
          <div style={{ minWidth: '180px' }}>
            <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
              Tables in {selectedNs}
            </h3>
            {tables.length === 0 ? (
              <p style={{ fontSize: '0.85em', color: '#666' }}>No tables</p>
            ) : (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {tables.map(t => (
                  <li
                    key={t}
                    onClick={() => selectTable(t)}
                    style={{
                      padding: '0.3em 0.6em',
                      cursor: 'pointer',
                      borderRadius: '4px',
                      backgroundColor: selectedTable === t ? '#2a2a4a' : 'transparent',
                      color: selectedTable === t ? '#646cff' : 'inherit',
                      fontSize: '0.9em',
                    }}
                  >
                    {t}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Schema */}
        {schema && selectedTable && (
          <div style={{ flex: 1 }}>
            <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
              {selectedNs}.{selectedTable} schema
            </h3>
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Required</th>
                </tr>
              </thead>
              <tbody>
                {schema.map(col => (
                  <tr key={col.name}>
                    <td style={{ fontFamily: 'monospace' }}>{col.name}</td>
                    <td>{col.type}</td>
                    <td>{col.required ? 'Yes' : 'No'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
