# Mobile Device Testing — Implementation Plan

## Overview

Add mobile device emulation so QAPAL can test apps on phones and tablets using Playwright's built-in device presets (e.g., "iPhone 12", "Pixel 5", "iPad Pro"). Playwright handles touch, viewport, user-agent, and device-scale-factor transparently via `browser.new_context(**devices[device_name])`.

No new actions or assertions needed — Playwright maps `click` to `tap` automatically on touch-enabled contexts.

---

## Step 1: Modify `_build_context()` to accept device kwargs

**File:** `crawler.py` (lines 382–416)

Add a `device_kwargs: Optional[dict] = None` parameter and apply it to all three `browser.new_context()` call sites:

```python
async def _build_context(browser, db, url, credentials=None, device_kwargs=None):
    extra = device_kwargs or {}
    domain = urlparse(url).netloc
    session = db.get_session(domain)

    if session and session.get("storage_state"):
        try:
            return await browser.new_context(storage_state=session["storage_state"], **extra)
        except Exception:
            pass

    if credentials:
        ctx = await browser.new_context(**extra)
        # ... existing login flow unchanged ...

    return await browser.new_context(**extra)
```

This is the core plumbing — everything else just passes `device_kwargs` to this function.

---

## Step 2: Update `Crawler` class

**File:** `crawler.py` (lines 690–730)

### Constructor
Add `device` and `viewport` params:

```python
def __init__(self, db, headless=None, device=None, viewport=None, credentials=None, state_graph=None):
    self._device = device or os.getenv("QAPAL_DEVICE", None)
    self._viewport = viewport  # tuple (width, height) or None
```

### `start()` method
After `self._pw = await async_playwright().start()`, resolve device kwargs:

```python
self._device_kwargs = {}
if self._device:
    try:
        self._device_kwargs = dict(self._pw.devices[self._device])
    except KeyError:
        available = ", ".join(sorted(self._pw.devices.keys())[:10])
        raise ValueError(f"Unknown device '{self._device}'. Examples: {available}...")
if self._viewport:
    self._device_kwargs["viewport"] = {"width": self._viewport[0], "height": self._viewport[1]}
```

### All `_build_context()` calls
Pass `device_kwargs=self._device_kwargs` everywhere the crawler creates contexts.

---

## Step 3: Update `Executor` class

**File:** `executor.py` (lines 1285–1330)

### Constructor
Add `device` and `viewport` params:

```python
def __init__(self, db, headless=None, device=None, viewport=None, ai_client=None, credentials=None, state_graph=None):
    self._device = device or os.getenv("QAPAL_DEVICE", None)
    self._viewport = viewport
```

### `start()` method
Same device resolution as Crawler:

```python
self._device_kwargs = {}
if self._device:
    try:
        self._device_kwargs = dict(self._pw.devices[self._device])
    except KeyError:
        available = ", ".join(sorted(self._pw.devices.keys())[:10])
        raise ValueError(f"Unknown device '{self._device}'. Examples: {available}...")
if self._viewport:
    self._device_kwargs["viewport"] = {"width": self._viewport[0], "height": self._viewport[1]}
```

### Pass to `_build_context()`
Pass `device_kwargs=self._device_kwargs` to all `_build_context()` calls in the executor.

### Pass to internal Crawler
The executor creates a Crawler internally — pass `device` and `viewport` through:

```python
self._crawler = Crawler(self._db, headless=self._headless, device=self._device, viewport=self._viewport, credentials=self._credentials)
```

---

## Step 4: Add `--device` and `--viewport` CLI flags

**File:** `main.py` (lines 1104–1197)

Add to all commands that use a browser (`crawl`, `run`, `prd-run`, `semantic`, `graph-crawl`, `explore`, `ux-audit`):

```python
p.add_argument("--device", "-d", default=None,
               help="Playwright device preset (e.g. 'iPhone 12', 'Pixel 5')")
p.add_argument("--viewport", nargs=2, type=int, metavar=("W", "H"),
               help="Custom viewport width and height (overrides device default)")
```

---

## Step 5: Wire CLI args through command handlers

**File:** `main.py` (command handler functions)

In each handler (`cmd_crawl`, `cmd_run`, `cmd_prd_run`, `cmd_semantic`, `cmd_graph_crawl`, `cmd_explore`, `cmd_ux_audit`):

```python
device = getattr(args, "device", None)
viewport = tuple(args.viewport) if getattr(args, "viewport", None) else None

# Pass to Crawler / Executor constructors
async with Crawler(db, headless=headless_mode, device=device, viewport=viewport, ...) as crawler:
    ...
```

---

## Step 6: Embed device info in plan JSON `_meta`

**File:** `generator.py` / `planner.py`

When a device is active during planning, record it in `_meta`:

```json
{
  "_meta": {
    "device": "iPhone 12",
    "viewport": {"width": 390, "height": 844},
    ...
  }
}
```

Informational only — the device is selected at runtime, not baked into execution.

---

## Step 7: Add unit tests

**File:** `tests/test.py` (or new `tests/test_mobile.py`)

- Test `_build_context()` passes device kwargs to `new_context()` (mocked Playwright)
- Test Crawler/Executor constructors store device params
- Test device resolution in `start()` with known device name
- Test error handling for unknown device names
- Test `QAPAL_DEVICE` env var fallback
- Test CLI argument parsing includes `--device` and `--viewport`

---

## What Does NOT Change

| Component | Why |
|-----------|-----|
| `actions.py` | All 19 actions work on mobile (Playwright handles touch) |
| `assertions.py` | All assertions are device-agnostic |
| `locator_db.py` | Same schema, same key formula |
| Locator resolution | Selector strategies work on mobile |
| Browser launch | Device config is context-level, not launch-level |
| Plan JSON format | Same structure, optional `_meta` addition only |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QAPAL_DEVICE` | `None` (desktop) | Playwright device preset name |
| `QAPAL_VIEWPORT_WIDTH` | `None` | Custom viewport width (optional) |
| `QAPAL_VIEWPORT_HEIGHT` | `None` | Custom viewport height (optional) |

---

## Risks

1. **Locator differences**: Mobile layouts may hide/show different elements than desktop. Users should re-crawl per device.
2. **Unknown device names**: Fail fast with clear error + examples of valid names.
3. **Plan portability**: A plan generated from desktop-crawled locators may fail on mobile if elements differ. Document this.

---

## Implementation Order

1. **Steps 1–3** — Core plumbing (`_build_context`, Crawler, Executor)
2. **Steps 4–5** — CLI flags + wiring
3. **Step 6** — Plan metadata
4. **Step 7** — Tests
