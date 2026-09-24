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
