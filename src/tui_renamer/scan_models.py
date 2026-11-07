"""Curated Ollama vision picks and conservative RAM hints.

Tags and sizes come from the official library pages, checked for this change.
They are not a handwriting-accuracy ranking. Download size is not runtime RAM.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LIGHT_RAM_BYTES = 8 * 1024 * 1024 * 1024
MAX_PULL = 80


@dataclass(frozen=True)
class ModelRec:
    tag: str
    role: str
    download: str
    license: str
    page: str
    notes: str

    def pull_command(self) -> str:
        return f"ollama pull {self.tag}"


# gemma3:270m and gemma3:1b are text-only on the library page. Do not recommend them.
LIGHT = ModelRec(
    tag="qwen3.5:2b",
    role="light",
    download="about 2.7 to 3.1 GB",
    license="Apache License 2.0",
    page="https://ollama.com/library/qwen3.5:2b",
    notes="Vision-capable Qwen 3.5 tag. Smaller download than the default.",
)
DEFAULT = ModelRec(
    tag="gemma3:4b",
    role="default",
    download="about 3.4 GB",
    license="Gemma Terms of Use",
    page="https://ollama.com/library/gemma3:4b",
    notes="Official multimodal Gemma 3 size. gemma3:latest is this 4B vision tag.",
)
LARGER = ModelRec(
    tag="gemma4:e4b",
    role="larger",
    download="about 6.6 to 9.5 GB",
    license="Gemma Terms of Use",
    page="https://ollama.com/library/gemma4",
    notes="Current Ollama vision quick-start family. Larger download than gemma3:4b.",
)

RECS = (LIGHT, DEFAULT, LARGER)
BY_TAG = {item.tag: item for item in RECS}

VISION_DOCS = "https://docs.ollama.com/capabilities/vision"
GENERATE_DOCS = "https://docs.ollama.com/api/generate"
TAGS_DOCS = "https://docs.ollama.com/api/tags"
SHOW_DOCS = "https://docs.ollama.com/api-reference/show-model-details"
DOWNLOAD_PAGE = "https://ollama.com/download"
HARDWARE_DOCS = "https://docs.ollama.com/gpu"
SERVE_COMMAND = "ollama serve"


def recs_for_ram(total_bytes: int | None) -> tuple[ModelRec, ...]:
    """Conservative picks from total system RAM, not free RAM or VRAM.

    Below 8 GiB, only the light tag is highlighted. Otherwise default plus light.
    The larger Gemma 4 tag is listed in help but never auto-chosen from RAM.
    """
    if total_bytes is not None and total_bytes < LIGHT_RAM_BYTES:
        return (LIGHT,)
    return (DEFAULT, LIGHT)


def suggested_rec(total_bytes: int | None, *, tier: str | None = None) -> ModelRec:
    if tier == "light":
        return LIGHT
    if tier == "standard":
        return DEFAULT
    if total_bytes is not None and total_bytes < LIGHT_RAM_BYTES:
        return LIGHT
    return DEFAULT


def pull_command(tag: str) -> str:
    clean = tag.strip()[:MAX_PULL]
    if not clean or any(ch.isspace() or ch in ";|&`$<>" for ch in clean):
        return DEFAULT.pull_command()
    return f"ollama pull {clean}"


def setup_lines(
    *,
    host: str,
    local_host: bool,
    total_bytes: int | None,
    installed: list[str] | None,
    discover_error: str,
) -> list[str]:
    lines = [
        "Ollama is optional. Type the back by hand any time. rn does not install "
        "Ollama, pull a model, or start the server.",
        "j/k move chips, related files, and the model list. q or Esc goes back and "
        "keeps the staged name. Esc first leaves a text field. r reads original, R related.",
        "A written calendar day in the filename uses YYYY-MM-DD-HHMMSS. Date-only "
        "days use 12:00. That clock is assumed, not read from the photo.",
        f"Install: {DOWNLOAD_PAGE} then {SERVE_COMMAND}.",
        f"Light: {LIGHT.pull_command()}  ({LIGHT.download}, {LIGHT.license}).",
        f"Default: {DEFAULT.pull_command()}  ({DEFAULT.download}, {DEFAULT.license}).",
        f"Larger official vision example: {LARGER.pull_command()}  ({LARGER.download}).",
        "Those sizes are downloads. Runtime memory is higher and is not GPU VRAM.",
    ]
    if total_bytes is None:
        lines.append(
            "Total RAM could not be read. Pick Light or Default. This is not a VRAM guess."
        )
    else:
        gib = total_bytes / (1024**3)
        pick = suggested_rec(total_bytes)
        lines.append(
            f"Detected total system RAM about {gib:.0f} GiB (not free RAM, not VRAM). "
            f"Conservative pick: {pick.tag}."
        )
    if not local_host:
        lines.append(
            f"The configured host {host} is not this computer. A Read sends the back "
            "image to that host."
        )
    if discover_error:
        lines.append(discover_error)
    elif installed is None:
        if local_host:
            lines.append("Checking local Ollama for installed vision models.")
    elif installed:
        lines.append("Installed vision models: " + ", ".join(installed[:12]))
    elif local_host:
        lines.append(
            "Local Ollama listed no vision-capable models. Pull one, then press Installed."
        )
    return lines


def total_ram_bytes() -> int | None:
    """Total installed RAM. None when the reading is missing or untrusted."""
    if sys.platform == "darwin":
        return _darwin_ram()
    if sys.platform == "win32":
        return _windows_ram()
    return _sysconf_ram()


def _sysconf_ram() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return _linux_meminfo()
    if not isinstance(pages, int) or not isinstance(size, int):
        return None
    if pages <= 0 or size <= 0:
        return None
    return pages * size


def _linux_meminfo() -> int | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def _darwin_ram() -> int | None:
    try:
        raw = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            timeout=1,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return _sysconf_ram()
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _windows_ram() -> int | None:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        value = int(status.ullTotalPhys)
    except (OSError, AttributeError, ValueError):
        return None
    return value if value > 0 else None
