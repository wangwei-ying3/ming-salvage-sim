## Session 2026-06-24 13:36 Local

### Goal
- Fix the two high-confidence pytest compatibility failures only: Windows/temp SQLite handling in `tests/test_arms_and_troops.py` and missing `_DummyDB.army_held_arms_all` in `tests/test_structured_directives.py`.

### What I inspected
- `AGENTS.md`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- `tests/test_arms_and_troops.py`
- `tests/test_structured_directives.py`
- `ming_sim/db/base.py` for the `GameDB` connect path

### Bugs found
- [High] `tests/test_arms_and_troops.py`: file-backed SQLite test DBs failed on Windows/local sandbox with `sqlite3.OperationalError: unable to open database file`.
- [Medium] `tests/test_structured_directives.py`: `_DummyDB` lacked `army_held_arms_all`, while `build_simulator_payload` reasonably calls that DB method.
- [Low] Local workspace: SQLite file probes left inaccessible `tmp*` directories and `pytest_probe.db-journal`; deletion attempts were denied by the OS/tool policy.

### Changes made
- `tests/test_arms_and_troops.py`: changed the two DB-backed test fixtures to use `GameDB(":memory:", ...)` and close the DB in `tearDown`, avoiding Windows file-lock/path issues without changing production DB logic.
- `tests/test_structured_directives.py`: added the minimal `_DummyDB.army_held_arms_all()` stub returning `{}`.
- `pytest.ini`: limited default pytest collection to `tests`, so `python -m pytest -q` does not scan unrelated root-level temp remnants.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, `docs/FIX_LOG.md`: recorded this repair loop.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests/test_arms_and_troops.py -q` | FAIL | Initial TemporaryDirectory path still failed with `sqlite3.OperationalError`; root cause was file-backed SQLite not usable in the local sandbox. |
| `.\.venv\Scripts\python.exe -m pytest tests/test_arms_and_troops.py -q` | PASS | `19 passed`; pytest cache warning remained. |
| `.\.venv\Scripts\python.exe -m pytest tests/test_structured_directives.py -q` | PASS | `3 passed`; pytest cache warning remained. |
| `.\.venv\Scripts\python.exe -m pytest -q` | FAIL | Failed collecting inaccessible `tmp*` dirs left by SQLite file probes. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `49 passed`; pytest cache warning remained. |
| `git diff -- tests/test_arms_and_troops.py tests/test_structured_directives.py` | PASS | Reviewed scoped test diff. |
| `git status --short` | PARTIAL | Shows intended modified/untracked files plus inaccessible temp-dir warnings and `pytest_probe.db-journal` residue. |

### Current status
- PASS for the three requested pytest commands after the scoped test fixes and `pytest.ini` collection guard.

### Remaining blockers
- Inaccessible root-level `tmp*` directories and `pytest_probe.db-journal` were created by local SQLite file probes and could not be deleted due access denied/tool policy.
- Pytest cannot write `.pytest_cache` in this workspace, producing warnings only.

### Next recommended action
- Clean the inaccessible `tmp*` directories and `pytest_probe.db-journal` from the workspace with normal local file-system permissions, then rerun `git status --short`.

### Files changed this session
- `tests/test_arms_and_troops.py`
- `tests/test_structured_directives.py`
- `pytest.ini`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 13:59 Local

### Goal
- Make `scripts/verify_local.ps1` robust against incomplete frontend dependencies without changing business code.

### What I inspected
- `web/package.json`
- `web/package-lock.json`
- `scripts/verify_local.ps1`
- Presence of `web/node_modules/.bin/tsc.cmd` and `web/node_modules/.bin/vite.cmd`

### Bugs found
- [Medium] `scripts/verify_local.ps1`: default frontend build checked only `web/node_modules`, so a half-installed dependency tree failed with `'tsc' is not recognized`.
- [Medium] Local npm install: `npm ci` failed with Windows `EPERM` errors in `web/node_modules` and npm cache paths.

### Changes made
- `scripts/verify_local.ps1`: default frontend build now requires both `web/node_modules/.bin/tsc.cmd` and `web/node_modules/.bin/vite.cmd`; otherwise it skips build and prompts `-Install`.
- `scripts/verify_local.ps1`: `-Install` uses `npm ci` when `web/package-lock.json` exists, otherwise `npm install`, then validates `tsc.cmd` and `vite.cmd`.
- `scripts/verify_local.ps1`: npm permission failures now print Windows recovery advice before stopping.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `Test-Path web\package-lock.json; Test-Path web\node_modules\.bin\tsc.cmd; Test-Path web\node_modules\.bin\vite.cmd` | PASS | Lockfile exists; `tsc.cmd` and `vite.cmd` were missing. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1 -Install` | FAIL | Used `npm ci`; npm failed with `EPERM` permission errors before frontend build. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | PASS | Python checks passed, pytest `49 passed, 1 warning`; frontend build skipped because dependencies are incomplete. |

### Current status
- PARTIAL: default verification passes, but install/build remains blocked by local Windows/npm `EPERM` permissions.

### Remaining blockers
- `npm ci` cannot complete due `EPERM` errors in `web/node_modules` and `C:\Users\Lenovo\AppData\Local\npm-cache`.

### Next recommended action
- Delete `web/node_modules`, clean npm cache, allowlist the project directory in antivirus/real-time scanning, then rerun `scripts\verify_local.ps1 -Install`.

### Files changed this session
- `scripts/verify_local.ps1`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:22 Local

### Goal
- Goal 7: inspect the `/bg_ending.webp` Vite static resource warning and determine whether it is benign public-root usage or a missing/path resource issue.

### What I inspected
- `web\public\bg_ending.webp`
- `web\public\`
- `web\src\styles.css`
- `docs\AGENT_PROGRESS.md`
- `docs\BUG_QUEUE.md`
- `docs\FIX_LOG.md`

### Bugs found
- [Low] `web/src/styles.css`: references `/bg_ending.webp` at lines 2972, 4858, and 4896, but `web/public/bg_ending.webp` does not exist and no `*bg_ending*` file exists under `web`. This is a missing project asset, not a benign public-asset warning.
- [Medium] Local frontend environment: `npm run build` is still blocked before asset resolution by Vite/esbuild `Error: spawn EPERM` while loading `vite.config.ts`.

### Changes made
- No frontend source, gameplay, business logic, LLM, upload, save, DeepSeek, or reducer changes.
- No image asset was added or downloaded.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded the Goal 7 investigation, classification, and verification blocker.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `Test-Path web\public\bg_ending.webp` | PASS | Returned `False`; expected file is missing. |
| `Get-ChildItem web -Recurse -Filter "*bg_ending*"` | PASS | Returned no matches under `web`. |
| `Select-String -Path web\src\**\*.* -Pattern "bg_ending"` | PASS | Returned no matches with that glob pattern. |
| `Select-String -Path web\**\*.* -Pattern "/bg_ending.webp" -ErrorAction SilentlyContinue` | PASS | Found references in `web\src\styles.css` at lines 2972, 4858, and 4896. |
| `Select-String -Path web\src\styles.css -Pattern "/bg_ending.webp" -Context 2,2` | PASS | Confirmed all references are CSS background URLs using the Vite public-root path. |
| `Get-ChildItem web\public -Force \| Select-Object Name,Length,Mode` | PASS | Confirmed many public assets exist, but no `bg_ending.webp`. |
| `npm run build` from `web` | FAIL | Failed before asset warning with Vite/esbuild `Error: spawn EPERM` while loading `vite.config.ts`. |
| `git status --short` | PASS | Shows existing tracked changes plus a local warning for inaccessible `pytest-cache-files-hgfs24uv/`. |

### Current status
- PARTIAL: Goal 7 classification is complete, but the requested build verification is blocked by the existing local Vite/esbuild `spawn EPERM` environment issue.

### Remaining blockers
- `web/public/bg_ending.webp` is missing. Because no same-named asset exists elsewhere under `web`, the project/user needs to provide the intended image or approve a specific asset/path replacement.
- `npm run build` cannot currently verify the final warning state because Vite fails earlier with esbuild `spawn EPERM`.

### Next recommended action
- Provide the intended `bg_ending.webp` asset for `web/public/`, or explicitly approve removing/changing the CSS background reference in a future scoped pass.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 17:37 Local

### Goal
- Goal 9B: fix avatar/portrait upload security risks from Goal 9A without real LLM API calls, gameplay changes, frontend UI changes, or unrelated refactors.

### What I inspected
- `web_app.py` custom portrait upload, delete, and read routes.
- `requirements.txt` runtime dependencies.
- `tests/` FastAPI test patterns and avatar upload coverage gap.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`.

### Bugs found
- [High] `web_app.py`: custom portrait filenames used raw character names, allowing path traversal/invalid Windows filename risks for unsafe persisted names.
- [Medium] `web_app.py`: uploads trusted `UploadFile.content_type`, accepted spoofed payloads, and read the full file before enforcing the 8 MB limit.
- [Medium] `web_app.py`: upload/delete sequencing was not atomic across file writes and DB updates.
- [Low] `tests/`: no backend regression coverage existed for avatar upload security.

### Changes made
- `web_app.py`: added NFKC+SHA-256 portrait storage keys, path containment checks, chunked upload reads, Pillow image verification/re-encoding, same-directory temp writes with `os.replace`, DB rollback for failed upload updates, DB-first delete behavior, and safe custom portrait reads.
- `requirements.txt`: added `Pillow>=10.4.0`.
- `tests/test_avatar_upload_security.py`: added mock-only FastAPI regression tests for malicious names, spoofed/SVG/corrupt content, valid PNG/JPEG/WebP, oversized uploads, rollback, delete behavior, and path containment.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 9B fix and verification.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests\test_avatar_upload_security.py -q` | FAIL | Red/diagnostic run before the implementation showed expected failures in path safety, content validation, size status, and rollback behavior. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_avatar_upload_security.py -q` | PASS | `9 passed, 2 warnings`; warnings are Starlette `TestClient` deprecation and known `.pytest_cache` permission warning. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `61 passed, 2 warnings`; no real LLM API was called. |
| `rg -n "await file\.read\(\)|_find_portrait_file|_portrait_storage_key|_read_upload_limited|_validate_and_normalize_portrait|os\.replace|/portraits/custom" web_app.py tests\test_avatar_upload_security.py` | PASS | Confirmed no unbounded `await file.read()` remains in the upload route and the custom portrait route uses the safe helpers. |
| `.NET Directory.Delete(...)` for local test temp directories | FAIL | Local WinError 5 denied cleanup of generated test directories; they remain untracked workspace artifacts and are not source changes. |

### Current status
- PASS: Goal 9B security fix and regression tests are implemented. No real LLM API was called.

### Remaining blockers
- Local workspace deletion permissions still block cleanup of some generated test directories.
- Existing `.pytest_cache` permission warning remains non-blocking; pytest passes.

### Next recommended action
- Commit Goal 9B source, dependency, test, and docs changes after reviewing the diff; clean inaccessible local temp directories manually if the OS releases them.

