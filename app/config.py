"""Load application settings from the environment and an optional .env file."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pipeline.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ALLOW_LOCAL_WEBHOOKS = os.getenv("ALLOW_LOCAL_WEBHOOKS", "false").lower() == "true"
