import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app.main import app

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