### Files changed this session
- `web_app.py`
- `requirements.txt`
- `tests/test_avatar_upload_security.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:27 Local

### Goal
- Goal 7B: safely remove missing `/bg_ending.webp` static resource references from CSS without adding unknown images or changing gameplay, backend, LLM, uploads, saves, DeepSeek, or reducers.

### What I inspected
- `web/src/styles.css` around the three known references at the former lines 2972, 4858, and 4896.
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

### Bugs found
- [Low] `web/src/styles.css`: three ending-screen background declarations referenced missing `/bg_ending.webp`; each declaration already had gradient fallback layers, so the missing image reference could be safely removed without adding a replacement asset.
- [Medium] Local frontend environment: `npm run build` and the full `scripts\verify_local.ps1` gate still fail at Vite/esbuild `Error: spawn EPERM` before Vite reaches resource processing.

### Changes made
- `web/src/styles.css`: removed `url("/bg_ending.webp")` from `.ending-document`, `.fullscreen-modal.modal-bg-ending`, and `.modal-bg-ending .ending-document`; retained the existing dark gradient fallbacks.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded the Goal 7B fix and verification results.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `Get-Content web\src\styles.css \| Select-Object -Skip 2958 -First 24` | PASS | Confirmed `.ending-document` had radial/linear gradient fallback plus missing image URL. |
| `Get-Content web\src\styles.css \| Select-Object -Skip 4848 -First 18` | PASS | Confirmed `.fullscreen-modal.modal-bg-ending` had a linear gradient fallback plus missing image URL. |
| `Get-Content web\src\styles.css \| Select-Object -Skip 4886 -First 18` | PASS | Confirmed `.modal-bg-ending .ending-document` had radial/linear gradient fallback plus missing image URL. |
| `Select-String -Path web\src\**\*.* -Pattern "bg_ending"` | PASS | No matches after the CSS fix. |
| `Select-String -Path web\**\*.* -Pattern "/bg_ending.webp" -ErrorAction SilentlyContinue` | PASS | No matches after the CSS fix. |
| `npm run build` from `web` | FAIL | Blocked by Vite/esbuild `Error: spawn EPERM` while loading `vite.config.ts`; no evidence of remaining `bg_ending` warning. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Python compile and pytest passed (`49 passed, 1 warning`); frontend build failed with the same Vite/esbuild `spawn EPERM`. |

### Current status
- PARTIAL: the missing `bg_ending` references are fixed and searches are clean; build verification remains blocked by local Vite/esbuild `spawn EPERM`.

### Remaining blockers
- `ENV_BLOCKER_INTERMITTENT`: Vite/esbuild cannot spawn during frontend build in this local environment.
- Pytest cache warning remains due to local `.pytest_cache` permission denial, but tests pass.

### Next recommended action
- Resolve the local esbuild spawn permission issue, then rerun `cd web; npm run build` and the full `scripts\verify_local.ps1` gate to confirm the `bg_ending` warning is gone in a complete build.

### Files changed this session
- `web/src/styles.css`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 14:22 Local

### Goal
- Fix Markdown bold rendering for AI text in frontend modals without backend, prompt, or broad UI changes.

### What I inspected
- `web/src/components/modals.tsx`
- `web/package.json`
- Existing frontend test setup

### Bugs found
- [Medium] `web/src/components/modals.tsx`: report/chat/secret-order AI text rendered `**bold**` markers as literal text because output was inserted as plain strings.
- [Medium] Local frontend build environment: `npm run build` reaches Vite, then fails with `Error: spawn EPERM` while starting esbuild.

### Changes made
- `web/src/components/modals.tsx`: added a safe inline bold renderer for `**text**` that returns React text nodes and `<strong>` elements without `dangerouslySetInnerHTML`.
- `web/src/components/modals.tsx`: applied the renderer to month-end reports, history reports, detail narrative, state report, edict report, ending summary, chat messages, and secret-order text fields.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `cd web; npm run build` | FAIL | `tsc -b` completed, then Vite failed loading config because esbuild `spawn EPERM`. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Python compileall and pytest passed (`49 passed, 1 warning`); frontend build failed with the same esbuild `spawn EPERM`. |

### Current status
- PARTIAL: Markdown bold rendering code is implemented; automated frontend build is blocked by local Windows/esbuild execution permissions.

### Remaining blockers
- `esbuild spawn EPERM` during Vite build. Recommended recovery: delete `web/node_modules`, clean npm cache, disable antivirus real-time scanning or allowlist the project directory, then reinstall frontend dependencies.

### Next recommended action
- After resolving local esbuild execution permissions, rerun `cd web && npm run build` and then `scripts\verify_local.ps1`.

### Files changed this session
- `web/src/components/modals.tsx`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

### Manual acceptance
- Input `这是 **重点** 内容`: `重点` should render bold.
- Input `<script>alert(1)</script> **安全**`: the script tag should display as text and not execute; `安全` should render bold.

## Session 2026-06-24 14:43 Local

### Goal
- Goal 0: read context and freeze current status.
- Goal 1: clean workspace hygiene and ensure local artifacts are ignored.

### What I inspected
- `AGENTS.md`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- `.gitignore`
- `git status --short`
- `git diff --stat`

### Bugs found
- [Low] `.gitignore`: editor swap files were not ignored.
- [Low] Workspace local artifacts: `.pytest.ini.swp` and `pytest_probe.db` exist in the workspace but cannot be deleted due `Access is denied`.

### Changes made
- `.gitignore`: added `*.swp` and `*.swo` ignore rules.
- `docs/AGENT_PROGRESS.md`: recorded Goal 0 and Goal 1 checkpoint results.
- `docs/BUG_QUEUE.md`: recorded remaining local cleanup ENV_BLOCKER.
- `docs/FIX_LOG.md`: recorded the `.gitignore` hygiene update.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `git status --short` | PASS | Captured initial and final status. Final status no longer shows `.pytest.ini.swp`. |
| `git diff --stat` | PASS | Captured current modified tracked files. |
| `cmd /c "del /F /Q .pytest.ini.swp"` | FAIL | Access denied; file is now ignored but still present locally. |
| `Get-ChildItem -Force -Name tmp*,pytest_probe.db*,*.swp,*.swo` | PASS | Listed `pytest_probe.db` and `.pytest.ini.swp`. |
| `cmd /c "del /F /Q pytest_probe.db"` | FAIL | Access denied; file is ignored but still present locally. |
| `Get-Process python,node -ErrorAction SilentlyContinue \| Select-Object Id,ProcessName,Path` | PARTIAL | Showed a Node process; no Python process listed. |

### Current status
- PARTIAL: Git-visible workspace hygiene is clean for local artifacts, but ignored local files remain due access denied.

### Remaining blockers
- ENV_BLOCKER: `.pytest.ini.swp` and `pytest_probe.db` could not be removed by this session. Suggested user action: close editors/Codex/VS Code/Node processes, inspect with `Get-Process python,node`, then delete manually if still present.

### Next recommended action
- Proceed to Goal 2 only after deciding whether to manually remove ignored local residue or accept it as non-committable local state.

### Files changed this session
- `.gitignore`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 14:54 Local

### Goal
- Goal 2: verify `scripts/verify_local.ps1` default-mode stability.

### What I inspected
- `scripts/verify_local.ps1`
- `git diff -- scripts/verify_local.ps1`
- Default verification output

### Bugs found
- [Medium] Frontend build environment: default verification reaches `npm run build`, but Vite fails while spawning esbuild with `Error: spawn EPERM`.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded Goal 2 verification result.
- `docs/BUG_QUEUE.md`: kept the frontend `esbuild spawn EPERM` item open as an ENV_BLOCKER.
- `docs/FIX_LOG.md`: recorded that no script change was needed for Goal 2 because default-mode behavior was correct.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `git diff -- scripts\verify_local.ps1` | PASS | Empty output because the script is currently untracked; direct inspection showed mode-gated behavior. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Default mode did not install dependencies or start uvicorn. Compileall passed, pytest passed (`49 passed, 1 warning`), frontend build failed with `esbuild spawn EPERM`. |

### Current status
- PARTIAL: Goal 2 script behavior is correct, but full default verification exits nonzero because frontend build hits ENV_BLOCKER `esbuild spawn EPERM`.

### Remaining blockers
- ENV_BLOCKER: Vite/esbuild cannot spawn esbuild during frontend build. Do not change source to work around it; proceed to Goal 3 diagnostics next.

### Next recommended action
- Execute Goal 3 to diagnose `esbuild spawn EPERM` with `tsc.cmd`, `vite.cmd`, `esbuild.cmd --version`, npm config, and build checks.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:15 Local

### Goal
- Goal 6: run the FastAPI backend smoke test only, verify uvicorn starts and is stopped safely, without calling real LLM APIs or reading real API keys.

### What I inspected
- `scripts/verify_local.ps1`
- `web_app.py` startup/import area and route setup
- Uvicorn smoke stdout/stderr logs in `%TEMP%`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

### Bugs found
- [Medium] `scripts/verify_local.ps1`: `-Smoke` mode still ran the default compile/test/frontend build gate before the backend smoke test, so the known local Vite/esbuild `spawn EPERM` environment issue blocked Goal 6 before uvicorn could start.

### Changes made
- `scripts/verify_local.ps1`: scoped the compileall, pytest, and frontend build phases to non-smoke runs, so `-Smoke` performs the backend startup/listening/cleanup check without being blocked by unrelated frontend build state.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, `docs/FIX_LOG.md`: recorded Goal 6 result and the smoke-mode script fix.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1 -Smoke` | FAIL | Reproduced the issue: Python compile and pytest passed, but frontend build failed with Vite/esbuild `Error: spawn EPERM` before uvicorn smoke started. |
| `rg -n "API_KEY|OPENAI|DEEPSEEK|DeepSeek|LLM|os\.environ|getenv|load_dotenv|startup|on_event|lifespan" web_app.py server_backend.py ming_sim server tests` | PASS | Confirmed API key/LLM access is route/config/game-path driven; the TCP-only smoke path did not call those routes. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1 -Smoke` | PASS | Uvicorn started, application startup completed, port `127.0.0.1:8010` listened, and the script exited cleanly. |
| `Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8010 -ErrorAction SilentlyContinue \| Select-Object LocalAddress,LocalPort,State,OwningProcess` | PASS | No connection/listener remained after the smoke script, confirming cleanup. |
| `Get-Content -Raw $env:TEMP\ming_verify_uvicorn_stderr.log; Get-Content -Raw $env:TEMP\ming_verify_uvicorn_stdout.log` | PASS | Uvicorn log showed startup complete and listening on `http://127.0.0.1:8010`; no LLM call output. |
| `git diff -- scripts\verify_local.ps1` | PASS | Reviewed the scoped smoke-mode script diff. |
| `git status --short` | PASS | Only expected tracked files are modified; local inaccessible cache warning remains. |

### Current status
- PASS: Goal 6 backend smoke passed after fixing the `-Smoke` mode script gating bug.

### Remaining blockers
- Known local frontend build environment blocker can still recur in the full default verification gate: Vite/esbuild `spawn EPERM`.
- Known local cache permission warning remains: `git status --short` reports it cannot open `pytest-cache-files-hgfs24uv/`.

### Next recommended action
- Keep Goal 6 closed; next pass should address only the existing environment cleanup/frontend blocker if requested.

### Files changed this session
- `scripts/verify_local.ps1`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 14:58 Local

### Goal
- Goal 3: diagnose frontend `esbuild spawn EPERM` and distinguish environment failure from source failure.

### What I inspected
- npm registry/proxy/https-proxy configuration.
- Frontend tool shims under `web/node_modules/.bin`.
- Platform esbuild binary under `web/node_modules/@esbuild/win32-x64`.
- Direct `tsc`, direct `esbuild`, and Vite build behavior.

### Bugs found
- [Medium] Frontend build environment: `tsc -b` passes and `esbuild.cmd --version` passes, but `npm run build` fails when Vite asks esbuild to bundle `vite.config.ts`, raising `Error: spawn EPERM`.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded Goal 3 diagnostics and classification.
- `docs/BUG_QUEUE.md`: refined frontend build blocker as Vite/esbuild environment `ENV_BLOCKER`.
- `docs/FIX_LOG.md`: recorded that Goal 3 found no TypeScript source failure and made no source changes.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `npm config get registry` | PASS | `https://registry.npmjs.org/` |
| `npm config get proxy` | PASS | `http://127.0.0.1:7892` |
| `npm config get https-proxy` | PASS | `http://127.0.0.1:7892` |
| `Test-Path .\node_modules\.bin\tsc.cmd` | PASS | Exists. |
| `Test-Path .\node_modules\.bin\vite.cmd` | PASS | Exists. |
| `Test-Path .\node_modules\.bin\esbuild.cmd` | PASS | Exists. |
| `Test-Path .\node_modules\@esbuild\win32-x64\esbuild.exe` | PASS | Exists. |
| `.\node_modules\.bin\tsc.cmd -b` | PASS | No TypeScript errors. |
| `.\node_modules\.bin\esbuild.cmd --version` | PASS | `0.27.7` |
| `npm run build` | FAIL | Vite failed loading config because esbuild service spawn returned `EPERM`. |

