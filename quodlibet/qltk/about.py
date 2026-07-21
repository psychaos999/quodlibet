# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2026 Quod Libet contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Adw, Gtk, GLib

import quodlibet
from quodlibet import const


class AboutDialog:
    """Application about dialog (libadwaita).

    ``Adw.AboutDialog`` is a final type, so this is a thin wrapper that
    forwards to an internal instance. Call sites may use ``run()`` for a
    blocking show (nested main-loop compatibility with the old
    ``Gtk.AboutDialog.run()``), or ``present(parent)`` for the native async API.
    """

    def __init__(self, parent, app):
        self._parent = parent
        self._dialog = Adw.AboutDialog(
            application_name=app.name,
            application_icon=app.icon_name,
            version=quodlibet.get_build_description(),
            developers=list(const.AUTHORS),
            artists=list(const.ARTISTS),
            comments=app.description,
            license_type=Gtk.License.GPL_2_0,
            translator_credits="\n".join(const.TRANSLATORS),
            website=const.WEBSITE,
            copyright=f"{const.COPYRIGHT}\n{const.SUPPORT_EMAIL}",
        )
        if hasattr(self._dialog, "set_issue_url"):
            self._dialog.set_issue_url(const.WEBSITE)

    def present(self, parent=None):
        self._dialog.present(parent if parent is not None else self._parent)

    def run(self):
        """Blocking show, matching the old Gtk.AboutDialog.run() call sites."""
        closed = [False]

        def on_closed(*_args):
            closed[0] = True

        handler_id = self._dialog.connect("closed", on_closed)
        self._dialog.present(self._parent)

        context = GLib.MainContext.default()
        while not closed[0]:
            context.iteration(True)

        self._dialog.disconnect(handler_id)
        return Gtk.ResponseType.CLOSE

    def destroy(self):
        """Compatibility for tests / callers that call destroy()."""
        try:
            self._dialog.force_close()
        except Exception:
            self._dialog.close()

    def __getattr__(self, name):
        return getattr(self._dialog, name)
