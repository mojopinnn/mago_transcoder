"""UI template injection + format definitions (no MXF)."""

from __future__ import annotations

import json
from pathlib import Path

from . import config

FORMAT_DEFS = {
    "exr": {
        "label": "EXR",
        "codecs": [
            {"id": "none", "label": "None (Uncompressed)"},
            {"id": "zip1", "label": "ZIP (Single Scanline)"},
            {"id": "zip16", "label": "ZIP (16 Scanlines)"},
            {"id": "piz", "label": "PIZ (Wavelet)"},
            {"id": "pxr24", "label": "PXR24 (Lossy)"},
            {"id": "b44", "label": "B44"},
            {"id": "b44a", "label": "B44A"},
            {"id": "dwaa", "label": "DWAA (Lossy)"},
            {"id": "dwab", "label": "DWAB (Lossy)"},
        ],
        "bitdepths": [
            {"id": "half", "label": "16-bit Half Float"},
            {"id": "float", "label": "32-bit Full Float"},
        ],
        "default_codec": "piz",
        "default_bitdepth": "half",
    },
    "dpx": {
        "label": "DPX",
        "codecs": [{"id": "raw", "label": "Raw (Uncompressed)"}],
        "bitdepths": [
            {"id": "8", "label": "8-bit"},
            {"id": "10", "label": "10-bit"},
            {"id": "12", "label": "12-bit"},
            {"id": "16", "label": "16-bit"},
        ],
        "default_codec": "raw",
        "default_bitdepth": "10",
    },
    "tiff": {
        "label": "TIFF",
        "codecs": [
            {"id": "none", "label": "None (Uncompressed)"},
            {"id": "lzw", "label": "LZW"},
            {"id": "deflate", "label": "Deflate"},
        ],
        "bitdepths": [
            {"id": "8", "label": "8-bit"},
            {"id": "16", "label": "16-bit"},
            {"id": "32", "label": "32-bit Float"},
        ],
        "default_codec": "none",
        "default_bitdepth": "16",
    },
    "png": {
        "label": "PNG",
        "codecs": [{"id": "default", "label": "Default"}],
        "bitdepths": [
            {"id": "8", "label": "8-bit"},
            {"id": "16", "label": "16-bit"},
        ],
        "default_codec": "default",
        "default_bitdepth": "8",
    },
    "jpg": {
        "label": "JPG",
        "codecs": [{"id": "default", "label": "Default"}],
        "bitdepths": [
            {"id": "8", "label": "8-bit"},
        ],
        "default_codec": "default",
        "default_bitdepth": "8",
    },
    "mov": {
        "label": "MOV",
        "codecs": [
            {"id": "h264", "label": "H.264"},
            {"id": "h265", "label": "H.265 (HEVC)"},
            {"id": "prores422", "label": "Apple ProRes 422"},
            {"id": "prores4444", "label": "Apple ProRes 4444"},
            {"id": "dnxhd", "label": "Avid DNxHD"},
        ],
        "bitdepths": [
            {"id": "8", "label": "8-bit"},
            {"id": "10", "label": "10-bit"},
        ],
        "default_codec": "prores422",
        "default_bitdepth": "10",
    },
}


def build_html(colorspaces: list[str], initial_ids: str = "", initial_names: str = "") -> str:
    html_path = Path(__file__).with_name("ui_page.html")
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("__FORMAT_DEFS_JSON__", json.dumps(FORMAT_DEFS, ensure_ascii=False))
    html = html.replace("__COLORSPACES_JSON__", json.dumps(colorspaces, ensure_ascii=False))
    html = html.replace("__OCIO_PATH__", config.OCIO_CONFIG_PATH)
    html = html.replace("__INITIAL_IDS__", json.dumps(initial_ids))
    html = html.replace("__INITIAL_NAMES__", json.dumps(initial_names))
    return html
