"""
crawler.py — QAPal Page Crawler
=================================
Triggered by the executor on every navigation, or run standalone to seed the DB.

Collection strategy:
  Primary:   a11y tree  (shadow DOM transparent)
  Secondary: DOM scan   (non-semantic elements with onclick/testid/tabindex)
  Tertiary:  iframes    (a11y inside accessible frames, skips cross-origin)

No MCP. Direct Playwright. No config files — all config from environment.

Install:
  pip install playwright tinydb python-dotenv
  playwright install chromium
"""

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from locator_db import LocatorDB, _make_id, _normalize_url, _compute_template_hash, _url_to_pattern
from _log import get_logger

log = get_logger("crawler")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Config ────────────────────────────────────────────────────────────

STALE_MINUTES = int(os.getenv("CRAWLER_STALE_MINUTES", "60"))


# ── Helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── A11y collection JS ───────────────────────────────────────────────

A11Y_JS = r"""
() => {
  var INTERACTIVE_ROLES = [
    'button','link','textbox','searchbox','combobox','listbox',
    'checkbox','radio','switch','slider','spinbutton','menuitem',
    'menuitemcheckbox','menuitemradio','option','tab','treeitem',
    'gridcell','columnheader','rowheader'
  ];

  function getRole(el) {
    var explicit = el.getAttribute('role');
    if (explicit) return explicit.toLowerCase();
    var tag  = el.tagName.toLowerCase();
    var type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'input') {
      return ({
        text:'textbox',email:'textbox',password:'textbox',
        search:'searchbox',tel:'textbox',url:'textbox',
        number:'spinbutton',range:'slider',
        checkbox:'checkbox',radio:'radio',
        submit:'button',reset:'button',button:'button',
      })[type] || 'textbox';
    }
    if (tag === 'select')   return 'combobox';
    if (tag === 'textarea') return 'textbox';
    return null;
  }

  function getName(el) {
    var tag = el.tagName.toLowerCase();
    var v;
    v = el.getAttribute('aria-label'); if (v) return v.trim();
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      return lb.split(/\s+/)
        .map(function(id){ return (document.getElementById(id)||{}).textContent||''; })
        .join(' ').trim();
    }
    if (el.labels && el.labels.length) return el.labels[0].textContent.trim();
    v = el.getAttribute('placeholder'); if (v) return v.trim();
    v = el.getAttribute('title');       if (v) return v.trim();
    if (tag === 'button' || tag === 'a') {
      v = el.textContent.trim(); if (v) return v;
    }
    return '';
  }

  function getTestId(el) {
    return el.getAttribute('data-testid')
        || el.getAttribute('data-cy')
        || el.getAttribute('data-qa')
        || el.getAttribute('data-test')
        || null;
  }

  function getContainer(el) {
    var LANDMARKS = ['dialog','main','nav','aside','section','form','header','footer','article'];
    var node = el.parentElement;
    for (var depth = 0; depth < 8 && node && node !== document.body; depth++) {
      var tag  = node.tagName.toLowerCase();
      var role = (node.getAttribute('role') || '').toLowerCase();
      var aria = node.getAttribute('aria-label') ||
                 (node.getAttribute('aria-labelledby') &&
                  (document.getElementById(node.getAttribute('aria-labelledby'))||{}).textContent) || '';
      var testid = node.getAttribute('data-testid') || node.getAttribute('data-test')
                || node.getAttribute('data-cy') || node.getAttribute('data-qa') || '';
      var stableId = node.id && !/^(:r|react-|ng-|v-|\d)/.test(node.id) ? node.id : '';
      var isLandmark = LANDMARKS.indexOf(tag) !== -1 || LANDMARKS.indexOf(role) !== -1;
      var isIdentifiable = isLandmark || aria || testid || stableId || (role && role !== 'none' && role !== 'presentation');
      if (isIdentifiable) {
        var selector = tag;
        if (testid) selector += '[data-testid="' + testid + '"]';
        else if (aria) selector += '[aria-label="' + aria.trim() + '"]';
        else if (stableId) selector += '#' + stableId;
        else if (role && !isLandmark) selector += '[role="' + role + '"]';
        var siblings = node.parentElement
          ? Array.from(node.parentElement.children).filter(function(c){ return c.tagName===node.tagName; })
          : [];
        if (siblings.length > 1) {
          var idx = siblings.indexOf(node) + 1;
          selector += ':nth-of-type(' + idx + ')';
        }
        return selector;
      }
      node = node.parentElement;
    }
    return '';
  }

  function getDomPath(el) {
    var parts = [], node = el, depth = 0;
    while (node && node !== document.body && depth < 4) {
      var tag = node.tagName.toLowerCase();
      var parent = node.parentElement;
      if (!parent) break;
      var siblings = Array.from(parent.children).filter(function(c){ return c.tagName===node.tagName; });
      var idx = siblings.indexOf(node) + 1;
      parts.unshift(siblings.length > 1 ? tag+':nth('+idx+')' : tag);
      node = parent; depth++;
    }
    return parts.join('>');
  }

  function looksLikeCode(str) {
    return str.indexOf('{') !== -1 || str.indexOf('function') !== -1
      || str.indexOf('<') !== -1 || str.length > 200;
  }

  var SKIP_TAGS = ['script','style','template','noscript','meta','link','head'];
  var results   = [];

  var nodes = document.querySelectorAll(
    'button,input,select,textarea,a[href],' +
    '[role],[aria-label],[tabindex],[data-testid],[data-cy],[data-qa]'
  );

  for (var i = 0; i < nodes.length; i++) {
    var el   = nodes[i];
    var role = getRole(el);
    if (!role) continue;
    if (INTERACTIVE_ROLES.indexOf(role) === -1) continue;
    if (SKIP_TAGS.indexOf(el.tagName.toLowerCase()) !== -1) continue;

    // We no longer skip hidden elements.
    // Capturing hidden menus, tabs, and modals ensures the AI knows they exist 
    // and can formulate a plan to trigger their visibility.
    var style = window.getComputedStyle(el);
    var isHidden = (style.display === 'none' || style.visibility === 'hidden'
        || style.opacity === '0' || el.hidden);
    
    var rect = el.getBoundingClientRect();
    var isZeroSize = (rect.width === 0 && rect.height === 0);

    var name = getName(el);
    if (looksLikeCode(name)) continue;

    var testid    = getTestId(el);
    var container = getContainer(el);
    var domPath   = getDomPath(el);
    var tag       = el.tagName.toLowerCase();
    var loc;

    // Capture elements with a semantic id even when they have no accessible name.
    // Skips auto-generated ids like "btn-42" or "react-1a2b".
    var elemId = el.id || '';
    var hasSemanticId = elemId && !/^(:r|react-|ng-|v-|[a-z]+-\d+$|\d)/.test(elemId);

    if (testid) {
      loc = { strategy: 'testid', value: testid };
    } else if (role && name) {
      loc = { strategy: 'role', value: { role: role, name: name } };
    } else if (hasSemanticId && el.offsetWidth > 0 && el.offsetHeight > 0) {
      loc = { strategy: 'id', value: elemId };
      // Use the id itself as the accessible name so the AI can reference it
      if (!name) name = elemId;
    } else {
      loc = { strategy: 'none', value: '', actionable: false };
    }

    var isVisible = el.offsetWidth > 0 && el.offsetHeight > 0;
    // Extract <option> labels for <select> (combobox) elements
    var options = null;
    if (tag === 'select') {
      options = Array.from(el.querySelectorAll('option'))
        .map(function(o){ return o.textContent.trim(); })
        .filter(function(t){ return t && !t.startsWith('Select'); });
    }

    var entry = {
      role:       role,
      name:       name,
      tag:        tag,
      testid:     testid,
      elemId:     hasSemanticId ? elemId : '',
      container:  container,
      domPath:    domPath,
      ariaLabel:  el.getAttribute('aria-label') || '',
      loc:        loc,
      actionable: !!(testid || (role && name) || (hasSemanticId && isVisible)) && isVisible,
    };
    if (options) entry.options = options;
    results.push(entry);
  }
  return results;
}
"""

