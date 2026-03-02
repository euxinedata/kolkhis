import { NavLink, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './auth.tsx'
import { Billing } from './pages/Billing.tsx'
import { QueryEditor } from './pages/QueryEditor.tsx'
import { QueryHistory } from './pages/QueryHistory.tsx'
import Resources from './pages/Resources.tsx'
import { Settings } from './pages/Settings.tsx'
import kolkhisLogo from './assets/kolkhis.svg'
import './App.css'

function App() {
  const { user, loading, login, logout } = useAuth()

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

  return (
    <div className="app">
      <nav className="app-nav">
        <span className="app-title">
          <img src={kolkhisLogo} alt="" className="nav-logo" />
          Kolkhis
        </span>
        <div className="nav-links">
          <NavLink to="/" end>Query</NavLink>
          <NavLink to="/resources">Resources</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/billing">Billing</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </div>
        <div className="nav-user">
          <span className="user-name">{user.name}</span>
          <button onClick={logout} className="btn-signout">Sign out</button>
        </div>
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<QueryEditor />} />
          <Route path="/resources" element={<Resources />} />
          <Route path="/history" element={<QueryHistory />} />
          <Route path="/billing" element={<Billing />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
