# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os

from gi.repository import GLib, Gtk, GObject, Pango, Gio
from quodlibet.fsn import fsnative

from quodlibet import ngettext, _
from quodlibet import config
from quodlibet import formats
from quodlibet import qltk
from quodlibet import app

from quodlibet.qltk.appwindow import AppWindow
from quodlibet.formats import AudioFileError
from quodlibet.plugins import PluginManager
from quodlibet.qltk.delete import trash_files
from quodlibet.qltk.edittags import EditTags
from quodlibet.qltk.filesel import MainFileSelector
from quodlibet.qltk.pluginwin import PluginWindow
from quodlibet.qltk.renamefiles import RenameFiles
from quodlibet.qltk.tagsfrompath import TagsFromPath
from quodlibet.qltk.tracknumbers import TrackNumbers
from quodlibet.qltk.menubutton import MenuButton
from quodlibet.qltk.about import AboutDialog
from quodlibet.qltk.songsmenu import SongsMenuPluginHandler
from quodlibet.qltk.x import (
    Align,
    ConfigRHPaned,
    SymbolicIconImage,
)
from quodlibet.qltk.window import PersistentWindowMixin, Window
from quodlibet.qltk.msg import CancelRevertSave
from quodlibet.qltk.notif import StatusBar, TaskController
from quodlibet.qltk.prefs import PreferencesWindow as QLPreferencesWindow
from quodlibet.qltk import Icons
from quodlibet.util import trash as trash_util
from quodlibet.util.i18n import numeric_phrase
from quodlibet.util.path import mtime, normalize_path
from quodlibet.util import connect_obj, connect_destroy, format_int_locale
from quodlibet.update import UpdateDialog


