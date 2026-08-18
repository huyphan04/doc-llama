"""
These tests run REAL Chromium via Playwright (no mocking) against local
file:// HTML fixtures. They confirm the overflow-detection heuristic,
console error capture, and screenshot pipeline actually work — not just
that the code is plausible. If Playwright's chromium binary isn't
installed in the environment running these tests, they will fail loudly
rather than silently pass; that's intentional (spec section 32: don't
hide errors).
"""
from pathlib import Path

import pytest

from tools.browser import BrowserTools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def make_fixtures():
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "overflow.html").write_text(
        """<!DOCTYPE html><html><head><style>
        .wide { width: 2000px; height: 50px; background: red; }
        </style></head><body>
        <h1>Test</h1><div class="wide">too wide</div>
        <script>console.error("intentional test error");</script>
        </body></html>"""
    )
    (FIXTURES / "normal.html").write_text(
        "<!DOCTYPE html><html><body><h1>Normal</h1><p>fine</p></body></html>"
    )
    (FIXTURES / "broken.html").write_text(
        "<!DOCTYPE html><html><body><script>throw new Error('boom');</script></body></html>"
    )
    yield


@pytest.fixture
def browser_tools():
    return BrowserTools(headless=True, navigation_timeout_ms=15000, wait_for_network_idle_ms=1000)


def test_detects_horizontal_overflow(browser_tools, tmp_path):
    r = browser_tools.check_viewport(
        url=f"file://{FIXTURES / 'overflow.html'}", viewport_name="mobile", width=375, height=812,
        screenshot_dir=tmp_path,
    )
    assert r.ok
    assert r.horizontal_overflow is True
    assert "scrollWidth" in r.overflow_detail
    assert Path(r.screenshot_path).exists()


def test_normal_page_no_overflow(browser_tools, tmp_path):
    r = browser_tools.check_viewport(
        url=f"file://{FIXTURES / 'normal.html'}", viewport_name="desktop", width=1440, height=900,
        screenshot_dir=tmp_path,
    )
    assert r.ok
    assert r.horizontal_overflow is False


def test_captures_console_error(browser_tools, tmp_path):
    r = browser_tools.check_viewport(
        url=f"file://{FIXTURES / 'overflow.html'}", viewport_name="mobile", width=375, height=812,
        screenshot_dir=tmp_path,
    )
    assert any("intentional test error" in c.text for c in r.console_errors)


def test_captures_page_error(browser_tools, tmp_path):
    r = browser_tools.check_viewport(
        url=f"file://{FIXTURES / 'broken.html'}", viewport_name="desktop", width=1440, height=900,
        screenshot_dir=tmp_path,
    )
    assert r.ok  # page still loads, but records the JS exception
    assert any("boom" in e for e in r.page_errors)


def test_check_all_viewports(browser_tools, tmp_path):
    from agent.config import Viewport

    viewports = [Viewport(name="mobile", width=375, height=812), Viewport(name="desktop", width=1440, height=900)]
    results = browser_tools.check_all_viewports(
        url=f"file://{FIXTURES / 'normal.html'}", viewports=viewports, screenshot_dir=tmp_path
    )
    assert len(results) == 2
    assert all(r.ok for r in results)
    assert {r.viewport_name for r in results} == {"mobile", "desktop"}


def test_handles_failed_network_request(browser_tools, tmp_path):
    """Regression test: Playwright's request.failure is a plain string in
    this version, not a dict — a page that references a nonexistent
    resource must not crash the check_viewport call."""
    (FIXTURES / "broken_request.html").write_text(
        '<!DOCTYPE html><html><body><h1>ok</h1>'
        '<img src="http://127.0.0.1:1/does-not-exist.png">'
        "</body></html>"
    )
    r = browser_tools.check_viewport(
        url=f"file://{FIXTURES / 'broken_request.html'}", viewport_name="desktop", width=1440, height=900,
        screenshot_dir=tmp_path,
    )
    assert r.ok
    assert r.error == ""
