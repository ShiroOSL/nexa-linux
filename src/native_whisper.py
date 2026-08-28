"""ctypes bindings to the native whisper shim (nexa_whisper_shim.c).

Keeps a single whisper.cpp model loaded in memory for the lifetime of the
app, so repeated transcriptions (live partial previews, final results)
avoid the per-call subprocess-spawn + model-reload overhead that calling
whisper-cli fresh each time would incur.
"""
import ctypes
import os
import re
import threading

import numpy as np

_SHIM_PATH_CANDIDATES = [
    "/app/lib/libnexa_whisper_shim.so",
]

# Whisper sometimes hallucinates bracketed non-speech captions (from its
# training data) on short or ambiguous audio, e.g. "(logo whooshing)",
# "[BLANK_AUDIO]", "(music)". Strip these rather than showing them as text.
_HALLUCINATION_TAG_RE = re.compile(r"[\(\[][^)\]]*[\)\]]")


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


class NativeWhisper:
    """Thread-safe: transcribe_pcm16() serializes concurrent calls through
    an internal lock, since whisper.cpp's context can't handle overlapping
    whisper_full() calls from multiple threads (Nexa's live partial-preview
    loop and its final transcription can otherwise race on the same context,
    which crashes the process)."""

    def __init__(self, model_path):
        self._lib = None
        self._ctx = None
        self.available = False
        # whisper.cpp's context isn't safe for concurrent whisper_full()
        # calls from multiple threads -- the live partial-preview loop and
        # the final transcription both hit this same context, so every
        # call must be serialized through this lock.
        self._call_lock = threading.Lock()

        shim_path = _first_existing(_SHIM_PATH_CANDIDATES)
        if not shim_path or not model_path or not os.path.exists(model_path):
            return

        try:
            lib = ctypes.CDLL(shim_path)
            lib.nexa_whisper_init.restype = ctypes.c_void_p
            lib.nexa_whisper_init.argtypes = [ctypes.c_char_p]
            lib.nexa_whisper_transcribe.restype = ctypes.c_void_p
            lib.nexa_whisper_transcribe.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p
            ]
            lib.nexa_whisper_free_string.argtypes = [ctypes.c_void_p]
            lib.nexa_whisper_free.argtypes = [ctypes.c_void_p]

            ctx = lib.nexa_whisper_init(model_path.encode("utf-8"))
            if not ctx:
                return

            self._lib = lib
            self._ctx = ctx
            self.available = True
        except Exception:
            self._lib = None
            self._ctx = None
            self.available = False

    def transcribe_pcm16(self, pcm_bytes, n_threads=4, audio_ctx=0, initial_prompt=None):
        """pcm_bytes: raw 16-bit signed little-endian mono 16kHz PCM (the
        same format Nexa's GStreamer capture already produces).

        audio_ctx limits the encoder context (in ~20ms frames; 0 = full
        30s context). A smaller value roughly halves latency for short
        clips -- but must comfortably exceed the actual audio duration,
        or the decoder loses alignment and loops on garbled repeated text.
        768 (~15s of context) is safe for anything up to ~10s of audio.

        initial_prompt biases recognition toward specific vocabulary
        (e.g. "Nexa" and her command words), which the tiny model
        otherwise often mishears as a similar-sounding common word."""
        if not self.available:
            return ""
        audio_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        if audio_i16.size == 0:
            return ""
        audio_f32 = np.ascontiguousarray(audio_i16.astype(np.float32) / 32768.0)
        ptr = audio_f32.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

        prompt_bytes = initial_prompt.encode("utf-8") if initial_prompt else None
        with self._call_lock:
            result_ptr = self._lib.nexa_whisper_transcribe(self._ctx, ptr, audio_f32.size, n_threads, audio_ctx, prompt_bytes)
            if not result_ptr:
                return ""
            text = ctypes.cast(result_ptr, ctypes.c_char_p).value
            self._lib.nexa_whisper_free_string(result_ptr)

        text = (text or b"").decode("utf-8", errors="ignore").strip()
        text = _HALLUCINATION_TAG_RE.sub("", text).strip()
        return text

    def close(self):
        if self._lib and self._ctx:
            try:
                self._lib.nexa_whisper_free(self._ctx)
            except Exception:
                pass
        self._ctx = None
        self.available = False
