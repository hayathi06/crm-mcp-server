"""Duplicate-lead detection.

Each rule compares a candidate lead with an existing record and, when it
matches, explains *why*. Results are never a bare yes/no: staff (or an AI
assistant) get the reasons and a confidence level so they can act on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}


def normalise_email(email: str | None) -> str:
    return (email or "").strip().lower()


def normalise_phone(phone: str | None) -> str:
    """Reduce an Australian phone number to its local digits.

    '+61 491 570 156', '0491-570-156' and '(04) 9157 0156' all become
    '0491570156'.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits


def normalise_name(first: str | None, last: str | None) -> str:
    return re.sub(r"\s+", " ", f"{first or ''} {last or ''}").strip().lower()


def name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Match:
    lead_id: int
    name: str
    confidence: str
    reasons: list[str]

    def as_dict(self) -> dict:
        return {
            "lead_id": self.lead_id,
            "name": self.name,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }


def check(candidate: dict, existing: list[dict], *, exclude_id: int | None = None) -> list[Match]:
    """Compare one candidate lead against existing leads.

    ``candidate`` and each item of ``existing`` use the CRM field names
    (first_name, last_name, email, phone, postcode, state). ``exclude_id``
    stops a lead from ever being matched against itself.
    """
    c_email = normalise_email(candidate.get("email"))
    c_phone = normalise_phone(candidate.get("phone"))
    c_name = normalise_name(candidate.get("first_name"), candidate.get("last_name"))
    c_post = (candidate.get("postcode") or "").strip()
    c_state = (candidate.get("state") or "").strip().upper()

    matches: list[Match] = []
    for row in existing:
        if exclude_id is not None and row["id"] == exclude_id:
            continue

        reasons: list[tuple[str, str]] = []
        r_name = normalise_name(row["first_name"], row["last_name"])

        if c_email and c_email == normalise_email(row["email"]):
            reasons.append((HIGH, "Same email address"))
        if len(c_phone) >= 8 and c_phone == normalise_phone(row["phone"]):
            reasons.append((HIGH, "Same phone number (after removing formatting)"))
        if c_name and c_name == r_name and c_post and c_post == row["postcode"]:
            reasons.append((HIGH, "Same full name and postcode"))
        else:
            sim = name_similarity(c_name, r_name)
            if sim >= 0.85 and c_post and c_post == row["postcode"]:
                reasons.append((MEDIUM, f"Very similar name ({sim:.0%}) in the same postcode"))
            elif c_name and c_name == r_name and c_state and c_state == (row["state"] or "").upper():
                reasons.append((LOW, "Same full name in the same state"))

        if reasons:
            best = max((r[0] for r in reasons), key=_RANK.__getitem__)
            matches.append(
                Match(
                    lead_id=row["id"],
                    name=f"{row['first_name']} {row['last_name']}".strip(),
                    confidence=best,
                    reasons=[r[1] for r in reasons],
                )
            )

    matches.sort(key=lambda m: (-_RANK[m.confidence], m.lead_id))
    return matches
