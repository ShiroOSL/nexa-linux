import os
import re
import shutil
import subprocess
import tempfile
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

MODEL_NAME = "en_US-lessac-medium.onnx"  # kept for backward compatibility

VOICE_MODELS = {
    "female": "en_US-amy-medium.onnx",
    "male": "en_US-ryan-medium.onnx",
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols, pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"  # flags
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "\U0000FE0F"             # variation selector
    "]+",
    flags=re.UNICODE,
)

# Tweak this to change what each voice says in the selector popup.
VOICE_PREVIEW_TEXT = "Hi, I'm Nexa. Choose the voice you'd like me to use."

# Cached preview clips live in the same config dir as everything else, one
# WAV per voice, keyed by voice_key so they survive across restarts and are
# only generated once (on first popup open, or if the file's gone missing) --
# not re-synthesized every time the voice-selector popup is opened.
_VOICE_PREVIEW_CACHE_DIR = os.path.expanduser("~/.config/nexa/voice-previews")
DEFAULT_VOICE = "female"

# Where the bundled Piper binary can live: inside the Flatpak (/app/lib/piper)
# or, if missing, whatever "piper" resolves to on PATH (e.g. a dev-machine install).
_PIPER_CANDIDATES = [
    "/app/lib/piper/piper",
    shutil.which("piper"),
]

# Where the voice models live: bundled next to this file inside the Flatpak
# install, or in ../data/voices when running straight from the source tree.
_HERE = os.path.dirname(os.path.abspath(__file__))


def _model_candidates(model_name):
    return [
        os.path.join(_HERE, "voices", model_name),
        os.path.join(_HERE, "..", "data", "voices", model_name),
    ]


