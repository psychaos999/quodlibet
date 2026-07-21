# Copyright 2005 Joe Wreschnig, Michael Urman
#       2021-22 Nick Boultbee
#           2026 Quod Libet contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


from gi.repository import Adw, Gtk, GLib

from quodlibet import _
from quodlibet import util
from quodlibet.qltk import get_top_parent
from quodlibet.qltk.icons import Icons
from quodlibet.util import escape
from quodlibet.fsn import fsn2text, path2fsn


def _strip_mnemonic(label: str) -> str:
    return label.replace("_", "") if label else label


class Message:
    """Adw.AlertDialog-based message with Gtk.MessageDialog-compatible API.

    Call sites use ``run()`` (blocking nested main loop) and response IDs as
    ``Gtk.ResponseType`` values. Internally this is an ``Adw.AlertDialog``.
    """

    def __init__(
        self,
        kind,
        parent: Gtk.Widget | None,
        title: str,
        description: str,
        buttons: Gtk.ButtonsType = Gtk.ButtonsType.OK,
        escape_desc: bool = True,
    ):
        self._parent = get_top_parent(parent)
        body = escape(description) if escape_desc else description
        self._dialog = Adw.AlertDialog(heading=title, body=body)
        self._dialog.set_body_use_markup(True)
        self._dialog.set_heading_use_markup(False)
        # Map MessageType to nothing visible beyond heading — AlertDialog has no icons
        self._response_map: dict[str, int] = {}
        self._default_response = Gtk.ResponseType.OK
        self._add_buttons_type(buttons)

    def _add_buttons_type(self, buttons: Gtk.ButtonsType) -> None:
        if buttons == Gtk.ButtonsType.NONE:
            return
        if buttons == Gtk.ButtonsType.OK:
            self.add_button(_("_OK"), Gtk.ResponseType.OK)
            self.set_default_response(Gtk.ResponseType.OK)
        elif buttons == Gtk.ButtonsType.CLOSE:
            self.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
            self.set_default_response(Gtk.ResponseType.CLOSE)
        elif buttons == Gtk.ButtonsType.CANCEL:
            self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
            self.set_default_response(Gtk.ResponseType.CANCEL)
        elif buttons == Gtk.ButtonsType.YES_NO:
            self.add_button(_("_No"), Gtk.ResponseType.NO)
            self.add_button(_("_Yes"), Gtk.ResponseType.YES)
            self.set_default_response(Gtk.ResponseType.YES)
        elif buttons == Gtk.ButtonsType.OK_CANCEL:
            self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
            self.add_button(_("_OK"), Gtk.ResponseType.OK)
            self.set_default_response(Gtk.ResponseType.OK)

    def _response_id_str(self, response_id: int) -> str:
        return f"r{int(response_id)}"

    def add_button(self, label: str, response_id: int):
        key = self._response_id_str(response_id)
        self._response_map[key] = response_id
        self._dialog.add_response(key, _strip_mnemonic(label))
        return

    def add_icon_button(self, label: str, icon_name: str, response_id: int):
        # AlertDialog has no per-response icons; keep API compatibility
        return self.add_button(label, response_id)

    def set_default_response(self, response_id: int) -> None:
        self._default_response = response_id
        key = self._response_id_str(response_id)
        if key in self._response_map:
            self._dialog.set_default_response(key)
            self._dialog.set_close_response(key)

    def set_response_appearance_destructive(self, response_id: int) -> None:
        key = self._response_id_str(response_id)
        self._dialog.set_response_appearance(key, Adw.ResponseAppearance.DESTRUCTIVE)

    def set_response_appearance_suggested(self, response_id: int) -> None:
        key = self._response_id_str(response_id)
        self._dialog.set_response_appearance(key, Adw.ResponseAppearance.SUGGESTED)

    def run(self, destroy: bool = True):
        """Blocking show; returns a Gtk.ResponseType-compatible int."""
        result = [self._default_response]
        done = [False]

        def on_response(_dialog, response: str):
            result[0] = self._response_map.get(response, self._default_response)
            done[0] = True

        handler = self._dialog.connect("response", on_response)
        # Ensure Escape/close maps to a known response
        if self._response_map:
            close_key = self._response_id_str(Gtk.ResponseType.CANCEL)
            if close_key not in self._response_map:
                close_key = self._response_id_str(self._default_response)
            if close_key not in self._response_map:
                close_key = next(iter(self._response_map))
            self._dialog.set_close_response(close_key)
        self._dialog.present(self._parent)

        ctx = GLib.MainContext.default()
        while not done[0]:
            ctx.iteration(True)

        self._dialog.disconnect(handler)
        if destroy:
            try:
                self._dialog.force_close()
            except Exception:
                pass
        return result[0]

    def destroy(self):
        try:
            self._dialog.force_close()
        except Exception:
            self._dialog.close()

    def present(self, parent=None):
        self._dialog.present(parent if parent is not None else self._parent)

    def __getattr__(self, name):
        return getattr(self._dialog, name)


class CancelRevertSave(Message):
    def __init__(self, parent):
        title = _("Discard tag changes?")
        description = _(
            "Tags have been changed but not saved. Save these "
            "files, or revert and discard changes?"
        )
        super().__init__(
            Gtk.MessageType.WARNING,
            parent,
            title,
            description,
            buttons=Gtk.ButtonsType.NONE,
        )
        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.add_button(_("_Revert"), Gtk.ResponseType.NO)
        self.add_button(_("_Save"), Gtk.ResponseType.YES)
        self.set_default_response(Gtk.ResponseType.NO)
        self.set_response_appearance_suggested(Gtk.ResponseType.YES)
        self.set_response_appearance_destructive(Gtk.ResponseType.NO)


class ErrorMessage(Message):
    """Like Message, but uses an error-indicating picture."""

    def __init__(self, *args, **kwargs):
        super().__init__(Gtk.MessageType.ERROR, *args, **kwargs)


class WarningMessage(Message):
    """Like Message, but uses a warning-indicating picture."""

    def __init__(self, *args, **kwargs):
        super().__init__(Gtk.MessageType.WARNING, *args, **kwargs)


class ConfirmationPrompt(WarningMessage):
    """Dialog to confirm actions, given a parent, title, description, and
    OK-button text"""

    RESPONSE_INVOKE = Gtk.ResponseType.YES

    def __init__(
        self,
        parent,
        title,
        description,
        ok_button_text,
        ok_button_icon=Icons.SYSTEM_RUN,
    ):
        super().__init__(parent, title, description, buttons=Gtk.ButtonsType.NONE)

        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.add_icon_button(ok_button_text, ok_button_icon, self.RESPONSE_INVOKE)
        self.set_default_response(Gtk.ResponseType.CANCEL)
        self.set_response_appearance_destructive(self.RESPONSE_INVOKE)


class ConfirmFileReplace(WarningMessage):
    RESPONSE_REPLACE = 1

    def __init__(self, parent, path):
        title = _("File exists")
        fn_format = util.bold(fsn2text(path2fsn(path)))
        description = _("Replace %(file-name)s?") % {"file-name": fn_format}

        super().__init__(
            parent, title, description, buttons=Gtk.ButtonsType.NONE, escape_desc=False
        )

        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.add_icon_button(
            _("_Replace File"), Icons.DOCUMENT_SAVE, self.RESPONSE_REPLACE
        )
        self.set_default_response(Gtk.ResponseType.CANCEL)
        self.set_response_appearance_destructive(self.RESPONSE_REPLACE)
