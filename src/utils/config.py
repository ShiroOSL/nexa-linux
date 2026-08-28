import os
import json

class Config:
    def __init__(self):
        self.config_dir = os.path.join(os.path.expanduser("~"), ".config", "nexa")
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.settings = {
            "voice_enabled": True,
            "theme": "system",
            "first_run": True
        }
        self.load()

    def load(self):
        """Load settings from the config file."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    self.settings.update(json.load(f))
            except Exception as e:
                print(f"Error loading config: {e}")
        else:
            self.save()

    def save(self):
        """Save current settings to the config file."""
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key):
        return self.settings.get(key)

    def set(self, key, value):
        self.settings[key] = value
        self.save()
