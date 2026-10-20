"""Product registry: the list of product names (product_model) the dashboard accepts.

Canonical form   TN-<number>_<model>      e.g. TN-86_PICO-IMX7, TN-175_PICO-IMX8M MINI
Accepted input   the hyphen after TN may be missing; '-', '_' and spaces are interchangeable
                 (TN73_PICO_IMX6 → TN-73_PICO-IMX6); case-insensitive; full-width characters OK.
match_key        the name upper-cased with every separator removed (TN73PICOIMX6) — unique per product.

resolve(text) tells the caller what the text means:
  known            → use `canonical`
  retired          → a registry product the administrator retired (not selectable; no upload)
  new              → a well-formed name nobody has used (needs the user's confirmation; R4)
  blocked_same_tn  → the TN number already belongs to another product (R16: a typo, not a new product)
  ambiguous        → a TN number or name fragment that matches several products
  bad_format       → not a product name at all
Pure helpers have no DB access (unit-testable); the registry itself lives in product_registry.
"""
import re
import unicodedata
from typing import Any, Optional

import db

SHAPE_RE = re.compile(r"^TN[-_ ]?(\d{2,4})[-_ ]+([A-Z0-9][A-Z0-9 _-]*[A-Z0-9])$")
TOKEN_RE = re.compile(r"TN[-_ ]?\d{2,4}[-_ ]+[A-Z0-9][A-Z0-9 _-]*[A-Z0-9]", re.IGNORECASE)
TN_ONLY_RE = re.compile(r"^(?:TN[-_ ]?)?(\d{2,4})$", re.IGNORECASE)
DASHES = dict.fromkeys(map(ord, "‐‑‒–—−－"), "-")


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).translate(DASHES).strip().upper()
    return re.sub(r"\s+", " ", s)


def match_key(text: str) -> str:
    return re.sub(r"[-_ ]", "", normalize(text))


def shape(text: str) -> Optional[tuple[int, str]]:
    """(TN number, model part) when the text has the shape of a product name."""
    m = SHAPE_RE.match(normalize(text))
    return (int(m.group(1)), m.group(2)) if m else None


