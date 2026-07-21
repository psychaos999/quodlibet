# Copyright 2012,2014 Christoph Reiter
#           2014,2017 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import sys
import os

from gi.repository import Gtk, Gdk, Adw

from quodlibet import config
from quodlibet.qltk import get_top_parent, is_wayland, is_accel
from quodlibet.qltk.x import Button
from quodlibet.util import DeferredSignal, print_d, print_w, InstanceTracker
from quodlibet.util import connect_obj, connect_destroy


def on_first_map(window, callback, *args, **kwargs):
    """Calls callback when the passed Gtk.Window is first visible
    on screen or it already is.
    """

    assert isinstance(window, Gtk.Window)

    if window.get_mapped():
        callback(*args, **kwargs)
        return False

    id_ = [0]

    def on_map(*otherargs):
        window.disconnect(id_[0])
        callback(*args, **kwargs)

    id_[0] = window.connect("map", on_map)

    return False


def should_use_header_bar():
    # GTK4 / libadwaita: prefer header bars for dialogs where the platform wants them
    settings = Gtk.Settings.get_default()
    if not settings:
        return True
    return settings.get_property("gtk-dialogs-use-header")


class Dialog(Gtk.Dialog):
    """A Gtk.Dialog subclass which supports the use_header_bar property
    for all Gtk versions and will ignore it if header bars shouldn't be
    used according to GtkSettings.
    """

    def __init__(self, *args, **kwargs):
        if not should_use_header_bar():
            kwargs.pop("use_header_bar", None)
        super().__init__(*args, **kwargs)

    def get_titlebar(self):
        # GTK4: get_titlebar() always exists
        return super().get_titlebar()

    def set_default_size(self, width, height):
        # GTK4: No size adjustments needed for headerbar
        if not self.get_titlebar():
            # In case we don't use a headerbar we have to add an additional
            # row of buttons in the content box. To get roughly the same
            # content height make the window a bit taller.
            if height != -1:
                height += 20
        super().set_default_size(width, height)

    def add_icon_button(self, label, icon_name, response_id):
        """Like add_button() but allows to pass an icon name"""

        button = Button(label, icon_name)
        button.show()
        self.add_action_widget(button, response_id)
        return button


