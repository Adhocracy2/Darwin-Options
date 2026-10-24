"""Dev dashboard API: the working copy's code on its own IBKR client id, reloaded on every edit.

Started by scripts/dev.sh. Uses the shared config and journal (~/darwin/shared), like production.
"""
import os

from darwin_options.api.app import create_app
from darwin_options.config import load_config


def app():
    cfg = load_config(os.environ.get("DARWIN_CONFIG", "config.yaml"))
    cfg.ibkr.client_id = int(os.environ.get("DARWIN_DEV_CLIENT_ID", "23"))  # never clash with the trader or darwin-web
    return create_app(cfg)
