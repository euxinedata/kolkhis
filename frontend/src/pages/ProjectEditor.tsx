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

interface OpenTab {
  path: string
  content: string
  savedContent: string
}

const PLACEHOLDER_PREFIX = '__new__'
const REPO_ROOT = 'repo-root'

function languageForPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    yml: 'yaml', yaml: 'yaml', sql: 'sql', py: 'python',
    md: 'markdown', json: 'json', txt: 'plaintext',
  }
  return map[ext ?? ''] ?? 'plaintext'
}

// ---------------------------------------------------------------------------
// File type icons — maps extensions/filenames to SVGs in /file-icons/
// ---------------------------------------------------------------------------
const FILE_ICON_MAP: Record<string, string> = {
  py: 'python',
  sql: 'database',
  yml: 'yaml',
  yaml: 'yaml',
  json: 'json',
  md: 'markdown',
  txt: 'document',
  xml: 'xml',
  csv: 'table',
  toml: 'toml',
}

const FILENAME_ICON_MAP: Record<string, string> = {
  '.gitignore': 'git',
  '.gitkeep': 'git',
}

function fileIconUrl(filename: string): string {
  const name = FILENAME_ICON_MAP[filename]
    ?? FILE_ICON_MAP[filename.split('.').pop()?.toLowerCase() ?? '']
    ?? 'file'
  return `/file-icons/${name}.svg`
}

