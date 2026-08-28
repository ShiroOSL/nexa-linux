#!/usr/bin/env python3
"""System tray icon for Nexa Assistant.

Implements the StatusNotifierItem + com.canonical.dbusmenu D-Bus specs
directly (no libappindicator dependency needed). Requires a tray host on
the session bus (GNOME's "AppIndicator and KStatusNotifierItem Support"
extension, KDE, etc.) -- if none is present, registration silently fails
and Nexa just runs without a tray icon.

Exposes three menu items:
  1. "Show Nexa"      -> on_show_app()
  2. "Quick Command"  -> on_quick_command()
  3. "Quit"           -> on_quit()
Left-clicking the icon itself also triggers on_show_app() via Activate().
"""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib

SNI_XML = """
<node>
  <interface name='org.kde.StatusNotifierItem'>
    <property name='Category' type='s' access='read'/>
    <property name='Id' type='s' access='read'/>
    <property name='Title' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='WindowId' type='i' access='read'/>
    <property name='IconName' type='s' access='read'/>
    <property name='IconThemePath' type='s' access='read'/>
    <property name='ItemIsMenu' type='b' access='read'/>
    <property name='Menu' type='o' access='read'/>
    <method name='Activate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='SecondaryActivate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='ContextMenu'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='Scroll'>
      <arg type='i' name='delta' direction='in'/>
      <arg type='s' name='orientation' direction='in'/>
    </method>
    <signal name='NewIcon'/>
    <signal name='NewTitle'/>
    <signal name='NewStatus'>
      <arg type='s' name='status'/>
    </signal>
  </interface>
</node>
"""

DBUSMENU_XML = """
<node>
  <interface name='com.canonical.dbusmenu'>
    <property name='Version' type='u' access='read'/>
    <property name='TextDirection' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconThemePath' type='as' access='read'/>
    <method name='GetLayout'>
      <arg type='i' name='parentId' direction='in'/>
      <arg type='i' name='recursionDepth' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='u' name='revision' direction='out'/>
      <arg type='(ia{sv}av)' name='layout' direction='out'/>
    </method>
    <method name='GetGroupProperties'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='a(ia{sv})' name='properties' direction='out'/>
    </method>
    <method name='GetProperty'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='name' direction='in'/>
      <arg type='v' name='value' direction='out'/>
    </method>
    <method name='Event'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='eventId' direction='in'/>
      <arg type='v' name='data' direction='in'/>
      <arg type='u' name='timestamp' direction='in'/>
    </method>
    <method name='EventGroup'>
      <arg type='a(isvu)' name='events' direction='in'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <method name='AboutToShow'>
      <arg type='i' name='id' direction='in'/>
      <arg type='b' name='needUpdate' direction='out'/>
    </method>
    <method name='AboutToShowGroup'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='ai' name='updatesNeeded' direction='out'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <signal name='ItemsPropertiesUpdated'>
      <arg type='a(ia{sv})' name='updatedProps'/>
      <arg type='a(ias)' name='removedProps'/>
    </signal>
    <signal name='LayoutUpdated'>
      <arg type='u' name='revision'/>
      <arg type='i' name='parent'/>
    </signal>
  </interface>
</node>
"""


