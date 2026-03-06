import { useEffect, useRef } from 'react'
import {
  UncontrolledTreeEnvironment,
  Tree,
  InteractionMode,
} from 'react-complex-tree'
import type {
  TreeDataProvider,
  TreeItem,
  TreeItemIndex,
  Disposable,
} from 'react-complex-tree'
import 'react-complex-tree/lib/style-modern.css'
import { apiFetch } from '../api'

interface CatalogEntry {
  name: string
  kind: 'database' | 'schema' | 'table' | 'view'
}

const CATALOG_ICON: Record<string, string | ((expanded: boolean) => string)> = {
  database: '/file-icons/database.svg',
  schema: (expanded: boolean) => expanded ? '/file-icons/folder-blue-open.svg' : '/file-icons/folder-blue.svg',
  table: '/file-icons/grid-3x3.svg',
  view: '/file-icons/grip.svg',
}

function catalogIconUrl(kind: string, isExpanded: boolean): string {
  const entry = CATALOG_ICON[kind]
  if (typeof entry === 'function') return entry(isExpanded)
  return entry ?? '/file-icons/file.svg'
}

class CatalogTreeDataProvider implements TreeDataProvider<CatalogEntry> {
  private items: Record<string, TreeItem<CatalogEntry>> = {}
  private listeners: Array<(ids: TreeItemIndex[]) => void> = []

  constructor() {
    this.items['root'] = {
      index: 'root',
      isFolder: true,
      children: [],
      data: { name: '', kind: 'database' },
    }
  }

  async getTreeItem(itemId: TreeItemIndex): Promise<TreeItem<CatalogEntry>> {
    const id = String(itemId)
    if (this.items[id]) return this.items[id]
    return {
      index: id,
      isFolder: false,
      children: undefined,
      data: { name: id, kind: 'table' },
    }
  }

  onDidChangeTreeData(listener: (changedItemIds: TreeItemIndex[]) => void): Disposable {
    this.listeners.push(listener)
    return { dispose: () => { this.listeners = this.listeners.filter(l => l !== listener) } }
  }

  private notify(ids: TreeItemIndex[]) {
    for (const l of this.listeners) l(ids)
  }

  async loadDatabases(): Promise<void> {
    const dbs = await apiFetch<{ name: string }[]>('/api/catalog/databases')
    for (const db of dbs) {
      this.items[db.name] = {
        index: db.name,
        isFolder: true,
        children: [],
        data: { name: db.name, kind: 'database' },
      }
    }
    this.items['root'] = {
      ...this.items['root'],
      children: dbs.map(d => d.name),
    }
    this.notify(['root'])
  }

  async loadSchemas(db: string): Promise<void> {
    const existing = this.items[db]
    if (existing?.children && existing.children.length > 0) return
    const data = await apiFetch<{ schemas: { name: string }[] }>(
      `/api/catalog/databases/${db}/schemas`
    )
    const schemas = data.schemas
    for (const s of schemas) {
      const id = `${db}.${s.name}`
      this.items[id] = {
        index: id,
        isFolder: true,
        children: [],
        data: { name: s.name, kind: 'schema' },
      }
    }
    this.items[db] = {
      ...this.items[db],
      children: schemas.map(s => `${db}.${s.name}`),
    }
    this.notify([db])
  }

  async loadObjects(db: string, schema: string): Promise<void> {
    const parentId = `${db}.${schema}`
    const existing = this.items[parentId]
    if (existing?.children && existing.children.length > 0) return
    const data = await apiFetch<{ objects: { name: string; type: string }[] }>(
      `/api/catalog/databases/${db}/schemas/${schema}/objects`
    )
    const objects = data.objects
    for (const obj of objects) {
      const id = `${db}.${schema}.${obj.name}`
      this.items[id] = {
        index: id,
        isFolder: false,
        children: undefined,
        data: { name: obj.name, kind: obj.type === 'view' ? 'view' : 'table' },
      }
    }
    this.items[parentId] = {
      ...this.items[parentId],
      children: objects.map(o => `${db}.${schema}.${o.name}`),
    }
    this.notify([parentId])
  }

  reset(): void {
    const rootItem = this.items['root']
    this.items = { root: { ...rootItem, children: [] } }
    this.notify(['root'])
    this.loadDatabases()
  }
}

interface CatalogPanelProps {
  refreshKey?: number
  onSelectObject?: (db: string, schema: string, name: string, objectType: string) => void
}

export function CatalogPanel({ refreshKey = 0, onSelectObject }: CatalogPanelProps) {
  const providerRef = useRef<CatalogTreeDataProvider | null>(null)
  const treeKeyRef = useRef(0)
  const prevRefreshKey = useRef(refreshKey)

  if (!providerRef.current) {
    providerRef.current = new CatalogTreeDataProvider()
  }

  useEffect(() => {
    providerRef.current!.loadDatabases()
  }, [])

  useEffect(() => {
    if (refreshKey > 0 && refreshKey !== prevRefreshKey.current) {
      prevRefreshKey.current = refreshKey
      providerRef.current!.reset()
      treeKeyRef.current += 1
    }
  }, [refreshKey])

  function handleExpandItem(item: TreeItem<CatalogEntry>) {
    const id = String(item.index)
    const kind = item.data.kind
    if (kind === 'database') {
      providerRef.current!.loadSchemas(id)
    } else if (kind === 'schema') {
      const parts = id.split('.')
      providerRef.current!.loadObjects(parts[0], parts[1])
    }
  }

  function handleSelectItems(items: TreeItemIndex[]) {
    if (!onSelectObject || items.length === 0) return
    const id = String(items[0])
    const parts = id.split('.')
    void providerRef.current?.getTreeItem(id).then(i => {
      const kind = i.data.kind
      if (kind === 'database') {
        onSelectObject(parts[0], '', '', 'database')
      } else if (kind === 'schema') {
        onSelectObject(parts[0], parts[1], '', 'schema')
      } else {
        onSelectObject(parts[0], parts[1], parts[2], kind)
      }
    })
  }

  return (
    <div className="catalog-panel">
      <div className="catalog-tree">
        <UncontrolledTreeEnvironment
          key={treeKeyRef.current}
          dataProvider={providerRef.current}
          getItemTitle={item => item?.data?.name ?? ''}
          viewState={{}}
          defaultInteractionMode={InteractionMode.ClickItemToExpand}
          canDragAndDrop={false}
          canSearch={false}
          canRename={false}
          onExpandItem={handleExpandItem}
          onSelectItems={handleSelectItems}
          renderItemTitle={({ title, item, context }) => {
            if (!item) return <span>{title}</span>
            const iconUrl = catalogIconUrl(item.data.kind, context.isExpanded ?? false)
            return (
              <span className="catalog-tree-title">
                <img className="catalog-tree-icon" src={iconUrl} alt="" />
                {title}
              </span>
            )
          }}
        >
          <Tree<CatalogEntry>
            treeId="catalog-tree"
            rootItem="root"
            treeLabel="Catalog"
          />
        </UncontrolledTreeEnvironment>
      </div>
    </div>
  )
}
