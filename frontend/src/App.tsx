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
import './App.css'

type View = 'analytics' | 'engineering' | 'reporting'

function viewFromPath(pathname: string): View {
  if (pathname.startsWith('/engineering')) return 'engineering'
  if (pathname.startsWith('/reporting')) return 'reporting'
  return 'analytics'
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
        <p className="login-subtitle">Data Warehouse</p>
        <button onClick={login}>Sign in with Google</button>
      </div>
    )
  }

  const handleViewClick = (view: View) => {
    if (view === 'analytics') navigate('/')
    else navigate(`/${view}`)
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
            <div className="sidebar-section">
              <button
                className={`sidebar-section-header ${activeView === 'analytics' ? 'active' : ''}`}
                onClick={() => handleViewClick('analytics')}
              >
                Analytics
              </button>
              {activeView === 'analytics' && (
                <div className="sidebar-links">
                  <NavLink to="/" end className="sidebar-link">Query</NavLink>
                  <NavLink to="/history" className="sidebar-link">History</NavLink>
                </div>
              )}
            </div>
            <div className="sidebar-section">
              <button
                className={`sidebar-section-header ${activeView === 'engineering' ? 'active' : ''}`}
                onClick={() => handleViewClick('engineering')}
              >
                Engineering
              </button>
              {activeView === 'engineering' && (
                <div className="sidebar-links">
                  <NavLink to="/engineering" end className="sidebar-link">Projects</NavLink>
                </div>
              )}
            </div>
            <div className="sidebar-section">
              <button
                className={`sidebar-section-header ${activeView === 'reporting' ? 'active' : ''}`}
                onClick={() => handleViewClick('reporting')}
              >
                Reporting
              </button>
            </div>
          </div>
          <div className="sidebar-bottom">
            <NavLink to="/resources" className="sidebar-link">Resources</NavLink>
            <NavLink to="/billing" className="sidebar-link">Billing</NavLink>
            <NavLink to="/settings" className="sidebar-link">Settings</NavLink>
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
