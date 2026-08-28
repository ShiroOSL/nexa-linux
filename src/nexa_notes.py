"""Simple persisted notes, mainly for the "remember this" / clipboard
commands. Each note is a dict:
{
    "id": str,
    "text": str,
    "created": float,   # unix timestamp
}
"""
import os
import json
import time
import uuid

NOTES_DIR = os.path.join(os.path.expanduser("~"), ".config", "nexa")
NOTES_FILE = os.path.join(NOTES_DIR, "notes.json")

MAX_NOTES = 100  # rolling cap so the file can't grow unbounded


def load_notes():
    if not os.path.exists(NOTES_FILE):
        return []
    try:
        with open(NOTES_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_notes(notes):
    if not os.path.exists(NOTES_DIR):
        os.makedirs(NOTES_DIR)
    try:
        with open(NOTES_FILE, "w") as f:
            json.dump(notes[:MAX_NOTES], f, indent=2)
        return True
    except Exception:
        return False


def add_note(text):
    notes = load_notes()
    note = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "created": time.time()}
    notes.insert(0, note)
    save_notes(notes)
    return note


def clear_notes():
    return save_notes([])
