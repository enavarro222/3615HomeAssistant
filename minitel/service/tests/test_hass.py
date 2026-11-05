import asyncio
import logging

import pytest

from minitel_ha import hass as hass_module
from minitel_ha.dashboard import Heading, Item
from minitel_ha.hass import Change, EntityState, HAError, Hass, websocket_url

from .fake_hass import FakeHass

FAMILLE = {
    "views": [
        {
            "title": "Pièces",
            "sections": [
                {
                    "cards": [
                        {"type": "heading", "heading": "Salon", "badges": ["sensor.salon_temp"]},
                        {"type": "tile", "entity": "light.salon", "name": "Plafonnier"},
                    ]
                }
            ],
        }
    ]
}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hass_module, "RELOAD_DELAY", 0.01)
    monkeypatch.setattr(hass_module, "RECONNECT_MIN", 0.01)
    monkeypatch.setattr(hass_module, "CALL_TIMEOUT", 2)


def make_fake():
    fake = FakeHass()
    fake.add_dashboard("famille", "Famille", FAMILLE)
    fake.add_dashboard("vide", "Vide", None)  # créé mais jamais enregistré
    fake.states = {
        "light.salon": ("off", {"friendly_name": "Salon", "brightness": None}),
        "sensor.salon_temp": ("21.5", {"unit_of_measurement": "°C"}),
        "light.cuisine": ("on", {"friendly_name": "Cuisine"}),
    }
    fake.areas = [{"area_id": "cuisine", "name": "Cuisine", "floor_id": None}]
    fake.entities = [{"entity_id": "light.cuisine", "area_id": "cuisine"}]
    return fake


def run(scenario, fake=None, option="", token="secret"):
    fake = fake or make_fake()

    async def main():
        url = await fake.start()
        hass = Hass(url, token, option)
        await hass.start()
        try:
            await scenario(hass, fake)
        finally:
            await hass.close()
            await fake.stop()

    asyncio.run(asyncio.wait_for(main(), 10))


async def next_event(queue, timeout=2):
    return await asyncio.wait_for(queue.get(), timeout)


@pytest.mark.parametrize(
    ("ha_url", "expected"),
    [
        ("http://supervisor/core", "ws://supervisor/core/api/websocket"),
        ("http://192.168.1.10:8123/", "ws://192.168.1.10:8123/api/websocket"),
        ("https://ha.example.org", "wss://ha.example.org/api/websocket"),
    ],
)
def test_websocket_url(ha_url, expected):
    assert websocket_url(ha_url) == expected


def test_loads_the_chosen_dashboard_and_its_states():
    async def scenario(hass, fake):
        assert await hass.wait_ready(2)
        assert hass.connected
        assert hass.dashboard.title == "Famille"
        assert hass.dashboard.views[0].elements[1] == Item("light.salon", "Plafonnier")
        # Seulement les entités du tableau de bord, badges compris.
        assert hass.states == {
            "light.salon": EntityState("off", {"friendly_name": "Salon", "brightness": None}),
            "sensor.salon_temp": EntityState("21.5", {"unit_of_measurement": "°C"}),
        }
        assert hass.state_values() == {"light.salon": "off", "sensor.salon_temp": "21.5"}

    run(scenario, option="famille")


def test_follows_the_default_dashboard_of_home_assistant():
    fake = make_fake()
    fake.system_data = {"default_panel": "famille"}

    async def scenario(hass, fake):
        assert await hass.wait_ready(2)
        assert hass.dashboard.title == "Famille"

    run(scenario, fake)


@pytest.mark.parametrize(
    ("option", "system_data", "message"),
    [
        ("", {"default_panel": "home"}, None),  # le tableau de bord intégré : rien à lire
        ("", None, None),  # Home Assistant trop ancien pour le réglage
        ("inconnu", {"default_panel": None}, "« inconnu » introuvable"),
        ("vide", {"default_panel": None}, "« vide » jamais enregistré"),
    ],
)
def test_falls_back_to_areas(option, system_data, message, caplog):
    fake = make_fake()
    fake.system_data = system_data

    async def scenario(hass, fake):
        assert await hass.wait_ready(2)
        assert hass.dashboard.title == "Pièces"
        assert [v.title for v in hass.dashboard.views] == ["Cuisine"]
        assert hass.dashboard.views[0].elements == (Heading("Lumières"), Item("light.cuisine", "Cuisine"))
        assert hass.state_values() == {"light.cuisine": "on"}

    with caplog.at_level(logging.INFO, logger="minitel_ha"):
        run(scenario, fake, option)
    if message:
        assert message in caplog.text
    assert "généré à partir des pièces" in caplog.text


