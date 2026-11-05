"""Serveur Vidéotex : une connexion TCP par appel Minitel."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .config import load_config
from .hass import Hass
from .session import Session
from .web import load_web_config, start_web

log = logging.getLogger("minitel_ha")

# Au démarrage de la machine, Home Assistant peut mettre un moment à répondre.
READY_TIMEOUT = 30


async def main() -> None:
    config = load_config()
    options = config.options
    web_config = load_web_config()
    hass = Hass(config.ha_url, config.ha_token, options.dashboard)
    web_runner = None

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("appel de %s", peer)
        try:
            await Session(reader, writer, hass, options.title).run()
        except ConnectionError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            log.info("fin de l'appel de %s", peer)

    try:
        await hass.start()
        if not await hass.wait_ready(READY_TIMEOUT):
            log.warning("Home Assistant ne répond pas encore : les appels attendront qu'il revienne")
        server = await asyncio.start_server(handle, config.listen_host, config.listen_port)
        log.info("en écoute sur %s:%d", config.listen_host, config.listen_port)
        if web_config:
            web_runner = await start_web(web_config)
        async with server:
            await server.serve_forever()
    finally:
        if web_runner:
            await web_runner.cleanup()
        await hass.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
