---
name: code-review
description: Code review assistant. Use when asked to review code, check for security issues, audit code quality, review pull requests, or check Go/Python/JS conventions. Covers OWASP top 10, language conventions, and performance patterns.
license: Internal
metadata:
  author: trngthnh369
  version: "1.0.0"
---

# Code Review

Systematic code review checklist for Go, Python, and JavaScript/TypeScript projects.

## Review Process

1. **Read the diff** — Understand what changed and why
2. **Security scan** — Check OWASP top 10
3. **Language conventions** — Check idiomatic patterns
4. **Performance** — Check for common anti-patterns
5. **Summarize** — List findings by severity (Critical → Warning → Info)

## Security Checklist (OWASP Top 10)

| # | Risk | What to Check |
|---|------|---------------|
| 1 | **Injection** | SQL: parameterized queries only (`$1, $2` in Go/pg, `%s` with params in Python). No string concatenation for queries. Command injection: no unsanitized input in `exec`, `os.system`, `child_process` |
| 2 | **Broken Auth** | Tokens validated server-side. Sessions expire. No hardcoded secrets. API keys in env vars, not code |
| 3 | **Sensitive Data** | No PII in logs. Passwords hashed (bcrypt/argon2). HTTPS enforced. Secrets encrypted at rest |
| 4 | **XXE** | XML parsers: disable external entities. Prefer JSON over XML |
| 5 | **Broken Access** | Authorization checked per-request. Multi-tenant: tenant_id in WHERE clauses. No IDOR (direct object references without ownership check) |
| 6 | **Misconfig** | Debug mode off in production. CORS restrictive. Error messages generic (no stack traces) |
| 7 | **XSS** | User input sanitized before HTML render. React: no `dangerouslySetInnerHTML` with user data. CSP headers |
| 8 | **Deserialization** | No `pickle.loads` on untrusted data. JSON schema validation on API input |
| 9 | **Known Vulns** | Dependencies up-to-date. No CVEs in lockfile (`npm audit`, `go vuln`) |
| 10 | **Logging** | Auth events logged. Failed attempts tracked. No sensitive data in logs |

## Go Conventions

```
✅ DO                              ❌ DON'T
─────────────────────────────────────────────────────
errors.Is(err, sentinel)           err == sentinel
switch/case on same var            if/else if chains
append(dst, src...)                loop-based append
context.Context as first param     global state
defer for cleanup                  manual cleanup in happy path
errors.New / fmt.Errorf("%w")      string error returns
```

**Error handling:**
- Always check returned errors — never `_` discard
- Wrap with context: `fmt.Errorf("fetch user %s: %w", id, err)`
- Use sentinel errors for expected conditions, wrap for unexpected

**Concurrency:**
- Protect shared state with `sync.Mutex` or channels
- Always `defer mu.Unlock()` after `mu.Lock()`
- Check for goroutine leaks: every goroutine must have an exit path
- Use `context.Context` for cancellation

**Performance:**
- Pre-allocate slices when size is known: `make([]T, 0, n)`
- Use `strings.Builder` for string concatenation in loops
- Avoid `reflect` in hot paths

## Python Conventions

- Type hints on public functions
- `with` statement for resource management (files, connections)
- List/dict comprehensions over `map`/`filter` when readable
- `pathlib.Path` over `os.path` string manipulation
- f-strings over `.format()` or `%`

## JavaScript/TypeScript Conventions

- `const` by default, `let` when reassignment needed, never `var`
- Optional chaining `?.` and nullish coalescing `??`
- `async/await` over `.then()` chains
- TypeScript: avoid `any`, use `unknown` + type guards
- Early returns to reduce nesting

## Database Query Review

- **N+1 queries**: Look for queries inside loops → batch with `WHERE id IN ($1)`
- **Missing indexes**: `WHERE`, `JOIN`, `ORDER BY` columns should have indexes
- **Unbounded queries**: Always have `LIMIT` on user-facing list endpoints
- **Transaction scope**: Keep transactions short, no external calls inside TX

## Output Format

```
## Code Review: {file/PR name}

### 🔴 Critical
- [Finding with file:line reference and fix suggestion]

### 🟡 Warning
- [Finding with explanation]

### 🔵 Info
- [Suggestion for improvement]

### ✅ Looks Good
- [Positive observations]
```
