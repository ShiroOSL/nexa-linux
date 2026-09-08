"""Local speech training-data collection for Nexa's speech models. Wake
word ("Hey Nexa") sample collection is opt-in via Preferences and OFF by
default -- there is no equivalent for general speech (STT) commands;
those are transcribed and used in memory only, never written to disk.
Nothing is ever sent anywhere automatically; the only way data leaves
this machine is if the user explicitly exports it themselves via the
Export button, which produces a local zip file they choose where to save.
"""
import os
import shutil
import time
import wave
import zipfile

DATA_DIR = os.path.expanduser("~/.local/share/nexa/training-data")
WAKEWORD_DIR = os.path.join(DATA_DIR, "wakeword")
SAMPLE_RATE = 16000


class TrainingDataCollector:
    def __init__(self):
        self.collect_wakeword = False

    def set_collect_wakeword(self, enabled):
        self.collect_wakeword = enabled

    def save_wakeword_sample(self, pcm_bytes):
        """pcm_bytes: raw 16-bit mono 16kHz PCM covering the moment "Hey
        Nexa" was heard, as captured by WakeWordEngine's rolling history."""
        if not self.collect_wakeword or not pcm_bytes:
            return
        try:
            os.makedirs(WAKEWORD_DIR, exist_ok=True)
            path = os.path.join(WAKEWORD_DIR, f"hey_nexa_{int(time.time() * 1000)}.wav")
            self._write_wav(path, pcm_bytes)
        except Exception:
            pass

    def _write_wav(self, path, pcm_bytes):
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_bytes)

    def counts(self):
        return self._count_files(WAKEWORD_DIR, ".wav")

    def _count_files(self, directory, suffix):
        if not os.path.isdir(directory):
            return 0
        return len([f for f in os.listdir(directory) if f.endswith(suffix)])

    def has_any_data(self):
        return self.counts() > 0

    def export_to_zip(self, output_path):
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.isdir(WAKEWORD_DIR):
                for fname in os.listdir(WAKEWORD_DIR):
                    fpath = os.path.join(WAKEWORD_DIR, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, arcname=os.path.join("wakeword", fname))

    def clear_all(self):
        if os.path.isdir(WAKEWORD_DIR):
            shutil.rmtree(WAKEWORD_DIR, ignore_errors=True)
