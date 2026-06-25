# Release / Sharing Checklist

Use this checklist before pushing a branch, opening a PR, or sharing a build.

## Required Runtime

- Python 3.12.x.
- Node.js 20+.
- A local Python virtualenv at `.venv/`.
- Frontend dependencies installed under `web/node_modules/`.
- LLM API credentials configured locally only, never committed.

## Fresh Setup

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd web
npm ci
cd ..
```

macOS/Linux shell:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt
cd web
npm ci
cd ..
```

## Local LLM Configuration

Copy `.env.example` to `.env` and fill local values only:

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
```

Do not commit `.env`, API keys, local save databases, uploaded portraits, logs, token traces, or local cache directories.

## Verification Commands

Python tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Backend smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1 -Smoke
```

Frontend build:

```powershell
cd web
npm run build
cd ..
```

Full local gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1
```

## Known Windows Environment Blocker

On some local Windows runs, Vite fails while loading `vite.config.ts` with `Error: spawn EPERM` from esbuild. If this happens and Python tests plus backend smoke pass, classify it as `ENV_BLOCKER_INTERMITTENT` unless TypeScript/CSS source diagnostics are also present.

Recovery actions that have previously cleared it:

- Close extra Node, VS Code, terminal, and assistant processes using the repo.
- Allowlist the repository and npm cache directory in antivirus / Defender.
- Delete `web/node_modules`.
- Run `npm cache verify` or clean the npm cache if needed.
- Re-run `cd web && npm ci`, then `npm run build`.

## Pre-Push Guard

Run:

```powershell
git status --short
git status --short --ignored
git ls-files | findstr /i ".env db sqlite node_modules dist .venv cache"
```

Expected:

- No tracked `.env`, API key files, local SQLite databases, save files, uploaded portraits, `.venv`, `node_modules`, `web/dist`, or cache directories.
- `.env.example` and source files under `ming_sim/db/` are expected tracked matches.

## PR Preparation

Recommended PR title:

```text
Harden local verification, uploads, save restore, LLM reducer validation, and settlement transactions
```

Recommended summary:

- Fix local verification dependencies and backend smoke flow.
- Secure custom portrait upload storage and validation.
- Make save restore candidate-validated and rollback-safe.
- Add strict structured LLM payload validation and reducer fail-fast behavior.
- Add SQLite transaction/savepoint support and settlement rollback coverage.

Recommended test plan:

- `.\.venv\Scripts\python.exe -m pytest -q`
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1 -Smoke`
- `cd web && npm run build`
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_local.ps1`
