import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib


class DBusManager:
    """Sends native notifications and opens URIs via the desktop portal.

    Uses GLib's Gio D-Bus bindings (bundled with PyGObject / GTK) instead
    of the dbus_next PyPI package, so it works inside the Flatpak sandbox
    without any extra dependencies.
    """

    def __init__(self):
        self.connection = None

    def initialize(self):
        """Connect to the session bus."""
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            print("D-Bus session bus connected.")
        except GLib.Error as e:
            print(f"Failed to connect to D-Bus: {e}")

    def send_notification(self, title, body, icon="org.nexa.Assistant"):
        """Send a native system notification via D-Bus."""
        if not self.connection:
            self.initialize()
        if not self.connection:
            return

        try:
            self.connection.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Nexa Assistant",  # app_name
                        0,                 # replaces_id
                        icon,              # app_icon
                        title,             # summary
                        body,              # body
                        [],                # actions
                        {},                # hints
                        -1,                # expire_timeout (-1 for default)
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            print(f"Notification sent: {title}")
        except GLib.Error as e:
            print(f"Error sending notification: {e}")

    def launch_portal_app(self, uri):
        """Securely launch an application through the OpenURI portal."""
        if not self.connection:
            self.initialize()
        if not self.connection:
            return

        try:
            self.connection.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.OpenURI",
                "OpenURI",
                GLib.Variant("(ssa{sv})", ("", uri, {})),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            print(f"Launched URI via portal: {uri}")
        except GLib.Error as e:
            print(f"Error launching portal app: {e}")
