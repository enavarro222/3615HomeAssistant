"""Bout en bout : un faux Home Assistant, le vrai client, un socket TCP et un Minitel simulé."""

import asyncio

import pytest

from minitel_ha import hass as hass_module
from minitel_ha import session as session_module
from minitel_ha import videotex as vt
from minitel_ha.config import DEFAULT_TITLE
from minitel_ha.dashboard import Dashboard, Item, View
from minitel_ha.hass import Hass
from minitel_ha.session import FIELD_COL, HELP_ROW, MESSAGE_ROW, PROMPT_ROW, SUBTITLE_ROW, Session

from .fake_hass import FakeHass
from .screen import Screen

ENVOI = b"\x13A"
RETOUR = b"\x13B"
REPETITION = b"\x13C"
GUIDE = b"\x13D"
ANNULATION = b"\x13E"
SOMMAIRE = b"\x13F"
CORRECTION = b"\x13G"
SUITE = b"\x13H"
FIN = b"\x13I"

TOP = 5  # première rangée de contenu

MAISON = {
    "views": [
        {
            "title": "Salon",
            "sections": [
                {
                    "cards": [
                        {"type": "heading", "heading": "Salon", "badges": ["sensor.salon_temp"]},
                        {"type": "tile", "entity": "light.salon", "name": "Plafonnier"},
                        {"type": "tile", "entity": "cover.volet", "name": "Volet"},
                        {"type": "tile", "entity": "sensor.salon_temp", "name": "Temperature"},
                        {"type": "tile", "entity": "lock.porte", "name": "Porte"},
                        {"type": "tile", "entity": "scene.cinema", "name": "Cinema"},
                        {"type": "tile", "entity": "light.cassee", "name": "Cassee"},
                    ]
                },
                {
                    "visibility": [{"condition": "state", "entity": "binary_sensor.fenetre", "state": "on"}],
                    "cards": [
                        {"type": "heading", "heading": "Alerte"},
                        {"type": "tile", "entity": "binary_sensor.fenetre", "name": "Fenetre"},
                    ],
                },
            ],
        },
        {"title": "Etage", "cards": [{"type": "tile", "entity": "light.chambre", "name": "Chambre"}]},
    ]
}


def line(left, value=""):
    """Une rangée de 40 colonnes : le texte à gauche, la valeur calée à droite."""
    return left.ljust(40 - len(value)) + value


def make_fake():
    fake = FakeHass()
    fake.add_dashboard("maison", "Maison", MAISON)
    fake.system_data = {"default_panel": "maison"}
    fake.states = {
        "light.salon": ("off", {}),
        "cover.volet": ("open", {"current_position": 40}),
        "sensor.salon_temp": ("21.52", {"unit_of_measurement": "°C"}),
        "lock.porte": ("locked", {}),
        "scene.cinema": ("2026-01-01T00:00:00+00:00", {}),
        "light.cassee": ("unavailable", {}),
        "binary_sensor.fenetre": ("off", {"device_class": "window"}),
        "light.chambre": ("on", {}),
    }
    return fake


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hass_module, "RELOAD_DELAY", 0.01)
    monkeypatch.setattr(hass_module, "RECONNECT_MIN", 0.05)


