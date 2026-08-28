"""Nexa Studio: a companion window for creating custom voice/text commands."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

import nexa_studio_commands as studio


class NexaStudioWindow(Adw.ApplicationWindow):
    def __init__(self, application, on_close_return_home, engine=None, voice=None):
        super().__init__(application=application)
        self.set_default_size(920, 640)
        self.set_title("Nexa Studio")
        self._on_close_return_home = on_close_return_home
        self._engine = engine
        self._voice = voice
        self._editing_id = None
        self._command_rows = []

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

    # ---------------------------------------------------------------- styling
    def _load_css(self):
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .studio-hero {
                background: linear-gradient(135deg, alpha(#3584e4, 0.20), alpha(#9141ac, 0.12));
                border-radius: 18px;
                padding: 22px;
            }
            .studio-hero-icon {
                background: alpha(#3584e4, 0.20);
                border-radius: 999px;
                min-width: 52px;
                min-height: 52px;
            }
            .studio-hero-icon image { color: #3584e4; -gtk-icon-size: 26px; }
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
            preferencesgroup > list.boxed-list {
                border-radius: 14px;
            }
            headerbar {
                padding-left: 4px;
                padding-right: 4px;
            }
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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_title(False)
        new_btn = Gtk.Button(tooltip_text="New Command")
        new_btn.add_css_class("suggested-action")
        new_btn.add_css_class("pill")
        new_btn_content = Adw.ButtonContent(icon_name="list-add-symbolic", label="New")
        new_btn.set_child(new_btn_content)
        new_btn.connect("clicked", self._on_new_command)
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
        header.pack_start(self.delete_btn)

        save_btn = Gtk.Button(label="Save Command")
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(save_btn)

        self.test_btn = Gtk.Button()
        self.test_btn.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Test"))
        self.test_btn.add_css_class("pill")
        self.test_btn.set_tooltip_text("Run this command right now to check it works, without leaving Studio")
        self.test_btn.connect("clicked", self._on_test_clicked)
        header.pack_end(self.test_btn)

        page = Adw.PreferencesPage()

        hero = Adw.PreferencesGroup()
        hero_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hero_box.add_css_class("studio-hero")
        icon_wrap = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        icon_wrap.add_css_class("studio-hero-icon")
        hero_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        hero_icon.set_halign(Gtk.Align.CENTER)
        hero_icon.set_valign(Gtk.Align.CENTER)
        hero_icon.set_hexpand(True)
        hero_icon.set_vexpand(True)
        icon_wrap.append(hero_icon)
        hero_box.append(icon_wrap)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        title_lbl = Gtk.Label(label="Teach Nexa something new", xalign=0)
        title_lbl.add_css_class("title-3")
        sub_lbl = Gtk.Label(
            label="Set a trigger phrase, then decide what Nexa does when she hears it.",
            xalign=0, wrap=True,
        )
        sub_lbl.add_css_class("dim-label")
        text_box.append(title_lbl)
        text_box.append(sub_lbl)
        hero_box.append(text_box)
        hero.add(hero_box)
        page.add(hero)

        trigger_group = Adw.PreferencesGroup(title="Trigger")
        self.trigger_row = Adw.EntryRow(title="When you say or type…")
        trigger_group.add(self.trigger_row)
        page.add(trigger_group)

        type_group = Adw.PreferencesGroup(title="Action")
        self.type_row = Adw.ComboRow(title="Nexa should")
        self.type_row.set_model(Gtk.StringList.new(["Say a response", "Run a terminal command"]))
        self.type_row.connect("notify::selected", self._on_type_changed)
        type_group.add(self.type_row)

        self.response_row = Adw.EntryRow(title="Response text")
        type_group.add(self.response_row)

        self.command_row = Adw.EntryRow(title="Terminal command")
        self.command_row.set_visible(False)
        type_group.add(self.command_row)

        self.speak_output_row = Adw.SwitchRow(
            title="Speak the command's output",
            subtitle="Off just runs it silently and replies \u201cDone.\u201d",
        )
        self.speak_output_row.set_active(True)
        self.speak_output_row.set_visible(False)
        type_group.add(self.speak_output_row)

        page.add(type_group)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(page)
        self.editor_page = Adw.NavigationPage(title="Command", child=toolbar_view)
        return self.editor_page

    # ---------------------------------------------------------------- logic
    def _on_type_changed(self, row, _param):
        is_run = row.get_selected() == 1
        self.response_row.set_visible(not is_run)
        self.command_row.set_visible(is_run)
        self.speak_output_row.set_visible(is_run)

    def _clear_editor(self):
        self._editing_id = None
        self.trigger_row.set_text("")
        self.response_row.set_text("")
        self.command_row.set_text("")
        self.type_row.set_selected(0)
        self.speak_output_row.set_active(True)
        self.delete_btn.set_visible(False)

    def _load_into_editor(self, cmd):
        self._editing_id = cmd["id"]
        self.trigger_row.set_text(cmd.get("trigger", ""))
        is_run = cmd.get("type") == "run"
        self.type_row.set_selected(1 if is_run else 0)
        self.response_row.set_text(cmd.get("response", ""))
        self.command_row.set_text(cmd.get("shell_command", ""))
        self.speak_output_row.set_active(cmd.get("speak_output", True))
        self.delete_btn.set_visible(True)
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
            row = Adw.ActionRow(title=cmd.get("trigger", ""), subtitle=subtitle or "")
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
            row = Adw.ActionRow(title=rec["trigger"], subtitle=subtitle or "")
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            row.add_css_class("studio-rec-row")
            add_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add")
            add_btn.connect("clicked", lambda _b, r=rec: self._add_recommendation(r))
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

        is_run = self.type_row.get_selected() == 1
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

        is_run = self.type_row.get_selected() == 1
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
