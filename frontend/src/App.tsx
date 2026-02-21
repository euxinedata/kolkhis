import { NavLink, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './auth.tsx'
import { QueryEditor } from './pages/QueryEditor.tsx'
import { QueryHistory } from './pages/QueryHistory.tsx'
import { CatalogBrowser } from './pages/CatalogBrowser.tsx'
import './App.css'

function App() {
  const { user, loading, login, logout } = useAuth()

  if (loading) {
    return <div className="app-loading">Loading...</div>
  }

  if (!user) {
    return (
      <div className="login-screen">
        <h1>Kolkhis</h1>
        <p className="login-subtitle">Data Warehouse</p>
        <button onClick={login}>Sign in with Google</button>
      </div>
    )
  }

  return (
    <div className="app">
      <nav className="app-nav">
        <span className="app-title">Kolkhis</span>
        <div className="nav-links">
          <NavLink to="/" end>Query</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/catalog">Catalog</NavLink>
        </div>
        <div className="nav-user">
          <span className="user-name">{user.name}</span>
          <button onClick={logout} className="btn-signout">Sign out</button>
        </div>
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<QueryEditor />} />
          <Route path="/history" element={<QueryHistory />} />
          <Route path="/catalog" element={<CatalogBrowser />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
