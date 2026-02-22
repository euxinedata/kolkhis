---
name: review
description: Critical review of current work — check patterns, scope, and consistency
user-invocable: true
---

# Critical Review

When invoked, review the current work or proposed changes with a critical eye. Check for consistency, scope creep, and potential issues.

## Review Checklist

### 1. Pattern Consistency

Check against established project patterns:

- **Routers**: prefix `/api/<resource>`, all endpoints use `Depends(require_auth)`, Pydantic models for request bodies
- **Models**: `Mapped` typed columns, `server_default=func.now()` for timestamps, defined in `models.py`
- **Database access**: `get_db` dependency in endpoints, `async_session()` context manager elsewhere — never both in the same function
- **Frontend API calls**: uses `apiFetch<T>()`, not raw `fetch` (except `auth.tsx`)
- **Frontend pages**: named exports, routes added in `App.tsx`
- **Config**: env vars in `config.py` with `os.environ.get()` defaults

### 2. Scope Check

Flag anything that:

- Adds features not explicitly requested
- Introduces abstractions for single-use cases
- Adds error handling for impossible scenarios
- Refactors code outside the current task
- Adds comments, docstrings, or type annotations to unchanged code
- Introduces backward-compatibility shims instead of just changing the code

### 3. Integration Integrity

Verify at integration points:

- New backend endpoints: are they mounted in `main.py`?
- New frontend pages: are they routed in `App.tsx` with a `NavLink`?
- New models: are they imported in a module that triggers table creation?
- New config vars: are they in `config.py` and documented?
- Database changes: do they break existing queries or models?

### 4. Data Flow Verification

Trace the complete data flow for any new feature:

- Backend: request → auth → validation → business logic → database → response
- Frontend: user action → API call → state update → render
- Background tasks: submission → polling → completion → result display

### 5. Common Pitfalls

Watch for:

- Forgetting `await` on async operations
- Missing `Depends(require_auth)` on new endpoints
- Frontend polling without cleanup on unmount
- Hardcoded URLs instead of using `API_URL` or config
- Synchronous database calls in async context
- Missing CORS implications for new endpoints
- Committing `.env` or secrets

### 6. Testing Gap Analysis

For any change, ask:

- Can this be tested with the existing test setup?
- What's the minimal test that proves this works?
- Are there edge cases that should be covered?
- Does this break any existing test assumptions?

## Output Format

When reviewing, provide:

1. **Status**: OK / Issues Found
2. **Issues** (if any): specific, actionable items with file references
3. **Suggestions** (optional): improvements that are clearly out of scope but worth noting for later
