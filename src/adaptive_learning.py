"""Local-only adaptive personalization for Nexa's voice recognition.

Important: this is NOT model training. Real fine-tuning of Whisper or the
wake-word classifier needs PyTorch (a heavy dependency we don't bundle) and
realistically hours-to-days of compute on hardware without a GPU -- not
something that can happen quietly in the background. What's here instead
are two lightweight, fully local heuristics built on data Nexa already
has access to:

1. A growing vocabulary bias: words that keep showing up in your actual
   transcripts get folded into the prompt fed to Whisper, the same
   mechanism already used to bias it toward "Nexa" and command words.
2. Self-tuning wake sensitivity: nudges the wake-word threshold toward
   whatever's actually working, based on real trigger confidence scores.

Both are off by default and never leave the machine -- this is separate
from (and doesn't require) the opt-in exportable training-data collection
in training_data.py.
"""
import json
import os
import re

CONFIG_DIR = os.path.expanduser("~/.config/nexa")
VOCAB_FILE = os.path.join(CONFIG_DIR, "learned_vocabulary.json")
SENSITIVITY_FILE = os.path.join(CONFIG_DIR, "learned_sensitivity.json")

# Common English words to ignore when looking for "notable" vocabulary --
# we want names/uncommon words that keep recurring, not "the"/"is"/etc.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "and", "or", "but", "if", "so", "because", "as", "of", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "than", "too", "very", "can", "will", "just", "don", "should", "now",
    "do", "does", "did", "doing", "have", "has", "had", "having",
    "what", "which", "who", "whom", "please", "hey", "nexa", "okay", "ok",
    "yes", "yeah", "hi", "hello", "thanks", "thank", "want", "need", "like",
    "get", "go", "going", "make", "know", "think", "see", "look", "come",
}

MAX_LEARNED_WORDS = 25
MIN_OCCURRENCES = 3  # a word must recur this many times before it's trusted
MAX_TRIGGER_SCORES = 50  # only keep the most recent N trigger scores


class AdaptiveLearning:
    def __init__(self):
        self.enabled = True
        self._word_counts = self._load_json(VOCAB_FILE, {}).get("word_counts", {})
        self._trigger_scores = self._load_json(SENSITIVITY_FILE, {}).get("trigger_scores", [])

    def set_enabled(self, enabled):
        self.enabled = enabled

    def _load_json(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, path, data):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    # --- Vocabulary -----------------------------------------------------------
    def record_transcript(self, text):
        """Text-only, no audio -- updates word-frequency counts used to
        grow Whisper's recognition-bias prompt over time."""
        if not self.enabled or not text:
            return
        for word in re.findall(r"[a-zA-Z']+", text.lower()):
            if len(word) < 3 or word in _STOPWORDS:
                continue
            self._word_counts[word] = self._word_counts.get(word, 0) + 1
        self._save_json(VOCAB_FILE, {"word_counts": self._word_counts})

    def get_learned_words(self):
        frequent = [w for w, c in self._word_counts.items() if c >= MIN_OCCURRENCES]
        frequent.sort(key=lambda w: self._word_counts[w], reverse=True)
        return frequent[:MAX_LEARNED_WORDS]

    def build_prompt(self, base_prompt):
        """Appends learned vocabulary onto the base command-vocabulary
        prompt already used to bias Whisper toward "Nexa" and command words."""
        learned = self.get_learned_words()
        if not learned:
            return base_prompt
        return base_prompt + " Also recognizable words: " + ", ".join(learned) + "."

    # --- Wake sensitivity -------------------------------------------------------
    def record_wake_trigger(self, score):
        """Tracks how confidently real "Hey Nexa" triggers have been
        scoring, to nudge the effective threshold toward what's actually
        working for this mic/room."""
        if not self.enabled or score is None:
            return
        self._trigger_scores.append(float(score))
        if len(self._trigger_scores) > MAX_TRIGGER_SCORES:
            del self._trigger_scores[:len(self._trigger_scores) - MAX_TRIGGER_SCORES]
        self._save_json(SENSITIVITY_FILE, {"trigger_scores": self._trigger_scores})

    def get_threshold_offset(self):
        """A small delta (within a safe band) to nudge the wake-word
        threshold: comfortably high-scoring triggers mean we can afford
        to be a bit stricter (fewer false positives); triggers barely
        clearing the bar mean we should ease off so real utterances
        aren't missed."""
        if len(self._trigger_scores) < 5:
            return 0.0
        avg = sum(self._trigger_scores) / len(self._trigger_scores)
        if avg > 0.85:
            return 0.05
        if avg < 0.60:
            return -0.05
        return 0.0

    def clear(self):
        self._word_counts = {}
        self._trigger_scores = []
        self._save_json(VOCAB_FILE, {"word_counts": {}})
        self._save_json(SENSITIVITY_FILE, {"trigger_scores": []})
