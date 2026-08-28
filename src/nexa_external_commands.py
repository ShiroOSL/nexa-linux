"""Storage for commands registered by OTHER apps/extensions that connect to
Nexa via the org.nexa.CommandRegistry D-Bus service (see
command_registry_service.py). Keyed by app_id so a disconnecting app's
commands can be cleanly removed.

{
  "app_id": {
    "app_name": str,
    "commands": [
      {
        "trigger": str,
        "description": str,
        "action_type": "dbus" | "say",
        # action_type == "dbus" (parameterless method calls only, v1):
        "bus_name": str, "object_path": str, "interface": str, "method": str,
        # action_type == "say":
        "response": str,
      },
      ...
    ]
  },
  ...
}
"""
import os
import json

REGISTRY_DIR = os.path.join(os.path.expanduser("~"), ".config", "nexa")
REGISTRY_FILE = os.path.join(REGISTRY_DIR, "external_commands.json")


def load_registry():
    if not os.path.exists(REGISTRY_FILE):
        return {}
    try:
        with open(REGISTRY_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_registry(registry):
    if not os.path.exists(REGISTRY_DIR):
        os.makedirs(REGISTRY_DIR)
    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
        return True
    except Exception:
        return False


def register_app(app_id, app_name, commands):
    registry = load_registry()
    clean_commands = []
    for cmd in commands:
        trigger = str(cmd.get("trigger", "")).strip().lower()
        if not trigger:
            continue
        action_type = cmd.get("action_type", "say")
        entry = {
            "trigger": trigger,
            "description": str(cmd.get("description", "")).strip(),
            "action_type": action_type,
        }
        if action_type == "dbus":
            entry["bus_name"] = str(cmd.get("bus_name", "")).strip()
            entry["object_path"] = str(cmd.get("object_path", "")).strip()
            entry["interface"] = str(cmd.get("interface", "")).strip()
            entry["method"] = str(cmd.get("method", "")).strip()
            if not all([entry["bus_name"], entry["object_path"], entry["interface"], entry["method"]]):
                continue
        else:
            entry["response"] = str(cmd.get("response", "")).strip()
            if not entry["response"]:
                continue
        clean_commands.append(entry)

    if not clean_commands:
        return False

    registry[app_id] = {"app_name": app_name.strip() or app_id, "commands": clean_commands}
    return save_registry(registry)


def unregister_app(app_id):
    registry = load_registry()
    if app_id in registry:
        del registry[app_id]
        save_registry(registry)
        return True
    return False


def list_apps():
    registry = load_registry()
    return {app_id: entry.get("app_name", app_id) for app_id, entry in registry.items()}


def list_commands(app_id):
    """Commands registered by one app, as [{trigger, description, action_type}, ...]."""
    registry = load_registry()
    entry = registry.get(app_id)
    if not entry:
        return []
    return entry.get("commands", [])


def find_matching_command(clean_text):
    """Returns (app_name, command_dict) for the first registered trigger
    that appears in clean_text, or (None, None) if nothing matches."""
    registry = load_registry()
    for entry in registry.values():
        app_name = entry.get("app_name", "")
        for cmd in entry.get("commands", []):
            trigger = cmd.get("trigger", "")
            if trigger and trigger in clean_text:
                return app_name, cmd
    return None, None
