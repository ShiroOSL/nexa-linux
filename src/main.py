#!/usr/bin/env python3
"""Nexa Assistant - main application entry point.

Owns the onboarding wizard (NexaSetupWindow) and the main chat
interface (NexaWindow), and wires together CommandEngine,
VoiceManager and DBusManager.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Adw, GLib, Gio, Gdk, Pango, GdkPixbuf
 
import os
import sys
import shutil
import subprocess
import html
import re
import difflib
import threading
import time
import platform
import webbrowser
import urllib.parse

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
NEXA_VERSION = "1.1.0"
NEXA_GITHUB_URL = "https://github.com/ShiroOSL/nexa-linux"
NEXA_DEVELOPER_URL = "https://github.com/ShiroOSL"

# Piper TTS currently ships x86_64-only (see manifest / third_party notes) --
# the upstream aarch64 binary is broken, so voice replies aren't available
# on ARM64 yet. Used to gray out the TTS controls with an explanatory note
# instead of letting them silently fail.
TTS_SUPPORTED = platform.machine() in ("x86_64", "amd64")

# Folders "find a file" is allowed to search, keyed by the code stored in the
# search_folders config value. Order here is display order in Settings and
# in the fallback "search everywhere allowed" scan.
FILE_SEARCH_FOLDERS = {
    "home": ("Entire Home Folder", "$HOME"),
    "downloads": ("Downloads", "$HOME/Downloads"),
    "documents": ("Documents", "$HOME/Documents"),
    "desktop": ("Desktop", "$HOME/Desktop"),
    "pictures": ("Pictures", "$HOME/Pictures"),
    "music": ("Music", "$HOME/Music"),
    "videos": ("Videos", "$HOME/Videos"),
}

# Command categories a user can individually switch off from Settings >
# Privacy > Command Access, keyed by the same codes CommandEngine.parse()
# checks via _permission_gate() before dispatching to each category's
# handler. All enabled (nothing locked down) by default. Order here is
# display order in Settings. Keep in sync with the categories actually
# gated in commands.py's parse() -- codes must match exactly.
COMMAND_CATEGORIES = {
    "system_control": ("System Controls", "Power, lock screen, dark mode, volume, brightness, Wi-Fi, Bluetooth, airplane mode, night light."),
    "system_info": ("System Info", "Battery level, CPU/RAM usage, and system specs."),
    "media": ("Media & Music", "Playback controls and playing your favorite track."),
    "open_apps": ("Opening Apps", "Launching other applications by name."),
    "notifications": ("Notifications", "Reading your notification history."),
    "clipboard_notes": ("Clipboard & Notes", "Reading your clipboard and saved notes."),
}

# Extension -> generic themed icon name, used for the file-search result card.
# These are all standard icon names shipped with Adwaita/hicolor (part of the
# GNOME runtime), so no bundled assets are needed -- same approach as the
# weather card's condition -> icon-name mapping in commands.py.
_FILE_ICON_EXTENSIONS = {
    "image-x-generic": ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif"),
    "audio-x-generic": ("mp3", "wav", "flac", "ogg", "m4a", "opus"),
    "video-x-generic": ("mp4", "mkv", "webm", "mov", "avi", "m4v"),
    "x-office-document": ("pdf", "doc", "docx", "odt", "rtf"),
    "x-office-spreadsheet": ("xls", "xlsx", "csv", "ods"),
    "x-office-presentation": ("ppt", "pptx", "odp"),
    "package-x-generic": ("zip", "tar", "gz", "xz", "7z", "rar", "bz2"),
    "text-x-script": ("py", "js", "ts", "c", "cpp", "h", "java", "rs", "go", "sh", "json", "html", "css", "xml", "yaml", "yml"),
}
_FILE_EXTENSION_ICON_MAP = {
    ext: icon for icon, exts in _FILE_ICON_EXTENSIONS.items() for ext in exts
}


def _icon_for_file(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return _FILE_EXTENSION_ICON_MAP.get(ext, "text-x-generic")


# Ordered so longer/more-specific markers are tried before shorter ones that
# could partially match them (***bold italic*** before **bold** before
# *italic*) -- each tuple is (regex, Pango markup template using \1 for the
# captured inner text).
_MD_BOLD_ITALIC_RULES = (
    (re.compile(r"\*\*\*([^*]+)\*\*\*"), r"<b><i>\1</i></b>"),
    (re.compile(r"___([^_]+)___"), r"<b><i>\1</i></b>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<b>\1</b>"),
    (re.compile(r"__([^_]+)__"), r"<b>\1</b>"),
    (re.compile(r"\*([^*]+)\*"), r"<i>\1</i>"),
    (re.compile(r"_([^_]+)_"), r"<i>\1</i>"),
)
_MD_HEADER_LINE_RE = re.compile(r"^(#{1,6})\s*(.+)$", re.MULTILINE)
_MD_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_BARE_URL_RE = re.compile(r"(?<![\">])(https?://[^\s<]+)")


def markdown_to_pango(text):
    """Converts a practical subset of Markdown into Pango markup so a plain
    Gtk.Label can render it via set_markup() -- bold/italic as <b>/<i>,
    [text](url) and bare URLs as clickable <a href> (GtkLabel handles link
    clicks natively), and headers as a larger bold inline span, since Pango
    markup has no real block-level "heading" concept the way a document
    renderer would -- a heading here is just bigger+bold text on its own
    line, not a distinct structural element.

    Escapes '&'/'<'/'>' FIRST, before any of the above run, so literal text
    from the source (which may itself contain those characters) is never
    misinterpreted as markup -- only markup this function itself inserts
    afterward is real Pango markup. Link URLs are escaped again separately
    since they need '&' escaped for the href attribute specifically."""
    text = html.escape(text, quote=False)

    def _header_sub(m):
        level = len(m.group(1))
        size = {1: "xx-large", 2: "x-large", 3: "large"}.get(level, "large")
        return f'<span size="{size}" weight="bold">{m.group(2)}</span>'

    text = _MD_HEADER_LINE_RE.sub(_header_sub, text)

    def _link_sub(m):
        label, url = m.group(1), m.group(2)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    text = _MD_MARKDOWN_LINK_RE.sub(_link_sub, text)
    text = _MD_BARE_URL_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(1), quote=True)}">{m.group(1)}</a>', text
    )

    for pattern, replacement in _MD_BOLD_ITALIC_RULES:
        text = pattern.sub(replacement, text)

    return text


class EntrySpellChecker:
    """Real-time squiggly-underline spellcheck for a Gtk.Entry, backed by
    enchant/hunspell (via _get_spell_dict below). libenchant-2 plus every
    en_* hunspell dictionary (including en_US) already ship in
    org.gnome.Platform//50's base runtime -- confirmed directly inside the
    sandbox -- so the only thing that needed vendoring was the pure-Python
    `pyenchant` wheel itself (ctypes bindings, no compiled extension).
    Underlines recompute on every keystroke, which is cheap at
    chat-message length. Purely visual feedback while typing -- the
    actual fix-it-for-you correction happens on send, via
    autocorrect_text() below, since that's what's actually useful here:
    Nexa should silently fix "bluetoot" -> "bluetooth" before it ever
    reaches command parsing, not require a manual click to fix each word."""

    _WORD_RE = re.compile(r"[A-Za-z']+")

    def __init__(self, entry):
        self.entry = entry
        entry.connect("changed", self._on_changed)
        self._on_changed(entry)  # in case the entry already has text

    def _tokenize(self, text):
        for m in self._WORD_RE.finditer(text):
            word = m.group(0).strip("'")
            if len(word) >= 2:
                yield word, m.start(), m.end()

    def _on_changed(self, entry):
        d = _get_spell_dict()
        text = entry.get_text()
        attrs = Pango.AttrList()
        if d and text:
            for word, start_char, end_char in self._tokenize(text):
                try:
                    correct = d.check(word)
                except Exception:
                    continue
                if correct:
                    continue
                start_byte = len(text[:start_char].encode("utf-8"))
                end_byte = len(text[:end_char].encode("utf-8"))
                underline = Pango.attr_underline_new(Pango.Underline.ERROR)
                underline.start_index = start_byte
                underline.end_index = end_byte
                attrs.insert(underline)
                color = Pango.attr_underline_color_new(65535, 0, 0)
                color.start_index = start_byte
                color.end_index = end_byte
                attrs.insert(color)
        entry.set_attributes(attrs)


_spell_dict = None
_spell_dict_tried = False


def _get_spell_dict():
    global _spell_dict, _spell_dict_tried
    if not _spell_dict_tried:
        _spell_dict_tried = True
        try:
            import enchant
            _spell_dict = enchant.Dict("en_US")
        except Exception:
            _spell_dict = None
    return _spell_dict


_AUTOCORRECT_WORD_RE = re.compile(r"[A-Za-z']+")

_command_vocab_words = None


def _get_command_vocab_words():
    """Nexa's own recognized command vocabulary, as a flat set of individual
    words -- derived from CommandEngine.get_vocabulary_prompt() (the exact
    same text already used to bias Whisper's speech recognition toward her
    real commands) rather than a separate hand-maintained list, so this
    autocorrect step and that speech-bias prompt can never drift out of
    sync with each other. Built once and cached at module scope.

    "wifi" is added explicitly on top of the extracted words: the prompt
    spells it "Wi-Fi", and _AUTOCORRECT_WORD_RE ([A-Za-z']+) splits on the
    hyphen, so it would otherwise extract as two unrelated words ("wi",
    "fi") and "wifi" would never be recognized as a real vocab word --
    which is exactly what let hunspell "correct" the already-correct word
    "wifi" into "wife" below."""
    global _command_vocab_words
    if _command_vocab_words is None:
        try:
            prompt = CommandEngine.get_vocabulary_prompt()
        except Exception:
            prompt = ""
        words = _AUTOCORRECT_WORD_RE.findall(prompt)
        _command_vocab_words = {w.lower() for w in words if len(w) >= 4}
        _command_vocab_words.add("wifi")
    return _command_vocab_words


def autocorrect_text(text, protected_words=None):
    """Fixes obvious typos in a message before it's shown/sent to the
    command engine, so "turn on bluetoot" is corrected to "turn on
    bluetooth" and parsed correctly, instead of confusing the intent
    matcher and getting a "didn't understand" reply. Checks similarity
    against Nexa's own known command vocabulary first (via
    difflib.get_close_matches against _get_command_vocab_words(), derived
    from get_vocabulary_prompt() -- the same list already used to bias
    speech recognition), since general hunspell often doesn't even
    recognize domain words like "bluetooth" or "wifi" as words at all and
    can "correct" them toward something unrelated (its own top suggestion
    for "bluetoot" was "blue toot"; for the already-correct word "wifi",
    "wife"). Falls through to enchant/hunspell for everything that isn't
    close to a known command word. The hunspell path is deliberately
    conservative: only swaps in its top suggestion, and only when it's a
    same-length-ish, letters-only replacement -- so it won't mangle
    command words, proper nouns, or short words it's unsure about into
    something unrelated. Skips words that are already correct, too short
    (<4 chars, where suggestions are least reliable), all-caps (acronyms),
    or contain digits. protected_words (e.g. the user's own name/username)
    are treated the same as a known command word and never touched -- no
    dictionary anywhere would recognize a name like "Shiro" as a real
    word, so without this it looked exactly like a typo and got
    "corrected" toward the nearest dictionary word ("shiro" -> "shirt",
    "shiroosl" -> "shirtfront")."""
    d = _get_spell_dict()
    vocab = _get_command_vocab_words()
    protected = {w.lower() for w in (protected_words or ()) if w}
    if not text:
        return text

    pieces = []
    last_end = 0
    for m in _AUTOCORRECT_WORD_RE.finditer(text):
        word = m.group(0)
        bare = word.strip("'")
        if len(bare) < 4 or bare.isupper() or any(c.isdigit() for c in bare):
            continue

        bare_lower = bare.lower()
        best = None

        if bare_lower in vocab or bare_lower in protected:
            continue  # already a known command word or protected word, leave it alone
        # Also protect a word that merely STARTS WITH a protected name --
        # covers typing the name glued to a suffix (e.g. a username like
        # "shiroosl" for a configured name "Shiro"), which an exact-match
        # check alone would miss and still send to hunspell as a typo.
        if any(bare_lower.startswith(p) for p in protected if len(p) >= 3):
            continue

        # Only ever try to correct a word that hunspell itself doesn't
        # recognize as valid English. Without this, a real word that
        # merely *resembles* a short vocab word ("please" vs "pause",
        # "chek"->"cheek" vs "lock") gets false-positive "corrected" into
        # an unrelated command word -- checking hunspell first restricts
        # correction to words that are actually wrong.
        word_is_valid = None
        if d:
            try:
                word_is_valid = d.check(word)
            except Exception:
                word_is_valid = None
        if word_is_valid:
            continue

        # Similarity match against Nexa's own vocabulary first. cutoff=0.72
        # is fairly strict -- e.g. "bluetoot" (0.88 vs "bluetooth") and
        # "wify"/"wifie" (0.8/0.73 vs "wifi") match; "wifi" itself is
        # already handled by the vocab-membership check above and never
        # gets here.
        close = difflib.get_close_matches(bare_lower, vocab, n=1, cutoff=0.72)
        if close:
            best = close[0]
        elif d and word_is_valid is False:
            try:
                suggestions = d.suggest(word)
            except Exception:
                continue
            if not suggestions:
                continue
            candidate = suggestions[0]
            # Only accept single-word, letters-only suggestions reasonably
            # close in length -- guards against enchant suggesting a totally
            # different (if technically "closer") word for a short typo.
            if " " in candidate or not candidate.replace("'", "").isalpha():
                continue
            if abs(len(candidate) - len(word)) > max(2, len(word) // 3):
                continue
            best = candidate

        if best is None:
            continue

        # Preserve the original word's capitalization style.
        if word.isupper():
            best = best.upper()
        elif word[0].isupper():
            best = best[0].upper() + best[1:]
        else:
            best = best.lower()

        pieces.append(text[last_end:m.start()])
        pieces.append(best)
        last_end = m.end()


    if last_end == 0:
        return text
    pieces.append(text[last_end:])
    return "".join(pieces)


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


def _request_background_portal(autostart: bool, reason: str = "Start Nexa Assistant automatically at login"):
    """Ask the XDG Background portal to enable/disable autostart + background running.
    Replaces manually writing to ~/.config/autostart/ (which required --filesystem=home)."""
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Background", None
        )
        options = {
            "reason": GLib.Variant("s", reason),
            "autostart": GLib.Variant("b", autostart),
            "commandline": GLib.Variant("as", ["nexa", "--background"]),
            "dbus-activatable": GLib.Variant("b", False),
        }
        proxy.call_sync(
            "RequestBackground",
            GLib.Variant("(sa{sv})", ("", options)),
            Gio.DBusCallFlags.NONE, -1, None
        )
        return True
    except Exception:
        return False


def enable_autostart():
    if _request_background_portal(autostart=True):
        return
    # Fallback for environments without xdg-desktop-portal-gnome/kde (e.g. some minimal DEs)
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
    if _request_background_portal(autostart=False):
        return
    try:
        os.remove(AUTOSTART_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


class NexaSetupWindow(Adw.ApplicationWindow):
    """Six-step onboarding wizard: welcome -> username -> location ->
    privacy/command-access -> web search (skippable) -> background & voice ->
    active-development notice.

    Visually matches the main chat window's hero page (glow-behind-icon +
    staggered fade-in) so onboarding doesn't feel like a bare, disconnected
    dialog bolted onto a more polished app. All config values written here
    use the exact same keys NexaWindow.__init__ reads on first launch, so
    the wizard is just pre-seeding config rather than a separate code path."""

    STEP_NAMES = ["welcome", "username", "location", "privacy", "websearch", "background", "done"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Welcome to Nexa")
        self.set_default_size(600, 560)
        self.set_resizable(False)

        # .nexa-hero-glow/.nexa-hero-icon are normally loaded by NexaWindow's
        # _build_entry_bar, which hasn't run yet on a fresh install (this
        # setup window is shown *instead of* NexaWindow) -- load the same
        # two rules here too so the glow-icon look matches exactly.
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b".nexa-hero-glow { background: radial-gradient(circle, alpha(#3584e4, 0.38) 0%, "
            b"alpha(#3584e4, 0.10) 45%, alpha(#3584e4, 0) 70%); border-radius: 9999px; }"
            b".nexa-hero-icon { filter: drop-shadow(0 6px 18px alpha(#3584e4, 0.4)); }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # In-memory choices, written to config only on Finish (step "done").
        self._disabled_categories = set()
        self._web_search_enabled = False
        self._firecrawl_api_key = ""

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_title(False)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.set_tooltip_text("Back")
        self.back_button.connect("clicked", self.on_back)
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)
        toolbar_view.add_top_bar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        toolbar_view.set_content(self.stack)

        self._build_welcome_page()
        self._build_username_page()
        self._build_location_page()
        self._build_privacy_page()
        self._build_websearch_page()
        self._build_background_page()
        self._build_done_page()

    def _go_to_step(self, name):
        self.stack.set_visible_child_name(name)
        self.back_button.set_visible(name != "welcome")

    def on_back(self, _button):
        current = self.stack.get_visible_child_name()
        idx = self.STEP_NAMES.index(current)
        if idx > 0:
            self._go_to_step(self.STEP_NAMES[idx - 1])

    def _build_hero_icon(self, box, size=160, icon_px=80):
        """Same glow-behind-icon treatment as NexaWindow's hero page."""
        glow = Gtk.Box()
        glow.add_css_class("nexa-hero-glow")
        glow.set_size_request(size, size)
        glow.set_halign(Gtk.Align.CENTER)
        glow.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("org.nexa.Assistant")
        icon.set_pixel_size(icon_px)
        icon.add_css_class("nexa-hero-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)

        overlay = Gtk.Overlay()
        overlay.set_child(glow)
        overlay.add_overlay(icon)
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_margin_bottom(8)
        box.append(overlay)
        return overlay

    def _fade_in_page(self, *widgets):
        """Staggered fade-in for a page's widgets, same easing/duration as
        NexaWindow's hero entrance animation."""
        for w in widgets:
            w.set_opacity(0)
        anims_holder = []

        def fade(widget, delay):
            def start(*_a):
                anim = Adw.TimedAnimation.new(
                    widget, 0, 1, 450,
                    Adw.CallbackAnimationTarget.new(lambda v: widget.set_opacity(v)),
                )
                anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
                anim.play()
                anims_holder.append(anim)
            GLib.timeout_add(delay, lambda: (start(), False)[1])

        for i, w in enumerate(widgets):
            fade(w, 120 + i * 140)
        self._setup_anims = anims_holder

    def _wizard_page(self, title, description, icon_size=0):
        """Shared scrollable page shell for the settings-heavy steps
        (privacy/web-search/background) so long option lists don't blow
        out the fixed 600x560 window."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_vexpand(True)

        if icon_size:
            icon_box = Gtk.Box()
            icon_box.set_halign(Gtk.Align.CENTER)
            icon_box.set_margin_top(18)
            self._build_hero_icon(icon_box, size=icon_size, icon_px=int(icon_size * 0.5))
            outer.append(icon_box)

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("title-1")
        title_label.set_margin_top(12 if not icon_size else 0)
        outer.append(title_label)

        desc_label = Gtk.Label(label=description)
        desc_label.add_css_class("dim-label")
        desc_label.set_wrap(True)
        desc_label.set_justify(Gtk.Justification.CENTER)
        desc_label.set_max_width_chars(48)
        desc_label.set_margin_bottom(8)
        outer.append(desc_label)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # A real Gtk.ListBox (styled as a libadwaita "boxed list"), not a
        # plain Gtk.Box -- Adw.SwitchRow/ComboRow/PasswordEntryRow are all
        # Adw.PreferencesRow subclasses, designed to live inside a
        # Gtk.ListBox the way Adw.PreferencesGroup manages internally
        # (confirmed: the equivalent working Settings rows all go through
        # PreferencesGroup.add(), never a bare Box). Appending them
        # directly to a plain Box left them without their expected parent
        # listbox context -- diagnosed via a real, reproducible dead click
        # on the onboarding Voice ComboRow (no popover, no error, zero log
        # output) plus a constant stream of
        # "gtk_list_box_row_grab_focus: assertion 'box != NULL' failed"
        # in `journalctl --user` the whole time onboarding was open, which
        # stopped being a mystery once this was found -- it's GTK's own
        # rows repeatedly trying to reach a ListBox parent that was never
        # there.
        content = Gtk.ListBox()
        content.set_selection_mode(Gtk.SelectionMode.NONE)
        content.add_css_class("boxed-list")
        content.set_margin_start(32)
        content.set_margin_end(32)
        content.set_margin_bottom(16)
        scroller.set_child(content)
        outer.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_halign(Gtk.Align.CENTER)
        footer.set_margin_top(4)
        footer.set_margin_bottom(20)
        outer.append(footer)

        return outer, content, footer

    def _build_welcome_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        self._build_hero_icon(box)

        title = Gtk.Label(label="Welcome to Nexa")
        title.add_css_class("title-1")
        box.append(title)

        # Friendlier, more concrete than the old one-liner -- says what
        # Nexa actually does day to day instead of a generic tagline.
        subtitle = Gtk.Label(
            label="A voice assistant that lives on your desktop \u2014 ask her "
                  "to check the weather, control your system, find a file, "
                  "or just chat, all without your data leaving your machine."
        )
        subtitle.add_css_class("dim-label")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        subtitle.set_max_width_chars(46)
        box.append(subtitle)

        button = Gtk.Button(label="Get Started")
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.set_margin_top(24)
        button.connect("clicked", lambda *_: self._go_to_step("username"))
        add_press_bounce(button)
        box.append(button)

        self.stack.add_named(box, "welcome")
        self._fade_in_page(title, subtitle, button)

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
        self.username_entry.connect("activate", lambda *_: self._on_username_continue())
        self.username_entry.connect("changed", lambda *_: self.username_error.set_visible(False))

        self.username_error = Gtk.Label(label="Please enter a name to continue.")
        self.username_error.add_css_class("error")
        self.username_error.set_visible(False)

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self._on_username_continue())
        add_press_bounce(next_button)

        box.append(self.username_entry)
        box.append(self.username_error)
        box.append(next_button)
        page.set_child(box)
        self.stack.add_named(page, "username")

    def _on_username_continue(self):
        # Require a real name instead of silently substituting "Friend" --
        # a blank/whitespace-only entry now shows an inline error and stays
        # on this step rather than quietly moving on with a placeholder
        # identity the user never chose.
        if not self.username_entry.get_text().strip():
            self.username_error.set_visible(True)
            self.username_entry.add_css_class("error")
            return
        self.username_entry.remove_css_class("error")
        self._go_to_step("location")

    def _build_location_page(self):
        page = Adw.StatusPage(
            title="Where are you based?",
            description="Used for weather lookups.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_halign(Gtk.Align.CENTER)

        self.location_dropdown = Gtk.DropDown.new_from_strings(LOCATIONS)
        # new_from_strings() builds a plain Gtk.StringList model with no
        # search expression attached, so set_enable_search(True) had a
        # search box that visually appeared but filtered nothing (DropDown
        # needs an expression telling it which property of each list item
        # to match against). Fixed by giving it a PropertyExpression on
        # Gtk.StringObject's "string" property.
        self.location_dropdown.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self.location_dropdown.set_enable_search(True)
        # Previously left unset, which meant "Finish" without touching the
        # dropdown silently saved LOCATIONS[0] ("Afghanistan") -- picking a
        # deliberate, clearly-a-placeholder starting point instead so an
        # unconfirmed choice is obvious rather than an arbitrary real country.
        default_idx = LOCATIONS.index("United States") if "United States" in LOCATIONS else 0
        self.location_dropdown.set_selected(default_idx)

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self._go_to_step("privacy"))
        add_press_bounce(next_button)

        box.append(self.location_dropdown)
        box.append(next_button)
        page.set_child(box)
        self.stack.add_named(page, "location")

    # --- Step 4: Privacy / Command Access -------------------------------------

    def _build_privacy_page(self):
        outer, content, footer = self._wizard_page(
            "What can Nexa do?",
            "Choose what Nexa is allowed to act on. You can change any of "
            "this later in Settings > Privacy.",
        )

        self._privacy_switches = {}
        for code, (label, description) in COMMAND_CATEGORIES.items():
            row = Adw.SwitchRow(title=html.escape(label), subtitle=html.escape(description))
            row.set_active(True)  # nothing locked down by default, matches Settings
            row.connect("notify::active", self._on_setup_category_toggled, code)
            content.append(row)
            self._privacy_switches[code] = row

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self._go_to_step("websearch"))
        add_press_bounce(next_button)
        footer.append(next_button)

        self.stack.add_named(outer, "privacy")

    def _on_setup_category_toggled(self, switch_row, _param, code):
        if switch_row.get_active():
            self._disabled_categories.discard(code)
        else:
            self._disabled_categories.add(code)

    # --- Step 5: Web Search (skippable) ---------------------------------------

    def _build_websearch_page(self):
        outer, content, footer = self._wizard_page(
            "Live web search",
            "Nexa is local-first and doesn't connect to the internet for "
            "anything else. Turning this on lets her run live web searches "
            "\u2014 current prices, news, and similar \u2014 via the third-party "
            "Firecrawl API, using your own free API key. Nexa is not "
            "affiliated with or endorsed by Firecrawl.",
        )

        enable_row = Adw.SwitchRow(title="Enable Web Search", subtitle="Requires an API key below to actually work")
        enable_row.set_active(False)
        enable_row.connect("notify::active", self._on_setup_websearch_toggled)
        content.append(enable_row)

        key_row = Adw.PasswordEntryRow(title="Firecrawl API Key")
        key_row.connect("changed", self._on_setup_firecrawl_key_changed)
        content.append(key_row)

        link_row = Adw.ActionRow(title="Get a free API key", subtitle="firecrawl.dev/app/api-keys")
        link_row.set_activatable(True)
        link_row.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        link_row.connect("activated", lambda *_: webbrowser.open("https://firecrawl.dev/app/api-keys"))
        content.append(link_row)

        skip_button = Gtk.Button(label="Skip")
        skip_button.add_css_class("pill")
        # Skip leaves web search OFF (config default) -- same end state as
        # just not touching the toggle, just a faster path through setup.
        skip_button.connect("clicked", lambda *_: self._go_to_step("background"))
        add_press_bounce(skip_button)

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self._go_to_step("background"))
        add_press_bounce(next_button)

        footer.append(skip_button)
        footer.append(next_button)

        self.stack.add_named(outer, "websearch")

    def _on_setup_websearch_toggled(self, switch_row, _param):
        self._web_search_enabled = switch_row.get_active()

    def _on_setup_firecrawl_key_changed(self, entry_row):
        self._firecrawl_api_key = entry_row.get_text().strip()

    # --- Step 6: Background, startup, voice -----------------------------------

    def _build_background_page(self):
        outer, content, footer = self._wizard_page(
            "Background & voice",
            "How Nexa runs, and how she sounds when she talks back. All of "
            "this can be changed later in Settings.",
        )

        self.setup_background_row = Adw.SwitchRow(
            title="Run in Background",
            subtitle="Keep Nexa running when you close the window, so she reopens instantly",
        )
        self.setup_background_row.set_active(True)
        content.append(self.setup_background_row)

        self.setup_startup_row = Adw.SwitchRow(
            title="Launch at Startup",
            subtitle="Start Nexa automatically when you log in",
        )
        self.setup_startup_row.set_active(False)
        content.append(self.setup_startup_row)

        self.setup_wakeword_row = Adw.SwitchRow(
            title="Wake Word",
            subtitle='Say "Hey Nexa" to start listening, even in the background',
        )
        self.setup_wakeword_row.set_active(False)
        content.append(self.setup_wakeword_row)

        self.setup_voice_row = Adw.SwitchRow(
            title="Speak Replies",
            subtitle="Nexa reads her answers out loud instead of just showing text",
        )
        self.setup_voice_row.set_active(True)
        content.append(self.setup_voice_row)

        self.setup_gender_row = Adw.ComboRow(title="Voice", subtitle="Male or female speaking voice")
        self.setup_gender_row.set_model(Gtk.StringList.new(["Female", "Male"]))
        self.setup_gender_row.set_selected(0)
        self.setup_gender_row.set_sensitive(TTS_SUPPORTED)
        content.append(self.setup_gender_row)

        if not TTS_SUPPORTED:
            tts_note_row = Adw.ActionRow(
                title="Limited voice quality on this device",
                subtitle="Nexa's natural voice (Piper) currently only supports x86_64 CPUs. "
                         "Replies will use your system's built-in speech synthesizer instead.",
            )
            tts_note_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            content.append(tts_note_row)

        next_button = Gtk.Button(label="Continue")
        next_button.add_css_class("suggested-action")
        next_button.add_css_class("pill")
        next_button.connect("clicked", lambda *_: self._go_to_step("done"))
        add_press_bounce(next_button)
        footer.append(next_button)

        self.stack.add_named(outer, "background")

    # --- Step 7: Active development notice ------------------------------------

    def _build_done_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        self._build_hero_icon(box)

        title = Gtk.Label(label="Nexa is under active development")
        title.add_css_class("title-1")
        box.append(title)

        subtitle = Gtk.Label(
            label="You're using an early version \u2014 things may be rough around "
                  "the edges, and features are still being added. Bug reports and "
                  "feature requests are always welcome on GitHub."
        )
        subtitle.add_css_class("dim-label")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        subtitle.set_max_width_chars(46)
        box.append(subtitle)

        github_button = Gtk.Button(label="Open GitHub Repo")
        github_button.add_css_class("pill")
        github_button.set_halign(Gtk.Align.CENTER)
        github_button.set_margin_top(20)
        github_button.connect("clicked", lambda *_: webbrowser.open(NEXA_GITHUB_URL))
        add_press_bounce(github_button)
        box.append(github_button)

        finish_button = Gtk.Button(label="Finish")
        finish_button.add_css_class("suggested-action")
        finish_button.add_css_class("pill")
        finish_button.set_halign(Gtk.Align.CENTER)
        finish_button.connect("clicked", self.on_finish)
        add_press_bounce(finish_button)
        box.append(finish_button)

        self.stack.add_named(box, "done")

    def on_finish(self, _button):
        username = self.username_entry.get_text().strip()
        idx = self.location_dropdown.get_selected()
        location = LOCATIONS[idx] if idx != Gtk.INVALID_LIST_POSITION else LOCATIONS[0]

        write_config("username", username)
        write_config("user_location", location)

        # Same config keys NexaWindow.__init__ reads -- pre-seeding them
        # here means the wizard's choices take effect on first real launch
        # with zero special-casing in NexaWindow itself.
        write_config("disabled_categories", ",".join(sorted(self._disabled_categories)))
        write_config("web_search_enabled", "1" if self._web_search_enabled else "0")
        write_config("firecrawl_api_key", self._firecrawl_api_key)

        run_in_background = self.setup_background_row.get_active()
        write_config("run_in_background", "1" if run_in_background else "0")

        wakeword_enabled = self.setup_wakeword_row.get_active()
        write_config("wakeword_enabled", "1" if wakeword_enabled else "0")

        voice_enabled = self.setup_voice_row.get_active()
        write_config("voice_enabled", "1" if voice_enabled else "0")

        gender_idx = self.setup_gender_row.get_selected()
        write_config("voice_gender", "female" if gender_idx == 0 else "male")

        # Launch-at-startup goes through the XDG Background portal (or the
        # autostart-file fallback), same as toggling it later in Settings --
        # not a plain config key, so it's applied directly rather than
        # written via write_config.
        if self.setup_startup_row.get_active():
            enable_autostart()

        write_config("setup_done", "1")

        app = self.get_application()
        main_win = NexaWindow(application=app)
        app.win = main_win  # so re-activation (hotkey) targets the real chat window
        app.start_tray()
        app.start_global_shortcut()
        main_win.present()
        self.close()


def add_press_bounce(button, min_scale=0.88):
    """Spring-driven squash/stretch on press+release, like a physical
    button. Uses a tiny CSS provider scoped to this one button (via a
    unique CSS node name) that's only rewritten while the spring is
    actively animating -- not on every hover frame -- so it stays cheap
    even though it's a real per-frame update while it plays. Shared by
    both NexaWindow and QuickCommandPill's mic/send buttons."""
    node_name = f"nexa-bounce-{id(button)}"
    button.set_name(node_name)
    provider = Gtk.CssProvider()

    def _apply_scale(scale):
        css = f"#{node_name} {{ transform: scale({scale:.3f}); }}".encode()
        provider.load_from_data(css)

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _apply_scale(1.0)

    spring_params = Adw.SpringParams.new(0.55, 1.0, 500.0)

    def _settle_to(target):
        start = getattr(button, "_bounce_scale", 1.0)
        spring = Adw.SpringAnimation.new(
            button, start, target,
            spring_params, Adw.CallbackAnimationTarget.new(_apply_scale),
        )
        spring.connect("done", lambda *_a: setattr(button, "_bounce_scale", target))
        spring.play()

    click = Gtk.GestureClick.new()
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", lambda *_a: _settle_to(min_scale))
    click.connect("released", lambda *_a: _settle_to(1.0))
    click.connect("cancel", lambda *_a: _settle_to(1.0))
    button.add_controller(click)


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
        self.adaptive_learning_enabled = read_config("adaptive_learning_enabled", "1") == "1"

        raw_search_folders = read_config("search_folders", "downloads,documents,desktop")
        self.search_folders = set(f for f in raw_search_folders.split(",") if f in FILE_SEARCH_FOLDERS)

        raw_disabled_categories = read_config("disabled_categories", "")
        self.disabled_categories = set(
            c for c in raw_disabled_categories.split(",") if c in COMMAND_CATEGORIES
        )

        self.web_search_enabled = read_config("web_search_enabled", "1") == "1"
        self.firecrawl_api_key = read_config("firecrawl_api_key", "")

        self.conversation_memory_enabled = read_config("conversation_memory_enabled", "1") == "1"

        self.autocorrect_enabled = read_config("autocorrect_enabled", "1") == "1"

        self.training_data = TrainingDataCollector()
        self.training_data.set_collect_wakeword(self.collect_wakeword_data)

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

        # Robust auto-scroll: hook the adjustment's own "changed" signal
        # (fires exactly when GTK recalculates scroll bounds after new
        # content is laid out) instead of animating toward
        # get_upper()-get_page_size() measured at append time -- that value
        # is stale before layout finishes, which is why replies (especially
        # taller ones like cards) were landing partly below the fold and
        # needed a manual scroll to see. Only auto-scrolls if the user was
        # already near the bottom, so scrolling up to read history doesn't
        # get yanked back down by a new message arriving.
        self._pinned_to_bottom = True
        adjustment = self.scrolled.get_vadjustment()
        adjustment.connect("changed", self._on_chat_adjustment_changed)
        adjustment.connect("value-changed", self._on_chat_adjustment_value_changed)

        return self.scrolled

    def _on_chat_adjustment_value_changed(self, adjustment):
        # Track whether the user is (still) near the bottom, so a
        # newly-arriving message only auto-scrolls when that's where they
        # already were -- not after they've scrolled up to read history.
        target = adjustment.get_upper() - adjustment.get_page_size()
        self._pinned_to_bottom = adjustment.get_value() >= target - 40

    def _on_chat_adjustment_changed(self, adjustment):
        if self._pinned_to_bottom:
            GLib.idle_add(self._scroll_to_bottom)

    def _build_entry_bar(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"entry.nexa-input { border-radius: 24px; min-height: 26px; "
            b"padding-left: 18px; padding-right: 18px; background: none; "
            b"box-shadow: none; border: none; } "
            b"entry.nexa-input text { background: none; }"
            b".nexa-user-bubble { background: linear-gradient(135deg, #4a91f0 0%, #3160c9 100%); "
            b"color: #ffffff; border-radius: 20px; padding: 10px 16px; "
            b"box-shadow: 0 2px 8px alpha(#1a4faf, 0.35); } "
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
            b".nexa-file-card { background-color: @view_bg_color; border: 1px solid alpha(@borders, 0.6); "
            b"border-radius: 18px; padding: 14px 16px; min-width: 260px; } "
            b".nexa-file-card-header { color: alpha(@window_fg_color, 0.6); font-size: 11px; "
            b"font-weight: 600; letter-spacing: 0.5px; } "
            b".nexa-file-item { border-radius: 14px; padding: 8px; min-width: 76px; } "
            b".nexa-file-item:hover { background-color: alpha(@window_fg_color, 0.06); } "
            b".nexa-file-item-label { font-size: 11px; }"
            b".nexa-music-card { border-radius: 18px; padding: 12px 16px; min-width: 260px; } "
            b".nexa-music-art { border-radius: 12px; background-color: alpha(#000000, 0.15); } "
            b".nexa-music-label { font-size: 11px; font-weight: 600; letter-spacing: 0.5px; } "
            b".nexa-music-title { font-size: 15px; font-weight: 700; } "
            b".nexa-music-subtitle { font-size: 12px; }"
            b".nexa-diag-card { background-color: @view_bg_color; border: 1px solid alpha(@borders, 0.6); "
            b"border-radius: 18px; padding: 14px 16px; min-width: 260px; } "
            b".nexa-diag-header { font-size: 13px; font-weight: 700; letter-spacing: 0.3px; } "
            b".nexa-diag-status-ok { color: #2ec27e; } "
            b".nexa-diag-status-warn { color: #e5a50a; } "
            b".nexa-diag-section-title { font-size: 11px; font-weight: 600; "
            b"color: alpha(@window_fg_color, 0.65); letter-spacing: 0.3px; } "
            b".nexa-diag-item { font-size: 12px; color: alpha(@window_fg_color, 0.85); }"
            b".nexa-websearch-card { background-color: alpha(@window_fg_color, 0.04); "
            b"border-radius: 16px; padding: 12px 14px; min-width: 320px; } "
            b".nexa-websearch-header { font-size: 11px; font-weight: 700; "
            b"color: alpha(@window_fg_color, 0.5); letter-spacing: 0.6px; margin-bottom: 4px; } "
            b".nexa-websearch-link { border-radius: 10px; padding: 8px; } "
            b".nexa-websearch-link:hover { background-color: alpha(@window_fg_color, 0.07); } "
            b".nexa-websearch-icon { color: alpha(@window_fg_color, 0.45); } "
            b".nexa-websearch-title { font-size: 13px; font-weight: 500; color: @accent_color; } "
            b".nexa-websearch-domain { font-size: 11px; color: alpha(@window_fg_color, 0.55); }"
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
        EntrySpellChecker(self.entry)

        self.mic_button = Gtk.Button(icon_name="audio-input-microphone-symbolic")
        self.mic_button.add_css_class("circular")
        self.mic_button.set_tooltip_text("Voice input")
        self.mic_button.connect("clicked", self.on_mic_clicked)
        add_press_bounce(self.mic_button)

        send_button = Gtk.Button(icon_name="mail-send-symbolic")
        send_button.add_css_class("suggested-action")
        send_button.add_css_class("circular")
        send_button.set_tooltip_text("Send")
        send_button.connect("clicked", self.on_send)
        add_press_bounce(send_button)

        entry_box.append(self.mic_button)
        entry_box.append(self.entry)
        entry_box.append(send_button)
        return entry_box

    # --- Chat behaviour ------------------------------------------------------------
    def _append_message(self, text, is_user=True, markdown=False):
        bubble = Gtk.Label(label="" if not is_user else "")
        bubble.set_wrap(True)
        bubble.set_xalign(0)
        bubble.add_css_class("nexa-user-bubble" if is_user else "nexa-bot-bubble")
        bubble.set_margin_top(4)
        bubble.set_margin_bottom(4)
        bubble.set_margin_start(10)
        bubble.set_margin_end(10)

        if markdown:
            # GtkLabel's own default link handler tries a direct
            # Gio.AppInfo launch, which doesn't reach outside the sandbox
            # -- route link clicks through the same OpenURI-portal path
            # everything else uses (_open_uri), and tell GTK we handled it
            # so it doesn't also attempt (and fail at) its own handling.
            bubble.connect("activate-link", self._on_bubble_link_activated)

        if is_user:
            # User messages are always shown as plain text (never
            # rendered as Markdown) -- what the user typed should appear
            # exactly as typed, not reinterpreted as formatting.
            bubble.set_text(text)
        elif not markdown:
            bubble.set_text(text)

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

        if is_user:
            self._pop_in_row(row)
        else:
            self._fade_in_row(row)
            self._reveal_words(bubble, text, markdown=markdown)

        GLib.idle_add(self._scroll_to_bottom)

    def _on_bubble_link_activated(self, _label, uri):
        self._open_uri(uri)
        return True  # tell GTK this is handled -- don't also run its own (sandbox-incompatible) default handler

    def _reveal_words(self, label, full_text, markdown=False):
        """Reveals a bot response word by word, each word cross-fading in
        (via animated Pango foreground-alpha over just that word's byte
        range) rather than the whole message appearing at once -- the
        familiar "streaming reply" look most AI chat UIs use. Runs on a
        single shared tick loop per message (one GLib.timeout_add, not one
        per word/animation) that both reveals new words on a cadence and
        advances every still-fading word's alpha, so concurrently-fading
        words never clobber each other's Pango attributes -- an earlier,
        simpler version that gave each word its own independent
        AttrList/animation caused exactly that: a new word's fade-in would
        overwrite the whole label's attributes and snap the previous word
        (if not fully done fading yet) instantly to full opacity."""
        words = full_text.split(" ")
        if not words or not words[0]:
            if markdown:
                label.set_markup(markdown_to_pango(full_text))
            else:
                label.set_text(full_text)
            return

        if markdown:
            self._reveal_words_markdown(label, words)
            return

        FRAME_MS = 30
        WORD_INTERVAL_MS = 55
        FADE_MS = 160

        state = {
            "revealed": "",
            "next_word_index": 0,
            "ms_since_last_word": WORD_INTERVAL_MS,  # reveal the first word immediately
            "pending_fades": [],  # list of [start_byte, end_byte, elapsed_ms]
        }

        def tick():
            state["ms_since_last_word"] += FRAME_MS

            if state["ms_since_last_word"] >= WORD_INTERVAL_MS and state["next_word_index"] < len(words):
                state["ms_since_last_word"] = 0
                word = words[state["next_word_index"]]
                prev_revealed = state["revealed"]
                new_revealed = f"{prev_revealed} {word}" if prev_revealed else word
                start_byte = len(prev_revealed.encode("utf-8")) + (1 if prev_revealed else 0)
                end_byte = len(new_revealed.encode("utf-8"))
                state["revealed"] = new_revealed
                state["next_word_index"] += 1
                state["pending_fades"].append([start_byte, end_byte, 0])
                label.set_text(new_revealed)

            still_fading = []
            attrs = Pango.AttrList()
            for fade in state["pending_fades"]:
                fade[2] += FRAME_MS
                progress = min(1.0, fade[2] / FADE_MS)
                alpha = int(progress * 65535)
                attr = Pango.attr_foreground_alpha_new(alpha)
                attr.start_index = fade[0]
                attr.end_index = fade[1]
                attrs.insert(attr)
                if progress < 1.0:
                    still_fading.append(fade)
            state["pending_fades"] = still_fading
            label.set_attributes(attrs)

            done = state["next_word_index"] >= len(words) and not state["pending_fades"]
            if not done:
                GLib.idle_add(self._scroll_to_bottom)
            return not done

        GLib.timeout_add(FRAME_MS, tick)

    def _reveal_words_markdown(self, label, words):
        """Markdown-aware counterpart to _reveal_words' plain-text tick
        loop. Can't reuse the same mechanism: that one reveals plain text
        via set_text() and layers the per-word fade on top via
        set_attributes(), but set_markup() replaces a label's ENTIRE
        attribute list with the one parsed from the markup string, so any
        attributes applied via set_attributes() would just get wiped out
        the next time set_markup() runs. Word-splitting the raw Markdown
        source directly is also unsafe -- a word boundary could land
        inside a **bold** run or a [text](url) link and produce truncated,
        broken markup mid-reveal.

        Instead, each tick re-converts the cumulative *plain-text* prefix
        through markdown_to_pango() from scratch (cheap at chat-message
        length) and bakes the newest word's fade progress directly into
        the markup as an inline <span alpha="..."> wrapped around just
        that word -- alpha is expressed as a 0-65535 integer, same range
        set_attributes() used, just written inline instead of layered on
        after the fact."""
        FRAME_MS = 30
        WORD_INTERVAL_MS = 55
        FADE_MS = 160

        state = {
            "next_word_index": 0,
            "ms_since_last_word": WORD_INTERVAL_MS,
            "fading_word_elapsed": None,  # ms into the fade of the most-recently-added word, or None
        }

        def render():
            revealed = " ".join(words[:state["next_word_index"]])
            if state["fading_word_elapsed"] is not None and state["next_word_index"] > 0:
                progress = min(1.0, state["fading_word_elapsed"] / FADE_MS)
                alpha = int(progress * 65535)
                stable = " ".join(words[:state["next_word_index"] - 1])
                newest = words[state["next_word_index"] - 1]
                stable_markup = markdown_to_pango(stable)
                newest_markup = markdown_to_pango(newest)
                sep = " " if stable else ""
                markup = f'{stable_markup}{sep}<span alpha="{alpha}">{newest_markup}</span>'
            else:
                markup = markdown_to_pango(revealed)
            try:
                label.set_markup(markup)
            except GLib.Error:
                # A partial word boundary can occasionally still produce
                # markup Pango rejects (e.g. an unclosed <a> tag mid-URL)
                # -- fall back to the fully-revealed plain markup for this
                # frame rather than crash or leave the label blank.
                label.set_markup(markdown_to_pango(revealed))

        def tick():
            state["ms_since_last_word"] += FRAME_MS

            if state["ms_since_last_word"] >= WORD_INTERVAL_MS and state["next_word_index"] < len(words):
                state["ms_since_last_word"] = 0
                state["next_word_index"] += 1
                state["fading_word_elapsed"] = 0
            elif state["fading_word_elapsed"] is not None:
                state["fading_word_elapsed"] += FRAME_MS
                if state["fading_word_elapsed"] >= FADE_MS:
                    state["fading_word_elapsed"] = None

            render()

            done = (
                state["next_word_index"] >= len(words)
                and state["fading_word_elapsed"] is None
            )
            if not done:
                GLib.idle_add(self._scroll_to_bottom)
            return not done

        GLib.timeout_add(FRAME_MS, tick)


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

        # Paired spring motion: the row rises into place with a touch of
        # bounce, alongside the opacity fade above. Underdamped
        # (damping_ratio < 1) so it slightly overshoots then settles,
        # which reads as much livelier than a plain linear/eased slide.
        RISE_PX = 14
        base_margin = row.get_margin_top()
        row.set_margin_top(base_margin + RISE_PX)
        spring_params = Adw.SpringParams.new(0.7, 1.0, 300.0)
        spring = Adw.SpringAnimation.new(
            row, RISE_PX, 0,
            spring_params,
            Adw.CallbackAnimationTarget.new(
                lambda offset: row.set_margin_top(base_margin + int(offset))
            ),
        )
        spring.play()
        self._row_anims.append(spring)

    def _pop_in_row(self, row):
        """User bubble entrance: the same fade + vertical spring-rise as
        _fade_in_row, plus a second, snappier horizontal spring layered on
        top -- giving the user's own message a touch more energy on send,
        to visually distinguish "you just sent this" from a bot reply
        quietly fading in."""
        self._fade_in_row(row)
        # A second, snappier spring purely on the row's horizontal margin
        # gives a small "settle in from the right" motion alongside the
        # vertical rise, reinforcing that this bubble just appeared rather
        # than always having been there.
        NUDGE_PX = 10
        base_margin_end = row.get_margin_end()
        row.set_margin_end(base_margin_end + NUDGE_PX)
        spring_params = Adw.SpringParams.new(0.55, 1.0, 380.0)
        spring = Adw.SpringAnimation.new(
            row, NUDGE_PX, 0,
            spring_params,
            Adw.CallbackAnimationTarget.new(
                lambda offset: row.set_margin_end(base_margin_end + int(offset))
            ),
        )
        spring.play()
        self._row_anims.append(spring)

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

    def _open_host_file(self, path):
        """Opens a file via the host's default handler (xdg-open), same
        sandbox-bypass mechanism as CommandEngine.handle_open_found_file."""
        try:
            subprocess.Popen(
                ["flatpak-spawn", "--host", "xdg-open", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            self._show_prefs_toast(f"Couldn't open {os.path.basename(path)}")

    def _append_file_card(self, data):
        """Horizontally-scrollable strip of file results from handle_find_file
        (icon + filename per item, click to open) shown instead of a plain
        text bubble -- same avatar+row layout and reveal style as the
        weather card, keyed off CommandEngine.last_card_data["type"] == "files"."""
        files = data.get("files", [])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("nexa-file-card")

        query = data.get("query", "")
        header = Gtk.Label(label=f"Results for \u201c{query}\u201d".upper(), xalign=0)
        header.add_css_class("nexa-file-card-header")
        card.append(header)

        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        strip_scroller = Gtk.ScrolledWindow()
        strip_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        strip_scroller.set_child(strip)
        strip_scroller.set_propagate_natural_height(True)

        for f in files:
            path = f.get("path", "")
            name = f.get("name", os.path.basename(path))

            item_button = Gtk.Button()
            item_button.add_css_class("nexa-file-item")
            item_button.add_css_class("flat")
            item_button.set_tooltip_text(path)
            item_button.connect("clicked", lambda _b, p=path: self._open_host_file(p))

            item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            item_box.set_halign(Gtk.Align.CENTER)

            icon_widget = Gtk.Image.new_from_icon_name(_icon_for_file(path))
            icon_widget.set_pixel_size(40)
            item_box.append(icon_widget)

            name_label = Gtk.Label(label=name, xalign=0.5)
            name_label.set_max_width_chars(12)
            name_label.set_wrap(False)
            name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name_label.add_css_class("nexa-file-item-label")
            item_box.append(name_label)

            item_button.set_child(item_box)
            strip.append(item_button)

        card.append(strip_scroller)

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

    def _analyze_album_art(self, art_path):
        """Loads the cover art and averages it down to a single RGB color
        by scaling to 1x1 with GdkPixbuf's bilinear interpolation -- a
        cheap, well-known trick that avoids pulling in a full imaging
        library just to get a representative color for the card."""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(art_path)
            tiny = pixbuf.scale_simple(1, 1, GdkPixbuf.InterpType.BILINEAR)
            pixels = tiny.get_pixels()
            return pixels[0], pixels[1], pixels[2]
        except Exception:
            return None

    def _music_card_colors(self, rgb):
        r, g, b = rgb
        # Darken a bit so text stays legible over bright/pastel covers
        bg_r, bg_g, bg_b = (max(0, min(255, int(c * 0.72))) for c in (r, g, b))
        bg_hex = f"#{bg_r:02x}{bg_g:02x}{bg_b:02x}"
        luminance = 0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b
        text_hex = "#1a1a1a" if luminance > 150 else "#ffffff"
        return bg_hex, text_hex

    def _append_music_card(self, data):
        """'Now Playing' card for handle_my_music, with a background color
        derived from the track's embedded cover art (falls back to a
        neutral dark card when there's no art or extraction failed).
        Colors are per-instance, so each card gets its own small CssProvider
        registered under a unique class name -- the static stylesheet only
        carries the structural (non-color) rules."""
        title = data.get("title") or "Unknown Track"
        artist = data.get("artist")
        album = data.get("album")
        art_path = data.get("art_path")

        bg_hex, text_hex = "#2b2b33", "#ffffff"
        rgb = self._analyze_album_art(art_path) if art_path and os.path.exists(art_path) else None
        if rgb:
            bg_hex, text_hex = self._music_card_colors(rgb)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        card.add_css_class("nexa-music-card")

        unique_class = f"nexa-music-card-{id(card)}"
        css = (
            f".{unique_class} {{ background-color: {bg_hex}; }} "
            f".{unique_class} .nexa-music-label {{ color: alpha({text_hex}, 0.75); }} "
            f".{unique_class} .nexa-music-title {{ color: {text_hex}; }} "
            f".{unique_class} .nexa-music-subtitle {{ color: alpha({text_hex}, 0.8); }}"
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        card.add_css_class(unique_class)

        art_widget = None
        if art_path and os.path.exists(art_path):
            try:
                art_widget = Gtk.Picture.new_for_filename(art_path)
                art_widget.set_content_fit(Gtk.ContentFit.COVER)
                art_widget.set_overflow(Gtk.Overflow.HIDDEN)
            except Exception:
                art_widget = None
        if art_widget is None:
            art_widget = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
            art_widget.set_pixel_size(32)
        art_widget.set_size_request(64, 64)
        art_widget.add_css_class("nexa-music-art")

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)

        now_playing_label = Gtk.Label(label="NOW PLAYING", xalign=0)
        now_playing_label.add_css_class("nexa-music-label")
        text_box.append(now_playing_label)

        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("nexa-music-title")
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_max_width_chars(24)
        text_box.append(title_label)

        subtitle_parts = [p for p in (artist, album) if p]
        if subtitle_parts:
            subtitle_label = Gtk.Label(label=" \u2014 ".join(subtitle_parts), xalign=0)
            subtitle_label.add_css_class("nexa-music-subtitle")
            subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle_label.set_max_width_chars(30)
            text_box.append(subtitle_label)

        card.append(art_widget)
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

    def _append_diagnostics_card(self, data):
        """Health-check results card for handle_diagnostics -- a vertical
        list of sections (Failed Services / Disk Space / Recent Errors),
        each shown only if it has entries, with a status icon+color per
        section (warning red/orange vs a clean green "no issues" state)
        rather than a flat wall of log lines."""
        errors = data.get("errors", [])
        failed_units = data.get("failed_units", [])
        disk_warnings = data.get("disk_warnings", [])
        healthy = not errors and not failed_units and not disk_warnings

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("nexa-diag-card")

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_icon = Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if healthy else "dialog-warning-symbolic"
        )
        status_icon.add_css_class("nexa-diag-status-ok" if healthy else "nexa-diag-status-warn")
        header_box.append(status_icon)
        header_label = Gtk.Label(
            label="SYSTEM HEALTHY" if healthy else "ISSUES FOUND", xalign=0
        )
        header_label.add_css_class("nexa-diag-header")
        header_box.append(header_label)
        card.append(header_box)

        def add_section(title, icon_name, items, item_prefix=""):
            if not items:
                return
            card.append(Gtk.Separator())
            section_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            title_icon = Gtk.Image.new_from_icon_name(icon_name)
            title_icon.set_pixel_size(14)
            title_row.append(title_icon)
            title_label = Gtk.Label(label=f"{title} ({len(items)})", xalign=0)
            title_label.add_css_class("nexa-diag-section-title")
            title_row.append(title_label)
            section_box.append(title_row)
            for item in items[:6]:
                line = Gtk.Label(label=f"{item_prefix}{item}", xalign=0)
                line.set_wrap(True)
                line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                line.set_max_width_chars(48)
                line.add_css_class("nexa-diag-item")
                section_box.append(line)
            card.append(section_box)

        add_section("Failed Services", "process-stop-symbolic", failed_units)
        add_section("Disk Space", "drive-harddisk-symbolic", disk_warnings)
        add_section("Recent Errors", "dialog-error-symbolic", errors, item_prefix="\u2022 ")

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

    def _append_web_search_card(self, data):
        """"Sources" card shown under a live web search answer -- up to 5
        clickable result rows (icon + title + domain), each opening the
        real page in the host browser via the OpenURI portal. This is
        attribution/citation, not the answer itself (that's the normal
        text bubble _process_query already appended above this)."""
        results = data.get("results", [])
        if not results:
            return

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("nexa-websearch-card")

        header = Gtk.Label(label=f"SOURCES ({len(results)})", xalign=0)
        header.add_css_class("nexa-websearch-header")
        card.append(header)

        for r in results:
            url = r.get("url", "")
            title = r.get("title") or url
            try:
                domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
            except Exception:
                domain = url

            link_button = Gtk.Button()
            link_button.add_css_class("flat")
            link_button.add_css_class("nexa-websearch-link")
            link_button.set_halign(Gtk.Align.FILL)
            if url:
                link_button.connect("clicked", lambda _b, u=url: self._open_uri(u))

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

            site_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
            site_icon.set_pixel_size(22)
            site_icon.add_css_class("nexa-websearch-icon")
            site_icon.set_valign(Gtk.Align.CENTER)
            row_box.append(site_icon)

            link_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            link_box.set_valign(Gtk.Align.CENTER)
            title_label = Gtk.Label(label=title, xalign=0)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(52)
            title_label.add_css_class("nexa-websearch-title")
            link_box.append(title_label)

            domain_label = Gtk.Label(label=domain, xalign=0)
            domain_label.add_css_class("nexa-websearch-domain")
            link_box.append(domain_label)

            row_box.append(link_box)
            link_button.set_child(row_box)
            card.append(link_button)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row2.set_halign(Gtk.Align.START)
        row2.set_margin_top(2)
        row2.set_margin_bottom(4)

        spacer = Gtk.Box()
        spacer.set_size_request(30, 1)
        row2.append(spacer)
        row2.append(card)

        row2.set_opacity(0)
        self.chat_box.append(row2)
        self._fade_in_row(row2)
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
        # Protect the user's own configured name (and common ways they
        # might type it, e.g. glued to a suffix like a username) from
        # being "corrected" -- no dictionary recognizes most names as
        # real words, so without this a name gets treated exactly like a
        # typo (confirmed: "shiro" -> "shirt", "shiroosl" -> "shirtfront"
        # via hunspell's own suggestions).
        if getattr(self, "autocorrect_enabled", True):
            protected = [self.user_name] if getattr(self, "user_name", None) else []
            text = autocorrect_text(text, protected_words=protected)
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
            elif card_data and card_data.get("type") == "files":
                self._append_file_card(card_data)
            elif card_data and card_data.get("type") == "music":
                self._append_music_card(card_data)
            elif card_data and card_data.get("type") == "diagnostics":
                self._append_diagnostics_card(card_data)
            elif card_data and card_data.get("type") == "web_search":
                # Old-Siri style: a short fixed line (no scraped-content
                # summarization to render or clean up -- that approach kept
                # surfacing new messy-page artifacts faster than they could
                # be patched, see handle_web_search's docstring) plus the
                # Sources card as the actual answer.
                self._append_message(response, is_user=False)
                self._append_web_search_card(card_data)
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
            # Escaped for the same reason as the Command Access rows above:
            # a connected app's own name/id (external, not hardcoded here)
            # could contain "&"/"<"/">" and silently blank the row's title
            # since Adw.ActionRow interprets it as Pango markup by default.
            row = Adw.ActionRow(title=html.escape(app_name), subtitle=html.escape(app_id))
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
        dialog = Adw.Dialog(content_width=440, content_height=420, title=html.escape(f"{app_name} Commands"))
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
                title=html.escape(f"\u201c{cmd.get('trigger', '')}\u201d"),
                subtitle=html.escape(cmd.get("description") or ""))
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

        view_stack = Adw.ViewStack()
        view_switcher = Adw.ViewSwitcher(stack=view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(view_switcher)
        toolbar_view.add_top_bar(header)

        def _tab_page():
            p = Adw.PreferencesPage()
            return p

        # --- Profile -------------------------------------------------------------
        profile_page = _tab_page()
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

        music_row = Adw.ActionRow(title="Favorite Music", subtitle="The song Nexa plays when you say \"play my music\"")
        pick_button = Gtk.Button(label="Choose File", valign=Gtk.Align.CENTER)
        pick_button.connect("clicked", self.on_pick_music_folder)
        music_row.add_suffix(pick_button)
        profile_group.add(music_row)

        profile_page.add(profile_group)
        view_stack.add_titled_with_icon(profile_page, "profile", "Profile", "avatar-default-symbolic")

        # --- Voice & Wake Word -----------------------------------------------------
        voice_page = _tab_page()
        voice_group = Adw.PreferencesGroup(title=html.escape("Voice & Wake Word"))

        voice_row = Adw.SwitchRow(title="Speak Replies", subtitle="Nexa reads her answers out loud instead of just showing text")
        voice_row.set_active(self.voice_enabled)
        voice_row.connect("notify::active", self._on_voice_toggled)
        voice_group.add(voice_row)

        if not TTS_SUPPORTED:
            tts_note_row = Adw.ActionRow(
                title="Limited voice quality on this device",
                subtitle="Nexa's natural voice (Piper) currently only supports x86_64 CPUs. "
                         "On ARM64/aarch64, replies will use your system's built-in speech "
                         "synthesizer instead, which sounds more robotic. Native Piper support "
                         "for ARM64 is planned for a future update.",
            )
            tts_note_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            voice_group.add(tts_note_row)

        gender_row = Adw.ComboRow(title="Voice", subtitle="Male or female speaking voice")
        gender_row.set_model(Gtk.StringList.new(["Female", "Male"]))
        gender_row.set_selected(0 if self.voice_gender == "female" else 1)
        gender_row.set_sensitive(TTS_SUPPORTED)
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

        voice_page.add(voice_group)
        view_stack.add_titled_with_icon(voice_page, "voice", "Voice", "audio-input-microphone-symbolic")

        # --- Background & Shortcuts ------------------------------------------------
        access_page = _tab_page()
        access_group = Adw.PreferencesGroup()
        access_group.set_title(html.escape("Background & Shortcuts"))
        access_group.set_description("How Nexa keeps running and how you can bring her back quickly.")

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

        access_page.add(access_group)
        view_stack.add_titled_with_icon(access_page, "access", "Background", "preferences-system-symbolic")

        # --- Extend Nexa ---------------------------------------------------------
        extend_page = _tab_page()
        extend_group = Adw.PreferencesGroup(title="Extend Nexa")
        studio_row = Adw.ActionRow(
            title="Open Nexa Studio",
            subtitle="Teach Nexa new phrases to respond to, with a reply to say or a command to run",
        )
        studio_row.set_activatable(True)
        studio_row.connect("activated", self.on_open_studio)
        studio_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        extend_group.add(studio_row)

        connected_apps_row = Adw.ActionRow(
            title="Connected Apps",
            subtitle=self._connected_apps_subtitle(),
        )
        connected_apps_row.set_activatable(True)
        connected_apps_row.connect("activated", self._open_connected_apps_dialog)
        connected_apps_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self._connected_apps_row = connected_apps_row
        extend_group.add(connected_apps_row)
        extend_page.add(extend_group)
        view_stack.add_titled_with_icon(extend_page, "extend", "Extend", "application-x-addon-symbolic")

        # --- Privacy -------------------------------------------------------------
        privacy_page = _tab_page()
        training_group = Adw.PreferencesGroup(
            title="Privacy",
            description="Off by default. Nothing ever leaves this computer automatically \u2014 "
                        "data only goes anywhere if you export and share it yourself.",
        )

        wakeword_data_row = Adw.SwitchRow(
            title="Collect \u201cHey Nexa\u201d Samples",
            subtitle="Saves a short audio clip each time the wake word is heard, to help improve wake word detection later",
        )
        wakeword_data_row.set_active(self.collect_wakeword_data)
        wakeword_data_row.connect("notify::active", self._on_collect_wakeword_toggled)
        training_group.add(wakeword_data_row)

        wc = self.training_data.counts()
        self._training_export_row = Adw.ActionRow(
            title="Export Training Data",
            subtitle=f"{wc} wake word clips saved on this device",
        )
        export_button = Gtk.Button(label="Export", valign=Gtk.Align.CENTER)
        export_button.connect("clicked", self.on_export_training_data)
        self._training_export_row.add_suffix(export_button)
        clear_button = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", self.on_clear_training_data)
        self._training_export_row.add_suffix(clear_button)
        training_group.add(self._training_export_row)
        privacy_page.add(training_group)

        # --- File Search access ---------------------------------------------------
        search_group = Adw.PreferencesGroup(
            title="File Search",
            description="Choose which folders Nexa is allowed to search for files in "
                        "when you ask her to find something.",
        )
        for code, (label, _path) in FILE_SEARCH_FOLDERS.items():
            folder_row = Adw.SwitchRow(title=label)
            folder_row.set_active(code in self.search_folders)
            folder_row.connect("notify::active", self._on_search_folder_toggled, code)
            search_group.add(folder_row)
        privacy_page.add(search_group)

        # --- Command Access ---------------------------------------------------------
        access_group = Adw.PreferencesGroup(
            title="Command Access",
            description="Lock down what Nexa is allowed to do. Turn off a category and "
                        "she'll decline those requests instead of carrying them out.",
        )
        for code, (label, description) in COMMAND_CATEGORIES.items():
            # Adw.SwitchRow's title/subtitle are interpreted as Pango
            # markup by default -- an unescaped "&" (as in "Media & Music",
            # "Clipboard & Notes") is invalid markup and silently blanked
            # the whole label instead of raising, which is why exactly
            # those two rows (and only those two) were showing no title
            # at all. Escaping here is the fix, not use_markup=False,
            # since these rows are also fine to keep using Pango
            # formatting later if that's ever wanted.
            category_row = Adw.SwitchRow(
                title=html.escape(label), subtitle=html.escape(description)
            )
            category_row.set_active(code not in self.disabled_categories)
            category_row.connect("notify::active", self._on_command_category_toggled, code)
            access_group.add(category_row)
        privacy_page.add(access_group)

        # --- Conversation Memory ------------------------------------------------------
        memory_group = Adw.PreferencesGroup(
            title="Conversation Memory",
            description="Lets Nexa refer back to the last few things you asked -- "
                        "\u201cplay the song we talked about\u201d, \u201copen that file from "
                        "earlier\u201d. Kept in memory only for the current session -- never "
                        "written to disk, and cleared the moment Nexa closes.",
        )
        self.conversation_memory_row = Adw.SwitchRow(title="Remember Recent Context")
        self.conversation_memory_row.set_active(self.conversation_memory_enabled)
        self.conversation_memory_row.connect("notify::active", self._on_conversation_memory_toggled)
        memory_group.add(self.conversation_memory_row)
        privacy_page.add(memory_group)

        # --- Auto-Corrector ------------------------------------------------------
        autocorrect_group = Adw.PreferencesGroup(
            title="Auto-Corrector",
            description="Silently fixes obvious typos in what you type before it's "
                        "sent (e.g. \u201cbluetoot\u201d \u2192 \u201cbluetooth\u201d). Turning this off "
                        "only disables the fix-on-send behavior -- the red squiggly "
                        "underline while typing stays on either way.",
        )
        self.autocorrect_row = Adw.SwitchRow(title="Auto-Correct Typos on Send")
        self.autocorrect_row.set_active(self.autocorrect_enabled)
        self.autocorrect_row.connect("notify::active", self._on_autocorrect_toggled)
        autocorrect_group.add(self.autocorrect_row)
        privacy_page.add(autocorrect_group)

        # --- Web Search --------------------------------------------------------------
        web_search_group = Adw.PreferencesGroup(
            title="Web Search",
            description="Nexa is local-first by design and doesn't connect to the "
                        "internet for anything else. Enabling this lets her run live "
                        "web searches -- current prices, news, and similar -- via the "
                        "third-party Firecrawl API, using your own free API key which "
                        "never leaves your machine except to query Firecrawl directly. "
                        "Nexa is not affiliated with or endorsed by Firecrawl.",
        )
        self.web_search_toggle_row = Adw.SwitchRow(
            title="Enable Web Search",
            subtitle="Requires an API key below to actually work",
        )
        self.web_search_toggle_row.set_active(self.web_search_enabled)
        self.web_search_toggle_row.connect("notify::active", self._on_web_search_toggled)
        web_search_group.add(self.web_search_toggle_row)

        self.firecrawl_api_key_row = Adw.PasswordEntryRow(title="Firecrawl API Key")
        if self.firecrawl_api_key:
            self.firecrawl_api_key_row.set_text(self.firecrawl_api_key)
        self.firecrawl_api_key_row.connect("changed", self._on_firecrawl_api_key_changed)
        web_search_group.add(self.firecrawl_api_key_row)

        get_key_row = Adw.ActionRow(
            title="Get a free API key",
            subtitle="1,000 searches/month, no credit card required",
        )
        get_key_row.set_activatable(True)
        get_key_row.connect("activated", lambda *_a: self._open_uri("https://www.firecrawl.dev/app/api-keys"))
        get_key_row.add_suffix(Gtk.Image.new_from_icon_name("web-browser-symbolic"))
        web_search_group.add(get_key_row)

        privacy_page.add(web_search_group)

        view_stack.add_titled_with_icon(privacy_page, "privacy", "Privacy", "channel-secure-symbolic")

        # --- About -------------------------------------------------------------------
        about_page = _tab_page()
        about_group = Adw.PreferencesGroup(title="About")
        about_row = Adw.ActionRow(title="About Nexa Assistant", subtitle=f"Version {NEXA_VERSION}")
        about_row.set_activatable(True)
        about_row.connect("activated", self.on_show_about)
        about_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        about_group.add(about_row)
        about_page.add(about_group)

        # --- Danger Zone ---------------------------------------------------------
        danger_group = Adw.PreferencesGroup(
            title="Danger Zone",
            description="Erases every Nexa setting, custom command, connected app, "
                        "and piece of collected data, then restarts Nexa fresh -- "
                        "as if it were just installed. This cannot be undone.",
        )
        reset_row = Adw.ActionRow(
            title="Reset Nexa",
            subtitle="Wipe all settings and data, then restart",
        )
        reset_button = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", self.on_reset_nexa)
        reset_row.add_suffix(reset_button)
        danger_group.add(reset_row)
        about_page.add(danger_group)

        view_stack.add_titled_with_icon(about_page, "about", "About", "help-about-symbolic")

        toolbar_view.set_content(view_stack)

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
        """Fires after every successful final transcription. Feeds the
        always-fully-local vocabulary adaptation (adaptive_learning.py) --
        the prompt is refreshed immediately so newly learned words take
        effect this same session. No raw audio is saved; only the
        transcript text is used, in memory, to build the vocabulary hint."""
        self.adaptive.record_transcript(text)
        self.stt.set_vocabulary_prompt(self.adaptive.build_prompt(self.engine.get_vocabulary_prompt()))

    def _on_wake_score(self, score):
        """Fires alongside every real wake-word trigger with its confidence
        score. Feeds the self-tuning sensitivity offset, reapplied
        immediately (see WakeWordEngine.apply_adaptive_offset)."""
        self.adaptive.record_wake_trigger(score)
        self.wake_engine.apply_adaptive_offset(self.adaptive.get_threshold_offset())

    def _on_search_folder_toggled(self, switch_row, _param, code):
        if switch_row.get_active():
            self.search_folders.add(code)
        else:
            self.search_folders.discard(code)
        write_config("search_folders", ",".join(sorted(self.search_folders)))

    def _on_command_category_toggled(self, switch_row, _param, code):
        if switch_row.get_active():
            self.disabled_categories.discard(code)
        else:
            self.disabled_categories.add(code)
        write_config("disabled_categories", ",".join(sorted(self.disabled_categories)))

    def _open_uri(self, uri):
        """Opens a link in the host's default browser via the OpenURI
        portal (webbrowser.open works transparently through it inside the
        sandbox, same as CommandEngine.handle_search's "search Google")."""
        try:
            webbrowser.open(uri)
        except Exception:
            pass

    def _on_web_search_toggled(self, switch_row, _param):
        self.web_search_enabled = switch_row.get_active()
        write_config("web_search_enabled", "1" if self.web_search_enabled else "0")

    def _on_conversation_memory_toggled(self, switch_row, _param):
        self.conversation_memory_enabled = switch_row.get_active()
        write_config("conversation_memory_enabled", "1" if self.conversation_memory_enabled else "0")
        if not self.conversation_memory_enabled:
            self.engine.context_history.clear()

    def _on_autocorrect_toggled(self, switch_row, _param):
        self.autocorrect_enabled = switch_row.get_active()
        write_config("autocorrect_enabled", "1" if self.autocorrect_enabled else "0")

    def _on_firecrawl_api_key_changed(self, entry_row):
        self.firecrawl_api_key = entry_row.get_text().strip()
        write_config("firecrawl_api_key", self.firecrawl_api_key)

    def _on_collect_wakeword_toggled(self, switch_row, _param):
        self.collect_wakeword_data = switch_row.get_active()
        self.training_data.set_collect_wakeword(self.collect_wakeword_data)
        write_config("collect_wakeword_data", "1" if self.collect_wakeword_data else "0")

    def _refresh_training_export_subtitle(self):
        if self._training_export_row is not None:
            wc = self.training_data.counts()
            self._training_export_row.set_subtitle(f"{wc} wake word samples collected")

    def on_export_training_data(self, _button):
        if not self.training_data.has_any_data():
            self._show_prefs_toast("There's no training data collected yet \u2014 use Nexa a bit first, or turn on wake word sample collection above.")
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

    def on_reset_nexa(self, _button):
        """Full factory reset -- wipes CONFIG_DIR (all per-key config files,
        Nexa Studio custom commands, and collected training data all live
        under the same ~/.config/nexa directory) then relaunches the process
        so the app re-runs onboarding exactly like a fresh install. Asking
        for a full os.execv relaunch rather than just clearing in-memory
        attributes -- there are too many subsystems (wake word, voice
        manager, D-Bus registry, tray, global shortcut) with their own
        derived state to safely reset by hand without missing something."""
        dialog = Adw.AlertDialog(
            heading="Reset Nexa?",
            body="This erases every setting, custom command, connected app, "
                 "and piece of collected data, then restarts Nexa fresh. "
                 "This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_nexa_response)
        dialog.present(self)

    def _on_reset_nexa_response(self, _dialog, response):
        if response != "reset":
            return
        try:
            # CONFIG_DIR itself (~/.config/nexa) is a Flatpak-sandbox mount
            # point (--filesystem=xdg-config/nexa:create), so it can't be
            # rmdir'd/removed as a whole -- shutil.rmtree(CONFIG_DIR) fails
            # with "Device or resource busy" once it tries to remove that
            # top-level directory. Clearing only its *contents* avoids
            # touching the mount point itself while still wiping every
            # config file, Nexa Studio command, and piece of training data.
            if os.path.isdir(CONFIG_DIR):
                for entry in os.listdir(CONFIG_DIR):
                    entry_path = os.path.join(CONFIG_DIR, entry)
                    if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                        shutil.rmtree(entry_path)
                    else:
                        os.remove(entry_path)
        except Exception as e:
            self._show_prefs_toast(f"Reset failed: {e}")
            return
        # Relaunch in-place so onboarding runs again on a clean process --
        # simplest reliable way to reset every subsystem (wake word engine,
        # voice manager, D-Bus registry, tray icon, global shortcut) at once.
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def on_show_about(self, _row):
        about = Adw.AboutDialog(
            application_name="Nexa Assistant",
            application_icon="org.nexa.Assistant",
            version=NEXA_VERSION,
            developer_name="ShiroOSL",
            developers=[f"ShiroOSL {NEXA_DEVELOPER_URL}"],
            website=NEXA_GITHUB_URL,
            issue_url=f"{NEXA_GITHUB_URL}/issues",
            license_type=Gtk.License.GPL_3_0,
            copyright="\u00a9 2026 ShiroOSL",
            comments="A local-first, privacy-focused voice assistant for the GNOME "
                     "desktop. Say \u201cHey Nexa\u201d and it listens, understands, and "
                     "talks back \u2014 wake-word detection, speech recognition, and "
                     "text-to-speech all run on-device, with nothing sent to the cloud.",
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
        add_press_bounce(self.mic_button)
        pill_box.append(self.mic_button)

        self.entry = Gtk.Entry()
        self.entry.add_css_class("nexa-input")
        self.entry.set_placeholder_text("Type a command...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.on_submit)
        EntrySpellChecker(self.entry)
        pill_box.append(self.entry)

        send_button = Gtk.Button()
        send_button.set_icon_name("mail-send-symbolic")
        send_button.add_css_class("circular")
        send_button.add_css_class("suggested-action")
        send_button.connect("clicked", self.on_submit)
        add_press_bounce(send_button)
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

        # Follow the system's GTK/libadwaita theme (light/dark/accent), same
        # as any other well-behaved GTK app -- was previously FORCE_DARK,
        # which ignored the user's actual desktop theme entirely.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
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