def _first_existing(paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


class VoiceManager:
    """Text-to-speech engine built on a bundled Piper neural voice model,
    played back through GStreamer. Falls back to the host's espeak-ng if
    Piper isn't available for some reason. Can be muted from Preferences."""

    def __init__(self, on_speech_start=None, on_speech_end=None):
        self.enabled = True
        self.piper_bin = _first_existing(_PIPER_CANDIDATES)
        self.voice_key = DEFAULT_VOICE
        self.model_path = _first_existing(_model_candidates(VOICE_MODELS[self.voice_key]))
        self._gst_ready = False
        self._player = None
        self._player_finished_event = None
        self._lock = threading.Lock()
        # Fired around actual playback, not just around speak() being called
        # (which returns immediately) -- lets callers pause anything that
        # listens to the mic (wake word) while Nexa's own voice is playing,
        # so she doesn't hear and respond to herself in a feedback loop.
        self.on_speech_start = on_speech_start or (lambda: None)
        self.on_speech_end = on_speech_end or (lambda: None)

    def preview_voice(self, voice_key, on_start=None, on_end=None):
        """Plays the cached intro clip for a specific voice, without
        changing the currently active voice. Used by the voice-selector
        popup so tapping a row previews that voice regardless of which
        one is actually selected. The clip is generated once and cached
        to disk (~/.config/nexa/voice-previews/<voice_key>.wav) -- if
        it's already there from a previous run, this just plays it
        straight away instead of re-running Piper. on_start/on_end are
        local to this one call (not the shared self.on_speech_start/end),
        so previewing a voice never pauses wake-word listening the way a
        real reply does. Returns False immediately (no thread started)
        if that voice's model isn't installed on disk.
        """
        model_name = VOICE_MODELS.get(voice_key)
        model_path = _first_existing(_model_candidates(model_name)) if model_name else None
        if not model_path or not self.piper_bin:
            return False
        threading.Thread(
            target=self._preview_worker,
            args=(voice_key, model_path, on_start, on_end),
            daemon=True,
        ).start()
        return True

    def ensure_voice_previews_cached(self):
        """Pre-generates any missing preview clips for installed voices, so
        the very first time the voice-selector popup is opened there's no
        synthesis delay before a row can be tapped. Safe to call on every
        startup -- it's a no-op once the cache files already exist. Runs
        synchronously; call it from a background thread at app launch."""
        os.makedirs(_VOICE_PREVIEW_CACHE_DIR, exist_ok=True)
        for voice_key, model_name in VOICE_MODELS.items():
            cache_path = self._preview_cache_path(voice_key)
            if os.path.exists(cache_path):
                continue
            model_path = _first_existing(_model_candidates(model_name))
            if not model_path or not self.piper_bin:
                continue
            self._render_preview_to_cache(model_path, cache_path)

    def _preview_cache_path(self, voice_key):
        return os.path.join(_VOICE_PREVIEW_CACHE_DIR, f"{voice_key}.wav")

    def _render_preview_to_cache(self, model_path, cache_path):
        """Synthesizes straight into the final cache path (via a temp file
        + atomic rename) rather than through the normal tempfile-then-play
        flow, since this clip is meant to be kept, not deleted after one
        playback like _play_wav does."""
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="nexa-preview-", dir=_VOICE_PREVIEW_CACHE_DIR)
        os.close(fd)
        try:
            subprocess.run(
                [self.piper_bin, "--model", model_path, "--output_file", tmp_path],
                input=VOICE_PREVIEW_TEXT.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=True,
            )
            os.replace(tmp_path, cache_path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _preview_worker(self, voice_key, model_path, on_start, on_end):
        if on_start:
            on_start()
        try:
            cache_path = self._preview_cache_path(voice_key)
            if not os.path.exists(cache_path):
                os.makedirs(_VOICE_PREVIEW_CACHE_DIR, exist_ok=True)
                self._render_preview_to_cache(model_path, cache_path)
            if os.path.exists(cache_path):
                # _play_wav deletes the file it's given when done (it's
                # designed for throwaway TTS clips) -- pass a throwaway
                # copy so the cached original survives for next time.
                fd, tmp_copy = tempfile.mkstemp(suffix=".wav", prefix="nexa-preview-play-")
                os.close(fd)
                shutil.copyfile(cache_path, tmp_copy)
                self._play_wav(tmp_copy)
        finally:
            if on_end:
                on_end()

    def set_voice(self, voice_key):
        """Switches the active Piper voice model. voice_key: 'female' | 'male'.
        Silently keeps the current voice if the requested model isn't bundled
        on disk (e.g. the male voice hasn't been downloaded yet)."""
        model_name = VOICE_MODELS.get(voice_key)
        if not model_name:
            return False
        path = _first_existing(_model_candidates(model_name))
        if not path:
            return False
        self.voice_key = voice_key
        self.model_path = path
        return True

    def load_model(self):
        """Initializes the GStreamer playback backend. Returns True if a
        speech path (Piper or the espeak-ng fallback) is available."""
        if not self._gst_ready:
            try:
                Gst.init(None)
                self._gst_ready = True
            except Exception:
                self._gst_ready = False
        return True

    def set_enabled(self, enabled):
        """Toggles Nexa's voice on/off, e.g. from the Preferences switch."""
        self.enabled = enabled
        if not enabled:
            self.stop_audio()

    def speak(self, text):
        """Speaks the text asynchronously, unless voice has been disabled."""
        if not text or not self.enabled:
            return

        clean_text = text.replace('"', '').replace('\n', ' ').strip()
        clean_text = _EMOJI_RE.sub('', clean_text).strip()
        clean_text = re.sub(r'\s{2,}', ' ', clean_text)
        if not clean_text:
            return
        threading.Thread(target=self._speak_worker, args=(clean_text,), daemon=True).start()

    def _speak_worker(self, text):
        self.on_speech_start()
        try:
            if self.piper_bin and self.model_path:
                wav_path = self._synthesize_with_piper(text)
                if wav_path:
                    self._play_wav(wav_path)
                    return
            # Piper missing or synthesis failed: fall back to the host voice.
            self._run_native_tts(text)
        finally:
            self.on_speech_end()

    def _synthesize_with_piper(self, text, model_path=None):
        """Runs the bundled Piper binary, rendering text to a temp WAV file.
        model_path lets callers (e.g. voice preview) synthesize with a
        specific voice model without disturbing the active self.model_path."""
        model_path = model_path or self.model_path
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="nexa-tts-")
        os.close(fd)
        try:
            subprocess.run(
                [self.piper_bin, "--model", model_path, "--output_file", wav_path],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=True,
            )
            return wav_path
        except Exception:
            try:
                os.remove(wav_path)
            except OSError:
                pass
            return None

    def _play_wav(self, wav_path):
        """Plays a WAV file through a GStreamer playbin, then cleans it up.
        Uses an Event rather than a blocking bus wait, so stop_audio() can
        wake this up immediately (for barge-in) instead of leaving it stuck
        for up to 30s with no EOS/ERROR message to notice the interruption."""
        if not self._gst_ready:
            self.load_model()

        self.stop_audio()

        player = Gst.ElementFactory.make("playbin", "nexa-player")
        if not player:
            try:
                os.remove(wav_path)
            except OSError:
                pass
            return

        player.set_property("uri", "file://" + wav_path)
        finished = threading.Event()

        with self._lock:
            self._player = player
            self._player_finished_event = finished

        bus = player.get_bus()
        bus.add_signal_watch()

        def on_message(_bus, message):
            if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
                finished.set()

        bus.connect("message", on_message)
        player.set_state(Gst.State.PLAYING)

        finished.wait(timeout=30)

        player.set_state(Gst.State.NULL)
        with self._lock:
            if self._player is player:
                self._player = None
                self._player_finished_event = None
        try:
            os.remove(wav_path)
        except OSError:
            pass

    def _run_native_tts(self, text):
        """Fallback voice: runs espeak-ng on the host via flatpak-spawn."""
        try:
            subprocess.run(
                ["flatpak-spawn", "--host", "espeak-ng", "-v", "en-us", "-s", "165", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def stop_audio(self):
        """Stops any in-flight speech playback immediately, and wakes up
        _play_wav's wait so on_speech_end() fires promptly (needed for
        barge-in: interrupting Nexa should immediately free up the mic
        instead of leaving her "speaking" state stuck for up to 30s)."""
        with self._lock:
            player, self._player = self._player, None
            finished, self._player_finished_event = self._player_finished_event, None
        if player is not None:
            try:
                player.set_state(Gst.State.NULL)
            except Exception:
                pass
        if finished is not None:
            finished.set()
        try:
            subprocess.run(
                ["flatpak-spawn", "--host", "pkill", "espeak-ng"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
