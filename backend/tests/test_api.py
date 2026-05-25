"""Tests for OnClock API endpoints.

Uses httpx AsyncClient with mocked Supabase client to test all API endpoints
without requiring a real database connection.
"""
import os
import sys
from unittest.mock import MagicMock, patch

# Mock slowapi BEFORE any app imports — must be a proper ASGI middleware
class MockSlowAPIMiddleware:
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)

mock_slowapi = MagicMock()
mock_slowapi.Limiter = MagicMock(return_value=MagicMock())
mock_slowapi._rate_limit_exceeded_handler = staticmethod(lambda r, e: None)
mock_slowapi.SlowAPIMiddleware = MockSlowAPIMiddleware
mock_slowapi.get_remote_address = staticmethod(lambda: 'test')
mock_slowapi.RateLimitExceeded = type('RateLimitExceeded', (Exception,), {})
mock_slowapi.errors = MagicMock()
mock_slowapi.errors.RateLimitExceeded = type('RateLimitExceeded', (Exception,), {})
mock_slowapi.middleware = MagicMock()
mock_slowapi.middleware.SlowAPIMiddleware = MockSlowAPIMiddleware
mock_slowapi.util = MagicMock()
mock_slowapi.util.get_remote_address = staticmethod(lambda: 'test')
sys.modules['slowapi'] = mock_slowapi
sys.modules['slowapi.middleware'] = mock_slowapi.middleware
sys.modules['slowapi.util'] = mock_slowapi.util
sys.modules['slowapi.errors'] = mock_slowapi.errors

# Set test environment BEFORE importing the app
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ["DASHBOARD_PASSWORD"] = "test-password"
os.environ["BUSINESS_NAME"] = "Test Business"
os.environ["HOURLY_RATE"] = "50"
os.environ["UPI_ID"] = "test@upi"
os.environ["UPI_PAYEE_NAME"] = "Test Business"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from httpx import ASGITransport
import pytest
from main import app


# ─── Mock Supabase Client ───────────────────────────────────────

class MockResponse:
    """Mock for Supabase query response."""
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def create_mock_table():
    """Create a mock table that returns itself for chainable methods."""
    mock = MagicMock()
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.neq.return_value = mock
    mock.gte.return_value = mock
    mock.lte.return_value = mock
    mock.order.return_value = mock
    mock.limit.return_value = mock
    mock.range.return_value = mock
    mock.maybe_single.return_value = mock
    mock.execute.return_value = MockResponse(data=[])
    return mock


@pytest.fixture(autouse=True)
def mock_supabase():
    """Mock the Supabase client globally for all tests."""
    mock_db = MagicMock()
    mock_db.table.return_value = create_mock_table()
    
    with patch("main.get_db", return_value=mock_db):
        with patch("database.get_db", return_value=mock_db):
            yield mock_db


# ─── Test Client ─────────────────────────────────────────────────

@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_frontend_index(client):
    """Test that the index page loads."""
    async with client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_frontend_dashboard(client):
    """Test that the dashboard page loads."""
    async with client:
        resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_frontend_customers(client):
    """Test customers page loads."""
    async with client:
        resp = await client.get("/customers")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_frontend_qr_codes(client):
    """Test QR codes page loads."""
    async with client:
        resp = await client.get("/qr-codes")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_frontend_track(client):
    """Test session tracking page loads."""
    async with client:
        resp = await client.get("/track")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_frontend_booking(client):
    """Test booking page loads."""
    async with client:
        resp = await client.get("/booking/1")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_frontend_booking_invalid(client):
    """Test invalid booking still serves a page."""
    async with client:
        resp = await client.get("/booking/99")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_stations(mock_supabase, client):
    """Test GET /api/stations returns station list."""
    mock_db = mock_supabase
    mock_resp = MockResponse(data=[
        {"id": 1, "station_number": 1, "name": "Station 1", "hourly_rate": 50, "status": "available"},
        {"id": 2, "station_number": 2, "name": "Station 2", "hourly_rate": 50, "status": "available"},
    ])
    mock_db.table.return_value.execute.return_value = mock_resp

    async with client:
        resp = await client.get("/api/stations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["station_number"] == 1


@pytest.mark.asyncio
async def test_api_stations_requires_auth(mock_supabase, client):
    """Test dashboard endpoints require password."""
    async with client:
        resp = await client.get("/api/sessions/active")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_sessions_with_auth(mock_supabase, client):
    """Test dashboard endpoints work with password."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    mock_resp = MockResponse(data=[
        {"id": 1, "station_id": 1, "player_name": "Test Player", 
         "start_time": now, "end_time": None, "status": "active",
         "num_players": 1, "hours_booked": 1, "rate_per_hour": 50,
         "total_amount": 50, "players": []}
    ])
    mock_supabase.table.return_value.execute.return_value = mock_resp

    async with client:
        resp = await client.get("/api/sessions/active", 
                                headers={"Authorization": "Bearer test-password"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_api_config_default(client):
    """Test /api/config returns default config from test env."""
    async with client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["currency"] == "INR"
    assert data["currency_symbol"] == "₹"
    assert data["is_inr"] == True
    assert data["hourly_rate"] == 50
    assert data["business_name"] == "Test Business"
    assert data["owner_whatsapp"] == ""
    assert data["upi_id"] == "test@upi"
    assert data["upi_payee_name"] == "Test Business"


@pytest.mark.asyncio
async def test_api_config_fields(client):
    """Test /api/config returns all expected fields."""
    async with client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    for field in ["currency", "currency_symbol", "is_inr", "hourly_rate",
                  "business_name", "owner_whatsapp", "upi_id", "upi_payee_name"]:
        assert field in data
    assert isinstance(data["is_inr"], bool)
    assert isinstance(data["hourly_rate"], (int, float))


@pytest.mark.asyncio
async def test_api_config_owner_whatsapp(client):
    """Test owner_whatsapp field is present in config."""
    async with client:
        resp = await client.get("/api/config")
    data = resp.json()
    assert "owner_whatsapp" in data


@pytest.mark.asyncio
async def test_api_stats(mock_supabase, client):
    """Test /api/stats endpoint."""
    mock_supabase.table.return_value.execute.return_value = MockResponse(data=[])

    async with client:
        resp = await client.get("/api/stats",
                                headers={"Authorization": "Bearer test-password"})
    assert resp.status_code == 200
