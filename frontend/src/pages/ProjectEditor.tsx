import { useState, useEffect, useCallback, useRef } from 'react'
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
import Terminal from '../components/Terminal'
import { CatalogPanel } from '../components/CatalogPanel'
import { useStatusBar } from '../StatusBarContext'
import { DatabaseDetail, SchemaDetail, ObjectDetail } from '../components/CatalogDetails'
import { defineKolkhisTheme, THEME_NAME } from '../monacoTheme'
import './ProjectEditor.css'


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
  cursorLine: number
  cursorColumn: number
  scrollTop: number
  tabType?: 'file' | 'database' | 'schema' | 'object'
  db?: string
  schema?: string
  objectName?: string
  objectType?: string
}

interface EditorSession {
  openTabs: OpenTab[]
  activeTab: string | null
  terminalTabs: { id: number; name: string }[]
  activeTerminalTab: number | null
  terminalOpen: boolean
  terminalHeight: number
  treeWidth: number
  nextTerminalId: number
}

const SESSION_KEY = 'kolkhis-editor-session-workspace'

function loadSession(): EditorSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch { return null }
}

function saveSession(session: EditorSession): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session))
  } catch { /* storage full — ignore */ }
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

  constructor(repoName: string) {
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
      data: { name: repoName, path: '', type: 'dir', size: 0 },
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
      `/api/workspace/files?path=${encodeURIComponent(dirPath)}`
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

      await apiFetch(`/api/workspace/${endpoint}`, {
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

      await apiFetch(`/api/workspace/rename`, {
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
const WORKSPACE_NAME = 'warehouse'

export function ProjectEditor() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load saved session
  const savedSession = loadSession()

  // Tab/editor state
  const [openTabs, setOpenTabs] = useState<OpenTab[]>(savedSession?.openTabs ?? [])
  const [activeTab, setActiveTab] = useState<string | null>(savedSession?.activeTab ?? null)
  const [fileLoading, setFileLoading] = useState(false)
  const editorRef = useRef<any>(null)
  // Refs for stable access from Monaco keybinding closure
  const openTabsRef = useRef(openTabs)
  openTabsRef.current = openTabs
  const activeTabRef = useRef(activeTab)
  activeTabRef.current = activeTab

  // Terminal state
  const [terminalOpen, setTerminalOpen] = useState(savedSession?.terminalOpen ?? false)
  const [terminalTabs, setTerminalTabs] = useState<{ id: number; name: string }[]>(savedSession?.terminalTabs ?? [])
  const [activeTerminalTab, setActiveTerminalTab] = useState<number | null>(savedSession?.activeTerminalTab ?? null)
  const [terminalHeight, setTerminalHeight] = useState(savedSession?.terminalHeight ?? 200)
  const [treeWidth, setTreeWidth] = useState(savedSession?.treeWidth ?? 240)

  const { setLeft, setRight } = useStatusBar()
  const nextTerminalId = useRef(savedSession?.nextTerminalId ?? 1)

  // Refs for unmount save (must track latest values)
  const terminalOpenRef = useRef(terminalOpen)
  terminalOpenRef.current = terminalOpen
  const terminalTabsRef = useRef(terminalTabs)
  terminalTabsRef.current = terminalTabs
  const activeTerminalTabRef = useRef(activeTerminalTab)
  activeTerminalTabRef.current = activeTerminalTab
  const terminalHeightRef = useRef(terminalHeight)
  terminalHeightRef.current = terminalHeight
  const treeWidthRef = useRef(treeWidth)
  treeWidthRef.current = treeWidth

  // Context menu state
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const [contextTarget, setContextTarget] = useState<string | null>(null)

  // Editor tab context menu state
  const [editorTabContextMenu, setEditorTabContextMenu] = useState<{ x: number; y: number; path: string } | null>(null)
  const [editorTabOverflowOpen, setEditorTabOverflowOpen] = useState(false)

  // Terminal context menu / rename state
  const [termContextMenu, setTermContextMenu] = useState<{ x: number; y: number; tabId: number } | null>(null)
  const [renamingTerminalId, setRenamingTerminalId] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // VCS status state (used once we add custom styling back)
  const [, setVcsStatus] = useState<Record<string, string>>({})

  // Tree data provider + ref
  const dataProviderRef = useRef<FileTreeDataProvider | null>(null)
  const treeRef = useRef<TreeRef<FileEntry> | null>(null)
  // Force re-render counter (needed when provider changes)
  const [treeKey, setTreeKey] = useState(0)
  // Track active placeholder for cleanup
  const activePlaceholderRef = useRef<string | null>(null)

  // Sidebar mode: files or databases
  const [sidebarMode, setSidebarMode] = useState<'files' | 'databases'>('files')

  // Persist session to localStorage (debounced)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      // Snapshot cursor/scroll from live editor into tabs before persisting
      const tabs = [...openTabsRef.current]
      const editor = editorRef.current
      const current = activeTabRef.current
      if (editor && current) {
        const pos = editor.getPosition()
        const scroll = editor.getScrollTop()
        const idx = tabs.findIndex(t => t.path === current)
        if (idx >= 0) {
          tabs[idx] = { ...tabs[idx], cursorLine: pos?.lineNumber ?? 1, cursorColumn: pos?.column ?? 1, scrollTop: scroll ?? 0 }
        }
      }
      saveSession({
        openTabs: tabs,
        activeTab: activeTabRef.current,
        terminalTabs,
        activeTerminalTab,
        terminalOpen,
        terminalHeight,
        treeWidth,
        nextTerminalId: nextTerminalId.current,
      })
    }, 300)
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [openTabs, activeTab, terminalTabs, activeTerminalTab, terminalOpen, terminalHeight, treeWidth])

  // Save immediately on unmount (captures cursor position)
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      const tabs = [...openTabsRef.current]
      const editor = editorRef.current
      const current = activeTabRef.current
      if (editor && current) {
        const pos = editor.getPosition()
        const scroll = editor.getScrollTop()
        const idx = tabs.findIndex(t => t.path === current)
        if (idx >= 0) {
          tabs[idx] = { ...tabs[idx], cursorLine: pos?.lineNumber ?? 1, cursorColumn: pos?.column ?? 1, scrollTop: scroll ?? 0 }
        }
      }
      saveSession({
        openTabs: tabs,
        activeTab: activeTabRef.current,
        terminalTabs: terminalTabsRef.current,
        activeTerminalTab: activeTerminalTabRef.current,
        terminalOpen: terminalOpenRef.current,
        terminalHeight: terminalHeightRef.current,
        treeWidth: treeWidthRef.current,
        nextTerminalId: nextTerminalId.current,
      })
    }
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const data = await apiFetch<Record<string, string>>(
        `/api/workspace/status`
      )
      setVcsStatus(data)
    } catch {
      // ignore — status is optional
    }
  }, [])

  // Initialize data provider
  useEffect(() => {
    const provider = new FileTreeDataProvider(WORKSPACE_NAME)
    dataProviderRef.current = provider
    provider.loadDirectory('')
      .then(() => {
        setTreeKey(k => k + 1)
        setLoading(false)
      })
      .catch(() => {
        setError('Failed to load workspace')
        setLoading(false)
      })
    loadStatus()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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

  // Close terminal context menu on click outside or Escape
  useEffect(() => {
    if (!termContextMenu) return
    const handleClick = () => setTermContextMenu(null)
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setTermContextMenu(null)
    }
    document.addEventListener('click', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('click', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [termContextMenu])

  // Close editor tab context menu on click outside or Escape
  useEffect(() => {
    if (!editorTabContextMenu) return
    const handleClick = () => setEditorTabContextMenu(null)
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setEditorTabContextMenu(null)
    }
    document.addEventListener('click', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('click', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [editorTabContextMenu])

  // Editor tab overflow dropdown dismiss
  useEffect(() => {
    if (!editorTabOverflowOpen) return
    const handleClick = (e: MouseEvent) => {
      const wrapper = (e.target as HTMLElement).closest('.tab-overflow-wrapper')
      if (wrapper) return
      setEditorTabOverflowOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setEditorTabOverflowOpen(false) }
    requestAnimationFrame(() => {
      document.addEventListener('mousedown', handleClick)
      document.addEventListener('keydown', handleKey)
    })
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [editorTabOverflowOpen])

  // Ctrl+` toggle terminal
  const toggleTerminalRef = useRef(toggleTerminal)
  toggleTerminalRef.current = toggleTerminal
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '`' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        toggleTerminalRef.current()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Drag resize handler
  const handleResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startHeight = terminalHeight
    const onMouseMove = (ev: MouseEvent) => {
      const delta = startY - ev.clientY
      setTerminalHeight(Math.max(100, Math.min(500, startHeight + delta)))
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [terminalHeight])

  // Drag resize handler for file tree width
  const handleTreeResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = treeWidth
    const onMouseMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      setTreeWidth(Math.max(140, Math.min(500, startWidth + delta)))
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [treeWidth])

  // Flush cursor/scroll from editor into the current tab's state
  function flushEditorState() {
    const editor = editorRef.current
    const current = activeTabRef.current
    if (!editor || !current) return
    const pos = editor.getPosition()
    const scroll = editor.getScrollTop()
    setOpenTabs(prev => prev.map(t =>
      t.path === current
        ? { ...t, cursorLine: pos?.lineNumber ?? t.cursorLine, cursorColumn: pos?.column ?? t.cursorColumn, scrollTop: scroll ?? t.scrollTop }
        : t
    ))
  }

  async function openFile(path: string) {
    // Use ref to avoid stale closure in useCallback handlers
    if (openTabsRef.current.some(t => t.path === path)) {
      flushEditorState()
      setActiveTab(path)
      return
    }
    flushEditorState()
    setActiveTab(path)
    setFileLoading(true)
    try {
      const data = await apiFetch<{ path: string; content: string }>(
        `/api/workspace/file?path=${encodeURIComponent(path)}`
      )
      setOpenTabs(prev => prev.some(t => t.path === path)
        ? prev
        : [...prev, { path, content: data.content, savedContent: data.content, cursorLine: 1, cursorColumn: 1, scrollTop: 0 }]
      )
    } catch {
      setOpenTabs(prev => prev.some(t => t.path === path)
        ? prev
        : [...prev, { path, content: '// Failed to load file', savedContent: '', cursorLine: 1, cursorColumn: 1, scrollTop: 0 }]
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

  function closeEditorTabs(filter: (tab: OpenTab, idx: number) => boolean) {
    setOpenTabs(prev => {
      const next = prev.filter((t, i) => !filter(t, i))
      if (activeTab && !next.find(t => t.path === activeTab)) {
        if (next.length > 0) {
          setActiveTab(next[next.length - 1].path)
        } else {
          setActiveTab(null)
        }
      }
      return next
    })
    setEditorTabContextMenu(null)
  }

  async function saveActiveTab() {
    const currentActive = activeTabRef.current
    const currentTabs = openTabsRef.current
    if (!currentActive) return
    const tab = currentTabs.find(t => t.path === currentActive)
    if (!tab || tab.content === tab.savedContent) return
    try {
      await apiFetch(`/api/workspace/files`, {
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

  function addTerminal() {
    const id = nextTerminalId.current++
    const tab = { id, name: `Terminal ${id}` }
    setTerminalTabs(prev => [...prev, tab])
    setActiveTerminalTab(id)
    setTerminalOpen(true)
  }

  function closeTerminal(id: number) {
    setTerminalTabs(prev => {
      const idx = prev.findIndex(t => t.id === id)
      const next = prev.filter(t => t.id !== id)
      if (next.length === 0) {
        setActiveTerminalTab(null)
        setTerminalOpen(false)
      } else if (activeTerminalTab === id) {
        const newIdx = Math.min(idx, next.length - 1)
        setActiveTerminalTab(next[newIdx].id)
      }
      return next
    })
  }

  function toggleTerminal() {
    if (terminalOpen) {
      setTerminalOpen(false)
    } else {
      if (terminalTabs.length === 0) {
        addTerminal()
      } else {
        setTerminalOpen(true)
      }
    }
  }

  // Status bar content
  useEffect(() => {
    const label = activeTab
      ? activeTab.startsWith('__catalog__:') ? activeTab.replace('__catalog__:', '') : activeTab
      : null
    setLeft(label ? <span className="status-bar-item">{label}</span> : null)
    setRight(
      <button
        className={`status-bar-btn${terminalOpen ? ' active' : ''}`}
        onClick={toggleTerminal}
        title="Toggle Terminal (Ctrl+`)"
      >
        Terminal
      </button>
    )
    return () => { setLeft(null); setRight(null) }
  }, [activeTab, terminalOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  function handleTerminalContextMenu(e: React.MouseEvent, tabId: number) {
    e.preventDefault()
    e.stopPropagation()
    setTermContextMenu({ x: e.clientX, y: e.clientY, tabId })
  }

  function startRenamingTerminal() {
    if (!termContextMenu) return
    const tab = terminalTabs.find(t => t.id === termContextMenu.tabId)
    if (!tab) return
    setRenamingTerminalId(termContextMenu.tabId)
    setRenameValue(tab.name)
    setTermContextMenu(null)
  }

  function commitTerminalRename() {
    if (renamingTerminalId === null) return
    const name = renameValue.trim()
    if (name) {
      setTerminalTabs(prev => prev.map(t =>
        t.id === renamingTerminalId ? { ...t, name } : t
      ))
    }
    setRenamingTerminalId(null)
    setRenameValue('')
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
    if (!contextTarget || !dataProviderRef.current) return
    const path = contextTarget
    setContextMenu(null)
    setContextTarget(null)

    await apiFetch(`/api/workspace/files`, {
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

  // --- Catalog detail tabs ---

  function handleCatalogSelect(db: string, schema: string, name: string, objectType: string) {
    let tabId: string
    let tabType: OpenTab['tabType']
    if (objectType === 'database') {
      tabId = `__catalog__:${db}`
      tabType = 'database'
    } else if (objectType === 'schema') {
      tabId = `__catalog__:${db}.${schema}`
      tabType = 'schema'
    } else {
      tabId = `__catalog__:${db}.${schema}.${name}`
      tabType = 'object'
    }
    if (openTabsRef.current.some(t => t.path === tabId)) {
      flushEditorState()
      setActiveTab(tabId)
      return
    }
    flushEditorState()
    const newTab: OpenTab = {
      path: tabId,
      content: '',
      savedContent: '',
      cursorLine: 1,
      cursorColumn: 1,
      scrollTop: 0,
      tabType,
      db,
      schema,
      objectName: name,
      objectType,
    }
    setOpenTabs(prev => [...prev, newTab])
    setActiveTab(tabId)
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
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
  if (!dataProviderRef.current) return null

  return (
    <div className="project-editor">
      <div className="project-editor-body">
        <div className="file-tree-panel" style={{ width: treeWidth, minWidth: treeWidth }}>
          <div className="sidebar-tabs">
            <div className={`sidebar-tab ${sidebarMode === 'files' ? 'active' : ''}`}
                 onClick={() => setSidebarMode('files')}>Files</div>
            <div className={`sidebar-tab ${sidebarMode === 'databases' ? 'active' : ''}`}
                 onClick={() => setSidebarMode('databases')}>Databases</div>
          </div>
          {sidebarMode === 'files' ? (
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
          ) : (
            <div className="file-tree-list">
              <CatalogPanel onSelectObject={handleCatalogSelect} />
            </div>
          )}
        </div>
        <div className="tree-resize-handle" onMouseDown={handleTreeResizeMouseDown} />
        <div className="file-editor-panel">
          <div className="editor-area">
            {openTabs.length > 0 && (
              <div className="editor-tabs">
                <div className="editor-tabs-inner">
                {openTabs.map(tab => {
                  const isCatalog = tab.tabType && tab.tabType !== 'file'
                  const dirty = !isCatalog && tab.content !== tab.savedContent
                  const name = isCatalog
                    ? tab.path.replace('__catalog__:', '')
                    : (tab.path.includes('/') ? tab.path.split('/').pop()! : tab.path)
                  const badgeMap: Record<string, string> = { database: 'DB', schema: 'S', table: 'T', view: 'V' }
                  const badgeLabel = isCatalog
                    ? (tab.tabType === 'object' ? badgeMap[tab.objectType ?? 'table'] : badgeMap[tab.tabType!])
                    : null
                  const badgeType = isCatalog
                    ? (tab.tabType === 'object' ? tab.objectType : tab.tabType)
                    : null
                  return (
                    <div
                      key={tab.path}
                      className={`editor-tab${tab.path === activeTab ? ' active' : ''}`}
                      title={isCatalog ? tab.path.replace('__catalog__:', '') : tab.path}
                      onClick={() => { flushEditorState(); setActiveTab(tab.path) }}
                      onContextMenu={e => { e.preventDefault(); setEditorTabContextMenu({ x: e.clientX, y: e.clientY, path: tab.path }) }}
                    >
                      {badgeLabel && (
                        <span className={`query-tab-badge query-tab-badge-${badgeType}`}>{badgeLabel}</span>
                      )}
                      <span className="editor-tab-name">{name}</span>
                      <span
                        className={`editor-tab-close${dirty ? ' dirty' : ''}`}
                        onClick={e => { e.stopPropagation(); closeTab(tab.path) }}
                      >
                        {dirty ? <span className="editor-tab-dot">●</span> : '✕'}
                      </span>
                    </div>
                  )
                })}
                </div>
                <div className="tab-overflow-wrapper">
                  <button className="tab-overflow-btn" onClick={() => setEditorTabOverflowOpen(v => !v)} title="Open tabs">
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                  </button>
                  {editorTabOverflowOpen && (
                    <div className="tab-overflow-dropdown">
                      {openTabs.map(tab => {
                        const isCat = tab.tabType && tab.tabType !== 'file'
                        const d = !isCat && tab.content !== tab.savedContent
                        const n = isCat
                          ? tab.path.replace('__catalog__:', '')
                          : (tab.path.includes('/') ? tab.path.split('/').pop()! : tab.path)
                        const bMap: Record<string, string> = { database: 'DB', schema: 'S', table: 'T', view: 'V' }
                        const bLabel = isCat
                          ? (tab.tabType === 'object' ? bMap[tab.objectType ?? 'table'] : bMap[tab.tabType!])
                          : null
                        const bType = isCat
                          ? (tab.tabType === 'object' ? tab.objectType : tab.tabType)
                          : null
                        return (
                          <div
                            key={tab.path}
                            className={`tab-overflow-item${tab.path === activeTab ? ' active' : ''}`}
                            onClick={() => { flushEditorState(); setActiveTab(tab.path); setEditorTabOverflowOpen(false) }}
                          >
                            {bLabel && <span className={`query-tab-badge query-tab-badge-${bType}`}>{bLabel}</span>}
                            <span className="tab-overflow-item-name">{d ? `${n} ●` : n}</span>
                            <span
                              className="tab-overflow-item-close"
                              onClick={e => { e.stopPropagation(); closeTab(tab.path) }}
                            >✕</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className="file-editor-content">
              {activeTab ? (() => {
                const currentTab = openTabs.find(t => t.path === activeTab)
                if (currentTab?.tabType === 'database') {
                  return <DatabaseDetail db={currentTab.db!} />
                }
                if (currentTab?.tabType === 'schema') {
                  return <SchemaDetail db={currentTab.db!} schema={currentTab.schema!} />
                }
                if (currentTab?.tabType === 'object') {
                  return <ObjectDetail db={currentTab.db!} schema={currentTab.schema!} name={currentTab.objectName!} objectType={currentTab.objectType!} />
                }
                return fileLoading && !openTabs.some(t => t.path === activeTab) ? (
                  <div className="file-editor-empty">Loading...</div>
                ) : (
                  <Editor
                    height="100%"
                    path={activeTab}
                    language={languageForPath(activeTab)}
                    value={openTabs.find(t => t.path === activeTab)?.content ?? ''}
                    beforeMount={defineKolkhisTheme}
                    theme={THEME_NAME}
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
                      // Restore cursor and scroll for the active tab
                      const tab = openTabsRef.current.find(t => t.path === activeTabRef.current)
                      if (tab) {
                        editor.setPosition({ lineNumber: tab.cursorLine, column: tab.cursorColumn })
                        editor.setScrollTop(tab.scrollTop)
                      }
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
              })() : (
                <div className="file-editor-empty">Select a file to view</div>
              )}
            </div>
          </div>
          {terminalOpen && (
            <>
              <div className="terminal-resize-handle" onMouseDown={handleResizeMouseDown} />
              <div className="terminal-panel" style={{ height: terminalHeight }}>
                <div className="terminal-tabs">
                  {terminalTabs.map(tab => (
                    <div
                      key={tab.id}
                      className={`terminal-tab${tab.id === activeTerminalTab ? ' active' : ''}`}
                      onClick={() => setActiveTerminalTab(tab.id)}
                      onContextMenu={e => handleTerminalContextMenu(e, tab.id)}
                    >
                      {renamingTerminalId === tab.id ? (
                        <input
                          className="terminal-tab-rename"
                          value={renameValue}
                          onChange={e => setRenameValue(e.target.value)}
                          onBlur={commitTerminalRename}
                          onKeyDown={e => {
                            if (e.key === 'Enter') commitTerminalRename()
                            if (e.key === 'Escape') { setRenamingTerminalId(null); setRenameValue('') }
                          }}
                          onClick={e => e.stopPropagation()}
                          autoFocus
                        />
                      ) : (
                        <span className="terminal-tab-name">{tab.name}</span>
                      )}
                      <span
                        className="terminal-tab-close"
                        onClick={e => { e.stopPropagation(); closeTerminal(tab.id) }}
                      >✕</span>
                    </div>
                  ))}
                  <button className="terminal-tab-add" onClick={addTerminal}>+</button>
                </div>
                <div className="terminal-content">
                  {terminalTabs.map(tab => (
                    <div key={tab.id} style={{
                      display: tab.id === activeTerminalTab ? 'block' : 'none',
                      width: '100%', height: '100%',
                    }}>
                      <Terminal
                        tabId={tab.id}
                        visible={tab.id === activeTerminalTab}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
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

      {termContextMenu && (
        <div
          className="context-menu"
          style={{ left: termContextMenu.x, top: termContextMenu.y }}
        >
          <div className="context-menu-item" onClick={e => { e.stopPropagation(); startRenamingTerminal() }}>
            Rename
          </div>
          <div className="context-menu-item" onClick={e => { e.stopPropagation(); setTermContextMenu(null); closeTerminal(termContextMenu.tabId) }}>
            Close
          </div>
        </div>
      )}

      {editorTabContextMenu && (() => {
        const idx = openTabs.findIndex(t => t.path === editorTabContextMenu.path)
        return (
          <div className="context-menu" style={{ left: editorTabContextMenu.x, top: editorTabContextMenu.y }}>
            <div className="context-menu-item" onClick={() => closeEditorTabs(t => t.path === editorTabContextMenu.path)}>Close</div>
            <div className="context-menu-item" onClick={() => closeEditorTabs(t => t.path !== editorTabContextMenu.path)}>Close Other Tabs</div>
            <div className="context-menu-item" onClick={() => closeEditorTabs(() => true)}>Close All Tabs</div>
            <div className="context-menu-item" onClick={() => closeEditorTabs(t => {
              const isCatalog = t.tabType && t.tabType !== 'file'
              return !!(isCatalog || t.content === t.savedContent)
            })}>Close Unmodified Tabs</div>
            <div className="context-menu-separator" />
            <div className="context-menu-item" onClick={() => closeEditorTabs((_t, i) => i < idx)}>Close Tabs to the Left</div>
            <div className="context-menu-item" onClick={() => closeEditorTabs((_t, i) => i > idx)}>Close Tabs to the Right</div>
          </div>
        )
      })()}
    </div>
  )
}
