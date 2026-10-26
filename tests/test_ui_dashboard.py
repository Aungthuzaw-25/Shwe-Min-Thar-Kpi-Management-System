import os
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app.main import app
from app.web import dashboard_web

BASE_URL = os.getenv("UI_BASE_URL", "http://127.0.0.1:18000")
SUPERADMIN_EMAIL = os.getenv("UI_TEST_SUPERADMIN_EMAIL", "qa.superadmin@example")


def required_ui_password(name):
    password = os.getenv(name)
    if not password:
        pytest.skip(f"{name} is not configured")
    return password


@pytest.fixture
def playwright_context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


def login(page, email, password):
    page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")

    # Allow the app to redirect to the role-specific dashboard while avoiding brittle
    # URL-only waiting for L0/L1 flows that may land on the shared /dashboard route.
    page.wait_for_function(
        """
        () => {
          const path = window.location.pathname || '';
          return path.includes('/dashboard') || path.includes('/superadmin/dashboard') || path.includes('/admin/dashboard');
        }
        """,
        timeout=60000,
    )


class _TemplateDBResult:
    def scalars(self):
        return self

    def first(self):
        return None

    def scalar_one_or_none(self):
        return None


class _TemplateDB:
    async def execute(self, _statement):
        return _TemplateDBResult()


@pytest.mark.parametrize(
    ("path", "role_name"),
    [
        ("/superadmin/dashboard", "Super Admin"),
        ("/admin/dashboard", "Admin"),
    ],
)
def test_fastapi_dashboard_templates_render_with_user_context(path, role_name):
    user = SimpleNamespace(
        id=uuid4(),
        first_name="Test",
        last_name="User",
        email="test.user@example.test",
        role=SimpleNamespace(name=role_name, permissions=[]),
        position_id=None,
        avatar_path=None,
        avatar_updated_at=None,
    )
    app.dependency_overrides[dashboard_web.get_current_user] = lambda: user
    app.dependency_overrides[dashboard_web.get_db] = _TemplateDB

    try:
        with TestClient(app) as client:
            response = client.get(path)
            liquid_glass = client.get("/static/css/liquid-glass-theme.css?v=20261021-4")
            kpi_theme = client.get("/static/css/superadmin-kpi-glass.css?v=20261021-2")
            login_theme = client.get("/static/css/login-form-glass.css?v=20261021-2")
            login_page = client.get("/auth/login")
            assert liquid_glass.status_code == 200
            assert kpi_theme.status_code == 200
            assert login_theme.status_code == 200
            assert "--kg-accent:#3D5A80;" in kpi_theme.text
            assert login_page.status_code == 200
            assert "login-form-glass.css?v=20261021-2" in login_page.text
            assert client.get("/static/css/executive-dashboard.css").status_code == 200
            design_system = client.get("/static/css/design-system.css?v=20261015-1")
            assert design_system.status_code == 200
            assert ".app-topbar__identity" in design_system.text
            assert "text-decoration: none;" in design_system.text
            assert (
                client.get(
                    "/static/js/superadmin-executive-dashboard.js?v=20261015-1"
                ).status_code
                == 200
            )
    finally:
        app.dependency_overrides.pop(dashboard_web.get_current_user, None)
        app.dependency_overrides.pop(dashboard_web.get_db, None)

    assert response.status_code == 200
    assert "Test User" in response.text
    assert 'class="app-shell app-shell--admin' in response.text
    assert "liquid-glass-theme.css?v=20261021-4" in response.text
    assert "kpi-avatar" in response.text
    assert (
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"
        in response.text
    )
    csp = response.headers["content-security-policy"]
    assert "style-src 'self'" in csp
    assert "https://fonts.googleapis.com" in csp
    assert "font-src 'self' https://fonts.gstatic.com" in csp
    assert "system-ui" in response.text
    assert len(response.headers.get_list("content-security-policy")) == 1
    nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", csp).group(1)
    inline_scripts = re.findall(
        r"<script\b(?![^>]*\bsrc=)([^>]*)>", response.text, re.IGNORECASE
    )
    inline_styles = re.findall(r"<style\b([^>]*)>", response.text, re.IGNORECASE)
    assert inline_scripts
    assert all(f'nonce="{nonce}"' in attributes for attributes in inline_scripts)
    assert all(f'nonce="{nonce}"' in attributes for attributes in inline_styles)
    assert not re.search(r"\sstyle\s*=", response.text, re.IGNORECASE)
    assert not re.search(
        r"\son(?:click|submit|change|keydown)\s*=",
        response.text,
        re.IGNORECASE,
    )


def test_avatar_macro_accepts_missing_user():
    avatar_macro = dashboard_web.templates.get_template(
        "components/avatar.njk"
    ).module.avatar

    rendered = str(avatar_macro(None, 32))

    assert 'aria-label="User"' in rendered
    assert "kpi-avatar--color-0" in rendered