class TrayManager:
    SNI_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"

    def __init__(self, on_show_app, on_quick_command, on_quit, icon_name="org.nexa.Assistant"):
        self.on_show_app = on_show_app
        self.on_quick_command = on_quick_command
        self.on_quit = on_quit
        self.icon_name = icon_name
        self._menu_items = {1: "Show Nexa", 2: "Quick Command", 3: "Quit"}
        self.connection = None
        self._sni_reg_id = None
        self._menu_reg_id = None
        try:
            # Use a PRIVATE connection rather than the shared process-wide one
            # (Gio.bus_get_sync). Most StatusNotifierWatcher implementations
            # only notice an item is gone when its D-Bus connection actually
            # closes (via NameOwnerChanged) -- unregistering the object alone
            # on a long-lived shared connection leaves a stale icon behind.
            # A private connection lets unregister() close cleanly and make
            # the icon disappear immediately.
            bus_address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            self.connection = Gio.DBusConnection.new_for_address_sync(
                bus_address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None,
            )
            self._register_sni()
            self._register_menu()
            self._register_with_watcher()
        except Exception as e:
            print(f"[TrayManager] failed to initialize system tray: {e}")

    def unregister(self):
        """Removes the tray icon immediately (used when the user turns the
        setting off without restarting Nexa). Closing the private connection
        is what actually makes the watcher drop the item."""
        if not self.connection:
            return
        try:
            if self._sni_reg_id is not None:
                self.connection.unregister_object(self._sni_reg_id)
            if self._menu_reg_id is not None:
                self.connection.unregister_object(self._menu_reg_id)
            self.connection.close_sync(None)
        except Exception as e:
            print(f"[TrayManager] error while unregistering tray icon: {e}")
        finally:
            self.connection = None

    # --- registration ------------------------------------------------------------
    def _register_sni(self):
        node_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        self._sni_reg_id = self.connection.register_object(
            self.SNI_PATH, node_info.interfaces[0],
            self._sni_method_call, self._sni_get_property, None,
        )

    def _register_menu(self):
        node_info = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML)
        self._menu_reg_id = self.connection.register_object(
            self.MENU_PATH, node_info.interfaces[0],
            self._menu_method_call, self._menu_get_property, None,
        )

    def _register_with_watcher(self):
        try:
            self.connection.call_sync(
                "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self.connection.get_unique_name(),)),
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
        except Exception as e:
            print(f"[TrayManager] no StatusNotifierWatcher available (no tray host running?): {e}")

    # --- org.kde.StatusNotifierItem ------------------------------------------------
    def _sni_get_property(self, connection, sender, object_path, interface, property_name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "org.nexa.Assistant"),
            "Title": GLib.Variant("s", "Nexa Assistant"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", self.MENU_PATH),
        }
        return values.get(property_name)

    def _sni_method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        if method_name == "Activate":
            self.on_show_app()
        # SecondaryActivate / ContextMenu / Scroll: no-op, just acknowledge.
        invocation.return_value(None)

    # --- com.canonical.dbusmenu -----------------------------------------------------
    def _menu_get_property(self, connection, sender, object_path, interface, property_name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(property_name)

    def _item_props(self, item_id):
        return {
            "label": GLib.Variant("s", self._menu_items.get(item_id, "")),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }

    def _menu_method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        if method_name == "GetLayout":
            children = [
                GLib.Variant("(ia{sv}av)", (item_id, self._item_props(item_id), []))
                for item_id in self._menu_items
            ]
            root_props = {"children-display": GLib.Variant("s", "submenu")}
            result = GLib.Variant("(u(ia{sv}av))", (1, (0, root_props, children)))
            invocation.return_value(result)

        elif method_name == "GetGroupProperties":
            ids, _prop_names = parameters.unpack()
            rows = [(iid, self._item_props(iid)) for iid in ids if iid in self._menu_items]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))

        elif method_name == "GetProperty":
            item_id, name = parameters.unpack()
            props = self._item_props(item_id)
            invocation.return_value(GLib.Variant("(v)", (props.get(name, GLib.Variant("s", "")),)))

        elif method_name == "Event":
            item_id, event_id, _data, _timestamp = parameters.unpack()
            if event_id == "clicked":
                if item_id == 1:
                    self.on_show_app()
                elif item_id == 2:
                    self.on_quick_command()
                elif item_id == 3:
                    self.on_quit()
            invocation.return_value(None)

        elif method_name == "EventGroup":
            invocation.return_value(GLib.Variant("(ai)", ([],)))

        elif method_name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))

        elif method_name == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))

        else:
            invocation.return_value(None)
