"""Configuration : variables d'environnement et fichier d'options."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TITLE = "3615 MAISON"


@dataclass(frozen=True)
class Options:
    title: str = DEFAULT_TITLE
    dashboard: str = ""
    """Le tableau de bord de Home Assistant à afficher. Vide : celui par défaut."""


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    listen_host: str
    listen_port: int
    options: Options


def load_options(path: Path) -> Options:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    title = raw.get("title") or DEFAULT_TITLE
    dashboard = (raw.get("dashboard") or "").strip()
    return Options(title, dashboard)


def load_config() -> Config:
    # LIGHTS_FILE : l'ancien nom, du temps où le fichier ne listait que des lumières.
    path = os.environ.get("OPTIONS_FILE") or os.environ.get("LIGHTS_FILE") or "minitel.yaml"
    return Config(
        ha_url=os.environ["HA_URL"].rstrip("/"),
        ha_token=os.environ["HA_TOKEN"],
        listen_host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        listen_port=int(os.environ.get("LISTEN_PORT", "3615")),
        options=load_options(Path(path)),
    )
