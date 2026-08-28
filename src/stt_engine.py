#!/usr/bin/env python3
"""Push-to-talk voice input for Nexa.

Records the microphone through GStreamer, uses a simple energy-based
silence detector to know when the user has finished speaking, then
transcribes the clip with a bundled whisper.cpp binary + ggml model.
"""
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from native_whisper import NativeWhisper

MODEL_NAME = "ggml-tiny.en.bin"
SAMPLE_RATE = 16000

# Whisper sometimes hallucinates bracketed non-speech captions from its
# training data on short/ambiguous audio, e.g. "(screaming)",
# "[BLANK_AUDIO]", "(music)". Applied to every transcription path here
# (native engine AND the whisper-cli fallback) so neither can leak these
# into what gets shown, sent, or spoken.
_HALLUCINATION_TAG_RE = re.compile(r"[\(\[][^)\]]*[\)\]]")

# How long the user must stay silent before Nexa considers them "finished
# talking". Selectable from Preferences -> Voice Input -> Listening Time.
SILENCE_TIMEOUTS = {
    "default": 1.0,
    "longer": 1.8,
    "longest": 3.0,
}
MAX_RECORDING_SECONDS = 30
ENERGY_THRESHOLD = 500.0

# A single loud audio chunk (e.g. a brief pop/click when the mic stream
# first opens) shouldn't count as "the user is talking" -- require energy
# to stay above threshold for this many consecutive chunks first, to
# filter out that kind of transient noise without meaningfully delaying
# real speech detection.
MIN_VOICE_CHUNK_STREAK = 3

# "Live" partial transcription: re-transcribe a rolling window of recent
# audio every PARTIAL_INTERVAL_SECONDS, so the entry box updates while the
# user is still talking. The final, authoritative transcript (from the full
# recording) still arrives via on_result once silence is detected, same as
# before -- these partials are just a live preview, not what gets sent.
PARTIAL_INTERVAL_SECONDS = 0.7
PARTIAL_WINDOW_SECONDS = 10

# Once a live partial transcript already matches one of Nexa's known
# commands, there's no need to wait through the full configured silence
# timeout to know the user is finished -- just a brief confirmation pause
# to avoid cutting someone off mid-sentence if they keep talking.
COMMAND_MATCH_SILENCE_SECONDS = 0.35

# As soon as this short a pause begins, take one immediate read on the
# complete utterance so far. Fixed-interval partial checks (every
# PARTIAL_INTERVAL_SECONDS) often land mid-word for short commands, since
# they aren't aligned with when the user actually stops talking -- this
# targets that moment directly instead of hoping a periodic check lines up.
EARLY_CHECK_SILENCE_SECONDS = 0.35

# Where the bundled whisper-cli binary can live: inside the Flatpak
# (/app/bin) or, if missing, whatever "whisper-cli" resolves to on PATH.
_WHISPER_CANDIDATES = [
    "/app/bin/whisper-cli",
    shutil.which("whisper-cli"),
]

# Where the ggml model lives: bundled next to this file inside the Flatpak
# install, or in ../data/whisper-models when running from the source tree.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_CANDIDATES = [
    os.path.join(_HERE, "whisper-models", MODEL_NAME),
    os.path.join(_HERE, "..", "data", "whisper-models", MODEL_NAME),
]


