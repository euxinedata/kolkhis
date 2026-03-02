import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Editor } from '@monaco-editor/react'
import {
  UncontrolledTreeEnvironment,
  Tree,
  InteractionMode,
} from 'react-complex-tree'
import type {
  TreeDataProvider,
  TreeItem,
  TreeItemIndex,
  TreeRef,
  Disposable,
} from 'react-complex-tree'
import 'react-complex-tree/lib/style-modern.css'
import { apiFetch } from '../api'
import './ProjectEditor.css'

interface Project {
  id: string
  name: string
  description: string
  created_at: string
}

interface FileEntry {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number
}

interface ContextMenu {
  x: number
  y: number
  targetDir: string
}

const PLACEHOLDER_PREFIX = '__new__'

function languageForPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    yml: 'yaml', yaml: 'yaml', sql: 'sql', py: 'python',
    md: 'markdown', json: 'json', txt: 'plaintext',
  }
  return map[ext ?? ''] ?? 'plaintext'
}

// ---------------------------------------------------------------------------
// FileTreeDataProvider — wraps our API for react-complex-tree
// ---------------------------------------------------------------------------
class FileTreeDataProvider implements TreeDataProvider<FileEntry> {
  private items: Record<string, TreeItem<FileEntry>> = {}
  private listeners: Array<(ids: TreeItemIndex[]) => void> = []
  private projectId: string

  constructor(projectId: string, projectName: string) {
    this.projectId = projectId
    // Initialise root item
    this.items['root'] = {
      index: 'root',
      isFolder: true,
      children: [],
      canRename: false,
      canMove: false,
      data: { name: projectName, path: '', type: 'dir', size: 0 },
    }
  }

  async getTreeItem(itemId: TreeItemIndex): Promise<TreeItem<FileEntry>> {
    const id = String(itemId)
    if (this.items[id]) return this.items[id]
    throw new Error(`Item ${id} not found`)
  }

