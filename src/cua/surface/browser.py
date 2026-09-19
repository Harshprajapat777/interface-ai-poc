"""A Surface backed by Playwright.

Frames are handled explicitly rather than ignored: the target app puts every
useful control inside a frameset, which is exactly the legacy shape this
project exists for. Each frame is scanned separately and its controls are
tagged with the frame name, so a recorded step knows which frame it belongs to.
"""

from pathlib import Path
from types import TracebackType
from typing import cast

from playwright.sync_api import Frame, Locator, Page, Playwright, sync_playwright

from cua.surface.base import Control, Observation
from cua.surface.snapshot import SCAN_JS, ScanResult, build_controls

# How long to wait for a control or a page load before giving up.
DEFAULT_TIMEOUT_MS = 10_000

# How long to let the network go quiet after an action before snapshotting.
SETTLE_TIMEOUT_MS = 3_000


class BrowserSurface:
    """Drives a Chromium page as a Surface."""

    def __init__(self, headless: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        self._playwright: Playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._context.set_default_timeout(timeout_ms)
        self._page: Page = self._context.new_page()

    def open(self, url: str) -> None:
        """Navigates to a starting point."""
        self._page.goto(url, wait_until="load")

    def observe(self) -> Observation:
        """Scans every frame and returns one combined snapshot."""
        controls: list[Control] = []
        texts: list[str] = []
        for frame in self._page.frames:
            frame_name = self._frame_name(frame)
            scanned = self._scan(frame, frame_name)
            if scanned is None:
                continue
            controls.extend(build_controls(scanned["controls"], frame_name))
            texts.extend(scanned["texts"])
        return Observation(
            url=self._page.url,
            title=self._page.title(),
            controls=controls,
            texts=texts,
        )

    def click(self, control: Control) -> None:
        """Clicks a control from the latest observation."""
        self._locate(control).click()
        self._settle()

    def fill(self, control: Control, text: str) -> None:
        """Types text into a control from the latest observation."""
        self._locate(control).fill(text)

    def screenshot(self, path: Path) -> None:
        """Saves a picture of the current screen."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(path), full_page=True)

    def html(self) -> str:
        """Returns the current page markup, for failure evidence."""
        return self._page.content()

    def close(self) -> None:
        """Shuts the browser down."""
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def __enter__(self) -> "BrowserSurface":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _frame_name(self, frame: Frame) -> str:
        """Names a frame, using an empty string for the top-level document."""
        return "" if frame is self._page.main_frame else (frame.name or frame.url)

    def _scan(self, frame: Frame, frame_name: str) -> ScanResult | None:
        """Runs the scan script in one frame, skipping frames that are not ready."""
        prefix = frame_name or "main"
        try:
            result = frame.evaluate(SCAN_JS, prefix)
        except Exception:
            # A frame can be mid-navigation or detached. It will be there next snapshot.
            return None
        return cast(ScanResult, result) if isinstance(result, dict) else None

    def _locate(self, control: Control) -> Locator:
        """Resolves a control back to the element the last snapshot tagged."""
        frame = self._frame_for(control)
        return frame.locator(f'[data-cua-ref="{control.ref}"]')

    def _frame_for(self, control: Control) -> Frame:
        """Finds the frame a control was seen in."""
        if not control.frame:
            return self._page.main_frame
        for frame in self._page.frames:
            if self._frame_name(frame) == control.frame:
                return frame
        raise LookupError(f"Frame {control.frame!r} is no longer on the page")

    def _settle(self) -> None:
        """Waits for whatever the last action started to finish loading.

        Waiting on the page's load state alone is not enough here: the page is
        a frameset that was already loaded, so a navigation happening inside a
        frame leaves the page state untouched and a snapshot taken immediately
        afterwards can catch the previous screen. Waiting for the network to go
        quiet covers the frame case too.
        """
        try:
            self._page.wait_for_load_state("load")
            self._page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            # A slow app is the checkpoint's problem to report, not this one's.
            pass