### Current status
- PARTIAL: Goal 3 classified the build failure as ENV_BLOCKER, not a TypeScript source failure.

### Remaining blockers
- ENV_BLOCKER: Vite cannot spawn esbuild while loading `vite.config.ts`, even though direct esbuild execution works.

### Next recommended action
- Continue Goal 3 environment recovery: close VS Code/Codex/extra Node processes, allowlist `D:\GitHub\ming-salvage-sim` and `C:\Users\Lenovo\AppData\Local\npm-cache`, delete `web\node_modules`, run `npm cache verify`, run `npm ci --no-audit --fund=false`, then rerun `npm run build`.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 15:05 Local

### Goal
- Record Goal 3 PASS after frontend environment recovery, without changing business source.

### What I inspected
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- User-provided Goal 3 verification results

### Bugs found
- [Low] Frontend build output: Vite still reports non-blocking warnings for `/bg_ending.webp` resolution at build time and chunks larger than 500 kB after minification.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded Goal 3 PASS and the recovered verification state.
- `docs/BUG_QUEUE.md`: moved `esbuild spawn EPERM` / frontend install blocker out of open blockers and recorded remaining Vite warnings as non-blocking.
- `docs/FIX_LOG.md`: recorded the environment recovery and full verification pass.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `npm run build` | PASS | User-provided result after reinstalling dependencies from the correct `web` directory; Vite warnings remain non-blocking. |
| `scripts\verify_local.ps1` | PASS | User-provided result. |
| `python -m compileall .` | PASS | User-provided result. |
| `python -m pytest -q` | PASS | User-provided result: `49 passed`. |
| `frontend build` | PASS | User-provided result. |

### Current status
- PASS: `esbuild spawn EPERM` was an environment/directory/dependency state issue and no longer blocks follow-up source repairs.

### Remaining blockers
- None for Goal 3.
- Non-blocking Vite warnings remain:
  - `/bg_ending.webp` referenced in `/bg_ending.webp` did not resolve at build time.
  - Some chunks are larger than 500 kB after minification.

### Next recommended action
- Goal 4: recheck the Markdown bold rendering fix and prepare a checkpoint commit.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 15:17 Local

### Goal
- Goal 4: recheck the Markdown bold rendering fix in `web/src/components/modals.tsx`, then stop without changing business source.

### What I inspected
- `web/src/components/modals.tsx`
- `renderInlineBoldText()` implementation and `renderReportText()` wrapper
- AI text display call sites for month-end report, history report, detail narrative, state report, edict report, ending summary, chat messages, and secret-order text
- Search results for `dangerouslySetInnerHTML`, `renderInlineBoldText`, and `renderReportText`

### Bugs found
- [Medium] Frontend build environment: `npm run build` failed again while Vite loaded `vite.config.ts`, raising `Error: spawn EPERM` from esbuild. This recurs after Goal 3 PASS and appears environmental, not caused by the Markdown bold renderer.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded Goal 4 review and verification results.
- `docs/BUG_QUEUE.md`: reopened the recurring Vite/esbuild `spawn EPERM` blocker.
- `docs/FIX_LOG.md`: recorded the Markdown bold renderer review result and the verification blocker.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "renderInlineBoldText|dangerouslySetInnerHTML|monthEndReport|history|detail|narrative|state|edict|ending|chat|secret|order|Secret|Report|message|content|summary" web\src\components\modals.tsx` | PASS | Located renderer and relevant call sites. |
| `rg -n "dangerouslySetInnerHTML|\*\*|renderInlineBoldText" web\src` | PASS | No `dangerouslySetInnerHTML` usage found; renderer usage is confined to `modals.tsx`. |
| `node -e "...regex trace..."` | PASS | Empty input, multiple bold segments, unclosed `**`, and `<script>alert(1)</script> **安全**` sample behaved as expected at the parsing level. |
| `cd web; npm run build` | FAIL | `tsc -b` completed, then Vite failed loading config because esbuild `spawn EPERM`. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Virtualenv check passed, compileall passed, pytest passed (`49 passed, 1 warning`), frontend build failed with the same esbuild `spawn EPERM`. |

### Current status
- PARTIAL: Markdown bold renderer review passed, but Goal 4 verification is blocked by recurring Vite/esbuild `spawn EPERM`.

### Remaining blockers
- ENV_BLOCKER: Vite cannot spawn esbuild while loading `vite.config.ts`.
- Pytest still reports a non-blocking cache warning because `.pytest_cache\v\cache` cannot be created.

### Next recommended action
- Restore the frontend environment again or identify the process/security rule causing recurring esbuild `spawn EPERM`; then rerun `cd web && npm run build` and `scripts\verify_local.ps1`.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 15:26 Local

### Goal
- Final environment-restored re-verification for Goal 3/4 without changing source code.

### What I inspected
- Direct frontend toolchain execution from `web`.
- Repository verification gate `scripts\verify_local.ps1`.
- `git status --short`.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`.

### Bugs found
- [Medium] ENV_BLOCKER_INTERMITTENT frontend build: direct `esbuild.cmd --version` and `tsc.cmd -b` pass, but `npm run build` and `scripts\verify_local.ps1` still fail while Vite loads `vite.config.ts`, raising esbuild `Error: spawn EPERM`.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded the final re-verification attempt and blocker classification.
- `docs/BUG_QUEUE.md`: kept the recurring esbuild `spawn EPERM` item open as `ENV_BLOCKER_INTERMITTENT`.
- `docs/FIX_LOG.md`: recorded that this pass made no source changes and found no TypeScript source failure.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `cd web; .\node_modules\.bin\esbuild.cmd --version` | PASS | Reported `0.27.7`. |
| `cd web; .\node_modules\.bin\tsc.cmd -b` | PASS | No TypeScript errors. |
| `cd web; npm run build` | FAIL | `tsc -b` completed, then Vite failed loading config because esbuild returned `Error: spawn EPERM`. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Virtualenv check passed, compileall passed, pytest passed (`49 passed, 1 warning`), frontend build failed with the same esbuild `spawn EPERM`. |
| `git status --short` | PASS | Shows existing modified/untracked files; no source files were changed in this pass. |

### Current status
- PARTIAL: Goal 4 renderer review remains PASS, but Goal 3/4 final verification is blocked by `ENV_BLOCKER_INTERMITTENT`.

### Remaining blockers
- ENV_BLOCKER_INTERMITTENT: Vite cannot spawn esbuild while loading `vite.config.ts`, even though direct esbuild and TypeScript execution pass.
- User recovery actions: close Node/VS Code/Codex, add Defender/antivirus allowlists for the repo and npm cache, delete `web\node_modules`, run `npm cache clean --force`, then run `npm ci` from `web`.

### Next recommended action
- After local environment recovery, rerun `cd web; npm run build` and `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1`.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 15:48 Local

### Goal
- Document the user-verified full PASS state after `esbuild spawn EPERM` was cleared, without changing source code, scripts, or business logic.

### What I inspected
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- User-provided manual verification results

### Bugs found
- [Low] Frontend build warning: `/bg_ending.webp` referenced in `/bg_ending.webp` did not resolve at build time.
- [Low] Frontend build warning: some chunks are larger than 500 kB after minification.

### Changes made
- `docs/AGENT_PROGRESS.md`: recorded the latest user-verified full local verification PASS state.
- `docs/BUG_QUEUE.md`: resolved the recurring `esbuild spawn EPERM` blocker and retained the two Vite warnings as low-priority items.
- `docs/FIX_LOG.md`: recorded the durable verification outcome and Markdown bold renderer review PASS.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `cd web && npm run build` | PASS | User manually re-verified; frontend build completed. |
| `scripts\verify_local.ps1` | PASS | User manually re-verified from repository root; local verification completed. |
| `python -m compileall .` | PASS | Covered by the successful local verification result. |
| `python -m pytest -q` | PASS | User manually re-verified: `49 passed`. |
| `frontend build` | PASS | Covered by `cd web && npm run build` and `scripts\verify_local.ps1`. |

### Current status
- PASS: local verification completed. The previous esbuild `spawn EPERM` no longer blocks the project, and the Markdown bold renderer review has passed.

### Remaining blockers
- None currently blocking checkpoint.
- Low-priority warnings remain:
  - `/bg_ending.webp` referenced in `/bg_ending.webp` did not resolve at build time.
  - Some chunks are larger than 500 kB after minification.

### Next recommended action
- Create a checkpoint commit when ready.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:05 Local

### Goal
- Goal 5: confirm Python runtime/test dependency declarations and pytest discovery configuration, without changing business logic, gameplay, frontend UI, or LLM behavior.

### What I inspected
- `requirements.txt`
- `requirements-dev.txt`
- `pytest.ini`
- `web_app.py` upload route usage
- `scripts/verify_local.ps1`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

### Bugs found
- [Medium] `requirements.txt`: missing explicit `python-multipart` runtime dependency even though `web_app.py` uses `UploadFile = File(...)`.
- [Low] Repository root: `requirements-dev.txt` was missing, so test-only dependencies were not declared separately.
- [Low] `pytest.ini`: missing `python_files = test_*.py`.
- [Low] Local workspace: exact `compileall .` scans generated/dependency directories and fails on access-restricted `.pytest_cache` / `web\node_modules` paths.
- [Medium] Local frontend environment: `scripts\verify_local.ps1` still fails at Vite/esbuild frontend build with `Error: spawn EPERM`.

### Changes made
- `requirements.txt`: added `python-multipart>=0.0.20`.
- `requirements-dev.txt`: added minimal dev dependency declaration with `pytest`.
- `pytest.ini`: added `python_files = test_*.py` while keeping `testpaths = tests`.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, `docs/FIX_LOG.md`: recorded Goal 5 findings, changes, verification, and remaining environment blockers.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `git status --short` | PASS | Initial status was clean. |
| `rg -n "UploadFile|File\(|Form\(|multipart" web_app.py server_backend.py ming_sim tests` | PASS | Found `web_app.py` import and `UploadFile = File(...)` route usage. |
| `.\.venv\Scripts\python.exe -m compileall .` | FAIL | Failed on local access-restricted generated/dependency paths, including `.pytest_cache` and Python files under `web\node_modules`. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `49 passed, 1 warning`; warning is the known `.pytest_cache` access issue. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Python compile and pytest phases passed; frontend build failed with Vite/esbuild `spawn EPERM`. |
| `.\.venv\Scripts\python.exe -m pip show python-multipart` | PASS | Installed version is `0.0.32`; declaration was missing before this pass. |
| `git diff -- requirements.txt requirements-dev.txt pytest.ini` | PASS | Reviewed scoped config diff. |

### Current status
- PARTIAL: Goal 5 config consistency changes are complete and Python tests pass, but the requested full verification gate is blocked by local environment/frontend issues.

### Remaining blockers
- Exact `compileall .` fails in this workspace because it scans access-restricted generated/dependency paths; `scripts\verify_local.ps1` uses exclusions and passed its Python compile phase.
- Vite/esbuild `spawn EPERM` recurred during `scripts\verify_local.ps1` frontend build.
- Pytest still reports the non-blocking `.pytest_cache` warning.