DOM_FALLBACK_JS = r"""
() => {
  function getDomPath(el) {
    var parts = [], node = el, depth = 0;
    while (node && node !== document.body && depth < 4) {
      var tag = node.tagName.toLowerCase();
      var parent = node.parentElement;
      if (!parent) break;
      var siblings = Array.from(parent.children).filter(function(c){ return c.tagName===node.tagName; });
      var idx = siblings.indexOf(node) + 1;
      parts.unshift(siblings.length > 1 ? tag+':nth('+idx+')' : tag);
      node = parent; depth++;
    }
    return parts.join('>');
  }

  function getContainer(el) {
    var LANDMARKS = ['dialog','main','nav','aside','section','form','header','footer','article'];
    var node = el.parentElement;
    for (var depth = 0; depth < 8 && node && node !== document.body; depth++) {
      var tag  = node.tagName.toLowerCase();
      var role = (node.getAttribute('role') || '').toLowerCase();
      var aria = node.getAttribute('aria-label') || '';
      var testid = node.getAttribute('data-testid') || node.getAttribute('data-test')
                || node.getAttribute('data-cy') || node.getAttribute('data-qa') || '';
      var stableId = node.id && !/^(:r|react-|ng-|v-|\d)/.test(node.id) ? node.id : '';
      var isLandmark = LANDMARKS.indexOf(tag) !== -1 || LANDMARKS.indexOf(role) !== -1;
      var isIdentifiable = isLandmark || aria || testid || stableId || (role && role !== 'none' && role !== 'presentation');
      if (isIdentifiable) {
        var selector = tag;
        if (testid) selector += '[data-testid="' + testid + '"]';
        else if (aria) selector += '[aria-label="' + aria.trim() + '"]';
        else if (stableId) selector += '#' + stableId;
        else if (role && !isLandmark) selector += '[role="' + role + '"]';
        var siblings = node.parentElement
          ? Array.from(node.parentElement.children).filter(function(c){ return c.tagName===node.tagName; })
          : [];
        if (siblings.length > 1) {
          var idx = siblings.indexOf(node) + 1;
          selector += ':nth-of-type(' + idx + ')';
        }
        return selector;
      }
      node = node.parentElement;
    }
    return '';
  }

  var nodes = document.querySelectorAll(
    "[onclick]," +
    "[tabindex='0']:not(input):not(button):not(a):not(select):not(textarea):not([role])," +
    "[data-testid]:not(input):not(button):not(a):not(select):not(textarea):not([role])," +
    "[data-cy]:not(input):not(button):not(a):not(select):not(textarea):not([role])," +
    "[data-qa]:not(input):not(button):not(a):not(select):not(textarea):not([role])"
  );

  var results = [];
  for (var i = 0; i < nodes.length; i++) {
    var el  = nodes[i];
    var tag = el.tagName.toLowerCase();
    if (el.getAttribute('role')) continue;
    if (['button','input','select','textarea','a'].indexOf(tag) !== -1) continue;

    var testid = el.getAttribute('data-testid')
      || el.getAttribute('data-cy')
      || el.getAttribute('data-qa')
      || null;

    var name = el.getAttribute('aria-label')
      || testid
      || el.textContent.trim()
      || '';

    var selector = testid
      ? '[data-testid="' + testid + '"]'
      : tag + (el.id && !/^(:r|react-|ng-|v-|\d)/.test(el.id) ? '#'+el.id : '');

    results.push({
      role:         'none',
      name:         name,
      tag:          tag,
      testid:       testid,
      container:    getContainer(el),
      domPath:      getDomPath(el),
      ariaLabel:    el.getAttribute('aria-label') || '',
      loc:          { strategy: testid ? 'testid' : 'css', value: selector },
      dom_fallback: true,
      actionable:   !!testid,
    });
  }
  return results;
}
"""


