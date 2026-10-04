"""A small, self-contained demo CRM backed by SQLite.

All data is fictional: names are invented, emails use the reserved
example.com domain, and phone numbers come from the ranges the Australian
regulator (ACMA) sets aside for fiction.
"""

from __future__ import annotations

import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import duplicates

STATUSES = ["New", "Contacted", "Qualified", "Quote Sent", "Won", "Lost"]
CLOSED = {"Won", "Lost"}
SOURCES = ["Website form", "Email enquiry", "Phone", "Event", "Referral"]
INTERESTS = ["Product demo", "Pricing enquiry", "Partnership", "Consultation"]
STATES = ["VIC", "NSW", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

MAX_TEXT = 200
MAX_NOTE = 2000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS salespeople (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    states TEXT NOT NULL,          -- comma-separated territory list
    capacity INTEGER NOT NULL      -- comfortable number of open leads
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    postcode TEXT,
    state TEXT,
    source TEXT,
    interest TEXT,
    status TEXT NOT NULL DEFAULT 'New',
    owner_id INTEGER REFERENCES salespeople(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    lead_id INTEGER,
    detail TEXT
);
"""

# Fictional mobile numbers reserved by ACMA for use in drama and examples.
_FICTIONAL_PHONES = [
    "0491 570 006", "0491 570 156", "0491 570 157", "0491 570 158", "0491 570 159",
    "0491 570 110", "0491 570 313", "0491 570 737", "0491 571 266", "0491 571 491",
    "0491 571 804", "0491 572 549", "0491 572 665", "0491 572 983", "0491 573 770",
    "0491 573 087", "0491 574 118", "0491 574 632", "0491 575 254", "0491 575 789",
    "0491 576 398", "0491 576 801", "0491 577 426", "0491 577 644", "0491 578 957",
    "0491 578 148", "0491 578 888", "0491 579 212", "0491 579 760", "0491 579 455",
]
_POSTCODES = {
    "VIC": ["3000", "3121", "3150", "3220", "3350"],
    "NSW": ["2000", "2150", "2500", "2800"],
    "QLD": ["4000", "4217", "4870"],
    "WA": ["6000", "6160"],
    "SA": ["5000", "5108"],
    "TAS": ["7000", "7250"],
    "ACT": ["2600"],
}
_FIRST = ["Olivia", "Liam", "Charlotte", "Noah", "Amelia", "Jack", "Isla", "William", "Mia", "Henry",
          "Ava", "Leo", "Grace", "Lucas", "Chloe", "Thomas", "Zara", "Oscar", "Ruby", "Ethan",
          "Priya", "Arjun", "Mei", "Kenji", "Fatima", "Omar", "Sofia", "Mateo", "Hannah", "Samuel"]
_LAST = ["Nguyen", "Smith", "Patel", "Wilson", "Brown", "Taylor", "Kelly", "Martin", "Singh", "Chen",
         "Anderson", "White", "Harris", "Walker", "Khan", "Lee", "Thompson", "Rossi", "Murphy", "Ali"]
_SALES = [
    ("Alex Morgan", "VIC,TAS", 12),
    ("Jordan Lee", "VIC", 10),
    ("Sam Patel", "NSW,ACT", 12),
    ("Taylor Brooks", "QLD,NT", 10),
    ("Casey Ward", "WA,SA", 10),
    ("Riley Chen", "NSW,QLD", 8),
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: str | None, field: str, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError(f"{field} is too long (max {limit} characters)")
    return value or None


class CRM:
    def __init__(self, path: str | Path, *, read_only: bool = False, seed: bool = True):
        self.path = str(path)
        self.read_only = read_only
        is_new = self.path == ":memory:" or not Path(self.path).exists()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        if is_new and seed:
            self._seed()

    # ------------------------------------------------------------------ helpers
    def _guard_write(self) -> None:
        if self.read_only:
            raise PermissionError("The CRM is in read-only mode (CRM_MCP_READ_ONLY=1). No changes were made.")

    def _audit(self, action: str, lead_id: int | None, detail: str) -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, action, lead_id, detail) VALUES (?, ?, ?, ?)",
            (_now(), action, lead_id, detail),
        )

    def _lead_row(self, lead_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
        if row is None:
            raise ValueError(f"No lead with id {lead_id}")
        return row

    def _lead_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        owner = None
        if d.get("owner_id"):
            o = self.db.execute("SELECT name FROM salespeople WHERE id = ?", (d["owner_id"],)).fetchone()
            owner = o["name"] if o else None
        d["owner"] = owner
        return d

    def _all_leads(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM leads")]

    # --------------------------------------------------------------------- seed
    def _seed(self) -> None:
        rnd = random.Random(42)
        self.db.executemany("INSERT INTO salespeople (name, states, capacity) VALUES (?, ?, ?)", _SALES)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        phones = list(_FICTIONAL_PHONES)
        rnd.shuffle(phones)

        rows = []
        for i in range(36):
            first, last = rnd.choice(_FIRST), rnd.choice(_LAST)
            state = rnd.choices(list(_POSTCODES), weights=[6, 5, 4, 2, 2, 1, 1])[0]
            created = now - timedelta(days=rnd.randint(1, 90), hours=rnd.randint(0, 23))
            status = rnd.choices(STATUSES, weights=[8, 6, 4, 3, 3, 2])[0]
            rows.append({
                "first_name": first, "last_name": last,
                "email": f"{first}.{last}{i}@example.com".lower(),
                "phone": phones[i] if i < len(phones) and rnd.random() > 0.15 else None,
                "postcode": rnd.choice(_POSTCODES[state]), "state": state,
                "source": rnd.choice(SOURCES), "interest": rnd.choice(INTERESTS),
                "status": status, "created": created,
            })

        # Deliberate duplicates so the detection has something real to find.
        a, b, c, d = rows[2], rows[5], rows[9], rows[14]
        used = {r["phone"] for r in rows if r["phone"]}
        b["phone"] = b["phone"] or next(p for p in phones if p not in used)
        rows.append({**a, "email": a["email"].upper(), "phone": None, "source": "Event"})          # email, different case
        rows.append({**b, "email": None, "phone": "+61 " + b["phone"][1:],
                     "source": "Phone"})                                                          # phone, different format
        c["first_name"] = "Sophia"
        rows.append({**c, "first_name": "Sofia", "email": None, "phone": None,
                     "source": "Website form"})                                                  # similar name, same postcode
        d["state"], d["postcode"] = "VIC", "3121"
        rows.append({**d, "email": None, "phone": None, "postcode": "3150",
                     "source": "Referral"})                                                      # same name, same state
        for dup in rows[-4:]:
            dup["status"] = "New"
            dup["created"] = now - timedelta(hours=rnd.randint(1, 30))

        sales = self.db.execute("SELECT id, states FROM salespeople").fetchall()
        for r in rows:
            owners = [s["id"] for s in sales if r["state"] in s["states"].split(",")]
            owner = rnd.choice(owners) if owners and r["status"] != "New" else None
            ts = r["created"].isoformat()
            cur = self.db.execute(
                """INSERT INTO leads (first_name, last_name, email, phone, postcode, state, source,
                                      interest, status, owner_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["first_name"], r["last_name"], r["email"], r["phone"], r["postcode"], r["state"],
                 r["source"], r["interest"], r["status"], owner, ts, ts),
            )
            if r["status"] in ("Contacted", "Qualified", "Quote Sent") and rnd.random() > 0.4:
                self.db.execute(
                    "INSERT INTO notes (lead_id, body, created_at) VALUES (?, ?, ?)",
                    (cur.lastrowid, rnd.choice([
                        "Called, asked for a pricing breakdown.",
                        "Prefers email contact after 5pm.",
                        "Interested, comparing with two other providers.",
                        "Booked a follow-up call for next week.",
                    ]), ts),
                )
        self.db.commit()

    # -------------------------------------------------------------------- reads
    def search_leads(self, query: str | None = None, status: str | None = None, state: str | None = None,
                     owner: str | None = None, limit: int = 20) -> dict:
        sql = ["SELECT l.* FROM leads l LEFT JOIN salespeople s ON s.id = l.owner_id WHERE 1=1"]
        args: list = []
        if query:
            q = f"%{_clean(query, 'query')}%"
            sql.append("AND (l.first_name || ' ' || l.last_name LIKE ? OR l.email LIKE ? OR l.phone LIKE ? OR l.postcode LIKE ?)")
            args += [q, q, q, q]
        if status:
            if status not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            sql.append("AND l.status = ?"); args.append(status)
        if state:
            state = state.upper()
            if state not in STATES:
                raise ValueError(f"state must be one of {STATES}")
            sql.append("AND l.state = ?"); args.append(state)
        if owner:
            if owner.lower() == "unassigned":
                sql.append("AND l.owner_id IS NULL")
            else:
                sql.append("AND s.name LIKE ?"); args.append(f"%{_clean(owner, 'owner')}%")
        limit = max(1, min(int(limit), 100))
        sql.append("ORDER BY l.created_at DESC LIMIT ?"); args.append(limit)
        rows = self.db.execute(" ".join(sql), args).fetchall()
        return {"count": len(rows), "leads": [self._lead_dict(r) for r in rows]}

    def get_lead(self, lead_id: int) -> dict:
        lead = self._lead_dict(self._lead_row(lead_id))
        lead["notes"] = [dict(n) for n in self.db.execute(
            "SELECT body, created_at FROM notes WHERE lead_id = ? ORDER BY created_at", (int(lead_id),))]
        return lead

    def find_duplicates(self, lead_id: int) -> dict:
        lead = dict(self._lead_row(lead_id))
        matches = duplicates.check(lead, self._all_leads(), exclude_id=lead["id"])
        return {"lead_id": lead["id"], "possible_duplicates": [m.as_dict() for m in matches]}

    def recommend_salesperson(self, lead_id: int) -> dict:
        lead = self._lead_row(lead_id)
        rows = self.db.execute(
            f"""SELECT s.id, s.name, s.states, s.capacity,
                       COUNT(l.id) FILTER (WHERE l.status NOT IN ({','.join('?' * len(CLOSED))})) AS open_leads
                FROM salespeople s LEFT JOIN leads l ON l.owner_id = s.id
                GROUP BY s.id""",
            list(CLOSED),
        ).fetchall()
        in_territory = [r for r in rows if lead["state"] in r["states"].split(",")]
        pool = in_territory or rows
        ranked = sorted(pool, key=lambda r: (r["open_leads"] / r["capacity"], r["open_leads"]))
        return {
            "lead_id": lead["id"],
            "lead_state": lead["state"],
            "territory_match": bool(in_territory),
            "recommendations": [
                {
                    "salesperson_id": r["id"],
                    "name": r["name"],
                    "territories": r["states"],
                    "open_leads": r["open_leads"],
                    "capacity": r["capacity"],
                    "reason": (f"Covers {lead['state']}; " if in_territory else "No one covers this state; ")
                              + f"workload {r['open_leads']}/{r['capacity']} open leads",
                }
                for r in ranked
            ],
        }

    def pipeline_summary(self, state: str | None = None) -> dict:
        where, args = "", []
        if state:
            state = state.upper()
            if state not in STATES:
                raise ValueError(f"state must be one of {STATES}")
            where, args = "WHERE state = ?", [state]
        by_status = {s: 0 for s in STATUSES}
        for r in self.db.execute(f"SELECT status, COUNT(*) n FROM leads {where} GROUP BY status", args):
            by_status[r["status"]] = r["n"]
        by_source = {r["source"]: r["n"] for r in self.db.execute(
            f"SELECT source, COUNT(*) n FROM leads {where} GROUP BY source ORDER BY n DESC", args)}
        total = sum(by_status.values())
        closed = by_status["Won"] + by_status["Lost"]
        unassigned = self.db.execute(
            f"SELECT COUNT(*) FROM leads {where + (' AND' if where else 'WHERE')} owner_id IS NULL "
            f"AND status NOT IN ('Won','Lost')", args).fetchone()[0]
        return {
            "scope": state or "All states",
            "total_leads": total,
            "by_status": by_status,
            "by_source": by_source,
            "open_unassigned": unassigned,
            "win_rate_of_closed": round(by_status["Won"] / closed, 2) if closed else None,
        }

    def recent_activity(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT at, action, lead_id, detail FROM audit_log ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 100)),))]

    # ------------------------------------------------------------------- writes
    def create_lead(self, first_name: str, last_name: str, email: str | None = None, phone: str | None = None,
                    postcode: str | None = None, state: str | None = None, source: str | None = None,
                    interest: str | None = None, allow_duplicate: bool = False) -> dict:
        self._guard_write()
        lead = {
            "first_name": _clean(first_name, "first_name"),
            "last_name": _clean(last_name, "last_name"),
            "email": _clean(email, "email"),
            "phone": _clean(phone, "phone", 30),
            "postcode": _clean(postcode, "postcode", 4),
            "state": (_clean(state, "state", 3) or "").upper() or None,
            "source": _clean(source, "source"),
            "interest": _clean(interest, "interest"),
        }
        if not lead["first_name"] or not lead["last_name"]:
            raise ValueError("first_name and last_name are required")
        if lead["email"] and not _EMAIL_RE.match(lead["email"]):
            raise ValueError("email does not look valid")
        if lead["postcode"] and not re.fullmatch(r"\d{4}", lead["postcode"]):
            raise ValueError("postcode must be 4 digits")
        if lead["state"] and lead["state"] not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        if not (lead["email"] or lead["phone"]):
            raise ValueError("Provide at least an email or a phone number so the lead can be contacted")

        matches = duplicates.check(lead, self._all_leads())
        strong = [m for m in matches if m.confidence == duplicates.HIGH]
        if strong and not allow_duplicate:
            return {
                "created": False,
                "message": "Not created: this looks like an existing lead. Review the matches, then call "
                           "create_lead again with allow_duplicate=true if it really is a new person.",
                "possible_duplicates": [m.as_dict() for m in matches],
            }

        ts = _now()
        cur = self.db.execute(
            """INSERT INTO leads (first_name, last_name, email, phone, postcode, state, source, interest,
                                  status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?, 'New', ?, ?)""",
            (*lead.values(), ts, ts),
        )
        self._audit("create_lead", cur.lastrowid,
                    "created despite duplicate warning" if strong else "created")
        self.db.commit()
        return {
            "created": True,
            "lead": self.get_lead(cur.lastrowid),
            "possible_duplicates": [m.as_dict() for m in matches],
        }

    def update_status(self, lead_id: int, status: str) -> dict:
        self._guard_write()
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        old = self._lead_row(lead_id)["status"]
        self.db.execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), int(lead_id)))
        self._audit("update_status", int(lead_id), f"{old} -> {status}")
        self.db.commit()
        return {"lead_id": int(lead_id), "previous_status": old, "status": status}

    def add_note(self, lead_id: int, note: str) -> dict:
        self._guard_write()
        self._lead_row(lead_id)
        body = _clean(note, "note", MAX_NOTE)
        if not body:
            raise ValueError("note cannot be empty")
        self.db.execute("INSERT INTO notes (lead_id, body, created_at) VALUES (?, ?, ?)", (int(lead_id), body, _now()))
        self._audit("add_note", int(lead_id), body[:80])
        self.db.commit()
        return {"lead_id": int(lead_id), "note_added": body}

    def assign_lead(self, lead_id: int, salesperson_id: int) -> dict:
        self._guard_write()
        self._lead_row(lead_id)
        sp = self.db.execute("SELECT id, name FROM salespeople WHERE id = ?", (int(salesperson_id),)).fetchone()
        if sp is None:
            raise ValueError(f"No salesperson with id {salesperson_id}")
        self.db.execute("UPDATE leads SET owner_id = ?, updated_at = ? WHERE id = ?",
                        (sp["id"], _now(), int(lead_id)))
        self._audit("assign_lead", int(lead_id), f"assigned to {sp['name']}")
        self.db.commit()
        return {"lead_id": int(lead_id), "assigned_to": sp["name"]}
