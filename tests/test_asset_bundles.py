"""Phase 5b asset bundles (perf/lcp): asset() helper, Jinja2 layout
rendering, and the immutable Cache-Control branch for hashed files."""

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import CachedStaticFiles
from app.web.template_support import asset_bundle, clear_asset_caches, templates


BASE_FLAG_OFF = [
    "/static/css/output.css",
    "/static/css/design-system.css?v=20261015-1",
    "/static/css/notifications.css",
    "/static/css/dashboard.css",
    "/static/css/enterprise-ui.css",
    "/static/css/executive-dashboard.css",
    "/static/css/liquid-glass-theme.css?v=20261021-4",
    "/static/css/role-themes.css",
    "/static/css/sidebar-panels.css",
    "/static/css/fonts.css",
    "/static/css/inter-fonts.css",
]

PORTAL_FLAG_OFF = [
    "/static/css/output.css",
    "/static/css/design-system.css?v=20261015-1",
    "/static/css/notifications.css",
    "/static/css/enterprise-ui.css",
    "/static/css/executive-dashboard.css",
    "/static/css/liquid-glass-theme.css?v=20261021-4",
    "/static/css/role-themes.css",
    "/static/css/inter-fonts.css",
]

FAKE_MANIFEST = {
    "base-head": "/static/css/base-head.aaaabbbbcccc.css",
    "executive": "/static/css/executive.111122223333.css",
    "base-tail": "/static/css/base-tail.444455556666.css",
    "fonts": "/static/css/fonts.777788889999.css",
    "portal-head": "/static/css/portal-head.aaaabbbbcccc.css",
    "portal-tail": "/static/css/portal-tail.444455556666.css",
}


@pytest.fixture
def asset_paths(tmp_path, monkeypatch):
    """Point the helper at a temp spec + manifest, resetting its caches."""
    spec = tmp_path / "bundles.json"
    spec.write_text(
        json.dumps(
            {
                "bundles": {
                    "base-head": {
                        "files": ["output.css"],
                        "hrefs": BASE_FLAG_OFF[:5],
                    },
                    "executive": {
                        "files": ["executive-dashboard.css"],
                        "hrefs": ["/static/css/executive-dashboard.css"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "asset-manifest.json"
    manifest.write_text(json.dumps(FAKE_MANIFEST), encoding="utf-8")
    monkeypatch.setenv("ASSET_BUNDLES_SPEC_PATH", str(spec))
    monkeypatch.setenv("ASSET_MANIFEST_PATH", str(manifest))
    clear_asset_caches()
    yield manifest
    clear_asset_caches()


def stylesheet_hrefs(html):
    return re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)


def test_asset_bundle_returns_hashed_href_on_manifest_hit(asset_paths):
    assert asset_bundle("base-head") == ["/static/css/base-head.aaaabbbbcccc.css"]


def test_asset_bundle_falls_back_to_spec_hrefs_when_manifest_missing(
    tmp_path, monkeypatch
):
    spec = tmp_path / "bundles.json"
    spec.write_text(
        json.dumps(
            {
                "bundles": {
                    "base-head": {"files": ["output.css"], "hrefs": BASE_FLAG_OFF[:5]},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASSET_BUNDLES_SPEC_PATH", str(spec))
    monkeypatch.setenv("ASSET_MANIFEST_PATH", str(tmp_path / "missing.json"))
    clear_asset_caches()
    try:
        assert asset_bundle("base-head") == BASE_FLAG_OFF[:5]
    finally:
        clear_asset_caches()


def test_asset_bundle_unknown_name_returns_empty(asset_paths):
    assert asset_bundle("no-such-bundle") == []


def test_jinja_flag_off_base_emits_original_links():
    rendered = templates.get_template("layouts/base.njk").render(
        asset_bundles=False, asset=asset_bundle
    )
    assert stylesheet_hrefs(rendered) == BASE_FLAG_OFF


def test_jinja_flag_off_portal_emits_original_links():
    rendered = templates.get_template("layouts/employee_base.njk").render(
        asset_bundles=False,
        asset=asset_bundle,
        user_name="QA User",
        permission_map={},
    )
    assert stylesheet_hrefs(rendered) == PORTAL_FLAG_OFF


def test_jinja_flag_on_base_bundles_in_order_executive_only_for_executive(
    asset_paths,
):
    template = templates.get_template("layouts/base.njk")
    plain = template.render(asset_bundles=True, asset=asset_bundle)
    assert stylesheet_hrefs(plain) == [
        "/static/css/base-head.aaaabbbbcccc.css",
        "/static/css/base-tail.444455556666.css",
        "/static/css/fonts.777788889999.css",
        "/static/css/inter-fonts.css",
    ]
    executive = template.render(
        asset_bundles=True, asset=asset_bundle, dashboard_variant="executive"
    )
    assert stylesheet_hrefs(executive) == [
        "/static/css/base-head.aaaabbbbcccc.css",
        "/static/css/executive.111122223333.css",
        "/static/css/base-tail.444455556666.css",
        "/static/css/fonts.777788889999.css",
        "/static/css/inter-fonts.css",
    ]
    needed = template.render(
        asset_bundles=True, asset=asset_bundle, needs_executive_css=True
    )
    assert stylesheet_hrefs(needed) == stylesheet_hrefs(executive)


def test_hashed_bundle_files_are_immutable_and_plain_files_unchanged(tmp_path):
    css_dir = tmp_path / "css"
    css_dir.mkdir()
    (css_dir / "base-head.aaaabbbbcccc.css").write_text("a{color:red}", encoding="utf-8")
    (css_dir / "dashboard.css").write_text("b{color:blue}", encoding="utf-8")
    # A short hash-like suffix must NOT match the 12-hex immutable pattern.
    (css_dir / "base-head.abc.css").write_text("c{color:green}", encoding="utf-8")

    mini = FastAPI()
    mini.mount("/static", CachedStaticFiles(directory=str(tmp_path)), name="static")
    client = TestClient(mini)

    hashed = client.get("/static/css/base-head.aaaabbbbcccc.css")
    assert hashed.status_code == 200
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"

    plain = client.get("/static/css/dashboard.css")
    assert plain.status_code == 200
    assert (
        plain.headers["cache-control"]
        == "public, max-age=604800, stale-while-revalidate=86400"
    )

    short = client.get("/static/css/base-head.abc.css")
    assert short.status_code == 200
    assert (
        short.headers["cache-control"]
        == "public, max-age=604800, stale-while-revalidate=86400"
    )
