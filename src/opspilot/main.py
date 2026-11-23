"""Hugging Face Docker Space and local Compose entrypoint."""

import logging

from opspilot.api.app import build_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = build_app()