  async loadDirectory(dirPath: string, force = false): Promise<void> {
    const itemId = dirPath === '' ? 'root' : dirPath
    const existing = this.items[itemId]
    if (!force && existing?.children && existing.children.length > 0) return

    const entries = await apiFetch<FileEntry[]>(
      `/api/projects/${this.projectId}/files?path=${encodeURIComponent(dirPath)}`
    )

    // Sort: dirs first, then files, alphabetically
    const sorted = [...entries].sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })

    for (const entry of sorted) {
      this.items[entry.path] = {
        index: entry.path,
        isFolder: entry.type === 'dir',
        children: entry.type === 'dir' ? [] : undefined,
        canRename: true,
        canMove: false,
        data: entry,
      }
    }

    this.items[itemId] = {
      ...this.items[itemId],
      children: sorted.map(e => e.path),
    }

    this.notify([itemId])
  }

  // --- Placeholder system for New File / New Folder ---

  createPlaceholder(targetDir: string, isFolder: boolean): string {
    const placeholderId = `${PLACEHOLDER_PREFIX}${targetDir}/${isFolder ? 'd' : 'f'}`
    const parentId = targetDir === '' ? 'root' : targetDir

    this.items[placeholderId] = {
      index: placeholderId,
      isFolder,
      children: isFolder ? [] : undefined,
      canRename: true,
      canMove: false,
      data: { name: '', path: placeholderId, type: isFolder ? 'dir' : 'file', size: 0 },
    }

    const parent = this.items[parentId]
    const children = [...(parent?.children ?? []), placeholderId]
    this.items[parentId] = { ...parent, children }
    this.notify([parentId])
    return placeholderId
  }

  removePlaceholder(placeholderId: string): void {
    // Extract parent dir from placeholder ID: "__new__<dir>/<f|d>"
    const inner = placeholderId.slice(PLACEHOLDER_PREFIX.length)
    const parentDir = inner.substring(0, inner.lastIndexOf('/'))
    const parentId = parentDir === '' ? 'root' : parentDir

    const parent = this.items[parentId]
    if (parent) {
      this.items[parentId] = {
        ...parent,
        children: (parent.children ?? []).filter(c => c !== placeholderId),
      }
    }
    delete this.items[placeholderId]
    this.notify([parentId])
  }

  isPlaceholder(id: string): boolean {
    return id.startsWith(PLACEHOLDER_PREFIX)
  }

  // --- TreeDataProvider interface ---

  onDidChangeTreeData(listener: (changedItemIds: TreeItemIndex[]) => void): Disposable {
    this.listeners.push(listener)
    return { dispose: () => { this.listeners = this.listeners.filter(l => l !== listener) } }
  }

  async onRenameItem(item: TreeItem<FileEntry>, name: string): Promise<void> {
    const oldId = String(item.index)
    const isPlaceholder = this.isPlaceholder(oldId)

    if (isPlaceholder) {
      // Extract target dir from placeholder ID
      const inner = oldId.slice(PLACEHOLDER_PREFIX.length)
      const targetDir = inner.substring(0, inner.lastIndexOf('/'))
      const fullPath = targetDir ? `${targetDir}/${name}` : name
      const endpoint = item.isFolder ? 'folders' : 'files'
      const body = item.isFolder
        ? { path: fullPath }
        : { path: fullPath, content: '' }

      await apiFetch(`/api/projects/${this.projectId}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      // Remove placeholder and reload directory
      delete this.items[oldId]
      await this.loadDirectory(targetDir, true)
    } else {
      // Real rename
      const parentDir = oldId.includes('/') ? oldId.substring(0, oldId.lastIndexOf('/')) : ''
      const newPath = parentDir ? `${parentDir}/${name}` : name
      if (newPath === oldId) return

      await apiFetch(`/api/projects/${this.projectId}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_path: oldId, new_path: newPath }),
      })

      await this.loadDirectory(parentDir, true)
    }
  }

  private notify(ids: TreeItemIndex[]) {
    this.listeners.forEach(l => l(ids))
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function ProjectEditor() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Editor state
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [fileLoading, setFileLoading] = useState(false)

  // Context menu state
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const [contextTarget, setContextTarget] = useState<string | null>(null)

  // VCS status state (used once we add custom styling back)
  const [, setVcsStatus] = useState<Record<string, string>>({})

  // Tree data provider + ref
  const dataProviderRef = useRef<FileTreeDataProvider | null>(null)
  const treeRef = useRef<TreeRef<FileEntry> | null>(null)
  // Force re-render counter (needed when provider changes)
  const [treeKey, setTreeKey] = useState(0)
  // Track active placeholder for cleanup
  const activePlaceholderRef = useRef<string | null>(null)

  const loadStatus = useCallback(async () => {
    if (!projectId) return
    try {
      const data = await apiFetch<Record<string, string>>(
        `/api/projects/${projectId}/status`
      )
      setVcsStatus(data)
    } catch {
      // ignore — status is optional
    }
  }, [projectId])

  // VCS class helpers — will be used once we add custom styling back
  // function vcsClass(path: string): string {
  //   const status = vcsStatus[path]
  //   if (status === 'new') return 'vcs-new'
  //   if (status === 'modified') return 'vcs-modified'
  //   if (status === 'deleted') return 'vcs-deleted'
  //   return ''
  // }
  // function vcsDirClass(dirPath: string): string {
  //   const prefix = dirPath ? dirPath + '/' : ''
  //   for (const p of Object.keys(vcsStatus)) {
  //     if (p.startsWith(prefix)) return 'vcs-dir-changed'
  //   }
  //   return ''
  // }

  // Fetch project metadata
  useEffect(() => {
    if (!projectId) return
    apiFetch<Project[]>('/api/projects')
      .then(projects => {
        const p = projects.find(p => p.id === projectId)
        if (p) setProject(p)
        else setError('Project not found')
      })
      .catch(() => setError('Failed to load project'))
      .finally(() => setLoading(false))
  }, [projectId])

  // Initialize data provider when project is loaded
  useEffect(() => {
    if (!projectId || !project) return
    const provider = new FileTreeDataProvider(projectId, project.name)
    dataProviderRef.current = provider
    provider.loadDirectory('').then(() => setTreeKey(k => k + 1))
    loadStatus()
  }, [projectId, project]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close context menu on click outside or Escape
  useEffect(() => {
    if (!contextMenu) return
    const handleClick = () => { setContextMenu(null); setContextTarget(null) }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setContextMenu(null); setContextTarget(null) }
    }
    document.addEventListener('click', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('click', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [contextMenu])

  async function openFile(path: string) {
    if (path === selectedFile) return
    setSelectedFile(path)
    setFileLoading(true)
    try {
      const data = await apiFetch<{ path: string; content: string }>(
        `/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`
      )
      setFileContent(data.content)
    } catch {
      setFileContent('// Failed to load file')
    } finally {
      setFileLoading(false)
    }
  }

  function handleContextMenu(e: React.MouseEvent, targetDir: string, nodePath?: string) {
    e.preventDefault()
    e.stopPropagation()
    setContextMenu({ x: e.clientX, y: e.clientY, targetDir })
    setContextTarget(nodePath ?? null)
  }

  function handleMenuAction(kind: 'file' | 'folder') {
    if (!contextMenu || !dataProviderRef.current) return
    const targetDir = contextMenu.targetDir
    const isFolder = kind === 'folder'
    const provider = dataProviderRef.current

    // Ensure target dir is expanded
    if (targetDir !== '') {
      treeRef.current?.expandItem(targetDir)
    }

    const placeholderId = provider.createPlaceholder(targetDir, isFolder)
    activePlaceholderRef.current = placeholderId

    // Wait for tree to render the placeholder, then trigger rename on it
    requestAnimationFrame(() => {
      treeRef.current?.startRenamingItem(placeholderId)
    })

    setContextMenu(null)
    setContextTarget(null)
  }

  function handleRenameAction() {
    if (!contextMenu || !contextTarget) return
    treeRef.current?.startRenamingItem(contextTarget)
    setContextMenu(null)
    setContextTarget(null)
  }

  // --- react-complex-tree event handlers ---

  const handleSelectItems = useCallback((items: TreeItemIndex[]) => {
    if (items.length === 0) return
    const id = String(items[0])
    if (id === 'root' || id.startsWith(PLACEHOLDER_PREFIX)) return
    const provider = dataProviderRef.current
    if (!provider) return
    provider.getTreeItem(id).then(item => {
      if (!item.isFolder) openFile(id)
    })
  }, [projectId, selectedFile]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleExpandItem = useCallback((item: TreeItem<FileEntry>) => {
    const id = String(item.index)
    const dirPath = id === 'root' ? '' : id
    dataProviderRef.current?.loadDirectory(dirPath)
  }, [])

  const handleRenameItem = useCallback(async (item: TreeItem<FileEntry>, name: string) => {
    const provider = dataProviderRef.current
    if (!provider) return
    const oldId = String(item.index)
    const wasPlaceholder = provider.isPlaceholder(oldId)

    await provider.onRenameItem!(item, name)
    activePlaceholderRef.current = null
    await loadStatus()

    if (wasPlaceholder && !item.isFolder) {
      // Auto-open newly created file
      const inner = oldId.slice(PLACEHOLDER_PREFIX.length)
      const targetDir = inner.substring(0, inner.lastIndexOf('/'))
      const fullPath = targetDir ? `${targetDir}/${name}` : name
      setSelectedFile(fullPath)
      setFileContent('')
    } else if (!wasPlaceholder) {
      // If renamed file was selected, update selection
      const parentDir = oldId.includes('/') ? oldId.substring(0, oldId.lastIndexOf('/')) : ''
      const newPath = parentDir ? `${parentDir}/${name}` : name
      if (selectedFile === oldId) {
        setSelectedFile(newPath)
      }
    }
  }, [loadStatus, selectedFile])

  const handleAbortRenaming = useCallback((item: TreeItem<FileEntry>) => {
    const id = String(item.index)
    if (dataProviderRef.current?.isPlaceholder(id)) {
      dataProviderRef.current.removePlaceholder(id)
      activePlaceholderRef.current = null
    }
  }, [])

  // Context menu via event delegation on the tree container
  function handleTreeContextMenu(e: React.MouseEvent) {
    // Walk up from the target to find the rct interactive element with data-rct-item-id
    let el = e.target as HTMLElement | null
    let itemId: string | null = null
    while (el && !el.classList.contains('file-tree-list')) {
      itemId = el.getAttribute('data-rct-item-id')
      if (itemId) break
      el = el.parentElement
    }

    if (!itemId) {
      // Clicked empty space
      handleContextMenu(e, '')
      return
    }

    e.preventDefault()
    e.stopPropagation()

    if (itemId === 'root') {
      handleContextMenu(e, '')
      return
    }

    const provider = dataProviderRef.current
    if (!provider) return
    provider.getTreeItem(itemId).then(item => {
      const isFolder = item.isFolder
      const targetDir = isFolder ? itemId! : (itemId!.includes('/') ? itemId!.substring(0, itemId!.lastIndexOf('/')) : '')
      setContextMenu({ x: e.clientX, y: e.clientY, targetDir })
      setContextTarget(itemId)
    })
  }

  if (loading) return <p style={{ color: '#8888bb', padding: '1em' }}>Loading...</p>
  if (error) return <p style={{ color: '#f87171', padding: '1em' }}>{error}</p>
  if (!project) return null
  if (!dataProviderRef.current) return null

  return (
    <div className="project-editor">
      <div className="project-editor-header">
        <button className="project-back-btn" onClick={() => navigate('/engineering')}>
          ← Projects
        </button>
        <span className="project-editor-name">{project.name}</span>
      </div>
      <div className="project-editor-body">
        <div className="file-tree-panel">
          <div className="file-tree-title">Files</div>
          <div
            className="file-tree-list"
            onContextMenu={handleTreeContextMenu}
          >
            <UncontrolledTreeEnvironment
              key={treeKey}
              dataProvider={dataProviderRef.current}
              getItemTitle={item => item.data.name}
              viewState={{
                'file-tree': {
                  expandedItems: ['root'],
                },
              }}
              defaultInteractionMode={InteractionMode.ClickItemToExpand}
              canDragAndDrop={false}
              canSearch={false}
              canRename={true}
              onSelectItems={handleSelectItems}
              onExpandItem={handleExpandItem}
              onRenameItem={handleRenameItem}
              onAbortRenamingItem={handleAbortRenaming}
            >
              <Tree<FileEntry>
                ref={treeRef}
                treeId="file-tree"
                rootItem="root"
                treeLabel="Files"
              />
            </UncontrolledTreeEnvironment>
          </div>
        </div>
        <div className="file-editor-panel">
          {selectedFile ? (
            fileLoading ? (
              <div className="file-editor-empty">Loading...</div>
            ) : (
              <Editor
                height="100%"
                language={languageForPath(selectedFile)}
                value={fileContent}
                theme="vs-dark"
                options={{
                  readOnly: true,
                  minimap: { enabled: false },
                  fontSize: 13,
                  lineNumbers: 'on',
                  scrollBeyondLastLine: false,
                  wordWrap: 'on',
                }}
              />
            )
          ) : (
            <div className="file-editor-empty">Select a file to view</div>
          )}
        </div>
      </div>

      {contextMenu && (
        <div
          className="context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <div className="context-menu-item" onClick={e => { e.stopPropagation(); handleMenuAction('file') }}>
            New File
          </div>
          <div className="context-menu-item" onClick={e => { e.stopPropagation(); handleMenuAction('folder') }}>
            New Folder
          </div>
          {contextTarget && (
            <div className="context-menu-item" onClick={e => { e.stopPropagation(); handleRenameAction() }}>
              Rename
            </div>
          )}
        </div>
      )}
    </div>
  )
}
