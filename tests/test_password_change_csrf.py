from pathlib import Path


def test_password_change_posts_the_csrf_cookie_in_the_expected_header():
    template = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "auth"
        / "change_password.njk"
    ).read_text(encoding="utf-8")

    assert ".find((cookie) => cookie.startsWith('csrf_token='))" in template
    assert "'X-CSRF-Token': csrfToken" in template
    assert "if (!csrfToken)" in template
    assert "Security token is unavailable. Reload this page and try again." in template
    assert "fetch('/api/v1/auth/change-password'" in template
    assert "credentials: 'include'" in template
    assert "new_password: newPassword" in template
    assert "window.location.href = `/auth/login?next=${encodeURIComponent(target)}`" in template
