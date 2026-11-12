@../Dev-Standards/COMMON_CODING_STANDARDS.md
@../Dev-Standards/PYTHON_STANDARDS.md
@../Dev-Standards/FLASK_STANDARDS.md
@../Dev-Standards/JAVASCRIPT_TYPESCRIPT_STANDARDS.md
@../Dev-Standards/REACT_STANDARDS.md

# Read before touching these areas (use Read tool at session start if relevant):
# - DATABASE_STANDARDS.md    — DB/repo/migration changes
# - TESTING_STANDARDS.md     — any test file changes
# - INFRASTRUCTURE_STANDARDS.md — Docker, CI/CD, env config
# - GOOGLE_CODING_STANDARDS.md  — Google APIs, OAuth, Sheets, Gmail, Drive

## Running tests

The full suite (`uv run pytest`, ~2,050 tests against real Postgres) takes ~15 minutes.
Follow `../Dev-Standards/TESTING_STANDARDS.md` "Run Targeted Tests While Iterating; the
Full Suite Once" on **every** edit, not only test changes: targeted test files or the
narrowest direct check while iterating, the full suite once before committing (or CI).
