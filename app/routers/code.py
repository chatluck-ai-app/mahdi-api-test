import os
import resource
import shutil
import subprocess
import tempfile
import time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import config
from ..auth import current_device
from ..db import Device

router = APIRouter(prefix="/v1", tags=["code"])

RUN_TIMEOUT = config.CODE_RUN_TIMEOUT


def _net_isolation_supported() -> bool:
    if not shutil.which("unshare"):
        return False
    try:
        result = subprocess.run(
            ["unshare", "--net", "--map-root-user", "true"],
            timeout=5,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


NET_ISOLATED = _net_isolation_supported()


def _limits() -> None:
    mem = config.CODE_RUN_MAX_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT, RUN_TIMEOUT + 2))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))


def _truncate(text: str) -> str:
    limit = config.CODE_RUN_MAX_OUTPUT
    if len(text) > limit:
        return text[:limit] + "\n...(output truncated)"
    return text


class CodeRunIn(BaseModel):
    code: str
    language: Literal["python"] = "python"


class CodeRunOut(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    network_isolated: bool


@router.post("/code/run", response_model=CodeRunOut)
def run_code(body: CodeRunIn, device: Device = Depends(current_device)):
    if not body.code.strip():
        return CodeRunOut(
            stdout="", stderr="empty code", exit_code=1, timed_out=False,
            duration_ms=0, network_isolated=NET_ISOLATED,
        )
    prefix = ["unshare", "--net", "--map-root-user"] if NET_ISOLATED else []
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as workdir:
        script = os.path.join(workdir, "main.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(body.code)
        try:
            proc = subprocess.run(
                prefix + ["python3", "-I", script],
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin", "HOME": workdir},
                preexec_fn=_limits,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return CodeRunOut(
                stdout=_truncate(proc.stdout),
                stderr=_truncate(proc.stderr),
                exit_code=proc.returncode,
                timed_out=False,
                duration_ms=duration_ms,
                network_isolated=NET_ISOLATED,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
            return CodeRunOut(
                stdout=_truncate(stdout),
                stderr=_truncate(stderr) or f"timed out after {RUN_TIMEOUT}s",
                exit_code=-1,
                timed_out=True,
                duration_ms=duration_ms,
                network_isolated=NET_ISOLATED,
            )
