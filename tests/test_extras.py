"""Distro package commands for optional HEIC and JPEG XL support."""

from tui_renamer.extras import family_from, install_offers, parse_os_release


def test_parse_os_release_quotes_and_likes() -> None:
    distro, like = parse_os_release('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n')
    assert distro == "opensuse-tumbleweed"
    assert like == ("opensuse", "suse")


def test_family_uses_id_like_for_manjaro() -> None:
    assert family_from("manjaro", ("arch",)) == "arch"
    assert family_from("ubuntu", ("debian",)) is None
    assert family_from("fedora", ()) == "fedora"


def test_arch_offers_both_plugins_with_sudo() -> None:
    offers = install_offers("arch", root=False, sudo="sudo", frozen=False)
    by_extra = {item.extra: item for item in offers}
    assert by_extra["heif"].display.startswith("sudo pacman -S")
    assert "python-pillow-heif" in by_extra["heif"].display
    assert "python-pillow-jpegxl-plugin" in by_extra["jxl"].display


def test_root_skips_sudo_and_frozen_offers_nothing() -> None:
    offers = install_offers("alpine", root=True, sudo="sudo", frozen=False)
    assert offers[0].argv[0] == "apk"
    assert install_offers("arch", frozen=True) == ()
    assert install_offers(None) == ()
    assert install_offers("debian") == ()
