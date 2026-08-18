# Autonomous Coding Agent

A local, autonomous coding agent that takes a Markdown task file, plans, implements,
tests, builds, runs the app, tests it in a real browser at multiple viewports, reviews
its own work, fixes what's broken, and repeats — until the task passes or the agent
gets stuck and reports `BLOCKED` instead of looping forever.

Point it at a task before bed. In the morning: read `reports/<task-id>.md`, run
`git diff` on the branch it created, eyeball the UI, and decide whether to merge.

> **Tiếng Việt:** xem [`docs/HUONG_DAN.md`](docs/HUONG_DAN.md) — hướng dẫn cài đặt
> chi tiết và cách trỏ agent vào một repo landing page có sẵn.

**This is not a chatbot.** The LLM calls real tools — it reads files, edits code, runs
shell commands, drives a real Chromium browser, and reads the results back — across
many turns, with no human in the loop until the run finishes.

---

## Architecture

```
                    ORCHESTRATOR
                         │
          ┌──────────────┼──────────────┐
          │              │              │
       PLANNER        CODER          TESTER
      (read-only)   (fs + shell)   (runs lint/
                                    build/tests)
          │              │              │
          └──────────────┼──────────────┘
                         │
                  BROWSER TESTER
                 (real Playwright)
                         │
                         ▼
                     REVIEWER
                   (read-only, does
                    NOT trust Coder)
                         │
                  PASS / FAIL / BLOCKED
                         │
                  ┌──────┴──────┐
                  │             │
                 FIX           DONE
                  │
                  └──── LOOP (max_iterations) ──┘
```

Each role is a separate LLM context with a separate, restricted tool set:

| Role     | Tools allowed                                              | Can edit code? |
|----------|-------------------------------------------------------------|----------------|
| Planner  | list_files, read_file, search_code, git_status/diff/log     | No (enforced)  |
| Coder    | + write_file, edit_file, delete_file, run_command            | Yes            |
| Tester   | not an LLM role — runs npm scripts directly, reports exit codes | No          |
| Browser Tester | not an LLM role — drives real Chromium via Playwright  | No             |
| Reviewer | list_files, read_file, search_code, git_status/diff/log     | No (enforced)  |

The Planner and Reviewer's tool restriction isn't just a prompt instruction — a
`ReadOnlyToolExecutor` wrapper actually rejects any write/shell/browser tool call at
the dispatch level, so a model ignoring its instructions still can't act on it.

---

## Installation

Requires Python 3.10+, Node.js (for testing JS/TS projects), and Git.

```bash
cd autonomous-coding-agent
pip install -r requirements.txt --break-system-packages   # or use a venv
playwright install chromium
```

Then run the environment check:

```bash
python -m cli.main doctor
```

This checks Ollama, your configured models, Node, npm, Git, Python, Playwright's
Chromium binary, and workspace write permissions — before you ever hand it a task.
Fix anything marked `FAIL` before proceeding.

---

## Ollama setup

