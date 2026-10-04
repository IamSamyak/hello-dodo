
"""Start and verify services required by Hello Dodo tools."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from service_config import (
    DASHBOARD_URL,
    DODO_BRIDGE_HOST,
    DODO_BRIDGE_PORT,
    DODO_BRIDGE_URL,
    HEALTH_CHECK_TIMEOUT_SECONDS,
    HELLO_DODO_BACKEND,
    JOB_AGENT_BACKEND,
    JOB_AGENT_HOST,
    JOB_AGENT_PORT,
    JOB_AGENT_PYTHON,
    JOB_AGENT_URL,
    SERVICE_START_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_job_agent_process: subprocess.Popen | None = None
_bridge_process: subprocess.Popen | None = None

# Prevent simultaneous voice requests from starting duplicate processes.
_service_lock = threading.RLock()


def _request_json(
    url: str,
    *,
    method: str = "GET",
    timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
) -> dict | None:
    request = Request(url, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result if isinstance(result, dict) else None
    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        ValueError,
    ):
        return None


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop a process started by this manager if startup fails."""
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    if process.poll() is None:
        process.terminate()

        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def is_job_agent_running() -> bool:
    result = _request_json(f"{JOB_AGENT_URL}/health")

    return bool(
        result
        and result.get("status") in {"ok", "online", "healthy"}
    )



def ensure_job_agent_running() -> None:
    """Start Job Agent and include captured startup logs on failure."""
    global _job_agent_process

    with _service_lock:
        if is_job_agent_running():
            return

        if _port_is_open(JOB_AGENT_HOST, JOB_AGENT_PORT):
            raise RuntimeError(
                f"Port {JOB_AGENT_PORT} is occupied, but the Job Agent "
                "health check failed. Refusing to start a duplicate process."
            )

        if not JOB_AGENT_PYTHON.is_file():
            raise RuntimeError(
                f"Job Agent Python environment was not found: "
                f"{JOB_AGENT_PYTHON}"
            )

        if not JOB_AGENT_BACKEND.is_dir():
            raise RuntimeError(
                f"Job Agent backend directory was not found: "
                f"{JOB_AGENT_BACKEND}"
            )

        log_path = JOB_AGENT_BACKEND / "service-manager-job-agent.log"
        logger.info("Starting Job Agent; startup log: %s", log_path)

        try:
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write("\n\n--- Job Agent startup attempt ---\n")
                log_file.flush()

                process = subprocess.Popen(
                    [
                        str(JOB_AGENT_PYTHON),
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        JOB_AGENT_HOST,
                        "--port",
                        str(JOB_AGENT_PORT),
                    ],
                    cwd=str(JOB_AGENT_BACKEND),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0,
                    ),
                )
        except OSError as exc:
            raise RuntimeError(
                f"Could not launch Job Agent. Details: {exc}"
            ) from exc

        _job_agent_process = process
        deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            if is_job_agent_running():
                logger.info("Job Application Agent is healthy.")
                return

            if process.poll() is not None:
                break

            time.sleep(0.5)

        _terminate_process_tree(process)
        exit_code = process.poll()
        _job_agent_process = None

        try:
            lines = log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
            recent_logs = "\n".join(lines[-30:])
        except OSError:
            recent_logs = "(Could not read the startup log.)"

        raise RuntimeError(
            "Job Application Agent failed to become healthy within "
            f"{SERVICE_START_TIMEOUT_SECONDS} seconds. "
            f"Exit code: {exit_code}.\n"
            f"Startup log ({log_path}):\n{recent_logs}"
        )

def ensure_dodo_bridge_running() -> None:
    """Start the existing Dodo bridge only if it is stopped."""
    global _bridge_process

    with _service_lock:
        health_url = f"{DODO_BRIDGE_URL}/health"
        result = _request_json(health_url)

        if result and result.get("ok") is True:
            return

        if _port_is_open(DODO_BRIDGE_HOST, DODO_BRIDGE_PORT):
            raise RuntimeError(
                f"Port {DODO_BRIDGE_PORT} is occupied, but the Dodo bridge "
                "health check failed. Refusing to start a duplicate process."
            )

        bridge_script = HELLO_DODO_BACKEND / "dodo_bridge.py"

        if not bridge_script.is_file():
            raise RuntimeError(
                f"Dodo bridge script was not found: {bridge_script}"
            )

        logger.info(
            "Starting Dodo bridge on port %s",
            DODO_BRIDGE_PORT,
        )

        process = subprocess.Popen(
            [sys.executable, str(bridge_script)],
            cwd=str(HELLO_DODO_BACKEND),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            ),
        )

        _bridge_process = process
        deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            result = _request_json(health_url)

            if result and result.get("ok") is True:
                logger.info("Dodo bridge is healthy.")
                return

            if process.poll() is not None:
                break

            time.sleep(0.5)

        exit_code = process.poll()
        _terminate_process_tree(process)
        _bridge_process = None

        raise RuntimeError(
            "Dodo bridge failed to become healthy within "
            f"{SERVICE_START_TIMEOUT_SECONDS} seconds "
            f"(exit code: {exit_code})."
        )


