"""Distro packages for optional image support.

The app does not ship these. A button can run the command only when the
system package manager already packages the plugin.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class InstallOffer:
    """One package-manager command for a missing optional plugin."""

    extra: str
    title: str
    manager: str
    argv: tuple[str, ...]

    @property
    def display(self) -> str:
        return " ".join(self.argv)


# Package names are the ones published by that distro, not pip extras.
_PACKAGES: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "arch": (
        (
            "heif",
            "HEIC",
            "pacman",
            ("pacman", "-S", "--needed", "--noconfirm", "python-pillow-heif"),
        ),
        (
            "jxl",
            "JPEG XL",
            "pacman",
            ("pacman", "-S", "--needed", "--noconfirm", "python-pillow-jpegxl-plugin"),
        ),
    ),
    "alpine": (("heif", "HEIC", "apk", ("apk", "add", "py3-pillow-heif")),),
    "fedora": (("jxl", "JPEG XL", "dnf", ("dnf", "install", "-y", "python-pillow-jxl-plugin")),),
    "opensuse": (
        ("heif", "HEIC", "zypper", ("zypper", "install", "-y", "python-pillow-heif")),
        ("jxl", "JPEG XL", "zypper", ("zypper", "install", "-y", "python-pillow-jxl-plugin")),
    ),
    "void": (
        ("heif", "HEIC", "xbps", ("xbps-install", "-y", "python3-pillow_heif")),
        ("jxl", "JPEG XL", "xbps", ("xbps-install", "-y", "python3-pillow-jxl")),
    ),
}

_FAMILIES = (
    ("opensuse", "opensuse"),
    ("alpine", "alpine"),
    ("fedora", "fedora"),
    ("arch", "arch"),
    ("suse", "opensuse"),
    ("void", "void"),
)


def parse_os_release(text: str) -> tuple[str, tuple[str, ...]]:
    """Return `/etc/os-release` ID and ID_LIKE tokens."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    like = tuple(part for part in values.get("ID_LIKE", "").split() if part)
    return values.get("ID", ""), like


def family_from(distro: str, like: tuple[str, ...] = ()) -> str | None:
    """Map an os-release id to a family we know how to install for."""
    for token in (distro, *like):
        for prefix, family in _FAMILIES:
            if token == prefix or token.startswith(prefix):
                return family
    return None


def distro_family(
    platform: str | None = None,
    os_release: str | None = None,
) -> str | None:
    """Family for this machine. macOS, Windows, and unknown Linux have none."""
    system = sys.platform if platform is None else platform
    if system in {"darwin", "win32"}:
        return None
    if os_release is None:
        try:
            os_release = open("/etc/os-release", encoding="utf-8").read()
        except OSError:
            return None
    distro, like = parse_os_release(os_release)
    return family_from(distro, like)


def _privileged(argv: tuple[str, ...], *, root: bool, sudo: str | None) -> tuple[str, ...]:
    if root or not sudo or argv[0] in {"sudo", "doas"}:
        return argv
    return (sudo, *argv)


def install_offers(
    family: str | None,
    *,
    root: bool | None = None,
    sudo: str | None = None,
    frozen: bool | None = None,
) -> tuple[InstallOffer, ...]:
    """Commands for optional plugins this distro packages.

    A frozen build cannot load a distro Python plugin, so it gets no command.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen or family is None:
        return ()
    rows = _PACKAGES.get(family)
    if not rows:
        return ()
    if root is None:
        root = hasattr(os, "geteuid") and os.geteuid() == 0
    if sudo is None and shutil.which("sudo"):
        sudo = "sudo"
    return tuple(
        InstallOffer(extra, title, manager, _privileged(argv, root=root, sudo=sudo))
        for extra, title, manager, argv in rows
    )


def offer_for(extra: str, family: str | None = None) -> InstallOffer | None:
    """The command for one extra on this machine, if the distro ships it."""
    if family is None:
        family = distro_family()
    for offer in install_offers(family):
        if offer.extra == extra:
            return offer
    return None
