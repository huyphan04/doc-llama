"""
End-to-end orchestrator test — spec section 27's self-test requirement.

Scenario scripted:
  1. Planner inspects repo, returns a plan.
  2. Coder "implements" (writes a server script) — no tool calls, straight
     to final JSON (keeps the script short; filesystem correctness is
     already covered by tests/test_filesystem.py).
  3. Tester runs npm scripts: lint/build initially configured to FAIL on
     iteration 1 (simulates spec section 10's example: "Iteration 1: Build
     FAIL"), then the fix instruction is issued, Coder "fixes" it (we swap
     the build script's exit code by writing a marker file the fake
     project's build script checks), Tester reruns and it passes.
  4. START_APP starts a real tiny Python HTTP server (standing in for
     `npm run dev`), BROWSER_TEST hits it with real Playwright/Chromium.
  5. VISUAL_REVIEW: vision model configured absent in this test's Config,
     so it should be skipped without blocking the loop (spec section 14:
     vision review only runs if a vision_model is configured).
  6. Reviewer scripted to PASS once tester+browser are both clean.

This exercises real filesystem tools, real shell execution, real process
management, real Chromium via Playwright, and real git branch creation —
only the LLM calls are scripted/fake. It is the closest thing to spec
section 27's "actually run the sample task end to end" achievable without
a live Ollama instance.
"""
from __future__ import annotations

import json
import socket
import sys
import textwrap
from pathlib import Path

import pytest

from agent.config import from_dict
from agent.logging_setup import AgentLogger
from agent.orchestrator import Orchestrator
from agent.state_machine import State
from agent.task_parser import parse_task_text
from tests.fake_llm import ScriptedLLMClient


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def project(tmp_path):
    """A toy 'Node' project whose build script fails until a marker file
    (created by the simulated Coder fix) exists, and whose 'dev' script is
    actually a tiny real HTTP server so BROWSER_TEST can hit something real."""
    ws = tmp_path / "ws"
    ws.mkdir()

    build_script = ws / "build_check.py"
    build_script.write_text(textwrap.dedent(f"""
        import sys, os
        marker = os.path.join(os.path.dirname(__file__), "FIXED")
        if not os.path.exists(marker):
            print("TypeError: Cannot read property 'map' of undefined in Hero.tsx", file=sys.stderr)
            sys.exit(1)
        print("build ok")
        sys.exit(0)
    """))

    server_script = ws / "server.py"
    server_script.write_text(textwrap.dedent("""
        import http.server, sys
        port = int(sys.argv[1])
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Landing Page</h1></body></html>")
            def log_message(self, *a): pass
        http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """))

    port = _free_port()
    pkg = {
        "name": "demo",
        "scripts": {
            "lint": f"{sys.executable} -c \"import sys; sys.exit(0)\"",
            "build": f"{sys.executable} build_check.py",
            "dev": f"{sys.executable} server.py {port}",
        },
    }
    (ws / "package.json").write_text(json.dumps(pkg))
    (ws / "README.md").write_text("# Demo project\n")

    return ws, port


@pytest.fixture
def config(tmp_path, project):
    ws, port = project
    raw = {
        "llm": {"provider": "ollama", "coding_model": "fake-model"},  # unused, ScriptedLLMClient bypasses this
        "agent": {"max_iterations": 10, "repeated_failure_threshold": 2, "max_strategy_changes": 3},
        "workspace": {"root": str(ws)},
        "browser": {
            "enabled": True, "headless": True,
            "viewports": [{"name": "mobile", "width": 375, "height": 812}],
        },
        "shell": {
            "timeout": 30,
            "allowed": ["npm", "python3", sys.executable.split("/")[-1], "git"],
        },
        "git": {"protected_branches": ["main", "master"], "branch_prefix": "ai"},
        "logging": {"dir": str(tmp_path / "logs")},
    }
    cfg = from_dict(raw, base_dir=tmp_path)
    return cfg, port


