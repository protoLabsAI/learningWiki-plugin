"""Four-rules compliance, asserted against the actual PAGE string."""

from __future__ import annotations

from learning_wiki.view import PAGE


def test_rule3_slug_aware_base_derivation():
    assert 'location.pathname.split("/plugins/")' in PAGE
    assert "http://localhost" not in PAGE


def test_rule4_kit_css_and_module_import_off_base():
    assert '"/_ds/plugin-kit.css"' in PAGE
    assert 'import(BASE + "/_ds/plugin-kit.js")' in PAGE


def test_rule2_data_via_gated_apifetch():
    assert "kit.apiFetch" in PAGE
    assert "/api/plugins/learning_wiki/pages" in PAGE


def test_no_handrolled_theme_or_handshake():
    # The kit owns the protoagent:init handshake + theme tokens.
    assert 'addEventListener("message"' not in PAGE
    assert ":root{--pl-" not in PAGE.replace(" ", "")


def test_boot_is_handshake_or_timer():
    assert "kit.initPluginView" in PAGE
    assert "setTimeout(boot" in PAGE


def test_responsive_by_container_query_not_media_query():
    # The page lives in a RESIZABLE panel — breakpoints key off the panel's
    # inline size, never the viewport.
    assert "container-type:inline-size" in PAGE
    assert "@container wiki (max-width: 560px)" in PAGE
    assert "@media" not in PAGE


def test_narrow_layout_has_back_navigation():
    assert "page-open" in PAGE
    assert "data-back" in PAGE
    assert 'classList.add("page-open")' in PAGE
    assert 'classList.remove("page-open")' in PAGE