### Next recommended action
- Resolve the local Windows permission issue affecting esbuild and generated cache paths, then rerun the three Goal 5 verification commands.

### Files changed this session
- `requirements.txt`
- `requirements-dev.txt`
- `pytest.ini`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:10 Local

### Goal
- Goal 5 convergence: run Python-only dependency and test configuration checks, without changing business source, frontend source, `web/src/components/modals.tsx`, `ming_sim/**`, `web_app.py`, DeepSeek, upload, or save logic.

### What I inspected
- `requirements.txt`
- `requirements-dev.txt`
- `pytest.ini`
- `.pytest_cache`
- `tests\__pycache__`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

### Bugs found
- [Low] Local cache permissions: filtered `compileall` cannot list `.pytest_cache` and cannot replace `.pyc` files under `tests\__pycache__`, returning WinError 5.
- [Low] Local pip cleanup: pip install commands complete successfully but cannot remove some temporary directories under `C:\Users\Lenovo\AppData\Local\Temp`.

### Changes made
- No source, frontend, gameplay, LLM, upload, or save logic changes.
- No additional dependency or pytest config changes were needed; Goal 5 declarations remain complete.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Python-only verification results and remaining local cache blocker.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` | PASS | All requirements already satisfied, including `python-multipart 0.0.32`; pip temp cleanup warnings only. |
| `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt` | PASS | `pytest 9.1.1` already satisfied; pip temp cleanup warnings only. |
| `.\.venv\Scripts\python.exe -m pip check` | PASS | `No broken requirements found.` |
| `.\.venv\Scripts\python.exe -m compileall -q -x '(\\|/)(\.venv|node_modules|web(\\|/)dist|\.pytest_cache|__pycache__|data|scripts(\\|/)runs)(\\|/)' .` | FAIL | Could not list `.pytest_cache`; WinError 5 replacing `.pyc` files in `tests\__pycache__`. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `49 passed, 1 warning`; warning is the known `.pytest_cache` write denial. |
| `Get-Item -Force .pytest_cache,tests\__pycache__` | PASS | Both cache directories exist as local directories, but compile/test cache writes are denied. |

### Current status
- PARTIAL: Python dependency and pytest configuration are complete, `pip check` passes, and tests pass. The remaining blocker is local filesystem permission denial on cache/pycache paths during compile/cache writes.

### Remaining blockers
- Local WinError 5 on `.pytest_cache` and `tests\__pycache__` prevents the requested filtered compileall command from passing.
- Pytest still passes but reports the known `.pytest_cache` warning.

### Next recommended action
- Clear or recreate `.pytest_cache` and `tests\__pycache__` with normal user permissions, then rerun the Python-only verification commands.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:33 Local

### Goal
- Goal 7B final re-verification and documentation convergence only: confirm the `/bg_ending.webp` source fix state, run the requested build and local verification gate, and avoid source changes unless a clear CSS/TS source error appears.

### What I inspected
- `web/src/styles.css`
- `scripts\verify_local.ps1`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- `git status --short`

### Bugs found
- [Medium] Local frontend environment: `npm run build` and `scripts\verify_local.ps1` both failed while Vite/esbuild loaded `vite.config.ts`, raising `Error: spawn EPERM`. This is the recurring environment blocker, not a reported CSS/TS source diagnostic.

### Changes made
- No source changes.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded the Goal 7B final re-verification result and current blocker classification.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `git status --short` | PASS | Initial status was clean. |
| `cd web; npm run build` | FAIL | `tsc -b` completed, then Vite failed to load config because esbuild child process spawn returned `EPERM`. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1` | FAIL | Python venv check, compile phase, and pytest passed; pytest reported `49 passed, 1 warning`; frontend build failed with the same Vite/esbuild `spawn EPERM`. |
| `Select-String -Path web\src\styles.css -Pattern "bg_ending|/bg_ending.webp"` | PASS | No matches. |
| `git status --short` | PASS | Run before documentation updates; no tracked/untracked changes were present at that point. |

### Current status
- PARTIAL: Goal 7B source fix is done and verified by search, but the requested full build/verification gate is blocked by recurring local Vite/esbuild `spawn EPERM`.

### Remaining blockers
- Vite/esbuild `spawn EPERM` recurred during frontend build and during the frontend phase of `scripts\verify_local.ps1`.
- Pytest still reports the non-blocking `.pytest_cache` write warning, while tests pass.

### Next recommended action
- Treat Goal 7B as `SOURCE_FIX_DONE / ENV_BLOCKER_INTERMITTENT`; rerun `npm run build` and `scripts\verify_local.ps1` after the local Windows esbuild spawn permission condition clears.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:42 Local

### Goal
- Goal 7B cleanup: remove obsolete `bg_ending`-specific ignore rules from `.gitignore` only, without changing business code or frontend source.

### What I inspected
- `.gitignore`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
- `git grep -n "bg_ending"`
- `git status --short`

### Bugs found
- [Low] `.gitignore`: obsolete `output/imagegen/bg_ending.png` and `web/public/bg_ending.webp` ignore rules would prevent a future restored ending asset from being tracked normally.

### Changes made
- `.gitignore`: removed `output/imagegen/bg_ending.png` and `web/public/bg_ending.webp`; left unrelated ending timeline ignore rules unchanged.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded the scoped cleanup.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `Select-String -Path .gitignore -Pattern "bg_ending|output/imagegen|web/public" -Context 2,2` | PASS | Confirmed both targeted `bg_ending` ignore rules were present before cleanup. |
| `git grep -n "bg_ending"` | PASS | After cleanup, no `.gitignore`, `web/src`, or `web/public` matches remained; matches were documentation history only. |
| `git status --short` | PASS | Showed `.gitignore` plus the three docs files modified. |

### Current status
- PASS for this scoped cleanup: the obsolete `.gitignore` `bg_ending` rules are removed, and source/public asset paths remain free of `bg_ending` matches.

### Remaining blockers
- The existing local Vite/esbuild `spawn EPERM` environment blocker remains tracked separately; this cleanup did not run build verification.
- Pytest cache permission warning remains tracked separately.

### Next recommended action
- Commit the Goal 7B CSS cleanup and `.gitignore` cleanup together when ready.

### Files changed this session
- `.gitignore`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 16:53 Local

### Goal
- Goal 8: audit and minimally fix DeepSeek V4 / advanced model smoke-test parameter compatibility without real API calls, real API key reads, gameplay changes, frontend UI changes, upload/save/reducer changes, or SQLite transaction changes.

### What I inspected
- `ming_sim/llm_config.py`
- `ming_sim/llm_model.py`
- `web_app.py` LLM config save and smoke-test paths
- `ming_sim/agents.py` monthly simulator/extractor agent creation paths
- `ming_sim/session.py` monthly resolve call path
- `tests/`
- DeepSeek official API documentation for V4 chat/thinking parameters

### Bugs found
- [Medium] `web_app.py`: advanced-model save validation smoked the advanced config with `enable_thinking=False`, while the real monthly simulator path uses `for_role(..., "simulator")` plus `create_chat_model(..., enable_thinking=True)`, so validation did not match the real advanced invocation shape.
- [Medium] `ming_sim/llm_config.py` / `ming_sim/llm_model.py`: DeepSeek provider thinking parameters were not capability-gated by model; all DeepSeek base URLs received `thinking: disabled` by default, while `enable_thinking=True` cleared the field entirely for V4.

### Changes made
- `ming_sim/llm_config.py`: added DeepSeek V4 model detection, DeepSeek reasoning-effort normalization, and centralized provider `extra_body` construction for DeepSeek, DashScope, and MiniMax.
- `ming_sim/llm_model.py`: routed model construction through the centralized provider parameter function; added DeepSeek V4 `reasoning_effort` handling; added `enable_thinking` to `verify_llm_available()`.
- `web_app.py`: advanced-model smoke validation now passes `enable_thinking=True`, matching the monthly simulator advanced path.
- `tests/test_llm_provider_params.py`: added mock-only tests for DeepSeek V4 thinking params, legacy DeepSeek no-V4-thinking behavior, and advanced smoke thinking-path parity.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 8 findings, fix, and verification.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "advanced|smoke|llm|model|DeepSeek|deepseek|enable_thinking|thinking|extra_body|api_key|LLM" web_app.py ming_sim tests` | PASS | Located config save, smoke, provider parameter, and monthly advanced call paths. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_llm_provider_params.py -q` | FAIL | Red step: 3 expected failures confirmed current mismatch and unsupported legacy DeepSeek `thinking` field behavior. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_llm_provider_params.py -q` | PASS | Green step: `3 passed, 1 warning`; warning is the known `.pytest_cache` permission issue. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `52 passed, 1 warning`; warning is the known `.pytest_cache` permission issue. |
| `git diff -- ming_sim\llm_config.py ming_sim\llm_model.py web_app.py tests\test_llm_provider_params.py` | PASS | Reviewed scoped source/test diff. |
| `git status --short` | PASS | Showed modified LLM files, docs, and new test file. |

### Current status
- PASS: Goal 8 compatibility fix is implemented and covered by mock-only tests. No real LLM API was called.

### Remaining blockers
- Local `.pytest_cache` write permission warning remains, but pytest passes.
- Existing frontend Vite/esbuild `spawn EPERM` environment blocker remains tracked separately and was not part of this Python-only Goal 8 pass.

### Next recommended action
- Commit Goal 8 with the mock tests and provider parameter normalization when ready.

### Files changed this session
- `ming_sim/llm_config.py`
- `ming_sim/llm_model.py`
- `web_app.py`
- `tests/test_llm_provider_params.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-24 17:02 Local

### Goal
- Goal 9A: audit avatar upload security only, without source changes, gameplay changes, DeepSeek changes, save/reducer changes, or real LLM API calls.

### What I inspected
- `web_app.py` upload/delete/custom portrait routes and upload directory constants
- `ming_sim/paths.py` user data directory resolution
- `ming_sim/db/characters.py` `characters.portrait_id` update and character insert paths
- `web/src/main.tsx` upload request construction
- `web/src/components/hud.tsx` file input constraints
- `web/src/components/drawers.tsx` portrait display/upload wiring
- `tests/` avatar upload coverage search
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, `docs/FIX_LOG.md`

### Bugs found
- [High] `web_app.py:4042`, `web_app.py:4070`, `web_app.py:4103`: custom portrait paths are derived from character `name` without filename sanitization or final path containment checks. Existing-character lookup reduces arbitrary input risk, but unsafe persisted names containing Windows path separators can still escape or collide.
- [Medium] `web_app.py:4057`, `web_app.py:4060`: upload type is selected from `UploadFile.content_type`; the backend does not validate extension, magic bytes, or decodable image headers, and reads the whole file before enforcing `MAX_PORTRAIT_BYTES`.
- [Medium] `web_app.py:4067`, `web_app.py:4070`, `web_app.py:4072`, `web_app.py:4081`, `web_app.py:4085`: upload/delete sequencing is not atomic across old-file removal, new-file write, and DB update, so failures can lose old avatars, create orphan files, or leave DB state pointing at missing files.
- [Low] `tests/`: no existing automated tests cover avatar upload security or failure modes.

