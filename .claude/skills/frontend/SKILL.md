---
name: frontend
description: Frontend development conventions for Kolkhis (React 19 + TypeScript + Vite 7)
user-invocable: false
---

# Frontend Development Conventions

## Page Components

Pages live in `frontend/src/pages/` as named exports:

```tsx
export function MyPage() {
  return <div>...</div>
}
```

## Routing

Add routes in `App.tsx`:

```tsx
import { MyPage } from './pages/MyPage.tsx'

// In the nav-links div:
<NavLink to="/mypage">My Page</NavLink>

// In the Routes block:
<Route path="/mypage" element={<MyPage />} />
```

## API Calls

Use `apiFetch<T>()` from `api.ts` for all backend requests:

```tsx
import { apiFetch } from '../api'

const data = await apiFetch<MyType>('/api/resource')

const result = await apiFetch<MyType>('/api/resource', {
  method: 'POST',
  body: JSON.stringify({ field: value }),
})
```

- Credentials are included automatically
- Content-Type defaults to `application/json`
- Throws `Error` on non-OK responses
- `API_URL` is configurable via `VITE_API_URL` env var

## Auth

Use the `useAuth()` hook for user state:

```tsx
import { useAuth } from './auth.tsx'

const { user, loading, login, logout } = useAuth()
```

- `user` is `{ id, email, name } | null`
- `loading` is `true` during initial auth check
- App renders login screen when `user` is null

## Polling Pattern

For async operations, use `setInterval` with ref-based cleanup:

```tsx
const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

function startPolling(id: string) {
  if (pollRef.current) clearInterval(pollRef.current)
  pollRef.current = setInterval(async () => {
    const job = await apiFetch<JobStatus>(`/api/resource/${id}`)
    if (job.status === 'completed' || job.status === 'failed') {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, 1000)
}

// Cleanup on unmount
useEffect(() => {
  return () => { if (pollRef.current) clearInterval(pollRef.current) }
}, [])
```

## CSS Conventions

- Status classes: `status-completed`, `status-failed`, `status-running`, `status-pending`
- Tables: wrap in `<div className="table-container">`, use `<table>` directly
- Pagination: `<div className="pagination">`
- Dark theme by default (styles in `App.css` and `index.css`)

## Key Files

- `frontend/src/App.tsx` — Layout, nav, routing
- `frontend/src/api.ts` — `apiFetch<T>()` wrapper and `API_URL`
- `frontend/src/auth.tsx` — `AuthProvider`, `useAuth()` hook
- `frontend/src/pages/QueryEditor.tsx` — Reference page with polling, pagination
- `frontend/src/App.css` — Component styles, status classes, tables
- `frontend/src/index.css` — Global styles, dark theme