@pytest.fixture
def git_initialized(project):
    ws, _ = project
    from tools.git_tools import GitTools

    gt = GitTools(workspace_root=ws, protected_branches=["main", "master"], branch_prefix="ai")
    gt._run("init", "-b", "main")
    gt._run("config", "user.email", "test@test.local")
    gt._run("config", "user.name", "Test")
    gt._run("add", "-A")
    gt._run("commit", "-m", "init")
    return ws


def test_full_loop_build_fails_then_passes(config, git_initialized):
    cfg, port = config
    logger = AgentLogger(log_dir=cfg.resolve_path(cfg.logging.dir))
    task = parse_task_text(
        "# Task\nBuild a landing page.\n\n## Acceptance Criteria\n- Build passes.\n- No console errors.\n",
        task_id="landing-page",
    )

    dev_url = f"http://127.0.0.1:{port}"

    planner_response = {
        "status": "PLANNED",
        "detected_stack": {"framework": "unknown", "styling": "css", "package_manager": "npm", "notes": ""},
        "relevant_files": ["server.py"],
        "plan_steps": ["Serve a landing page"],
        "dependency_requests": [],
        "risks": [],
        "verification_plan": {"lint": True, "build": True, "browser": True},
    }

    coder_initial_response = {
        "status": "IMPLEMENTED",
        "summary": "Initial implementation (build intentionally broken to test fix loop)",
        "files_changed": [],
        "files_created": [],
        "files_deleted": [],
        "notes_for_reviewer": "",
    }

    # Coder "fix" turn: creates the FIXED marker file via write_file tool call,
    # then returns its final JSON.
    coder_fix_tool_call = [("write_file", {"path": "FIXED", "content": "done"})]
    coder_fix_response = {
        "status": "IMPLEMENTED",
        "summary": "Fixed the Hero.tsx TypeError by guarding undefined props",
        "files_changed": ["Hero.tsx"],
        "files_created": [],
        "files_deleted": [],
        "notes_for_reviewer": "",
    }

    reviewer_pass_response = {
        "status": "PASS",
        "score": 95,
        "issues": [],
        "required_fixes": [],
        "reasoning": "Build passes, browser check clean, acceptance criteria met.",
    }

    llm = ScriptedLLMClient(
        script=[
            planner_response,       # PLAN
            coder_initial_response, # IMPLEMENT (iteration 1)
            coder_fix_tool_call,    # IMPLEMENT_FIX turn 1: tool call
            coder_fix_response,     # IMPLEMENT_FIX turn 2: final answer
            reviewer_pass_response, # REVIEW (iteration 2, after fix)
        ]
    )

    orch = Orchestrator(cfg, llm, logger, task)
    orch.dev_server_url = dev_url

    result = orch.run()

    assert result.final_status == "READY_FOR_HUMAN_REVIEW", f"block_reason={result.block_reason}"
    assert result.branch_name.startswith("ai/landing-page-")
    assert len(result.iterations) >= 2

    # iteration 1 should show a build failure recorded
    iter1 = result.iterations[0]
    assert iter1.tester_report is not None
    build_check = next(c for c in iter1.tester_report.checks if c.name == "build")
    assert build_check.status == "FAIL"

    # final iteration's tester report (after fix) should show build passing
    final_tester = [r.tester_report for r in result.iterations if r.tester_report][-1]
    final_build_check = next(c for c in final_tester.checks if c.name == "build")
    assert final_build_check.status == "PASS"

    # browser check actually ran against the real server
    assert any(r.browser_results for r in result.iterations)
    browser_iter = next(r for r in result.iterations if r.browser_results)
    assert browser_iter.browser_results[0].ok
    assert not browser_iter.browser_results[0].horizontal_overflow

    assert result.iterations[-1].review_verdict is not None
    assert result.iterations[-1].review_verdict.status == "PASS"