class Window(Adw.Window):
    """Base window class that keeps track of all window instances.

    Built on ``Adw.Window`` so libadwaita styling, breakpoints, and
    adaptive layout work without changing call sites. Content goes through
    ``set_content`` (with ``set_child`` / ``add`` as compatibility aliases).

    All active instances can be accessed through Window.windows.
    By defining dialog=True as a kwarg binds Escape to close, otherwise
    ^W will close the window.
    """

    windows: list[Gtk.Window] = []
    _preven_inital_show = False

    def __init__(self, *args, **kwargs):
        self._header_bar = None
        self._is_dialog = kwargs.pop("dialog", True)
        # Adw.Window / Gtk.Window no longer take type_hint
        kwargs.pop("type_hint", None)
        super().__init__(*args, **kwargs)
        type(self).windows.append(self)
        if self._is_dialog:
            # Modal is the only way to center the window on the parent
            # with wayland atm
            if is_wayland():
                self.set_modal(True)
        self.set_destroy_with_parent(True)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

    # --- content / child API (Adw.Window uses set_content) ---

    def set_child(self, child):
        """Compatibility: map set_child → Adw.Window.set_content."""
        self.set_content(child)

    def get_child(self):
        """Compatibility: map get_child → Adw.Window.get_content."""
        return self.get_content()

    def add(self, child):
        """Compatibility: map add → Adw.Window.set_content."""
        self.set_content(child)

    def destroy(self):
        windows = type(self).windows
        if self in windows:
            windows.remove(self)
        if isinstance(self, InstanceTracker):
            self._deregister_instance()
        # Adw/Gtk4: prefer close(); keep destroy if present for tests
        if hasattr(super(), "destroy"):
            try:
                super().destroy()
                return
            except Exception:
                pass
        self.close()

    def _on_key_pressed(self, controller, keyval, keycode, state):
        # Build a minimal stand-in so is_accel / callers keep working
        class _Key:
            def __init__(self, keyval, hardware_keycode, state):
                self.keyval = keyval
                self.hardware_keycode = hardware_keycode
                self.state = state

        event = _Key(keyval, keycode, state)

        if (self._is_dialog and is_accel(event, "Escape")) or (
            not self._is_dialog and is_accel(event, "<Primary>w")
        ):
            # Do not close the window if we edit a Gtk.CellRendererText.
            # Focus the treeview instead.
            focus = self.get_focus()
            if isinstance(focus, Gtk.Entry) and isinstance(
                focus.get_parent(), Gtk.TreeView
            ):
                focus.get_parent().grab_focus()
                return Gdk.EVENT_PROPAGATE
            self.close()
            return Gdk.EVENT_STOP

        if not self._is_dialog and is_accel(event, "F11"):
            self.toggle_fullscreen()
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_key_press(self, widget, event):
        """Legacy GTK3 entry point kept for callers that still use it."""
        return self._on_key_pressed(
            None, event.keyval, getattr(event, "hardware_keycode", 0), event.state
        )

    def toggle_fullscreen(self):
        """Toggle the fullscreen mode of the window depending on its current
        state. If the windows isn't realized it will switch to fullscreen
        when it does.
        """

        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def set_default_size(self, width, height):
        # GTK4: No size adjustments needed for headerbar
        super().set_default_size(width, height)

    def show_now(self):
        """Show and present the window immediately."""
        self.show()
        self.present()

    def use_header_bar(self):
        """Try to use a headerbar, returns the widget
        or None if headerbars are disabled (under xfce for example)
        """

        assert not self._header_bar

        if not should_use_header_bar():
            return False

        # Prefer Adw.HeaderBar for correct libadwaita spacing / styling
        header_bar = Adw.HeaderBar()
        header_bar.set_show_end_title_buttons(True)
        header_bar.show()
        old_title = self.get_title()
        self.set_titlebar(header_bar)
        if old_title is not None:
            self.set_title(old_title)
        self._header_bar = header_bar
        self.set_default_size(*self.get_default_size())
        return header_bar

    def has_close_button(self):
        """Returns True in case we are sure that the window decorations include
        a close button.
        """

        if not self._is_dialog:
            return True

        if os.name == "nt":
            return True

        if sys.platform == "darwin":
            return True

        if self._header_bar is not None:
            if isinstance(self._header_bar, Adw.HeaderBar):
                return self._header_bar.get_show_end_title_buttons()
            return self._header_bar.get_show_title_buttons()

        return True

    def present(self):
        """A version of present that also works if not called from an event
        handler (there is no active input event).
        See https://bugzilla.gnome.org/show_bug.cgi?id=688830
        """

        # In GTK4, just use the standard present() - it works correctly
        super().present()

    def set_transient_for(self, parent):
        """Set a parent for the window.

        In case parent=None, fall back to the main window.

        """

        is_toplevel = parent and isinstance(parent, Gtk.Window)

        if parent is None or not is_toplevel:
            if parent:
                print_w(f"Not a toplevel window set for: {self!r}")
            from quodlibet import app

            parent = app.window
        super().set_transient_for(parent)

    @classmethod
    def prevent_inital_show(cls, value):
        cls._preven_inital_show = bool(value)

    def show_maybe(self):
        """Show the window, except if prevent_inital_show() was called and
        this is the first time.

        Returns whether the window was shown.
        """

        if not self._preven_inital_show:
            self.show()
        return not self._preven_inital_show


