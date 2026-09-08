import os

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8000"
SUPERADMIN_EMAIL = os.getenv("UI_TEST_SUPERADMIN_EMAIL", "superadmin@kpi.com")


def required_ui_password(name):
    password = os.getenv(name)
    if not password:
        pytest.skip(f"{name} is not configured")
    return password


@pytest.fixture(scope="function")
def playwright_context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


def login(page, email, password):
    page.goto(f"{BASE_URL}/auth/login")
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
        os.getenv("UI_TEST_L2_EMAIL", "cashier.mdy@kpi.com"),
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
        os.getenv("UI_TEST_L0_EMAIL", "finance.l0@kpi.com"),
        required_ui_password("UI_TEST_L0_PASSWORD"),
    )
    page.wait_for_selector("#currentScore", timeout=60000)
    assert page.locator("#currentScore").first.is_visible()
    page.close()


def test_l1_user_dashboard_flow(playwright_context):
    page = playwright_context.new_page()
    login(
        page,
        os.getenv("UI_TEST_L1_EMAIL", "finance.l1@kpi.com"),
        required_ui_password("UI_TEST_L1_PASSWORD"),
    )
    page.wait_for_selector("#currentScore", timeout=60000)
    assert page.locator("#currentScore").first.is_visible()
    page.close()
