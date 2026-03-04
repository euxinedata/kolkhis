import { NavLink, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from './auth.tsx'
import { Billing } from './pages/Billing.tsx'
import { QueryEditor } from './pages/QueryEditor.tsx'
import { QueryHistory } from './pages/QueryHistory.tsx'
import Resources from './pages/Resources.tsx'
import { Settings } from './pages/Settings.tsx'
import { Engineering } from './pages/Engineering.tsx'
import { ProjectEditor } from './pages/ProjectEditor.tsx'
import { Placeholder } from './pages/Placeholder.tsx'
import kolkhisLogo from './assets/kolkhis.svg'
import { Table, CodeXml, LayoutDashboard, Server, ReceiptText, Settings as SettingsIcon } from 'lucide-react'
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

  return (
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
                <NavLink to="/history" className="sidebar-flyout-link">History</NavLink>
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
                <NavLink to="/engineering" end className="sidebar-flyout-link">Projects</NavLink>
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
            <Route path="/engineering/editor/:projectId" element={<ProjectEditor />} />
            <Route path="/reporting" element={<Placeholder view="reporting" />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default App
