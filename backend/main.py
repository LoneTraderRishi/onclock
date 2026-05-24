import os
import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Query, Header, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import hashlib
import secrets

from dotenv import load_dotenv

from database import get_db, validate_env
from models import (
    CyberSessionStart, CyberSessionEnd, CyberPlayStationCreate,
    CyberAdvanceBooking
)

load_dotenv()

# Validate environment at startup
validate_env()

app = FastAPI(title="Onclock — Station Management API")

# CORS — restrict to known origins
ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip CSP for API endpoints — they return JSON, not HTML
        is_api = request.url.path.startswith("/api/")
        is_static = any(request.url.path.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff2"])

        response = await call_next(request)

        # Security headers (always)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Cache-Control: prevent edge CDN caching for HTML & API
        if not is_static:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        # CSP — only on HTML pages (API endpoints don't need it)
        if not is_api:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://*.supabase.co; "
                "media-src 'self' https:; "
                "frame-src 'none'; "
                "object-src 'none'"
            )

        return response

app.add_middleware(SecurityHeadersMiddleware)


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Fine-grained Cache-Control headers based on URL path.

    - API endpoints (/api/*) → never cache
    - Service worker (/sw.js) → always fresh
    - HTML pages → short cache with revalidation
    - Static assets (images, manifest) → long cache, immutable
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path

        # Service worker — MUST always be fresh
        if path == "/sw.js":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        # API endpoints — live data, never cache
        elif "api" in path:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        # Static assets — rarely change
        elif any(path.endswith(ext) for ext in [".jpg", ".svg", ".json", ".ico"]):
            response.headers["Cache-Control"] = "public, max-age=3600, immutable"
        # HTML pages & known routes — short cache with revalidation
        elif (path.endswith(".html")
              or path in ["/", "/cyber", "/cyber/"]
              or "/track" in path
              or "/qr" in path):
            response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"

        return response


app.add_middleware(CacheControlMiddleware)

# Paths
BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR / "frontend"

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Cyber Cafe")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")
HOURLY_RATE = float(os.getenv("HOURLY_RATE", "50"))

# ─── Password Hashing ───────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password with random salt using SHA-256."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"

def check_password(password: str, stored: str) -> bool:
    """Verify password against stored hash. Handles legacy plaintext passwords."""
    if not stored or "$" not in stored:
        return password == stored  # legacy plaintext comparison
    salt, h = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


# ─── Password Change Model ─────────────────────────────────────────

class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


# ─── Helper Functions ───────────────────────────────────────────────

def parse_timestamp(ts_str):
    """Parse ISO timestamp, handling various formats from Supabase. Returns timezone-aware UTC datetime."""
    if not ts_str:
        return None
    ts = ts_str.replace("Z", "+00:00")
    if '.' in ts:
        base, tz = ts.split('+', 1)
        parts = base.rsplit('.', 1)
        micros = parts[1][:6].ljust(6, '0')
        ts = parts[0] + '.' + micros + '+' + tz
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        # Assume naive timestamps are UTC (legacy or from utcnow)
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)  # Convert to UTC


# ─── Cyber Dashboard Auth ────────────────────────────────────────

def get_cyber_password_from_request(
    password: str = Query(""),
    authorization: str = Header(None),
) -> str:
    """Extract password from Authorization Bearer header (preferred) or query param (deprecated fallback)."""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    if password:
        return password
    raise HTTPException(status_code=401, detail="Authentication required")

def verify_cyber_dashboard(pw: str = Depends(get_cyber_password_from_request)):
    """Verify cyber dashboard password."""
    correct = os.getenv("DASHBOARD_PASSWORD", "changeme")
    try:
        db = get_db()
        res = db.table("admin_settings").select("value").eq("key", "cyber_dashboard_password").maybe_single().execute()
        if res.data and res.data.get("value"):
            correct = res.data["value"]
    except Exception:
        pass
    if not check_password(pw, correct):
        raise HTTPException(status_code=401, detail="Wrong password")

require_cyber = Depends(verify_cyber_dashboard)


# ─── Cyber Frontend Routes ──────────────────────────────────────

@app.get("/cyber/track")
async def serve_cyber_track():
    """Serve the cyber session tracking page."""
    return FileResponse(FRONTEND_DIR / "cyber-track.html")


@app.get("/cyber", response_class=HTMLResponse)
@app.get("/cyber/", response_class=HTMLResponse, include_in_schema=False)
async def serve_cyber_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/cyber/ps/{ps_number}", response_class=HTMLResponse)
async def serve_cyber_ps_number(ps_number: str):
    try:
        n = int(ps_number)
        if n < 1 or n > 4:
            return FileResponse(FRONTEND_DIR / "index.html")
        return FileResponse(FRONTEND_DIR / "ps-menu.html")
    except (ValueError, TypeError):
        return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/cyber/dashboard", response_class=HTMLResponse)
async def serve_cyber_dashboard():
    return FileResponse(FRONTEND_DIR / "cyber-dashboard.html")


@app.get("/cyber/customers", response_class=HTMLResponse)
async def serve_cyber_customers():
    return FileResponse(FRONTEND_DIR / "cyber-customers.html")


@app.get("/cyber/qr-codes", response_class=HTMLResponse)
async def serve_cyber_qr_codes():
    return FileResponse(FRONTEND_DIR / "cyber-qr.html")


@app.get("/cyber/qr", response_class=RedirectResponse)
async def redirect_cyber_qr():
    return RedirectResponse(url="/cyber/qr-codes")


@app.get("/qr", response_class=RedirectResponse)
async def redirect_qr():
    return RedirectResponse(url="/cyber/qr-codes")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/onclock-logo.jpg")
async def onclock_logo():
    f = FRONTEND_DIR / "onclock-logo.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


@app.get("/upi-qr.jpg")
async def upi_qr():
    f = FRONTEND_DIR / "upi-qr.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


# ═══════════════════════════════════════════════════════════════════════
# CYBER CAFE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@app.get("/api/cyber/playstations")
async def list_playstations():
    """List all active playstations with current session info."""
    _auto_expire_sessions()
    db = get_db()
    ps_list = db.table("playstations").select("*").eq("is_active", True).order("playstation_number").execute()

    # Batch-load all active sessions to avoid N+1
    ps_ids = [ps["id"] for ps in (ps_list.data or [])]
    sessions_map = {}
    if ps_ids:
        all_active = db.table("sessions").select("*").eq("status", "active").in_("playstation_id", ps_ids).execute()
        for s in (all_active.data or []):
            sessions_map[s["playstation_id"]] = s

    result = []
    for ps in (ps_list.data or []):
        session = sessions_map.get(ps["id"])
        ps["current_session"] = session
        ps["status"] = "occupied" if session else "available"
        result.append(ps)

    return result


@app.post("/api/cyber/playstations")
async def create_playstation(ps: CyberPlayStationCreate, _: None = Depends(verify_cyber_dashboard)):
    """Create a new playstation."""
    db = get_db()
    data = ps.model_dump()
    data["status"] = "available"
    data["is_active"] = True
    result = db.table("playstations").insert(data).execute()
    return result.data[0]


@app.put("/api/cyber/playstations/{ps_id}")
async def update_playstation(ps_id: int, ps: CyberPlayStationCreate, _: None = Depends(verify_cyber_dashboard)):
    """Update a playstation."""
    db = get_db()
    result = db.table("playstations").update(ps.model_dump()).eq("id", ps_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="PlayStation not found")
    return result.data[0]


@app.delete("/api/cyber/playstations/{ps_id}")
async def delete_playstation(ps_id: int, _: None = Depends(verify_cyber_dashboard)):
    """Soft delete a playstation."""
    db = get_db()
    db.table("playstations").update({"is_active": False}).eq("id", ps_id).execute()
    return {"ok": True}


@app.get("/api/cyber/playstations/{ps_id}/status")
async def get_ps_status(ps_id: int):
    """Get status of a specific playstation."""
    db = get_db()
    ps = db.table("playstations").select("*").eq("id", ps_id).execute()
    if not ps.data:
        raise HTTPException(status_code=404, detail="PlayStation not found")

    session = db.table("sessions").select("*").eq("playstation_id", ps_id).eq("status", "active").execute()
    return {
        "playstation": ps.data[0],
        "session": session.data[0] if session.data else None
    }


# ─── Cyber Session Endpoints ───────────────────────────────────────

@app.post("/api/cyber/sessions/start")
async def start_session(session: CyberSessionStart):
    """Customer starts a gaming session."""
    db = get_db()

    ps = db.table("playstations").select("*").eq("id", session.playstation_id).execute()
    if not ps.data:
        raise HTTPException(status_code=404, detail="PlayStation not found")

    if ps.data[0]["status"] == "occupied":
        raise HTTPException(status_code=400, detail="PlayStation is currently in use")

    hours = float(session.hours)
    rate = ps.data[0]["hourly_rate"]
    num_players = max(1, session.num_players or 1)
    effective_rate = rate if num_players <= 1 else 125 * num_players
    total = hours * effective_rate

    start_time = datetime.now(timezone.utc)
    buffer_minutes = 5  # Free buffer for CD changes
    end_time = start_time + timedelta(hours=hours) + timedelta(minutes=buffer_minutes)

    primary_player = build_player_entry(
        session.player_name, session.player_phone,
        hours, effective_rate, start_time
    )

    session_data = {
        "playstation_id": session.playstation_id,
        "player_name": session.player_name,
        "player_phone": session.player_phone,
        "num_players": session.num_players or 1,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "hours_booked": hours,
        "rate_per_hour": effective_rate,
        "total_amount": total,
        "status": "active",
        "players": [primary_player]
    }

    result = db.table("sessions").insert(session_data).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to start session")

    db.table("playstations").update({"status": "occupied"}).eq("id", session.playstation_id).execute()

    return {"ok": True, "session_id": result.data[0]["id"], "total": total}


@app.post("/api/cyber/sessions/dashboard-start")
async def dashboard_start_session(
    playstation_id: int = Body(...),
    player_name: str = Body(default="Guest"),
    player_phone: str = Body(default=""),
    num_players: int = Body(default=1),
    hours: float = Body(...),
    _: None = Depends(verify_cyber_dashboard)
):
    """Owner starts a session from the dashboard."""
    db = get_db()

    ps = db.table("playstations").select("*").eq("id", playstation_id).execute()
    if not ps.data:
        raise HTTPException(status_code=404, detail="PlayStation not found")

    if ps.data[0]["status"] == "occupied":
        raise HTTPException(status_code=400, detail="PlayStation is currently in use")

    rate = ps.data[0]["hourly_rate"]
    effective_rate = rate if num_players <= 1 else 125 * num_players
    total = hours * effective_rate

    start_time = datetime.now(timezone.utc)
    buffer_minutes = 5
    end_time = start_time + timedelta(hours=hours) + timedelta(minutes=buffer_minutes)

    primary_player = build_player_entry(
        player_name, player_phone,
        hours, effective_rate, start_time
    )

    session_data = {
        "playstation_id": playstation_id,
        "player_name": player_name,
        "player_phone": player_phone,
        "num_players": max(1, num_players),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "hours_booked": hours,
        "rate_per_hour": effective_rate,
        "total_amount": total,
        "status": "active",
        "players": [primary_player]
    }

    result = db.table("sessions").insert(session_data).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to start session")

    db.table("playstations").update({"status": "occupied"}).eq("id", playstation_id).execute()

    return {"ok": True, "session_id": result.data[0]["id"], "total": total, "player_name": player_name}


@app.get("/api/cyber/sessions")
async def get_sessions(completed: bool = Query(False), _: None = Depends(verify_cyber_dashboard)):
    """Get all sessions (owner only). Auto-expires overdue sessions first.
    Use ?completed=true to get only completed sessions sorted by end_time.
    """
    db = get_db()
    _auto_expire_sessions()
    if completed:
        result = db.table("sessions").select("*,playstations(playstation_number,name)").neq("status", "active").order("end_time", desc=True).limit(20).execute()
    else:
        result = db.table("sessions").select("*,playstations(playstation_number,name)").order("start_time", desc=True).limit(50).execute()
    return result.data or []


@app.get("/api/cyber/sessions/active")
async def get_active_sessions(_: None = Depends(verify_cyber_dashboard)):
    """Get all active sessions (authenticated)."""
    db = get_db()
    result = db.table("sessions").select("*,playstations(playstation_number,name)").eq("status", "active").execute()
    return result.data or []


@app.get("/api/cyber/sessions/check-expired")
async def check_expired():
    """Check and mark expired sessions. Returns newly expired session IDs."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    expired = db.table("sessions").select("id,playstation_id,player_name,total_amount").eq("status", "active").execute()
    newly_expired = []

    for s in (expired.data or []):
        end_time = s.get("end_time", "")
        if end_time:
            end_dt = parse_timestamp(end_time)
            if end_dt and end_dt <= datetime.now(timezone.utc):
                newly_expired.append(s)

    for s in newly_expired:
        db.table("sessions").update({
            "end_time": now,
            "status": "completed",
            "end_reason": "Auto-expired: time limit reached"
        }).eq("id", s["id"]).execute()
        db.table("playstations").update({"status": "available"}).eq("id", s["playstation_id"]).execute()

    return {"expired": newly_expired}


def _auto_expire_sessions():
    """Check and mark expired sessions. Call before any status read."""
    db = get_db()
    expired = db.table("sessions").select("id,playstation_id,end_time").eq("status", "active").execute()
    now = datetime.now(timezone.utc)
    for s in (expired.data or []):
        end_time = s.get("end_time", "")
        if end_time:
            end_dt = parse_timestamp(end_time)
            if end_dt and end_dt <= now:
                db.table("sessions").update({
                    "status": "completed",
                    "end_reason": "Auto-expired: time limit reached"
                }).eq("id", s["id"]).execute()
                db.table("playstations").update({"status": "available"}).eq("id", s["playstation_id"]).execute()


# ─── Advance Booking (Prepaid) ──────────────────────────────────

@app.post("/api/cyber/sessions/book")
async def create_advance_booking(booking: CyberAdvanceBooking):
    """Customer books a session in advance with prepaid payment.
    Creates a pending booking — owner confirms after payment screenshot is verified.
    """
    db = get_db()

    # Validate playstation
    ps = db.table("playstations").select("*").eq("id", booking.playstation_id).execute()
    if not ps.data:
        raise HTTPException(status_code=404, detail="PlayStation not found")

    # Validate scheduled time is in the future
    try:
        scheduled = parse_timestamp(booking.scheduled_start)
        if not scheduled:
            raise ValueError("Invalid parse")
        if scheduled <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Scheduled time must be in the future")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_start format. Use ISO datetime (e.g., 2026-05-20T14:00:00)")

    # Check for time slot conflicts with existing bookings/sessions
    hours = float(booking.hours)
    req_start = scheduled
    req_end = scheduled + timedelta(hours=hours) + timedelta(minutes=5)  # 5 min buffer

    existing = db.table("sessions").select("id,start_time,end_time,status,player_name").in_("status", ["pending", "confirmed", "active"]).eq("playstation_id", booking.playstation_id).execute()
    for s in (existing.data or []):
        s_start = parse_timestamp(s.get("start_time"))
        s_end = parse_timestamp(s.get("end_time"))
        if s_start and s_end:
            # Overlap: existing.start < req.end AND existing.end > req.start
            if s_start < req_end and s_end > req_start:
                raise HTTPException(
                    status_code=409,
                    detail=f"Time slot conflicts with an existing {s['status']} booking ({s.get('player_name', 'Guest')}). "
                           f"Please choose a different time."
                )

    rate = ps.data[0]["hourly_rate"]
    num_players = max(1, booking.num_players or 1)
    effective_rate = rate if num_players <= 1 else 125 * num_players
    total = round(hours * effective_rate, 2)

    end_time = req_end

    # Format timestamps with UTC suffix for Supabase TIMESTAMPTZ column
    def fmt_ts(dt):
        return dt.isoformat() + "+00:00"

    session_data = {
        "playstation_id": booking.playstation_id,
        "player_name": booking.player_name,
        "player_phone": booking.player_phone,
        "num_players": num_players,
        "start_time": fmt_ts(scheduled),
        "end_time": fmt_ts(end_time),
        "hours_booked": hours,
        "rate_per_hour": effective_rate,
        "total_amount": total,
        "status": "pending",
    }

    result = db.table("sessions").insert(session_data).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to create booking")

    created = result.data[0]
    return {
        "ok": True,
        "booking_id": created["id"],
        "total": total,
        "payment_status": "unpaid",
        "scheduled_start": fmt_ts(scheduled),
        "message": "Booking created. Pay via UPI and send screenshot to owner's WhatsApp for confirmation."
    }


@app.get("/api/cyber/sessions/pending")
async def get_pending_bookings(_: None = Depends(verify_cyber_dashboard)):
    """Get all pending advance bookings (authenticated)."""
    db = get_db()
    result = db.table("sessions").select("*,playstations(playstation_number,name)").eq("status", "pending").order("start_time", desc=False).execute()
    return result.data or []


@app.patch("/api/cyber/sessions/{session_id}/confirm")
async def confirm_booking(session_id: int, _: None = Depends(verify_cyber_dashboard)):
    """Owner confirms an advance booking after verifying payment screenshot."""
    db = get_db()

    session = db.table("sessions").select("*").eq("id", session_id).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = session.data[0]
    if sess["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot confirm — status is '{sess['status']}'")

    # Mark as confirmed
    db.table("sessions").update({
        "status": "confirmed",
    }).eq("id", session_id).execute()

    return {
        "ok": True,
        "session_id": session_id,
        "status": "confirmed",
        "message": "Booking confirmed! Session will be ready at scheduled time."
    }


@app.patch("/api/cyber/sessions/{session_id}/cancel")
async def cancel_booking(session_id: int, _: None = Depends(verify_cyber_dashboard)):
    """Owner cancels a pending booking."""
    db = get_db()

    session = db.table("sessions").select("*").eq("id", session_id).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = session.data[0]
    if sess["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot cancel — status is '{sess['status']}'")

    db.table("sessions").update({
        "status": "cancelled",
    }).eq("id", session_id).execute()

    return {"ok": True, "session_id": session_id, "status": "cancelled"}


@app.get("/api/cyber/sessions/{session_id}")
async def get_cyber_session(session_id: int):
    """Public: Get session details for tracking timer."""
    _auto_expire_sessions()
    db = get_db()
    result = db.table("sessions").select("*,playstations(playstation_number,name)").eq("id", session_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    session = result.data[0]
    now = datetime.now(timezone.utc)

    end_str = session.get("end_time", "")
    remaining_seconds = 0
    end_time = None
    if end_str:
        try:
            end_dt = parse_timestamp(end_str)
            if end_dt:
                remaining_seconds = max(0, int((end_dt - datetime.now(timezone.utc)).total_seconds()))
                end_time = session.get("end_time", end_str)
        except (ValueError, AttributeError):
            remaining_seconds = 0
            end_time = session.get("end_time", "")

    return {
        "ok": True,
        "session": {
            "id": session["id"],
            "playstation_number": session.get("playstations", {}).get("playstation_number") if isinstance(session.get("playstations"), dict) else session.get("playstation_number"),
            "playstation_name": session.get("playstations", {}).get("name") if isinstance(session.get("playstations"), dict) else None,
            "player_name": session.get("player_name", "Guest"),
            "num_players": session.get("num_players", 1),
            "start_time": session.get("start_time", ""),
            "end_time": end_time or session.get("end_time", ""),
            "hours_booked": session.get("hours_booked", 0),
            "rate_per_hour": session.get("rate_per_hour", 0),
            "total_amount": session.get("total_amount", 0),
            "status": session.get("status", "unknown")
        },
        "remaining_seconds": remaining_seconds,
        "is_active": session.get("status") == "active"
    }


@app.patch("/api/cyber/sessions/{session_id}/end")
async def end_session(session_id: int, update: CyberSessionEnd, _: None = Depends(verify_cyber_dashboard)):
    """End a session."""
    db = get_db()

    session = db.table("sessions").select("*").eq("id", session_id).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = session.data[0]
    end_time = datetime.now(timezone.utc)

    # Always charge the full booked amount — fixed pricing regardless of early end
    booked_hours = float(sess["hours_booked"])
    total_amount = float(sess["total_amount"])

    db.table("sessions").update({
        "end_time": end_time.isoformat(),
        "hours_booked": booked_hours,
        "total_amount": total_amount,
        "status": "completed",
        "end_reason": update.end_reason or ""
    }).eq("id", session_id).execute()

    db.table("playstations").update({"status": "available"}).eq("id", sess["playstation_id"]).execute()

    return {"ok": True, "actual_hours": booked_hours, "total": total_amount}


class SessionExtend(BaseModel):
    player_name: str
    player_phone: Optional[str] = ""
    hours: float = Field(gt=0, description="Extra hours to add (must be positive)")


class AddPlayerRequest(BaseModel):
    player_name: str = Field(default="Guest", description="New player's name")
    player_phone: str = Field(default="", description="New player's phone")
    hours: float = Field(gt=0, description="Hours this player wants to book (must be positive)")


def build_player_entry(name: str, phone: str, hours: float, rate_per_hour: float, joined_at: datetime) -> dict:
    """Create a player entry for the JSONB players array."""
    buffer_minutes = 5
    amount = round(hours * rate_per_hour, 2)
    end_time = joined_at + timedelta(hours=hours) + timedelta(minutes=buffer_minutes)
    return {
        "name": name,
        "phone": phone,
        "joined_at": joined_at.isoformat(),
        "hours_booked": hours,
        "rate_per_hour": rate_per_hour,
        "amount": amount,
        "end_time": end_time.isoformat()
    }


@app.post("/api/cyber/sessions/{session_id}/extend")
async def extend_session(session_id: int, extend: SessionExtend):
    """Extend an active session by adding hours."""
    db = get_db()

    session = db.table("sessions").select("*,playstations(hourly_rate)").eq("id", session_id).eq("status", "active").execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Active session not found")

    sess = session.data[0]
    rate = sess["playstations"]["hourly_rate"] if isinstance(sess["playstations"], dict) else sess["rate_per_hour"]
    num_p = max(1, int(sess.get("num_players", 1)))
    eff_rate = float(rate) if num_p <= 1 else 125 * num_p
    extra_amount = round(float(extend.hours) * eff_rate, 2)

    current_end = parse_timestamp(sess["end_time"])
    if not current_end:
        raise HTTPException(status_code=500, detail="Invalid session end_time")
    new_end = current_end + timedelta(hours=extend.hours)

    updated_hours = round(float(sess.get("hours_booked", 0)) + extend.hours, 2)

    db.table("sessions").update({
        "end_time": new_end.isoformat(),
        "hours_booked": updated_hours,
        "total_amount": round(float(sess["total_amount"]) + extra_amount, 2),
        "player_name": extend.player_name,
        "player_phone": extend.player_phone
    }).eq("id", session_id).execute()

    return {"ok": True, "new_end_time": new_end.isoformat(), "added_hours": extend.hours, "extra_charge": extra_amount}


@app.post("/api/cyber/sessions/{session_id}/add-player")
async def add_player_to_session(session_id: int, player: AddPlayerRequest):
    """Add a new player to an active session with per-player build calculation.

    Each player gets their own entry with individual timing and cost.
    The new player's timing starts from their join time.
    """
    db = get_db()

    session = db.table("sessions").select("*,playstations(hourly_rate)").eq("id", session_id).eq("status", "active").execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Active session not found")

    sess = session.data[0]
    current_players = int(sess.get("num_players", 1))
    new_players = current_players + 1
    now = datetime.now(timezone.utc)

    # Calculate new player's rate
    base_rate = sess["playstations"]["hourly_rate"] if isinstance(sess["playstations"], dict) else sess["rate_per_hour"]
    # Group rate applies if total players will be 2+
    new_player_rate = 125 if new_players >= 2 else float(base_rate)

    # Build the new player's entry
    new_player = build_player_entry(
        player.player_name, player.player_phone,
        player.hours, new_player_rate, now
    )

    # Get existing players array
    existing_players = sess.get("players") or []
    if isinstance(existing_players, list):
        updated_players = list(existing_players) + [new_player]
    else:
        updated_players = [new_player]

    # Calculate new total amount
    extra_amount = new_player["amount"]
    new_total = round(float(sess.get("total_amount", 0)) + extra_amount, 2)
    new_hours = round(float(sess.get("hours_booked", 0)) + player.hours, 2)

    # Update session end_time to cover the latest-ending player
    current_end = parse_timestamp(sess["end_time"])
    new_player_end = parse_timestamp(new_player["end_time"])
    final_end = None
    if current_end and new_player_end:
        final_end = max(current_end, new_player_end)
    elif current_end:
        final_end = current_end
    elif new_player_end:
        final_end = new_player_end
    end_time_str = final_end.isoformat() if final_end else sess["end_time"]

    updates = {
        "num_players": new_players,
        "total_amount": new_total,
        "hours_booked": new_hours,
        "end_time": end_time_str,
        "players": updated_players
    }

    # If crossing from 1 to 2 players, update the session rate_per_hour for display
    if current_players == 1 and new_players >= 2:
        updates["rate_per_hour"] = 125 * new_players

    db.table("sessions").update(updates).eq("id", session_id).execute()

    return {
        "ok": True,
        "num_players": new_players,
        "player": new_player,
        "extra_charge": extra_amount,
        "session_total": new_total,
        "player_end_time": new_player["end_time"]
    }


@app.get("/api/cyber/stats")
async def get_cyber_stats(_: None = Depends(verify_cyber_dashboard)):
    """Get cafe statistics."""
    db = get_db()

    playstations = db.table("playstations").select("*").eq("is_active", True).execute()
    all_sessions = db.table("sessions").select("*").execute()
    active = db.table("sessions").select("*").eq("status", "active").execute()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_sessions = [s for s in (all_sessions.data or []) if s.get("start_time", "").startswith(today)]

    # Amount to collect: total of all active sessions
    amount_to_collect = round(sum(float(s.get("total_amount", 0)) for s in (active.data or [])), 2)

    return {
        "total_ps": len(playstations.data or []),
        "available": len([p for p in (playstations.data or []) if p["status"] == "available"]),
        "occupied": len([p for p in (playstations.data or []) if p["status"] == "occupied"]),
        "active_sessions": len(active.data or []),
        "revenue_today": sum(float(s.get("total_amount", 0)) for s in today_sessions),
        "all_time_revenue": sum(float(s.get("total_amount", 0)) for s in (all_sessions.data or [])),
        "amount_to_collect": amount_to_collect
    }


@app.get("/api/cyber/customers")
async def get_cyber_customers(_: None = Depends(verify_cyber_dashboard)):
    """Get customer database for marketing — deduplicated by phone, with visit history & spend."""
    db = get_db()
    all_sessions = db.table("sessions").select("*").neq("player_phone", "").not_.is_("player_phone", "null").execute()
    sessions = all_sessions.data or []

    # Aggregate by phone
    customers = {}
    for s in sessions:
        phone = s.get("player_phone", "").strip()
        name = s.get("player_name", "Guest") or "Guest"
        if not phone:
            continue
        if phone not in customers:
            customers[phone] = {
                "phone": phone,
                "name": name,
                "visit_count": 0,
                "total_spent": 0.0,
                "first_visit": None,
                "last_visit": None,
                "favourite_ps": {},
                "statuses": {"active": 0, "completed": 0, "cancelled": 0},
            }
        c = customers[phone]
        c["visit_count"] += 1
        c["total_spent"] += float(s.get("total_amount", 0) or 0)
        t = s.get("start_time") or s.get("created_at")
        if t:
            if not c["first_visit"] or t < c["first_visit"]:
                c["first_visit"] = t
            if not c["last_visit"] or t > c["last_visit"]:
                c["last_visit"] = t
        ps = s.get("playstation_id")
        if ps:
            c["favourite_ps"][str(ps)] = c["favourite_ps"].get(str(ps), 0) + 1
        st = s.get("status", "")
        if st in c["statuses"]:
            c["statuses"][st] += 1
        # Use latest name (most recent session)
        if name and name != "Guest":
            c["name"] = name

    # Compute favourite ps number, sort by last_visit desc
    result = []
    for phone, c in customers.items():
        fav_ps = max(c["favourite_ps"], key=c["favourite_ps"].get) if c["favourite_ps"] else None
        result.append({
            "phone": c["phone"],
            "name": c["name"],
            "visit_count": c["visit_count"],
            "total_spent": round(c["total_spent"], 0),
            "first_visit": c["first_visit"],
            "last_visit": c["last_visit"],
            "favourite_ps": int(fav_ps) if fav_ps else None,
            "completed": c["statuses"]["completed"],
            "cancelled": c["statuses"]["cancelled"],
        })

    result.sort(key=lambda x: x.get("last_visit") or "", reverse=True)
    return {
        "total_customers": len(result),
        "customers": result
    }


# ═══════════════════════════════════════════════════════════════════════
# CHANGE PASSWORD (Cyber only)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/change-password")
async def change_password(body: PasswordChange):
    """Change cyber dashboard password."""
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if len(body.new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    correct = os.getenv("DASHBOARD_PASSWORD", "changeme")
    try:
        db = get_db()
        r = db.table("admin_settings").select("value").eq("key", "cyber_dashboard_password").maybe_single().execute()
        if r.data and r.data.get("value"):
            correct = r.data["value"]
    except Exception:
        pass

    if not check_password(body.current_password, correct):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    hashed = hash_password(body.new_password)
    try:
        db = get_db()
        db.table("admin_settings").upsert({"key": "cyber_dashboard_password", "value": hashed}, on_conflict="key").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save password: {e}. Please ensure admin_settings table exists in Supabase.")

    return {"success": True, "dashboard": "cyber", "message": "Dashboard password updated successfully"}


# ─── Run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
