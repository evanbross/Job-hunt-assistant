# Codex Fix Script — 2026-04-22

Apply each fix independently and commit after each one. Do not batch unrelated changes.
Read the relevant file before editing. Preserve all existing behaviour.

---

## 1. `app.py` — Replace deprecated `datetime.utcnow()` (2 occurrences)

`UTC` is already imported at the top: `from datetime import date, datetime, UTC`

**Line 1861:**
```python
# Before
"created_at": datetime.utcnow().isoformat(),
# After
"created_at": datetime.now(UTC).isoformat(),
```

**Line 2356:**
```python
# Before
"uploaded_at": datetime.utcnow().isoformat(),
# After
"uploaded_at": datetime.now(UTC).isoformat(),
```

---

## 2. `app.py` — Move in-function imports to top of file

Three imports are buried inside functions. Move all three to the top-level import block
(after line 50, alongside the other `from backend.*` imports).

**`import anthropic` (line 621, inside `_generate_board_search_queries`):**
- Add `import anthropic` to the top-level imports.
- Remove the local `import anthropic` at line 621.
- Remove `import anthropic as _anthropic` at line 1777; update all uses of `_anthropic`
  in `cover_letter_chat` to use `anthropic` directly.

**`from google_auth_oauthlib.flow import Flow` (lines 1203 and 1239, inside route handlers):**
- Add `from google_auth_oauthlib.flow import Flow` to the top-level imports.
- Remove both local import lines.

Note: `import fitz` inside the PDF extraction `try` block (line 2341) is intentional —
PyMuPDF is an optional dependency that degrades gracefully. Leave it in the `try` block.

---

## 3. `app.py` — Fix `fitz.open` file-handle leak (line 2343)

The current code calls `doc.close()` manually, which leaks if `get_text()` raises.

```python
# Before
doc = fitz.open(str(save_path))
text = "\n".join(page.get_text() for page in doc)
doc.close()

# After
with fitz.open(str(save_path)) as doc:
    text = "\n".join(page.get_text() for page in doc)
```

---

## 4. `backend/fit_scorer.py` — Guard against empty API response

`response.content[0].text` raises `IndexError` if Anthropic returns an empty content
list (e.g. after a safety filter trip).

Add a guard immediately after the `messages.create` call:

```python
response = self._client.messages.create(...)

if not response.content:
    raise ValueError("Anthropic scorer returned an empty response (possible safety filter)")

raw = response.content[0].text.strip()
```

---

## 5. `backend/career_site_scanner.py` — Fix substring match in `_extract_json_array_by_key`

**Problem (around line 223):** `marker = f'"{key}":['` matches `"otherJobPostings":[`
when searching for `"jobPostings":[` because it uses plain string `in`.

**Fix:** Use a regex that requires the key to be preceded by `{` or `,` (with optional
whitespace) so it only matches whole keys:

```python
import re  # already imported

pattern = re.compile(
    r'[{,]\s*"' + re.escape(key) + r'"\s*:\s*\['
)
match = pattern.search(text)
if not match:
    return []
start = text.index("[", match.start())
# ... rest of extraction logic unchanged
```

---

## 6. `backend/sheets_client.py` — Only delete OAuth token on `invalid_grant`

**Problem:** The `RefreshError` handler deletes the token file for any error, including
transient network issues.

Find the `RefreshError` handler and narrow the condition:

```python
from google.auth.exceptions import RefreshError

except RefreshError as exc:
    if "invalid_grant" in str(exc).lower():
        GOOGLE_TOKEN_FILE.unlink(missing_ok=True)
        logger.warning("Google token invalidated (invalid_grant); re-auth required.")
    else:
        logger.warning("Google token refresh failed (transient?): %s", exc)
    raise NotAuthenticatedError("Google token refresh failed") from exc
```

---

## 7. `frontend/src/components/CareerSiteResultsPanel.jsx` — Fix `setInterval` async race and double-start

**Problem A (async race):** The `useEffect` cleanup fires before `fetchStatus()` resolves,
so `cancelled` is already set but the async callback hasn't checked it yet. The interval
then starts on an unmounted component.

**Problem B (double-start):** `runScanNow` and `runScanForSite` assign to `pollRef.current`
without first clearing any existing interval, so a double-click leaks the first one.

Fix A — add a `cancelled` flag to the effect:
```js
useEffect(() => {
  let cancelled = false;
  fetchStatus().then((s) => {
    if (cancelled) return;
    if (s.running) {
      pollRef.current = setInterval(pollStatus, 1500);
    }
  });
  return () => {
    cancelled = true;
    clearInterval(pollRef.current);
  };
}, []);
```

Fix B — clear before reassigning in every handler that starts a new interval:
```js
// At the top of any function that does: pollRef.current = setInterval(...)
clearInterval(pollRef.current);
pollRef.current = setInterval(pollStatus, 1500);
```

Apply this pattern to `runScanNow`, `runScanForSite`, and any other handlers that
set `pollRef.current`.

---

## 8. `frontend/src/components/SettingsPanel.jsx` — Three small fixes

### 8a. Check `res.ok` before refetching

Every write call (toggle enabled, delete, save penalty, etc.) currently refetches
regardless of whether the write succeeded. Add a `res.ok` check:

```js
const res = await fetch("/api/...", { method: "PUT", body: ... });
if (!res.ok) {
  window.alert(`Save failed (${res.status})`);
  return;
}
await fetchSources();   // only refetch on success
```

Apply this pattern to: `toggleEnabled`, `deleteSource`, `toggleCareerSiteEnabled`,
`deleteCareerSite`, `savePenalty`, `deletePenalty`.

### 8b. Validate `email_lookback_days` before sending

```js
// Before
setValue("email_lookback_days", parseInt(e.target.value, 10))

// After — at the point of saving (not on every keystroke):
const days = parseInt(value, 10);
if (!Number.isFinite(days) || days < 1 || days > 90) {
  window.alert("Lookback days must be a number between 1 and 90.");
  return;
}
```

### 8c. Consistent `window.confirm`

Replace every bare `confirm(...)` call with `window.confirm(...)` to match the rest
of the codebase and satisfy linter rules.

---

## 9. `backend/db.py` — Add `_ALLOWED_COLUMNS` allowlists to repository insert/update helpers

**Problem:** Any repository that builds SQL like `f"INSERT INTO jobs ({cols}) VALUES ..."` 
by interpolating `dict.keys()` directly has no guard against unknown or attacker-controlled
column names if the calling code ever passes through user input.

For each repository class that has a generic `insert` or `update` method, add:

```python
_ALLOWED_COLUMNS: frozenset[str] = frozenset({
    # list every valid column name for this table
    "company", "job_title", "url", ...
})

def insert(self, row: dict) -> int:
    unknown = set(row.keys()) - self._ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"Attempt to insert unknown columns: {unknown}")
    # ... existing SQL building ...
```

Do this for: `SQLiteJobRepository`, `SQLiteCareerScanResultRepository`,
`SQLiteCareerSiteSourceRepository`, and any other repo that uses dynamic column building.

Look up the actual column names from the `CREATE TABLE` statements in `init_db()` to
build each frozenset accurately. Do not guess — read the schema.

> **Note:** This is the most invasive fix. Do it last and test that existing
> inserts still work after adding the allowlists.
