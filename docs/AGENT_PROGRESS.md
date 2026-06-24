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
