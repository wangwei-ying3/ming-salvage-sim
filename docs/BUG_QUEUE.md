## Open

- [Medium] ENV_BLOCKER_INTERMITTENT frontend build recurrence: final Goal 3/4 re-verification still fails during `npm run build` and `scripts\verify_local.ps1` while Vite loads `vite.config.ts`, raising esbuild `Error: spawn EPERM`. Direct `esbuild.cmd --version` and `tsc.cmd -b` pass, and Python compileall/pytest pass.
- [Low] Frontend build warning: Vite previously reported `/bg_ending.webp` referenced in `/bg_ending.webp` did not resolve at build time. Warning remains a follow-up item after the build environment is stable.
- [Low] Frontend build warning: Vite previously reported some chunks are larger than 500 kB after minification. Warning remains a follow-up item after the build environment is stable.
- [Low] ENV_BLOCKER workspace cleanup: ignored local files `.pytest.ini.swp` and `pytest_probe.db` remain on disk but return `Access is denied` when deleted. They are ignored and do not appear in `git status --short`.
- [Low] Workspace cleanup: remove inaccessible root-level `tmp*` directories and `pytest_probe.db-journal` created by local SQLite file probes. Attempts to delete them from this session returned access denied or were blocked by tool policy.
- [Low] Pytest cache warning: `.venv\Lib\site-packages\_pytest\cacheprovider.py` reports it cannot create `.pytest_cache\v\cache` under the workspace. Tests pass, but cache persistence is unavailable.

## Resolved

- [Medium] ENV_BLOCKER frontend build: Vite failed while starting esbuild with `Error: spawn EPERM`. Previously resolved in Goal 3 by reinstalling dependencies from the correct `web` directory and rebuilding; reopened as a recurrence in Goal 4.
- [Medium] Frontend install: earlier `npm ci` attempts failed with Windows `EPERM` permission errors in `web/node_modules` and npm cache paths. Resolved by correcting the frontend dependency/directory state and rebuilding successfully.
- [Low] `.gitignore`: editor swap files were not ignored. Resolved by adding `*.swp` and `*.swo`.

- [Medium] `web/src/components/modals.tsx`: AI text displayed Markdown bold markers such as `**已办成**` literally in reports/chats. Resolved with a React-node inline bold renderer that does not use `dangerouslySetInnerHTML`.
- [Medium] `scripts/verify_local.ps1`: default frontend build failed on half-installed `web/node_modules` because it checked only the directory, not required `.bin` tools. Resolved by requiring `tsc.cmd` and `vite.cmd` before build.
- [High] `tests/test_arms_and_troops.py`: Windows/local file-backed SQLite fixture failed with `sqlite3.OperationalError: unable to open database file`. Resolved by using an in-memory `GameDB` fixture for the affected tests.
- [Medium] `tests/test_structured_directives.py`: `_DummyDB` missing `army_held_arms_all`. Resolved with a minimal stub returning `{}`.
