import pytest
from fastapi.testclient import TestClient
from backend.main import app
import os, sys

print("[DEBUG] CWD:", os.getcwd())
print("[DEBUG] sys.path:", sys.path)
print("[DEBUG] PYTHONPATH:", os.environ.get("PYTHONPATH"))

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_login_guide():
    response = client.get("/login-guide/facebook")
    assert response.status_code == 200
    assert response.json()["platform"] == "Facebook"
    assert len(response.json()["steps"]) > 0

def test_invalid_login_guide():
    response = client.get("/login-guide/invalid")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_scan_request():
    response = client.post(
        "/scan",
        json={
            "url": "https://example.com",
            "username": "testuser"
        }
    )
    assert response.status_code == 200
    assert "scan_id" in response.json()