class PersistentWindowMixin:
    """A mixin for saving/restoring window size/maximized state.

    Expects to be mixed into a ``Gtk.Window`` subclass (provides connect,
    maximize, resize, get_width, etc.).
    """

    # Type checkers cannot see Window methods on a bare mixin.
    if False:  # pragma: no cover

        def connect(self, *args, **kwargs): ...
        def get_transient_for(self): ...
        def get_visible(self): ...
        def maximize(self): ...
        def unmaximize(self): ...
        def resize(self, width, height): ...
        def get_width(self): ...
        def get_height(self): ...

    def enable_window_tracking(self, config_prefix, size_suffix=""):
        """Enable tracking/saving of changes and restore size/maximized state.

        config_prefix -- prefix for the config key
                         (prefix_size, prefix_maximized)
        size_suffix -- optional suffix for saving the size. For cases where the
                       window has multiple states with different content sizes.
                       (example: edit tags with one song or multiple)

        """

        self.__state = 0
        self.__name = config_prefix
        self.__size_suffix = size_suffix
        self.__save_size_pos_deferred = DeferredSignal(
            self.__do_save_size_pos, timeout=50, owner=self
        )

        # GTK4: Use property notifications for window size/state changes
        # Track window size changes
        self.connect("notify::default-width", self.__configure_notify)
        self.connect("notify::default-height", self.__configure_notify)
        # Track window state changes
        self.connect("notify::maximized", self.__window_state_notify)
        self.connect("notify::fullscreened", self.__window_state_notify)
        parent = self.get_transient_for()
        if parent:
            connect_destroy(
                parent, "notify::default-width", self.__parent_configure_notify
            )
            connect_destroy(
                parent, "notify::default-height", self.__parent_configure_notify
            )

        self.connect("notify::visible", self.__visible_changed)
        self.__restore_window_state()

    def __visible_changed(self, *args):
        if not self.get_visible():
            # https://bugzilla.gnome.org/show_bug.cgi?id=731287
            # if we restore after hide, mutter will remember for the next show
            # hurray!
            self.__restore_window_state()

    def __restore_window_state(self):
        if not is_wayland():
            self.__restore_state()
        self.__restore_size()

    def __conf(self, name):
        if name == "size":
            name += "_" + self.__size_suffix
        return f"{self.__name}_{name}"

    def __restore_state(self):
        print_d("Restore state")
        if config.getint("memory", self.__conf("maximized"), 0):
            self.maximize()
        else:
            self.unmaximize()

    def __restore_size(self):
        print_d("Restore size")
        value = config.get("memory", self.__conf("size"), "")
        if not value:
            return

        try:
            x, y = map(int, value.split())
        except ValueError:
            return

        # GTK4: get_screen() removed, skip screen size clamping
        # GTK4 handles window sizing constraints automatically
        if x >= 1 and y >= 1:
            self.resize(x, y)

    def __configure_notify(self, *args):
        """GTK4: Handle notify::default-width/height instead of configure-event"""
        self.__save_size_pos_deferred()

    def __parent_configure_notify(self, *args):
        """GTK4: Parent position changes - no-op since positioning not saved"""

    def __window_state_notify(self, window, pspec):
        """GTK4: Handle notify::maximized/fullscreened instead of window-state-event"""
        # Update state tracking
        state = 0
        if window.is_maximized():
            state |= Gdk.WindowState.MAXIMIZED
        if window.is_fullscreen():
            state |= Gdk.WindowState.FULLSCREEN
        self.__state = state

        # Save maximized state
        if state & Gdk.WindowState.WITHDRAWN:
            return
        maximized = int(bool(state & Gdk.WindowState.MAXIMIZED))
        config.set("memory", self.__conf("maximized"), maximized)

    def _should_ignore_state(self):
        if self.__state & Gdk.WindowState.MAXIMIZED:
            return True
        if self.__state & Gdk.WindowState.FULLSCREEN:
            return True
        if not self.get_visible():
            return True
        return False

    def __do_save_size_pos(self):
        if self._should_ignore_state():
            return

        # GTK4: get_size() removed, use get_width()/get_height()
        width, height = self.get_width(), self.get_height()
        value = f"{width} {height}"
        config.set("memory", self.__conf("size"), value)


class _Unique:
    """A mixin for window-like classes to ensure one instance per class.

    Uses a non-mangled registry so each concrete subclass keeps its own
    singleton (double-underscore attributes are shared via the defining class).
    """

    _unique_window = None

    def __new__(cls, *args, **kwargs):
        window = cls.__dict__.get("_unique_window")
        if window is None:
            return super().__new__(cls, *args, **kwargs)
        # Look for widgets in the args, if there is one and it has
        # a new top level window, re-parent and reposition the window.
        widgets = [w for w in args if isinstance(w, Gtk.Widget)]
        if widgets:
            parent = window.get_transient_for()
            new_parent = get_top_parent(widgets[0])
            if parent and new_parent and parent is not new_parent:
                window.set_transient_for(new_parent)
                window.hide()
                window.show()
        window.present()
        return window

    @classmethod
    def is_not_unique(cls):
        """Returns True if a window instance already exists."""
        return bool(cls.__dict__.get("_unique_window"))

    def __init__(self, *args, **kwargs):
        cls = type(self)
        if cls.__dict__.get("_unique_window"):
            return
        cls._unique_window = self
        super().__init__(*args, **kwargs)
        # GTK4/Adw: "destroy" is often a no-op shim; also clear on close/hide
        connect_obj(self, "destroy", self.__clear_unique, self)
        self.connect("close-request", self.__on_close_request)

    def __on_close_request(self, *_args):
        self.__clear_unique()
        return False  # allow default close

    def __clear_unique(self, *_args):
        cls = type(self)
        if cls.__dict__.get("_unique_window") is self:
            cls._unique_window = None

    def destroy(self):
        """Destroy/close and drop the unique registration."""
        self.__clear_unique()
        # Continue MRO so Window.destroy can update Window.windows, etc.
        try:
            super().destroy()
        except Exception:
            close = getattr(self, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class UniqueWindow(_Unique, Window):
    pass
