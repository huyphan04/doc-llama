"""
Visual feedback — spec section 14.

Sends each viewport's screenshot to the configured vision model and asks
it to flag only what's reliably determinable from a static screenshot:
overflow, clipping, broken layout, spacing, unreadable text, overlap,
image sizing, nav problems, obvious regressions. We explicitly do NOT ask
the vision model to judge things it can't reliably assess from a single
screenshot (e.g. "is this accessible", "is this on-brand") — spec section
14 says not to ask for judgments that can't be reliably determined this way.

If no vision_model is configured, VisionNotSupportedError propagates up
so the orchestrator can skip this stage rather than silently treating
"no vision model" as "no issues found".
"""
from __future__ import annotations

import json
import re

from agent.llm.base import LLMClient, VisionNotSupportedError
from agent.logging_setup import AgentLogger
from tools.browser import ViewportCheckResult

VISION_PROMPT = """You are reviewing a screenshot of a web page rendered at a specific viewport size.

Look ONLY for issues that are reliably visible in a static screenshot:
- horizontal overflow / content cut off at the edge
- clipped or truncated content
- obviously broken layout (elements stacked wrong, misaligned)
- text that is unreadable (too small, low contrast, overlapping other text)
- overlapping elements that shouldn't overlap
- images that are the wrong size, stretched, or broken
- broken-looking navigation (menu items overlapping, cut off)
- obvious visual regressions (large blank areas where content should be, broken CSS)

Do NOT comment on subjective design taste, branding, or anything you cannot be confident about
from this single screenshot alone.

Respond with ONLY a JSON object:
{"issues": ["specific issue description", ...]}
If there are no issues, respond with {"issues": []}
"""


def _parse_issues(text: str) -> list[str]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return [f"vision model returned unparseable response (treated as needing human look): {text[:200]}"]
    try:
        data = json.loads(match.group(0))
        return list(data.get("issues", []))
    except json.JSONDecodeError:
        return [f"vision model returned invalid JSON (treated as needing human look): {text[:200]}"]


def summarize_visual_issues(
    llm: LLMClient, logger: AgentLogger, browser_results: list[ViewportCheckResult]
) -> list[str]:
    all_issues: list[str] = []
    for r in browser_results:
        if not r.screenshot_path:
            continue
        try:
            result = llm.vision(VISION_PROMPT, r.screenshot_path)
        except VisionNotSupportedError:
            raise  # propagate — caller decides how to handle "no vision model"
        issues = _parse_issues(result.message.content)
        if issues:
            logger.info(f"Vision review found {len(issues)} issue(s) at {r.viewport_name}", issues=issues)
        all_issues.extend(f"[{r.viewport_name}] {issue}" for issue in issues)
    return all_issues