### Changes made
- No source changes.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 9A audit findings and Goal 9B patch plan direction.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "UploadFile|File\(|portrait|avatar|FormData|multipart|content_type|StaticFiles|mount\(" web_app.py ming_sim tests web/src` | PASS | Located backend route, constants, frontend upload call, and display paths. |
| `rg -n "portrait|avatar|image|custom_portrait|portrait_file|avatar" ming_sim\db ming_sim tests` | PASS | Located DB `portrait_id` schema/update and confirmed no upload tests. |
| `rg -n "api_upload_portrait|/api/consorts/.*/portrait|consorts/.*/portrait|portrait upload|UploadFile|MAX_PORTRAIT_BYTES|_PORTRAIT_EXT|_find_portrait_file|custom portrait" tests web_app.py web/src ming_sim` | PASS | Confirmed route and no direct test coverage in `tests/`. |
| `rg -n "client|TestClient|portrait|UploadFile|multipart|files=" tests` | PASS | No existing upload/client tests found. |
| `git status --short` | PASS | Shows pre-existing Goal 8 source/test changes plus docs modified by this audit. |

### Current status
- RISK_FOUND: audit completed; no source code was modified this session.

### Remaining blockers
- Goal 9B should implement filename/path containment, content verification, atomic writes, DB rollback/cleanup, and upload security tests.
- Existing uncommitted Goal 8 source/test changes remain in the working tree.

### Next recommended action
- Execute Goal 9B as a scoped patch with tests for malicious names, spoofed content, SVG/non-image payloads, oversized uploads, write/DB failure rollback, and old-avatar preservation.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 08:19 Local

### Goal
- Goal 9B-Cleanup: clean avatar upload test temp directory leakage, prevent future `git status` pollution from local avatar test temp directories, and verify tests.

### What I inspected
- `tests/test_avatar_upload_security.py` temp directory fixture and upload dir monkeypatching.
- `web_app.py` portrait upload directory default and safe path helper.
- `.gitignore` local temp/cache rules.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`.

### Bugs found
- [Low] `tests/test_avatar_upload_security.py`: the avatar upload fixture created `test_avatar_upload_tmp/` directly under repo root, and older runs had also left `.test_avatar_upload/` and `.tmp_pytest/` visible to Git.
- [Low] Local environment: default Python temp paths and `tempfile.TemporaryDirectory` cleanup fail with WinError 5 in this workspace, and recursive cleanup commands are blocked by tool policy or permissions.

### Changes made
- `.gitignore`: added root-level ignore rules for `/.test_avatar_upload/`, `/.tmp_pytest/`, and `/test_avatar_upload_tmp/`.
- `tests/test_avatar_upload_security.py`: kept test isolation under ignored `test_avatar_upload_tmp/` with per-test UUID directories and best-effort cleanup, avoiding `.test_avatar_upload/` and `.tmp_pytest/`. A direct `tmp_path`/`TemporaryDirectory` switch was tested but is blocked by local WinError 5 permissions.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 9B-Cleanup verification and remaining physical cleanup blocker.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "test_avatar_upload|tmp_pytest|TemporaryDirectory|tmp_path|UPLOAD_PORTRAIT_DIR|mkdtemp|tempfile" tests\test_avatar_upload_security.py web_app.py .gitignore docs\BUG_QUEUE.md docs\FIX_LOG.md docs\AGENT_PROGRESS.md` | PASS | Found the test fixture as the current root temp source; `web_app.py` uses `user_data_path()` by default and only receives test upload dirs through monkeypatching. |
| Python `tempfile.TemporaryDirectory(...)` probe | FAIL | Default temp path under `C:\Users\Lenovo\AppData\Local\Temp` could create a dir but could not write or clean it due WinError 5. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_avatar_upload_security.py -q` | FAIL | Attempted `TemporaryDirectory`/`mkdtemp` variants failed in this local sandbox with WinError 5 during child directory creation or cleanup. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_avatar_upload_security.py -q` | PASS | `9 passed, 2 warnings`; warnings are Starlette `TestClient` deprecation and known `.pytest_cache` permission warning. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `61 passed, 2 warnings`; no real LLM API was called. |
| `git status --short` | PASS | No `.test_avatar_upload/`, `.tmp_pytest/`, or `test_avatar_upload_tmp/` entries appear after `.gitignore` protection. |

### Current status
- PASS: tests pass and the three temp directories no longer pollute `git status --short`.

### Remaining blockers
- Physical deletion of old local temp directories remains blocked by tool policy or WinError 5, so they may still exist on disk as ignored local artifacts.
- Default `tmp_path`/`TemporaryDirectory` locations are not usable in this workspace due local permissions.

### Next recommended action
- Commit Goal 9B and Goal 9B-Cleanup together after reviewing the diff; manually remove ignored temp directories later if Windows releases the handles/permissions.

### Files changed this session
- `.gitignore`
- `tests/test_avatar_upload_security.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 08:46 Local

### Goal
- Goal 10A: audit save/load restore DB replacement and session rebuild atomicity without source changes, gameplay changes, LLM calls, or commits.

### What I inspected
- `web_app.py` `WebGame.save_to()`, `WebGame.load_save()`, `WebGame._rebuild_session()`, menu load route, in-game save/load routes, save scanning, and main DB path resolution.
- `ming_sim/db/base.py` `GameDB.backup_to()`.
- `ming_sim/session.py` `GameSession.__init__()`, `begin_turn()`, `auto_save()`, and `close()`.
- `tests/` save/load coverage search.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`.

### Bugs found
- [High] `web_app.py:648`: `load_save()` closes the current session, then copies the selected save directly into the main DB path via SQLite backup, with no temporary main DB, no `os.replace`, and no rollback backup if copy or rebuild fails.
- [High] `web_app.py:668` / `web_app.py:670`: `_rebuild_session()` runs after the main DB has already been overwritten; if LLM verification, `GameSession` construction, schema/state loading, or `begin_turn()` fails, the old live session is closed and the main DB may already be replaced.
- [Medium] `web_app.py:651` / `web_app.py:661`: candidate save files are only checked for existence and are not validated with a separate connection, `PRAGMA integrity_check`, required table checks, or game-state checks before being copied into the main DB.
- [Medium] `web_app.py:625`, `web_app.py:648`, `web_app.py:2709`, `web_app.py:2740`, `web_app.py:3958`: save/load/reset/session replacement paths have no shared process lock, so concurrent load/save/reset/chat/turn operations can race on the same DB path and `web_game.session`.
- [Low] `tests/`: no existing tests cover save-load success, corrupt DB rejection, rebuild failure rollback, copy failure rollback, concurrent load, or DB/session consistency after failure.

### Changes made
- No source changes.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 10A audit findings and Goal 10B patch direction.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "load_save|save|restore|backup|autosave|_rebuild_session|rebuild|GameSession|db_path|save_path|copyfile|copy2|os\.replace|shutil\.copy|sqlite|database" web_app.py ming_sim tests` | PASS | Located save/load routes, DB paths, backup calls, and session rebuild paths. |
| `rg -n "@app\.(get|post|delete).*save|@app\.(get|post|delete).*load|@app\.(get|post|delete).*backup|@app\.(get|post|delete).*restore|save|load" web_app.py` | PASS | Located menu and in-game save/load endpoints. |
| `rg -n "Lock\(|RLock|asyncio\.Lock|threading\.Lock|load_save|save_to|backup_to|_rebuild_session|sqlite backup|os\.replace|rollback|BEGIN|transaction|PRAGMA integrity_check|quick_check" web_app.py ming_sim tests` | PASS | Found no shared save/load lock, no integrity-check validation, and no atomic replace in save-load path. |
| `rg -n "load_save|api_load_save|api_menu_load_save|save_to|api_create_save|backup_to|_rebuild_session|invalid DB|corrupt|rollback|concurrent|saves" tests` | PASS | Confirmed no save-load atomicity/failure-mode test coverage. |
| `git status --short` | PASS | Shows existing Goal 9B changes plus docs updated by this audit. |

### Current status
- RISK_FOUND: audit completed; no source code was modified and no real LLM API was called.

### Remaining blockers
- Goal 10B should implement candidate DB validation, temp DB restore, atomic main DB replacement, rollback, session rebuild ordering, and regression tests.
- Existing uncommitted Goal 9B/cleanup source, dependency, test, and docs changes remain in the working tree.

### Next recommended action
- Execute Goal 10B as a scoped patch with tests for corrupt save, copy failure, rebuild failure, successful load, and concurrent load/save behavior.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 11:38 Local

### Goal
- Goal 12D: migrate only the Goal 12C Blocking internal commits to the transaction-aware commit guard and add rollback tests.

### What I inspected
- `ming_sim/flows.py`: salary/arrears economy subpaths reachable from `apply_score_extraction()`.
- `ming_sim/db/factions.py`, `ming_sim/db/regions.py`, `ming_sim/db/characters.py`, `ming_sim/db/secret_orders.py`, and `ming_sim/db/buildings.py`: Blocking helper commit points identified in Goal 12C.
- `ming_sim/issues.py`: character location and duplicate-office helper commits.
- Existing transaction tests in `tests/test_db_transaction_helper.py` and `tests/test_settlement_transaction_rollback.py`.

### Bugs found
- [High] Blocking direct commits in settlement-reachable helper paths still committed inside `db.transaction()`, so rollback after a later reducer failure could not restore faction/class/economy/character/secret-order/building effects.

### Changes made
- `ming_sim/flows.py`: changed salary/arrears economy subpath commits from `db.conn.commit()` to `db.commit()`.
- `ming_sim/issues.py`: changed `_apply_character_location()` and `_displace_duplicate_offices()` commits from `db.conn.commit()` to `db.commit()`.
- `ming_sim/db/factions.py`: changed `adjust_factions()` to `self.commit()`.
- `ming_sim/db/regions.py`: changed `adjust_classes()` to `self.commit()`.
- `ming_sim/db/characters.py`: changed `set_character_status()`, `apply_character_power_changes()`, `set_character_office()`, and `add_character()` to `self.commit()`.
- `ming_sim/db/secret_orders.py`: changed `close_secret_order()` and `_append_secret_order_line()` to `self.commit()`.
- `ming_sim/db/buildings.py`: changed `add_building()`, `remove_building()`, `apply_building_deltas()`, `add_technology()`, and `add_department()` to `self.commit()`.
- `tests/test_settlement_transaction_blocking_commits.py`: added rollback coverage for all Goal 12C Blocking categories plus representative outside-transaction commit compatibility.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 12D status and remaining Follow-up/Safe boundaries.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_blocking_commits.py -q` | FAIL | RED run: 5 rollback tests failed against the old direct commits; representative outside-transaction test passed. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_blocking_commits.py -q` | PASS | GREEN run: `6 passed, 1 warning`; warning is local `.pytest_cache` WinError 5. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `94 passed, 2 warnings`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |
| `rg -n "\bself\.conn\.commit\(\)|\bdb\.conn\.commit\(\)" ming_sim\flows.py ming_sim\db\factions.py ming_sim\db\regions.py ming_sim\db\characters.py ming_sim\issues.py ming_sim\db\secret_orders.py ming_sim\db\buildings.py` | PASS | Confirmed remaining direct commits in these files are Follow-up/Safe for the current extraction transaction boundary. |

### Current status
- PASS: all Goal 12C Blocking commit points were migrated and targeted/full pytest passed.

### Remaining blockers
- Follow-up/Safe direct commits remain outside the current `_settle_after_narrative()` extraction transaction boundary: fixed monthly flows, inertia/ongoing effects, chat/memory/admin/schema/seed/save-state/directive paths, and arms dispatch.
- A future hardening pass may decide whether to expand transaction coverage beyond extraction settlement.

### Next recommended action
- Proceed to Goal 13 or a new explicit follow-up for broader turn-level transaction hardening.

