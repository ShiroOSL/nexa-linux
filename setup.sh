#!/usr/bin/env bash
# Nexa Assistant — setup / install / update script
# Downloads private assets, installs Flatpak deps, builds & installs the app.
set -e

REPO_URL="https://github.com/ShiroOSL/nexa-linux.git"
APP_ID="org.nexa.Assistant"
WORKDIR="$HOME/.local/share/nexa-setup"
PRIVATE_RELEASE_URL="https://github.com/ShiroOSL/nexa-private-assets/releases/latest/download/nexa-private.tar.gz"
# ^ TODO: replace with real private-assets release URL once hosting is set up

echo "== Nexa Assistant setup =="

# 1. Install flatpak + flathub remote if missing
if ! command -v flatpak >/dev/null 2>&1; then
    echo "Installing flatpak..."
    if command -v apt >/dev/null 2>&1; then sudo apt install -y flatpak; fi
    if command -v dnf >/dev/null 2>&1; then sudo dnf install -y flatpak; fi
    if command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm flatpak; fi
fi

flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# 2. Install GNOME SDK/Platform runtime
echo "Installing GNOME SDK/Platform..."
flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50 || true

# 3. Clone or update the public repo
if [ -d "$WORKDIR/.git" ]; then
    echo "Updating existing checkout..."
    git -C "$WORKDIR" pull --ff-only
else
    echo "Cloning nexa-linux..."
    rm -rf "$WORKDIR"
    git clone "$REPO_URL" "$WORKDIR"
fi

cd "$WORKDIR"

# 4. Download and unpack private assets (models + private .py files)
echo "Downloading private assets..."
curl -L "$PRIVATE_RELEASE_URL" -o /tmp/nexa-private.tar.gz
tar -xzf /tmp/nexa-private.tar.gz -C "$WORKDIR"
rm -f /tmp/nexa-private.tar.gz
# Expected archive layout (extracts directly into place):
#   data/voices/*.onnx(.json)
#   data/whisper-models/ggml-tiny.en.bin
#   data/wakeword-models/*.onnx
#   src/commands.py
#   src/nexa_studio_commands.py
#   src/training_data.py

# 5. Build & install via flatpak-builder
if ! command -v flatpak-builder >/dev/null 2>&1; then
    echo "Installing flatpak-builder..."
    flatpak install --user -y flathub org.flatpak.Builder || \
    { command -v apt >/dev/null 2>&1 && sudo apt install -y flatpak-builder; }
fi

echo "Building Nexa Assistant..."
flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
    build-dir "$APP_ID.json"

echo "== Done. Launch with: flatpak run $APP_ID =="