Install [Ollama](https://ollama.com), then pull the models you intend to use:

```bash
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-vl:7b     # optional — only needed for visual (screenshot) review
ollama serve                  # if not already running as a service
```

### Model configuration

Nothing is hard-coded. Edit `config.yaml`:

```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434
  coding_model: qwen2.5-coder:7b
  vision_model: qwen2.5-vl:7b
```

**Sizing guidance for a 12GB-VRAM card (e.g. RTX 3060 12GB):**
- `qwen2.5-coder:7b` fits fully in VRAM and is the safest default.
- `qwen2.5-coder:14b` at Q4 is borderline (~9–10GB) — noticeably slower, may spill to
  CPU RAM depending on what else is loaded.
- Avoid 32b+ coding models on 12GB cards unless you're comfortable with CPU-offloaded
  inference, which will be dramatically slower (potentially hours per iteration
  instead of minutes) — this matters a lot for an overnight run with a fixed
  `max_iterations` budget.
- `qwen2.5-vl:7b` is a reasonable default for the vision role since it shares the
  Qwen tokenizer/family, but any Ollama-servable vision model works.

If `vision_model` is left blank, the `VISUAL_REVIEW` state is automatically skipped
(not silently treated as "passed") and this is noted in the log and final report.

### Swapping providers later

`agent/llm/base.py` defines the `LLMClient` interface (`chat()`, `vision()`,
`health_check()`). `agent/llm/ollama_client.py` is the only file that knows about
Ollama's HTTP API. To add another backend, implement `LLMClient` and register it in
`agent/llm/__init__.py`'s `build_llm_client()` factory — nothing else changes.

---

## Playwright setup

```bash
pip install playwright
playwright install chromium
```

If `playwright install` fails on a `--with-deps` apt step in a sandboxed/CI
environment, try `playwright install chromium` without `--with-deps` — the browser
binary itself may already be cached, and only the OS-level font/codec packages
(usually unnecessary for headless testing) fail to install.

---

## Running the agent

```bash
python -m cli.main run tasks/landing-page.md
```

Or, after `pip install -e .`:

```bash
ai-agent run tasks/landing-page.md
```

### Other commands

```bash
ai-agent status              # show status of all known tasks
ai-agent logs <task-id>      # print that task's human-readable log
ai-agent logs                # print the tail of every task's log
ai-agent report <task-id>    # print the final report
ai-agent stop <task-id>      # send SIGTERM to a running task by task-id
ai-agent resume <task-id>    # show last known state (see Limitations — no
                              # true mid-run resume yet)
ai-agent doctor              # environment health check
```

---

## Creating tasks

Tasks are Markdown files under `tasks/`. See `tasks/landing-page.md` for a working
example matching this format:

```markdown
# Task

Build a landing page for product X.

## Requirements

- Follow existing project architecture.
- Reuse existing components.
- Do not introduce unnecessary dependencies.
- Responsive design required.

## Viewports

- 375x812
- 768x1024
- 1440x900

## Acceptance Criteria

- Build passes.
- Lint passes.
- No console errors.
- No obvious layout overflow.
- Mobile layout works.
- Tablet layout works.
- Desktop layout works.
- Existing pages remain functional.
```

Unrecognized `##` sections aren't dropped — they're preserved and passed to the
Planner as extra context, so you can add project-specific notes freely.

---

## Configuration reference (`config.yaml`)

| Key | Meaning |
|---|---|
| `llm.*` | provider, base URL, model names, timeouts/retries |
| `agent.max_iterations` | hard cap — the run goes `BLOCKED` past this, never loops forever |
| `agent.dev_server_url` | URL the Browser Tester navigates to; override per-task with a `## Dev Server` section in the task file |
| `agent.repeated_failure_threshold` | how many times the *same* failure signature must repeat before the Coder is told to change strategy |
| `agent.max_strategy_changes` | if strategy changes this many times and still fails identically, go `BLOCKED` early |
| `workspace.root` | the ONLY directory the agent may read/write/execute in |
| `browser.viewports` | list of `{name, width, height}` — tested every iteration |
| `shell.allowed` / `shell.denied_patterns` | command allow-list and destructive-pattern denylist |
| `git.*` | branch prefix, protected branches, auto_commit/auto_push (both default `false` and, for push, not even implemented — see Security) |

---

## Monitoring an overnight run

- `tail -f logs/<task-id>/agent.log` for human-readable progress.
- `logs/<task-id>/agent.jsonl` for machine-readable structured events.
- `logs/<task-id>/iteration-NNN/` per iteration: `tool_calls.json`, `test-results.json`,
  `review.json`, `screenshots/*.png`, `stdout.log`, `stderr.log`.
- `ai-agent status` shows a one-line summary of every task the agent knows about.

## Reports

`reports/<task-id>.md` is written at the end of every run (whether it passed or was
blocked). It contains: status, timing, iteration count, files changed, per-check test
results, per-viewport browser results, console/network errors, visual issues, the
Reviewer's remaining concerns, and the git branch to review. It deliberately never
claims the work is "100% perfect", "guaranteed", or "production-ready" — only
`READY_FOR_HUMAN_REVIEW` or `BLOCKED`. A human always makes the merge decision.

---

## Security model

- **Filesystem:** every path goes through `tools/sandbox.py` before touching disk.
  Blocks `../` traversal, absolute-path escape, and symlink escape out of
  `workspace.root`. Denied filenames (`.env`, `.ssh`, `secrets`, `credentials`) are
  blocked anywhere in the path, even nested.
- **Shell:** first-token allow-list (`shell.allowed`) plus a denylist of destructive
  patterns (`rm -rf /`, `shutdown`, fork bombs, etc.), enforced even across shell
  chain operators (`&&`, `;`, `|`). Commands run via `subprocess` with `shell=False`
  (argv list), not a shell string, so metacharacter injection can't smuggle an
  unapproved command onto an approved one.
- **Git:** the agent creates and works on its own `ai/<task>-<timestamp>` branch.
  If `workspace.root` isn't a git repository yet, the agent initializes one and makes
  an initial commit before branching, so pointing it at a freshly-downloaded project
  works out of the box. If the workspace already has uncommitted changes, the agent
  refuses to start (`BLOCKED`) rather than silently folding your in-progress edits
  into its own branch — commit or stash first. Any write operation (`commit`) is
  hard-blocked in code if the current branch isn't an `ai/` branch — not just
  discouraged by config. **`git push`, `git reset --hard`, and `git clean -fd` have
  no implementation at all** — there is no method to call, by design, regardless of
  config. Auto-commit defaults to `false`.
- **This is not a provably-secure sandbox against an adversarial model.** The goal is
  to prevent *accidental* damage during normal autonomous exploration (an LLM running
  `npm test` shouldn't be able to `rm -rf /` by mistake or malformed command), not to
  contain a model actively trying to escape. Don't point `workspace.root` at anything
  you're not comfortable an autonomous process modifying overnight.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `doctor` shows `ollama: cannot reach ...` | Ollama isn't running (`ollama serve`) or `base_url` is wrong |
| `doctor` shows a model missing | `ollama pull <model-name>` — model names in config must match `ollama list` exactly (including the `:tag`) |
| Run goes `BLOCKED` immediately | Check `logs/<task-id>/agent.log` — often a git branch creation failure (dirty repo state) or Planner LLM failure |
| `START_APP` fails to become ready | Check `logs/<task-id>/iteration-NNN/*.stdout.log` / `*.stderr.log` for the dev server's actual error — usually a missing dependency the Coder wasn't authorized to install |
| Browser test always fails with connection refused | The dev server process may have been killed between states — check the dev server's log files; also confirm the port in your task/project matches what the orchestrator is polling |
| Same failure repeats every iteration | This is meant to trigger `repeated_failure_threshold` and force a strategy change — if it still can't resolve after `max_strategy_changes`, it will go `BLOCKED` rather than loop forever; read the report for the repeated failure signature |

---

## Limitations (honest list — read before relying on this overnight)

- **Local models are weaker than hosted frontier models at multi-file agentic coding.**
  How well this actually converges on a nontrivial task depends heavily on which
  model you point it at. Simple, well-scoped tasks (one page, one component) will go
  far better than "redesign the whole app."
- **No true mid-run resume.** `ai-agent resume` shows the last known state but cannot
  restore an in-progress Coder/Tester loop — this is flagged as a known gap, not
  silently pretended to work (state persistence + full resume is a natural Phase 6
  follow-up, not implemented in this version).
- **Vision review is best-effort.** It's explicitly scoped to only flag what's
  reliably visible in a static screenshot (overflow, clipping, broken layout,
  overlap, broken images) — not subjective design judgment. If no `vision_model` is
  configured, this stage is skipped, not silently marked "passed."
- **Shell policy is an allow-list, not a sandboxed container.** It stops accidental
  destructive commands, not a determined adversarial prompt injection from a
  malicious file inside the repository the agent is asked to work on. Don't run this
  against untrusted repositories without additional isolation (e.g. a VM or
  container around the whole `workspace.root`).
- **`docker` in `shell.allowed` does not mean the agent sandboxes itself in Docker** —
  it just means the agent is *permitted to run* `docker` commands if the task needs
  them (e.g. testing a containerized service). The agent's own process still runs
  directly on your machine.
- **The Tester agent's stack detection is npm/`package.json`-based only** in this
  version. Python/other-stack projects will show all static checks as `SKIPPED`
  rather than silently `PASS` — but full multi-language build detection (pytest,
  cargo, etc.) is not yet implemented.
