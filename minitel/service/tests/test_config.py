from pathlib import Path

import pytest

from minitel_ha.config import DEFAULT_TITLE, Options, load_config, load_options
from minitel_ha.dashboard import Dashboard, Item, View

EXAMPLE = Path(__file__).parents[3] / "minitel.example.yaml"


def write(tmp_path, content):
    path = tmp_path / "minitel.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_empty_file_follows_home_assistant(tmp_path):
    assert load_options(write(tmp_path, "")) == Options(DEFAULT_TITLE, "", None)


def test_dashboard_and_title(tmp_path):
    options = load_options(write(tmp_path, "title: 3615 CHEZ NOUS\ndashboard: ' maison-minitel '\n"))
    assert options == Options("3615 CHEZ NOUS", "maison-minitel", None)


def test_app_options_as_written_by_run_sh(tmp_path):
    # jq écrit null pour une option absente.
    content = '{"title": "3615 MAISON", "dashboard": null, "pages": []}'
    assert load_options(write(tmp_path, content)) == Options(DEFAULT_TITLE, "", None)


def test_handwritten_pages_become_a_fixed_dashboard(tmp_path):
    content = "pages:\n  - title: Étage\n    lights:\n      - {name: Palier, entity: light.palier}\n"
    options = load_options(write(tmp_path, content))
    assert options.pages == Dashboard("Sommaire", (View("Étage", (Item("light.palier", "Palier"),)),))


def test_pages_are_no_longer_limited_to_16(tmp_path):
    lights = "".join(f"      - {{name: n, entity: light.l{i}}}\n" for i in range(20))
    options = load_options(write(tmp_path, f"pages:\n  - title: T\n    lights:\n{lights}"))
    assert len(options.pages.views[0].elements) == 20


def test_a_dashboard_wins_over_pages(tmp_path):
    content = "dashboard: maison\npages:\n  - title: T\n    lights:\n      - {name: n, entity: light.n}\n"
    assert load_options(write(tmp_path, content)) == Options(DEFAULT_TITLE, "maison", None)


def test_rejects_an_empty_page(tmp_path):
    with pytest.raises(ValueError, match="aucune entité"):
        load_options(write(tmp_path, "pages:\n  - title: T\n    lights: []\n"))


def test_shipped_example_is_valid():
    assert load_options(EXAMPLE) == Options(DEFAULT_TITLE, "", None)


def test_env(tmp_path, monkeypatch):
    path = write(tmp_path, "dashboard: maison\n")
    monkeypatch.setenv("HA_URL", "http://ha:8123/")
    monkeypatch.setenv("HA_TOKEN", "secret")
    monkeypatch.setenv("OPTIONS_FILE", str(path))
    monkeypatch.delenv("LISTEN_HOST", raising=False)
    monkeypatch.delenv("LISTEN_PORT", raising=False)
    config = load_config()
    assert config.ha_url == "http://ha:8123"
    assert config.listen_host == "0.0.0.0"
    assert config.listen_port == 3615
    assert config.options.dashboard == "maison"


def test_env_accepts_the_old_variable_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HA_URL", "http://ha:8123")
    monkeypatch.setenv("HA_TOKEN", "secret")
    monkeypatch.delenv("OPTIONS_FILE", raising=False)
    monkeypatch.setenv("LIGHTS_FILE", str(write(tmp_path, "title: X\n")))
    assert load_config().options.title == "X"
