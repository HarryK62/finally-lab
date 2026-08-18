> **Archived and stale.** This README review was written before the platform was
> built, when only `backend/` and `planning/` existed. Every finding in it has
> since been overtaken by the build; it is kept for history only. For the current
> assessment see `planning/MARKET_DATA_REVIEW.md`.

# README.md — Review Findings

Review of `/README.md` (root) as of commit `14550e1`, branch `main`. No changes made — findings only.

## Summary

The README is well-written, concise, and accurately reflects `planning/PLAN.md`. Its central problem is that it documents the **planned** system as if it already exists. The repository currently contains only `backend/` (market data subsystem) and `planning/`. Every instruction in **Quick Start** fails today, and 4 of the 6 directories in **Project Structure** do not exist.

Severity legend: **[H]** blocks a reader from using the repo · **[M]** misleading or incomplete · **[L]** polish.

---

## Critical / Blocking

### 1. [H] Quick Start does not work — no Docker assets exist
Lines 28–38 instruct:

```bash
cp .env.example .env
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

Repository reality (`git ls-files`):
- `.env.example` — **does not exist** (`.gitignore:138` ignores `.env`, but no example is committed)
- `Dockerfile` — **does not exist**
- `docker-compose.yml` — **does not exist**

A reader following the README hits an error on the very first command. This is the single highest-impact issue.

### 2. [H] Project Structure lists directories that are absent
Lines 50–58 show six directories. Present: `backend/`, `planning/`. Absent: `frontend/`, `test/`, `db/`, `scripts/`.

Note also that `.github/` (two Claude Code workflows) and `.claude/` (settings + `cerebras` skill) *do* exist and are not listed.

### 3. [H] No indication of build status
Nothing in the README tells the reader that only the market data component is complete. `CLAUDE.md` and `planning/MARKET_DATA_SUMMARY.md` state this clearly; the README does not. A "Status / Roadmap" section (or a one-line banner near the top) would resolve items 1–3 at low cost — the aspirational content can stay if it is labelled as target state.

---

## Accuracy / Completeness

### 4. [M] No way to run anything that actually exists
The only currently runnable artefacts are undocumented in the root README:

```bash
cd backend
uv sync --extra dev
uv run --extra dev pytest -v      # test suite
uv run market_data_demo.py        # live terminal price dashboard
```

The demo in particular is a strong "see it work in 30 seconds" hook and deserves a place in the README.

### 5. [M] No prerequisites section
Docker is assumed. For local work, `uv` and Python ≥3.12 (`backend/pyproject.toml:6`) are required, plus Node for the future frontend. None are stated, and there is no link to install instructions.

### 6. [M] `OPENROUTER_API_KEY` marked "Required" is overstated
Per `planning/PLAN.md` §5, the app runs fine without it — only the AI chat panel is affected, and `LLM_MOCK=true` bypasses the need entirely. Suggest "Required for AI chat" rather than a flat "Yes", and state the default for `LLM_MOCK` (`false`) as the table does not list defaults for any variable.

### 7. [M] "Clone and configure" has no clone command
Line 29's comment says "Clone and configure" but the block starts at `cp .env.example .env`. Either add the `git clone` line or retitle the step.

### 8. [M] Start/stop scripts are never mentioned
`planning/PLAN.md` §11 specifies `scripts/start_mac.sh`, `stop_mac.sh`, `start_windows.ps1`, `stop_windows.ps1` as the intended entry point for students — a friendlier path than raw `docker build`/`docker run`. The README documents only the raw Docker commands and never mentions `scripts/`, which it does list in the structure tree. There is also no stop/cleanup instruction at all.

### 9. [M] Docker command details worth expanding
- Runs in the foreground with no `-d` and no `--name`, so the stop instruction (missing anyway) cannot be written naturally.
- Uses a named volume `finally-data`, while `planning/PLAN.md` §4 describes the repo's `db/` directory as the volume mount target. These are two different persistence models; the README should pick one and say which.
- No note on what to do if port 8000 is already in use.

---

## Cross-Document Consistency

### 10. [M] `backend/README.md` install command is wrong
`backend/README.md:25` says `uv sync --dev`, but `backend/pyproject.toml:15-21` declares dev tools under `[project.optional-dependencies]`, i.e. an *extra*, not a dependency group. The correct invocation is `uv sync --extra dev`, which is what `backend/CLAUDE.md` uses. `uv sync --dev` will not install pytest/ruff. Same issue affects the four `uv run pytest ...` examples at `backend/README.md:28-37`, which omit `--extra dev`.

Out of scope for the root README strictly, but it is the file a reader lands on next, and the two docs currently contradict each other.

### 11. [L] Root README does not link to sibling docs
No links to `planning/PLAN.md` (the full spec), `planning/MARKET_DATA_SUMMARY.md`, or `backend/README.md`. A short "Documentation" section would help agents and humans navigate.

---

## Polish

### 12. [L] License section is thin
Line 62 says only "See LICENSE". The file is MIT (© 2026 Ed Donner). Naming the licence inline is conventional and lets readers skip the click. A badge is optional.

### 13. [L] No visuals
For a project whose stated selling point is being "visually stunning" (line 3) with a "Dark terminal aesthetic" (line 14), the absence of a screenshot or GIF is a notable gap. The Rich terminal demo could supply an interim one before the frontend exists.

### 14. [L] Marketing tone in the opening line
"A visually stunning AI-powered trading workstation" is copied verbatim from the plan's Vision section. Self-praise reads oddly in a README where the described UI does not yet exist; consider deferring the adjective until there is a screenshot to back it.

### 15. [L] Minor wording
- Line 24: "Massive API (optional)" — the API's relationship to Polygon.io is explained in the env table but not here; a reader meeting "Massive" for the first time at line 24 has no context.
- Line 45: "Massive (Polygon.io) key" — consistent with the plan, fine, but worth a link to where one is obtained.
- No contributing, development-workflow, or architecture-diagram section. Reasonable to omit for a course capstone; worth a conscious decision.

---

## What the README gets right

- Structure and ordering are conventional and easy to scan (features → architecture → quick start → env → layout → licence).
- Feature and architecture bullets match `planning/PLAN.md` precisely — no drift between spec and README.
- Environment-variable table is clear, and correctly explains the simulator-vs-Massive fallback.
- Length is appropriate; it does not duplicate the full plan.

---

## Suggested priority order

1. Add a **Status** note stating that only the market data backend is implemented (fixes 1–3 in one stroke).
2. Add a **Running locally today** section: `uv sync --extra dev`, pytest, `market_data_demo.py` (4, 5).
3. Commit a `.env.example`, or drop the `cp .env.example .env` line until it exists (1).
4. Add **Prerequisites** and a **Documentation** links section (5, 11).
5. Fix `backend/README.md`'s `--dev` → `--extra dev` (10).
6. Soften `OPENROUTER_API_KEY` requirement, add defaults column (6).
7. Polish: licence name, screenshot placeholder, stop instructions (8, 12, 13).
