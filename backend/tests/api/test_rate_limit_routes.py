from fastapi.testclient import TestClient


def test_login_route_rate_limit_headers(client: TestClient):
    # Perform a request to login
    res = client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "wrongpassword"},
    )
    assert "RateLimit-Limit" in res.headers
    assert "RateLimit-Remaining" in res.headers
    assert "RateLimit-Reset" in res.headers
