---
name: security
description: Security audit — check authentication, authorization, injection, and data exposure
user-invocable: true
---

# Security Audit

When invoked, audit the codebase for security flaws. Focus on real, exploitable issues rather than theoretical concerns.

## Audit Checklist

### 1. Authentication

Verify every endpoint enforces authentication:

- All `/api/*` endpoints must use `Depends(require_auth)`
- JWT validation: check expiry, algorithm, secret strength
- Cookie settings: `httponly`, `secure`, `samesite` flags
- Auth bypass: no alternative paths that skip `require_auth`
- Session middleware: secret key not hardcoded or weak
- Token leakage: JWTs must not appear in URLs, logs, or error responses

### 2. Authorization

Check that authenticated users can only access what they should:

- No horizontal privilege escalation (user A accessing user B's resources)
- Query jobs: users should only see/modify their own jobs
- Catalog operations: verify appropriate access controls
- Admin-only operations: confirm they are restricted

### 3. Injection

Scan for injection vectors:

- **SQL injection**: raw string formatting in SQL queries, especially in `query_engine.py` and catalog routes
- **DuckDB injection**: user-supplied SQL passed to `conn.execute()` — verify sandboxing
- **Path traversal**: file paths constructed from user input (`WAREHOUSE_PATH`, `RESULTS_PATH`)
- **Command injection**: any use of `subprocess`, `os.system`, or `eval`
- **Template injection**: user input rendered in responses without escaping

### 4. Data Exposure

Check for information leaks:

- Error messages: do they reveal internal paths, stack traces, or schema details?
- API responses: do they return more fields than necessary?
- Debug endpoints: are `/docs`, `/redoc`, `/openapi.json` protected?
- Secrets in code: `.env` values, API keys, passwords in source files
- CORS: is `allow_origins` restricted to the frontend URL only?
- Result files: are Parquet result files accessible only to the owning user?

### 5. Input Validation

Verify all external input is validated:

- Request body validation via Pydantic models (not manual parsing)
- Query parameters: type-checked and bounded
- Path parameters: validated against expected patterns
- File uploads (if any): size limits, type checking
- SQL input: length limits, dangerous statement detection (DROP, ALTER, etc.)

### 6. Dependency and Configuration

- Are dependencies pinned to avoid supply chain attacks?
- Are default passwords or secrets used in production config?
- Is `DEBUG` mode disabled in production?
- Are HTTPS redirects enforced?
- Are rate limits in place for auth endpoints?

## Output Format

Provide:

1. **Severity**: Critical / High / Medium / Low
2. **Findings**: each with file reference, description, and exploit scenario
3. **Recommendations**: specific, actionable fixes for each finding
