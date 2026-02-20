import { useAuth } from './auth.tsx'
import './App.css'

function App() {
  const { user, loading, login, logout } = useAuth()

  if (loading) {
    return <p>Loading...</p>
  }

  if (!user) {
    return (
      <div className="card">
        <h1>Kolkhis</h1>
        <button onClick={login}>Sign in with Google</button>
      </div>
    )
  }

  return (
    <div className="card">
      <h1>Kolkhis</h1>
      <p>Welcome, {user.name}</p>
      <button onClick={logout}>Sign out</button>
    </div>
  )
}

export default App