# ── Stale detection ───────────────────────────────────────────────────

def _is_stale(db: LocatorDB, url: str) -> bool:
    page = db.get_page(url)
    if not page:
        return True
    last = page.get("last_crawled")
    if not last:
        return True
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
        return age > STALE_MINUTES
    except Exception:
        return True


# ── Stable render wait ────────────────────────────────────────────────

async def wait_for_stable(page: Page, timeout: int = 10_000):
    """
    Wait for the page to finish rendering.
    1. networkidle — fast path for traditional pages
    2. MutationObserver settle — handles SPAs with persistent connections
    3. 200ms final flush
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout, 4000))
    except Exception:
        pass

    remaining = max(1000, timeout - 4000)
    try:
        await page.evaluate(
            """(timeout) => new Promise(resolve => {
                var settled = false, tid = null;
                var obs = new MutationObserver(function() {
                    clearTimeout(tid);
                    tid = setTimeout(function() {
                        if (!settled) { settled=true; obs.disconnect(); resolve(); }
                    }, 500);
                });
                obs.observe(document.body, {childList:true,subtree:true,attributes:true});
                setTimeout(function() {
                    if (!settled) { settled=true; obs.disconnect(); resolve(); }
                }, timeout);
            })""",
            remaining,
        )
    except Exception:
        pass

    await page.wait_for_timeout(200)


# ── Auth ──────────────────────────────────────────────────────────────

async def _build_context(
    browser:     Browser,
    db:          LocatorDB,
    url:         str,
    credentials: Optional[dict] = None,
    device_kwargs: Optional[dict] = None,
) -> BrowserContext:
    domain  = urlparse(url).netloc
    session = db.get_session(domain)
    dk = device_kwargs or {}

    if session and session.get("storage_state"):
        try:
            return await browser.new_context(storage_state=session["storage_state"], **dk)
        except Exception:
            pass

    if credentials:
        ctx  = await browser.new_context(**dk)
        page = await ctx.new_page()
        try:
            await _run_login(page, credentials)
            # ── Task 4.4: Auth Verification ───────────────────────────
            auth_ok = await _verify_auth_state(page, credentials)
            if not auth_ok:
                raise RuntimeError("Auth verification failed: session not established or indicators missing")

            state = await ctx.storage_state()
            db.save_session(
                domain        = domain,
                storage_state = state,
                auth_type     = "credentials",
                cookies       = state.get("cookies", []),
            )
            await page.close()
            return ctx
        except Exception as e:
            await page.close()
            await ctx.close()
            raise RuntimeError(f"Login failed: {e}") from e

    return await browser.new_context(**dk)


async def _verify_auth_state(page: Page, credentials: dict) -> bool:
    """Check if we are actually logged in based on cookies or UI markers."""
    await wait_for_stable(page, timeout=5000)
    
    # 1. Check for session cookies
    cookies = await page.context.cookies()
    if not cookies:
        log.warning("No cookies found after login attempt")
        return False
        
    # 2. Check for UI indicators (e.g. Dashboard text, Logout button)
    indicators = ["logout", "sign out", "dashboard", "profile", "account", "settings"]
    content = (await page.content()).lower()
    
    found = any(ind in content for ind in indicators)
    if not found:
         # Also check if login form is still present (negative indicator)
         login_form_present = await page.locator("input[type=password]").is_visible()
         if login_form_present:
             log.warning("Login form still visible after login attempt")
             return False
             
    return True


_USERNAME_SELECTORS = [
    "input[type=email]",
    "input[type=text][name*=user]",
    "input[type=text][name*=email]",
    "input[type=text][name*=login]",
    "input[type=text][id*=user]",
    "input[type=text][id*=email]",
    "input[type=text][id*=login]",
    "input[type=text]",
]

_PASSWORD_SELECTORS = [
    "input[type=password]",
]

_SUBMIT_SELECTORS = [
    "button[type=submit]",
    "input[type=submit]",
    "button:text-matches('sign in', 'i')",
    "button:text-matches('log in', 'i')",
    "button:text-matches('login', 'i')",
    "button:text-matches('submit', 'i')",
    "button",
]


async def _find_selector(page: Page, candidates: list[str]) -> str:
    """Return the first selector from candidates that matches a visible element."""
    for sel in candidates:
        try:
            if await page.locator(sel).first.is_visible(timeout=3000):
                return sel
        except Exception:
            continue
    raise RuntimeError(f"Could not find a matching element. Tried: {candidates}")


async def _run_login(page: Page, credentials: dict):
    await page.goto(credentials["url"], wait_until="networkidle")

    user_sel   = credentials.get("username_selector") or await _find_selector(page, _USERNAME_SELECTORS)
    pass_sel   = credentials.get("password_selector") or await _find_selector(page, _PASSWORD_SELECTORS)

    await page.fill(user_sel, credentials["username"])
    await page.fill(pass_sel, credentials["password"])

    submit_sel = credentials.get("submit_selector") or await _find_selector(page, _SUBMIT_SELECTORS)
    await page.click(submit_sel)

    wait_for = credentials.get("wait_for")
    if wait_for:
        await page.wait_for_selector(wait_for, timeout=10_000)
    else:
        await wait_for_stable(page, timeout=10_000)


# ── Core crawl ────────────────────────────────────────────────────────

async def crawl_page(
    page:        Page,
    url:         str,
    db:          LocatorDB,
    force:       bool = False,
    state_graph=None,
) -> dict:
    """
    Crawl a single page and update the locator DB.
    Returns a summary dict.
    """
    url = _normalize_url(url)

    if not force and not _is_stale(db, url):
        return {
            "url":      url,
            "crawled":  False,
            "elements": len(db.get_all(url)),
            "new":      0,
            "updated":  0,
            "invalid":  0,
            "warnings": [],
        }

    seen_ids     = set()
    all_elements = []
    all_warnings = []

    # Optional: simulate hovers on potential menus to trigger lazy rendering
    try:
        await page.evaluate('''() => {
            document.querySelectorAll('[aria-haspopup], [aria-expanded="false"], .menu, .dropdown').forEach(el => {
                try { el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true})); } catch(e) {}
            });
        }''')
        await page.wait_for_timeout(200) # give JS time to render the dropdowns
    except Exception as e:
        all_warnings.append(f"Hover expansion failed: {e}")

    # Main frame — a11y
    try:
        a11y = await page.evaluate(A11Y_JS)
        if not isinstance(a11y, list):
            all_warnings.append(f"a11y JS returned unexpected type {type(a11y).__name__}, expected list")
            a11y = []
        for el in a11y:
            el["frameId"] = "main"
        all_elements.extend(a11y)
    except Exception as e:
        all_warnings.append(f"a11y collection failed: {e}")

    # Main frame — DOM fallback
    try:
        dom_els = await page.evaluate(DOM_FALLBACK_JS)
        if not isinstance(dom_els, list):
            all_warnings.append(f"DOM fallback JS returned unexpected type {type(dom_els).__name__}, expected list")
            dom_els = []
        for el in dom_els:
            el["frameId"] = "main"
        all_elements.extend(dom_els)
    except Exception as e:
        all_warnings.append(f"DOM fallback failed: {e}")

    # iframes
    for frame_idx, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        raw_url = frame.url or ""
        if raw_url and not raw_url.startswith(("about:", "blob:", "javascript:", "data:")):
            frame_url = _normalize_url(raw_url)
        else:
            frame_url = frame.name or f"frame-{frame_idx}"

        try:
            frame_a11y = await frame.evaluate(A11Y_JS)
            if not isinstance(frame_a11y, list):
                frame_a11y = []
            for el in frame_a11y:
                el["frameId"]     = frame_url
                el["frameName"]   = frame.name or ""
                el["crossOrigin"] = False
            all_elements.extend(frame_a11y)

            frame_dom = await frame.evaluate(DOM_FALLBACK_JS)
            if not isinstance(frame_dom, list):
                frame_dom = []
            for el in frame_dom:
                el["frameId"]     = frame_url
                el["frameName"]   = frame.name or ""
                el["crossOrigin"] = False
            all_elements.extend(frame_dom)
        except Exception:
            all_warnings.append(f"Cross-origin iframe blocked: {frame_url}")
            all_elements.append({
                "role": "iframe", "name": frame.name or frame_url,
                "tag": "iframe", "frameId": frame_url,
                "frameName": frame.name or "", "crossOrigin": True,
                "container": "", "domPath": "",
                "loc": {"strategy": "css",
                        "value": f"iframe[name='{frame.name}']" if frame.name
                                 else f"iframe:nth-of-type({frame_idx+1})"},
                "actionable": False,
            })

    log.debug("collected %d raw elements from %s", len(all_elements), url)

    # Guard: if nothing was collected at all, the page probably didn't render
    # (blank page, error page, network interstitial, JavaScript crash).
    # Bail out early so we don't stamp the page as "freshly crawled" and then
    # skip it on the next call — that would cache a broken empty result.
    actionable_count = sum(1 for el in all_elements if el.get("actionable"))
    if actionable_count == 0 and not all_warnings:
        all_warnings.append(
            "Zero actionable elements found — page may not have rendered correctly"
        )
    if len(all_elements) == 0:
        log.warning("crawl yielded 0 elements for %s — aborting upsert to avoid caching empty result", url)
        return {
            "url":      url,
            "crawled":  False,
            "elements": 0,
            "new":      0,
            "updated":  0,
            "invalid":  0,
            "warnings": all_warnings,
        }

    # Upsert into DB
    new_count = updated_count = 0
    for el in all_elements:
        role      = el.get("role", "")
        name      = el.get("name", "")
        container = el.get("container", "")
        furl      = el.get("frameId", "main")
        dom_path  = el.get("domPath", "")
        doc_id    = _make_id(url, role, name, container, furl, dom_path)

        existing = db._locs.get(db._Q.id == doc_id)
        doc      = db.upsert(url, el)

        if doc:
            seen_ids.add(doc_id)
            if existing:
                updated_count += 1
            else:
                new_count += 1
            if doc.get("warnings"):
                all_warnings.extend(doc["warnings"])

    # ── Template fingerprinting: skip re-crawl if layout already known ──
    if state_graph is not None:
        stored_docs   = db.get_all(url, valid_only=True)
        template_hash = _compute_template_hash(stored_docs)
        existing_tmpl = state_graph.get_template(template_hash)

        if existing_tmpl and existing_tmpl["sample_url"] != url:
            # Page matches a known template and is NOT the original sample URL.
            # Inherit locators from the sample and skip full crawl bookkeeping.
            state_graph.record_template_match(template_hash, url)
            inherited = db.inherit_locators(
                source_url=existing_tmpl["sample_url"],
                target_url=url,
                template_id=template_hash,
            )
            db.upsert_page(url, len(all_elements))
            return {
                "url":            url,
                "crawled":        True,
                "elements":       len(all_elements),
                "new":            inherited,
                "updated":        0,
                "invalid":        0,
                "warnings":       list(dict.fromkeys(all_warnings)),
                "template_match": True,
                "template_id":    template_hash,
                "inherited_from": existing_tmpl["sample_url"],
            }
        else:
            # New structural layout (or re-crawl of the sample URL itself) —
            # register_template is a no-op if template_id already exists.
            state_graph.register_template(
                template_id=template_hash,
                url=url,
                elements=stored_docs,
                url_pattern=_url_to_pattern(url),
            )

    db.soft_decay(url, seen_ids)
    invalidated = len([
        d for d in db.get_all(url, valid_only=False)
        if not d.get("history", {}).get("valid", True)
    ])

    # Screenshot this page for visual state inspection
    screenshot_path = ""
    try:
        import hashlib
        from pathlib import Path as _Path
        shot_dir = _Path(os.getenv("QAPAL_STATE_SCREENSHOTS", "reports/states"))
        shot_dir.mkdir(parents=True, exist_ok=True)
        slug = hashlib.md5(url.encode()).hexdigest()[:12]
        shot_file = shot_dir / f"{slug}.png"
        await page.screenshot(path=str(shot_file), full_page=False)
        screenshot_path = str(shot_file)
    except Exception:
        pass  # non-fatal

    db.upsert_page(url, len(all_elements), screenshot_path=screenshot_path)

    # Cascade-refresh: if this URL is a known sample_url, push updated locators
    # to all pages that previously inherited from it.
    if state_graph is not None:
        tmpl = state_graph.get_template_by_sample_url(url)
        if tmpl:
            for target_url in state_graph.get_inherited_urls(tmpl["template_id"]):
                db.inherit_locators(
                    source_url=url,
                    target_url=target_url,
                    template_id=tmpl["template_id"],
                )

    return {
        "url":      url,
        "crawled":  True,
        "elements": len(all_elements),
        "new":      new_count,
        "updated":  updated_count,
        "invalid":  invalidated,
        "warnings": list(dict.fromkeys(all_warnings)),
    }


# ── Crawler class ─────────────────────────────────────────────────────

class Crawler:
    """
    Stateful crawler. Holds one browser open.

    Usage:
        async with Crawler(db) as crawler:
            result = await crawler.on_page_load(page, url)

        async with Crawler(db) as crawler:
            results = await crawler.bulk_crawl(["https://app.com/", "/login"])
    """

    def __init__(
        self,
        db:           LocatorDB,
        headless:     Optional[bool] = None,
        credentials:  Optional[dict] = None,
        state_graph=None,
        device:       Optional[str]  = None,
        viewport:     Optional[tuple] = None,
    ):
        self._db          = db
        self._headless    = headless if headless is not None else (
            os.getenv("QAPAL_HEADLESS", "true").lower() == "true"
        )
        self._credentials = credentials
        self._state_graph = state_graph
        self._device      = device
        self._viewport    = viewport
        self._pw          = None
        self._browser     = None
        self._started     = False

    async def start(self):
        if self._started:
            return
        self._pw      = await async_playwright().start()
        self._pw.selectors.set_test_id_attribute("data-test")
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._started = True

    async def stop(self):
        if not self._started:
            return
        if self._browser:
            try: await self._browser.close()
            except Exception: pass
        if self._pw:
            try: await self._pw.stop()
            except Exception: pass
        self._started = False
        self._browser = None
        self._pw      = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.stop()

    async def on_page_load(self, page: Page, url: str, force: bool = False) -> dict:
        """
        Called by executor on every navigation.
        Does NOT wait for stable — executor already did that.
        """
        return await crawl_page(page=page, url=_normalize_url(url), db=self._db, force=force,
                                state_graph=self._state_graph)

    async def crawl_url(self, url: str, force: bool = False) -> dict:
        """
        Crawl a single URL in an isolated context.
        Retries once on transient network failure (DNS blip, connection reset).
        """
        if not self._started:
            await self.start()

        last_err: Exception = None
        for attempt in range(2):  # 1 attempt + 1 retry
            ctx  = await _build_context(self._browser, self._db, url, self._credentials)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await wait_for_stable(page)
                return await crawl_page(page, _normalize_url(url), self._db, force=force,
                                        state_graph=self._state_graph)
            except Exception as e:
                last_err = e
                log.warning("crawl_url attempt %d failed for %s: %s", attempt + 1, url, e)
            finally:
                await ctx.close()

            if attempt == 0:
                await asyncio.sleep(2)  # brief pause before retry

        # Both attempts failed — return a failure result (do not raise)
        return {
            "url":      _normalize_url(url),
            "crawled":  False,
            "elements": 0,
            "new":      0,
            "updated":  0,
            "invalid":  0,
            "warnings": [f"crawl_url failed after 2 attempts: {last_err}"],
        }

    async def bulk_crawl(
        self,
        urls:        list,
        concurrency: int  = None,
        force:       bool = False,
    ) -> list:
        """
        Crawl multiple URLs concurrently.
        Page fetches run in parallel; DB writes are serialised.
        """
        if not self._started:
            await self.start()

        concurrency = concurrency or int(os.getenv("QAPAL_CRAWL_CONCURRENCY", "3"))
        semaphore   = asyncio.Semaphore(concurrency)
        db_lock     = asyncio.Lock()
        results     = []

        async def _one(url: str):
            async with semaphore:
                ctx  = await _build_context(self._browser, self._db, url, self._credentials)
                page = await ctx.new_page()
                try:
                    # Network fetch + JS evaluation run in parallel across URLs.
                    # Only the final DB writes are serialised (via db_lock) to
                    # prevent TinyDB race conditions on concurrent upserts.
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await wait_for_stable(page)
                except Exception as e:
                    await ctx.close()
                    results.append({
                        "url": _normalize_url(url), "crawled": False,
                        "elements": 0, "new": 0, "updated": 0, "invalid": 0,
                        "warnings": [f"Page load failed: {e}"],
                    })
                    log.warning("  [failed]  %s — %s", url, e)
                    return

                try:
                    async with db_lock:
                        result = await crawl_page(page, _normalize_url(url), self._db, force=force,
                                                  state_graph=self._state_graph)
                except Exception as e:
                    result = {
                        "url": _normalize_url(url), "crawled": False,
                        "elements": 0, "new": 0, "updated": 0, "invalid": 0,
                        "warnings": [f"Crawl extraction failed: {e}"],
                    }
                finally:
                    await ctx.close()

                results.append(result)
                if result["crawled"]:
                    log.info("  [crawled] %s — %d elements | %d new | %d updated",
                             url, result["elements"], result["new"], result["updated"])
                else:
                    log.info("  [skipped] %s (fresh)", url)
                for w in result.get("warnings", []):
                    log.warning("  %s", w)

        await asyncio.gather(*[_one(u) for u in urls])
        return results

    async def spider_crawl(
        self,
        start_urls:  list,
        max_depth:   int  = 2,
        max_pages:   int  = 30,
        concurrency: int  = None,
        force:       bool = False,
    ) -> list:
        """
        Spider the site starting from start_urls, following same-domain links
        up to max_depth hops deep, crawling at most max_pages pages.
        Deduplicates by URL pattern so e.g. /product/ID-1 and /product/ID-2
        are treated as the same page type — only one is crawled.
        Discovery runs level-by-level concurrently (not sequentially).
        """
        if not self._started:
            await self.start()

        disc_concurrency = int(os.getenv("QAPAL_CRAWL_CONCURRENCY", "3"))
        disc_semaphore   = asyncio.Semaphore(disc_concurrency)

        # Replace ID-like path segments with {id} for pattern deduplication.
        # Matches ULIDs (26 base32 chars), UUIDs, hex strings, and pure numbers.
        _ID_RE = re.compile(
            r"(?<=/)"
            r"([A-Za-z0-9]{20,}|[0-9a-f]{8}-[0-9a-f-]{27}|[0-9]+)"
            r"(?=/|$)",
        )

        def _url_pattern(url: str) -> str:
            p = urlparse(url)
            return p.netloc + _ID_RE.sub("{id}", p.path)

        allowed_domains  = {urlparse(u).netloc for u in start_urls}
        visited_urls     = set()
        visited_patterns = set()
        all_urls         = []

        def _accept(norm: str) -> bool:
            """Return True and register URL if it's a new pattern we should visit."""
            if norm in visited_urls or len(all_urls) >= max_pages:
                return False
            pat = _url_pattern(norm)
            if pat in visited_patterns:
                return False
            # Skip URLs already in the locator DB from a previous run, but only
            # if their data is still fresh (respects CRAWLER_STALE_MINUTES).
            if not force and self._db.get_all(norm, valid_only=True):
                page_info = self._db.get_page(norm)
                if page_info:
                    stale_min = int(os.getenv("CRAWLER_STALE_MINUTES", "60"))
                    last = page_info.get("last_crawled", "")
                    if last:
                        from datetime import datetime, timezone
                        try:
                            ts  = datetime.fromisoformat(last.replace("Z", "+00:00"))
                            age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                            if age < stale_min:
                                visited_urls.add(norm)
                                visited_patterns.add(pat)
                                return False
                        except Exception:
                            pass
            visited_urls.add(norm)
            visited_patterns.add(pat)
            all_urls.append(norm)
            return True

        async def _extract_links(url: str) -> list[str]:
            """Navigate to url, return same-domain hrefs. Fast — no DB write."""
            async with disc_semaphore:
                ctx  = await _build_context(self._browser, self._db, url, self._credentials)
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    await page.wait_for_timeout(800)   # brief settle for SPAs
                    hrefs = await page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => e.href).filter(h => h "
                        "&& !h.startsWith('javascript') "
                        "&& !h.startsWith('mailto') "
                        "&& !h.startsWith('tel'))"
                    )
                    return hrefs
                except Exception:
                    return []
                finally:
                    await ctx.close()

        # Seed with start_urls (depth 0)
        current_level = []
        for u in start_urls:
            norm = _normalize_url(u)
            if _accept(norm):
                current_level.append(norm)

        # BFS level-by-level, each level fetched concurrently
        for depth in range(max_depth):
            if not current_level or len(all_urls) >= max_pages:
                break
            log.info("  [spider] depth %d: discovering links from %d page(s)...",
                     depth, len(current_level))
            link_lists = await asyncio.gather(*[_extract_links(u) for u in current_level])

            next_level = []
            for hrefs in link_lists:
                for href in hrefs:
                    if urlparse(href).netloc not in allowed_domains:
                        continue
                    norm = _normalize_url(href)
                    if _accept(norm):
                        next_level.append(norm)
                    if len(all_urls) >= max_pages:
                        break

            current_level = next_level

        log.info("  [spider] discovered %d unique page type(s)", len(all_urls))
        return await self.bulk_crawl(all_urls, concurrency=concurrency, force=force)


# ── Standalone Testing ────────────────────────────────────────────────

if __name__ == "__main__":
    # Standalone smoke test
    import asyncio
    async def test():
        from locator_db import LocatorDB
        db = LocatorDB()
        try:
            async with Crawler(db, headless=True) as crawler:
                log.info("Crawler initialized. Running smoke test...")
                results = await crawler.bulk_crawl(["https://example.com"])
                log.info("Crawled %d URLs.", len(results))
        except Exception as e:
            log.error("Smoke test failed: %s", e)
        finally:
            db.close()

    asyncio.run(test())
