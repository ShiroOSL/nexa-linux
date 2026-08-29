# Copyright (c) 2026 ShiroOSL. All Rights Reserved.
# This file is proprietary and NOT covered by the repo's GPL-3.0 license.
# See LICENSE-PRIVATE.md.
"""Nexa Studio: user-defined custom commands.

Each command is a dict:
{
    "id": str,
    "trigger": str,           # phrase the user says/types
    "type": "say" | "run",    # respond with text, or execute a host command
    "response": str,          # spoken/text reply (type == "say")
    "shell_command": str,     # command to run (type == "run")
    "speak_output": bool,     # for "run": speak the command's stdout back
}
"""
import os
import json
import uuid

STUDIO_DIR = os.path.join(os.path.expanduser("~"), ".config", "nexa")
STUDIO_FILE = os.path.join(STUDIO_DIR, "studio_commands.json")


def load_commands():
    if not os.path.exists(STUDIO_FILE):
        return []
    try:
        with open(STUDIO_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_commands(commands):
    if not os.path.exists(STUDIO_DIR):
        os.makedirs(STUDIO_DIR)
    try:
        with open(STUDIO_FILE, "w") as f:
            json.dump(commands, f, indent=2)
        return True
    except Exception:
        return False


def new_command(trigger, cmd_type, response="", shell_command="", speak_output=True):
    return {
        "id": uuid.uuid4().hex[:8],
        "trigger": trigger.strip(),
        "type": cmd_type,
        "response": response.strip(),
        "shell_command": shell_command.strip(),
        "speak_output": speak_output,
    }


RECOMMENDATIONS = [
    {"trigger": "good morning", "type": "say", "response": "Good morning! Ready when you are."},
    {"trigger": "good night", "type": "say", "response": "Good night! I'll be here when you're back."},
    {"trigger": "open my files", "type": "run", "shell_command": "xdg-open ~"},
    {"trigger": "empty trash", "type": "run", "shell_command": "gio trash --empty"},
    {"trigger": "tell me a joke", "type": "say", "response": "Why do programmers prefer dark mode? Because light attracts bugs."},
    {"trigger": "lock my screen please", "type": "run", "shell_command": "loginctl lock-session"},
]
