"""Auth helpers (all env vars are read at call time so they can change without re-importing).

Two kinds of callers:
  * STAFF  - X-Staff-Token header. Either one shared STAFF_TOKEN (legacy) or per-person tokens in
             STAFF_TOKENS="asha:tok1,ravi:tok2". A named token means the audit log records WHO acted
             (taken from the token, not from whatever the request body claims).
  * FILER  - X-Case-Token header. Returned ONCE when a case is filed; lets the filer read only their own case.

Dev mode: if no staff token is configured at all, everything is open (as in v0.2).
Reads are protected by default whenever a staff token exists; set PROTECT_READS=0 to switch that off.
"""
import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException


def _staff_map() -> dict[str, str]:
    """token -> staff name"""
    out: dict[str, str] = {}
    for part in os.getenv("STAFF_TOKENS", "").split(","):
        if ":" in part:
            name, tok = part.split(":", 1)
            if name.strip() and tok.strip():
                out[tok.strip()] = name.strip()
    legacy = os.getenv("STAFF_TOKEN", "")
    if legacy:
        out.setdefault(legacy, "staff")
    return out


def auth_configured() -> bool:
    return bool(_staff_map())


def protect_reads() -> bool:
    v = os.getenv("PROTECT_READS")
    if v is not None and v.strip() != "":
        return v.strip().lower() not in ("0", "false", "no", "off")
    return auth_configured()


def staff_name(token: Optional[str]) -> Optional[str]:
    """Name of the staff member this token belongs to, or None. Dev mode => 'dev-staff'."""
    m = _staff_map()
    if not m:
        return "dev-staff"
    presented = (token or "").encode()
    found = None
    for tok, name in m.items():  # compare against all (no early exit) to keep timing flat
        if hmac.compare_digest(presented, tok.encode()):
            found = name
    return found


def is_staff(token: Optional[str]) -> bool:
    return staff_name(token) is not None


def require_staff(x_staff_token: Optional[str] = Header(default=None)) -> str:
    name = staff_name(x_staff_token)
    if name is None:
        raise HTTPException(401, "Invalid or missing X-Staff-Token")
    return name


def caller_is_staff(x_staff_token: Optional[str] = Header(default=None)) -> bool:
    return is_staff(x_staff_token)


# ---------------------------------------------------------------- per-case filer tokens
def new_case_token() -> tuple[str, str]:
    """(plain token to give the filer once, sha256 to store)"""
    plain = secrets.token_urlsafe(24)
    return plain, hash_token(plain)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def can_access_case(case, x_staff_token: Optional[str], x_case_token: Optional[str]) -> bool:
    if not protect_reads():
        return True
    if is_staff(x_staff_token):
        return True
    stored = getattr(case, "access_token_hash", None)
    if stored and x_case_token:
        return hmac.compare_digest(hash_token(x_case_token), stored)
    return False


def check_case_access(case, x_staff_token: Optional[str], x_case_token: Optional[str]) -> None:
    if not can_access_case(case, x_staff_token, x_case_token):
        # 404 (not 403) so outsiders can't probe which case ids exist
        raise HTTPException(404, "Case not found")


def require_staff_for_listing(x_staff_token: Optional[str]) -> None:
    if protect_reads() and not is_staff(x_staff_token):
        raise HTTPException(401, "Staff token required to list records")


# ---------------------------------------------------------------- who is filing the case
SOURCES = ("user", "judiciary", "ai")


def ai_token_ok(token: Optional[str]) -> bool:
    expected = os.getenv("AI_TOKEN", "")
    return bool(expected and token and hmac.compare_digest(token.encode(), expected.encode()))


def authorize_source(source: str, staff_token: Optional[str], ai_token: Optional[str]) -> Optional[str]:
    """Checks the caller may file as `source`; returns the acting name (None for the public).
      user      - anybody (the public)
      judiciary - needs a staff token (X-Staff-Token)
      ai        - needs the AI service token (X-AI-Token, env AI_TOKEN) or a staff token
    Dev mode (no tokens configured) lets everything through."""
    if source not in SOURCES:
        raise HTTPException(422, f"source must be one of {', '.join(SOURCES)}")
    if source == "user":
        return None
    who = staff_name(staff_token)
    if source == "judiciary":
        if who is None:
            raise HTTPException(401, "Filing as 'judiciary' needs a valid X-Staff-Token")
        return who
    if ai_token_ok(ai_token):
        return "ai-service"
    if who is not None:
        return who
    raise HTTPException(401, "Filing as 'ai' needs X-AI-Token (or a staff token)")
