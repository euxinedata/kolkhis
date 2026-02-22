---
name: test-frontend
description: Run and write frontend tests for Kolkhis
user-invocable: true
---

# Frontend Testing

## Running Tests

```bash
cd frontend && npm test
```

## Setup

### Install Dependencies

```bash
cd frontend && npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

### Vitest Config

Add to `frontend/vite.config.ts`:

```typescript
/// <reference types="vitest/config" />
export default defineConfig({
  // ...existing config
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
  },
})
```

### Setup File

Create `frontend/src/test-setup.ts`:

```typescript
import '@testing-library/jest-dom'
```

### Package.json Script

Add to `frontend/package.json` scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

## Test Location

Co-located with source: `frontend/src/pages/MyPage.test.tsx` or in `frontend/src/__tests__/`.

## Writing Tests

### Mocking apiFetch

```tsx
import { vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api', () => ({
  apiFetch: vi.fn(),
  API_URL: 'http://test',
}))

import { apiFetch } from '../api'
const mockApiFetch = vi.mocked(apiFetch)
```

### Mocking useAuth

```tsx
vi.mock('../auth.tsx', () => ({
  useAuth: () => ({
    user: { id: '1', email: 'test@example.com', name: 'Test User' },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}))
```

### Component Test Example

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MyPage } from './MyPage'

test('renders page heading', () => {
  render(
    <MemoryRouter>
      <MyPage />
    </MemoryRouter>
  )
  expect(screen.getByText('My Page')).toBeInTheDocument()
})
```

## Conventions

- Wrap components in `<MemoryRouter>` when they use routing hooks
- Mock `apiFetch` rather than making real API calls
- Mock `useAuth` to provide a test user
- Test user interactions with `@testing-library/user-event` when needed