def test_avatar_macro_photo_keeps_color_fallback():
    # A photo avatar whose image fails must still show its initials on
    # the per-user colour — not white text on a transparent circle
    # (which vanished against the white card below the dark banner).
    avatar_macro = dashboard_web.templates.get_template(
        "components/avatar.njk"
    ).module.avatar

    rendered = str(
        avatar_macro(
            {
                "display_name": "Thae Aung",
                "avatar_url": "/static/image/does-not-exist.jpg",
                "avatar_color_index": 3,
                "initials": "TA",
            },
            128,
        )
    )

    assert "kpi-avatar--photo" in rendered
    assert "kpi-avatar--color-3" in rendered


def test_static_assets_are_cacheable_without_csrf_cookie():
    response = TestClient(app).get("/static/css/dashboard.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == (
        "public, max-age=604800, stale-while-revalidate=86400"
    )
    assert "set-cookie" not in response.headers


def test_superadmin_dashboard_access(playwright_context):
    page = playwright_context.new_page()
    login(page, SUPERADMIN_EMAIL, required_ui_password("UI_TEST_SUPERADMIN_PASSWORD"))
    assert page.url.endswith("/superadmin/dashboard")
    page.wait_for_selector("#valUsers", timeout=60000)
    assert page.locator("#valUsers").first.is_visible()
    page.close()


def test_l2_user_dashboard_flow(playwright_context):
    page = playwright_context.new_page()
    login(
        page,
        os.getenv("UI_TEST_L2_EMAIL", "qa.l2@example.test"),
        required_ui_password("UI_TEST_L2_PASSWORD"),
    )
    page.wait_for_selector("#currentScore", timeout=60000)
    assert page.locator("text=My Performance Trend").first.is_visible()
    page.goto(f"{BASE_URL}/tasks")
    page.wait_for_selector('button:has-text("Personal Task")', timeout=60000)
    page.click('button:has-text("Personal Task")')
    assert page.locator("text=Log Personal Task").first.is_visible()
    page.close()


def test_l0_user_dashboard_flow(playwright_context):
    page = playwright_context.new_page()
    login(
        page,
        os.getenv("UI_TEST_L0_EMAIL", "qa.l0@example.test"),
        required_ui_password("UI_TEST_L0_PASSWORD"),
    )
    page.wait_for_selector("#currentScore", timeout=60000)
    assert page.locator("#currentScore").first.is_visible()
    page.close()


def test_l1_user_dashboard_flow(playwright_context):
    page = playwright_context.new_page()
    login(
        page,
        os.getenv("UI_TEST_L1_EMAIL", "qa.l1@example.test"),
        required_ui_password("UI_TEST_L1_PASSWORD"),
    )
    page.wait_for_selector("#currentScore", timeout=60000)
    assert page.locator("#currentScore").first.is_visible()
    page.close()


def test_offline_dashboard_banner_and_cached_data(playwright_context):
    """Read-only offline: a dashboard viewed online must re-open while
    offline, show the offline banner, and render its data from the
    service-worker cache."""
    page = playwright_context.new_page()
    login(page, SUPERADMIN_EMAIL, required_ui_password("UI_TEST_SUPERADMIN_PASSWORD"))
    page.wait_for_selector("#valUsers", timeout=60000)
    # The first visit installs the worker; reload so the page is
    # controlled and its dashboard reads pass through the worker.
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#valUsers", timeout=60000)
    page.wait_for_function(
        """async () => {
          if (!('serviceWorker' in navigator)) return false;
          const registration = await navigator.serviceWorker.getRegistration();
          if (!registration || !registration.active) return false;
          const names = await caches.keys();
          return names.some((name) => name.startsWith('kpi-data-'));
        }""",
        timeout=60000,
    )
    playwright_context.set_offline(True)
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#offlineBanner:not([hidden])", timeout=60000)
        assert "offline" in page.locator("#offlineBanner").inner_text().lower()
        assert page.locator("#valUsers").first.is_visible()
    finally:
        playwright_context.set_offline(False)
    page.close()


def test_offline_login_page_purges_private_caches(playwright_context):
    """Arriving at the login page must leave no previous user's cached
    pages/data behind (the shell cache of public assets may stay)."""
    page = playwright_context.new_page()
    login(page, SUPERADMIN_EMAIL, required_ui_password("UI_TEST_SUPERADMIN_PASSWORD"))
    page.wait_for_selector("#valUsers", timeout=60000)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#valUsers", timeout=60000)
    page.wait_for_function(
        """async () => {
          const names = await caches.keys();
          return names.some((name) => name.startsWith('kpi-data-'));
        }""",
        timeout=60000,
    )
    playwright_context.clear_cookies()
    page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded")
    page.wait_for_function(
        """async () => {
          const names = await caches.keys();
          return !names.some(
            (name) => name.startsWith('kpi-pages-') || name.startsWith('kpi-data-'),
          );
        }""",
        timeout=60000,
    )
    page.close()
