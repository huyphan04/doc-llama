#!/usr/bin/env python3
"""
CLI — spec section 23.

    ai-agent run tasks/landing-page.md
    ai-agent status
    ai-agent logs [task-id]
    ai-agent report <task-id>
    ai-agent stop <task-id>
    ai-agent resume <task-id>
    ai-agent doctor

Kept intentionally thin: this module parses arguments and prints output;
all real logic lives in agent/ and cli/doctor.py, cli/run_state.py so it
stays testable without invoking a subprocess.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from agent.config import ConfigError, load as load_config
from agent.llm import build_llm_client
from agent.logging_setup import AgentLogger
from agent.orchestrator import Orchestrator
from agent.report_generator import write_report
from agent.task_parser import TaskParseError, parse_task_file
from cli.doctor import format_doctor_report, run_doctor
from cli.run_state import RunState, list_all_states, read_state, write_state


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    try:
        task = parse_task_file(args.task_file)
    except TaskParseError as e:
        print(f"Task parse error: {e}", file=sys.stderr)
        return 1

    logs_dir = config.resolve_path(config.logging.dir)
    logger = AgentLogger(
        log_dir=logs_dir / task.task_id, level=config.logging.level, json_log=config.logging.json_log
    )

    state = RunState(task_id=task.task_id, status="RUNNING", started_at=__import__("time").time(), updated_at=0, pid=os.getpid())
    write_state(logs_dir, state)

    llm = build_llm_client(config.llm)
    ok, msg = llm.health_check()
    if not ok:
        print(f"LLM health check failed: {msg}", file=sys.stderr)
        print("Run 'ai-agent doctor' for a full environment check.", file=sys.stderr)
        state.status = "ERROR"
        write_state(logs_dir, state)
        return 1

    logger.info(f"Starting task '{task.task_id}': {task.title}")
    orchestrator = Orchestrator(config, llm, logger, task)

    def _handle_sigterm(signum, frame):
        logger.warning("Received termination signal — stopping run")
        state.status = "STOPPED"
        write_state(logs_dir, state)
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    result = orchestrator.run()

    state.status = result.final_status
    state.branch_name = result.branch_name
    state.current_iteration = len(result.iterations)
    write_state(logs_dir, state)

    reports_dir = config.resolve_path(config.reports.dir)
    report_path = write_report(task, result, reports_dir)

    print(f"\n{'=' * 60}")
    print(f"STATUS: {result.final_status}")
    print(f"Branch: {result.branch_name}")
    print(f"Iterations: {len(result.iterations)}")
    print(f"Report: {report_path}")
    print(f"{'=' * 60}\n")

    if result.final_status == "BLOCKED":
        print(f"Blocked reason: {result.block_reason}")
        return 2
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logs_dir = config.resolve_path(config.logging.dir)
    states = list_all_states(logs_dir)
    if not states:
        print("No tasks have been run yet.")
        return 0
    for s in states:
        print(f"{s.task_id:30s} {s.status:25s} iteration={s.current_iteration:3d} branch={s.branch_name}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logs_dir = config.resolve_path(config.logging.dir)
    if args.task_id:
        log_file = logs_dir / args.task_id / "agent.log"
        if not log_file.exists():
            print(f"No logs found for task '{args.task_id}'", file=sys.stderr)
            return 1
        print(log_file.read_text(encoding="utf-8"))
    else:
        for child in sorted(logs_dir.iterdir()) if logs_dir.exists() else []:
            log_file = child / "agent.log"
            if log_file.exists():
                print(f"--- {child.name} ---")
                print(log_file.read_text(encoding="utf-8")[-2000:])
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    reports_dir = config.resolve_path(config.reports.dir)
    report_file = reports_dir / f"{args.task_id}.md"
    if not report_file.exists():
        print(f"No report found for task '{args.task_id}'", file=sys.stderr)
        return 1
    print(report_file.read_text(encoding="utf-8"))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logs_dir = config.resolve_path(config.logging.dir)
    state = read_state(logs_dir, args.task_id)
    if not state:
        print(f"No known run for task '{args.task_id}'", file=sys.stderr)
        return 1
    if state.status != "RUNNING":
        print(f"Task '{args.task_id}' is not running (status={state.status})")
        return 0
    try:
        os.kill(state.pid, signal.SIGTERM)
        print(f"Sent stop signal to task '{args.task_id}' (pid {state.pid})")
        return 0
    except ProcessLookupError:
        print(f"Process {state.pid} not found — marking as stopped", file=sys.stderr)
        state.status = "STOPPED"
        write_state(logs_dir, state)
        return 1


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logs_dir = config.resolve_path(config.logging.dir)
    state = read_state(logs_dir, args.task_id)
    if not state:
        print(f"No known run for task '{args.task_id}' — nothing to resume", file=sys.stderr)
        return 1
    print(
        f"Last known state for '{args.task_id}': status={state.status}, "
        f"iteration={state.current_iteration}, branch={state.branch_name}\n"
    )
    print(
        "NOTE: mid-iteration resume is not implemented in this version — this is a "
        "known limitation (see README). Re-running the task will start a fresh "
        "iteration sequence on a new branch, reusing whatever work already exists "
        "in the workspace on disk."
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    checks = run_doctor(config)
    print(format_doctor_report(checks))
    return 0 if all(c.ok for c in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-agent", description="Autonomous Coding Agent CLI")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a task from a markdown file")
    p_run.add_argument("task_file")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="show status of all known tasks")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="show logs for a task (or all tasks)")
    p_logs.add_argument("task_id", nargs="?")
    p_logs.set_defaults(func=cmd_logs)

    p_report = sub.add_parser("report", help="print the final report for a task")
    p_report.add_argument("task_id")
    p_report.set_defaults(func=cmd_report)

    p_stop = sub.add_parser("stop", help="stop a running task")
    p_stop.add_argument("task_id")
    p_stop.set_defaults(func=cmd_stop)

    p_resume = sub.add_parser("resume", help="show last state of a task (see limitations)")
    p_resume.add_argument("task_id")
    p_resume.set_defaults(func=cmd_resume)

    p_doctor = sub.add_parser("doctor", help="check environment health")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