function folderIconUrl(isOpen: boolean): string {
  return isOpen ? '/file-icons/folder-open.svg' : '/file-icons/folder.svg'
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
    // Hidden container (never rendered)
    this.items['root'] = {
      index: 'root',
      isFolder: true,
      children: [REPO_ROOT],
      canRename: false,
      canMove: false,
      data: { name: '', path: '', type: 'dir', size: 0 },
    }
    // Visible repo root
    this.items[REPO_ROOT] = {
      index: REPO_ROOT,
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
    // Return a stub for items the library references after deletion (e.g. placeholders)
    return {
      index: id,
      isFolder: false,
      children: undefined,
      canRename: false,
      canMove: false,
      data: { name: '', path: id, type: 'file', size: 0 },
    }
  }

  async loadDirectory(dirPath: string, force = false): Promise<void> {
    const itemId = dirPath === '' ? REPO_ROOT : dirPath
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
    const parentId = targetDir === '' ? REPO_ROOT : targetDir

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
    const parentId = parentDir === '' ? REPO_ROOT : parentDir

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

  getItemSync(itemId: string): TreeItem<FileEntry> | undefined {
    return this.items[itemId]
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

      // Reload directory — replaces children list, effectively removing the placeholder
      await this.loadDirectory(targetDir, true)
      delete this.items[oldId]
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

  // Tab/editor state
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
  const [activeTab, setActiveTab] = useState<string | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const editorRef = useRef<any>(null)
  // Refs for stable access from Monaco keybinding closure
  const openTabsRef = useRef(openTabs)
  openTabsRef.current = openTabs
  const activeTabRef = useRef(activeTab)
  activeTabRef.current = activeTab

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
    const handleClick = (e: MouseEvent) => {
      // Don't close when clicking inside the context menu itself
      const menuEl = document.querySelector('.context-menu')
      if (menuEl && menuEl.contains(e.target as Node)) return
      setContextMenu(null)
      setContextTarget(null)
    }
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
    // Use ref to avoid stale closure in useCallback handlers
    if (openTabsRef.current.some(t => t.path === path)) {
      setActiveTab(path)
      return
    }
    setActiveTab(path)
    setFileLoading(true)
    try {
      const data = await apiFetch<{ path: string; content: string }>(
        `/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`
      )
      setOpenTabs(prev => prev.some(t => t.path === path)
        ? prev
        : [...prev, { path, content: data.content, savedContent: data.content }]
      )
    } catch {
      setOpenTabs(prev => prev.some(t => t.path === path)
        ? prev
        : [...prev, { path, content: '// Failed to load file', savedContent: '' }]
      )
    } finally {
      setFileLoading(false)
    }
  }

  function closeTab(path: string) {
    setOpenTabs(prev => {
      const idx = prev.findIndex(t => t.path === path)
      const next = prev.filter(t => t.path !== path)
      if (activeTab === path) {
        if (next.length === 0) {
          setActiveTab(null)
        } else {
          // Switch to adjacent tab
          const newIdx = Math.min(idx, next.length - 1)
          setActiveTab(next[newIdx].path)
        }
      }
      return next
    })
  }

  async function saveActiveTab() {
    const currentActive = activeTabRef.current
    const currentTabs = openTabsRef.current
    if (!currentActive || !projectId) return
    const tab = currentTabs.find(t => t.path === currentActive)
    if (!tab || tab.content === tab.savedContent) return
    try {
      await apiFetch(`/api/projects/${projectId}/files`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: tab.path, content: tab.content }),
      })
      setOpenTabs(prev => prev.map(t =>
        t.path === currentActive ? { ...t, savedContent: t.content } : t
      ))
      await loadStatus()
    } catch {
      // save failed — leave dirty indicator
    }
  }

  async function handleMenuAction(kind: 'file' | 'folder') {
    if (!contextMenu || !dataProviderRef.current) return
    const targetDir = contextMenu.targetDir
    const isFolder = kind === 'folder'
    const provider = dataProviderRef.current

    // Load directory contents first so the expand handler becomes a no-op
    await provider.loadDirectory(targetDir)

    // Create placeholder BEFORE expanding so the tree sees non-empty children
    const placeholderId = provider.createPlaceholder(targetDir, isFolder)
    activePlaceholderRef.current = placeholderId

    if (targetDir !== '') {
      treeRef.current?.expandItem(targetDir)
    }

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

  async function handleDeleteAction() {
    if (!contextTarget || !projectId || !dataProviderRef.current) return
    const path = contextTarget
    setContextMenu(null)
    setContextTarget(null)

    await apiFetch(`/api/projects/${projectId}/files`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })

    const parentDir = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : ''
    await dataProviderRef.current.loadDirectory(parentDir, true)

    // Close tabs for deleted file (or files inside deleted directory)
    const tabsToClose = openTabs.filter(t => t.path === path || t.path.startsWith(path + '/'))
    for (const t of tabsToClose) {
      closeTab(t.path)
    }

    await loadStatus()
  }

  // --- react-complex-tree event handlers ---

  const handleSelectItems = useCallback((items: TreeItemIndex[]) => {
    if (items.length === 0) return
    const id = String(items[0])
    if (id === 'root' || id === REPO_ROOT || id.startsWith(PLACEHOLDER_PREFIX)) return
    const provider = dataProviderRef.current
    if (!provider) return
    provider.getTreeItem(id).then(item => {
      if (!item.isFolder) openFile(id)
    })
  }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleExpandItem = useCallback((item: TreeItem<FileEntry>) => {
    const id = String(item.index)
    const dirPath = (id === 'root' || id === REPO_ROOT) ? '' : id
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
      openFile(fullPath)
    } else if (!wasPlaceholder) {
      // If renamed file has an open tab, update the tab's path
      const parentDir = oldId.includes('/') ? oldId.substring(0, oldId.lastIndexOf('/')) : ''
      const newPath = parentDir ? `${parentDir}/${name}` : name
      setOpenTabs(prev => prev.map(t =>
        t.path === oldId ? { ...t, path: newPath } : t
      ))
      if (activeTab === oldId) {
        setActiveTab(newPath)
      }
    }
  }, [loadStatus, activeTab]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleAbortRenaming = useCallback((item: TreeItem<FileEntry>) => {
    const id = String(item.index)
    if (dataProviderRef.current?.isPlaceholder(id)) {
      dataProviderRef.current.removePlaceholder(id)
      activePlaceholderRef.current = null
    }
  }, [])

  // Context menu via native event listener on the tree container.
  // Using a native listener (not React onContextMenu) so that
  // preventDefault() fires directly on the native event before the
  // browser can act on it.
  const treeListRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = treeListRef.current
    if (!el) return

    const handler = (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()

      // Walk up from the target to find data-rct-item-id
      let node = e.target as HTMLElement | null
      let itemId: string | null = null
      while (node && node !== el) {
        itemId = node.getAttribute('data-rct-item-id')
        if (itemId) break
        node = node.parentElement
      }

      if (!itemId || itemId === 'root' || itemId === REPO_ROOT) {
        setContextMenu({ x: e.clientX, y: e.clientY, targetDir: '' })
        setContextTarget(null)
        return
      }

      const provider = dataProviderRef.current
      if (!provider) return
      const item = provider.getItemSync(itemId)
      if (!item) return
      const targetDir = item.isFolder ? itemId : (itemId.includes('/') ? itemId.substring(0, itemId.lastIndexOf('/')) : '')
      setContextMenu({ x: e.clientX, y: e.clientY, targetDir })
      setContextTarget(itemId)
    }

    el.addEventListener('contextmenu', handler)
    return () => el.removeEventListener('contextmenu', handler)
  }, [treeKey]) // re-attach when tree remounts

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
            ref={treeListRef}
          >
            <UncontrolledTreeEnvironment
              key={treeKey}
              dataProvider={dataProviderRef.current}
              getItemTitle={item => item?.data?.name ?? ''}
              viewState={{
                'file-tree': {
                  expandedItems: ['root', REPO_ROOT],
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
              renderItemTitle={({ title, item, context }) => {
                if (!item) return <span>{title}</span>
                const iconUrl = item.isFolder
                  ? folderIconUrl(context.isExpanded ?? false)
                  : fileIconUrl(item.data?.name ?? '')
                return (
                  <span className="file-tree-title-with-icon">
                    <img className="file-tree-icon" src={iconUrl} alt="" />
                    {title}
                  </span>
                )
              }}
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
          {openTabs.length > 0 && (
            <div className="editor-tabs">
              {openTabs.map(tab => {
                const dirty = tab.content !== tab.savedContent
                const name = tab.path.includes('/') ? tab.path.split('/').pop()! : tab.path
                return (
                  <div
                    key={tab.path}
                    className={`editor-tab${tab.path === activeTab ? ' active' : ''}`}
                    title={tab.path}
                    onClick={() => setActiveTab(tab.path)}
                  >
                    <span className="editor-tab-name">{name}</span>
                    <span
                      className={`editor-tab-close${dirty ? ' dirty' : ''}`}
                      onClick={e => { e.stopPropagation(); closeTab(tab.path) }}
                    >
                      {dirty ? <span className="editor-tab-dot">●</span> : '×'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
          <div className="file-editor-content">
            {activeTab ? (
              fileLoading && !openTabs.some(t => t.path === activeTab) ? (
                <div className="file-editor-empty">Loading...</div>
              ) : (
                <Editor
                  height="100%"
                  path={activeTab}
                  language={languageForPath(activeTab)}
                  value={openTabs.find(t => t.path === activeTab)?.content ?? ''}
                  theme="vs-dark"
                  onChange={(value) => {
                    setOpenTabs(prev => prev.map(t =>
                      t.path === activeTab ? { ...t, content: value ?? '' } : t
                    ))
                  }}
                  onMount={(editor, monaco) => {
                    editorRef.current = editor
                    editor.addAction({
                      id: 'save-file',
                      label: 'Save File',
                      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
                      run: () => { saveActiveTab() },
                    })
                  }}
                  options={{
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
            <>
              <div className="context-menu-item" onClick={e => { e.stopPropagation(); handleRenameAction() }}>
                Rename
              </div>
              <div className="context-menu-item" onClick={e => { e.stopPropagation(); handleDeleteAction() }}>
                Delete
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
