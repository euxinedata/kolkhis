import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

interface CatalogObjectInfo {
  name: string
  type: string  // "table" or "view"
}

interface ObjectSchema {
  type: string
  columns: ColumnInfo[]
  sql?: string
}

const listStyle = { listStyle: 'none' as const, padding: 0, margin: 0 }

function ListItem({
  label,
  selected,
  onClick,
  prefix,
}: {
  label: string
  selected: boolean
  onClick: () => void
  prefix?: string
}) {
  return (
    <li
      onClick={onClick}
      style={{
        padding: '0.3em 0.6em',
        cursor: 'pointer',
        borderRadius: '4px',
        backgroundColor: selected ? '#2a2a4a' : 'transparent',
        color: selected ? '#646cff' : 'inherit',
        fontSize: '0.9em',
        fontFamily: 'monospace',
      }}
    >
      {prefix && (
        <span style={{ color: '#8888bb', marginRight: '0.4em', fontSize: '0.8em' }}>
          {prefix}
        </span>
      )}
      {label}
    </li>
  )
}

export function CatalogBrowser() {
  const [databases, setDatabases] = useState<string[]>([])
  const [selectedDb, setSelectedDb] = useState<string | null>(null)
  const [schemas, setSchemas] = useState<string[]>([])
  const [selectedSchema, setSelectedSchema] = useState<string | null>(null)
  const [objects, setObjects] = useState<CatalogObjectInfo[]>([])
  const [selectedObject, setSelectedObject] = useState<string | null>(null)
  const [objectSchema, setObjectSchema] = useState<ObjectSchema | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch<{ name: string }[]>('/api/catalog/databases')
      .then(dbs => setDatabases(dbs.map(d => d.name)))
      .finally(() => setLoading(false))
  }, [])

  async function selectDatabase(db: string) {
    setSelectedDb(db)
    setSelectedSchema(null)
    setObjects([])
    setSelectedObject(null)
    setObjectSchema(null)
    const result = await apiFetch<{ name: string }[]>(
      `/api/catalog/databases/${db}/schemas`
    )
    setSchemas(result.map(s => s.name))
  }

  async function selectSchema(schema: string) {
    if (!selectedDb) return
    setSelectedSchema(schema)
    setSelectedObject(null)
    setObjectSchema(null)
    const result = await apiFetch<CatalogObjectInfo[]>(
      `/api/catalog/databases/${selectedDb}/schemas/${schema}/objects`
    )
    setObjects(result)
  }

  async function selectObject(name: string) {
    if (!selectedDb || !selectedSchema) return
    setSelectedObject(name)
    const data = await apiFetch<ObjectSchema>(
      `/api/catalog/databases/${selectedDb}/schemas/${selectedSchema}/objects/${name}/schema`
    )
    setObjectSchema(data)
  }

  if (loading) return <p style={{ color: '#8888bb' }}>Loading...</p>

  return (
    <div>
      <h2>Catalog Browser</h2>

      <div style={{ display: 'flex', gap: '2em', marginTop: '1em' }}>
        {/* Databases */}
        <div style={{ minWidth: '150px' }}>
          <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
            Databases
          </h3>
          {databases.length === 0 ? (
            <p style={{ fontSize: '0.85em', color: '#666' }}>No databases</p>
          ) : (
            <ul style={listStyle}>
              {databases.map(db => (
                <ListItem
                  key={db}
                  label={db}
                  selected={selectedDb === db}
                  onClick={() => selectDatabase(db)}
                />
              ))}
            </ul>
          )}
        </div>

        {/* Schemas */}
        {selectedDb && (
          <div style={{ minWidth: '150px' }}>
            <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
              Schemas
            </h3>
            {schemas.length === 0 ? (
              <p style={{ fontSize: '0.85em', color: '#666' }}>No schemas</p>
            ) : (
              <ul style={listStyle}>
                {schemas.map(s => (
                  <ListItem
                    key={s}
                    label={s}
                    selected={selectedSchema === s}
                    onClick={() => selectSchema(s)}
                  />
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Objects */}
        {selectedSchema && (
          <div style={{ minWidth: '180px' }}>
            <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
              Objects
            </h3>
            {objects.length === 0 ? (
              <p style={{ fontSize: '0.85em', color: '#666' }}>No objects</p>
            ) : (
              <ul style={listStyle}>
                {objects.map(obj => (
                  <ListItem
                    key={obj.name}
                    label={obj.name}
                    selected={selectedObject === obj.name}
                    onClick={() => selectObject(obj.name)}
                    prefix={obj.type === 'table' ? 'T' : 'V'}
                  />
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Schema detail */}
        {objectSchema && selectedObject && (
          <div style={{ flex: 1 }}>
            <h3 style={{ fontSize: '0.9em', color: '#8888bb', marginBottom: '0.5em' }}>
              {selectedDb}.{selectedSchema}.{selectedObject}
              <span style={{ marginLeft: '0.5em', fontSize: '0.85em', color: '#666' }}>
                ({objectSchema.type})
              </span>
            </h3>
            {objectSchema.type === 'view' && objectSchema.sql && (
              <pre
                style={{
                  background: '#1a1a2e',
                  padding: '0.8em',
                  borderRadius: '4px',
                  fontSize: '0.85em',
                  overflow: 'auto',
                  marginBottom: '1em',
                }}
              >
                {objectSchema.sql}
              </pre>
            )}
            {objectSchema.columns.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Required</th>
                  </tr>
                </thead>
                <tbody>
                  {objectSchema.columns.map(col => (
                    <tr key={col.name}>
                      <td style={{ fontFamily: 'monospace' }}>{col.name}</td>
                      <td>{col.type}</td>
                      <td>{col.required ? 'Yes' : 'No'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
