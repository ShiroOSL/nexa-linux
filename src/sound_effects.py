"""Short UI sound-effect playback for Nexa, built on GStreamer playbin.

Fire-and-forget: each play_*() call spins up its own tiny playbin, so
overlapping sounds don't fight each other or need a shared pipeline like
the continuous wake-word listener does.
"""
import os
import random

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

# Where the bundled sound files live: inside the Flatpak install, or in
# ../data/sounds when running straight from the source tree.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SOUND_DIR_CANDIDATES = [
    os.path.join(_HERE, "sounds"),
    os.path.join(_HERE, "..", "data", "sounds"),
]

# "listening" has three variants, weighted so listening1 plays most often.
_LISTENING_FILES = ["listening1.mp3", "listening2.mp3", "listening3.mp3"]
_LISTENING_WEIGHTS = [3, 1, 1]

_SOUND_FILES = {
    "working": "working.mp3",
    "success_long": "success_long.mp3",
    "error": "error.mp3",
}

def _first_sound_dir():
    for d in _SOUND_DIR_CANDIDATES:
        if d and os.path.isdir(d):
            return d
    return None


class SoundEffects:
    def __init__(self):
        self._sound_dir = _first_sound_dir()
        self._gst_ready = False
        self._players = []  # keep references alive until each finishes

    def _ensure_gst(self):
        if not self._gst_ready:
            try:
                Gst.init(None)
                self._gst_ready = True
            except Exception:
                pass
        return self._gst_ready

    def _play_file(self, filename):
        if not self._sound_dir or not self._ensure_gst():
            return
        path = os.path.join(self._sound_dir, filename)
        if not os.path.exists(path):
            return

        player = Gst.ElementFactory.make("playbin", None)
        if not player:
            return
        player.set_property("uri", "file://" + path)
        self._players.append(player)

        bus = player.get_bus()
        bus.add_signal_watch()

        def on_message(_bus, message):
            if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
                player.set_state(Gst.State.NULL)
                if player in self._players:
                    self._players.remove(player)

        bus.connect("message", on_message)
        player.set_state(Gst.State.PLAYING)

    def play_listening(self):
        """Plays when Nexa starts listening (wake word detected or mic button pressed)."""
        self._play_file(random.choices(_LISTENING_FILES, weights=_LISTENING_WEIGHTS)[0])

    def play_working(self):
        """Plays when Nexa starts thinking about a response."""
        self._play_file(_SOUND_FILES["working"])

    def play_success_long(self):
        """Plays when Nexa finishes a response that took a while to think about."""
        self._play_file(_SOUND_FILES["success_long"])

    def play_error(self):
        """Plays when something goes wrong (mic/voice/wake-word failure, etc)."""
        self._play_file(_SOUND_FILES["error"])
