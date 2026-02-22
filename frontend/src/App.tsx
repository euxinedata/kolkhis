import { useState, useEffect } from 'react'
import { NavLink, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './auth.tsx'
import { QueryEditor } from './pages/QueryEditor.tsx'
import { QueryHistory } from './pages/QueryHistory.tsx'
import kolkhisLogo from './assets/kolkhis.svg'
import './App.css'

function App() {
  const { user, loading, login, logout } = useAuth()
  const [theme, setTheme] = useState(() => localStorage.getItem('kolkhis_theme') || 'dark')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('kolkhis_theme', theme)
  }, [theme])

  function toggleTheme() {
    setTheme(t => t === 'dark' ? 'light' : 'dark')
  }

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
          <NavLink to="/history">History</NavLink>
        </div>
        <div className="nav-user">
          <button onClick={toggleTheme} className="btn-theme">
            {theme === 'dark'
              ? <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><circle cx="7" cy="7" r="3" /><line x1="7" y1="0.5" x2="7" y2="2" /><line x1="7" y1="12" x2="7" y2="13.5" /><line x1="0.5" y1="7" x2="2" y2="7" /><line x1="12" y1="7" x2="13.5" y2="7" /><line x1="2.4" y1="2.4" x2="3.5" y2="3.5" /><line x1="10.5" y1="10.5" x2="11.6" y2="11.6" /><line x1="2.4" y1="11.6" x2="3.5" y2="10.5" /><line x1="10.5" y1="3.5" x2="11.6" y2="2.4" /></svg>
              : <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="M12 8.5A5.5 5.5 0 1 1 5.5 2c0 4 3 6.5 6.5 6.5z" /></svg>
            }
          </button>
          <span className="user-name">{user.name}</span>
          <button onClick={logout} className="btn-signout">Sign out</button>
        </div>
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<QueryEditor theme={theme} />} />
          <Route path="/history" element={<QueryHistory />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="app-footer"><a href="https://euxine.eu" target="_blank" rel="noopener noreferrer">Euxine Data Platform</a></footer>
    </div>
  )
}

export default App
