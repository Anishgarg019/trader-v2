"""Configuration & environment loading.

Loads `.env` (secrets, mode, vault path) and `config/config.yaml` (static knobs).
Cross-platform: all paths via pathlib so this runs on Mac (dev) and Windows (prod).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"


@dataclass(frozen=True)
class Settings:
    mode: str
    kite_api_key: str | None
    kite_api_secret: str | None
    kite_access_token: str | None
    vault_path: Path
    config: dict[str, Any]

    @property
    def is_paper(self) -> bool:
        return self.mode.strip().lower() == "paper"


def load_settings(config_path: Path | str = DEFAULT_CONFIG_PATH,
                  env_path: Path | str | None = None) -> Settings:
    """Read .env + config.yaml into a Settings object.

    `.env` is loaded if present; real secrets never live in source or the repo.
    """
    if env_path is None:
        env_path = REPO_ROOT / ".env"
    # load_dotenv is a no-op if the file is absent; existing env vars win by default.
    load_dotenv(env_path)

    config: dict[str, Any] = {}
    cfg_file = Path(config_path)
    if cfg_file.exists():
        config = yaml.safe_load(cfg_file.read_text()) or {}

    vault = os.environ.get("VAULT_PATH") or config.get("paths", {}).get("vault", "./vault-dev")
    vault_path = Path(vault).expanduser()
    if not vault_path.is_absolute():
        vault_path = (REPO_ROOT / vault_path).resolve()

    return Settings(
        mode=os.environ.get("MODE", "paper"),
        kite_api_key=os.environ.get("KITE_API_KEY") or None,
        kite_api_secret=os.environ.get("KITE_API_SECRET") or None,
        kite_access_token=os.environ.get("KITE_ACCESS_TOKEN") or None,
        vault_path=vault_path,
        config=config,
    )