def ensure_dashboard_running() -> str:
    """Reuse Job Agent's existing frontend manager endpoint."""
    ensure_job_agent_running()

    result = _request_json(
        f"{JOB_AGENT_URL}/frontend/ensure",
        method="POST",
        timeout=SERVICE_START_TIMEOUT_SECONDS,
    )

    if not result or result.get("status") != "ok":
        raise RuntimeError(
            "The dashboard could not be started through the existing "
            "Job Agent /frontend/ensure endpoint."
        )

    return str(result.get("url") or DASHBOARD_URL)


def _find_edge_executable() -> str | None:
    discovered = shutil.which("msedge.exe") or shutil.which("msedge")

    if discovered:
        return discovered

    candidates = (
        Path(
            os.environ.get(
                "PROGRAMFILES(X86)",
                r"C:\Program Files (x86)",
            )
        )
        / "Microsoft/Edge/Application/msedge.exe",
        Path(
            os.environ.get(
                "PROGRAMFILES",
                r"C:\Program Files",
            )
        )
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft/Edge/Application/msedge.exe",
    )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


def open_edge_for_job_action(action: str) -> None:
    """Open the appropriate page; this does not prove extension readiness."""
    targets = {
        "NAUKRI_OPEN_JOBS": "https://www.naukri.com/",
        "LINKEDIN_GET_JOBS": "https://www.linkedin.com/jobs/",
        "LINKEDIN_APPLY_EASY_APPLY": "https://www.linkedin.com/jobs/",
        "JOB_APPLICATION_DASHBOARD": DASHBOARD_URL,
    }

    target_url = targets.get(action)

    if not target_url:
        return

    edge_executable = _find_edge_executable()

    if not edge_executable:
        raise RuntimeError(
            "Microsoft Edge could not be located. "
            "Open Edge and enable the required extension."
        )

    try:
        subprocess.Popen(
            [edge_executable, target_url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            ),
        )
    except OSError as exc:
        raise RuntimeError(
            f"Could not launch Microsoft Edge: {exc}"
        ) from exc



def prepare_job_action(
    action: str,
    trace_id: str = "-",
) -> str | None:
    """Prepare required services and log each local execution stage."""
    started = time.monotonic()

    logger.info(
        "TRACE=%s PREPARE_JOB_ACTION_START action=%s",
        trace_id,
        action,
    )

    try:
        ensure_job_agent_running()

        logger.info(
            "TRACE=%s JOB_AGENT_READY action=%s",
            trace_id,
            action,
        )

        dashboard_url = None

        if action == "JOB_APPLICATION_DASHBOARD":
            dashboard_url = ensure_dashboard_running()

            logger.info(
                "TRACE=%s DASHBOARD_SERVICE_READY url=%s",
                trace_id,
                dashboard_url,
            )

        if action in {
            "NAUKRI_OPEN_JOBS",
            "LINKEDIN_GET_JOBS",
            "LINKEDIN_APPLY_EASY_APPLY",
            "JOB_APPLICATION_DASHBOARD",
        }:
            open_edge_for_job_action(action)

            logger.info(
                "TRACE=%s EDGE_LAUNCH_REQUESTED action=%s",
                trace_id,
                action,
            )

        logger.info(
            "TRACE=%s PREPARE_JOB_ACTION_END action=%s elapsed=%.2fs",
            trace_id,
            action,
            time.monotonic() - started,
        )

        return dashboard_url

    except Exception:
        logger.exception(
            "TRACE=%s PREPARE_JOB_ACTION_FAILED action=%s elapsed=%.2fs",
            trace_id,
            action,
            time.monotonic() - started,
        )
        raise