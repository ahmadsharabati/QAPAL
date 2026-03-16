"""
locator_db.py — QAPal Locator Database
========================================
TinyDB document store. One document per unique element.

Identity key:  url + role + name + container + frame_url + dom_path
               dom_path disambiguates repeated elements (table rows, list items).
               name is normalised before hashing to survive dynamic values.

Locator chain (strict priority, no scoring):
  1. data-testid          -> unique by convention
  2. role + name          -> verified at runtime (unique=None until executor checks)
  3. role + name + container -> scoped lookup
  4. aria-label           -> explicit accessibility
  5. placeholder          -> for textboxes only

All config from environment variables (.env supported via python-dotenv).

Install:
  pip install tinydb python-dotenv
"""

import hashlib
import json
import os
import re
import threading
import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from tinydb import Query, TinyDB
from tinydb.middlewares import CachingMiddleware
from tinydb.storages import JSONStorage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Config ────────────────────────────────────────────────────────────

MISS_THRESHOLD = int(os.getenv("LOCATOR_MISS_THRESHOLD", "3"))

# ── Shared regex ──────────────────────────────────────────────────────
# Matches trailing dynamic ID suffixes: ULID (26 base32 chars), UUID, or long hex (>=16).
# Imported by generator.py and planner.py — single source of truth.
DYNAMIC_ID_RE = re.compile(
    r"[-/]([0-9A-Za-z]{26}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9a-fA-F]{16,})$"
)


# ── Helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domain(url: str) -> str:
    return urlparse(url).netloc


def _normalize_url(url: str) -> str:
    """Strip query params and fragments. scheme + host + path only."""
    if not url:
        return ""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


# Matches dynamic path segments: ULID (26 base32), UUID, or 4+ digit numbers.
# Used by _url_to_pattern to replace volatile segments with :id placeholder.
_DYNAMIC_PATH_SEG_RE = re.compile(
    r"(?<=/)"
    r"(?:[0-9A-Za-z]{26}"                                          # ULID (26-char base32)
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # UUID
    r"|[a-zA-Z0-9_-]*\d{2,}"                                      # slug-with-number (e.g. tipping-the-velvet_999)
    r"|\d{4,}"                                                     # numeric IDs ≥4 digits
    r")(?=/|$)"
)


def _url_to_pattern(url: str) -> str:
    """
    Replace volatile path segments (ULID, UUID, numeric ID) with ':id'.
    e.g. '/product/01KKNWM4EP4HYEP1X6CF8622XB' → '/product/:id'
    Used by StateGraph to group stale/rotating product URLs under a stable pattern.
    """
    if not url:
        return url
    p = _normalize_url(url)
    path = urlparse(p).path
    normalized_path = _DYNAMIC_PATH_SEG_RE.sub(":id", path)
    if normalized_path == path:
        return p
    parsed = urlparse(p)
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


# ── Template fingerprinting helpers ──────────────────────────────────

_NTH_RE = re.compile(r":nth\(\d+\)")


def _strip_nth(dom_path: str) -> str:
    """
    Strip positional :nth(N) indices from a dom_path.
    e.g. 'article>div:nth(2)>form>button' → 'article>div>form>button'
    """
    return _NTH_RE.sub("", dom_path)


def _compute_template_hash(elements: list) -> str:
    """
    Structural fingerprint from a list of locator DB documents.
    Uses role + container tag (stripped of attrs) + dom_path (without :nth indices).
    Keys are deduplicated — presence of a slot type matters, not repetition count.
    This makes pages with 1 vs 2 related-product links hash identically.
    Ignores element names — purely structural.
    Returns a 12-char hex string.
    """
    structural_keys = sorted(set(
        (
            elem.get("identity", {}).get("role", ""),
            elem.get("identity", {}).get("container", "").split("[")[0].split("#")[0],
            _strip_nth(elem.get("identity", {}).get("dom_path", "")),
        )
        for elem in elements
        if elem.get("locators", {}).get("actionable", False)
    ))
    return hashlib.sha256(json.dumps(structural_keys).encode()).hexdigest()[:12]


# ── Dynamic name normalization ────────────────────────────────────────

