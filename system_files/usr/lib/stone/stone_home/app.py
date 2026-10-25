"""STONE Home: a full-screen WebKitGTK window inside Steam Game Mode (Gamescope).

  stone://home/<file>         the interface, from /usr/share/stone-home (fixed file set)
  stone://art/<appid>/<kind>  game artwork resolved by library.art_path()
  window.webkit.messageHandlers.stone.postMessage({method, args}) -> Promise
                              requests to Host (host.py), answered asynchronously
  window.__stoneEvent(name, data)
                              events pushed from stoned and the system

The page cannot open other URLs, load remote content or read files: navigation is
limited to stone://, and the scheme handler serves only the files listed in UI_DIR and
artwork for a validated AppID. Runs as the console user, never as root.
"""

import json
import mimetypes
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("JavaScriptCore", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, JavaScriptCore, WebKit  # noqa: E402

from . import shortcuts  # noqa: E402
from .host import Host  # noqa: E402

UI_DIR = os.environ.get("STONE_HOME_UI", "/usr/share/stone-home")
APP_ID = "org.stonemachine.Home"
SELFTEST_TIMEOUT_S = 60
VOID = "#0a0a0b"          # the interface's background (--void); the window is never see-through

# Logged to home.log (console messages go to stdout): whether Web Audio can run here.
AUDIO_PROBE = """
(function(){try{var C=window.AudioContext||window.webkitAudioContext;
if(!C){console.warn('[stone] audio: no AudioContext');return;}
var c=new C();console.info('[stone] audio: created, state='+c.state+' rate='+c.sampleRate);
c.resume().then(function(){console.info('[stone] audio: resumed, state='+c.state);setTimeout(function(){c.close();},500);},
function(e){console.warn('[stone] audio: resume failed '+e);});
}catch(e){console.warn('[stone] audio: '+e);}})();
"""


def _ui_file(name):
    """Only regular files directly in UI_DIR or UI_DIR/fonts; no other path is reachable."""
    sub, _, base = name.rpartition("/")
    if sub not in ("", "fonts") or not base or base.startswith("."):
        return None
    d = os.path.join(UI_DIR, sub) if sub else UI_DIR
    try:
        ok = base in os.listdir(d)
    except OSError:
        return None
    p = os.path.join(d, base)
    return p if ok and os.path.isfile(p) else None


class HomeWindow:
    def __init__(self, app, selftest=False):
        self.app = app
        self.selftest = selftest
        self.exit_code = 0
        self.ready = False
        self.queue = []
        self.host = Host(self.emit)
        self.host.restart = self._restart
        if selftest:
            self.host.selftest = self._selftest_done

        ctx = WebKit.WebContext.get_default()
        ctx.register_uri_scheme("stone", self._scheme)
        sec = ctx.get_security_manager()
        sec.register_uri_scheme_as_secure("stone")
        sec.register_uri_scheme_as_cors_enabled("stone")

        ucm = WebKit.UserContentManager()
        ucm.register_script_message_handler_with_reply("stone", None)
        ucm.connect("script-message-with-reply-received::stone", self._on_message)
        ucm.add_script(WebKit.UserScript.new(AUDIO_PROBE, WebKit.UserContentInjectedFrames.TOP_FRAME,
                                             WebKit.UserScriptInjectionTime.END, None, None))
        kw = {"user_content_manager": ucm}
        try:
            # Interface sounds start without a click: the controller's buttons are not "user
            # gestures" for WebKit, so a stricter policy would keep Web Audio suspended forever.
            kw["website_policies"] = WebKit.WebsitePolicies(autoplay=WebKit.AutoplayPolicy.ALLOW)
        except (AttributeError, TypeError) as e:
            print(f"stone-home: autoplay policy not set ({e})", file=sys.stderr, flush=True)
        self.view = WebKit.WebView(**kw)
        bg = Gdk.RGBA()
        bg.parse(VOID)
        self.view.set_background_color(bg)
        s = self.view.get_settings()
        s.set_enable_developer_extras(bool(os.environ.get("STONE_HOME_DEVTOOLS")))
        s.set_media_playback_requires_user_gesture(False)
        for name, value in (("set_enable_webaudio", True), ("set_enable_media", True),
                            ("set_enable_write_console_messages_to_stdout", True)):
            if hasattr(s, name):
                getattr(s, name)(value)
        s.set_javascript_can_open_windows_automatically(False)
        s.set_allow_file_access_from_file_urls(False)
        s.set_allow_universal_access_from_file_urls(False)
        s.set_enable_back_forward_navigation_gestures(False)
        self.view.connect("decide-policy", self._policy)
        self.view.connect("context-menu", lambda *a: True)
        self.view.connect("load-changed", self._load_changed)
        self.view.connect("web-process-terminated", self._crashed)

        css = Gtk.CssProvider()
        rule = f"window, window.background {{ background-color: {VOID}; }}"
        if hasattr(css, "load_from_string"):
            css.load_from_string(rule)
        else:
            css.load_from_data(rule.encode(), -1)
        disp = Gdk.Display.get_default()
        if disp is not None:
            Gtk.StyleContext.add_provider_for_display(disp, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.win = Gtk.ApplicationWindow(application=app, title="STONE")
        self.win.set_child(self.view)
        size = os.environ.get("STONE_HOME_SIZE", "")
        w, _, h = size.partition("x")
        if not selftest and w.isdigit() and h.isdigit() and 320 <= int(w) <= 3840 and 240 <= int(h) <= 2160:
            # Light drawing mode: a smaller window Gamescope scales to the screen (fewer pixels
            # to draw in software). The page scales itself to the window.
            self.win.set_decorated(False)
            self.win.set_default_size(int(w), int(h))
        else:
            self.win.set_default_size(1920, 1080)
            if not selftest:
                self.win.fullscreen()
        self.win.present()
        self.view.load_uri("stone://home/index.html" + ("#selftest" if selftest else ""))
        if selftest:
            GLib.timeout_add_seconds(SELFTEST_TIMEOUT_S, self._selftest_timeout)

    # ---- page -> host --------------------------------------------------------------------
    def _on_message(self, ucm, value, reply):
        try:
            req = json.loads(value.to_json(0))
        except (ValueError, GLib.Error):
            req = None

        def done(result=None, code=None, message=""):
            payload = {"ok": code is None, "result": result} if code is None else \
                {"ok": False, "code": code, "message": message}
            reply.return_value(_js(value, payload))
        self.host.handle(req, done)
        return True

    # ---- host -> page --------------------------------------------------------------------
    def emit(self, name, data):
        script = f"window.__stoneEvent && window.__stoneEvent({json.dumps(name)}, {json.dumps(data)})"
        if not self.ready:
            self.queue.append(script)
            return
        self.view.evaluate_javascript(script, -1, None, None, None, None, None)

    def _load_changed(self, view, ev):
        if ev == WebKit.LoadEvent.FINISHED:
            self.ready = True
            for s in self.queue:
                self.view.evaluate_javascript(s, -1, None, None, None, None, None)
            self.queue.clear()

    # ---- scheme ----------------------------------------------------------------------------
    def _scheme(self, request):
        uri = request.get_uri()
        rest = uri[len("stone://"):].split("?")[0].split("#")[0]
        host, _, path = rest.partition("/")
        f = None
        if host == "home":
            f = _ui_file(path or "index.html")
        elif host == "art":
            appid, _, kind = path.partition("/")
            f = self.host.art_path(appid, kind)
        if not f:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "not found", Gio.IOErrorEnum.NOT_FOUND))
            return
        try:
            stream = Gio.File.new_for_path(f).read(None)
            size = os.path.getsize(f)
        except (GLib.Error, OSError):
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "not found", Gio.IOErrorEnum.NOT_FOUND))
            return
        mime = mimetypes.guess_type(f)[0] or "application/octet-stream"
        request.finish(stream, size, mime)

    def _policy(self, view, decision, kind):
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith("stone://home/"):
                decision.ignore()
                return True
        if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def _restart(self):
        """Same process (Steam keeps tracking it), started again through /usr/bin/stone-home so a
        new drawing mode applies."""
        launcher = "/usr/bin/stone-home"
        if not os.access(launcher, os.X_OK):
            print("stone-home: restart not possible here", file=sys.stderr, flush=True)
            return
        print("[stone] restarting", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(launcher, [launcher])

    def _crashed(self, view, reason):
        # The web process died: reload the interface. stoned holds every state that matters.
        print(f"stone-home: web process terminated ({reason}); reloading", file=sys.stderr, flush=True)
        self.ready = False
        GLib.timeout_add(500, lambda: (self.view.load_uri("stone://home/index.html"), False)[1])

    # ---- self-test (CI and first hardware check) -----------------------------------------
    def _selftest_done(self, result):
        print(json.dumps({"selftest": result}, ensure_ascii=False), flush=True)
        self.exit_code = 0 if isinstance(result, dict) and result.get("pass") else 1
        GLib.timeout_add(100, lambda: (self.app.quit(), False)[1])

    def _selftest_timeout(self):
        print(json.dumps({"selftest": {"pass": False, "reason": "timeout"}}), flush=True)
        self.exit_code = 1
        self.app.quit()
        return False


def _js(value, payload):
    return JavaScriptCore.Value.new_from_json(value.get_context(), json.dumps(payload))


def _startup_log():
    """One line per start in home.log: how STONE Home was started and how it draws."""
    ids = None
    try:
        ids = shortcuts.record_ids()
    except Exception as e:  # noqa: BLE001  (diagnostics must never stop the interface)
        print(f"stone-home: could not record Steam ids: {e}", file=sys.stderr, flush=True)
    disp = Gdk.Display.get_default()
    keys = ("DISPLAY", "XDG_CURRENT_DESKTOP", "STONE_HOME_RENDER", "STONE_HOME_SIZE", "GDK_BACKEND", "GSK_RENDERER",
            "GSK_DEBUG", "WEBKIT_DISABLE_COMPOSITING_MODE", "WEBKIT_DISABLE_DMABUF_RENDERER")
    env = " ".join(f"{k}={os.environ.get(k, '-')}" for k in keys)
    print(f"[stone] start: steam_ids={ids} display={type(disp).__name__ if disp else None} "
          f"webkit={WebKit.get_major_version()}.{WebKit.get_minor_version()}.{WebKit.get_micro_version()} {env}",
          flush=True)


def main(argv):
    selftest = "--selftest" in argv
    app = Gtk.Application(application_id=APP_ID if not selftest else APP_ID + ".SelfTest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE if selftest else Gio.ApplicationFlags.DEFAULT_FLAGS)
    holder = {}

    def activate(a):
        if "w" not in holder and not selftest:
            _startup_log()
        if "w" not in holder:
            holder["w"] = HomeWindow(a, selftest)
        else:
            holder["w"].win.present()
    app.connect("activate", activate)
    app.run([argv[0] if argv else "stone-home"])
    return holder["w"].exit_code if "w" in holder else 1
