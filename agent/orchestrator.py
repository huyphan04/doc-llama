"""
Orchestrator — spec sections 10, 11, 12, 21.

Drives the state machine end to end for one task:
  - creates the AI git branch (section 8)
  - runs Planner once (INSPECT+PLAN)
  - loops IMPLEMENT -> STATIC_TEST -> BUILD -> START_APP -> BROWSER_TEST ->
    VISUAL_REVIEW -> REVIEW, routing failures into ANALYZE_FAILURE ->
    CREATE_FIX_PLAN -> IMPLEMENT_FIX -> retest
  - enforces max_iterations -> BLOCKED (section 11)
  - enforces repeated-failure detection -> forces a strategy change (section 12)
  - writes per-iteration logs under logs/iteration-NNN/ (section 12)
  - always tears down any process it started, every iteration, even on
    exception, so a dev server never survives past the run
  - generates reports/<task_id>.md at the end (section 21)

This module intentionally contains no LLM prompt text of its own — all
role-specific prompting lives in agent/agents/*.py so orchestrator.py stays
readable as "the sequence of steps", not "the sequence of steps interleaved
with prompt engineering".
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from agent.agents.coder import build_fix_instruction, build_initial_instruction, run_coder
from agent.agents.planner import run_planner
from agent.agents.reviewer import ReviewVerdict, run_reviewer
from agent.agents.tester import TesterReport, run_tester
from agent.config import Config
from agent.failure_memory import FailureMemory
from agent.llm.base import LLMClient, LLMError, VisionNotSupportedError
from agent.logging_setup import AgentLogger
from agent.role_loop import AgentResponseError
from agent.state_machine import State, TERMINAL_STATES, next_state
from agent.task_parser import Task
from agent.tool_executor import ToolExecutor
from agent.vision_review import summarize_visual_issues
from tools.browser import ViewportCheckResult


@dataclass
class IterationRecord:
    iteration: int
    state_sequence: list[str] = field(default_factory=list)
    tester_report: TesterReport | None = None
    browser_results: list[ViewportCheckResult] = field(default_factory=list)
    visual_issues: list[str] = field(default_factory=list)
    review_verdict: ReviewVerdict | None = None
    coder_summary: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class RunResult:
    task_id: str
    final_status: str  # "READY_FOR_HUMAN_REVIEW" | "BLOCKED"
    iterations: list[IterationRecord] = field(default_factory=list)
    branch_name: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    block_reason: str = ""


class Orchestrator:
    def __init__(self, config: Config, llm: LLMClient, logger: AgentLogger, task: Task):
        self.config = config
        self.llm = llm
        self.logger = logger
        self.task = task
        self.failure_memory = FailureMemory(
            threshold=config.agent.repeated_failure_threshold,
        )
        self.dev_server_url = self._resolve_dev_server_url(config, task)

    def _resolve_dev_server_url(self, config: Config, task: Task) -> str:
        """Priority: task's own '## Dev Server' section (if present) >
        config.yaml's agent.dev_server_url > built-in default. This is the
        only override path — there is no separate CLI flag, since the URL
        is inherently per-project (per-task), not a global run option."""
        task_override = task.extra_sections.get("dev server", "").strip()
        if task_override:
            return task_override.splitlines()[0].strip()
        return config.agent.dev_server_url

    def _iteration_dir(self, n: int) -> Path:
        return self.config.resolve_path(self.config.logging.dir) / f"iteration-{n:03d}"

    def run(self) -> RunResult:
        result = RunResult(task_id=self.task.task_id, final_status="BLOCKED", started_at=time.time())

        executor_root = ToolExecutor(self.config, self.logger.for_iteration(self._iteration_dir(0)))

        if not executor_root.git.is_repo():
            self.logger.info("workspace is not yet a git repository — initializing one")
            init_res = executor_root.git.init_repo_if_needed()
            if not init_res.ok:
                self.logger.error(f"Failed to initialize git repository: {init_res.error}")
                result.block_reason = f"could not initialize git repository: {init_res.error}"
                result.finished_at = time.time()
                return result
        elif executor_root.git.has_uncommitted_changes():
            # Do NOT auto-commit someone else's in-progress work on their
            # behalf — that could silently bundle unrelated changes into
            # the agent's branch and make the eventual diff impossible to
            # review cleanly. Fail loudly instead and let the human decide.
            self.logger.error(
                "workspace has uncommitted changes on the current branch — refusing to start. "
                "Commit or stash your changes first, then re-run."
            )
            result.block_reason = (
                "workspace has uncommitted changes — commit or stash them before running the agent, "
                "so the agent's branch only contains the agent's own changes"
            )
            result.finished_at = time.time()
            return result

        branch_result = executor_root.git.create_branch(self.task.task_id)
        if not branch_result.ok:
            self.logger.error(f"Failed to create git branch: {branch_result.error}")
            result.block_reason = f"could not create git branch: {branch_result.error}"
            result.finished_at = time.time()
            return result
        result.branch_name = branch_result.output
        self.logger.info(f"Working on branch {result.branch_name}")

        plan = self._run_planner_phase(executor_root)
        if plan is None:
            result.block_reason = "Planner failed to produce a plan"
            result.finished_at = time.time()
            return result

        instruction = build_initial_instruction(self.task.description, plan)
        state = State.IMPLEMENT
        iteration_n = 0
        step_n = 0
        strategy_changed_this_iteration = False
        record: IterationRecord | None = None
        MAX_STEPS_PER_ITERATION = 12  # safety valve: a single iteration should never need more state
        steps_this_iteration = 0
        executor: ToolExecutor | None = None
        iter_logger = None

        while state not in TERMINAL_STATES:
            # A new "iteration" (spec sections 10-12) begins each time we
            # (re)enter IMPLEMENT or IMPLEMENT_FIX — i.e. one full trip
            # through implement -> verify -> [fail -> fix]. Intermediate
            # bookkeeping states (STATIC_TEST, BUILD, START_APP,
            # BROWSER_TEST, ANALYZE_FAILURE, ...) are STEPS within an
            # iteration, sharing one ToolExecutor (and therefore one
            # ProcessManager) so a dev server started in START_APP is
            # still alive and tracked when BROWSER_TEST runs a few steps
            # later — cleanup only happens at iteration boundaries, not
            # after every step, or the dev server would be killed the
            # instant the step that started it finished.
            if state in (State.IMPLEMENT, State.IMPLEMENT_FIX) and steps_this_iteration != 0:
                steps_this_iteration = 0
                if executor is not None:
                    executor.cleanup()

            if steps_this_iteration == 0:
                iteration_n += 1
                if iteration_n > self.config.agent.max_iterations:
                    self.logger.warning(f"max_iterations ({self.config.agent.max_iterations}) exceeded")
                    result.block_reason = f"exceeded max_iterations ({self.config.agent.max_iterations})"
                    state = State.BLOCKED
                    break
                record = IterationRecord(iteration=iteration_n)
                self.logger.info(f"=== Iteration {iteration_n} starting (state={state.value}) ===")
                iter_logger = self.logger.for_iteration(self._iteration_dir(iteration_n))
                executor = ToolExecutor(self.config, iter_logger)

            steps_this_iteration += 1
            step_n += 1
            if steps_this_iteration > MAX_STEPS_PER_ITERATION:
                self.logger.error(f"iteration {iteration_n} exceeded {MAX_STEPS_PER_ITERATION} internal steps — logic error, aborting")
                result.block_reason = "internal error: too many state-machine steps within one iteration"
                state = State.BLOCKED
                break

            self.logger.info(f"--- step {step_n}: state={state.value} ---")

            try:
                state, record, instruction = self._run_one_iteration(
                    state, executor, iter_logger, instruction, record, strategy_changed_this_iteration
                )
                strategy_changed_this_iteration = False
            except (AgentResponseError, LLMError) as e:
                self.logger.error(f"Agent/LLM failure in iteration {iteration_n}: {e}")
                record.error = str(e)
                result.iterations.append(record)
                result.block_reason = f"agent/LLM failure: {e}"
                state = State.BLOCKED
                break
            except Exception as e:  # noqa: BLE001 — must never crash the overnight run silently
                tb = traceback.format_exc()
                self.logger.error(f"Unexpected error in iteration {iteration_n}: {e}\n{tb}")
                record.error = f"{e}\n{tb}"
                result.iterations.append(record)
                result.block_reason = f"unexpected error: {e}"
                state = State.BLOCKED
                break

            # Track this iteration's record in the results list (idempotent
            # across the several steps that share one record — only append
            # the first time this particular record object is seen).
            if not result.iterations or result.iterations[-1] is not record:
                result.iterations.append(record)

            if self.failure_memory.should_force_strategy_change():
                if self.failure_memory.exceeded_max_strategy_changes(self.config.agent.max_strategy_changes):
                    self.logger.warning("Exceeded max_strategy_changes with repeated failures — BLOCKED")
                    result.block_reason = "repeated identical failures across multiple strategy changes"
                    state = State.BLOCKED
                    break
                self.failure_memory.note_strategy_change()
                strategy_changed_this_iteration = True

        if executor is not None:
            executor.cleanup()  # final safety net — no dev server survives past the run

        result.final_status = "READY_FOR_HUMAN_REVIEW" if state == State.PASS else "BLOCKED"
        result.finished_at = time.time()
        return result

    def _run_planner_phase(self, executor: ToolExecutor) -> dict | None:
        try:
            return run_planner(self.llm, self.logger, executor, self.task)
        except (AgentResponseError, LLMError) as e:
            self.logger.error(f"Planner failed: {e}")
            return None

    def _run_one_iteration(
        self,
        state: State,
        executor: ToolExecutor,
        iter_logger,
        instruction: str,
        record: IterationRecord,
        forced_strategy_change: bool,
    ) -> tuple[State, IterationRecord, str]:
        if state == State.IMPLEMENT or state == State.IMPLEMENT_FIX:
            if forced_strategy_change:
                instruction += "\n\n" + self.failure_memory.summary_for_prompt()
            coder_result = run_coder(self.llm, self.logger, executor, instruction)
            record.coder_summary = coder_result
            state = next_state(state if state == State.IMPLEMENT else State.IMPLEMENT_FIX, success=True)
            return state, record, instruction

        if state == State.STATIC_TEST:
            tester_report = run_tester(self.logger, executor.fs, executor.shell)
            record.tester_report = tester_report
            iter_logger.write_json("test-results.json", tester_report.to_dict())
            static_checks_ok = all(
                c.status != "FAIL" for c in tester_report.checks if c.name != "build"
            )
            state = next_state(State.STATIC_TEST, success=static_checks_ok)
            if not static_checks_ok:
                return state, record, self._build_fix_instruction(record, tester_report)
            return state, record, instruction

        if state == State.BUILD:
            # build already ran as part of the Tester phase; re-check its result
            tester_report = record.tester_report
            build_check = next((c for c in (tester_report.checks if tester_report else []) if c.name == "build"), None)
            build_ok = build_check is not None and build_check.status == "PASS"
            state = next_state(State.BUILD, success=build_ok)
            if not build_ok:
                return state, record, self._build_fix_instruction(record, tester_report)
            return state, record, instruction

        if state == State.START_APP:
            start_result = executor.processes.start_process(
                name="dev_server", command=self._dev_command(executor), cwd="."
            )
            if not start_result.get("ok"):
                state = next_state(State.START_APP, success=False)
                return state, record, self._build_generic_fix_instruction(
                    record, f"Failed to start dev server: {start_result.get('error')}"
                )
            ready = executor.processes.wait_until_ready(self.dev_server_url, timeout_s=60)
            state = next_state(State.START_APP, success=bool(ready.get("ok")))
            if not ready.get("ok"):
                logs = executor.processes.tail_logs("dev_server")
                return state, record, self._build_generic_fix_instruction(
                    record, f"Dev server did not become ready: {ready.get('error')}\nLogs: {logs}"
                )
            return state, record, instruction

        if state == State.BROWSER_TEST:
            viewports = self.config.browser.viewports
            results = executor.browser.check_all_viewports(
                url=self.dev_server_url, viewports=viewports, screenshot_dir=iter_logger.dir / "screenshots"
            )
            record.browser_results = results
            browser_ok = all(
                r.ok and not r.console_errors and not r.page_errors and not r.horizontal_overflow
                for r in results
            )
            state = next_state(State.BROWSER_TEST, success=browser_ok)
            if not browser_ok:
                return state, record, self._build_browser_fix_instruction(record, results)
            return state, record, instruction

        if state == State.VISUAL_REVIEW:
            try:
                issues = summarize_visual_issues(self.llm, self.logger, record.browser_results)
                record.visual_issues = issues
                state = next_state(State.VISUAL_REVIEW, success=len(issues) == 0)
                if issues:
                    return state, record, self._build_generic_fix_instruction(
                        record, "Visual review found issues:\n" + "\n".join(f"- {i}" for i in issues)
                    )
            except VisionNotSupportedError as e:
                self.logger.warning(f"Skipping visual review: {e}")
                state = next_state(State.VISUAL_REVIEW, success=True)
            return state, record, instruction

        if state == State.REVIEW:
            git_diff = executor.git.diff()
            verdict = run_reviewer(
                self.llm, self.logger, executor, self.task,
                coder_summary=record.coder_summary,
                tester_report=record.tester_report or TesterReport(overall_status="FAIL"),
                browser_results=record.browser_results,
                git_diff_text=git_diff.output,
                repeated_failure_note=self.failure_memory.summary_for_prompt(),
            )
            record.review_verdict = verdict
            iter_logger.write_json("review.json", verdict.to_dict())
            if verdict.status == "BLOCKED":
                state = State.BLOCKED
                return state, record, instruction
            state = next_state(State.REVIEW, success=(verdict.status == "PASS"))
            if verdict.status != "PASS":
                return state, record, self._build_review_fix_instruction(record, verdict)
            return state, record, instruction

        if state == State.ANALYZE_FAILURE:
            # Record the failure signature here (once) for repeated-failure
            # detection — CREATE_FIX_PLAN below is a separate, deliberately
            # trivial pass-through state and must NOT record again, or a
            # single real failure would be double-counted and could trip
            # should_force_strategy_change() after only one genuine failure.
            failed_names, error_texts = self._extract_failure_signature(record)
            self.failure_memory.record(record.iteration, failed_names, error_texts)
            state = next_state(state, success=True)
            return state, record, instruction

        if state == State.CREATE_FIX_PLAN:
            # The fix instruction was already built by the failing stage
            # above (e.g. _build_fix_instruction); this state exists as an
            # explicit point in the state machine per spec section 10 but
            # needs no additional work in this MVP.
            state = next_state(state, success=True)
            return state, record, instruction

        raise AssertionError(f"unhandled state in orchestrator: {state}")

    def _dev_command(self, executor: ToolExecutor) -> list[str]:
        scripts_res = executor.fs.read_file("package.json")
        pm = "npm"
        if executor.fs.read_file("pnpm-lock.yaml").ok:
            pm = "pnpm"
        elif executor.fs.read_file("yarn.lock").ok:
            pm = "yarn"
        return [pm, "run", "dev"]

    def _extract_failure_signature(self, record: IterationRecord) -> tuple[list[str], list[str]]:
        names, errors = [], []
        if record.tester_report:
            for c in record.tester_report.failed_checks():
                names.append(c.name)
                errors.append(c.error)
        for r in record.browser_results:
            if not r.ok or r.console_errors or r.page_errors or r.horizontal_overflow:
                names.append(f"browser:{r.viewport_name}")
                errors.append(r.overflow_detail + " ".join(c.text for c in r.console_errors))
        if record.review_verdict and record.review_verdict.status != "PASS":
            names.append("review")
            errors.extend(record.review_verdict.required_fixes)
        return names, errors

    def _build_fix_instruction(self, record: IterationRecord, tester_report: TesterReport | None) -> str:
        fixes = [f"{c.name}: {c.error[:500]}" for c in (tester_report.failed_checks() if tester_report else [])]
        return build_fix_instruction(
            previous_summary=str(record.coder_summary.get("summary", "")),
            required_fixes=fixes,
            failure_context=self.failure_memory.summary_for_prompt(),
        )

    def _build_generic_fix_instruction(self, record: IterationRecord, detail: str) -> str:
        return build_fix_instruction(
            previous_summary=str(record.coder_summary.get("summary", "")),
            required_fixes=[detail],
            failure_context=self.failure_memory.summary_for_prompt(),
        )

    def _build_browser_fix_instruction(self, record: IterationRecord, results: list[ViewportCheckResult]) -> str:
        fixes = []
        for r in results:
            if r.horizontal_overflow:
                fixes.append(f"Horizontal overflow at {r.viewport_name} ({r.width}x{r.height}): {r.overflow_detail}")
            for c in r.console_errors:
                fixes.append(f"Console error at {r.viewport_name}: {c.text}")
            for e in r.page_errors:
                fixes.append(f"Page error at {r.viewport_name}: {e}")
        return build_fix_instruction(
            previous_summary=str(record.coder_summary.get("summary", "")),
            required_fixes=fixes,
            failure_context=self.failure_memory.summary_for_prompt(),
        )

    def _build_review_fix_instruction(self, record: IterationRecord, verdict: ReviewVerdict) -> str:
        return build_fix_instruction(
            previous_summary=str(record.coder_summary.get("summary", "")),
            required_fixes=verdict.required_fixes,
            failure_context=self.failure_memory.summary_for_prompt(),
        )