def test_blocked_after_max_iterations(config, git_initialized):
    """If the Coder never fixes the build, orchestrator must go BLOCKED
    rather than loop forever — spec section 11."""
    cfg, port = config
    cfg.agent.max_iterations = 2
    logger = AgentLogger(log_dir=cfg.resolve_path(cfg.logging.dir))
    task = parse_task_text("# Task\nBuild a landing page.\n\n## Acceptance Criteria\n- Build passes.\n", task_id="stuck-task")

    planner_response = {
        "status": "PLANNED", "detected_stack": {}, "relevant_files": [],
        "plan_steps": ["step"], "dependency_requests": [], "risks": [],
        "verification_plan": {},
    }
    coder_response = {
        "status": "IMPLEMENTED", "summary": "does nothing useful",
        "files_changed": [], "files_created": [], "files_deleted": [], "notes_for_reviewer": "",
    }

    # Never creates the FIXED marker, so build fails every time -> BLOCKED expected
    llm = ScriptedLLMClient(script=[planner_response] + [coder_response] * 10)

    orch = Orchestrator(cfg, llm, logger, task)
    orch.dev_server_url = f"http://127.0.0.1:{port}"
    result = orch.run()

    assert result.final_status == "BLOCKED"
    assert "max_iterations" in result.block_reason or "repeated" in result.block_reason


def test_orchestrator_auto_inits_git_when_no_repo_present(config, project):
    """Simulates pointing the agent at a freshly downloaded/unzipped
    project with no .git yet — must not go straight to BLOCKED."""
    cfg, port = config
    ws, _ = project  # note: no git_initialized fixture used here on purpose
    logger = AgentLogger(log_dir=cfg.resolve_path(cfg.logging.dir))
    task = parse_task_text("# Task\nBuild a landing page.\n\n## Acceptance Criteria\n- Build passes.\n", task_id="fresh-repo-task")

    planner_response = {
        "status": "PLANNED", "detected_stack": {}, "relevant_files": [],
        "plan_steps": ["step"], "dependency_requests": [], "risks": [],
        "verification_plan": {},
    }
    coder_response = {
        "status": "IMPLEMENTED", "summary": "noop",
        "files_changed": [], "files_created": [], "files_deleted": [], "notes_for_reviewer": "",
    }
    llm = ScriptedLLMClient(script=[planner_response] + [coder_response] * 3)

    from tools.git_tools import GitTools
    assert not GitTools(ws, ["main", "master"]).is_repo()

    orch = Orchestrator(cfg, llm, logger, task)
    orch.dev_server_url = f"http://127.0.0.1:{port}"
    result = orch.run()

    # It should NOT be blocked because of missing git — it auto-inits and
    # proceeds (it may still end up BLOCKED later for unrelated reasons
    # like max_iterations, but not with a "could not create git branch" reason).
    assert "could not create git branch" not in (result.block_reason or "")
    assert "could not initialize git repository" not in (result.block_reason or "")
    assert result.branch_name.startswith("ai/fresh-repo-task-")


def test_orchestrator_refuses_dirty_workspace(config, git_initialized):
    """If the workspace has uncommitted changes before the agent even
    starts, refuse rather than silently mixing the user's in-progress
    edits into the agent's branch."""
    cfg, port = config
    (git_initialized / "uncommitted.txt").write_text("oops, forgot to commit this")

    logger = AgentLogger(log_dir=cfg.resolve_path(cfg.logging.dir))
    task = parse_task_text("# Task\nx\n\n## Acceptance Criteria\n- Build passes.\n", task_id="dirty-task")
    llm = ScriptedLLMClient(script=[])  # should never even be called

    orch = Orchestrator(cfg, llm, logger, task)
    result = orch.run()

    assert result.final_status == "BLOCKED"
    assert "uncommitted changes" in result.block_reason
    assert llm.calls_made == 0