### Files changed this session
- `ming_sim/flows.py`
- `ming_sim/issues.py`
- `ming_sim/db/factions.py`
- `ming_sim/db/regions.py`
- `ming_sim/db/characters.py`
- `ming_sim/db/secret_orders.py`
- `ming_sim/db/buildings.py`
- `tests/test_settlement_transaction_blocking_commits.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 11:24 Local

### Goal
- Goal 12C: audit remaining direct DB commits after Goal 12B-3 and decide whether any still break `_settle_after_narrative()` transaction rollback.

### What I inspected
- `ming_sim/decree.py`: confirmed the current transaction scope wraps only successful extractor output application, `apply_score_extraction()`, `save_turn_report()`, and `save_turn_extraction()`.
- `ming_sim/issues.py`: traced `apply_score_extraction()` through issue, character, secret-order, and issue-effect helper paths.
- `ming_sim/flows.py`: checked economy salary/arrears paths plus fixed annual/monthly flows.
- `ming_sim/db/**`: searched all remaining `self.conn.commit()` points and compared them to the current settlement transaction call graph.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: updated audit status and follow-up scope.

### Bugs found
- [High] `ming_sim/flows.py:337` and `ming_sim/flows.py:420`: salary/arrears economy subpaths can run inside `apply_score_extraction()` and still call `db.conn.commit()` directly.
- [High] `ming_sim/db/factions.py:76` and `ming_sim/db/regions.py:456`: faction/class adjustment helpers can run inside the extraction transaction and still call `self.conn.commit()` directly.
- [High] `ming_sim/db/characters.py:105`, `ming_sim/db/characters.py:150`, `ming_sim/db/characters.py:194`, `ming_sim/db/characters.py:463`, `ming_sim/issues.py:272`, and `ming_sim/issues.py:1603`: character status/power/office/new-character/location/duplicate-office paths can run inside the extraction transaction and still commit directly.
- [High] `ming_sim/db/secret_orders.py:194` and `ming_sim/db/secret_orders.py:277`: secret-order close/sim-note paths can run inside `apply_score_extraction()` and still commit directly.
- [High] `ming_sim/db/buildings.py:112`, `ming_sim/db/buildings.py:130`, `ming_sim/db/buildings.py:221`, `ming_sim/db/buildings.py:325`, and `ming_sim/db/buildings.py:372`: issue-effect building/technology/department paths can run inside issue resolution during `apply_score_extraction()` and still commit directly.

### Changes made
- No source code changes.
- No tests added or modified.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 12C RISK_FOUND and the required Goal 12D scope.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "\bself\.conn\.commit\(\)|\bdb\.conn\.commit\(\)" ming_sim\issues.py ming_sim\decree.py ming_sim\flows.py ming_sim\db` | PASS | Listed remaining direct commit calls in the audited scope. |
| `rg -n "def _settle_after_narrative|apply_score_extraction|save_turn_report|save_turn_extraction|with db\.transaction" ming_sim\decree.py ming_sim\issues.py` | PASS | Confirmed the current extraction transaction boundary. |
| `rg -n "^\\s*def |\\bself\\.conn\\.commit\\(\\)|\\bdb\\.conn\\.commit\\(\\)" ming_sim\\issues.py ming_sim\\flows.py ming_sim\\db` | PASS | Mapped direct commit lines to owning functions. |
| `git status --short` | PASS | Captured existing working tree state before docs updates. |

### Current status
- RISK_FOUND: Goal 12C audit completed. Blocking direct commits remain reachable inside the current extraction transaction, so Goal 12 core transaction closure is not complete.

### Remaining blockers
- Goal 12D should migrate only the Blocking direct commit points reachable inside `apply_score_extraction()` to transaction-aware `self.commit()` / `db.commit()` and add rollback tests for the optional extractor paths.
- Follow-up commits outside the current narrow transaction boundary remain for later hardening but do not block Goal 12D's targeted closure.

### Next recommended action
- Run Goal 12D before moving to Goal 13.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 11:16 Local

### Goal
- Goal 12B-3: migrate only `apply_score_extraction()` settlement-chain DB helper internal commits in the allowed files to the transaction-aware `self.commit()` guard, add rollback tests, and avoid real LLM calls.

### What I inspected
- `ming_sim/issues.py::apply_score_extraction()`: direct DB helper calls for new armies, region/army/power deltas, fiscal changes, issue tracker output, character/power/status/secret-order paths, and turn settlement return structure.
- `ming_sim/db/regions.py`, `ming_sim/db/armies.py`, `ming_sim/db/powers.py`, `ming_sim/db/fiscal.py`, `ming_sim/db/issues.py`, and `ming_sim/db/turns.py`: direct `self.conn.commit()` sites reachable from the allowed settlement chain.
- `ming_sim/flows.py`: confirmed some economy/faction/class paths are reachable but outside this turn's allowed modification list.

### Bugs found
- [High] Allowed DB helper chain: `apply_region_deltas()`, `apply_army_deltas()`, `create_armies_from_extraction()`, `apply_power_deltas()`, fiscal helpers, issue tracker/economy/legacy helpers, and turn report/extraction writes could call `self.conn.commit()` inside `db.transaction()`, preventing rollback after later reducer failures.
- [Medium] Out-of-scope optional paths: selected `ming_sim/flows.py`, arms, character, faction/class, and secret-order helpers can still contain direct commits and need a separate scoped pass before every optional extractor field can be claimed fully rollback-safe.

### Changes made
- `ming_sim/db/regions.py`: changed `apply_region_deltas()` to use `self.commit()`.
- `ming_sim/db/armies.py`: changed `apply_army_deltas()` and `create_armies_from_extraction()` to use `self.commit()`.
- `ming_sim/db/powers.py`: changed `apply_power_deltas()` to use `self.commit()`.
- `ming_sim/db/fiscal.py`: changed settlement-chain fiscal config/create/remove/dynamic tax helpers to use `self.commit()`.
- `ming_sim/db/issues.py`: changed settlement-chain issue tracker, economy move, legacy, and event-trigger helpers to use `self.commit()`.
- `ming_sim/db/turns.py`: changed `save_turn_report()` and `save_turn_extraction()` to use `self.commit()`.
- `tests/test_settlement_transaction_rollback.py`: added real SQLite rollback tests covering new-army-before-region-fail, region-before-army-fail, report/extraction trace failure, and outside-transaction compatibility.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 12B-3 completion and residual out-of-scope paths.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "def apply_score_extraction|db\.|apply_.*delta|save_turn_report|save_turn_extraction|save_state|update_|create_|insert_|expire_|commit\(" ...` | PASS | Located reducer DB helper chain and direct commit sites. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_rollback.py -q` | FAIL | Initial TDD run failed: new army, region delta, and turn report/extraction rows survived rollback due to direct commits. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_rollback.py -q` | PASS | `4 passed`; local `.pytest_cache` WinError 5 warning remains environmental. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `88 passed`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |

### Current status
- PASS: allowed settlement-chain DB helper commits now use the transaction-aware guard, and rollback tests plus full Python tests pass.

### Remaining blockers
- Optional extractor paths outside this pass's allowed files still need a focused audit/migration before claiming every possible extractor field is rollback-safe.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Audit and, if approved, migrate the remaining optional extractor paths outside this pass's allowed file list: `ming_sim/flows.py` economy subpaths, faction/class helpers, arms, character, and secret-order writes.

### Files changed this session
- `ming_sim/db/regions.py`
- `ming_sim/db/armies.py`
- `ming_sim/db/powers.py`
- `ming_sim/db/fiscal.py`
- `ming_sim/db/issues.py`
- `ming_sim/db/turns.py`
- `tests/test_settlement_transaction_rollback.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:56 Local

### Goal
- Goal 12B-2: wrap only the post-extractor `apply_score_extraction()` settlement write segment in `ming_sim/decree.py::_settle_after_narrative()` with `db.transaction()`, without wrapping LLM/extractor calls or unrelated monthly phases.

### What I inspected
- `ming_sim/decree.py::_settle_after_narrative()`: extractor setup/call, extractor exception fallback, `apply_score_extraction()`, turn report/extraction persistence, chapter/minister memory writes, issue inertia/ongoing effects, directive marking, `next_period()`, and `save_state()`.
- Existing tests for reducer boundaries, DB transaction helper behavior, and current git status.

### Bugs found
- [High] `ming_sim/decree.py`: `apply_score_extraction()` and immediate turn report/extraction writes were not inside a managed transaction boundary after Goal 12B-1.
- [Medium] `ming_sim/decree.py`: extractor failure fallback still called the reducer with an empty extraction payload; for this boundary pass, extractor failure should not enter the transaction or reducer path.

### Changes made
- `ming_sim/decree.py`: added `extraction_failed` tracking.
- `ming_sim/decree.py`: wrapped only successful extractor result application (`apply_score_extraction()`, `save_turn_report()`, and `save_turn_extraction()`) in `db.transaction()`.
- `ming_sim/decree.py`: left extractor/LLM calls, chapter memory, minister recap, issue inertia/ongoing, ending checks, directive marking, `next_period()`, and `save_state()` outside this transaction.
- `tests/test_settlement_transaction_boundary.py`: added mock-only tests for reducer failure rollback, success commit, and extractor failure bypassing the transaction/reducer.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 12B-2 completion and the remaining 12B-3 helper-internal commit risk.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "def _settle_after_narrative|apply_score_extraction|extract|save_turn_extraction|next_period|save_state|resolve_turn|resolve_directives|submit_decisions" ming_sim\decree.py tests` | PASS | Located the transaction insertion point and related tests. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_boundary.py -q` | FAIL | Initial TDD run failed because `apply_score_extraction()` was outside transaction and extractor failure still called the reducer. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_settlement_transaction_boundary.py -q` | PASS | `3 passed`; local `.pytest_cache` WinError 5 warning remains environmental. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `84 passed`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |

### Current status
- PASS: the post-extractor settlement write segment is now inside `db.transaction()` and targeted plus full Python tests pass.

### Remaining blockers
- Many reducer DB helpers still call `self.conn.commit()` directly, so full rollback semantics require Goal 12B-3 migration to `self.commit()` or equivalent transaction-aware behavior.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Execute Goal 12B-3 by migrating only the DB helpers used by `apply_score_extraction()` from direct `self.conn.commit()` to transaction-aware `self.commit()`, then add rollback state tests.

### Files changed this session
- `ming_sim/decree.py`
- `tests/test_settlement_transaction_boundary.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:38 Local

### Goal
- Goal 12B-1: add a reusable DB transaction/savepoint helper and commit guard, plus focused tests and docs, without wiring monthly settlement or calling real LLM APIs.

### What I inspected
- `ming_sim/db/base.py`: `GameDB` base connection initialization, `backup_to()`, and absence of a managed transaction API.
- `ming_sim/db/__init__.py`: `GameDB` mixin composition and `_BaseMixin` placement.
- `ming_sim/db/**`: existing direct `self.conn.commit()` usage to confirm this pass should only provide the migration entry point.
- Existing test patterns and current git status.

### Bugs found
- [High] `ming_sim/db/base.py`: no shared transaction/savepoint context manager existed for Goal 12B-2 to wrap structured extraction settlement safely.
- [High] `ming_sim/db/**`: helper methods still call `self.conn.commit()` directly; this remains a migration risk for Goal 12B-3, but this session added the transaction-aware `db.commit()` entry point.

### Changes made
- `ming_sim/db/base.py`: added `GameDB.transaction()` with outer `BEGIN`/`COMMIT`/`ROLLBACK`, nested `SAVEPOINT`/`RELEASE`/`ROLLBACK TO`, exception propagation, and no connection closing.
- `ming_sim/db/base.py`: added `GameDB.commit()` guard that commits normally outside managed transactions and defers real commit while inside one.
- `tests/test_db_transaction_helper.py`: added focused tests for success commit, rollback, nested savepoint success, nested savepoint rollback with outer continuation, guarded in-transaction commit, and outside-transaction commit.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 12B-1 completion and remaining 12B-2/12B-3 integration work.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "self\.conn\.commit\(|def commit\(|transaction\(" ming_sim\db tests` | PASS | Confirmed there was no existing transaction helper and many direct helper commits remain. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_db_transaction_helper.py -q` | FAIL | Initial TDD run failed with missing `GameDB.transaction()` / `GameDB.commit()`, as expected. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_db_transaction_helper.py -q` | PASS | `6 passed`; local `.pytest_cache` WinError 5 warning remains environmental. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `81 passed`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |

### Current status
- PASS: DB transaction/savepoint helper and commit guard exist, and targeted plus full Python tests pass.

### Remaining blockers
- `_settle_after_narrative()` / `apply_score_extraction()` are not yet wrapped in this transaction helper; that is Goal 12B-2.
- Reducer-related DB helpers still need selective migration from `self.conn.commit()` to `self.commit()` or another transaction-aware pattern; that is Goal 12B-3.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Execute Goal 12B-2 by wrapping only the extractor -> `apply_score_extraction()` DB write segment in `ming_sim/decree.py::_settle_after_narrative()` with `db.transaction()`, then add rollback tests.

### Files changed this session
- `ming_sim/db/base.py`
- `tests/test_db_transaction_helper.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:28 Local

### Goal
- Goal 12A-2: supplement the SQLite/monthly settlement transaction audit with `ming_sim/decree.py` and the GameSession orchestration chain, without source or test changes.

### What I inspected
- `web_app.py`: decree issue, streaming issue, and decision-resolution API routes.
- `ming_sim/session.py`: `GameSession.resolve_turn()`, `submit_decisions()`, `begin_turn()`, and `auto_save()` wrappers needed to complete the GameSession chain requested by the task.
- `ming_sim/decree.py`: `resolve_directives()`, `_settle_after_narrative()`, `resolve_decisions_phase2()`, and the normal/fallback settlement branches.
- `ming_sim/simulation.py`: extractor entry and validation call point.
- `ming_sim/issues.py`: `apply_score_extraction()` and `apply_issue_inertia_and_ongoing()`.
- `ming_sim/db/**`, `ming_sim/flows.py`, `ming_sim/memories.py`, and relevant web chat/favorites paths for commit/write phase classification.

### Bugs found
- [High] `ming_sim/decree.py`: normal settlement concentrates extraction, reducer, report/extraction persistence, memories, issue inertia, ending, directive marking, turn advance, and state save in `_settle_after_narrative()` without a transaction/savepoint boundary.
- [High] `ming_sim/session.py`: `resolve_turn()` performs `auto_save("preresolve")` before settlement and `resolve_directives()` applies fixed monthly flows before simulator/extractor. Wrapping the whole `resolve_turn()` call would include intentionally persistent rollback points and is too broad for the first patch.
- [High] `ming_sim/flows.py` / `ming_sim/db/**`: fixed monthly flows and reducer helpers directly commit; helper-internal commits must be made transaction-aware before rollback tests can pass.
- [Medium] `web_app.py` chat/favorites/history writes are independent UI/session state writes with their own commits and should not be included in the first monthly extraction transaction patch.

### Changes made
- No source code changes.
- No tests added or modified.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: documented the full orchestration chain and Goal 12B patch boundary.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "class GameSession|def resolve_turn|def submit_decisions|def _.*phase|extract_scores_by_modules_with_agno|apply_score_extraction|apply_issue_inertia_and_ongoing|save_turn_extraction|save_state|next_period|mark_directives_issued|record_turn_report|chat|history|favorite|resolve_decision|Decision|awaiting|commit\\(|rollback\\(|transaction|SAVEPOINT|BEGIN" ming_sim\decree.py` | PASS | Located decree orchestration and settlement write phases. |
| `rg -n "api_issue_decree|api_issue_decree_stream|api_resolve_decisions_stream|resolve_turn|submit_decisions|refresh_turn" web_app.py` | PASS | Located web entry points to GameSession monthly settlement. |
| `rg -n "class GameSession|def resolve_turn|def submit_decisions|def begin_turn|def auto_save" ming_sim\session.py web_app.py` | PASS | Completed the GameSession wrapper chain requested by this audit. |
| `rg -n "def _apply_metric_dict|def _apply_economy_list|def _apply_faction_dict|def _apply_class_dict|record_issue_economy_move|db\\.conn\\.commit\\(|db\\.record_economy_moves" ming_sim\flows.py ming_sim\issues.py` | PASS | Confirmed metrics are in-memory only while economy/faction/class can commit through helpers. |
| `git status --short` | PASS | Only docs are modified in this audit turn. |

### Current status
- RISK_FOUND: orchestration audit completed; no source code or tests were modified and no real LLM API was called.

### Remaining blockers
- Goal 12B must handle helper-internal commits before any rollback guarantee can be claimed.
- Phase2 decision path is marked deprecated but still callable through `GameSession.submit_decisions()`; it must either share the transaction wrapper or be explicitly retired later.

### Next recommended action
- Start Goal 12B-1 with a transaction/savepoint helper in the DB layer, then Goal 12B-2 should wrap only the `_settle_after_narrative()` extraction/reducer block around `apply_score_extraction()` first.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:12 Local

### Goal
- Goal 11B-2: make critical LLM reducer failures fail fast after validated payload entry, without DB transaction changes or real LLM API calls.

### What I inspected
- `ming_sim/issues.py`: `apply_score_extraction()` reducer order and all broad `except Exception` branches around reducer handoff.
- `ming_sim/issues.py`: remaining broad exceptions outside the core monthly extraction handoff, including issue-effect best-effort helpers, per-item character/office rejection paths, and secret-order per-item rejection paths.
- `tests/`: existing reducer and structured-payload tests, then new mock-only reducer fail-fast tests.

### Bugs found
- [High] `ming_sim/issues.py`: module-level critical reducer failures in `new_armies`, `region_delta`, `army_delta`, `arms_changes`, `power_updates`, and `character_power_changes` were printed as warnings and swallowed, allowing later reducer steps to continue after a failed state/DB mutation.
- [High] `ming_sim/issues.py` / `ming_sim/db/**`: full rollback is still not guaranteed because earlier reducer steps may have already committed before a later fail-fast exception; this remains Goal 12.

### Changes made
- `ming_sim/issues.py`: added `_raise_reducer_failure()` and changed module-level critical reducer exception handling to raise `RuntimeError("<module> reducer failed: ...")` with exception chaining.
- `ming_sim/issues.py`: left per-item rejection/reporting branches for character status, office changes, secret orders, issue effects, and issue creation unchanged to avoid broad behavior changes outside this goal.
- `tests/test_llm_reducer_fail_fast.py`: added mock-only tests proving `region_delta` and `army_delta` failures raise and stop later reducer calls, plus a valid minimal payload still returning a summary.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded the fail-fast scope and Goal 12 rollback residual risk.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "def apply_score_extraction|except Exception|apply_region_deltas|apply_army_deltas|create_armies_from_extraction|apply_arms_stock_deltas|apply_power_deltas|apply_issue_tracker_output" ming_sim\issues.py` | PASS | Located broad reducer exception branches. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_llm_reducer_fail_fast.py -q` | FAIL | RED confirmed current code swallowed region/army reducer exceptions and did not raise. Earlier setup failures were fixed by adding minimal fake DB/mocked issue/victory dependencies. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_llm_reducer_fail_fast.py -q` | PASS | `3 passed`; `.pytest_cache` WinError 5 warning only. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `75 passed`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |

### Current status
- PASS: module-level critical reducer failures now fail fast and stop later reducer processing.

### Remaining blockers
- DB rollback/transaction semantics remain unresolved for Goal 12; fail-fast prevents silent continuation but does not undo earlier commits.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Execute Goal 12 to add a transaction/staging boundary around monthly structured extraction settlement so fail-fast exceptions can roll back already-applied state.

### Files changed this session
- `ming_sim/issues.py`
- `tests/test_llm_reducer_fail_fast.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:21 Local

### Goal
- Goal 12A: audit SQLite/monthly settlement transaction boundaries and document rollback risks without source or test changes.

### What I inspected
- `web_app.py`: decree issue and streaming routes that call `game.session.resolve_turn()` / `submit_decisions()`.
- `ming_sim/simulation.py`: extractor parse/validation/sanitizer path, confirming it prepares payloads but does not write DB.
- `ming_sim/issues.py`: `apply_score_extraction()`, 11B-2 fail-fast helper, `apply_issue_inertia_and_ongoing()`, direct `db.conn.commit()` calls, and reducer ordering.
- `ming_sim/db/**`: SQLite connection setup and commit points in state, turn, region, army, arms, power, fiscal/economy, issue, character, secret-order, building, memory, and chat helpers.
- `tests/**`: rollback/transaction/fail-fast coverage.

### Bugs found
- [High] `ming_sim/issues.py` / `ming_sim/db/**`: no visible outer transaction/savepoint encloses monthly structured settlement. 11B-2 fail-fast stops later reducers but cannot roll back earlier committed steps.
- [High] `ming_sim/db/**`: many reducer helpers call `self.conn.commit()` internally, so a naive outer transaction around `apply_score_extraction()` would be broken unless those helpers defer commits or become transaction-aware.
- [Medium] `ming_sim/issues.py`: `apply_issue_inertia_and_ongoing()` and issue-effect helpers also write/commit month-end effects outside an audited rollback boundary.
- [Medium] Audit scope: the full `GameSession`/decree orchestration appears to pass through `ming_sim/decree.py` by search result, but that file was not in this turn's explicit allowed-read list, so the exact internal phase ordering still needs confirmation before patching.

### Changes made
- No source code changes.
- No tests added or modified.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded transaction-boundary risks and Goal 12B patch plan.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "apply_score_extraction\\(|extract_scores_by_modules_with_agno\\(|simulate_month|run_month|month_end|monthly|next_month|advance_month|settlement|结算|推演" web_app.py ming_sim\simulation.py ming_sim\issues.py tests` | PASS | Located allowed-file entry points and reducer/extractor functions. |
| `rg -n "\\.commit\\(|commit\\(|rollback\\(|BEGIN|SAVEPOINT|RELEASE|in_transaction|isolation_level|transaction|atomic|savepoint" ming_sim\db ming_sim\issues.py ming_sim\simulation.py web_app.py tests` | PASS | Found many helper-local commits and no existing savepoint/transaction boundary in audited files. |
| `rg -n "def apply_region_deltas|def apply_army_deltas|def create_armies_from_extraction|def apply_arms_stock_deltas|def apply_power_deltas|def add_ledger|def add_metric|def update_state|def save_state|def advance_issue|def insert_issue|def spend_issue_budget|def remove_fiscal_item|def create_fiscal_item|def set_fiscal" ming_sim\db ming_sim\issues.py` | PASS | Mapped major mutation helper functions to commit points. |
| `rg -n "rollback|transaction|savepoint|in_transaction|BEGIN|fail fast|fail-fast|half|partial|region_delta.*fail|army_delta.*fail|metrics.*fail|DB state|state unchanged|rollback semantics|apply_score_extraction" tests` | PASS | Found fail-fast tests but no DB rollback/state-unchanged tests for reducer failures. |
| `git status --short` | PASS | Existing working tree includes prior Goal 11 changes plus docs updated by this audit. |

### Current status
- RISK_FOUND: audit completed; no source code or tests were modified and no real LLM API was called.

### Remaining blockers
- Need to confirm the `ming_sim/decree.py` / `GameSession` internal monthly phase ordering before implementing Goal 12B, because it was outside this turn's explicit allowed-read list.
- Need an outer transaction/savepoint design that accounts for helper-internal commits.

### Next recommended action
- Execute Goal 12B in small steps: add a transaction/savepoint helper, make monthly settlement use it, make helpers defer commits when inside settlement, and add rollback tests for failures after metrics/economy/region writes.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 10:03 Local

### Goal
- Goal 11B-1: add a strict LLM structured payload validation boundary after extractor parse/sanitizer repair and before reducer entry, with mock-only tests and no DB transaction changes.

### What I inspected
- `ming_sim/simulation.py`: extractor module allowlists, top-level/item canonicalization, sanitizer fallback path, `_sanitize_module_output()`, and `extract_scores_by_modules_with_agno()` reducer handoff.
- `ming_sim/constants.py`: region, army, fiscal, and power field alias/allowed-field constants used by existing reducers.
- `ming_sim/issues.py`: reducer broad-exception and partial-apply risks from Goal 11A, kept out of this boundary-only patch.
- `tests/`: existing patterns and the new mock-only structured payload validation tests.

### Bugs found
- [Medium] `ming_sim/simulation.py`: parsed extractor payloads and sanitizer-repaired payloads could reach permissive module sanitization without fail-closed checks for unknown top-level fields or selected unknown nested fields.
- [Medium] `ming_sim/simulation.py`: partial valid/invalid payloads were not rejected as a whole before reducer entry.
- [High] `ming_sim/issues.py` / `ming_sim/db/**`: reducer partial-apply and transaction/rollback gaps remain out of scope for Goal 11B-1 and are tracked for Goal 11B-2/Goal 12.

### Changes made
- `ming_sim/simulation.py`: added `validate_structured_extraction_payload()` plus small type/nested-field helpers. The validator canonicalizes aliases, enforces module top-level allowlists, rejects invalid top-level value types, and rejects unknown nested fields in `region_delta`, `army_delta`, and `power_updates`.
- `ming_sim/simulation.py`: calls the validator immediately after `parse_agent_json()`, including the sanitizer repair path, and before `_sanitize_module_output()` can copy fields into reducer input.
- `tests/test_llm_structured_payload_validation.py`: added mock-only tests for unknown top-level fields, unknown nested region/army/power fields, partial valid/invalid rejection, valid minimal payload acceptance, and sanitizer-path illegal structures.
- `docs/BUG_QUEUE.md` and `docs/FIX_LOG.md`: recorded the fixed validation boundary and remaining transaction/rollback follow-up.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "def _canonicalize_extraction|def _sanitize_module_output|MODULE_FIELDS|EMPTY_EXTRACTION|parse_agent_json|apply_score_extraction|REGION_SCORE_FIELDS|ARMY_SCORE_FIELDS|POWER_FIELD_ALIASES" ming_sim/simulation.py ming_sim/constants.py ming_sim/issues.py tests/test_llm_structured_payload_validation.py` | PASS | Located the narrow extractor-parse to reducer boundary and field allowlists. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_llm_structured_payload_validation.py -q` | PASS | `6 passed`; `.pytest_cache` WinError 5 warning only. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `72 passed`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |
| `git diff -- ming_sim\simulation.py tests\test_llm_structured_payload_validation.py --check` | PASS | No whitespace errors; Git warned LF will be replaced by CRLF when touched. |

### Current status
- PASS: illegal structured payloads are blocked before reducer entry, and both targeted and full Python tests pass.

### Remaining blockers
- Reducer exception propagation, staging, and transaction/rollback semantics are still open for Goal 11B-2/Goal 12.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Execute Goal 11B-2/Goal 12 to remove broad reducer swallowing and add an outer transaction/rollback boundary for monthly structured extraction settlement.

### Files changed this session
- `ming_sim/simulation.py`
- `tests/test_llm_structured_payload_validation.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 09:38 Local

### Goal
- Goal 10B: implement the minimal save-load atomicity fix with candidate validation, live DB rollback, shared save/load/reset locking, regression tests, and docs.

### What I inspected
- `web_app.py` `WebGame.save_to()`, `delete_save()`, `reset_game()`, `load_save()`, `_rebuild_session()`, imports, and DB path handling.
- `ming_sim/db/base.py` `GameDB.backup_to()` behavior for live DB backups.
- `ming_sim/db/schema.py` and `ming_sim/db/state.py` for minimal save DB validation fields.
- Existing `tests/` patterns and local temp/SQLite behavior.

### Bugs found
- [High] `web_app.py`: existing `load_save()` closed the current session and wrote a selected save directly into the live DB before rebuild, with no rollback if candidate copy or `_rebuild_session()` failed.
- [Medium] `web_app.py`: save DB candidates were not checked with `PRAGMA integrity_check`, required tables, or a main `game_state` row before replacing the live DB.
- [Medium] `web_app.py`: save/load/delete-save/reset were not serialized with a shared process lock.
- [Low] Local environment: repo-local SQLite probe files hit `sqlite3.OperationalError: disk I/O error`, and recursive cleanup of ignored probe directories was blocked by tool policy; pytest itself passes using system temp paths.

### Changes made
- `web_app.py`: added `sqlite3` import, `WebGame._state_lock`, `_state_lock_guard()`, candidate/rollback temp DB helpers, candidate validation, rollback restore, and best-effort SQLite sidecar/temp cleanup.
- `web_app.py`: changed `load_save()` to backup the selected save into a candidate DB, validate it, backup the current live DB to rollback, replace the live DB with `os.replace()`, and roll back if replacement or session rebuild fails.
- `web_app.py`: wrapped `save_to()`, `delete_save()`, `reset_game()`, and `load_save()` with the shared state lock.
- `tests/test_save_load_atomicity.py`: added regression coverage for corrupt saves, validation failure without session close, rebuild-failure rollback, successful load/rebuild, and lock entry.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 10B fix, tests, and residual broader concurrency follow-up.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "def db|def load_save|def save_to|def delete_save|def reset_game|def _rebuild_session|class WebGame|game_state|kv_store" web_app.py ming_sim/db tests` | PASS | Located save/load implementation and schema/state validation targets. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_save_load_atomicity.py -q` | FAIL | Initial TDD run failed against old behavior; after patch, rerun passed. |
| `.\.venv\Scripts\python.exe -m pytest tests\test_save_load_atomicity.py -q` | PASS | `5 passed, 1 warning`; warning is local `.pytest_cache` WinError 5. |
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | `66 passed, 2 warnings`; warnings are Starlette TestClient deprecation and local `.pytest_cache` WinError 5. |
| `git diff -- web_app.py` | PASS | Reviewed the save/load/reset scoped diff after restoring an encoding-safe copy of `web_app.py`. |

### Current status
- PASS: Goal 10B save-load atomicity fix is implemented and the full Python test suite passes.

### Remaining blockers
- Broader turn/chat mutation paths are not yet comprehensively serialized under the new state lock; tracked as a follow-up outside Goal 10B scope.
- Local `.pytest_cache` creation still reports WinError 5, but tests pass and this is an environment/cache permission issue.

### Next recommended action
- Review the Goal 10B diff and commit with the existing Goal 9B work when ready.

### Files changed this session
- `web_app.py`
- `tests/test_save_load_atomicity.py`
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`

## Session 2026-06-25 09:48 Local

### Goal
- Goal 11A: audit the LLM structured-output to deterministic reducer boundary without source changes, tests, commits, gameplay changes, or real LLM API calls.

### What I inspected
- `ming_sim/agents.py`: non-stream agent output capture, `parse_agent_json()`, JSON fence/snippet repair, and sanitizer agent entry points.
- `ming_sim/simulation.py`: extractor module allowed fields, top-level aliasing, canonicalization, module output sanitization, sanitizer fallback, output merge, and trace serialization.
- `ming_sim/issues.py`: `apply_score_extraction()` reducer order, broad exception handling, issue/personnel/secret-order mutation paths, and final summary return.
- `ming_sim/db/regions.py`, `ming_sim/db/armies.py`, `ming_sim/db/arms.py`, `ming_sim/db/powers.py`, `ming_sim/db/turns.py`, and other `ming_sim/db/**` commit/rollback searches.
- `web_app.py`: structured directive request shape, structured directive API routes, and month-end/decision SSE worker exception handling.
- `tests/**`: existing reducer, structured directive, field coverage, and save-load tests.

### Bugs found
- [High] `ming_sim/issues.py:1607`: `apply_score_extraction()` applies early state/DB changes before later reducers, then catches several later reducer failures with broad `except Exception` and continues, allowing partial monthly settlement.
- [High] `ming_sim/db/**`: reducer helpers commit independently (`regions.py:312`, `armies.py:503/738`, `powers.py:153`, `turns.py:197/351`, etc.) and the audited files do not show a surrounding transaction/rollback boundary for a complete monthly extraction settlement.
- [Medium] `ming_sim/simulation.py:819`: module sanitization enforces a top-level per-module allowlist by copying allowed keys and dropping unknown keys, but it does not fail closed on unknown top-level fields.
- [Medium] `ming_sim/db/regions.py:191` and `ming_sim/db/armies.py:356`: nested-field behavior is inconsistent; invalid `region_delta` fields raise, while invalid `army_delta` and `power_updates` fields are logged/skipped, so partial valid/invalid payload semantics differ by reducer.
- [Medium] `ming_sim/simulation.py:1256`: malformed extractor JSON can be repaired by a sanitizer LLM and accepted if it parses, but the accepted object is still validated only by the permissive sanitizer/reducer path rather than a strict schema.
- [Low] `tests/`: no clear regression coverage for malformed JSON, unknown top-level fields, unknown nested fields, partial valid/invalid extractor payloads, reducer exception propagation, or rollback semantics.

### Changes made
- No source code changes.
- No tests added or modified.
- `docs/AGENT_PROGRESS.md`, `docs/BUG_QUEUE.md`, and `docs/FIX_LOG.md`: recorded Goal 11A risks and Goal 11B patch direction.

### Commands run
| Command | Result | Notes |
|---|---|---|
| `rg -n "json|JSON|structured|extract|extractor|repair|fallback|directive|payload|apply|delta|reducer|commit|rollback|transaction|except Exception|validate|schema" ming_sim/agents.py ming_sim/simulation.py ming_sim/issues.py ming_sim/db web_app.py tests` | PASS | Located extractor parse/sanitize/reducer and DB commit paths. |
| `rg -n "region_delta|army_delta|treasury|minister|edict|chat|history|structured_directives|turn_structured|turn_directives|delta" ...` | PASS | Located field-specific reducer and structured directive surfaces; one broad scan included extra `ming_sim` output but no source was modified. |
| `rg -n "def parse_agent_json|JSON_SANITIZER|MODULE_FIELDS|EMPTY_EXTRACTION|TOP_LEVEL|ITEM_FIELD|_clean_economy_moves|_clean_fiscal|_merge_module_outputs" ming_sim\agents.py ming_sim\simulation.py` | PASS | Confirmed JSON object parse, sanitizer fallback, top-level module allowlists, and permissive clean/drop behavior. |
| `rg -n "def apply_score_extraction|apply_region_deltas|apply_army_deltas|commit\(|rollback\(|except Exception" ming_sim\issues.py ming_sim\db tests` | PASS | Confirmed broad exception/continue paths and many independent DB commits; no rollback boundary found in audited files. |
| `rg -n "malformed|invalid JSON|unknown|unknown nested|region_delta|army_delta|partial|rollback|exception|LLMContractError|apply_score_extraction|extract_scores|sanitizer" tests` | PASS | Found existing happy-path/field tests, but not the failure-mode/rollback tests required for Goal 11B. |
| `git status --short` | PASS | Shows existing Goal 10B changes plus docs updated by this audit. |

### Current status
- RISK_FOUND: audit completed; no source code or tests were modified and no real LLM API was called.

### Remaining blockers
- Goal 11B should add strict schema validation, fail-closed unknown-field handling, reducer staging, exception propagation, transaction rollback, and targeted mock tests.
- Existing uncommitted Goal 10B source/test/docs changes remain in the working tree.

### Next recommended action
- Execute Goal 11B as a scoped patch around extractor validation and reducer transaction semantics, without changing gameplay values.

### Files changed this session
- `docs/AGENT_PROGRESS.md`
- `docs/BUG_QUEUE.md`
- `docs/FIX_LOG.md`
