
"""Central configuration for Hello Dodo managed services."""

from pathlib import Path
import os


HELLO_DODO_BACKEND = Path(__file__).resolve().parent

JOB_AGENT_ROOT = Path(
    os.getenv(
        "JOB_AGENT_ROOT",
        r"D:\Devlopment\job-application-agent",
    )
)
JOB_AGENT_BACKEND = JOB_AGENT_ROOT / "backend"
JOB_AGENT_PYTHON = JOB_AGENT_BACKEND / ".venv" / "Scripts" / "python.exe"

HELLO_DODO_HOST = "127.0.0.1"
HELLO_DODO_PORT = 8000

JOB_AGENT_HOST = "127.0.0.1"
JOB_AGENT_PORT = 8001
JOB_AGENT_URL = f"http://{JOB_AGENT_HOST}:{JOB_AGENT_PORT}"

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5173
DASHBOARD_URL = f"http://localhost:{DASHBOARD_PORT}/"

DODO_BRIDGE_HOST = "127.0.0.1"
DODO_BRIDGE_PORT = 8766
DODO_BRIDGE_URL = (
    f"http://{DODO_BRIDGE_HOST}:{DODO_BRIDGE_PORT}"
)

SERVICE_START_TIMEOUT_SECONDS = 25
HEALTH_CHECK_TIMEOUT_SECONDS = 2