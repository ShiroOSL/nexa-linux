#!/usr/bin/env python3
"""Nexa Assistant - main application entry point.

Owns the onboarding wizard (NexaSetupWindow) and the main chat
interface (NexaWindow), and wires together CommandEngine,
VoiceManager and DBusManager.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Gdk
 
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commands import CommandEngine
from voice_manager import VoiceManager
from stt_engine import VoiceInputEngine
from wake_word_engine import WakeWordEngine
from dbus_manager import DBusManager
from command_registry_service import CommandRegistryService
import nexa_external_commands
from sound_effects import SoundEffects
from training_data import TrainingDataCollector
from adaptive_learning import AdaptiveLearning
from nexa_studio import NexaStudioWindow
from tray_manager import TrayManager
from global_shortcut_manager import GlobalShortcutManager

CONFIG_DIR = os.path.expanduser("~/.config/nexa")
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "org.nexa.Assistant.desktop")
VOICE_INPUT_MODES = ["Default", "Longer", "Longest"]
VOICE_INPUT_KEYS = ["default", "longer", "longest"]
LONG_THINKING_SECONDS = 5.0  # threshold for playing the "finally done" sound cue
WAKE_RESUME_COOLDOWN_MS = 1500  # buffer after she finishes speaking before wake-word listening resumes, so trailing echo of her own voice can't immediately false-trigger it again
NEXA_VERSION = "1.0.0"
NEXA_GITHUB_URL = "https://github.com/ShiroOSL"
LOCATIONS = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
    "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia",
    "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso",
    "Burundi", "Cabo Verde", "Cambodia", "Cameroon", "Canada", "Central African Republic",
    "Chad", "Chile", "China", "Colombia", "Comoros", "Congo", "Costa Rica", "Croatia", "Cuba",
    "Cyprus", "Czechia", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador",
    "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
    "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece",
    "Grenada", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras", "Hungary",
    "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica",
    "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan",
    "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
    "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco",
    "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
    "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama",
    "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
    "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe",
    "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia",
    "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan",
    "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan",
    "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago",
    "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City",
    "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
]


def ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def read_config(name, default=""):
    path = os.path.join(CONFIG_DIR, name)
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return default


def write_config(name, value):
    ensure_config_dir()
    with open(os.path.join(CONFIG_DIR, name), "w") as f:
        f.write(value)


def enable_autostart():
    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Nexa Assistant\n"
        "Comment=Start Nexa Assistant automatically at login\n"
        "Exec=flatpak run org.nexa.Assistant\n"
        "Icon=org.nexa.Assistant\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
    )
    with open(AUTOSTART_FILE, "w") as f:
        f.write(content)


def disable_autostart():
    try:
        os.remove(AUTOSTART_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


class NexaSetupWindow(Adw.ApplicationWindow):
    """Three-step onboarding wizard: welcome -> username -> location."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Welcome to Nexa")
        self.set_default_size(560, 480)
        self.set_resizable(False)

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_title(False)
        toolbar_view.add_top_bar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        toolbar_view.set_content(self.stack)

        self._build_welcome_page()
        self._build_username_page()
        self._build_location_page()

    def _build_welcome_page(self):
        page = Adw.StatusPage(
            title="Welcome to Nexa",
            description="Your personal assistant for the GNOME desktop.",
            icon_name="org.nexa.Assistant",
        )
        button = Gtk.Button(label="Get Started")
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.set_margin_top(24)
        button.connect("clicked", lambda *_: self.stack.set_visible_child_name("username"))
        page.set_child(button)
        self.stack.add_named(page, "welcome")

    def _build_username_page(self):
        page = Adw.StatusPage(
            title="What should I call you?",
            description="Nexa will use this name when greeting you.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_halign(Gtk.Align.CENTER)

        self.username_entry = Gtk.Entry()
        self.username_entry.set_placeholder_text("Your name")
        self.username_entry.set_width_chars(24)

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self.stack.set_visible_child_name("location"))

        box.append(self.username_entry)
        box.append(next_button)
        page.set_child(box)
        self.stack.add_named(page, "username")

    def _build_location_page(self):
        page = Adw.StatusPage(
            title="Where are you based?",
            description="Used for weather lookups.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_halign(Gtk.Align.CENTER)

        self.location_dropdown = Gtk.DropDown.new_from_strings(LOCATIONS)
        self.location_dropdown.set_enable_search(True)

        finish_button = Gtk.Button(label="Finish")
        finish_button.add_css_class("suggested-action")
        finish_button.add_css_class("pill")
        finish_button.connect("clicked", self.on_finish)

        box.append(self.location_dropdown)
        box.append(finish_button)
        page.set_child(box)
        self.stack.add_named(page, "location")

    def on_finish(self, _button):
        username = self.username_entry.get_text().strip() or "Friend"
        idx = self.location_dropdown.get_selected()
        location = LOCATIONS[idx] if idx != Gtk.INVALID_LIST_POSITION else "Morocco"

        write_config("username", username)
        write_config("user_location", location)
        write_config("setup_done", "1")

        app = self.get_application()
        main_win = NexaWindow(application=app)
        app.win = main_win  # so re-activation (hotkey) targets the real chat window
        app.start_tray()
        app.start_global_shortcut()
        main_win.present()
        self.close()


class NexaWindow(Adw.ApplicationWindow):
    """Main chat workspace."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Nexa Assistant")
        self.set_default_size(1060, 600)
        self.connect("close-request", self.on_close_request)

        # Register our bundled weather icons with the icon theme so they can
        # be looked up by name (e.g. "sunny") via Gtk.Image + set_pixel_size.
        # This rasterizes each SVG fresh at the exact requested pixel size
        # (and correct HiDPI scale) on every lookup, avoiding the blurriness
        # and inconsistent sizing that comes from manually loading a fixed
        # texture into a Gtk.Picture.
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for icons_dir in (
            "/app/share/nexa/weather-icons",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "weather-icons"),
        ):
            if os.path.isdir(icons_dir):
                icon_theme.add_search_path(icons_dir)

        self.user_name = read_config("username", "Friend")
        self.user_location = read_config("user_location", "Morocco")
        self.fav_music = read_config("fav_music", "")
        self.voice_enabled = read_config("voice_enabled", "1") == "1"
        self.voice_gender = read_config("voice_gender", "female")
        if self.voice_gender not in ("female", "male"):
            self.voice_gender = "female"
        self.voice_input_mode = read_config("voice_input_mode", "default")
        if self.voice_input_mode not in VOICE_INPUT_KEYS:
            self.voice_input_mode = "default"
        self.run_in_background = read_config("run_in_background", "1") == "1"
        self.launch_at_startup = os.path.exists(AUTOSTART_FILE)
        self.show_tray_icon = read_config("show_tray_icon", "1") == "1"
        self.quick_command_hotkey_enabled = read_config("quick_command_hotkey_enabled", "0") == "1"
        self.wakeword_enabled = read_config("wakeword_enabled", "0") == "1"
        self.wakeword_sensitivity = read_config("wakeword_sensitivity", "medium")
        if self.wakeword_sensitivity not in ("low", "medium", "high"):
            self.wakeword_sensitivity = "medium"
        self.collect_wakeword_data = read_config("collect_wakeword_data", "0") == "1"
        self.collect_stt_data = read_config("collect_stt_data", "0") == "1"
        self.adaptive_learning_enabled = read_config("adaptive_learning_enabled", "1") == "1"

        self.training_data = TrainingDataCollector()
        self.training_data.set_collect_wakeword(self.collect_wakeword_data)
        self.training_data.set_collect_stt(self.collect_stt_data)

        self.adaptive = AdaptiveLearning()
        self.adaptive.set_enabled(self.adaptive_learning_enabled)

        self.engine = CommandEngine(self)
        self.voice = VoiceManager(
            on_speech_start=self._on_speech_start,
            on_speech_end=self._on_speech_end,
        )
        self.voice.load_model()
        self.voice.set_voice(self.voice_gender)
        self.voice.set_enabled(self.voice_enabled)
        self.stt = VoiceInputEngine(
            on_state_change=self._on_voice_state_change,
            on_result=self._on_voice_result,
            on_error=self._on_voice_error,
            on_partial_result=self._on_voice_partial_result,
            on_stt_sample=self._on_stt_sample,
        )
        self.stt.set_timeout_mode(self.voice_input_mode)
        self.stt.set_vocabulary_prompt(self.adaptive.build_prompt(self.engine.get_vocabulary_prompt()))
        self.stt.set_command_matcher(self.engine.matches_known_command)
        self.wake_engine = WakeWordEngine(
            on_wake=self._on_wake_word_detected,
            on_error=self._on_wake_word_error,
            on_wake_audio=self.training_data.save_wakeword_sample,
            on_wake_score=self._on_wake_score,
        )
        self.wake_engine.set_threshold(self.wakeword_sensitivity)
        self.wake_engine.apply_adaptive_offset(self.adaptive.get_threshold_offset())
        self.dbus = DBusManager()
        self.dbus.initialize()
        self.sounds = SoundEffects()
        self._is_speaking = False
        self._training_export_row = None
        self._prefs_toast_overlay = None
        self._connected_apps_row = None
        self._connected_apps_listbox = None

        self.command_registry_service = CommandRegistryService(
            on_registry_changed=self._refresh_connected_apps_group,
            on_register_request=self._on_connect_nexa_request,
        )

        self._build_ui()

        if self.wakeword_enabled and self.wake_engine.is_available():
            self.wake_engine.start()

    # --- UI construction ---------------------------------------------------------
    def _build_ui(self):
        self.nav_view = Adw.NavigationView()
        self.set_content(self.nav_view)
        self.nav_view.add(self._build_chat_page())

    def _build_chat_page(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_title_widget(Gtk.Label(label="Nexa Assistant"))
        toolbar_view.add_top_bar(header)

        reset_button = Gtk.Button(icon_name="edit-clear-all-symbolic")
        reset_button.set_tooltip_text("Reset Conversation")
        reset_button.connect("clicked", self.on_reset_conversation)
        header.pack_start(reset_button)

        prefs_button = Gtk.Button(icon_name="preferences-system-symbolic")
        prefs_button.set_tooltip_text("Preferences")
        prefs_button.connect("clicked", self.on_open_preferences)
        header.pack_end(prefs_button)

        # Stack: empty "hero" state <-> active conversation view
        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(400)

        self.content_stack.add_named(self._build_hero_page(), "hero")
        self.content_stack.add_named(self._build_conversation_view(), "chat")
        self.content_stack.set_visible_child_name("hero")

        entry_box = self._build_entry_bar()
        entry_box.set_valign(Gtk.Align.END)
        entry_box.set_halign(Gtk.Align.FILL)

        self.entry_revealer = Gtk.Revealer()
        self.entry_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.entry_revealer.set_transition_duration(550)
        self.entry_revealer.set_valign(Gtk.Align.END)
        self.entry_revealer.set_halign(Gtk.Align.FILL)
        self.entry_revealer.set_child(entry_box)
        self.entry_revealer.set_reveal_child(False)
        GLib.timeout_add(150, lambda: (self.entry_revealer.set_reveal_child(True), False)[1])

        content_overlay = Gtk.Overlay()
        content_overlay.set_child(self.content_stack)
        content_overlay.add_overlay(self.entry_revealer)

        toolbar_view.set_content(content_overlay)
        return Adw.NavigationPage(title="Nexa", child=toolbar_view)

    def _build_hero_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        self.hero_glow = Gtk.Box()
        self.hero_glow.add_css_class("nexa-hero-glow")
        self.hero_glow.set_size_request(220, 220)
        self.hero_glow.set_halign(Gtk.Align.CENTER)
        self.hero_glow.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
        icon.set_pixel_size(112)
        icon.add_css_class("nexa-hero-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)

        overlay = Gtk.Overlay()
        overlay.set_child(self.hero_glow)
        overlay.add_overlay(icon)
        overlay.set_halign(Gtk.Align.CENTER)
        box.append(overlay)

        self.hero_greeting = Gtk.Label(label=self.engine.get_initial_greeting())
        self.hero_greeting.add_css_class("title-2")
        self.hero_greeting.set_opacity(0)
        box.append(self.hero_greeting)

        self.hero_subtitle = Gtk.Label(label='Say "Hey Nexa" or type below to get started')
        self.hero_subtitle.add_css_class("dim-label")
        self.hero_subtitle.set_opacity(0)
        box.append(self.hero_subtitle)

        self.hero_box = box
        box.set_opacity(0)
        GLib.idle_add(self._animate_hero_entrance)
        return box

    def _animate_hero_entrance(self):
        fade_box = Adw.TimedAnimation.new(
            self.hero_box, 0, 1, 550,
            Adw.CallbackAnimationTarget.new(lambda v: self.hero_box.set_opacity(v)),
        )
        fade_box.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        fade_box.play()
        self._hero_anims = [fade_box]

        def fade_text(label, delay):
            def start(*_a):
                anim = Adw.TimedAnimation.new(
                    label, 0, 1, 450,
                    Adw.CallbackAnimationTarget.new(lambda v: label.set_opacity(v)),
                )
                anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
                anim.play()
                self._hero_anims.append(anim)
            GLib.timeout_add(delay, lambda: (start(), False)[1])

        fade_text(self.hero_greeting, 200)
        fade_text(self.hero_subtitle, 350)

        glow_pulse = Adw.TimedAnimation.new(
            self.hero_glow, 0.55, 1.0, 1900,
            Adw.CallbackAnimationTarget.new(lambda v: self.hero_glow.set_opacity(v)),
        )
        glow_pulse.set_easing(Adw.Easing.EASE_IN_OUT_SINE)
        glow_pulse.set_repeat_count(0)
        glow_pulse.set_alternate(True)
        glow_pulse.play()
        self._hero_anims.append(glow_pulse)
        return False

    def _build_conversation_view(self):
        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.chat_box.set_margin_top(24)
        self.chat_box.set_margin_bottom(90)
        self.chat_box.set_margin_start(24)
        self.chat_box.set_margin_end(24)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_child(self.chat_box)
        return self.scrolled

    def _build_entry_bar(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"entry.nexa-input { border-radius: 24px; min-height: 26px; "
            b"padding-left: 18px; padding-right: 18px; } "
            b".nexa-user-bubble { background-color: #3584e4; color: #ffffff; "
            b"border-radius: 999px; padding: 10px 16px; } "
            b".nexa-bot-bubble { background-color: transparent; border-radius: 999px; padding: 10px 16px; }"
            b".nexa-weather-card { background: linear-gradient(135deg, #4a90e2 0%, #2f6fd1 100%); "
            b"border-radius: 22px; padding: 18px 26px; min-width: 260px; } "
            b".nexa-weather-location { color: alpha(#ffffff, 0.85); font-size: 12px; "
            b"font-weight: 600; letter-spacing: 0.5px; } "
            b".nexa-weather-condition { color: alpha(#ffffff, 0.95); font-size: 14px; font-weight: 500; } "
            b".nexa-weather-temp { color: #ffffff; font-size: 34px; font-weight: 700; } "
            b".nexa-weather-humidity-pill { background-color: alpha(#ffffff, 0.16); border-radius: 999px; "
            b"padding: 4px 10px; } "
            b".nexa-weather-humidity { color: #eaf3ff; font-size: 12px; font-weight: 500; }"
            b".nexa-hero-glow { background: radial-gradient(circle, alpha(#3584e4, 0.38) 0%, "
            b"alpha(#3584e4, 0.10) 45%, alpha(#3584e4, 0) 70%); border-radius: 9999px; }"
            b".nexa-hero-icon { filter: drop-shadow(0 6px 18px alpha(#3584e4, 0.4)); }"
            b".nexa-float-bar { background-color: @view_bg_color; border-radius: 999px; "
            b"padding: 6px; box-shadow: 0 4px 18px alpha(black, 0.22), 0 1px 3px alpha(black, 0.15); "
            b"border: 1px solid alpha(@borders, 0.6); }"
            b".nexa-float-bar entry.nexa-input { background: none; box-shadow: none; border: none; }"
            b".nexa-float-bar entry.nexa-input:focus, .nexa-float-bar entry.nexa-input:focus-within, "
            b".nexa-float-bar entry.nexa-input text, .nexa-float-bar entry.nexa-input text:focus { "
            b"box-shadow: none; outline: none; border: none; background: none; }"
            b".nexa-float-bar entry.nexa-input image.entry_icon { color: inherit; }"
            b".nexa-mic-active { background-color: #e01b24; color: #ffffff; }"
            b"window.nexa-quick-pill-window { background: none; box-shadow: none; } "
            b"window.nexa-quick-pill-window decoration { background: none; box-shadow: none; border-radius: 0; } "
            b".nexa-quick-pill { min-width: 460px; border-radius: 999px; box-shadow: 0 8px 28px alpha(#000000, 0.45); } "
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry_box.add_css_class("nexa-float-bar")
        entry_box.set_margin_start(24)
        entry_box.set_margin_end(24)
        entry_box.set_margin_bottom(18)
        entry_box.set_margin_top(8)
        entry_box.set_halign(Gtk.Align.CENTER)
        entry_box.set_size_request(560, -1)

        self.entry = Gtk.Entry()
        self.entry.add_css_class("nexa-input")
        self.entry.set_hexpand(True)
        self.entry.set_placeholder_text("Type here a message...")
        self.entry.connect("activate", self.on_send)

        self.mic_button = Gtk.Button(icon_name="audio-input-microphone-symbolic")
        self.mic_button.add_css_class("circular")
        self.mic_button.set_tooltip_text("Voice input")
        self.mic_button.connect("clicked", self.on_mic_clicked)

        send_button = Gtk.Button(icon_name="mail-send-symbolic")
        send_button.add_css_class("suggested-action")
        send_button.add_css_class("circular")
        send_button.set_tooltip_text("Send")
        send_button.connect("clicked", self.on_send)

        entry_box.append(self.mic_button)
        entry_box.append(self.entry)
        entry_box.append(send_button)
        return entry_box

    # --- Chat behaviour ------------------------------------------------------------
    def _append_message(self, text, is_user=True):
        bubble = Gtk.Label(label=text)
        bubble.set_wrap(True)
        bubble.set_xalign(0)
        bubble.add_css_class("nexa-user-bubble" if is_user else "nexa-bot-bubble")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)
        bubble.set_margin_start(10)
        bubble.set_margin_end(10)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.END if is_user else Gtk.Align.START)

        if not is_user:
            icon = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
            icon.set_pixel_size(24)
            icon.set_valign(Gtk.Align.CENTER)
            row.append(icon)

        row.append(bubble)
        row.set_opacity(0)
        self.chat_box.append(row)
        self._fade_in_row(row)

        GLib.idle_add(self._scroll_to_bottom)

    def _fade_in_row(self, row):
        anim = Adw.TimedAnimation.new(
            row, 0, 1, 350,
            Adw.CallbackAnimationTarget.new(lambda v: row.set_opacity(v)),
        )
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.play()
        if not hasattr(self, "_row_anims"):
            self._row_anims = []
        self._row_anims.append(anim)

    def _append_weather_card(self, data):
        """Rich weather card (icon + condition + temp + humidity) shown
        instead of a plain text bubble, when CommandEngine.last_card_data
        was set by handle_weather(). Matches the same avatar+row layout as
        a normal bot message."""
        icon_key = data.get('icon', 'sunny')
        icon_path = f"/app/share/nexa/weather-icons/{icon_key}.svg"
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "weather-icons", f"{icon_key}.svg")

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        card.add_css_class("nexa-weather-card")

        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if icon_theme.has_icon(icon_key):
            icon_widget = Gtk.Image.new_from_icon_name(icon_key)
            icon_widget.set_pixel_size(64)
            icon_widget.set_valign(Gtk.Align.CENTER)
            card.append(icon_widget)
        elif os.path.exists(icon_path):
            icon_picture = Gtk.Picture.new_for_filename(icon_path)
            icon_picture.set_size_request(64, 64)
            icon_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            icon_picture.set_valign(Gtk.Align.CENTER)
            card.append(icon_picture)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)

        location = data.get("location")
        if location:
            location_label = Gtk.Label(label=str(location).upper(), xalign=0)
            location_label.add_css_class("nexa-weather-location")
            text_box.append(location_label)

        temp_label = Gtk.Label(label=f"{data.get('temp_c', '?')}°C", xalign=0)
        temp_label.add_css_class("nexa-weather-temp")
        text_box.append(temp_label)

        condition_label = Gtk.Label(label=str(data.get("condition", "")).strip().capitalize(), xalign=0)
        condition_label.add_css_class("nexa-weather-condition")
        text_box.append(condition_label)

        spacer = Gtk.Box()
        spacer.set_size_request(-1, 8)
        text_box.append(spacer)

        humidity_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        humidity_pill.add_css_class("nexa-weather-humidity-pill")
        humidity_pill.set_halign(Gtk.Align.START)
        humidity_icon = Gtk.Image.new_from_icon_name("weather-showers-symbolic")
        humidity_icon.set_pixel_size(12)
        humidity_icon.add_css_class("nexa-weather-humidity")
        humidity_label = Gtk.Label(label=f"{data.get('humidity', '?')}% humidity", xalign=0)
        humidity_label.add_css_class("nexa-weather-humidity")
        humidity_pill.append(humidity_icon)
        humidity_pill.append(humidity_label)
        text_box.append(humidity_pill)

        card.append(text_box)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.START)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        avatar = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
        avatar.set_pixel_size(24)
        avatar.set_valign(Gtk.Align.START)
        row.append(avatar)
        row.append(card)

        row.set_opacity(0)
        self.chat_box.append(row)
        self._fade_in_row(row)
        GLib.idle_add(self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        adjustment = self.scrolled.get_vadjustment()
        target = adjustment.get_upper() - adjustment.get_page_size()

        animation = Adw.TimedAnimation.new(
            self.scrolled,
            adjustment.get_value(),
            target,
            250,
            Adw.CallbackAnimationTarget.new(lambda value: adjustment.set_value(value)),
        )
        animation.play()
        return False

    def on_send(self, _widget):
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")

        if self.content_stack.get_visible_child_name() == "hero":
            self.content_stack.set_visible_child_name("chat")

        self._append_message(text, is_user=True)

        spinner_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_row.set_halign(Gtk.Align.START)
        spinner_row.set_margin_top(4)
        spinner_row.set_margin_bottom(4)

        avatar = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
        avatar.set_pixel_size(24)
        avatar.set_valign(Gtk.Align.CENTER)
        spinner_row.append(avatar)

        spinner = Gtk.Spinner(spinning=True)
        spinner.set_valign(Gtk.Align.CENTER)
        spinner_row.append(spinner)

        thinking_label = Gtk.Label(label="Thinking...")
        thinking_label.add_css_class("dim-label")
        thinking_label.set_valign(Gtk.Align.CENTER)
        spinner_row.append(thinking_label)

        self.chat_box.append(spinner_row)
        GLib.idle_add(self._scroll_to_bottom)
        GLib.timeout_add_seconds(3, self._maybe_play_working, spinner_row)

        start_time = time.monotonic()
        threading.Thread(target=self._process_query, args=(text, spinner_row, start_time), daemon=True).start()

    def _maybe_play_working(self, spinner_row):
        """Fires 3s after sending. Only plays the 'still thinking' sound if
        the response genuinely hasn't come back yet (spinner_row still attached)."""
        if spinner_row.get_parent() is not None:
            self.sounds.play_working()
        return False  # one-shot timer, don't repeat

    def _process_query(self, text, spinner_row, start_time):
        response = self.engine.parse(text)
        self.voice.speak(response)
        elapsed = time.monotonic() - start_time

        def finish():
            self.chat_box.remove(spinner_row)
            card_data = self.engine.last_card_data
            self.engine.last_card_data = None
            if card_data and card_data.get("type") == "weather":
                self._append_weather_card(card_data)
            else:
                self._append_message(response, is_user=False)
            if elapsed >= LONG_THINKING_SECONDS:
                self.sounds.play_success_long()
            return False

        GLib.idle_add(finish)

    def on_reset_conversation(self, _button=None):
        """Clears the chat history and drops back to the empty hero state."""
        child = self.chat_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.chat_box.remove(child)
            child = next_child

        self.engine.pending_power_action = None
        self.content_stack.set_visible_child_name("hero")
        self.hero_greeting.set_label(self.engine.get_initial_greeting())

    # --- Voice input ----------------------------------------------------------------
    def on_mic_clicked(self, _button):
        if not self.stt.is_recording():
            if self._is_speaking:
                self.voice.stop_audio()  # barge-in: clicking the mic interrupts her
            self.wake_engine.stop()  # avoid two mic pipelines fighting for CPU at once
            self.sounds.play_listening()
        self.stt.toggle()

    def _on_voice_state_change(self, state):
        def apply():
            self.mic_button.remove_css_class("destructive-action")
            self.mic_button.remove_css_class("suggested-action")
            self.mic_button.set_sensitive(True)
            if state == "recording":
                self.mic_button.set_icon_name("media-playback-stop-symbolic")
                self.mic_button.add_css_class("destructive-action")
                self.mic_button.set_tooltip_text("Listening... click to stop")
            elif state == "transcribing":
                self.mic_button.set_icon_name("content-loading-symbolic")
                self.mic_button.set_sensitive(False)
                self.mic_button.set_tooltip_text("Transcribing...")
            else:
                self.mic_button.set_icon_name("audio-input-microphone-symbolic")
                self.mic_button.set_tooltip_text("Voice input")
                # Don't resume wake-word listening immediately -- if a voice
                # response is about to play (the usual case right after a
                # voice command), starting the pipeline now and stopping it
                # again a split-second later when speech begins is exactly
                # the kind of rapid start/stop race that can leak through
                # and pick up her own voice. Route through the same
                # cooldown _on_speech_end uses; by the time it fires,
                # _is_speaking will correctly reflect whether she's talking.
                GLib.timeout_add(WAKE_RESUME_COOLDOWN_MS, self._delayed_resume_wake_engine)
            return False
        GLib.idle_add(apply)

    def _maybe_resume_wake_engine(self):
        """Single gatekeeper for restarting wake-word listening. Only
        resumes if the feature is actually on, nothing else is currently
        using the mic, and -- critically -- Nexa isn't mid-speech, so she
        can't hear and respond to her own voice (which otherwise loops
        forever with no user needed to keep it going)."""
        if self.wakeword_enabled and not self.stt.is_recording() and not self._is_speaking:
            self.wake_engine.start()

    def _on_speech_start(self):
        def apply():
            self._is_speaking = True
            self.wake_engine.stop()
            return False
        GLib.idle_add(apply)

    def _on_speech_end(self):
        def apply():
            self._is_speaking = False
            GLib.timeout_add(WAKE_RESUME_COOLDOWN_MS, self._delayed_resume_wake_engine)
            return False
        GLib.idle_add(apply)

    def _delayed_resume_wake_engine(self):
        self._maybe_resume_wake_engine()
        return False  # one-shot timer, don't repeat

    def _on_voice_result(self, text):
        def apply():
            self.entry.set_text(text)
            self.on_send(self.entry)
            return False
        GLib.idle_add(apply)

    def _on_voice_partial_result(self, text):
        """Live preview while still recording -- purely cosmetic, doesn't send."""
        def apply():
            if self.stt.is_recording():  # ignore a stale partial that lands after stop
                self.entry.set_text(text)
                self.entry.set_position(-1)
            return False
        GLib.idle_add(apply)

    def _on_voice_error(self, message):
        def apply():
            if self.content_stack.get_visible_child_name() == "hero":
                self.content_stack.set_visible_child_name("chat")
            self._append_message(message, is_user=False)
            self.sounds.play_error()
            return False
        GLib.idle_add(apply)

    # --- Wake word --------------------------------------------------------------------
    def _on_wake_word_detected(self):
        def apply():
            self.wake_engine.stop()
            self.set_visible(True)
            self.present()
            self.sounds.play_listening()
            if self.content_stack.get_visible_child_name() == "hero":
                self.content_stack.set_visible_child_name("chat")
            self.stt.start_recording()
            return False
        GLib.idle_add(apply)

    def _on_wake_word_error(self, message):
        def apply():
            self.set_visible(True)
            self.present()
            if self.content_stack.get_visible_child_name() == "hero":
                self.content_stack.set_visible_child_name("chat")
            self._append_message(message, is_user=False)
            self.sounds.play_error()
            return False
        GLib.idle_add(apply)

    # --- Connect Nexa (external app command registration) -----------------------------
    def _lookup_app_icon_widget(self, app_id, size=56):
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_name = app_id if icon_theme.has_icon(app_id) else "application-x-executable"
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(size)
        return img

    def _on_connect_nexa_request(self, app_id, app_name, commands, decide):
        """Fired from CommandRegistryService when an app calls RegisterApp().
        Shows a custom Allow/Cancel consent card (matches the "Connect Nexa"
        design) before `decide` completes the D-Bus call."""
        def show():
            dialog = Adw.Dialog(content_width=380, can_close=True)

            decided = {"done": False}
            def decide_once(allowed):
                if decided["done"]:
                    return
                decided["done"] = True
                decide(allowed)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            outer.set_margin_top(16)
            outer.set_margin_bottom(28)
            outer.set_margin_start(20)
            outer.set_margin_end(20)

            # top-left close (X)
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            close_btn = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START)
            close_btn.add_css_class("flat")
            close_btn.add_css_class("circular")
            close_btn.connect("clicked", lambda _b: (decide_once(False), dialog.close()))
            top_row.append(close_btn)
            outer.append(top_row)

            # app icon  <—connect—>  Nexa icon
            icons_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24,
                                 halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            icons_row.set_margin_top(8)
            icons_row.set_margin_bottom(8)

            app_icon_frame = Gtk.Frame()
            app_icon_frame.add_css_class("card")
            app_icon_frame.set_size_request(84, 84)
            app_icon_box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            app_icon_box.append(self._lookup_app_icon_widget(app_id))
            app_icon_frame.set_child(app_icon_box)
            icons_row.append(app_icon_frame)

            nexa_icon = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
            nexa_icon.set_pixel_size(84)
            icons_row.append(nexa_icon)
            outer.append(icons_row)

            count = len(commands)
            label = Gtk.Label(
                label=f"“{app_name or app_id}” wants to connect to Nexa and add "
                      f"{count} voice command{'s' if count != 1 else ''}.",
                wrap=True, justify=Gtk.Justification.CENTER,
            )
            label.add_css_class("dim-label")
            outer.append(label)

            allow_btn = Gtk.Button(label="Allow")
            allow_btn.add_css_class("suggested-action")
            allow_btn.add_css_class("pill")
            allow_btn.set_size_request(220, 44)
            allow_btn.set_halign(Gtk.Align.CENTER)
            allow_btn.connect("clicked", lambda _b: (decide_once(True), dialog.close()))
            outer.append(allow_btn)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.add_css_class("flat")
            cancel_btn.set_halign(Gtk.Align.CENTER)
            cancel_btn.connect("clicked", lambda _b: (decide_once(False), dialog.close()))
            outer.append(cancel_btn)

            dialog.set_child(outer)
            dialog.connect("close-attempt", lambda _d: decide_once(False))
            # Bring Nexa to the foreground first -- a dialog presented on a
            # hidden/tray-only parent window is invisible to the user.
            self.set_visible(True)
            self.present()
            dialog.present(self)
            return False
        GLib.idle_add(show)

    def _refresh_connected_apps_group(self):
        if getattr(self, "_connected_apps_listbox", None) is not None:
            self._populate_connected_apps_listbox(self._connected_apps_listbox)
        if getattr(self, "_connected_apps_row", None) is not None:
            self._connected_apps_row.set_subtitle(self._connected_apps_subtitle())

    def _connected_apps_subtitle(self):
        n = len(nexa_external_commands.list_apps())
        if n == 0:
            return "No apps connected"
        return f"{n} app{'s' if n != 1 else ''} connected"

    def _populate_connected_apps_listbox(self, listbox):
        child = listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            listbox.remove(child)
            child = nxt
        apps = nexa_external_commands.list_apps()
        if not apps:
            placeholder = Adw.ActionRow(title="No apps connected", subtitle="Apps that connect via \u201cConnect Nexa\u201d appear here")
            placeholder.set_sensitive(False)
            listbox.append(placeholder)
            return
        for app_id, app_name in apps.items():
            row = Adw.ActionRow(title=app_name, subtitle=app_id)
            row.add_prefix(self._lookup_app_icon_widget(app_id, size=32))
            commands_btn = Gtk.Button(icon_name="view-list-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Show Commands")
            commands_btn.add_css_class("flat")
            commands_btn.connect("clicked", lambda _b, aid=app_id, aname=app_name: self._open_app_commands_dialog(aid, aname))
            row.add_suffix(commands_btn)
            disconnect_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Disconnect")
            disconnect_btn.add_css_class("flat")
            disconnect_btn.connect("clicked", lambda _b, aid=app_id: self._disconnect_app(aid, listbox))
            row.add_suffix(disconnect_btn)
            listbox.append(row)

    def _open_app_commands_dialog(self, app_id, app_name):
        commands = nexa_external_commands.list_commands(app_id)
        dialog = Adw.Dialog(content_width=440, content_height=420, title=f"{app_name} Commands")
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        listbox.set_margin_top(12)
        listbox.set_margin_bottom(28)
        listbox.set_margin_start(24)
        listbox.set_margin_end(24)

        if not commands:
            placeholder = Adw.ActionRow(title="No commands registered")
            placeholder.set_sensitive(False)
            listbox.append(placeholder)
        for cmd in commands:
            row = Adw.ActionRow(
                title=f"\u201c{cmd.get('trigger', '')}\u201d",
                subtitle=cmd.get("description") or "")
            row.add_prefix(Gtk.Image(icon_name="audio-input-microphone-symbolic", pixel_size=18))
            listbox.append(row)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(listbox)
        toolbar_view.set_content(scroller)
        dialog.set_child(toolbar_view)
        dialog.present(self)

    def _disconnect_app(self, app_id, listbox):
        nexa_external_commands.unregister_app(app_id)
        self._populate_connected_apps_listbox(listbox)
        self._refresh_connected_apps_group()

    def _open_connected_apps_dialog(self, _row):
        dialog = Adw.Dialog(content_width=560, content_height=480, title="Connected Apps")
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        listbox.set_margin_top(12)
        listbox.set_margin_bottom(32)
        listbox.set_margin_start(28)
        listbox.set_margin_end(28)
        self._connected_apps_listbox = listbox
        self._populate_connected_apps_listbox(listbox)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(listbox)
        toolbar_view.set_content(scroller)
        dialog.set_child(toolbar_view)
        dialog.present(self)

    # --- Preferences ------------------------------------------------------------------
    def on_open_preferences(self, _button):
        self.nav_view.push(self._build_preferences_page())

    def on_open_studio(self, _row):
        """Closes Nexa's main window and opens Nexa Studio in its place.
        Coming back from Studio re-presents this same window."""
        self.set_visible(False)
        studio_win = NexaStudioWindow(
            application=self.get_application(),
            on_close_return_home=lambda: (self.set_visible(True), self.present()),
            engine=self.engine,
            voice=self.voice,
        )
        studio_win.present()

    def _build_preferences_page(self):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        prefs_page = Adw.PreferencesPage()

        # --- Profile -------------------------------------------------------------
        profile_group = Adw.PreferencesGroup(
            title="Profile",
            description="Used to personalize how Nexa greets you and answers weather questions.",
        )

        name_row = Adw.EntryRow(title="Name")
        name_row.set_text(self.user_name)
        name_row.set_show_apply_button(True)
        name_row.connect("apply", lambda row: self._save_name(row))
        profile_group.add(name_row)

        location_row = Adw.ComboRow(title="Location", subtitle="Used for weather requests")
        location_row.set_model(Gtk.StringList.new(LOCATIONS))
        try:
            location_row.set_selected(LOCATIONS.index(self.user_location))
        except ValueError:
            location_row.set_selected(0)
        location_row.connect("notify::selected", lambda row, _p: self._save_location(row))
        profile_group.add(location_row)

        prefs_page.add(profile_group)

        # --- Voice & Listening -----------------------------------------------------
        voice_group = Adw.PreferencesGroup(title="Voice & Listening")

        voice_row = Adw.SwitchRow(title="Speak Replies", subtitle="Nexa reads her answers out loud instead of just showing text")
        voice_row.set_active(self.voice_enabled)
        voice_row.connect("notify::active", self._on_voice_toggled)
        voice_group.add(voice_row)

        gender_row = Adw.ComboRow(title="Voice", subtitle="Male or female speaking voice")
        gender_row.set_model(Gtk.StringList.new(["Female", "Male"]))
        gender_row.set_selected(0 if self.voice_gender == "female" else 1)
        gender_row.connect("notify::selected", self._on_voice_gender_changed)
        voice_group.add(gender_row)

        listening_row = Adw.ComboRow(title="Listening Time", subtitle="How long Nexa waits after you stop talking before responding")
        listening_row.set_model(Gtk.StringList.new(VOICE_INPUT_MODES))
        listening_row.set_selected(VOICE_INPUT_KEYS.index(self.voice_input_mode))
        listening_row.connect("notify::selected", self._on_voice_input_mode_changed)
        voice_group.add(listening_row)

        wakeword_row = Adw.SwitchRow(
            title="Wake Word",
            subtitle='Say "Hey Nexa" to start listening \u2014 works even while Nexa is running in the background',
        )
        wakeword_row.set_active(self.wakeword_enabled)
        wakeword_row.connect("notify::active", self._on_wakeword_toggled)
        voice_group.add(wakeword_row)

        sensitivity_row = Adw.ComboRow(
            title="Wake Word Sensitivity",
            subtitle="How easily Nexa reacts to \u201cHey Nexa.\u201d Higher responds faster but may trigger on background noise",
        )
        sensitivity_row.set_model(Gtk.StringList.new(["Low", "Medium", "High"]))
        sensitivity_row.set_selected(["low", "medium", "high"].index(self.wakeword_sensitivity))
        sensitivity_row.connect("notify::selected", self._on_wakeword_sensitivity_changed)
        voice_group.add(sensitivity_row)

        adaptive_row = Adw.SwitchRow(
            title="Adaptive Learning",
            subtitle="Nexa quietly learns from how you talk to her, so \u201cHey Nexa\u201d detection and command recognition "
                      "get more accurate the more you use her",
        )
        adaptive_row.set_active(self.adaptive_learning_enabled)
        adaptive_row.connect("notify::active", self._on_adaptive_learning_toggled)
        voice_group.add(adaptive_row)

        prefs_page.add(voice_group)

        # --- Background & Access -------------------------------------------------
        access_group = Adw.PreferencesGroup(
            title="Background & Access",
            description="How Nexa keeps running and how you can bring her back quickly.",
        )

        startup_row = Adw.SwitchRow(
            title="Launch at Startup",
            subtitle="Start Nexa automatically when you log in",
        )
        startup_row.set_active(self.launch_at_startup)
        startup_row.connect("notify::active", self._on_launch_at_startup_toggled)
        access_group.add(startup_row)

        background_row = Adw.SwitchRow(
            title="Run in Background",
            subtitle="Keep Nexa running when you close the window, instead of quitting, so she reopens instantly",
        )
        background_row.set_active(self.run_in_background)
        background_row.connect("notify::active", self._on_background_toggled)
        access_group.add(background_row)

        tray_row = Adw.SwitchRow(
            title="System Tray Icon",
            subtitle="Adds a tray icon with Show Nexa, Quick Command, and Quit. Needs a tray icon "
                      "extension turned on in GNOME Extensions first (for example, Ubuntu AppIndicators) \u2014 "
                      "GNOME doesn't show tray icons on its own.",
        )
        tray_row.set_active(self.show_tray_icon)
        tray_row.connect("notify::active", self._on_tray_icon_toggled)
        access_group.add(tray_row)

        quick_command_hotkey_row = Adw.SwitchRow(
            title="Quick Command Hotkey",
            subtitle="Press a keyboard shortcut anytime, even with Nexa in the background, to open a small "
                      "command box you can type or speak into. The system will ask you to confirm the key "
                      "combo the first time you turn this on.",
        )
        quick_command_hotkey_row.set_active(self.quick_command_hotkey_enabled)
        quick_command_hotkey_row.connect("notify::active", self._on_quick_command_hotkey_toggled)
        access_group.add(quick_command_hotkey_row)

        prefs_page.add(access_group)

        # --- Media -----------------------------------------------------------------
        media_group = Adw.PreferencesGroup(title="Media")
        audio_row = Adw.ActionRow(title="Favorite Music", subtitle="The song Nexa plays when you say \"play my music\"")
        pick_button = Gtk.Button(label="Choose File", valign=Gtk.Align.CENTER)
        pick_button.connect("clicked", self.on_pick_music_folder)
        audio_row.add_suffix(pick_button)
        media_group.add(audio_row)
        prefs_page.add(media_group)

        # --- Custom Commands ---------------------------------------------------------
        studio_group = Adw.PreferencesGroup(title="Custom Commands")
        studio_row = Adw.ActionRow(
            title="Open Nexa Studio",
            subtitle="Teach Nexa new phrases to respond to, with a reply to say or a command to run",
        )
        studio_row.set_activatable(True)
        studio_row.connect("activated", self.on_open_studio)
        studio_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        studio_group.add(studio_row)
        prefs_page.add(studio_group)

        connected_apps_group = Adw.PreferencesGroup(title="Connect Nexa")
        connected_apps_row = Adw.ActionRow(
            title="Connected Apps",
            subtitle=self._connected_apps_subtitle(),
        )
        connected_apps_row.set_activatable(True)
        connected_apps_row.connect("activated", self._open_connected_apps_dialog)
        connected_apps_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._connected_apps_row = connected_apps_row
        connected_apps_group.add(connected_apps_row)
        prefs_page.add(connected_apps_group)

        # --- Privacy & Training Data -------------------------------------------------
        training_group = Adw.PreferencesGroup(
            title="Privacy & Training Data",
            description="Both switches below are off by default. Nothing ever leaves this computer "
                        "automatically \u2014 data only goes anywhere if you export and share it yourself.",
        )

        wakeword_data_row = Adw.SwitchRow(
            title="Collect \u201cHey Nexa\u201d Samples",
            subtitle="Saves a short audio clip each time the wake word is heard, to help improve wake word detection later",
        )
        wakeword_data_row.set_active(self.collect_wakeword_data)
        wakeword_data_row.connect("notify::active", self._on_collect_wakeword_toggled)
        training_group.add(wakeword_data_row)

        stt_data_row = Adw.SwitchRow(
            title="Collect Speech Recognition Samples",
            subtitle="Saves your spoken commands and their transcripts, to help improve speech recognition later",
        )
        stt_data_row.set_active(self.collect_stt_data)
        stt_data_row.connect("notify::active", self._on_collect_stt_toggled)
        training_group.add(stt_data_row)

        wc, sc = self.training_data.counts()
        self._training_export_row = Adw.ActionRow(
            title="Export Training Data",
            subtitle=f"{wc} wake word clips and {sc} speech samples saved on this device",
        )
        export_button = Gtk.Button(label="Export", valign=Gtk.Align.CENTER)
        export_button.connect("clicked", self.on_export_training_data)
        self._training_export_row.add_suffix(export_button)
        clear_button = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", self.on_clear_training_data)
        self._training_export_row.add_suffix(clear_button)
        training_group.add(self._training_export_row)
        prefs_page.add(training_group)

        # --- About -------------------------------------------------------------------
        about_group = Adw.PreferencesGroup(title="About")
        about_row = Adw.ActionRow(title="About Nexa Assistant", subtitle=f"Version {NEXA_VERSION}")
        about_row.set_activatable(True)
        about_row.connect("activated", self.on_show_about)
        about_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        about_group.add(about_row)
        prefs_page.add(about_group)

        toolbar_view.set_content(prefs_page)

        toast_overlay = Adw.ToastOverlay()
        toast_overlay.set_child(toolbar_view)
        self._prefs_toast_overlay = toast_overlay

        return Adw.NavigationPage(title="Preferences", child=toast_overlay)

    def _show_prefs_toast(self, message):
        """For feedback from actions taken IN Preferences (export/clear/toggle
        errors) -- shown as a toast inside the settings page itself, not
        injected into the actual Nexa conversation."""
        if self._prefs_toast_overlay is not None:
            self._prefs_toast_overlay.add_toast(Adw.Toast(title=message))

    def _on_stt_sample(self, pcm_bytes, text):
        """Fires after every successful final transcription. Feeds two
        independent, separately-gated systems: the opt-in exportable audio
        collection (training_data.py) and the always-fully-local vocabulary
        adaptation (adaptive_learning.py) -- the prompt is refreshed
        immediately so newly learned words take effect this same session."""
        self.training_data.save_stt_sample(pcm_bytes, text)
        self.adaptive.record_transcript(text)
        self.stt.set_vocabulary_prompt(self.adaptive.build_prompt(self.engine.get_vocabulary_prompt()))

    def _on_wake_score(self, score):
        """Fires alongside every real wake-word trigger with its confidence
        score. Feeds the self-tuning sensitivity offset, reapplied
        immediately (see WakeWordEngine.apply_adaptive_offset)."""
        self.adaptive.record_wake_trigger(score)
        self.wake_engine.apply_adaptive_offset(self.adaptive.get_threshold_offset())

    def _on_collect_wakeword_toggled(self, switch_row, _param):
        self.collect_wakeword_data = switch_row.get_active()
        self.training_data.set_collect_wakeword(self.collect_wakeword_data)
        write_config("collect_wakeword_data", "1" if self.collect_wakeword_data else "0")

    def _on_collect_stt_toggled(self, switch_row, _param):
        self.collect_stt_data = switch_row.get_active()
        self.training_data.set_collect_stt(self.collect_stt_data)
        write_config("collect_stt_data", "1" if self.collect_stt_data else "0")

    def _refresh_training_export_subtitle(self):
        if self._training_export_row is not None:
            wc, sc = self.training_data.counts()
            self._training_export_row.set_subtitle(f"{wc} wake word samples, {sc} speech samples collected")

    def on_export_training_data(self, _button):
        if not self.training_data.has_any_data():
            self._show_prefs_toast("There's no training data collected yet \u2014 turn on the toggles above and use Nexa a bit first.")
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Export Training Data")
        dialog.set_initial_name("nexa-training-data.zip")
        dialog.save(self, None, self._on_export_dialog_response)

    def _on_export_dialog_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file:
            return
        path = file.get_path()
        try:
            self.training_data.export_to_zip(path)
            self._show_prefs_toast(f"Exported to {path}. Thanks for helping improve Nexa!")
        except Exception as e:
            self._show_prefs_toast(f"Couldn't export training data: {e}")

    def on_clear_training_data(self, _button):
        self.training_data.clear_all()
        self._refresh_training_export_subtitle()
        self._show_prefs_toast("Cleared all collected training data.")

    def on_show_about(self, _row):
        about = Adw.AboutDialog(
            application_name="Nexa Assistant",
            application_icon="org.nexa.Assistant",
            version=NEXA_VERSION,
            developer_name="Shiro",
            developers=["Shiro"],
            website=NEXA_GITHUB_URL,
            issue_url=NEXA_GITHUB_URL,
            copyright="\u00a9 2026 Shiro",
            comments="A local-first voice assistant for the GNOME desktop \u2014 wake word, "
                     "speech recognition, and voice replies all running on-device.",
        )
        about.present(self)

    def _save_name(self, name_row):
        self.user_name = name_row.get_text().strip() or self.user_name
        write_config("username", self.user_name)
        self.hero_greeting.set_label(self.engine.get_initial_greeting())

    def _save_location(self, location_row):
        idx = location_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION:
            self.user_location = LOCATIONS[idx]
            write_config("user_location", self.user_location)

    def _on_voice_toggled(self, switch_row, _param):
        self.voice_enabled = switch_row.get_active()
        self.voice.set_enabled(self.voice_enabled)
        write_config("voice_enabled", "1" if self.voice_enabled else "0")

    def _on_voice_gender_changed(self, combo_row, _param):
        idx = combo_row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        key = "female" if idx == 0 else "male"
        if self.voice.set_voice(key):
            self.voice_gender = key
            write_config("voice_gender", key)
        else:
            # Model not downloaded yet: let them know and snap the row back.
            self._show_prefs_toast(f"I don't have the {key} voice installed yet \u2014 check the setup docs to download it.")
            combo_row.set_selected(0 if self.voice_gender == "female" else 1)

    def _on_voice_input_mode_changed(self, combo_row, _param):
        idx = combo_row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        self.voice_input_mode = VOICE_INPUT_KEYS[idx]
        self.stt.set_timeout_mode(self.voice_input_mode)
        write_config("voice_input_mode", self.voice_input_mode)

    def _on_wakeword_toggled(self, switch_row, _param):
        enabled = switch_row.get_active()
        if enabled and not self.wake_engine.is_available():
            self._show_prefs_toast("The wake word models aren't installed yet \u2014 check the setup docs to download them.")
            switch_row.set_active(False)
            return
        self.wakeword_enabled = enabled
        write_config("wakeword_enabled", "1" if enabled else "0")
        if enabled:
            self._maybe_resume_wake_engine()  # respects mid-recording/mid-speech state
        else:
            self.wake_engine.stop()

    def _on_wakeword_sensitivity_changed(self, combo_row, _param):
        idx = combo_row.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        self.wakeword_sensitivity = ["low", "medium", "high"][idx]
        self.wake_engine.set_threshold(self.wakeword_sensitivity)
        self.wake_engine.apply_adaptive_offset(self.adaptive.get_threshold_offset())
        write_config("wakeword_sensitivity", self.wakeword_sensitivity)

    def _on_adaptive_learning_toggled(self, switch_row, _param):
        self.adaptive_learning_enabled = switch_row.get_active()
        self.adaptive.set_enabled(self.adaptive_learning_enabled)
        write_config("adaptive_learning_enabled", "1" if self.adaptive_learning_enabled else "0")

    def _on_background_toggled(self, switch_row, _param):
        self.run_in_background = switch_row.get_active()
        write_config("run_in_background", "1" if self.run_in_background else "0")

    def _on_tray_icon_toggled(self, switch_row, _param):
        self.show_tray_icon = switch_row.get_active()
        write_config("show_tray_icon", "1" if self.show_tray_icon else "0")
        app = self.get_application()
        if self.show_tray_icon:
            app.start_tray()
        elif app.tray is not None:
            app.tray.unregister()
            app.tray = None

    def _on_launch_at_startup_toggled(self, switch_row, _param):
        self.launch_at_startup = switch_row.get_active()
        if self.launch_at_startup:
            enable_autostart()
        else:
            disable_autostart()

    def _on_quick_command_hotkey_toggled(self, switch_row, _param):
        self.quick_command_hotkey_enabled = switch_row.get_active()
        write_config("quick_command_hotkey_enabled", "1" if self.quick_command_hotkey_enabled else "0")
        app = self.get_application()
        if app.global_shortcut is None:
            app.start_global_shortcut()
        elif self.quick_command_hotkey_enabled:
            app.global_shortcut.enable()
        else:
            app.global_shortcut.disable()

    def on_pick_music_folder(self, _button):
        dialog = Gtk.FileDialog()
        dialog.open(self, None, self._on_music_folder_chosen)

    def _on_music_folder_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.fav_music = path
                write_config("fav_music", path)
        except GLib.Error:
            pass

    # --- Background mode -------------------------------------------------------------
    def on_close_request(self, *_args):
        """If "Run in Background" is on, hide instead of quitting, so Nexa keeps
        running and a keyboard shortcut running `flatpak run org.nexa.Assistant`
        can bring the same window right back. If it's off, closing the window
        quits Nexa entirely, same as any normal app. Either way, the conversation
        resets so the next open starts fresh."""
        self.on_reset_conversation()
        if self.run_in_background:
            self.set_visible(False)
            return True  # stop the default close/destroy behavior
        self.get_application().quit()
        return True