class Minitel:
    """Côté Minitel de la connexion : envoie des touches, tient l'écran à jour."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.screen = Screen()
        self.received = bytearray()

    async def settle(self, quiet=0.05):
        while True:
            try:
                data = await asyncio.wait_for(self.reader.read(4096), quiet)
            except TimeoutError:
                return
            if not data:
                return
            self.received += data
            self.screen.feed(data)

    async def press(self, keys):
        self.writer.write(keys)
        await self.writer.drain()
        await self.settle()

    def row(self, row):
        return self.screen.row_text(row)

    def find(self, text):
        """La rangée qui commence par ce texte (sans le numéro)."""
        for row in range(TOP, TOP + 16):
            if self.row(row)[3:].startswith(text):
                return row
        raise AssertionError(f"{text!r} absent de l'écran")


def call(scenario, fake=None, option="", fixed=None, title=DEFAULT_TITLE):
    fake = fake or make_fake()

    async def run():
        url = await fake.start()
        hass = Hass(url, "secret", option, fixed)
        await hass.start()
        assert await hass.wait_ready(2)

        async def handle(reader, writer):
            try:
                await Session(reader, writer, hass, title).run()
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with server:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    minitel = Minitel(reader, writer)
                    await minitel.settle()
                    await scenario(minitel, fake)
                finally:
                    writer.close()
        finally:
            await hass.close()
            await fake.stop()

    asyncio.run(asyncio.wait_for(run(), 10))


async def open_salon(minitel):
    await minitel.press(b"1" + ENVOI)


# --- Sommaire et navigation ----------------------------------------------------------------------


def test_home_is_the_summary():
    async def scenario(minitel, fake):
        assert minitel.row(2).strip() == "3615 MAISON"
        assert minitel.row(SUBTITLE_ROW) == "Maison"
        assert minitel.row(TOP) == "  1  Salon"
        assert minitel.row(TOP + 1) == "  2  Etage"
        assert minitel.row(PROMPT_ROW) == "Votre choix :"
        assert (minitel.screen.row, minitel.screen.col) == (PROMPT_ROW, FIELD_COL)
        assert minitel.screen.cursor_on
        assert len(minitel.received) < 120 * 3  # 3 s à 1200 bauds

    call(scenario)


def test_a_single_view_opens_directly():
    fixed = Dashboard("Sommaire", (View("Lumieres", (Item("light.salon", "Salon"),)),))

    async def scenario(minitel, fake):
        assert minitel.row(SUBTITLE_ROW) == "Lumieres"
        assert minitel.row(TOP) == line(" 1 Salon", "ETEINT")

    call(scenario, fixed=fixed)


def test_view_lists_entities_and_numbers_what_can_be_commanded():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        assert minitel.row(SUBTITLE_ROW) == "Salon"
        assert minitel.row(TOP) == line(" Salon", "21,5?C")
        assert minitel.row(TOP + 1) == line(" 1 Plafonnier", "ETEINT")
        assert minitel.row(TOP + 2) == line(" 2 Volet", "OUVERT 40%")
        assert minitel.row(TOP + 3) == line("   Temperature", "21,5?C")
        assert minitel.row(TOP + 4) == line(" 3 Porte", "VERROUILLE")
        assert minitel.row(TOP + 5) == line(" 4 Cinema", "LANCER")
        assert minitel.row(TOP + 6) == line(" 5 Cassee", "HS")
        assert minitel.row(TOP + 7) == ""  # la fenêtre est fermée : pas d'alerte
        assert minitel.row(HELP_ROW).startswith("Numero + ENVOI")
        assert len(minitel.received) < 120 * 6

    call(scenario)


def test_suite_and_retour_walk_through_views_and_sommaire_goes_home():
    async def scenario(minitel, fake):
        await minitel.press(SUITE)
        assert minitel.row(SUBTITLE_ROW) == "Salon"
        await minitel.press(SUITE)
        assert minitel.row(SUBTITLE_ROW) == "Etage"
        await minitel.press(SUITE)
        assert minitel.row(SUBTITLE_ROW) == "Salon"
        await minitel.press(RETOUR)
        assert minitel.row(SUBTITLE_ROW) == "Etage"
        await minitel.press(SOMMAIRE)
        assert minitel.row(SUBTITLE_ROW) == "Maison"

    call(scenario)


def test_numbers_refer_to_the_current_view():
    async def scenario(minitel, fake):
        await minitel.press(b"2" + ENVOI)
        await minitel.press(b"1" + ENVOI)
        assert fake.service_calls[0]["target"] == {"entity_id": "light.chambre"}

    call(scenario)


@pytest.mark.parametrize("entry", [b"3", b"0", b"99", b""])
def test_unknown_number_in_the_summary(entry):
    async def scenario(minitel, fake):
        await minitel.press(entry + ENVOI)
        assert minitel.row(MESSAGE_ROW) == "Numero inconnu"
        assert minitel.row(SUBTITLE_ROW) == "Maison"

    call(scenario)


def test_guide_shows_the_keys_and_any_key_comes_back():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(GUIDE)
        assert minitel.row(SUBTITLE_ROW) == "Aide"
        assert any("raccrocher" in minitel.row(row) for row in range(TOP, TOP + 16))
        await minitel.press(b"7")
        assert minitel.row(SUBTITLE_ROW) == "Salon"
        assert minitel.row(PROMPT_ROW) == "Votre choix :"  # la touche ne compte pas

    call(scenario)


def test_repetition_redraws():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        minitel.received.clear()
        await minitel.press(REPETITION)
        assert minitel.received.startswith(vt.clear())
        assert minitel.row(TOP + 1).endswith("ETEINT")

    call(scenario)


# --- Commandes -----------------------------------------------------------------------------------


def test_number_and_envoi_toggles_then_only_that_line_is_redrawn():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        minitel.received.clear()
        await minitel.press(b"1")
        assert minitel.row(PROMPT_ROW) == "Votre choix : 1"
        await minitel.press(ENVOI)
        call = fake.service_calls[0]
        assert (call["domain"], call["service"], call["target"]) == (
            "light",
            "toggle",
            {"entity_id": "light.salon"},
        )
        assert minitel.row(TOP + 1).endswith("ALLUME")
        assert minitel.screen.is_inverse(TOP + 1, 40)
        assert minitel.row(PROMPT_ROW) == "Votre choix :"
        assert (minitel.screen.row, minitel.screen.col) == (PROMPT_ROW, FIELD_COL)
        assert len(minitel.received) < 100

    call(scenario)


def test_number_and_suite_adjusts_and_keeps_the_number():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"2" + SUITE)
        await minitel.press(SUITE)
        assert [c["service"] for c in fake.service_calls] == ["set_cover_position"] * 2
        assert fake.service_calls[0]["service_data"] == {"position": 50}
        assert minitel.row(PROMPT_ROW) == "Votre choix : 2"
        await minitel.press(ANNULATION + SUITE)  # sans numéro : écran suivant
        assert minitel.row(SUBTITLE_ROW) == "Etage"

    call(scenario)


def test_a_lock_asks_for_confirmation():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"3" + ENVOI)
        assert fake.service_calls == []
        assert minitel.row(MESSAGE_ROW) == "Porte : ENVOI pour confirmer"
        await minitel.press(ENVOI)
        assert [(c["domain"], c["service"]) for c in fake.service_calls] == [("lock", "unlock")]

    call(scenario)


def test_any_other_key_cancels_the_confirmation():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"3" + ENVOI)
        await minitel.press(ANNULATION + ENVOI)
        assert fake.service_calls == []
        assert minitel.row(MESSAGE_ROW) == "Numero inconnu"

    call(scenario)


def test_a_scene_says_it_was_launched():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"4" + ENVOI)
        assert (fake.service_calls[0]["domain"], fake.service_calls[0]["service"]) == ("scene", "turn_on")
        assert minitel.row(MESSAGE_ROW) == "Cinema : lance"

    call(scenario)


def test_unavailable_entity_is_not_commanded():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"5" + ENVOI)
        assert fake.service_calls == []
        assert minitel.row(MESSAGE_ROW) == "Cassee : indisponible"

    call(scenario)


@pytest.mark.parametrize("entry", [b"6", b"0", b""])
def test_unknown_number_in_a_view(entry):
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(entry + ENVOI)
        assert fake.service_calls == []
        assert minitel.row(MESSAGE_ROW) == "Numero inconnu"

    call(scenario)


def test_field_editing():
    async def scenario(minitel, fake):
        await minitel.press(b"a123")
        assert minitel.row(PROMPT_ROW) == "Votre choix : 12"
        await minitel.press(CORRECTION)
        assert minitel.row(PROMPT_ROW) == "Votre choix : 1"
        await minitel.press(ANNULATION)
        assert minitel.row(PROMPT_ROW) == "Votre choix :"
        assert minitel.screen.col == FIELD_COL
        minitel.received.clear()
        await minitel.press(CORRECTION)
        assert minitel.received == b""

    call(scenario)


def test_shorter_message_erases_the_previous_one():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"5" + ENVOI)
        await minitel.press(b"9" + ENVOI)
        assert minitel.row(MESSAGE_ROW) == "Numero inconnu"

    call(scenario)


# --- En direct -----------------------------------------------------------------------------------


def test_state_changes_redraw_only_what_changed():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        minitel.received.clear()
        await fake.set_state("sensor.salon_temp", "21.54")  # même affichage : rien n'est envoyé
        await minitel.settle()
        assert minitel.received == b""
        await fake.set_state("sensor.salon_temp", "22.0")
        await minitel.settle()
        assert minitel.row(TOP).endswith("22,0?C")  # le titre de la section
        assert minitel.row(TOP + 3).endswith("22,0?C")
        assert len(minitel.received) < 120
        assert (minitel.screen.row, minitel.screen.col) == (PROMPT_ROW, FIELD_COL)

    call(scenario)


def test_typing_survives_a_live_update():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await minitel.press(b"1")
        await fake.set_state("light.salon", "on")
        await minitel.settle()
        assert (minitel.screen.row, minitel.screen.col) == (PROMPT_ROW, FIELD_COL + 1)
        await minitel.press(ENVOI)
        assert fake.service_calls[0]["target"] == {"entity_id": "light.salon"}

    call(scenario)


def test_sections_appear_and_disappear_with_their_conditions():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        await fake.set_state("binary_sensor.fenetre", "on")
        await minitel.settle()
        assert minitel.row(TOP + 7) == " Alerte"
        assert minitel.row(TOP + 8) == line("   Fenetre", "OUVERT")
        await fake.set_state("binary_sensor.fenetre", "off")
        await minitel.settle()
        assert minitel.row(TOP + 7) == ""
        assert minitel.row(TOP + 8) == ""

    call(scenario)


def test_edited_dashboard_is_redrawn():
    async def scenario(minitel, fake):
        await open_salon(minitel)
        fake.configs["maison"] = {
            "views": [{"title": "Nouveau", "cards": [{"type": "tile", "entity": "light.salon"}]}]
        }
        await fake.fire("lovelace_updated", {"url_path": "maison"})
        await minitel.settle(0.2)
        assert minitel.row(SUBTITLE_ROW) == "Nouveau"
        assert minitel.row(MESSAGE_ROW) == "Tableau de bord mis a jour"

    call(scenario)


def test_home_assistant_down_then_back(monkeypatch):
    monkeypatch.setattr(hass_module, "RECONNECT_MIN", 0.5)

    async def scenario(minitel, fake):
        await open_salon(minitel)
        await fake.drop()
        await minitel.settle()
        assert minitel.row(0) == "Home Assistant injoignable"
        await minitel.press(b"1" + ENVOI)
        assert minitel.row(MESSAGE_ROW) == "Home Assistant injoignable"
        assert minitel.row(TOP + 1).endswith("ETEINT")  # l'écran reste
        minitel.received.clear()
        await minitel.settle(0.8)  # reconnexion
        assert minitel.row(0) == ""
        assert not minitel.received.startswith(vt.clear())  # pas tout l'écran pour si peu

    call(scenario)


# --- Fin d'appel ---------------------------------------------------------------------------------


def test_connexion_fin_says_goodbye_and_closes():
    async def scenario(minitel, fake):
        await minitel.press(FIN)
        assert "Au revoir" in minitel.row(12)
        assert minitel.received.endswith(vt.disconnect())
        assert await minitel.reader.read() == b""

    call(scenario)


def test_idle_call_is_hung_up(monkeypatch):
    monkeypatch.setattr(session_module, "IDLE_TIMEOUT", 0.1)

    async def scenario(minitel, fake):
        await asyncio.sleep(0.2)
        await minitel.settle()
        assert "Deconnexion" in minitel.row(12)
        assert await minitel.reader.read() == b""

    call(scenario)


def test_title_is_centered():
    async def scenario(minitel, fake):
        assert minitel.row(2).strip() == "3615 CHEZ NOUS"
        assert minitel.row(2).index("3") == 13

    call(scenario, title="3615 CHEZ NOUS")
