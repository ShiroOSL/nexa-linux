#!/usr/bin/env python3
"""Global keyboard shortcut for Nexa's Quick Command pill, via the
org.freedesktop.portal.GlobalShortcuts XDG portal.

This is the correct, sandbox-safe way for a Flatpak app to register a
system-wide hotkey (unlike writing directly to GNOME's
org.gnome.settings-daemon.plugins.media-keys schema, which the Flatpak
dconf confinement blocks for paths outside the app's own namespace).

The first time the hotkey is enabled, the compositor shows a small system
dialog letting the user pick/confirm the key combination. GNOME's portal
implementation (mutter, 45+) is required for this to work.
"""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SHORTCUT_ID = "quick-command"


class GlobalShortcutManager:
    def __init__(self, on_activate):
        self.on_activate = on_activate
        self.session_handle = None
        self.bound = False
        self.connection = None
        self._activated_sub_id = None
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._activated_sub_id = self.connection.signal_subscribe(
                None, GLOBAL_SHORTCUTS_IFACE, "Activated", PORTAL_OBJECT_PATH,
                None, Gio.DBusSignalFlags.NONE, self._on_activated, None,
            )
        except Exception as e:
            print(f"[GlobalShortcut] failed to connect to session bus: {e}")

    def _request_path(self, token):
        unique_name = self.connection.get_unique_name()[1:].replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{unique_name}/{token}"

    def _subscribe_response(self, request_path, callback):
        sub_id_box = [None]

        def on_response(connection, sender, path, iface, signal, params, user_data):
            self.connection.signal_unsubscribe(sub_id_box[0])
            response_code, results = params.unpack()
            callback(response_code, results)

        sub_id_box[0] = self.connection.signal_subscribe(
            None, REQUEST_IFACE, "Response", request_path, None,
            Gio.DBusSignalFlags.NONE, on_response, None,
        )

    def enable(self):
        """Idempotent: does nothing if already bound or in progress."""
        if not self.connection or self.session_handle:
            return
        try:
            session_token = f"nexa_session_{GLib.get_monotonic_time()}"
            request_token = f"nexa_req_{GLib.get_monotonic_time()}"
            request_path = self._request_path(request_token)
            self._subscribe_response(request_path, self._on_create_session_response)

            options = {
                "handle_token": GLib.Variant("s", request_token),
                "session_handle_token": GLib.Variant("s", session_token),
            }
            self.connection.call_sync(
                PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, GLOBAL_SHORTCUTS_IFACE, "CreateSession",
                GLib.Variant("(a{sv})", (options,)),
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
        except Exception as e:
            print(f"[GlobalShortcut] enable() failed (portal not available?): {e}")

    def _on_create_session_response(self, response_code, results):
        if response_code != 0:
            print("[GlobalShortcut] CreateSession was cancelled or failed")
            return
        self.session_handle = results.get("session_handle")
        if not self.session_handle:
            print("[GlobalShortcut] CreateSession returned no session_handle")
            return
        self._bind_shortcuts()

    def _bind_shortcuts(self):
        try:
            request_token = f"nexa_bind_{GLib.get_monotonic_time()}"
            request_path = self._request_path(request_token)
            self._subscribe_response(request_path, self._on_bind_response)

            shortcut_props = {
                "description": GLib.Variant("s", "Open Nexa Quick Command"),
                "preferred_trigger": GLib.Variant("s", "<Super><Alt>n"),
            }
            shortcuts = [(SHORTCUT_ID, shortcut_props)]
            options = {"handle_token": GLib.Variant("s", request_token)}
            self.connection.call_sync(
                PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, GLOBAL_SHORTCUTS_IFACE, "BindShortcuts",
                GLib.Variant("(oa(sa{sv})sa{sv})", (self.session_handle, shortcuts, "", options)),
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
        except Exception as e:
            print(f"[GlobalShortcut] BindShortcuts call failed: {e}")

    def _on_bind_response(self, response_code, _results):
        if response_code == 0:
            self.bound = True
            print("[GlobalShortcut] Quick Command hotkey bound successfully")
        else:
            print("[GlobalShortcut] BindShortcuts was cancelled or failed")

    def disable(self):
        if self.session_handle and self.connection:
            try:
                self.connection.call_sync(
                    PORTAL_BUS_NAME, self.session_handle, SESSION_IFACE, "Close",
                    None, None, Gio.DBusCallFlags.NONE, -1, None,
                )
            except Exception as e:
                print(f"[GlobalShortcut] error closing session: {e}")
        self.session_handle = None
        self.bound = False

    def _on_activated(self, connection, sender, path, iface, signal, params, user_data):
        session_handle, shortcut_id, _timestamp, _options = params.unpack()
        if self.session_handle and session_handle == self.session_handle and shortcut_id == SHORTCUT_ID:
            self.on_activate()
