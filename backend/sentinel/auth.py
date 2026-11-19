"""Demo authentication and role-based authorisation.

The role switcher in the UI signs in as one of three demo users and receives a server-issued
bearer token. Every API call derives the role from that token on the server; the client never
sends its role, so changing the UI cannot widen access. This is a prototype: there are no
passwords and tokens are HMAC-signed and stateless so they work across serverless instances.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, Header, HTTPException

from . import config as C

USERS = {
    "pho-north": {"id": "pho-north", "name": "Demo user: District Public Health Officer", "role": "public_health_officer"},
    "hse-emp-a": {"id": "hse-emp-a", "name": "Demo user: HSE Safety Lead, Employer A", "role": "hse_lead", "employer": "EMP_A"},
    "policy-analyst": {"id": "policy-analyst", "name": "Demo user: Policy & Regulatory Analyst", "role": "policy_analyst"},
}

# What each role may see. Enforced in api.py through the helpers below.
PERMISSIONS = {
    "public_health_officer": {
        "zone_scope": "all",
        "zone_signals": list(C.SIGNAL_IDS),
        "district_series": True,
        "site_scope": "all",
        "cohort_drilldown": True,
        "decision_statuses": ["acknowledged", "action_planned", "not_actionable"],
        "exports": ["zone_daily", "sites"],
        "summary": "All zones; environmental, clinical and workplace aggregates; site-level aggregates; no person-level data.",
    },
    "hse_lead": {
        "zone_scope": "employer",
        "zone_signals": list(C.ENV_SIGNALS),
        "district_series": False,
        "site_scope": "employer",
        "cohort_drilldown": True,
        "decision_statuses": ["acknowledged", "action_planned", "not_actionable"],
        "exports": ["sites"],
        "summary": "Only zones where the employer has sites; environmental data and the employer's own site aggregates. Clinic data appear only as alert contribution shares, never as counts.",
    },
    "policy_analyst": {
        "zone_scope": "all",
        "zone_signals": list(C.ENV_SIGNALS),
        "district_series": True,
        "site_scope": "none",
        "cohort_drilldown": False,
        "decision_statuses": ["noted_for_review"],
        "exports": ["zone_daily"],
        "summary": "All zones' alert status and environmental data; clinical and workplace counts at district level only; no site or cohort views.",
    },
}

SESSION_TTL_SECONDS = 12 * 3600
_EPHEMERAL_SECRET = secrets.token_bytes(32)


def hosted() -> bool:
    """True on Vercel (or when SENTINEL_HOSTED=1): several stateless function instances serve requests."""
    return bool(os.environ.get("VERCEL")) or os.environ.get("SENTINEL_HOSTED") == "1"


def _secret() -> bytes:
    """Signing key for session tokens. Tokens are stateless (HMAC-signed), so any function instance can verify
    them. Hosted deployments MUST set SENTINEL_SESSION_SECRET; there is deliberately no built-in fallback there,
    because a key known to the source code would let anyone mint a token for any role. Locally an ephemeral
    per-process key is used when the variable is unset."""
    env = os.environ.get("SENTINEL_SESSION_SECRET", "").strip()
    if len(env) >= 16:
        return env.encode()
    if hosted():
        raise HTTPException(503, "Demo sign-in is not configured: set the SENTINEL_SESSION_SECRET environment variable (at least 16 characters).")
    return _EPHEMERAL_SECRET


def session_configured() -> bool:
    try:
        _secret()
        return True
    except HTTPException:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def issue_token(user_id: str) -> str:
    if user_id not in USERS:
        raise HTTPException(404, "Unknown demo user")
    body = _b64(json.dumps({"u": user_id, "exp": int(time.time()) + SESSION_TTL_SECONDS}, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def _verify(token: str) -> str | None:
    """Return the user id for a valid, unexpired token, else None. The role is always looked up server-side."""
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        claims = json.loads(_unb64(body))
        if int(claims["exp"]) < time.time():
            return None
        return claims["u"] if claims["u"] in USERS else None
    except HTTPException:
        raise
    except Exception:
        return None


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required")
    user_id = _verify(authorization.split(" ", 1)[1].strip())
    if not user_id:
        raise HTTPException(401, "Session expired; sign in again")
    user = USERS[user_id]
    return {**user, "permissions": PERMISSIONS[user["role"]]}


CurrentUser = Depends(current_user)


def zones_in_scope(user: dict) -> list[str]:
    if user["permissions"]["zone_scope"] == "all":
        return list(C.ZONE_IDS)
    emp = user.get("employer")
    return sorted({s["zone"] for s in C.SITES if s["employer"] == emp}, key=C.ZONE_IDS.index)


def sites_in_scope(user: dict) -> list[str]:
    scope = user["permissions"]["site_scope"]
    if scope == "all":
        return list(C.SITE_IDS)
    if scope == "employer":
        return [s["id"] for s in C.SITES if s["employer"] == user.get("employer")]
    return []


def require_zone(user: dict, zone: str) -> None:
    if zone not in C.ZONE_INDEX:
        raise HTTPException(404, "Unknown zone")
    if zone not in zones_in_scope(user):
        raise HTTPException(403, "This zone is outside your role's scope")


def require_site(user: dict, site: str) -> None:
    if site not in C.SITE_INDEX:
        raise HTTPException(404, "Unknown site")
    if site not in sites_in_scope(user):
        raise HTTPException(403, "This site is outside your role's scope")
