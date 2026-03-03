import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

interface ObjectSchema {
  type: string
  columns: ColumnInfo[]
  sql?: string
}

interface CatalogObjectInfo {
  name: string
  type: string
}

export function DatabaseDetail({ db }: { db: string }) {
  const [schemas, setSchemas] = useState<{ name: string }[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<{ name: string }[]>(`/api/catalog/databases/${db}/schemas`)
      .then(d => { setSchemas(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [db])

  if (loading) return <div className="object-detail"><span style={{ color: '#8888bb' }}>Loading...</span></div>
  if (!schemas) return <div className="object-detail"><span style={{ color: '#f87171' }}>Failed to load database</span></div>

  return (
    <div className="object-detail">
      <div className="object-detail-header">
        <img className="catalog-detail-icon" src="/file-icons/database.svg" alt="" />
        <span className="object-detail-name">{db}</span>
      </div>
      <div className="object-detail-section">
        <span className="object-detail-label">{schemas.length} {schemas.length === 1 ? 'schema' : 'schemas'}</span>
      </div>
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr><th className="tree-col-name">Schema</th></tr>
          </thead>
          <tbody>
            {schemas.map(s => (
              <tr key={s.name}><td className="tree-col-name">{s.name}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function SchemaDetail({ db, schema }: { db: string; schema: string }) {
  const [objects, setObjects] = useState<CatalogObjectInfo[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<CatalogObjectInfo[]>(`/api/catalog/databases/${db}/schemas/${schema}/objects`)
      .then(d => { setObjects(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [db, schema])

  if (loading) return <div className="object-detail"><span style={{ color: '#8888bb' }}>Loading...</span></div>
  if (!objects) return <div className="object-detail"><span style={{ color: '#f87171' }}>Failed to load schema</span></div>

  const tables = objects.filter(o => o.type === 'table')
  const views = objects.filter(o => o.type === 'view')

  return (
    <div className="object-detail">
      <div className="object-detail-header">
        <img className="catalog-detail-icon" src="/file-icons/folder-blue.svg" alt="" />
        <span className="object-detail-name">{db}.{schema}</span>
      </div>
      <div className="object-detail-section">
        <span className="object-detail-label">{tables.length} {tables.length === 1 ? 'table' : 'tables'}, {views.length} {views.length === 1 ? 'view' : 'views'}</span>
      </div>
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Object</th>
              <th className="tree-col-type">Type</th>
            </tr>
          </thead>
          <tbody>
            {objects.map(o => (
              <tr key={o.name}>
                <td className="tree-col-name">{o.name}</td>
                <td className="tree-col-type">{o.type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ObjectDetail({ db, schema, name, objectType }: { db: string; schema: string; name: string; objectType: string }) {
  const [data, setData] = useState<ObjectSchema | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<ObjectSchema>(
      `/api/catalog/databases/${db}/schemas/${schema}/objects/${name}/schema`
    ).then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [db, schema, name])

  if (loading) return <div className="object-detail"><span style={{ color: '#8888bb' }}>Loading...</span></div>
  if (!data) return <div className="object-detail"><span style={{ color: '#f87171' }}>Failed to load schema</span></div>

  const badge = objectType === 'view'
    ? { label: 'V', cls: 'tree-icon-view' }
    : { label: 'T', cls: 'tree-icon-table' }

  return (
    <div className="object-detail">
      <div className="object-detail-header">
        <span className={`tree-type-badge ${badge.cls}`}>{badge.label}</span>
        <span className="object-detail-name">{db}.{schema}.{name}</span>
      </div>
      {data.type === 'view' && data.sql && (
        <pre className="object-detail-sql">{data.sql}</pre>
      )}
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Column</th>
              <th className="tree-col-type">Type</th>
            </tr>
          </thead>
          <tbody>
            {data.columns.map(col => (
              <tr key={col.name}>
                <td className="tree-col-name">{col.name}</td>
                <td className="tree-col-type">{col.type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
