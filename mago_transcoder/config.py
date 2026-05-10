"""Paths and environment-driven settings (no secrets in code)."""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return (v.strip() if isinstance(v, str) else default) or default


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

MAGO_ROOT = _env("MAGO_ROOT", "/storage/inhouse/env/mago")

# ShotGrid / FastAPI optional vendored libs (studio layout)
FASTAPI_LIB_CANDIDATES = [
    MAGO_ROOT + "/common/fastapi_lib",
    "/storage/inhouse/env/mago/common/fastapi_lib",  # Explicit company path
    MAGO_ROOT + "/mago_ops/mago_ops_lib",
    MAGO_ROOT + "/mago_ops/mago_fastapi_lib",
    str(PACKAGE_DIR.parent / "mago_fastapi_lib"),
]

SG_LIBRARY_PATHS = [
    "/storage/inhouse/python_lib/python3.9/lib/python3.9/site-packages",
    "/storage/inhouse/python_lib/python3.9-django/lib/python3.9/site-packages",
    "/storage/inhouse/python_lib/python3.11/lib/python3.11/site-packages",
]

NUKE_EXEC = _env("NUKE_EXEC", "/storage/inhouse/rez/bin/mago_bin/nuke14.0v6x.sh")

_default_converter = REPO_ROOT / "nuke_converter.py"
NUKE_CONVERTER = _env("NUKE_CONVERTER", str(_default_converter))

OCIO_CONFIG_PATH = _env("OCIO_CONFIG", "/storage/inhouse/ocio/aces_1.2/config.ocio")

SG_ENV_PATH = _env("SG_ENV_FILE", str(Path(MAGO_ROOT) / "sg_comp_api"))

SG_SERVER = _env("SG_SERVER", "https://studiomago.shotgrid.autodesk.com")
SG_PROXY = _env("SG_PROXY", "192.168.50.200:3128")

HOST = _env("MAGO_HOST", "0.0.0.0")
PORT = int(_env("MAGO_PORT", "8000"))

# Load SG secrets into os.environ from line-based KEY=value file (optional)
def load_sg_env_file() -> None:
    path = SG_ENV_PATH
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def ensure_sys_path() -> None:
    """Studio modules (FastAPI bundle, shotgun_api3) — same idea as hwang_edit."""
    import sys

    for p in FASTAPI_LIB_CANDIDATES:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
            break
    for p in SG_LIBRARY_PATHS:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
