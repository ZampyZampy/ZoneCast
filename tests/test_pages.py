"""
Exercises actual template rendering (/ and /login), not just JSON API
endpoints — a previous deploy broke exactly this path (a starlette
major-version bump changed Jinja2Templates.TemplateResponse's calling
convention) while every API-only test still passed, since TestClient
alone doesn't render HTML unless something actually GETs a page route.
"""


def test_login_page_renders(client):
    res = client.get("/login")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "<form" in res.text


def test_dashboard_renders_when_authenticated(admin_client):
    res = admin_client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "ZoneCast" in res.text


def test_dashboard_redirects_when_not_authenticated(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/login"


def test_session_of_a_deleted_user_does_not_loop_between_pages(admin_client):
    from fastapi.testclient import TestClient

    from app.main import app

    admin_client.post("/api/auth/users", json={"username": "gone-soon", "password": "gone-soon-password", "role": "operator"})
    users = admin_client.get("/api/auth/users").json()
    victim = next(u for u in users if u["username"] == "gone-soon")
    with TestClient(app) as other:
        assert other.post("/api/auth/login", json={"username": "gone-soon", "password": "gone-soon-password"}).status_code == 200
        admin_client.delete(f"/api/auth/users/{victim['id']}")
        res = other.get("/", follow_redirects=False)
        assert res.headers["location"] == "/login"
        assert other.get("/login", follow_redirects=False).status_code == 200