class ExFalsoWindow(Window, PersistentWindowMixin, AppWindow):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    pm = SongsMenuPluginHandler()

    @classmethod
    def init_plugins(cls):
        PluginManager.instance.register_handler(cls.pm)

    def __init__(self, library, dir=None):
        super().__init__(dialog=False)
        self.set_title("Ex Falso")
        self.set_default_size(750, 475)
        self.enable_window_tracking("exfalso")

        self.__library = library

        hp = ConfigRHPaned("memory", "exfalso_paned_position", 1.0)
        hp.set_border_width(0)
        hp.set_position(250)
        hp.show()
        self.add(hp)

        vb = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
        )

        bbox = Gtk.Box(spacing=6)

        def prefs_cb(*args):
            window = PreferencesWindow(self)
            window.present()

        def plugin_window_cb(*args):
            window = PluginWindow(self)
            window.present()

        def about_cb(*args):
            about = AboutDialog(self, app)
            about.run()

        def update_cb(*args):
            d = UpdateDialog(self)
            d.run()

        action_group = Gio.SimpleActionGroup()
        menu_model = Gio.Menu()

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", about_cb)
        action_group.add_action(about_action)
        menu_model.append(_("About"), "appmenu.about")

        update_action = Gio.SimpleAction.new("check-updates", None)
        update_action.connect("activate", update_cb)
        action_group.add_action(update_action)
        menu_model.append(_("Check for Updates…"), "appmenu.check-updates")

        tools = Gio.Menu()
        plugin_action = Gio.SimpleAction.new("plugins", None)
        plugin_action.connect("activate", plugin_window_cb)
        action_group.add_action(plugin_action)
        tools.append(_("Plugins"), "appmenu.plugins")

        prefs_action = Gio.SimpleAction.new("preferences", None)
        prefs_action.connect("activate", prefs_cb)
        action_group.add_action(prefs_action)
        tools.append(_("Preferences"), "appmenu.preferences")
        menu_model.append_section(None, tools)

        menu = Gtk.PopoverMenu.new_from_model(menu_model)
        menu.insert_action_group("appmenu", action_group)

        menu_button = MenuButton(
            SymbolicIconImage(Icons.OPEN_MENU, Gtk.IconSize.LARGE),
            arrow=True,
            down=False,
        )
        menu_button.set_menu(menu)
        bbox.prepend(menu_button)

        statusbox = StatusBarBox()
        self.statusbar = statusbox.statusbar

        bbox.prepend(statusbox)

        l = Gtk.Label()
        l.set_xalign(1.0)
        l.set_yalign(0.5)
        l.set_ellipsize(Pango.EllipsizeMode.END)
        bbox.append(l)

        self._fs = fs = MainFileSelector()

        vb.append(fs)
        vb.append(Align(bbox, border=6))
        vb.show_all()

        hp.set_start_child(vb)
        hp.set_resize_start_child(True)
        hp.set_shrink_start_child(False)

        nb = qltk.Notebook()
        nb.props.scrollable = True
        nb.show()
        for Page in [EditTags, TagsFromPath, RenameFiles, TrackNumbers]:
            page = Page(self, self.__library)
            page.show()
            nb.append_page(page)
        hp.set_end_child(nb)
        hp.set_resize_end_child(True)
        hp.set_shrink_end_child(False)
        fs.connect("changed", self.__changed, l)
        if dir:
            fs.go_to(dir)

        connect_destroy(self.__library, "changed", self.__library_changed, fs)

        self.__save = None
        connect_obj(self, "changed", self.set_pending, None)
        for sw in (fs.get_start_child(), fs.get_end_child()):
            sw.get_child().connect(
                "button-press-event", self.__pre_selection_changed, fs, nb
            )
            sw.get_child().connect("focus", self.__pre_selection_changed, fs, nb)
        fs.get_end_child().get_child().connect("popup-menu", self.__popup_menu, fs)
        self.emit("changed", [])

        self.get_child().show()

        # macOS native menu integration is not currently supported in GTK4;
        # keep a placeholder so set_as_osx_window() has something to pass.
        self._dummy_osx_menu_bar = Gtk.Box()
        self._dummy_osx_menu_bar.set_visible(False)
        vb.prepend(self._dummy_osx_menu_bar)

    def __library_changed(self, library, songs, fs):
        fs.rescan()

    def set_as_osx_window(self, osx_app):
        osx_app.set_menu_bar(self._dummy_osx_menu_bar)

    def get_is_persistent(self):
        return False

    def open_file(self, filename):
        assert isinstance(filename, fsnative)

        if not os.path.isdir(filename):
            return False

        self._fs.go_to(filename)
        return None

    def set_pending(self, button, *excess):
        self.__save = button

    def __pre_selection_changed(self, view, event, fs, nb):
        if self.__save:
            resp = CancelRevertSave(self).run()
            if resp == Gtk.ResponseType.YES:
                self.__save.clicked()
            elif resp == Gtk.ResponseType.NO:
                fs.rescan()
            else:
                nb.grab_focus()
                return True  # cancel or closed
        return None

    def __popup_menu(self, view, fs):
        filenames = [
            normalize_path(f, canonicalise=True) for f in fs.get_selected_paths()
        ]
        songs = [s for f in filenames if (s := self.__library.get(f))]
        menu = self._compose_file_popup(songs, filenames, fs)
        return view.popup_menu(menu, 0, GLib.CURRENT_TIME)

    def _compose_file_popup(self, songs, filenames, fs):
        actions = Gio.SimpleActionGroup()
        model = Gio.Menu()

        trash_action = Gio.SimpleAction.new("trash", None)
        trash_action.connect("activate", lambda *_a: self.__delete(filenames, fs))
        actions.add_action(trash_action)
        top = Gio.Menu()
        label = _("Move to Trash") if trash_util.use_trash() else _("Delete")
        top.append(label, "ef.trash")
        model.append_section(None, top)

        if songs:
            plugin_item = self.pm.build_menu_item(
                self.__library,
                songs,
                actions,
                "ef",
                lambda: self,
            )
            if plugin_item is not None:
                plugins = Gio.Menu()
                plugins.append_item(plugin_item)
                model.append_section(None, plugins)

        menu = Gtk.PopoverMenu.new_from_model(model)
        menu.insert_action_group("ef", actions)
        return menu

    def __delete(self, paths, fs):
        trash_files(self, paths)
        fs.rescan()

    def __changed(self, selector, selection, label):
        model, rows = selection.get_selected_rows()
        files = []

        if len(rows) < 2:
            count = len(model or [])
        else:
            count = len(rows)
        label.set_text(numeric_phrase("%d song", "%d songs", count))

        for row in rows:
            filename = model[row][0]
            if not os.path.exists(filename):
                pass
            elif filename in self.__library:
                song = self.__library[filename]
                if song("~#mtime") + 1.0 < mtime(filename):
                    try:
                        song.reload()
                    except AudioFileError:
                        pass
                files.append(song)
            else:
                files.append(formats.MusicFile(filename))
        files = list(filter(None, files))
        if len(files) == 0:
            self.set_title("Ex Falso")
        elif len(files) == 1:
            self.set_title("{} - Ex Falso".format(files[0].comma("title")))
        else:
            params = {
                "title": files[0].comma("title"),
                "count": format_int_locale(len(files) - 1),
            }
            self.set_title(
                "%s - Ex Falso"
                % (
                    ngettext(
                        "%(title)s and %(count)s more",
                        "%(title)s and %(count)s more",
                        len(files) - 1,
                    )
                    % params
                )
            )
        self.__library.add(files)
        self.emit("changed", files)


class PreferencesWindow(QLPreferencesWindow):
    def __init__(self, parent):
        if self.is_not_unique():
            return
        super().__init__(parent, all_pages=False)
        # Seems nicer when there's only one page
        self.set_resizable(True)
        self.set_title(_("Ex Falso Preferences"))

    def __destroy(self):
        config.save()


class StatusBarBox(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=6)
        self.statusbar = StatusBar(TaskController.default_instance)
        self.append(self.statusbar)
