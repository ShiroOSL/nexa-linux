#!/usr/bin/env python3
"""org.nexa.CommandRegistry -- the "Connect Nexa" D-Bus service.

Lets other apps and GNOME Shell extensions register a set of voice
commands with Nexa: each app calls RegisterApp() with a list of trigger
phrases and what should happen when Nexa hears them (either call one of
the app's own D-Bus methods, or just have Nexa say a canned response).

GNOME Shell extensions can call this immediately, no permissions needed
(unsandboxed). A sandboxed Flatpak app needs one line in its own manifest:
  --talk-name=org.nexa.CommandRegistry

Nexa owns this bus name itself (see --own-name in org.nexa.Assistant.json).
"""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib

import nexa_external_commands as registry

BUS_NAME = "org.nexa.CommandRegistry"
OBJECT_PATH = "/org/nexa/CommandRegistry"

IFACE_XML = """
<node>
  <interface name='org.nexa.CommandRegistry'>
    <method name='RegisterApp'>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='app_name' direction='in'/>
      <arg type='aa{sv}' name='commands' direction='in'/>
      <arg type='b' name='success' direction='out'/>
    </method>
    <method name='UnregisterApp'>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='b' name='success' direction='out'/>
    </method>
    <method name='ListRegisteredApps'>
      <arg type='a{ss}' name='apps' direction='out'/>
    </method>
  </interface>
</node>
"""


class CommandRegistryService:
    def __init__(self, on_registry_changed=None, on_register_request=None):
        """on_registry_changed: optional callback fired (with no args) after
        any successful RegisterApp/UnregisterApp call, so the Settings UI
        can refresh its "Connected Apps" list live.

        on_register_request: optional callback fired as
        (app_id, app_name, commands, decide) for every RegisterApp call.
        `decide` is a one-shot function the caller must invoke with True
        (Allow) or False (Cancel) once the user has answered the consent
        prompt -- this is what actually persists the commands and replies
        over D-Bus. If this callback isn't set, RegisterApp auto-approves
        (used by the standalone/background CLI service, if any)."""
        self.on_registry_changed = on_registry_changed
        self.on_register_request = on_register_request
        self.connection = None
        self._reg_id = None
        self._own_name_id = None
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node_info = Gio.DBusNodeInfo.new_for_xml(IFACE_XML)
            self._reg_id = self.connection.register_object(
                OBJECT_PATH, node_info.interfaces[0],
                self._method_call, None, None,
            )
            self._own_name_id = Gio.bus_own_name_on_connection(
                self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None,
            )
        except Exception as e:
            print(f"[CommandRegistryService] failed to start: {e}")

    def _method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        if method_name == "RegisterApp":
            app_id, app_name, commands = parameters.unpack()

            def finish(ok):
                invocation.return_value(GLib.Variant("(b)", (ok,)))
                if ok and self.on_registry_changed:
                    self.on_registry_changed()

            if self.on_register_request:
                # Defer: the invocation is completed later by `finish`, once
                # the user answers the Allow/Cancel consent prompt.
                def decide(allowed):
                    ok = registry.register_app(app_id, app_name, commands) if allowed else False
                    finish(ok)
                self.on_register_request(app_id, app_name, commands, decide)
            else:
                finish(registry.register_app(app_id, app_name, commands))

        elif method_name == "UnregisterApp":
            (app_id,) = parameters.unpack()
            ok = registry.unregister_app(app_id)
            invocation.return_value(GLib.Variant("(b)", (ok,)))
            if ok and self.on_registry_changed:
                self.on_registry_changed()

        elif method_name == "ListRegisteredApps":
            apps = registry.list_apps()
            invocation.return_value(GLib.Variant("(a{ss})", (apps,)))

        else:
            invocation.return_value(None)
