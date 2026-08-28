#!/usr/bin/env python3
"""Always-on "Hey Nexa" wake-word listener for Nexa, built on openWakeWord.

Runs a lightweight GStreamer mic pipeline continuously, feeding 80ms
(1280-sample) chunks of 16kHz mono audio into an openWakeWord ONNX model.
On detection, fires on_wake() so the caller can kick off a normal
push-to-talk recording (VoiceInputEngine) for the actual command.

Pinned to openwakeword==0.4.0 deliberately: 0.5.0+ made tflite-runtime a
hard Linux dependency, and tflite-runtime hasn't published a wheel newer
than cp39 -- it's unresolvable on any Python this Flatpak's runtime is
likely to ship for the foreseeable future. 0.4.0 predates that dependency
entirely and is onnxruntime-only, so there's no backend to select in the
first place -- no `inference_framework` kwarg exists on this version's
Model/AudioFeatures classes.
"""
import os
import threading
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's native frame size
DEFAULT_THRESHOLD = 0.5
COOLDOWN_SECONDS = 3.0  # ignore repeat triggers right after a detection

# How much recent audio to keep around at all times, so that on a trigger
# there's an actual usable clip of "Hey Nexa" to hand off for (opt-in)
# training-data collection -- the model itself only ever sees 80ms chunks,
# which is far too short to be a useful sample on its own.
HISTORY_SECONDS = 2.0

# Sensitivity presets for the Preferences dropdown. Score threshold a chunk
# must clear to count as "Hey Nexa" -- lower = triggers more easily (more
# false positives from background noise/TV), higher = must be said more
# clearly/closer to the mic (fewer false positives, but easier to miss).
SENSITIVITY_THRESHOLDS = {
    "low": 0.7,
    "medium": DEFAULT_THRESHOLD,
    "high": 0.3,
}

MEL_MODEL = "melspectrogram.onnx"
EMBEDDING_MODEL = "embedding_model.onnx"
WAKEWORD_MODEL = "hey_nexa.onnx"
REQUIRED_FILES = [MEL_MODEL, EMBEDDING_MODEL, WAKEWORD_MODEL]

# Where the bundled ONNX models live: inside the Flatpak install, or in
# ../data/wakeword-models when running straight from the source tree.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR_CANDIDATES = [
    os.path.join(_HERE, "wakeword-models"),
    os.path.join(_HERE, "..", "data", "wakeword-models"),
]


def _first_model_dir():
    for d in _MODEL_DIR_CANDIDATES:
        if d and all(os.path.exists(os.path.join(d, f)) for f in REQUIRED_FILES):
            return d
    return None


class WakeWordEngine:
    """Continuous "Hey Nexa" listener.

    Call start()/stop() to control it. on_wake() fires off the GStreamer
    thread whenever the phrase is heard -- callers must marshal back to the
    GTK main thread themselves (e.g. via GLib.idle_add), same convention as
    VoiceInputEngine.
    """

    def __init__(self, on_wake=None, on_error=None, on_wake_audio=None, on_wake_score=None):
        self.on_wake = on_wake or (lambda: None)
        self.on_error = on_error or (lambda msg: None)
        self.on_wake_audio = on_wake_audio or (lambda pcm: None)
        self.on_wake_score = on_wake_score or (lambda score: None)

        self._model_dir = _first_model_dir()
        self._model = None
        self._gst_ready = False
        self._pipeline = None
        self._listening = False
        self._buffer = bytearray()
        self._history = bytearray()
        self._last_trigger = 0.0
        self._base_threshold = DEFAULT_THRESHOLD
        self._threshold = DEFAULT_THRESHOLD
        self._lock = threading.Lock()

    def set_threshold(self, sensitivity_key):
        """Applies a sensitivity preset ("low"/"medium"/"high"). Takes effect
        immediately, even mid-listen -- no restart needed."""
        self._base_threshold = SENSITIVITY_THRESHOLDS.get(sensitivity_key, DEFAULT_THRESHOLD)
        self._threshold = self._base_threshold

    def apply_adaptive_offset(self, offset):
        """Nudges the effective threshold by a small delta on top of
        whichever preset is active (see AdaptiveLearning.get_threshold_offset),
        clamped to a safe band so it can't drift into unusable territory."""
        self._threshold = max(0.2, min(0.85, self._base_threshold + offset))

    def is_available(self):
        return self._model_dir is not None

    def is_listening(self):
        return self._listening

    def _load_model(self):
        if self._model is not None:
            return True
        try:
            import openwakeword
            self._model = openwakeword.Model(
                wakeword_model_paths=[os.path.join(self._model_dir, WAKEWORD_MODEL)],
                melspec_onnx_model_path=os.path.join(self._model_dir, MEL_MODEL),
                embedding_onnx_model_path=os.path.join(self._model_dir, EMBEDDING_MODEL),
            )
            return True
        except Exception as e:
            self.on_error(f"Couldn't load the wake word model: {e}")
            return False

    def start(self):
        with self._lock:
            if self._listening:
                return
            if not self.is_available():
                self.on_error("Wake word models aren't installed yet.")
                return
            if not self._load_model():
                return
            if not self._gst_ready:
                try:
                    Gst.init(None)
                    self._gst_ready = True
                except Exception as e:
                    self.on_error(f"Couldn't start audio: {e}")
                    return

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
                "appsink name=wwsink emit-signals=true sync=false"
            )
            try:
                pipeline = Gst.parse_launch(pipeline_desc)
            except Exception as e:
                self.on_error(f"Couldn't set up the microphone: {e}")
                return

            appsink = pipeline.get_by_name("wwsink")
            appsink.connect("new-sample", self._on_new_sample)

            self._pipeline = pipeline
            self._buffer = bytearray()
            self._history = bytearray()
            self._listening = True
            pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        with self._lock:
            if not self._listening:
                return
            self._listening = False
            pipeline = self._pipeline
            self._pipeline = None

        if pipeline:
            pipeline.set_state(Gst.State.NULL)

    def _on_new_sample(self, appsink):
        if not self._listening:
            return Gst.FlowReturn.OK
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if success:
            try:
                self._buffer.extend(mapinfo.data)
                self._history.extend(mapinfo.data)
                history_bytes = int(HISTORY_SECONDS * SAMPLE_RATE * 2)
                if len(self._history) > history_bytes:
                    del self._history[:len(self._history) - history_bytes]
                chunk_bytes = CHUNK_SAMPLES * 2  # 16-bit samples
                while len(self._buffer) >= chunk_bytes:
                    chunk = bytes(self._buffer[:chunk_bytes])
                    del self._buffer[:chunk_bytes]
                    self._process_chunk(chunk)
            finally:
                buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def _process_chunk(self, chunk_bytes):
        if not self._listening or self._model is None:
            return
        try:
            import numpy as np
            samples = np.frombuffer(chunk_bytes, dtype=np.int16)
            predictions = self._model.predict(samples)
        except Exception:
            return

        now = time.monotonic()
        if (now - self._last_trigger) <= COOLDOWN_SECONDS:
            return
        for _name, score in predictions.items():
            if score > self._threshold:
                self._last_trigger = now
                self.on_wake_score(float(score))
                self.on_wake_audio(bytes(self._history))
                self.on_wake()
                break
