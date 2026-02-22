import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

interface ColumnInfo {
  name: string
  type: string
  required: boolean
}

interface CatalogObjectInfo {
  name: string
  type: string
}

interface ObjectSchema {
  type: string
  columns: ColumnInfo[]
  sql?: string
}

export function CatalogPanel() {
  const [databases, setDatabases] = useState<string[]>([])
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [schemas, setSchemas] = useState<Record<string, string[]>>({})
  const [objects, setObjects] = useState<Record<string, CatalogObjectInfo[]>>({})
  const [selectedObject, setSelectedObject] = useState<string | null>(null)
  const [objectSchema, setObjectSchema] = useState<ObjectSchema | null>(null)

  useEffect(() => {
    apiFetch<{ name: string }[]>('/api/catalog/databases').then(dbs =>
      setDatabases(dbs.map(d => d.name))
    )
  }, [])

  async function toggleDatabase(db: string) {
    const key = db
    if (expanded[key]) {
      setExpanded(prev => ({ ...prev, [key]: false }))
      return
    }
    if (!schemas[db]) {
      const result = await apiFetch<{ name: string }[]>(
        `/api/catalog/databases/${db}/schemas`
      )
      setSchemas(prev => ({ ...prev, [db]: result.map(s => s.name) }))
    }
    setExpanded(prev => ({ ...prev, [key]: true }))
  }

  async function toggleSchema(db: string, schema: string) {
    const key = `${db}.${schema}`
    if (expanded[key]) {
      setExpanded(prev => ({ ...prev, [key]: false }))
      return
    }
    if (!objects[key]) {
      const result = await apiFetch<CatalogObjectInfo[]>(
        `/api/catalog/databases/${db}/schemas/${schema}/objects`
      )
      setObjects(prev => ({ ...prev, [key]: result }))
    }
    setExpanded(prev => ({ ...prev, [key]: true }))
  }

  async function selectObject(db: string, schema: string, name: string) {
    const key = `${db}.${schema}.${name}`
    if (selectedObject === key) {
      setSelectedObject(null)
      setObjectSchema(null)
      return
    }
    setSelectedObject(key)
    const data = await apiFetch<ObjectSchema>(
      `/api/catalog/databases/${db}/schemas/${schema}/objects/${name}/schema`
    )
    setObjectSchema(data)
  }

  return (
    <div className="catalog-panel">
      <div className="catalog-tree">
        {databases.map(db => (
          <div key={db}>
            <div
              className="tree-node tree-database"
              onClick={() => toggleDatabase(db)}
            >
              <span className="tree-arrow">{expanded[db] ? '▾' : '▸'}</span>
              {db}
            </div>
            {expanded[db] && schemas[db]?.map(schema => {
              const schemaKey = `${db}.${schema}`
              return (
                <div key={schema} style={{ paddingLeft: '1em' }}>
                  <div
                    className="tree-node tree-schema"
                    onClick={() => toggleSchema(db, schema)}
                  >
                    <span className="tree-arrow">
                      {expanded[schemaKey] ? '▾' : '▸'}
                    </span>
                    {schema}
                  </div>
                  {expanded[schemaKey] && objects[schemaKey]?.map(obj => {
                    const objKey = `${db}.${schema}.${obj.name}`
                    return (
                      <div key={obj.name} style={{ paddingLeft: '1em' }}>
                        <div
                          className={`tree-node tree-object ${selectedObject === objKey ? 'tree-selected' : ''}`}
                          onClick={() => selectObject(db, schema, obj.name)}
                        >
                          <span className="tree-type-badge">
                            {obj.type === 'table' ? 'T' : 'V'}
                          </span>
                          {obj.name}
                        </div>
                        {selectedObject === objKey && objectSchema && (
                          <div className="tree-columns">
                            {objectSchema.type === 'view' && objectSchema.sql && (
                              <pre className="tree-view-sql">{objectSchema.sql}</pre>
                            )}
                            {objectSchema.columns.map(col => (
                              <div key={col.name} className="tree-column">
                                <span className="tree-col-name">{col.name}</span>
                                <span className="tree-col-type">{col.type}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
