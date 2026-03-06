import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

interface PartitionField {
  name: string
  transform: string
}

interface SnapshotInfo {
  snapshot_id: number
  timestamp_ms: number
  operation?: string
  added_records?: number
  deleted_records?: number
}

interface ObjectSchema {
  type: string
  row_count: number | null
  total_file_size: number | null
  total_data_files: number | null
  last_updated_ms: number | null
  partition_fields: PartitionField[] | null
  snapshots: SnapshotInfo[]
  columns: ColumnInfo[]
  sql?: string
}

interface CatalogObjectInfo {
  name: string
  type: string
  columns: number
  file_size: number | null
}

interface SchemaInfo {
  name: string
  tables: number
  file_size: number
}

type OnNavigate = (db: string, schema: string, name: string, objectType: string) => void

export function DatabaseDetail({ db, onNavigate }: { db: string; onNavigate?: OnNavigate }) {
  const [schemas, setSchemas] = useState<SchemaInfo[] | null>(null)
  const [totalSize, setTotalSize] = useState<number>(0)
  const [totalTables, setTotalTables] = useState<number>(0)
  const [lastUpdatedMs, setLastUpdatedMs] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<{ schemas: SchemaInfo[]; total_size: number; total_tables: number; last_updated_ms: number | null }>(`/api/catalog/databases/${db}/schemas`)
      .then(d => { setSchemas(d.schemas); setTotalSize(d.total_size); setTotalTables(d.total_tables); setLastUpdatedMs(d.last_updated_ms); setLoading(false) })
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
        <div className="object-detail-stat">
          <span className="object-detail-stat-label">Schemas</span>
          <span className="object-detail-stat-value">{schemas.length}</span>
        </div>
        <div className="object-detail-stat">
          <span className="object-detail-stat-label">Tables</span>
          <span className="object-detail-stat-value">{totalTables}</span>
        </div>
        {totalSize > 0 && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Total size</span>
            <span className="object-detail-stat-value">{formatBytes(totalSize)}</span>
          </div>
        )}
        {lastUpdatedMs !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Last updated</span>
            <span className="object-detail-stat-value">{new Date(lastUpdatedMs).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'}</span>
          </div>
        )}
      </div>
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Schema</th>
              <th className="tree-col-type">Tables</th>
              <th className="tree-col-type">Size</th>
            </tr>
          </thead>
          <tbody>
            {schemas.map(s => (
              <tr key={s.name} className={onNavigate ? 'clickable-row' : ''} onClick={onNavigate ? () => onNavigate(db, s.name, '', 'schema') : undefined}>
                <td className="tree-col-name">{s.name}</td>
                <td className="tree-col-type">{s.tables}</td>
                <td className="tree-col-type">{s.file_size > 0 ? formatBytes(s.file_size) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function SchemaDetail({ db, schema, onNavigate }: { db: string; schema: string; onNavigate?: OnNavigate }) {
  const [objects, setObjects] = useState<CatalogObjectInfo[] | null>(null)
  const [totalSize, setTotalSize] = useState<number>(0)
  const [lastUpdatedMs, setLastUpdatedMs] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiFetch<{ objects: CatalogObjectInfo[]; total_size: number; last_updated_ms: number | null }>(`/api/catalog/databases/${db}/schemas/${schema}/objects`)
      .then(d => { setObjects(d.objects); setTotalSize(d.total_size); setLastUpdatedMs(d.last_updated_ms); setLoading(false) })
      .catch(() => setLoading(false))
  }, [db, schema])

  if (loading) return <div className="object-detail"><span style={{ color: '#8888bb' }}>Loading...</span></div>
  if (!objects) return <div className="object-detail"><span style={{ color: '#f87171' }}>Failed to load schema</span></div>

  const tables = objects.filter(o => o.type === 'table')
  const views = objects.filter(o => o.type === 'view')

  return (
    <div className="object-detail">
      {onNavigate && (
        <div className="catalog-breadcrumb">
          <img className="catalog-breadcrumb-icon" src="/file-icons/database.svg" alt="" />
          <a className="catalog-link" onClick={() => onNavigate(db, '', '', 'database')}>{db}</a>
        </div>
      )}
      <div className="object-detail-header">
        <img className="catalog-detail-icon" src="/file-icons/folder-blue.svg" alt="" />
        <span className="object-detail-name">{schema}</span>
      </div>
      <div className="object-detail-section">
        <div className="object-detail-stat">
          <span className="object-detail-stat-label">Tables</span>
          <span className="object-detail-stat-value">{tables.length}</span>
        </div>
        {views.length > 0 && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Views</span>
            <span className="object-detail-stat-value">{views.length}</span>
          </div>
        )}
        {totalSize > 0 && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Total size</span>
            <span className="object-detail-stat-value">{formatBytes(totalSize)}</span>
          </div>
        )}
        {lastUpdatedMs !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Last updated</span>
            <span className="object-detail-stat-value">{new Date(lastUpdatedMs).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'}</span>
          </div>
        )}
      </div>
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Object</th>
              <th className="tree-col-type">Type</th>
              <th className="tree-col-type">Columns</th>
              <th className="tree-col-type">Size</th>
            </tr>
          </thead>
          <tbody>
            {objects.map(o => (
              <tr key={o.name} className={onNavigate ? 'clickable-row' : ''} onClick={onNavigate ? () => onNavigate(db, schema, o.name, o.type) : undefined}>
                <td className="tree-col-name">{o.name}</td>
                <td className="tree-col-type">{o.type}</td>
                <td className="tree-col-type">{o.columns}</td>
                <td className="tree-col-type">{o.file_size !== null ? formatBytes(o.file_size) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ObjectDetail({ db, schema, name, objectType, onPreview, onNavigate }: { db: string; schema: string; name: string; objectType: string; onPreview?: (sql: string) => void; onNavigate?: OnNavigate }) {
  const [data, setData] = useState<ObjectSchema | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)

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

  const qualifiedName = `${db}.${schema}.${name}`

  function handleCopy() {
    navigator.clipboard.writeText(qualifiedName).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="object-detail">
      {onNavigate && (
        <div className="catalog-breadcrumb">
          <img className="catalog-breadcrumb-icon" src="/file-icons/database.svg" alt="" />
          <a className="catalog-link" onClick={() => onNavigate(db, '', '', 'database')}>{db}</a>
          <span className="catalog-breadcrumb-sep">/</span>
          <img className="catalog-breadcrumb-icon" src="/file-icons/folder-blue.svg" alt="" />
          <a className="catalog-link" onClick={() => onNavigate(db, schema, '', 'schema')}>{schema}</a>
        </div>
      )}
      <div className="object-detail-header">
        <span className={`tree-type-badge ${badge.cls}`}>{badge.label}</span>
        <span className="object-detail-name">{name}</span>
        <button className="copy-name-button" onClick={handleCopy} title="Copy qualified name">
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <div className="object-detail-section">
        {data.row_count !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Rows</span>
            <span className="object-detail-stat-value">{data.row_count.toLocaleString()}</span>
          </div>
        )}
        {data.total_file_size !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Size</span>
            <span className="object-detail-stat-value">{formatBytes(data.total_file_size)}</span>
          </div>
        )}
        {data.total_data_files !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Files</span>
            <span className="object-detail-stat-value">{data.total_data_files.toLocaleString()}</span>
          </div>
        )}
        {data.last_updated_ms !== null && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Last updated</span>
            <span className="object-detail-stat-value">{new Date(data.last_updated_ms).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'}</span>
          </div>
        )}
        {data.partition_fields && data.partition_fields.length > 0 && (
          <div className="object-detail-stat">
            <span className="object-detail-stat-label">Partitioned by</span>
            <span className="object-detail-stat-value">
              {data.partition_fields.map(p =>
                p.transform === 'identity' ? p.name : `${p.transform}(${p.name})`
              ).join(', ')}
            </span>
          </div>
        )}
      </div>
      {onPreview && (
        <div className="object-detail-section">
          <button
            className="preview-button"
            onClick={() => onPreview(`SELECT * FROM ${db}.${schema}.${name} LIMIT 100`)}
          >
            Preview Data
          </button>
        </div>
      )}
      {data.type === 'view' && data.sql && (
        <pre className="object-detail-sql">{data.sql}</pre>
      )}
      <div className="object-detail-columns">
        <table>
          <thead>
            <tr>
              <th className="tree-col-name">Column</th>
              <th className="tree-col-type">Type</th>
              <th className="tree-col-nullable">Nullable</th>
            </tr>
          </thead>
          <tbody>
            {data.columns.map(col => (
              <tr key={col.name}>
                <td className="tree-col-name">{col.name}</td>
                <td className="tree-col-type">{col.type}</td>
                <td className="tree-col-nullable">{col.required ? 'NO' : 'YES'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.snapshots.length > 0 && (
        <div className="object-detail-section">
          <span className="object-detail-label">Snapshot history</span>
          <div className="object-detail-columns">
            <table>
              <thead>
                <tr>
                  <th className="tree-col-name">Timestamp</th>
                  <th className="tree-col-type">Operation</th>
                  <th className="tree-col-type">Records</th>
                </tr>
              </thead>
              <tbody>
                {data.snapshots.map(snap => (
                  <tr key={snap.snapshot_id}>
                    <td className="tree-col-name">{new Date(snap.timestamp_ms).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'}</td>
                    <td className="tree-col-type">{snap.operation ?? '—'}</td>
                    <td className="tree-col-type">
                      {snap.added_records !== undefined ? `+${snap.added_records.toLocaleString()}` : ''}
                      {snap.deleted_records !== undefined ? ` −${snap.deleted_records.toLocaleString()}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