def _first_existing(paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


class VoiceInputEngine:
    """Push-to-talk mic capture + local Whisper transcription.

    Usage: call toggle() from a mic button. State changes and the final
    transcript are reported through the on_state_change/on_result/on_error
    callbacks, which fire off the GStreamer/worker thread -- callers must
    marshal back to the GTK main thread themselves (e.g. via GLib.idle_add).
    """

    def __init__(self, on_state_change=None, on_result=None, on_error=None, on_partial_result=None, on_stt_sample=None):
        self.whisper_bin = _first_existing(_WHISPER_CANDIDATES)
        self.model_path = _first_existing(_MODEL_CANDIDATES)
        self.on_state_change = on_state_change or (lambda state: None)
        self.on_result = on_result or (lambda text: None)
        self.on_error = on_error or (lambda msg: None)
        self.on_partial_result = on_partial_result or (lambda text: None)
        self.on_stt_sample = on_stt_sample or (lambda pcm, text: None)

        # Fast path: persistent in-process model (no per-call subprocess
        # spawn / model reload). Falls back to whisper-cli automatically
        # in _transcribe() if this couldn't load for some reason.
        self._native = NativeWhisper(self.model_path)
        self._vocabulary_prompt = None  # set via set_vocabulary_prompt()
        self._command_matcher = None    # set via set_command_matcher()
        self._command_matched = False
        self._early_check_done = False
        self._voice_chunk_streak = 0

        self.silence_timeout = SILENCE_TIMEOUTS["default"]
        self._gst_ready = False
        self._pipeline = None
        self._recording = False
        self._heard_voice = False
        self._wav_path = None
        self._lock = threading.Lock()
        self._pcm_buffer = bytearray()
        self._pcm_lock = threading.Lock()

    def set_timeout_mode(self, mode):
        """mode: 'default' | 'longer' | 'longest'"""
        self.silence_timeout = SILENCE_TIMEOUTS.get(mode, SILENCE_TIMEOUTS["default"])

    def set_vocabulary_prompt(self, prompt):
        """Biases Whisper toward recognizing Nexa's name and command
        vocabulary (see CommandEngine.get_vocabulary_prompt)."""
        self._vocabulary_prompt = prompt

    def set_command_matcher(self, matcher_fn):
        """matcher_fn(text) -> bool, side-effect-free (see
        CommandEngine.matches_known_command). Lets Nexa cut the silence
        wait short once a live partial already looks like a complete
        command, instead of waiting through the full configured pause."""
        self._command_matcher = matcher_fn

    def is_available(self):
        return bool(self.whisper_bin and self.model_path)

    def is_recording(self):
        return self._recording

    def toggle(self):
        if self._recording:
            self.stop_recording(user_cancelled=False)
        else:
            self.start_recording()

    def start_recording(self):
        if self._recording:
            return
        if not self.is_available():
            self.on_error("Voice input isn't set up yet (Whisper model missing).")
            return
        if not self._gst_ready:
            try:
                Gst.init(None)
                self._gst_ready = True
            except Exception as e:
                self.on_error(f"Couldn't start audio: {e}")
                return

        fd, self._wav_path = tempfile.mkstemp(suffix=".wav", prefix="nexa-stt-")
        os.close(fd)

        pipeline_desc = (
            # webrtcdsp (WebRTC's noise suppression/AGC/high-pass filter)
            # requires a named echo-probe element to even start, even
            # though we don't want real echo cancellation here -- this
            # silent branch exists purely to satisfy that requirement.
            "audiotestsrc volume=0 wave=silence ! audioconvert ! audioresample ! "
            f"audio/x-raw,format=S16LE,channels=1,rate={SAMPLE_RATE} ! "
            "webrtcechoprobe name=echoprobe0 ! fakesink "
            "pulsesrc ! audioconvert ! audioresample ! "
            f"audio/x-raw,format=S16LE,channels=1,rate={SAMPLE_RATE} ! "
            "webrtcdsp noise-suppression-level=high probe=echoprobe0 ! audioconvert ! "
            "tee name=t "
            "t. ! queue ! wavenc ! filesink location=" + self._wav_path + " "
            "t. ! queue ! appsink name=vadsink emit-signals=true sync=false"
        )
        try:
            pipeline = Gst.parse_launch(pipeline_desc)
        except Exception as e:
            self.on_error(f"Couldn't set up the microphone: {e}")
            return

        appsink = pipeline.get_by_name("vadsink")
        appsink.connect("new-sample", self._on_new_sample)

        self._pipeline = pipeline
        self._recording = True
        self._heard_voice = False
        self._command_matched = False
        self._early_check_done = False
        self._voice_chunk_streak = 0
        self._last_voice_time = time.monotonic()
        self._start_time = time.monotonic()
        with self._pcm_lock:
            self._pcm_buffer = bytearray()
        pipeline.set_state(Gst.State.PLAYING)

        self.on_state_change("recording")
        threading.Thread(target=self._watch_silence, daemon=True).start()
        threading.Thread(target=self._partial_transcribe_loop, daemon=True).start()

    def _on_new_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if success:
            try:
                with self._pcm_lock:
                    self._pcm_buffer.extend(mapinfo.data)
                count = len(mapinfo.data) // 2
                if count > 0:
                    samples = struct.unpack(f"<{count}h", mapinfo.data[:count * 2])
                    rms = (sum(s * s for s in samples) / count) ** 0.5
                    if rms > ENERGY_THRESHOLD:
                        self._voice_chunk_streak += 1
                        if self._voice_chunk_streak >= MIN_VOICE_CHUNK_STREAK:
                            self._last_voice_time = time.monotonic()
                            self._heard_voice = True
                            self._early_check_done = False
                    else:
                        self._voice_chunk_streak = 0
            finally:
                buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def _watch_silence(self):
        while self._recording:
            time.sleep(0.1)
            now = time.monotonic()
            if now - self._start_time > MAX_RECORDING_SECONDS:
                self.stop_recording(user_cancelled=False)
                return

            silence_elapsed = (now - self._last_voice_time) if self._heard_voice else 0

            # As soon as a brief pause begins, get one immediate read on the
            # whole utterance so far -- much more likely to catch a clean,
            # complete command than waiting for the next periodic partial
            # check, which isn't aligned with when the user actually stopped.
            if self._heard_voice and not self._early_check_done and silence_elapsed > EARLY_CHECK_SILENCE_SECONDS:
                self._early_check_done = True
                threading.Thread(target=self._early_command_check, daemon=True).start()

            threshold = COMMAND_MATCH_SILENCE_SECONDS if self._command_matched else self.silence_timeout
            if self._heard_voice and silence_elapsed > threshold:
                self.stop_recording(user_cancelled=False)
                return

    def _early_command_check(self):
        with self._pcm_lock:
            data = bytes(self._pcm_buffer)
        if len(data) < int(SAMPLE_RATE * 2 * 0.3):  # need at least ~0.3s to bother
            return
        text = self._transcribe(data, audio_ctx=768)
        if text and self._recording:
            self.on_partial_result(text)
            if self._command_matcher and self._command_matcher(text):
                self._command_matched = True

    def _partial_transcribe_loop(self):
        """Periodically re-transcribes a rolling window of recent audio so
        the entry box can show a live preview while the user is still
        talking. Purely cosmetic -- the actual message sent always comes
        from the final, full-recording transcription in _transcribe_worker."""
        window_bytes = PARTIAL_WINDOW_SECONDS * SAMPLE_RATE * 2  # 16-bit mono
        min_bytes = int(SAMPLE_RATE * 2 * 0.5)  # need at least ~0.5s to bother

        while self._recording:
            time.sleep(PARTIAL_INTERVAL_SECONDS)
            if not self._recording or not self._heard_voice:
                continue

            with self._pcm_lock:
                data = bytes(self._pcm_buffer[-window_bytes:])
            if len(data) < min_bytes:
                continue

            text = self._transcribe(data, audio_ctx=768)

            if text and self._command_matcher and self._command_matcher(text):
                self._command_matched = True

            # Don't report a partial that finished after recording already stopped
            if text and self._recording:
                self.on_partial_result(text)

    def _transcribe(self, pcm_bytes, wav_path_fallback=None, audio_ctx=0):
        """Prefers the fast native (persistent-context) path; falls back to
        spawning whisper-cli on a WAV file if native isn't available."""
        text = None
        if self._native and self._native.available:
            text = self._native.transcribe_pcm16(pcm_bytes, audio_ctx=audio_ctx, initial_prompt=self._vocabulary_prompt)

        if not text:
            if wav_path_fallback:
                text = self._run_whisper(wav_path_fallback)
            else:
                tmp_path = self._write_partial_wav(pcm_bytes)
                if tmp_path:
                    try:
                        text = self._run_whisper(tmp_path)
                    finally:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

        if not text:
            return None

        text = _HALLUCINATION_TAG_RE.sub("", text).strip()
        return text if text else None

    def _write_partial_wav(self, pcm_data):
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="nexa-stt-partial-")
            os.close(fd)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_data)
            return path
        except Exception:
            return None

    def stop_recording(self, user_cancelled=True):
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        if user_cancelled or not self._heard_voice:
            self._cleanup_wav()
            self.on_state_change("idle")
            return

        self.on_state_change("transcribing")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self):
        with self._pcm_lock:
            full_pcm = bytes(self._pcm_buffer)
        text = self._transcribe(full_pcm, wav_path_fallback=self._wav_path)
        self._cleanup_wav()
        self.on_state_change("idle")
        if text:
            self.on_stt_sample(full_pcm, text)
            self.on_result(text)
        else:
            self.on_error("Sorry, I couldn't make that out.")

    def _run_whisper(self, wav_path):
        """Runs the bundled whisper-cli binary and returns the plain-text
        transcript (-nt/-np keep stdout to just the recognized words)."""
        try:
            result = subprocess.run(
                [self.whisper_bin, "-m", self.model_path, "-f", wav_path,
                 "-l", "en", "-nt", "-np"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            text = result.stdout.decode("utf-8", errors="ignore").strip()
            return text if text else None
        except Exception:
            return None

    def _cleanup_wav(self):
        if self._wav_path:
            try:
                os.remove(self._wav_path)
            except OSError:
                pass
        self._wav_path = None
