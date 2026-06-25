## Open

- [Medium] ENV_BLOCKER_INTERMITTENT frontend build recurrence: Goal 7B final recheck still fails during frontend build while Vite loads `vite.config.ts`, raising esbuild `Error: spawn EPERM`. `scripts\verify_local.ps1` passed the Python venv, compile, and pytest phases (`49 passed, 1 warning`) before failing at the same frontend build step.
- [Low] ENV_BLOCKER Python compile cache permissions: exact `compileall .` scans access-restricted generated/dependency paths, and the Python-only filtered compileall command still fails because `.pytest_cache` cannot be listed and `.pyc` files under `tests\__pycache__` cannot be replaced. This is local WinError 5 cache/pycache permissions, not a dependency declaration failure.
- [Low] Frontend build warning: Vite previously reported some chunks are larger than 500 kB after minification. Warning remains a follow-up item after the build environment is stable.
- [Low] ENV_BLOCKER workspace cleanup: ignored local files `.pytest.ini.swp` and `pytest_probe.db` remain on disk but return `Access is denied` when deleted. They are ignored and do not appear in `git status --short`.
- [Low] ENV_BLOCKER avatar test temp physical cleanup: old local directories `.test_avatar_upload/`, `.tmp_pytest/`, and `test_avatar_upload_tmp/` may remain on disk because recursive deletion is blocked by tool policy or WinError 5. They are now ignored and do not appear in `git status --short`.
- [Low] Workspace cleanup: remove inaccessible root-level `tmp*` directories and `pytest_probe.db-journal` created by local SQLite file probes. Attempts to delete them from this session returned access denied or were blocked by tool policy.
- [Low] Pytest cache warning: `.venv\Lib\site-packages\_pytest\cacheprovider.py` reports it cannot create `.pytest_cache\v\cache` under the workspace. Tests pass, but cache persistence is unavailable.

## Resolved

- [Medium] Goal 8 DeepSeek V4 advanced smoke mismatch: advanced-model save validation now runs the smoke check with the same thinking-enabled parameter path used by the monthly simulator, DeepSeek V4 thinking parameters are centralized, and legacy DeepSeek model IDs no longer receive V4-only `thinking` fields by default.
- [Low] Goal 9B-Cleanup git-status temp directory hygiene: `.gitignore` now excludes root-level avatar test temp directories (`/.test_avatar_upload/`, `/.tmp_pytest/`, `/test_avatar_upload_tmp/`), and `tests/test_avatar_upload_security.py` no longer creates `.test_avatar_upload/` or `.tmp_pytest/`. The current verification status no longer shows these directories in `git status --short`.
- [High/Medium/Low] Goal 9B avatar upload security: AVATAR-001..005 fixed by hashing custom portrait filenames with NFKC+SHA-256, resolving and containing portrait paths under `UPLOAD_PORTRAIT_DIR`, streaming the 8 MB upload limit, validating and re-encoding real PNG/JPEG/WebP images with Pillow, using temp-file plus `os.replace` writes with rollback, clearing DB before delete-file cleanup, and adding regression tests in `tests/test_avatar_upload_security.py`.
- [Low] Frontend missing asset reference: `web/src/styles.css` no longer references `/bg_ending.webp`; the three ending background declarations now use their existing dark gradient fallbacks only, and searches for `bg_ending` and `/bg_ending.webp` return no matches. Goal 7B final recheck did not find any residual CSS reference.
- [Low] `.gitignore` bg_ending cleanup: removed obsolete `output/imagegen/bg_ending.png` and `web/public/bg_ending.webp` ignore rules so any future restored ending asset can be tracked normally.
- [Medium] Goal 6 smoke-mode gating: `scripts\verify_local.ps1 -Smoke` previously ran compile/test/frontend build before uvicorn, so the local Vite/esbuild `spawn EPERM` environment blocker prevented backend smoke from running. Resolved by making `-Smoke` skip the unrelated full gate and run only the backend startup/listening/cleanup check after the venv check.
- [Medium] Python runtime dependency declaration: `requirements.txt` now includes `python-multipart`, required by FastAPI `UploadFile = File(...)` routes in `web_app.py`.
- [Low] Python test dependency declaration: added `requirements-dev.txt` with `pytest`.
- [Low] Pytest discovery configuration: `pytest.ini` now declares both `testpaths = tests` and `python_files = test_*.py`.
- [Low] Python dependency consistency: installing `requirements.txt` and `requirements-dev.txt` succeeds, and `pip check` reports no broken requirements.
- [Medium] ENV_BLOCKER_INTERMITTENT frontend build recurrence: user manually re-verified `cd web && npm run build` and root `scripts\verify_local.ps1` as PASS after the Windows `esbuild spawn EPERM` condition cleared. Current status is no longer blocking.
- [Medium] Markdown bold renderer review: reviewed and accepted as PASS. AI text `**bold**` markers are rendered via React nodes without `dangerouslySetInnerHTML`.
- [Medium] ENV_BLOCKER frontend build: Vite failed while starting esbuild with `Error: spawn EPERM`. Resolved after dependency/environment recovery and confirmed again by user manual verification.
- [Medium] Frontend install: earlier `npm ci` attempts failed with Windows `EPERM` permission errors in `web/node_modules` and npm cache paths. Resolved by correcting the frontend dependency/directory state and rebuilding successfully.
- [Low] `.gitignore`: editor swap files were not ignored. Resolved by adding `*.swp` and `*.swo`.

- [Medium] `web/src/components/modals.tsx`: AI text displayed Markdown bold markers such as `**已办成**` literally in reports/chats. Resolved with a React-node inline bold renderer that does not use `dangerouslySetInnerHTML`.
- [Medium] `scripts/verify_local.ps1`: default frontend build failed on half-installed `web/node_modules` because it checked only the directory, not required `.bin` tools. Resolved by requiring `tsc.cmd` and `vite.cmd` before build.
- [High] `tests/test_arms_and_troops.py`: Windows/local file-backed SQLite fixture failed with `sqlite3.OperationalError: unable to open database file`. Resolved by using an in-memory `GameDB` fixture for the affected tests.
- [Medium] `tests/test_structured_directives.py`: `_DummyDB` missing `army_held_arms_all`. Resolved with a minimal stub returning `{}`.
