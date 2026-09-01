#!/usr/bin/env bash
# Nexa Assistant — interactive setup/update/uninstall script
set -e

REPO_URL="https://github.com/ShiroOSL/nexa-linux.git"
APP_ID="org.nexa.Assistant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR=""

# ---------- helpers ----------

is_installed() {
    flatpak info "$APP_ID" >/dev/null 2>&1
}

# True if this script is sitting inside an existing local checkout
# of the repo (i.e. the manifest is right next to it), so we can
# build in place instead of re-cloning from GitHub.
using_local_checkout() {
    [[ -f "$SCRIPT_DIR/$APP_ID.json" ]]
}

cleanup() {
    [[ -n "$WORKDIR" ]] && rm -rf "$WORKDIR"
}
trap cleanup EXIT

install_flatpak_if_missing() {
    if ! command -v flatpak >/dev/null 2>&1; then
        echo "Installing flatpak..."
        if command -v apt >/dev/null 2>&1; then sudo apt install -y flatpak; fi
        if command -v dnf >/dev/null 2>&1; then sudo dnf install -y flatpak; fi
        if command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm flatpak; fi
    fi
    flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
}

install_builder_if_missing() {
    if ! command -v flatpak-builder >/dev/null 2>&1; then
        echo "Installing flatpak-builder..."
        if command -v apt >/dev/null 2>&1; then sudo apt install -y flatpak-builder; fi
        if command -v dnf >/dev/null 2>&1; then sudo dnf install -y flatpak-builder; fi
        if command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm flatpak-builder; fi
        if ! command -v flatpak-builder >/dev/null 2>&1; then
            echo "Could not install flatpak-builder automatically."
            echo "Please install it manually for your distro, then re-run this script."
            exit 1
        fi
    fi
}

do_build_and_install() {
    echo ""
    if using_local_checkout; then
        echo "Using local checkout at $SCRIPT_DIR"
        cd "$SCRIPT_DIR"
    else
        echo "Fetching Nexa Assistant..."
        WORKDIR="$(mktemp -d /tmp/nexa-setup.XXXXXX)"
        git clone --depth 1 "$REPO_URL" "$WORKDIR/nexa-linux"
        cd "$WORKDIR/nexa-linux"
    fi

    install_flatpak_if_missing
    echo "Installing GNOME SDK/Platform..."
    flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50 || true
    install_builder_if_missing

    echo "Building Nexa Assistant..."
    flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
        build-dir "$APP_ID.json"

    echo ""
    echo "== Done. Launch with: flatpak run $APP_ID =="
}

do_install() {
    do_build_and_install
}

do_update() {
    echo "Updating Nexa Assistant to the latest version..."
    if using_local_checkout && [[ -d "$SCRIPT_DIR/.git" ]]; then
        echo "Pulling latest changes into $SCRIPT_DIR..."
        git -C "$SCRIPT_DIR" pull --ff-only || {
            echo "Could not fast-forward local checkout (local changes?)."
            echo "Rebuilding with what's currently on disk instead."
        }
    fi
    do_build_and_install
}

do_uninstall() {
    echo ""
    flatpak uninstall --user -y "$APP_ID"
    echo ""
    read -rp "Also remove Nexa's user data/config (~/.config/nexa, ~/.var/app/$APP_ID)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm -rf "$HOME/.config/nexa" "$HOME/.var/app/$APP_ID"
        echo "User data removed."
    fi
    echo "== Nexa Assistant uninstalled. =="
}

# ---------- main menu ----------

echo "==================================================="
echo "                Nexa Assistant Setup"
echo "==================================================="
echo ""

if is_installed; then
    echo "Nexa Assistant is currently installed."
    echo ""
    echo "  1) Update Nexa Assistant"
    echo "  2) Uninstall Nexa Assistant"
    echo "  3) Exit"
    echo ""
    read -rp "Choose an option [1-3]: " choice
    case "$choice" in
        1) do_update ;;
        2) do_uninstall ;;
        *) echo "Exiting."; exit 0 ;;
    esac
else
    echo "Welcome to Nexa Assistant — a local-first, privacy-focused"
    echo "voice assistant for Linux, built with GTK4/Adwaita."
    echo ""
    echo "Nexa runs fully offline: wake-word detection, speech-to-text,"
    echo "and text-to-speech all happen on your machine, with no data"
    echo "sent to the cloud."
    echo ""
    echo "  1) Install Nexa Assistant"
    echo "  2) Exit"
    echo ""
    read -rp "Choose an option [1-2]: " choice
    case "$choice" in
        1) do_install ;;
        *) echo "Exiting."; exit 0 ;;
    esac
fi
