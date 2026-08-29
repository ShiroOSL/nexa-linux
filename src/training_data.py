# Copyright (c) 2026 ShiroOSL. All Rights Reserved.
# This file is proprietary and NOT covered by the repo's GPL-3.0 license.
# See LICENSE-PRIVATE.md.
"""Optional, privacy-conscious local training-data collection for Nexa's
speech models (Whisper for both general commands and the "Hey Nexa" wake
phrase specifically). Both are OFF by default -- each must be explicitly
enabled in Preferences. Nothing is ever sent anywhere automatically; the
only way data leaves this machine is if the user explicitly exports it
themselves via the Export button, which produces a local zip file they
choose where to save.
"""
import os
import shutil
import time
import wave
import zipfile

DATA_DIR = os.path.expanduser("~/.local/share/nexa/training-data")
WAKEWORD_DIR = os.path.join(DATA_DIR, "wakeword")
STT_DIR = os.path.join(DATA_DIR, "stt")
SAMPLE_RATE = 16000


class TrainingDataCollector:
    def __init__(self):
        self.collect_wakeword = False
        self.collect_stt = False

    def set_collect_wakeword(self, enabled):
        self.collect_wakeword = enabled

    def set_collect_stt(self, enabled):
        self.collect_stt = enabled

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

    def save_stt_sample(self, pcm_bytes, transcript):
        """A (recording, final transcript) pair -- one full voice command."""
        if not self.collect_stt or not pcm_bytes or not transcript:
            return
        try:
            os.makedirs(STT_DIR, exist_ok=True)
            stamp = int(time.time() * 1000)
            self._write_wav(os.path.join(STT_DIR, f"stt_{stamp}.wav"), pcm_bytes)
            with open(os.path.join(STT_DIR, f"stt_{stamp}.txt"), "w", encoding="utf-8") as f:
                f.write(transcript)
        except Exception:
            pass

    def _write_wav(self, path, pcm_bytes):
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_bytes)

    def counts(self):
        return self._count_files(WAKEWORD_DIR, ".wav"), self._count_files(STT_DIR, ".wav")

    def _count_files(self, directory, suffix):
        if not os.path.isdir(directory):
            return 0
        return len([f for f in os.listdir(directory) if f.endswith(suffix)])

    def has_any_data(self):
        wc, sc = self.counts()
        return (wc + sc) > 0

    def export_to_zip(self, output_path):
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for base_dir, arc_prefix in [(WAKEWORD_DIR, "wakeword"), (STT_DIR, "stt")]:
                if not os.path.isdir(base_dir):
                    continue
                for fname in os.listdir(base_dir):
                    fpath = os.path.join(base_dir, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, arcname=os.path.join(arc_prefix, fname))

    def clear_all(self):
        for d in (WAKEWORD_DIR, STT_DIR):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