_DYNAMIC_PATTERNS: List[tuple] = [
    (re.compile(r"\s*\(\d+\)\s*$"),        "counter"),
    (re.compile(r"#\s*\d+"),               "order_id"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"),     "date"),
    (re.compile(r"\bToday\b", re.I),        "relative_date"),
    (re.compile(r"\bYesterday\b", re.I),    "relative_date"),
    (re.compile(r"\d+\s*results?"),         "count"),
    (re.compile(r"\d+\s*items?"),           "count"),
    (re.compile(r"\b\d{1,2}:\d{2}\b"),      "time"),
    (re.compile(r"\$\d[\d,]*\.?\d*"),       "price"),
    (re.compile(r"\d+%"),                   "percentage"),
]


def _normalise_name(name: str) -> str:
    if not name:
        return ""
    result = name
    for pattern, _ in _DYNAMIC_PATTERNS:
        result = pattern.sub("", result)
    return result.strip()


def _name_pattern(name: str) -> Optional[str]:
    if not name:
        return None
    for pattern, _ in _DYNAMIC_PATTERNS:
        if pattern.search(name):
            stable = _normalise_name(name)
            if stable:
                return f"^{re.escape(stable)}"
            return None
    return None


# ── Identity hash ─────────────────────────────────────────────────────

def _make_id(
    url:       str,
    role:      str,
    name:      str,
    container: str,
    frame_url: str,
    dom_path:  str = "",
) -> str:
    key = f"{url}|{role}|{_normalise_name(name)}|{container}|{frame_url}|{dom_path}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Frame helper ──────────────────────────────────────────────────────

def _make_frame(element: dict, page_url: str = "main") -> dict:
    frame_id = element.get("frameId", "main")
    if frame_id == "main":
        return {"type": "main", "url": page_url, "name": "", "cross_origin": False, "accessible": True}
    return {
        "type":         "iframe",
        "url":          frame_id,
        "name":         element.get("frameName", ""),
        "cross_origin": element.get("crossOrigin", False),
        "accessible":   not element.get("crossOrigin", False),
    }


# ── Locator chain builder ─────────────────────────────────────────────

def _build_chain(element: dict, container: str) -> List[Dict[str, Any]]:
    chain   = []
    loc     = element.get("loc", {})
    role    = element.get("role", "")
    name    = element.get("name", "")

    testid = element.get("testid") or (
        loc.get("value") if loc.get("strategy") == "testid" else None
    )
    if testid:
        chain.append({"strategy": "testid", "value": testid, "unique": True})

    # id strategy: highest confidence after testid — comes before role so the
    # executor tries the stable id first for unnamed-but-id'd elements (P0.4).
    elem_id = element.get("elemId") or (
        loc.get("value") if loc.get("strategy") == "id" else None
    )
    if elem_id and not testid:
        chain.append({"strategy": "id", "value": elem_id, "unique": True})

    if role and role != "none" and name:
        chain.append({
            "strategy": "role",
            "value":    {"role": role, "name": name},
            "unique":   None,
        })

    if role and role != "none" and name and container:
        chain.append({
            "strategy": "role+container",
            "value":    {"role": role, "name": name, "container": container},
            "unique":   None,
        })

    if (not role or role == "none") and name:
        chain.append({"strategy": "text", "value": name, "unique": None})

    aria = element.get("ariaLabel")
    if aria:
        chain.append({"strategy": "aria-label", "value": aria, "unique": None})

    placeholder = element.get("placeholder")
    if placeholder:
        chain.append({"strategy": "placeholder", "value": placeholder, "unique": None})

    if loc.get("strategy") == "css" and loc.get("value"):
        chain.append({"strategy": "css", "value": loc["value"], "unique": False})

    return chain


# ── LocatorDB ─────────────────────────────────────────────────────────

class LocatorDB:
    """
    Document store for all interactive elements discovered by the crawler.

    Thread-safe. All writes serialised through a single RLock.
    CachingMiddleware batches disk flushes for performance.

    Usage:
        with LocatorDB() as db:
            db.upsert(page_url, element)
            rec = db.get(page_url, role="button", name="Save")
    """

    def __init__(self, path: Optional[str] = None):
        self._path = path or os.getenv("QAPAL_DB_PATH", "locators.json")
        self._lock = threading.RLock()
        self._db   = TinyDB(self._path, storage=CachingMiddleware(JSONStorage))
        self._locs        = self._db.table("locators")
        self._pages       = self._db.table("pages")
        self._sessions    = self._db.table("sessions")
        self._states      = self._db.table("states")
        self._transitions = self._db.table("transitions")
        self._Q           = Query()

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Pages ─────────────────────────────────────────────────────────

    def get_page(self, url: str) -> Optional[dict]:
        url = _normalize_url(url)
        return self._pages.get(self._Q.url == url)

    def upsert_page(self, url: str, element_count: int, screenshot_path: str = "") -> None:
        url = _normalize_url(url)
        now = _now()
        with self._lock:
            if self._pages.get(self._Q.url == url):
                update = {"last_crawled": now, "element_count": element_count}
                if screenshot_path:
                    update["screenshot_path"] = screenshot_path
                self._pages.update(update, self._Q.url == url)
            else:
                doc = {
                    "url":           url,
                    "domain":        _domain(url),
                    "last_crawled":  now,
                    "element_count": element_count,
                }
                if screenshot_path:
                    doc["screenshot_path"] = screenshot_path
                self._pages.insert(doc)

    def all_pages(self) -> List[dict]:
        return self._pages.all()

    # ── Locators ──────────────────────────────────────────────────────

    def upsert(self, page_url: str, element: dict) -> Optional[dict]:
        """
        Insert or update a locator document.
        Returns the document, or None if element has no actionable locators.
        All reads AND writes happen inside a single lock to prevent races.
        """
        page_url   = _normalize_url(page_url)
        role       = element.get("role", "")
        name       = element.get("name", "")
        tag        = element.get("tag", "")
        container  = element.get("container", "")
        dom_path   = element.get("domPath", "")
        actionable = element.get("actionable", True)
        frame      = _make_frame(element, page_url)
        frame_url  = frame["url"]
        doc_id     = _make_id(page_url, role, name, container, frame_url, dom_path)
        chain      = _build_chain(element, container)
        pattern    = _name_pattern(name)

        if not chain:
            return None

        confidence = "high" if (role and name) else "low"
        source     = element.get("source", "a11y" if confidence == "high" else "dom_fallback")

        with self._lock:
            existing = self._locs.get(self._Q.id == doc_id)

            # Skip non-actionable elements unless we're updating an existing record
            # (allows actionable=False to propagate to existing DB entries on re-crawl)
            if not actionable and not existing:
                return None

            warnings = self._warnings(page_url, role, name, container, chain, actionable, exclude_id=doc_id)

            if existing:
                old_chain = existing.get("locators", {}).get("chain", [])
                prev      = existing.get("previous_locators", [])
                if old_chain != chain:
                    prev = (prev + [{"chain": old_chain, "retired": _now()}])[-5:]

                self._locs.update(
                    {
                        "identity": {
                            **existing["identity"],
                            "name":         name,
                            "name_pattern": pattern,
                            "container":    container,
                            "dom_path":     dom_path or existing["identity"].get("dom_path", ""),
                        },
                        "locators": {
                            "chain":      chain,
                            "confidence": confidence,
                            "source":     source,
                            "actionable": actionable,
                        },
                        "history": {
                            "first_seen": existing.get("history", {}).get("first_seen", _now()),
                            "last_seen":  _now(),
                            "hit_count":  existing.get("history", {}).get("hit_count", 0) + 1,
                            "miss_count": 0,
                            "valid":      True,
                        },
                        "previous_locators": prev,
                        "warnings":          warnings,
                    },
                    self._Q.id == doc_id,
                )
            else:
                now = _now()
                self._locs.insert({
                    "id":      doc_id,
                    "url":     page_url,
                    "identity": {
                        "role":         role,
                        "name":         name,
                        "name_pattern": pattern,
                        "tag":          tag,
                        "container":    container,
                        "dom_path":     dom_path,
                        "frame":        frame,
                    },
                    "locators": {
                        "chain":      chain,
                        "confidence": confidence,
                        "source":     source,
                        "actionable": actionable,
                    },
                    "history": {
                        "first_seen": now,
                        "last_seen":  now,
                        "hit_count":  1,
                        "miss_count": 0,
                        "valid":      True,
                    },
                    "previous_locators": [],
                    "warnings":          warnings,
                })

            return self._locs.get(self._Q.id == doc_id)

    def _warnings(
        self,
        url:       str,
        role:      str,
        name:      str,
        container: str,
        chain:     list,
        actionable: bool,
        exclude_id: Optional[str] = None,
    ) -> List[str]:
        """Generate quality warnings. Must be called inside lock."""
        w = []
        if not role:
            w.append("No ARIA role.")
        if not name:
            w.append("No accessible name.")
        if not container and role and name:
            dupes = [
                d for d in self._locs.all()
                if d.get("url") == url
                and d.get("identity", {}).get("role") == role
                and _normalise_name(d.get("identity", {}).get("name", "")) == _normalise_name(name)
                and d.get("id") != exclude_id
            ]
            if len(dupes) >= 1:
                w.append(
                    f"Duplicate name '{name}' with role '{role}' — "
                    "add container fingerprint."
                )
        if not chain or not actionable:
            w.append("No stable locator — not actionable.")
        return w

    def get(
        self,
        url:       str,
        role:      str,
        name:      str,
        container: str = "",
        frame_url: str = "main",
        dom_path:  str = "",
    ) -> Optional[dict]:
        url    = _normalize_url(url)
        doc_id = _make_id(url, role, name, container, frame_url, dom_path)
        return self._locs.get(self._Q.id == doc_id)

    def get_by_id(self, doc_id: str) -> Optional[dict]:
        return self._locs.get(self._Q.id == doc_id)

    def get_all(self, url: str, valid_only: bool = True) -> List[dict]:
        url = _normalize_url(url)
        results = [
            d for d in self._locs.all()
            if d.get("url") == url
            and (not valid_only or d.get("history", {}).get("valid", True))
        ]
        return sorted(results, key=lambda d: d.get("history", {}).get("hit_count", 0), reverse=True)

    def get_all_locators(self, valid_only: bool = True) -> List[dict]:
        """Return all locators across all URLs, sorted by hit_count descending."""
        results = [
            d for d in self._locs.all()
            if not valid_only or d.get("history", {}).get("valid", True)
        ]
        return sorted(results, key=lambda d: d.get("history", {}).get("hit_count", 0), reverse=True)

    def search(
        self,
        url:           str,
        name_fragment: str,
        role:          Optional[str] = None,
        container:     Optional[str] = None,
        valid_only:    bool          = True,
    ) -> List[dict]:
        url = _normalize_url(url)
        out = []
        for d in self._locs.all():
            if d.get("url") != url:
                continue
            if valid_only and not d.get("history", {}).get("valid", True):
                continue
            identity    = d.get("identity", {})
            doc_name    = identity.get("name", "")
            doc_pattern = identity.get("name_pattern")
            if doc_pattern:
                try:
                    if not re.search(doc_pattern, name_fragment):
                        continue
                except re.error:
                    if name_fragment.lower() not in doc_name.lower():
                        continue
            else:
                if name_fragment.lower() not in doc_name.lower():
                    continue
            if role and identity.get("role") != role:
                continue
            if container and identity.get("container") != container:
                continue
            out.append(d)
        return sorted(out, key=lambda d: d.get("history", {}).get("hit_count", 0), reverse=True)

    def mark_unique(self, doc_id: str, unique: bool) -> None:
        with self._lock:
            existing = self._locs.get(self._Q.id == doc_id)
            if not existing:
                return
            chain = copy.deepcopy(existing["locators"]["chain"])
            for entry in chain:
                if entry["strategy"] in ("role", "role+container") and entry["unique"] is None:
                    entry["unique"] = unique
            self._locs.update(
                {"locators": {**existing["locators"], "chain": chain}},
                self._Q.id == doc_id,
            )

    def mark_ai_rediscovered(
        self,
        url:       str,
        role:      str,
        name:      str,
        new_chain: List[dict],
        container: str = "",
        frame_url: str = "main",
        dom_path:  str = "",
    ) -> bool:
        url    = _normalize_url(url)
        doc_id = _make_id(url, role, name, container, frame_url, dom_path)
        with self._lock:
            existing = self._locs.get(self._Q.id == doc_id)
            if not existing:
                return False
            prev = existing.get("previous_locators", [])
            prev = (prev + [{"chain": existing["locators"]["chain"], "retired": _now()}])[-5:]
            self._locs.update(
                {
                    "locators": {
                        **existing["locators"],
                        "chain":  new_chain,
                        "source": "ai_rediscovery",
                    },
                    "previous_locators": prev,
                },
                self._Q.id == doc_id,
            )
            return True

    # ── Soft decay ────────────────────────────────────────────────────

    def soft_decay(self, url: str, seen_ids: set) -> int:
        url         = _normalize_url(url)
        invalidated = 0
        with self._lock:
            to_decay = [
                d for d in self._locs.all()
                if d.get("url") == url
                and d.get("history", {}).get("valid", True)
                and d.get("id") not in seen_ids
            ]
            for doc in to_decay:
                history               = doc.get("history", {})
                history["miss_count"] = history.get("miss_count", 0) + 1
                if history["miss_count"] >= MISS_THRESHOLD:
                    history["valid"] = False
                    invalidated     += 1
                self._locs.update({"history": history}, self._Q.id == doc["id"])
        return invalidated

    # ── Sessions ──────────────────────────────────────────────────────

    def save_session(
        self,
        domain:        str,
        storage_state: dict,
        auth_type:     str               = "credentials",
        cookies:       Optional[List[dict]] = None,
    ) -> None:
        doc = {
            "domain":        domain,
            "cookies":       cookies or [],
            "storage_state": storage_state,
            "auth_type":     auth_type,
            "saved_at":      _now(),
        }
        with self._lock:
            if self._sessions.get(self._Q.domain == domain):
                self._sessions.update(doc, self._Q.domain == domain)
            else:
                self._sessions.insert(doc)

    def get_session(self, domain: str) -> Optional[dict]:
        return self._sessions.get(self._Q.domain == domain)

    def delete_session(self, domain: str) -> None:
        with self._lock:
            self._sessions.remove(self._Q.domain == domain)

    # ── States (URL + DOM fingerprint + semantic context) ─────────────

    def upsert_state(self, url: str, dom_hash: str, semantic_context: dict) -> None:
        """
        Save or update the state record for a URL.
        Called by the semantic pipeline (after crawl) and by the executor
        when a new unknown state is discovered at runtime.
        """
        url = _normalize_url(url)
        doc = {
            "url":              url,
            "dom_hash":         dom_hash,
            "semantic_context": semantic_context,
            "updated_at":       _now(),
        }
        with self._lock:
            if self._states.get(self._Q.url == url):
                self._states.update(doc, self._Q.url == url)
            else:
                self._states.insert(doc)

    def get_state(self, url: str) -> Optional[dict]:
        url = _normalize_url(url)
        return self._states.get(self._Q.url == url)

    def get_semantic_context(self, url: str) -> Optional[dict]:
        state = self.get_state(url)
        return state.get("semantic_context") if state else None

    def all_states(self) -> List[dict]:
        return self._states.all()

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        all_locs = self._locs.all()
        valid    = [d for d in all_locs if d.get("history", {}).get("valid", True)]
        return {
            "total_elements":   len(all_locs),
            "valid_elements":   len(valid),
            "invalid_elements": len(all_locs) - len(valid),
            "low_confidence":   len([d for d in valid if d.get("locators", {}).get("confidence") == "low"]),
            "with_warnings":    len([d for d in valid if d.get("warnings")]),
            "total_pages":      len(self._pages.all()),
            "total_sessions":   len(self._sessions.all()),
            "total_states":      len(self._states.all()),
            "total_transitions": len(self._transitions.all()),
            "db_path":           self._path,
        }

    def clear_page(self, url: str) -> int:
        url = _normalize_url(url)
        with self._lock:
            removed = self._locs.remove(self._Q.url == url)
            return len(removed) if isinstance(removed, list) else removed

    def clear_all(self) -> dict:
        with self._lock:
            n_locs   = len(self._locs.all())
            n_pages  = len(self._pages.all())
            n_sess   = len(self._sessions.all())
            n_states = len(self._states.all())
            self._locs.truncate()
            self._pages.truncate()
            self._sessions.truncate()
            self._states.truncate()
            return {"locators": n_locs, "pages": n_pages, "sessions": n_sess, "states": n_states}

    def inherit_locators(self, source_url: str, target_url: str, template_id: str = "") -> int:
        """
        Copy all valid locator records from source_url to target_url.
        Each copied record is tagged with template_id for cascade-refresh tracking.
        Skips records that already exist for target_url.
        Returns the number of new records inserted.
        """
        source_url = _normalize_url(source_url)
        target_url = _normalize_url(target_url)
        if source_url == target_url:
            return 0

        source_docs = self.get_all(source_url, valid_only=True)
        copied = 0
        now = _now()

        with self._lock:
            for doc in source_docs:
                identity  = doc.get("identity", {})
                role      = identity.get("role", "")
                name      = identity.get("name", "")
                container = identity.get("container", "")
                frame_url = identity.get("frame", {}).get("url", "main")
                dom_path  = identity.get("dom_path", "")
                new_id    = _make_id(target_url, role, name, container, frame_url, dom_path)

                if self._locs.get(self._Q.id == new_id):
                    continue  # already present — skip

                new_doc = {
                    "id":      new_id,
                    "url":     target_url,
                    "identity": {
                        **identity,
                        "frame": {
                            **identity.get("frame", {}),
                            "url": target_url if frame_url == source_url else frame_url,
                        },
                    },
                    "locators": doc.get("locators", {}),
                    "history": {
                        "first_seen": now,
                        "last_seen":  now,
                        "hit_count":  0,
                        "miss_count": 0,
                        "valid":      True,
                    },
                    "previous_locators": [],
                    "warnings":          doc.get("warnings", []),
                    "template_id":       template_id,
                }
                self._locs.insert(new_doc)
                copied += 1

        return copied