class QuickCommandPill(Gtk.Window):
    """Small floating command bar opened from the tray icon's "Quick Command"
    item. Lets the user type or speak a command without opening the full
    window; on submit it hands the text to NexaWindow and auto-sends it."""

    def __init__(self, app_window):
        super().__init__(transient_for=app_window, modal=False)
        self.app_window = app_window
        self._stt_swapped = False
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("nexa-quick-pill-window")

        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill_box.add_css_class("nexa-float-bar")
        pill_box.add_css_class("nexa-quick-pill")
        pill_box.set_margin_top(16)
        pill_box.set_margin_bottom(16)
        pill_box.set_margin_start(16)
        pill_box.set_margin_end(16)

        self.mic_button = Gtk.Button()
        self.mic_button.set_icon_name("audio-input-microphone-symbolic")
        self.mic_button.add_css_class("circular")
        self.mic_button.add_css_class("flat")
        self.mic_button.set_tooltip_text("Voice input")
        self.mic_button.connect("clicked", self.on_mic_clicked)
        pill_box.append(self.mic_button)

        self.entry = Gtk.Entry()
        self.entry.add_css_class("nexa-input")
        self.entry.set_placeholder_text("Type a command...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.on_submit)
        pill_box.append(self.entry)

        send_button = Gtk.Button()
        send_button.set_icon_name("mail-send-symbolic")
        send_button.add_css_class("circular")
        send_button.add_css_class("suggested-action")
        send_button.connect("clicked", self.on_submit)
        pill_box.append(send_button)

        # Draggable: any drag on the handle moves the whole window.
        handle = Gtk.WindowHandle()
        handle.set_child(pill_box)
        self.set_child(handle)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)
        self.connect("close-request", self._on_close_request)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_close_request(self, *_args):
        self._restore_stt_callbacks(stop_if_recording=True)
        return False

    def on_submit(self, *_args):
        text = self.entry.get_text().strip()
        if not text:
            return
        self._restore_stt_callbacks(stop_if_recording=False)
        self.close()
        self.app_window.set_visible(True)
        self.app_window.present()
        self.app_window.entry.set_text(text)
        self.app_window.on_send(self.app_window.entry)

    # --- Voice input: temporarily borrow the shared STT engine's callbacks --------
    def on_mic_clicked(self, _button):
        stt = self.app_window.stt
        if not stt.is_recording():
            self._swap_stt_callbacks()
            if self.app_window._is_speaking:
                self.app_window.voice.stop_audio()
            self.app_window.wake_engine.stop()
            self.app_window.sounds.play_listening()
        stt.toggle()

    def _swap_stt_callbacks(self):
        stt = self.app_window.stt
        self._orig_on_result = stt.on_result
        self._orig_on_partial_result = stt.on_partial_result
        stt.on_result = self._pill_on_result
        stt.on_partial_result = self._pill_on_partial_result
        self._stt_swapped = True

    def _restore_stt_callbacks(self, stop_if_recording):
        stt = self.app_window.stt
        if stop_if_recording and stt.is_recording():
            stt.toggle()
        if self._stt_swapped:
            stt.on_result = self._orig_on_result
            stt.on_partial_result = self._orig_on_partial_result
            self._stt_swapped = False

    def _pill_on_partial_result(self, text):
        def apply():
            self.entry.set_text(text)
            return False
        GLib.idle_add(apply)

    def _pill_on_result(self, text):
        def apply():
            self.entry.set_text(text)
            self._restore_stt_callbacks(stop_if_recording=False)
            self.on_submit()
            return False
        GLib.idle_add(apply)


class NexaApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.nexa.Assistant",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.win = None
        self.tray = None
        self._quick_pill = None
        self._pending_quick_command = False
        self.global_shortcut = None

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_command_line(self, command_line):
        """Handles both the initial launch and any re-invocation (e.g. from
        a GNOME custom keyboard shortcut running
        `flatpak run org.nexa.Assistant --quick-command`), which GApplication
        forwards here instead of do_activate when the app is single-instance."""
        args = command_line.get_arguments()
        self._pending_quick_command = "--quick-command" in args[1:]
        self.activate()
        return 0

    def _tray_show_app(self):
        def apply():
            self.win.set_visible(True)
            self.win.present()
            return False
        GLib.idle_add(apply)

    def _tray_quick_command(self):
        def apply():
            # Reuse the existing pill if it's already open instead of stacking another.
            if self._quick_pill is not None:
                self._quick_pill.present()
                return False
            self._quick_pill = QuickCommandPill(self.win)
            self._quick_pill.connect("close-request", lambda *_a: self._clear_quick_pill())
            self._quick_pill.present()
            return False
        GLib.idle_add(apply)

    def _clear_quick_pill(self):
        self._quick_pill = None

    def _tray_quit(self):
        GLib.idle_add(self.quit)

    def start_tray(self):
        """Idempotent: safe to call from either the setup flow or normal
        activation, whichever creates the NexaWindow first."""
        if self.tray is None and read_config("show_tray_icon", "1") == "1":
            self.tray = TrayManager(
                on_show_app=self._tray_show_app,
                on_quick_command=self._tray_quick_command,
                on_quit=self._tray_quit,
            )

    def start_global_shortcut(self):
        """Idempotent: creates the manager once and enables it if the setting
        is on. Called at startup and whenever the settings toggle flips on."""
        if self.global_shortcut is None:
            self.global_shortcut = GlobalShortcutManager(on_activate=self._tray_quick_command)
        if read_config("quick_command_hotkey_enabled", "0") == "1":
            self.global_shortcut.enable()

    def do_activate(self):
        # Re-invocation while already running (e.g. a keyboard shortcut running
        # `flatpak run org.nexa.Assistant`, or `... --quick-command`) lands here
        # too, since GApplication is single-instance by default.
        if self.win is not None:
            if self._pending_quick_command:
                self._pending_quick_command = False
                self._tray_quick_command()
            else:
                self.win.set_visible(True)
                self.win.present()
            return

        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        ensure_config_dir()
        if read_config("setup_done", "") == "1":
            self.win = NexaWindow(application=self)
            self.start_tray()
            self.start_global_shortcut()
        else:
            self.win = NexaSetupWindow(application=self)

        # Keep the process alive even when the window is hidden, not closed.
        self.hold()
        if self._pending_quick_command and isinstance(self.win, NexaWindow):
            self._pending_quick_command = False
            self._tray_quick_command()
        else:
            self.win.present()


def main():
    app = NexaApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
