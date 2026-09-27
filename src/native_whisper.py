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
import time

import numpy as np

_SHIM_PATH_CANDIDATES = [
    "/app/lib/libnexa_whisper_shim.so",
]

# Whisper sometimes hallucinates bracketed non-speech captions (from its
# training data) on short or ambiguous audio, e.g. "(logo whooshing)",
# "[BLANK_AUDIO]", "(music)". Strip these rather than showing them as text.
_HALLUCINATION_TAG_RE = re.compile(r"[\(\[][^)\]]*[\)\]]")

# If whisper hasn't been used for this long, unload it to free memory.
# It reloads automatically (lazily) next time it's actually needed.
IDLE_UNLOAD_SECONDS = 120
IDLE_CHECK_INTERVAL_SECONDS = 15


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
        self._init_lock = threading.Lock()

        self._shim_path = _first_existing(_SHIM_PATH_CANDIDATES)
        self._model_path = model_path
        self._last_used = 0.0
        # Lazy-load: the model (~77MB+ resident) is only loaded into memory
        # on the first real transcription call, not at app startup. Most
        # sessions never use STT, so this keeps idle RAM down.
        if self._shim_path and model_path and os.path.exists(model_path):
            self.available = True
            threading.Thread(target=self._idle_watch_loop, daemon=True).start()

    def _idle_watch_loop(self):
        while True:
            time.sleep(IDLE_CHECK_INTERVAL_SECONDS)
            if self._ctx is None:
                continue
            if (time.time() - self._last_used) >= IDLE_UNLOAD_SECONDS:
                self.close()

    def _ensure_loaded(self):
        if self._ctx is not None:
            return True
        with self._init_lock:
            if self._ctx is not None:
                return True
            try:
                lib = ctypes.CDLL(self._shim_path)
                lib.nexa_whisper_init.restype = ctypes.c_void_p
                lib.nexa_whisper_init.argtypes = [ctypes.c_char_p]
                lib.nexa_whisper_transcribe.restype = ctypes.c_void_p
                lib.nexa_whisper_transcribe.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p
                ]
                lib.nexa_whisper_free_string.argtypes = [ctypes.c_void_p]
                lib.nexa_whisper_free.argtypes = [ctypes.c_void_p]

                ctx = lib.nexa_whisper_init(self._model_path.encode("utf-8"))
                if not ctx:
                    self.available = False
                    return False

                self._lib = lib
                self._ctx = ctx
                return True
            except Exception:
                self.available = False
                return False

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
        self._last_used = time.time()
        audio_i16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        if audio_i16.size == 0:
            return ""
        audio_f32 = np.ascontiguousarray(audio_i16.astype(np.float32) / 32768.0)
        ptr = audio_f32.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

        prompt_bytes = initial_prompt.encode("utf-8") if initial_prompt else None
        with self._call_lock:
            # Re-checked/reloaded while holding _call_lock so close() (which
            # also takes _call_lock, after _init_lock) can never free _ctx
            # between the load and the native call below -- avoids a race
            # where idle-unload frees the context out from under an
            # in-flight transcription.
            if self._ctx is None and not self._ensure_loaded():
                return ""
            result_ptr = self._lib.nexa_whisper_transcribe(self._ctx, ptr, audio_f32.size, n_threads, audio_ctx, prompt_bytes)
            if not result_ptr:
                return ""
            text = ctypes.cast(result_ptr, ctypes.c_char_p).value
            self._lib.nexa_whisper_free_string(result_ptr)

        text = (text or b"").decode("utf-8", errors="ignore").strip()
        text = _HALLUCINATION_TAG_RE.sub("", text).strip()
        return text

    def close(self):
        """Frees the loaded model/context to reclaim memory. Safe to call
        while idle -- the model reloads automatically (see _ensure_loaded)
        the next time transcribe_pcm16() is actually called."""
        # Lock order must match transcribe_pcm16 (_call_lock then _init_lock,
        # since transcribe holds _call_lock while _ensure_loaded takes
        # _init_lock inside it) -- acquiring them in the opposite order here
        # would deadlock against an in-flight transcription.
        with self._call_lock, self._init_lock:
            if self._lib and self._ctx:
                try:
                    self._lib.nexa_whisper_free(self._ctx)
                except Exception:
                    pass
            self._ctx = None
        # self.available stays True -- it only reflects whether the shim
        # and model files exist, not whether the context is currently
        # loaded. Setting it False here would permanently disable STT.