def test_state_changes_reach_listeners():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        with hass.listen() as queue:
            await fake.set_state("light.salon", "on", brightness=128)
            assert await next_event(queue) == frozenset({"light.salon"})
        assert hass.states["light.salon"] == EntityState("on", {"friendly_name": "Salon", "brightness": 128})

    run(scenario, option="famille")


def test_compressed_diffs():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        hass._on_entities({"c": {"light.salon": {"+": {"a": {"x": 1}}, "-": {"a": ["brightness"]}}}})
        assert hass.states["light.salon"] == EntityState("off", {"friendly_name": "Salon", "x": 1})
        hass._on_entities({"r": ["light.salon"], "c": {"light.inconnue": {"+": {"s": "on"}}}})
        assert "light.salon" not in hass.states
        assert "light.inconnue" not in hass.states

    run(scenario, option="famille")


def test_call_service_then_the_new_state_arrives():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        with hass.listen() as queue:
            await hass.call_service("light", "toggle", "light.salon")
            assert await next_event(queue) == frozenset({"light.salon"})
        assert hass.states["light.salon"].state == "on"
        call = fake.service_calls[0]
        assert (call["domain"], call["service"], call["target"]) == (
            "light",
            "toggle",
            {"entity_id": "light.salon"},
        )

    run(scenario, option="famille")


def test_a_refused_command_raises():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        with pytest.raises(HAError) as err:
            await hass._call({"type": "nope"})
        assert err.value.code == "unknown_command"

    run(scenario, option="famille")


def test_reloads_when_the_dashboard_is_edited():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        with hass.listen() as queue:
            await fake.fire("lovelace_updated", {"url_path": "autre"})  # pas le nôtre
            fake.configs["famille"] = {
                "views": [{"title": "Cuisine", "cards": [{"type": "tile", "entity": "light.cuisine"}]}]
            }
            await fake.fire("lovelace_updated", {"url_path": "famille"})
            await fake.fire("lovelace_updated", {"url_path": "famille"})  # une rafale : un seul rechargement
            event = await next_event(queue)
            while event != Change.DASHBOARD:
                event = await next_event(queue)
            await asyncio.sleep(0.05)
            assert queue.empty()
        assert [v.title for v in hass.dashboard.views] == ["Cuisine"]
        # Les états suivent les nouvelles entités.
        assert hass.state_values() == {"light.cuisine": "on"}
        assert fake.commands.count("lovelace/config") == 2

    run(scenario, option="famille")


def test_areas_reload_when_registries_change():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        fake.areas.append({"area_id": "salon", "name": "Salon", "floor_id": None})
        fake.entities.append({"entity_id": "light.salon", "area_id": "salon"})
        with hass.listen() as queue:
            await fake.fire("entity_registry_updated", {"action": "update"})
            while await next_event(queue) != Change.DASHBOARD:
                pass
        assert [v.title for v in hass.dashboard.views] == ["Cuisine", "Salon"]

    run(scenario)


def test_reconnects_after_home_assistant_restarts():
    async def scenario(hass, fake):
        await hass.wait_ready(2)
        with hass.listen() as queue:
            await fake.drop()
            assert await next_event(queue) == Change.CONNECTION
            assert not hass.connected
            with pytest.raises(HAError):
                await hass.call_service("light", "toggle", "light.salon")
            fake.states["light.salon"] = ("on", {})
            while await next_event(queue) != Change.CONNECTION:
                pass
        assert hass.connected
        assert fake.connections == 2
        assert hass.states["light.salon"].state == "on"  # rechargé à la reconnexion

    run(scenario, option="famille")


def test_a_refused_token_never_gets_ready(caplog):
    async def scenario(hass, fake):
        assert not await hass.wait_ready(0.3)
        assert not hass.connected

    with caplog.at_level(logging.WARNING, logger="minitel_ha"):
        run(scenario, token="faux")
    assert "jeton refusé" in caplog.text


def test_unknown_entities_of_the_dashboard_are_reported(caplog):
    fake = make_fake()
    config = {
        "views": [
            {
                "title": "Salon",
                "cards": [
                    {"type": "tile", "entity": "light.salon"},
                    {"type": "tile", "entity": "light.absente"},
                ],
            }
        ]
    }
    fake.add_dashboard("petit", "Petit", config)

    async def scenario(hass, fake):
        assert await hass.wait_ready(2)
        assert hass.state_values() == {"light.salon": "off"}

    with caplog.at_level(logging.INFO, logger="minitel_ha"):
        run(scenario, fake, option="petit")
    assert "entité inconnue de Home Assistant : light.absente" in caplog.text
