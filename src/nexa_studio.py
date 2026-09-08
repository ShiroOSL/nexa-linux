"""Nexa Studio: a companion window for creating custom voice/text commands."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

import html
import nexa_studio_commands as studio


def add_press_bounce(button, min_scale=0.9):
    """Spring squash/stretch on press+release. Duplicated from main.py's
    module-level helper of the same name (not imported, to avoid a
    main.py <-> nexa_studio.py circular import -- main.py imports this
    module at startup). Keep behavior identical if either changes."""
    node_name = f"nexa-bounce-{id(button)}"
    button.set_name(node_name)
    provider = Gtk.CssProvider()

    def _apply_scale(scale):
        provider.load_from_data(f"#{node_name} {{ transform: scale({scale:.3f}); }}".encode())

    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _apply_scale(1.0)
    spring_params = Adw.SpringParams.new(0.55, 1.0, 500.0)

    def _settle_to(target):
        start = getattr(button, "_bounce_scale", 1.0)
        spring = Adw.SpringAnimation.new(
            button, start, target, spring_params,
            Adw.CallbackAnimationTarget.new(_apply_scale),
        )
        spring.connect("done", lambda *_a: setattr(button, "_bounce_scale", target))
        spring.play()

    click = Gtk.GestureClick.new()
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", lambda *_a: _settle_to(min_scale))
    click.connect("released", lambda *_a: _settle_to(1.0))
    click.connect("cancel", lambda *_a: _settle_to(1.0))
    button.add_controller(click)


class NexaStudioWindow(Adw.ApplicationWindow):
    def __init__(self, application, on_close_return_home, engine=None, voice=None):
        super().__init__(application=application)
        self.set_default_size(940, 660)
        self.set_title("Nexa Studio")
        self._on_close_return_home = on_close_return_home
        self._engine = engine
        self._voice = voice
        self._editing_id = None
        self._command_rows = []
        self._hero_anims = []
        self._action_type = "say"  # "say" | "run" -- driven by the picker cards

        self._load_css()

        self.split = Adw.NavigationSplitView()
        self.split.set_sidebar(self._build_sidebar())
        self.editor_page = self._build_editor()
        self.placeholder_page = self._build_placeholder()
        self.split.set_content(self.placeholder_page)
        self.split.set_min_sidebar_width(300)
        self.split.set_max_sidebar_width(360)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.split)
        self.set_content(self.toast_overlay)
        self.connect("close-request", self._on_close_request)
        self._refresh_list()
        GLib.idle_add(self._animate_hero_entrance)

    # ---------------------------------------------------------------- styling
    def _load_css(self):
        css = Gtk.CssProvider()
        # .nexa-hero-glow / .nexa-hero-icon copied verbatim from NexaWindow's
        # hero page (main.py _build_entry_bar's CSS block) so Studio's hero
        # matches the main chat window's glow exactly, not a re-derived copy.
        css.load_from_data(b"""
            .nexa-hero-glow {
                background: radial-gradient(circle, alpha(#3584e4, 0.38) 0%,
                    alpha(#3584e4, 0.10) 45%, alpha(#3584e4, 0) 70%);
                border-radius: 9999px;
            }
            .nexa-hero-icon {
                filter: drop-shadow(0 6px 18px alpha(#3584e4, 0.4));
            }
            .studio-row-icon {
                border-radius: 999px;
                min-width: 34px;
                min-height: 34px;
            }
            .studio-row-icon-say { background: alpha(#3584e4, 0.16); }
            .studio-row-icon-say image { color: #3584e4; }
            .studio-row-icon-run { background: alpha(#2ec27e, 0.16); }
            .studio-row-icon-run image { color: #26a269; }
            .studio-rec-row {
                border-radius: 12px;
                background: alpha(currentColor, 0.03);
                margin-bottom: 2px;
            }
            .studio-empty-page { opacity: 0.85; }
            .studio-sidebar-scroll { background: transparent; }

            /* Colorful action-type picker: two big gradient cards instead of
               a plain dropdown, echoing the same "gradient card" language
               as the weather/music bubbles in the main chat window. */
            .studio-type-card {
                border-radius: 18px;
                padding: 16px 14px;
                min-height: 92px;
                color: #ffffff;
            }
            .studio-type-card-say {
                background: linear-gradient(135deg, #4a91f0 0%, #3160c9 100%);
                box-shadow: 0 4px 14px alpha(#1a4faf, 0.35);
            }
            .studio-type-card-run {
                background: linear-gradient(135deg, #3ddc97 0%, #1f9e6b 100%);
                box-shadow: 0 4px 14px alpha(#137a4f, 0.35);
            }
            .studio-type-card-unselected {
                background: alpha(currentColor, 0.06);
                color: @window_fg_color;
                box-shadow: none;
            }
            .studio-type-card:hover { filter: brightness(1.06); }
            .studio-type-card-title { font-weight: 700; font-size: 14px; }
            .studio-type-card-sub { font-size: 11px; opacity: 0.85; }
            .studio-type-card image { color: inherit; }

            preferencesgroup > list.boxed-list { border-radius: 14px; }
            headerbar { padding-left: 4px; padding-right: 4px; }
            headerbar .title { font-weight: 700; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _icon_badge(self, icon_name, css_class):
        badge = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        badge.add_css_class("studio-row-icon")
        badge.add_css_class(css_class)
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        image.set_hexpand(True)
        image.set_vexpand(True)
        badge.append(image)
        return badge

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self):
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_title(False)
        new_btn = Gtk.Button(tooltip_text="New Command")
        new_btn.add_css_class("suggested-action")
        new_btn.add_css_class("pill")
        new_btn_content = Adw.ButtonContent(icon_name="list-add-symbolic", label="New")
        new_btn.set_child(new_btn_content)
        new_btn.connect("clicked", self._on_new_command)
        add_press_bounce(new_btn)
        header.pack_end(new_btn)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.add_css_class("studio-sidebar-scroll")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        content.set_margin_top(16)
        content.set_margin_bottom(20)
        content.set_margin_start(14)
        content.set_margin_end(14)

        self.commands_group = Adw.PreferencesGroup(title="Your Commands")
        content.append(self.commands_group)

        self.rec_group = Adw.PreferencesGroup(
            title="Recommendations",
            description="One-tap ideas to get started",
        )
        content.append(self.rec_group)
        self._populate_recommendations()

        scrolled.set_child(content)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrap.append(header)
        wrap.append(scrolled)
        return Adw.NavigationPage(title="My Commands", child=wrap)

    # ---------------------------------------------------------------- placeholder
    def _build_placeholder(self):
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle(title="Nexa Studio"))

        status = Adw.StatusPage(
            title="Select or create a command",
            description="Choose a command on the left, or tap New to teach Nexa something.",
            icon_name="star-new-symbolic",
        )
        status.add_css_class("studio-empty-page")
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(status)
        return Adw.NavigationPage(title="Command", child=toolbar_view)

    # ---------------------------------------------------------------- editor
    def _build_editor(self):
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle(title="Nexa Studio"))

        self.delete_btn = Gtk.Button(label="Delete")
        self.delete_btn.add_css_class("destructive-action")
        self.delete_btn.add_css_class("flat")
        self.delete_btn.set_visible(False)
        self.delete_btn.connect("clicked", self._on_delete_clicked)
        add_press_bounce(self.delete_btn)
        header.pack_start(self.delete_btn)

        save_btn = Gtk.Button(label="Save Command")
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", self._on_save_clicked)
        add_press_bounce(save_btn)
        header.pack_end(save_btn)

        self.test_btn = Gtk.Button()
        self.test_btn.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Test"))
        self.test_btn.add_css_class("pill")
        self.test_btn.set_tooltip_text("Run this command right now to check it works, without leaving Studio")
        self.test_btn.connect("clicked", self._on_test_clicked)
        add_press_bounce(self.test_btn)
        header.pack_end(self.test_btn)

        page = Adw.PreferencesPage()

        # --- Hero: identical glow-icon + staggered fade-in as NexaWindow's
        # empty-chat hero page (main.py _build_hero_page / _animate_hero_entrance).
        hero = Adw.PreferencesGroup()
        hero_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        hero_box.set_halign(Gtk.Align.CENTER)
        hero_box.set_margin_top(6)
        hero_box.set_margin_bottom(20)

        self.hero_glow = Gtk.Box()
        self.hero_glow.add_css_class("nexa-hero-glow")
        self.hero_glow.set_size_request(140, 140)
        self.hero_glow.set_halign(Gtk.Align.CENTER)
        self.hero_glow.set_valign(Gtk.Align.CENTER)

        hero_icon = Gtk.Image.new_from_icon_name("star-new-symbolic")
        hero_icon.set_pixel_size(56)
        hero_icon.add_css_class("nexa-hero-icon")
        hero_icon.set_halign(Gtk.Align.CENTER)
        hero_icon.set_valign(Gtk.Align.CENTER)

        icon_overlay = Gtk.Overlay()
        icon_overlay.set_child(self.hero_glow)
        icon_overlay.add_overlay(hero_icon)
        icon_overlay.set_halign(Gtk.Align.CENTER)
        hero_box.append(icon_overlay)

        self.hero_title_lbl = Gtk.Label(label="Teach Nexa something new")
        self.hero_title_lbl.add_css_class("title-2")
        self.hero_title_lbl.set_opacity(0)
        hero_box.append(self.hero_title_lbl)

        self.hero_sub_lbl = Gtk.Label(
            label="Set a trigger phrase, then decide what Nexa does when she hears it.",
            wrap=True, justify=Gtk.Justification.CENTER,
        )
        self.hero_sub_lbl.add_css_class("dim-label")
        self.hero_sub_lbl.set_opacity(0)
        hero_box.append(self.hero_sub_lbl)

        self.hero_box = hero_box
        hero_box.set_opacity(0)
        hero.add(hero_box)
        page.add(hero)

        trigger_group = Adw.PreferencesGroup(title="Trigger")
        self.trigger_row = Adw.EntryRow(title="When you say or type…")
        trigger_group.add(self.trigger_row)
        page.add(trigger_group)

        # --- Colorful type picker: two gradient cards (blue "Say", green
        # "Run") the user taps, replacing the old plain ComboRow.
        type_group = Adw.PreferencesGroup(title="Nexa should")
        cards_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        cards_row.set_margin_top(4)
        cards_row.set_margin_bottom(8)

        self.say_card = self._build_type_card(
            "chat-message-new-symbolic", "Say a response",
            "Speak or show text back", "say",
        )
        self.run_card = self._build_type_card(
            "utilities-terminal-symbolic", "Run a command",
            "Execute a terminal command", "run",
        )
        cards_row.append(self.say_card)
        cards_row.append(self.run_card)
        type_group.add(cards_row)
        page.add(type_group)

        action_group = Adw.PreferencesGroup(title="Action")
        self.response_row = Adw.EntryRow(title="Response text")
        action_group.add(self.response_row)

        self.command_row = Adw.EntryRow(title="Terminal command")
        self.command_row.set_visible(False)
        action_group.add(self.command_row)

        self.speak_output_row = Adw.SwitchRow(
            title="Speak the command's output",
            subtitle="Off just runs it silently and replies \u201cDone.\u201d",
        )
        self.speak_output_row.set_active(True)
        self.speak_output_row.set_visible(False)
        action_group.add(self.speak_output_row)

        page.add(action_group)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(page)
        self.editor_page = Adw.NavigationPage(title="Command", child=toolbar_view)
        self._refresh_type_cards()
        return self.editor_page

    def _build_type_card(self, icon_name, title, subtitle, kind):
        card = Gtk.Button()
        card.add_css_class("studio-type-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_halign(Gtk.Align.START)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(22)
        inner.append(icon)
        title_lbl = Gtk.Label(label=title, halign=Gtk.Align.START)
        title_lbl.add_css_class("studio-type-card-title")
        inner.append(title_lbl)
        sub_lbl = Gtk.Label(label=subtitle, halign=Gtk.Align.START, wrap=True)
        sub_lbl.add_css_class("studio-type-card-sub")
        inner.append(sub_lbl)
        card.set_child(inner)
        card.connect("clicked", lambda _b, k=kind: self._on_type_card_clicked(k))
        add_press_bounce(card, min_scale=0.96)
        return card

    def _on_type_card_clicked(self, kind):
        self._action_type = kind
        self._refresh_type_cards()
        is_run = kind == "run"
        self.response_row.set_visible(not is_run)
        self.command_row.set_visible(is_run)
        self.speak_output_row.set_visible(is_run)

    def _refresh_type_cards(self):
        say_selected = self._action_type == "say"
        for cls in ("studio-type-card-say", "studio-type-card-unselected"):
            self.say_card.remove_css_class(cls)
            self.run_card.remove_css_class(cls)
        self.say_card.add_css_class("studio-type-card-say" if say_selected else "studio-type-card-unselected")
        self.run_card.add_css_class("studio-type-card-run" if not say_selected else "studio-type-card-unselected")

    # ---------------------------------------------------------------- hero animation
    def _animate_hero_entrance(self):
        """Staggered fade-in for the editor hero, matching NexaWindow's
        _animate_hero_entrance easing/timing exactly (main.py)."""
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

        fade_text(self.hero_title_lbl, 200)
        fade_text(self.hero_sub_lbl, 350)

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

    # ---------------------------------------------------------------- logic
    def _clear_editor(self):
        self._editing_id = None
        self.trigger_row.set_text("")
        self.response_row.set_text("")
        self.command_row.set_text("")
        self._action_type = "say"
        self._refresh_type_cards()
        self.response_row.set_visible(True)
        self.command_row.set_visible(False)
        self.speak_output_row.set_visible(False)
        self.speak_output_row.set_active(True)
        self.delete_btn.set_visible(False)
        self.hero_title_lbl.set_label("Teach Nexa something new")
        self.hero_sub_lbl.set_label("Set a trigger phrase, then decide what Nexa does when she hears it.")

    def _load_into_editor(self, cmd):
        self._editing_id = cmd["id"]
        self.trigger_row.set_text(cmd.get("trigger", ""))
        is_run = cmd.get("type") == "run"
        self._action_type = "run" if is_run else "say"
        self._refresh_type_cards()
        self.response_row.set_text(cmd.get("response", ""))
        self.response_row.set_visible(not is_run)
        self.command_row.set_text(cmd.get("shell_command", ""))
        self.command_row.set_visible(is_run)
        self.speak_output_row.set_active(cmd.get("speak_output", True))
        self.speak_output_row.set_visible(is_run)
        self.delete_btn.set_visible(True)
        self.hero_title_lbl.set_label("Edit this command")
        self.hero_sub_lbl.set_label("Update the trigger or what Nexa does, then save.")
        self.split.set_content(self.editor_page)

    def _on_new_command(self, _btn):
        self._clear_editor()
        self.split.set_content(self.editor_page)

    def _refresh_list(self):
        for row in self._command_rows:
            self.commands_group.remove(row)
        self._command_rows = []

        commands = studio.load_commands()
        if not commands:
            empty = Adw.ActionRow(title="No commands yet", subtitle="Tap New to create your first one")
            empty.add_prefix(self._icon_badge("star-new-symbolic", "studio-row-icon-say"))
            self.commands_group.add(empty)
            self._command_rows.append(empty)
            return
        for cmd in commands:
            subtitle = cmd.get("response") if cmd.get("type") == "say" else cmd.get("shell_command")
            # Escaped: a user-typed trigger/response containing "&"/"<"/">"
            # would otherwise be invalid Pango markup and silently blank
            # the row's title entirely, the same bug as the Settings
            # Command Access rows (see main.py for the full writeup).
            row = Adw.ActionRow(title=html.escape(cmd.get("trigger", "")), subtitle=html.escape(subtitle or ""))
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            is_say = cmd.get("type") == "say"
            icon = "chat-message-new-symbolic" if is_say else "utilities-terminal-symbolic"
            row.add_prefix(self._icon_badge(icon, "studio-row-icon-say" if is_say else "studio-row-icon-run"))
            row.set_activatable(True)
            row.connect("activated", lambda _r, c=cmd: self._load_into_editor(c))
            self.commands_group.add(row)
            self._command_rows.append(row)

    def _populate_recommendations(self):
        for rec in studio.RECOMMENDATIONS:
            subtitle = rec.get("response") if rec.get("type") == "say" else rec.get("shell_command")
            row = Adw.ActionRow(title=html.escape(rec["trigger"]), subtitle=html.escape(subtitle or ""))
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            row.add_css_class("studio-rec-row")
            add_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add")
            add_btn.connect("clicked", lambda _b, r=rec: self._add_recommendation(r))
            add_press_bounce(add_btn)
            row.add_suffix(add_btn)
            self.rec_group.add(row)

    def _add_recommendation(self, rec):
        commands = studio.load_commands()
        cmd = studio.new_command(
            rec["trigger"], rec["type"],
            response=rec.get("response", ""),
            shell_command=rec.get("shell_command", ""),
        )
        commands.append(cmd)
        studio.save_commands(commands)
        self._refresh_list()

    def _on_test_clicked(self, _btn):
        """Runs whatever's currently in the editor (saved or not) right now,
        so you can check it works without switching to the chat window."""
        trigger = self.trigger_row.get_text().strip()
        if not trigger:
            self.trigger_row.add_css_class("error")
            self.toast_overlay.add_toast(Adw.Toast(title="Add a trigger phrase first"))
            return
        self.trigger_row.remove_css_class("error")

        if self._engine is None:
            self.toast_overlay.add_toast(Adw.Toast(title="Test isn't available right now"))
            return

        is_run = self._action_type == "run"
        self.test_btn.set_sensitive(False)

        if not is_run:
            result = self.response_row.get_text().strip() or "Okay."
            self._show_test_result(result)
            return

        shell_command = self.command_row.get_text().strip()
        if not shell_command:
            self.command_row.add_css_class("error")
            self.toast_overlay.add_toast(Adw.Toast(title="Add a terminal command first"))
            self.test_btn.set_sensitive(True)
            return
        self.command_row.remove_css_class("error")
        speak_output = self.speak_output_row.get_active()

        def run_in_background():
            if speak_output:
                output = self._engine._run_host_cmd_output(["bash", "-c", shell_command])
                result = output if output else "Done."
            else:
                ok = self._engine._run_host_cmd(["bash", "-c", shell_command])
                result = "Done." if ok else "I couldn't run that command."
            GLib.idle_add(self._show_test_result, result)

        import threading
        threading.Thread(target=run_in_background, daemon=True).start()

    def _show_test_result(self, result):
        self.test_btn.set_sensitive(True)
        preview = result if len(result) <= 90 else result[:87] + "..."
        self.toast_overlay.add_toast(Adw.Toast(title=preview, timeout=6))
        if self._voice is not None:
            try:
                self._voice.speak(result)
            except Exception:
                pass
        return False

    def _on_save_clicked(self, _btn):
        trigger = self.trigger_row.get_text().strip()
        if not trigger:
            self.trigger_row.add_css_class("error")
            return
        self.trigger_row.remove_css_class("error")

        is_run = self._action_type == "run"
        cmd_type = "run" if is_run else "say"
        response = self.response_row.get_text()
        shell_command = self.command_row.get_text()
        speak_output = self.speak_output_row.get_active()

        commands = studio.load_commands()
        if self._editing_id:
            for c in commands:
                if c["id"] == self._editing_id:
                    c.update(trigger=trigger, type=cmd_type, response=response,
                              shell_command=shell_command, speak_output=speak_output)
                    break
        else:
            commands.append(studio.new_command(
                trigger, cmd_type, response=response,
                shell_command=shell_command, speak_output=speak_output,
            ))
        studio.save_commands(commands)
        self._refresh_list()
        self._clear_editor()

    def _on_delete_clicked(self, _btn):
        if not self._editing_id:
            return
        commands = [c for c in studio.load_commands() if c["id"] != self._editing_id]
        studio.save_commands(commands)
        self._refresh_list()
        self._clear_editor()
        self.split.set_content(self.placeholder_page)

    def _on_close_request(self, *_a):
        self._on_close_return_home()
        return False
