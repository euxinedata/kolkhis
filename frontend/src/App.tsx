import { useState, useEffect } from 'react'
import { NavLink, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from './auth.tsx'
import { apiFetch } from './api'
import { Billing } from './pages/Billing.tsx'
import { QueryEditor } from './pages/QueryEditor.tsx'
import { QueryHistory } from './pages/QueryHistory.tsx'
import Resources from './pages/Resources.tsx'
import { Settings } from './pages/Settings.tsx'
import { Engineering } from './pages/Engineering.tsx'
import { Placeholder } from './pages/Placeholder.tsx'
import { Onboarding } from './pages/Onboarding.tsx'
import kolkhisLogo from './assets/kolkhis.svg'
import { Table, CodeXml, LayoutDashboard, Server, ReceiptText, Logs, Settings as SettingsIcon } from 'lucide-react'
import { StatusBarProvider, useStatusBar } from './StatusBarContext.tsx'
import './App.css'

type View = 'analytics' | 'engineering' | 'reporting'

function viewFromPath(pathname: string): View {
  if (pathname.startsWith('/engineering')) return 'engineering'
  if (pathname.startsWith('/reporting')) return 'reporting'
  return 'analytics'
}

function isBottomActive(pathname: string, section: string): boolean {
  return pathname.startsWith(`/${section}`)
}

function App() {
  const { user, loading, login, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const activeView = viewFromPath(location.pathname)

  if (loading) {
    return <div className="app-loading">Loading...</div>
  }

  if (!user) {
    return (
      <div className="login-screen">
        <img src={kolkhisLogo} alt="Kolkhis" className="login-logo" />
        <h1>Kolkhis</h1>
        <p className="login-subtitle">Euxine Data Platform</p>
        <button onClick={login}>Sign in with Google</button>
      </div>
    )
  }

  if (!user.org_id) {
    return <Onboarding onComplete={() => window.location.reload()} />
  }

  return (
    <StatusBarProvider>
    <div className="app">
      <nav className="app-nav">
        <span className="app-title">
          <img src={kolkhisLogo} alt="" className="nav-logo" />
          Kolkhis
        </span>
        <div className="nav-user">
          <span className="user-name">{user.name}</span>
          <button onClick={logout} className="btn-signout">Sign out</button>
        </div>
      </nav>
      <div className="app-body">
        <aside className="app-sidebar">
          <div className="sidebar-sections">
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${activeView === 'analytics' ? 'active' : ''}`}
                onClick={() => navigate('/')}
                title="Analytics"
              >
                <Table size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Analytics</div>
                <NavLink to="/" end className="sidebar-flyout-link">Query</NavLink>
              </div>
            </div>
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${activeView === 'engineering' ? 'active' : ''}`}
                onClick={() => navigate('/engineering')}
                title="Engineering"
              >
                <CodeXml size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Engineering</div>
                <NavLink to="/engineering" end className="sidebar-flyout-link">Workspace</NavLink>
              </div>
            </div>
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${activeView === 'reporting' ? 'active' : ''}`}
                onClick={() => navigate('/reporting')}
                title="Reporting"
              >
                <LayoutDashboard size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Reporting</div>
                <NavLink to="/reporting" className="sidebar-flyout-link">Reports</NavLink>
              </div>
            </div>
          </div>
          <div className="sidebar-bottom">
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${isBottomActive(location.pathname, 'history') ? 'active' : ''}`}
                onClick={() => navigate('/history')}
                title="History"
              >
                <Logs size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">History</div>
                <NavLink to="/history" className="sidebar-flyout-link">Query History</NavLink>
              </div>
            </div>
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${isBottomActive(location.pathname, 'resources') ? 'active' : ''}`}
                onClick={() => navigate('/resources')}
                title="Resources"
              >
                <Server size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Resources</div>
                <NavLink to="/resources" className="sidebar-flyout-link">Resources</NavLink>
              </div>
            </div>
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${isBottomActive(location.pathname, 'billing') ? 'active' : ''}`}
                onClick={() => navigate('/billing')}
                title="Billing"
              >
                <ReceiptText size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Billing</div>
                <NavLink to="/billing" className="sidebar-flyout-link">Billing</NavLink>
              </div>
            </div>
            <div className="sidebar-item">
              <button
                className={`sidebar-icon-btn ${isBottomActive(location.pathname, 'settings') ? 'active' : ''}`}
                onClick={() => navigate('/settings')}
                title="Settings"
              >
                <SettingsIcon size={20} />
              </button>
              <div className="sidebar-flyout">
                <div className="sidebar-flyout-title">Settings</div>
                <NavLink to="/settings" className="sidebar-flyout-link">Settings</NavLink>
              </div>
            </div>
          </div>
        </aside>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<QueryEditor />} />
            <Route path="/resources" element={<Resources />} />
            <Route path="/history" element={<QueryHistory />} />
            <Route path="/billing" element={<Billing />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/engineering" element={<Engineering />} />
            <Route path="/reporting" element={<Placeholder view="reporting" />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      <StatusBar />
    </div>
    </StatusBarProvider>
  )
}

const WORKER_TYPES: Record<string, { size: string; bird: string; specs: string }> = {
  cpx42: { size: 'XS', bird: 'Sparrow', specs: '8 vCPU, 16 GB' },
  cpx62: { size: 'S',  bird: 'Dove',    specs: '16 vCPU, 32 GB' },
  ccx43: { size: 'M',  bird: 'Falcon',  specs: '16 vCPU, 64 GB' },
  ccx53: { size: 'L',  bird: 'Stork',   specs: '32 vCPU, 128 GB' },
  ccx63: { size: 'XL', bird: 'Swan',    specs: '48 vCPU, 192 GB' },
}

interface WorkerInfo {
  id: number
  status: string
  server_type: string
}

function WorkerIndicator() {
  const [worker, setWorker] = useState<WorkerInfo | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    const poll = () => {
      apiFetch<WorkerInfo[]>('/api/workers')
        .then(ws => { if (!cancelled) setWorker(ws.length > 0 ? ws[0] : null) })
        .catch(() => { if (!cancelled) setWorker(null) })
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  if (!worker) return null

  const info = WORKER_TYPES[worker.server_type]
  const birdKey = (info?.bird ?? 'falcon').toLowerCase()
  const tooltip = info
    ? `${info.bird} \u2014 ${info.size} (${info.specs}) \u2014 ${worker.status}`
    : `${worker.server_type} \u2014 ${worker.status}`

  return (
    <div className="worker-indicator" onClick={() => navigate('/resources')}>
      <img
        src={`/worker-icons/${birdKey}.svg`}
        alt={info?.bird ?? 'worker'}
        className={`worker-icon worker-${worker.status}`}
      />
      <div className="worker-tooltip">{tooltip}</div>
    </div>
  )
}

function StatusBar() {
  const { state } = useStatusBar()
  return (
    <div className="status-bar">
      <div className="status-bar-left">{state.left}</div>
      <div className="status-bar-right">
        {state.right}
        <WorkerIndicator />
      </div>
    </div>
  )
}

export default App
