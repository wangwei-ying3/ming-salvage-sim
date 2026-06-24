\# AGENTS.md



\## Project Goal



This repository is a Ming dynasty strategy simulation game using a Python/FastAPI backend, SQLite/local state, LLM-driven narrative and decision processing, a React/Vite frontend, and optional Electron packaging.



The primary goal of this repair workflow is to make the project reliably installable, runnable, testable, and shareable without changing core gameplay design unless explicitly requested.



\## Hard Rules



\* Do not commit or expose API keys, `.env`, local database files, save files, personal images, logs containing secrets, or local machine paths.

\* Do not modify core gameplay balance, historical setting, character design, numeric formulas, or story logic unless the task explicitly asks for it.

\* Do not perform broad rewrites. Prefer small, reviewable patches.

\* Fix one bug category per pass whenever possible.

\* Before changing code, try to reproduce or precisely identify the failure.

\* After changing code, run the smallest relevant check first, then run the broader verification gate.

\* If a command cannot run because dependencies, OS, credentials, API keys, or network are missing, record that clearly instead of pretending it passed.

\* Do not call real paid LLM APIs during automated tests unless explicitly requested. Use mocks, fixtures, or dependency injection for tests.

\* Prefer deterministic reducers and schema validation for game state updates. LLM output should not directly mutate persistent state without validation.

\* Do not create git commits unless explicitly instructed by the user.



\## Repository Areas



Expected areas to inspect:



\* `web\_app.py`: FastAPI app and web backend routes.

\* `server\_backend.py`: backend/server integration if used.

\* `main.py`: CLI gameplay entry.

\* `ming\_sim/`: core simulation, state, entities, game logic.

\* `web/`: React/Vite frontend and optional Electron configuration.

\* `tests/`: Python tests.

\* `.github/workflows/`: CI/release workflows.

\* `requirements.txt`: Python dependencies.

\* `web/package.json`: frontend, build, Electron scripts.



\## Standard Verification Commands



Use Windows PowerShell commands unless the environment is Linux/macOS.



Python checks:



```powershell

python --version

python -m pip install -U pip

pip install -r requirements.txt

python -m compileall .

python -m pytest -q

```



Frontend checks:



```powershell

cd web

npm ci

npm run build

cd ..

```



Backend smoke test:



```powershell

python -m uvicorn web\_app:app --host 127.0.0.1 --port 8010

```



Do not run long-lived servers indefinitely. If a server starts successfully, record that and stop it.



\## Bugfix Loop Protocol



For each repair loop:



1\. Inspect the current failing command, issue, or user-reported bug.

2\. Identify the minimal affected files.

3\. Reproduce the issue or explain why reproduction is not possible.

4\. Add or update a test when practical.

5\. Apply the smallest safe fix.

6\. Run the relevant targeted check.

7\. Run the broader verification gate if the targeted check passes.

8\. Review `git diff`.

9\. Update `docs/AGENT\_PROGRESS.md`.

10\. Update `docs/BUG\_QUEUE.md` and `docs/FIX\_LOG.md`.



\## Required End-of-Session Output



At the end of every Codex session, append this template to `docs/AGENT\_PROGRESS.md`:



```markdown

\## Session YYYY-MM-DD HH:mm Local



\### Goal

\- ...



\### What I inspected

\- ...



\### Bugs found

\- \[Severity] File/path: description



\### Changes made

\- File/path: summary



\### Commands run

| Command | Result | Notes |

|---|---|---|

| `...` | PASS/FAIL/SKIPPED | ... |



\### Current status

\- PASS/FAIL/PARTIAL



\### Remaining blockers

\- ...



\### Next recommended action

\- ...



\### Files changed this session

\- ...

```



Also update `docs/FIX\_LOG.md` with durable fixes and update `docs/BUG\_QUEUE.md` with remaining unresolved items.



\## Definition of Done



A fix is not considered done unless:



\* The relevant failure is reproduced or clearly explained.

\* The fix is minimal and scoped.

\* The relevant tests/build commands were run or explicitly marked skipped with reason.

\* `docs/AGENT\_PROGRESS.md` was updated.

\* `git diff` contains no accidental secrets, local files, databases, save files, or unrelated formatting churn.



\## Sharing and Release Rules



Before sharing the fixed project:



\* Keep the original `LICENSE`.

\* Keep attribution to the upstream repository.

\* Do not include `.env`, API keys, personal save files, local databases, or uploaded images.

\* Ensure `README.md` has accurate setup instructions for Windows and macOS/Linux if changed.

\* Ensure `requirements.txt` and `web/package.json` reflect the actual dependencies.

\* Prefer publishing a GitHub branch, pull request, or GitHub Release rather than sending random zip files.