def canonical_form(text: str) -> Optional[str]:
    """The canonical spelling of a well-formed name: TN-<n>_<model with - inside>."""
    s = shape(text)
    if not s:
        return None
    number, model = s
    return f"TN-{number}_{re.sub(r'[ _-]+', '-', model)}"


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Registry:
    """In-memory view of the registry for one request. Active products are matched; retired ones
    are recognised (so they are never re-proposed as new) and keep their TN number taken (R16)."""

    def __init__(self, rows: list[dict]):
        self.rows = [r for r in rows if r.get("status", "active") == "active"]
        self.by_key: dict[str, dict] = {}
        self.retired_by_key: dict[str, dict] = {}
        self.by_tn: dict[int, list[dict]] = {}
        for r in rows:
            target = self.by_key if r.get("status", "active") == "active" else self.retired_by_key
            target[r["match_key"]] = r
            for alias in r.get("aliases") or []:
                target.setdefault(match_key(alias), r)
            if r.get("tn_number") is not None:
                self.by_tn.setdefault(int(r["tn_number"]), []).append(r)

    def names(self) -> list[str]:
        """Active canonical names (selection lists, the assistant's enum)."""
        return [r["canonical"] for r in self.rows]

    def resolve(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {"status": "bad_format", "input": raw}
        norm = normalize(raw)
        if re.match(r"^TN[-_ ]?\d", norm) and not shape(raw) and not TN_ONLY_RE.match(norm):
            # A TN name with the wrong shape (TN-99_, TN-99_EDM1-IMX6P-, TN99EDM1IMX6P) is a typo even when
            # its letters happen to equal a known product's: the separators are part of the format.
            return {"status": "bad_format", "input": raw}
        key = match_key(raw)
        if key in self.by_key:
            return {"status": "known", "input": raw, "canonical": self.by_key[key]["canonical"]}
        if key in self.retired_by_key:
            return {"status": "retired", "input": raw, "canonical": self.retired_by_key[key]["canonical"]}
        tn_only = TN_ONLY_RE.match(normalize(raw))
        if tn_only:
            rows = [r for r in self.by_tn.get(int(tn_only.group(1)), []) if r.get("status", "active") == "active"]
            if len(rows) == 1:
                return {"status": "known", "input": raw, "canonical": rows[0]["canonical"], "by_number": True}
            if len(rows) > 1:
                return {"status": "ambiguous", "input": raw, "candidates": [r["canonical"] for r in rows]}
            return {"status": "bad_format", "input": raw}
        s = shape(raw)
        if not s:
            # colloquial text: a unique product whose key contains the text's key
            partial = key if len(key) >= 4 else ""
            hits = [r for k, r in self.by_key.items() if partial and partial in k]
            hits = list({r["canonical"]: r for r in hits}.values())
            if len(hits) == 1:
                return {"status": "known", "input": raw, "canonical": hits[0]["canonical"], "by_text": True}
            if len(hits) > 1:
                return {"status": "ambiguous", "input": raw, "candidates": [r["canonical"] for r in hits][:8]}
            return {"status": "bad_format", "input": raw}
        number, _ = s
        proposal = canonical_form(raw)
        near = sorted({r["canonical"] for k, r in self.by_key.items() if _lev(key, k) <= 2})
        same_tn = [r["canonical"] for r in self.by_tn.get(number, [])]
        if same_tn:
            return {"status": "blocked_same_tn", "input": raw, "proposal": proposal, "same_tn": same_tn, "near": near}
        return {"status": "new", "input": raw, "proposal": proposal, "near": near}


def load(conn=None) -> Registry:
    sql = "SELECT id, canonical, match_key, tn_number, aliases, status, note, source, created_by, created_at FROM product_registry ORDER BY tn_number, canonical"
    rows = db.jsonable(db.fetch_all(conn, sql)) if conn is not None else db.rows(sql)
    return Registry(rows)


def listing(conn=None) -> list[dict]:
    """Every product with usage counts (for the Products page)."""
    sql = """
        SELECT p.id, p.canonical, p.tn_number, p.aliases, p.status, p.note, p.source, p.created_by, p.created_at,
               COALESCE(u.units, 0) AS units, u.last_test, COALESCE(u.work_orders, 0) AS work_orders
        FROM product_registry p
        LEFT JOIN (
            SELECT product_model, COUNT(*) AS units, MAX(test_time) AS last_test, COUNT(DISTINCT work_order) AS work_orders
            FROM mv_unit GROUP BY product_model
        ) u ON u.product_model = p.canonical
        ORDER BY p.status, p.tn_number, p.canonical
    """
    return db.jsonable(db.fetch_all(conn, sql)) if conn is not None else db.rows(sql)


def add(conn, canonical: str, created_by: str, source: str = "web", note: Optional[str] = None,
        upload_id: Optional[str] = None) -> dict:
    s = shape(canonical)
    if not s:
        raise ValueError("bad_format")
    key = match_key(canonical)
    existing = db.fetch_one(conn, "SELECT canonical, status FROM product_registry WHERE match_key = %s", (key,))
    if existing:      # never re-activates a retired product: only the administrator does that
        return {"canonical": existing["canonical"], "created": False}
    db.execute(conn, """
        INSERT INTO product_registry (canonical, match_key, tn_number, status, source, note, created_by, created_upload_id)
        VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)
    """, (canonical, key, s[0], source, note, created_by, upload_id))
    return {"canonical": canonical, "created": True}


def seed_from_data(cur) -> int:
    """First run: every product_model already in test_record becomes a registry entry."""
    cur.execute("SELECT DISTINCT product_model FROM test_record WHERE product_model IS NOT NULL AND product_model <> ''")
    added = 0
    for row in cur.fetchall():
        name = row["product_model"]
        s = shape(name)
        cur.execute("""
            INSERT INTO product_registry (canonical, match_key, tn_number, status, source, created_by)
            VALUES (%s, %s, %s, 'active', 'seed', 'migration')
            ON CONFLICT (match_key) DO NOTHING
        """, (name, match_key(name), s[0] if s else None))
        added += cur.rowcount
    return added
