"""
Browser Tester tools — spec sections 4, 13, 15.

Wraps Playwright sync API. Every check() call captures console errors,
network failures, and a screenshot together, because the Reviewer needs
all three correlated to the same page state (spec section 4: "Reviewer
must not just trust Coder").

One browser context is reused across viewport resizes within a single
`check_all_viewports` call for efficiency, but a fresh context per full
browser_test run avoids state leaking between iterations (cookies,
localStorage) that could mask a real bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


@dataclass
class ConsoleMessage:
    type: str
    text: str


@dataclass
class NetworkFailure:
    url: str
    method: str
    status: int | None
    failure_text: str


@dataclass
class ViewportCheckResult:
    viewport_name: str
    width: int
    height: int
    ok: bool
    screenshot_path: str = ""
    console_errors: list[ConsoleMessage] = field(default_factory=list)
    network_failures: list[NetworkFailure] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    horizontal_overflow: bool = False
    overflow_detail: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "viewport": self.viewport_name,
            "width": self.width,
            "height": self.height,
            "ok": self.ok,
            "screenshot_path": self.screenshot_path,
            "console_errors": [vars(c) for c in self.console_errors],
            "network_failures": [vars(n) for n in self.network_failures],
            "page_errors": self.page_errors,
            "horizontal_overflow": self.horizontal_overflow,
            "overflow_detail": self.overflow_detail,
            "error": self.error,
        }


class BrowserTools:
    def __init__(self, headless: bool = True, navigation_timeout_ms: int = 30000, wait_for_network_idle_ms: int = 3000):
        self.headless = headless
        self.navigation_timeout_ms = navigation_timeout_ms
        self.wait_for_network_idle_ms = wait_for_network_idle_ms

    def check_viewport(
        self,
        url: str,
        viewport_name: str,
        width: int,
        height: int,
        screenshot_dir: Path,
    ) -> ViewportCheckResult:
        result = ViewportCheckResult(viewport_name=viewport_name, width=width, height=height, ok=False)
        screenshot_dir = Path(screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"{viewport_name}.png"

        console_errors: list[ConsoleMessage] = []
        network_failures: list[NetworkFailure] = []
        page_errors: list[str] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    page.set_default_navigation_timeout(self.navigation_timeout_ms)

                    page.on(
                        "console",
                        lambda msg: console_errors.append(ConsoleMessage(type=msg.type, text=msg.text))
                        if msg.type == "error" else None,
                    )
                    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

                    def _on_response(resp):
                        if resp.status >= 400:
                            network_failures.append(
                                NetworkFailure(url=resp.url, method=resp.request.method, status=resp.status, failure_text="")
                            )

                    page.on("response", _on_response)
                    page.on(
                        "requestfailed",
                        lambda req: network_failures.append(
                            NetworkFailure(
                                url=req.url, method=req.method, status=None,
                                failure_text=req.failure or "",
                            )
                        ),
                    )

                    page.goto(url, wait_until="load")
                    try:
                        page.wait_for_load_state("networkidle", timeout=self.wait_for_network_idle_ms)
                    except PlaywrightError:
                        pass  # not fatal — some apps have long-polling that never idles

                    overflow_detail = ""
                    horizontal_overflow = False
                    try:
                        scroll_width = page.evaluate("document.documentElement.scrollWidth")
                        client_width = page.evaluate("document.documentElement.clientWidth")
                        if scroll_width > client_width + 1:  # +1 tolerance for subpixel rounding
                            horizontal_overflow = True
                            overflow_detail = f"scrollWidth={scroll_width} > clientWidth={client_width}"
                    except PlaywrightError as e:
                        overflow_detail = f"could not measure overflow: {e}"

                    page.screenshot(path=str(screenshot_path), full_page=True)

                    result.ok = True
                    result.screenshot_path = str(screenshot_path)
                    result.console_errors = console_errors
                    result.network_failures = network_failures
                    result.page_errors = page_errors
                    result.horizontal_overflow = horizontal_overflow
                    result.overflow_detail = overflow_detail
                finally:
                    browser.close()
        except PlaywrightError as e:
            result.error = f"playwright error: {e}"
        except Exception as e:  # noqa: BLE001 - must never crash the loop; report and let Reviewer see it
            result.error = f"unexpected browser error: {e}"

        return result

    def check_all_viewports(
        self, url: str, viewports: list[Any], screenshot_dir: Path
    ) -> list[ViewportCheckResult]:
        results = []
        for vp in viewports:
            results.append(
                self.check_viewport(
                    url=url, viewport_name=vp.name, width=vp.width, height=vp.height, screenshot_dir=screenshot_dir
                )
            )
        return results
