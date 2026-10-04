#!/usr/bin/env python3
"""
pyReader — an EPUB reader that lives on your desktop.

Renders EPUB 2/3 books (text, images, embedded fonts, MathML equations) with
Qt WebEngine in the same "Midnight Ink" theme as pySysMon and pyQuotes.

- The book to read is set in ~/.config/pyreader/pyreader.toml
  (`book = "~/Books/some-book.epub"`); saving that file opens the book at once.
- The window fills the desktop space left of pySysMon / pyQuotes and is kept
  below all other windows (EWMH hints via ctypes + libX11).
- Reading position, text size and highlights are remembered per book in
  ~/.local/state/pyreader/state.json.

Keys: ← / → turn pages, a / z change the text size, Shift + ← returns to the
page a link or footnote was followed from.
Ctrl + drag selects text (Ctrl+C copies); Ctrl + Shift + drag highlights it,
Ctrl + Shift + click on a highlight removes it.
"""

import ctypes
import ctypes.util
import hashlib
import json
import mimetypes
import os
import posixpath
import signal
import sys
import tomllib
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urldefrag

try:
    import pyphen  # optional: hyphenation for justified text
except ImportError:
    pyphen = None

# Keeping a window below others needs X11 (XWayland on Wayland sessions)
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QBuffer, QFileSystemWatcher, QIODevice, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWebEngineCore import (  # noqa: E402
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
CONFIG_DIR = os.path.expanduser("~/.config/pyreader")
CONFIG_PATH = os.path.join(CONFIG_DIR, "pyreader.toml")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                         "pyreader")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

SCHEME = b"pyreader"
APP_URL = "pyreader://local/app/reader.html"
BG_COLOR = "#191926"

DEFAULT_CONFIG_TEXT = '''# pyReader settings. Save this file and pyReader updates immediately.

# The EPUB to read (~ is allowed), for example:
#   book = "~/Books/The Hobbit.epub"
book = ""

# Pages per window:
#   1      = one page that spans the full width of the window
#   2      = two facing pages, like an open book
#   "auto" = two pages when there is room, otherwise one
pages = "auto"

# Font for body text when the book doesn't choose one.
body_font = "Noto Serif"

# Play the page-turn animation.
animations = true

# Desktop widgets to keep clear of: pyReader fills the space to their left.
avoid_windows = ["pySysMon", "pyQuotes"]
# Space (pixels) to keep free on the right when none of them are running.
reserve_right = 876
# Distance from the screen edges, and from the widgets above.
margin = 24
gap = 16

# Window opacity, 0.0 - 1.0.
opacity = 0.97

# Justify text like a printed book (false = ragged right).
justify = true

# Hyphenate long words (in the book's language) so justified lines have even spacing.
hyphenate = true
'''

DEFAULT_CONFIG = {
    "book": "",
    "body_font": "Noto Serif",
    "animations": True,
    "avoid_windows": ["pySysMon", "pyQuotes"],
    "reserve_right": 876,
    "margin": 24,
    "gap": 16,
    "opacity": 0.97,
    "justify": True,
    "hyphenate": True,
}


def log(msg):
    sys.stderr.write(f"[pyReader] {msg}\n")


# ------------------------------------------------------------------------------
# Config & state
# ------------------------------------------------------------------------------
def load_config():
    """Returns (config, error message or None)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_TEXT)
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg.update(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return cfg, f"Couldn't read {CONFIG_PATH}: {e}"
    return cfg, None


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        if isinstance(state, dict):
            state.setdefault("books", {})
            return state
    except (OSError, ValueError):
        pass
    return {"font_scale": 1.0, "books": {}}


def save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        log(f"Couldn't save state: {e}")


# ------------------------------------------------------------------------------
# EPUB parsing
# ------------------------------------------------------------------------------
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
HTML_TYPES = ("application/xhtml+xml", "text/html", "application/x-dtbook+xml")


class Book:
    def __init__(self, path):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        self.names = set(self.zip.namelist())
        self.lower_names = {n.lower(): n for n in self.names}
        st = os.stat(path)
        self.id = hashlib.sha1(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:12]

        self._check_drm()
        container = ET.fromstring(self.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", NS).get("full-path")
        opf_dir = posixpath.dirname(rootfile)
        opf = ET.fromstring(self.read(rootfile))

        meta = opf.find("opf:metadata", NS)
        def dc(tag):
            return [("".join(e.itertext())).strip() for e in meta.findall(f"dc:{tag}", NS)] if meta is not None else []
        self.title = next((t for t in dc("title") if t), os.path.splitext(os.path.basename(path))[0])
        self.author = ", ".join(a for a in dc("creator") if a)
        self.language = next((t for t in dc("language") if t), "")

        manifest = {}
        for item in opf.findall("opf:manifest/opf:item", NS):
            manifest[item.get("id")] = (self.resolve(opf_dir, item.get("href", "")),
                                        item.get("media-type", ""), item.get("properties", ""))

        spine = opf.find("opf:spine", NS)
        refs = spine.findall("opf:itemref", NS) if spine is not None else []
        linear = [r for r in refs if r.get("linear", "yes") != "no"] or refs
        titles = self._toc_titles(opf_dir, manifest, spine)

        self.chapters = []
        last_title = self.title
        for ref in linear:
            item = manifest.get(ref.get("idref"))
            if not item or item[1] not in HTML_TYPES:
                continue
            href = item[0]
            if href not in self.names and href.lower() not in self.lower_names:
                continue
            last_title = titles.get(href, last_title)
            self.chapters.append({"href": href, "title": last_title,
                                  "size": max(1, self.zip.getinfo(self.real_name(href)).file_size)})
        if not self.chapters:
            raise ValueError("this EPUB has no readable chapters")

    # Obfuscated embedded fonts are allowed (they just fall back to other fonts);
    # any other encryption is DRM.
    FONT_OBFUSCATION = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")

    def _check_drm(self):
        if "META-INF/rights.xml" in self.names:
            raise ValueError("this book is DRM-protected (Adobe ADEPT) and can't be opened")
        if "META-INF/encryption.xml" not in self.names:
            return
        root = ET.fromstring(self.read("META-INF/encryption.xml"))
        for method in root.iter("{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod"):
            if method.get("Algorithm") not in self.FONT_OBFUSCATION:
                raise ValueError("this book is DRM-protected and can't be opened")

    @staticmethod
    def resolve(base_dir, href):
        href = unquote(urldefrag(href)[0])
        return posixpath.normpath(posixpath.join(base_dir, href)) if href else ""

    def real_name(self, name):
        if name in self.names:
            return name
        return self.lower_names[name.lower()]  # KeyError if missing

    def read(self, name):
        return self.zip.read(self.real_name(name))

    def _toc_titles(self, opf_dir, manifest, spine):
        """Map chapter file -> title from the EPUB 3 nav document or EPUB 2 NCX."""
        titles = {}

        def add(base, href, text):
            target = self.resolve(base, href)
            text = " ".join(text.split())
            if target and text and target not in titles:
                titles[target] = text

        try:
            nav = next((v for v in manifest.values() if "nav" in v[2].split()), None)
            if nav:
                root = ET.fromstring(self.read(nav[0]))
                base = posixpath.dirname(nav[0])
                navs = root.iter("{%s}nav" % NS["x"])
                toc = next((n for n in navs if n.get(EPUB_TYPE) == "toc"), None)
                for a in (toc.iter("{%s}a" % NS["x"]) if toc is not None else []):
                    add(base, a.get("href", ""), "".join(a.itertext()))
            ncx_id = spine.get("toc") if spine is not None else None
            if not titles and ncx_id and ncx_id in manifest:
                ncx_path = manifest[ncx_id][0]
                root = ET.fromstring(self.read(ncx_path))
                base = posixpath.dirname(ncx_path)
                for point in root.iter("{%s}navPoint" % NS["ncx"]):
                    label = point.find("ncx:navLabel/ncx:text", NS)
                    content = point.find("ncx:content", NS)
                    if label is not None and content is not None:
                        add(base, content.get("src", ""), label.text or "")
        except (ET.ParseError, KeyError, AttributeError) as e:
            log(f"Table of contents unreadable ({e}); using file order")
        return titles

    def to_js(self):
        return {"id": self.id, "title": self.title, "author": self.author,
                "language": self.language, "chapters": self.chapters}


# ------------------------------------------------------------------------------
# Web plumbing: serve the reader UI and the book's files from pyreader://local/
# ------------------------------------------------------------------------------
MIME_OVERRIDES = {
    ".xhtml": "application/xhtml+xml", ".xht": "application/xhtml+xml",
    ".html": "text/html", ".htm": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".svg": "image/svg+xml", ".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ncx": "application/xml", ".opf": "application/xml",
    ".webp": "image/webp", ".avif": "image/avif",
}


def mime_for(name):
    ext = os.path.splitext(name)[1].lower()
    return MIME_OVERRIDES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


class SchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.book = None

    def requestStarted(self, job):
        url = job.requestUrl()
        path = url.path()
        try:
            if path.startswith("/app/"):
                full = os.path.realpath(os.path.join(WEB_DIR, path[len("/app/"):]))
                if not full.startswith(os.path.realpath(WEB_DIR) + os.sep):
                    raise KeyError(path)
                with open(full, "rb") as f:
                    data = f.read()
                mime = mime_for(full)
            elif path.startswith("/book/") and self.book:
                _, _, book_id, inner = path.split("/", 3)
                if book_id != self.book.id:
                    raise KeyError(path)
                data = self.book.read(inner)
                # Chapters that aren't well-formed XML are retried as HTML
                mime = "text/html" if url.query() == "as=html" else mime_for(inner)
            else:
                raise KeyError(path)
        except (KeyError, ValueError, OSError, zipfile.BadZipFile):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buf = QBuffer(job)
        buf.setData(data)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode(), buf)


class OfflineInterceptor(QWebEngineUrlRequestInterceptor):
    """Books never reach the network (no tracking pixels, no remote fonts)."""

    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in ("pyreader", "data", "blob"):
            info.block(True)


class ReaderPage(QWebEnginePage):
    def __init__(self, profile, owner):
        super().__init__(profile, owner)
        self.owner = owner

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith("PYR:"):
            try:
                self.owner.on_message(json.loads(message[4:]))
            except ValueError:
                pass
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            log(f"JS: {message} ({source}:{line})")

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "pyreader":
            return True
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        return False


# ------------------------------------------------------------------------------
# X11 helpers (pure ctypes + libX11): keep-below hints and sibling widgets
# ------------------------------------------------------------------------------
class X11:
    STATES = ("_NET_WM_STATE_BELOW", "_NET_WM_STATE_STICKY", "_NET_WM_STATE_SKIP_TASKBAR",
              "_NET_WM_STATE_SKIP_PAGER", "_KDE_NET_WM_STATE_SKIP_SWITCHER")

    def __init__(self):
        self.x = None
        self.d = None
        try:
            x = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
            vp, ul, ci, cu = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
            P = ctypes.POINTER
            x.XOpenDisplay.restype = vp
            x.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x.XInternAtom.restype = ul
            x.XInternAtom.argtypes = [vp, ctypes.c_char_p, ci]
            x.XDefaultRootWindow.restype = ul
            x.XDefaultRootWindow.argtypes = [vp]
            x.XSendEvent.argtypes = [vp, ul, ci, ctypes.c_long, vp]
            x.XFlush.argtypes = [vp]
            x.XFree.argtypes = [vp]
            x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, ci, ul,
                                             P(ul), P(ci), P(ul), P(ul), P(vp)]
            x.XGetGeometry.argtypes = [vp, ul, P(ul), P(ci), P(ci), P(cu), P(cu), P(cu), P(cu)]
            x.XTranslateCoordinates.argtypes = [vp, ul, ul, ci, ci, P(ci), P(ci), P(ul)]
            # A sibling window can vanish between calls; never let Xlib exit on that
            self._handler = ctypes.CFUNCTYPE(ci, vp, vp)(lambda d, e: 0)
            x.XSetErrorHandler(self._handler)
            d = x.XOpenDisplay(None)
            if d:
                self.x, self.d = x, d
                self.root = x.XDefaultRootWindow(d)
        except (OSError, AttributeError):
            pass

    def atom(self, name):
        return self.x.XInternAtom(self.d, name.encode(), 0)

    def _prop(self, win, name, req_type):
        ct = ctypes
        at, fmt, n, after, data = ct.c_ulong(), ct.c_int(), ct.c_ulong(), ct.c_ulong(), ct.c_void_p()
        if self.x.XGetWindowProperty(self.d, win, self.atom(name), 0, 1 << 16, 0, req_type,
                                     ct.byref(at), ct.byref(fmt), ct.byref(n), ct.byref(after),
                                     ct.byref(data)) != 0 or not data:
            return None
        try:
            if fmt.value == 32:
                arr = ct.cast(data, ct.POINTER(ct.c_ulong))
                return [arr[i] for i in range(n.value)]
            if fmt.value == 8:
                return ct.string_at(data, n.value)
            return None
        finally:
            self.x.XFree(data)

    def client_windows(self):
        """[(title, x, y, w, h)] for every managed window."""
        if not self.d:
            return []
        out = []
        for w in self._prop(self.root, "_NET_CLIENT_LIST", 33) or []:  # 33 = XA_WINDOW
            raw = self._prop(w, "_NET_WM_NAME", self.atom("UTF8_STRING")) or self._prop(w, "WM_NAME", 31)
            if not raw:
                continue
            ct = ctypes
            r, gx, gy, gw, gh, bw, depth = (ct.c_ulong(), ct.c_int(), ct.c_int(), ct.c_uint(),
                                           ct.c_uint(), ct.c_uint(), ct.c_uint())
            if not self.x.XGetGeometry(self.d, w, ct.byref(r), ct.byref(gx), ct.byref(gy), ct.byref(gw),
                                       ct.byref(gh), ct.byref(bw), ct.byref(depth)):
                continue
            rx, ry, child = ct.c_int(), ct.c_int(), ct.c_ulong()
            if not self.x.XTranslateCoordinates(self.d, w, self.root, 0, 0, ct.byref(rx), ct.byref(ry),
                                                ct.byref(child)):
                continue
            out.append((raw.decode("utf-8", "replace"), rx.value, ry.value, gw.value, gh.value))
        return out

    def dock_struts(self):
        """[left, right, top, bottom] pixels reserved by X11 docks such as polybar.
        KWin on Wayland leaves X11 struts out of the work area, so read them here."""
        out = [0, 0, 0, 0]
        if not self.d:
            return out
        dock = self.atom("_NET_WM_WINDOW_TYPE_DOCK")
        for w in self._prop(self.root, "_NET_CLIENT_LIST", 33) or []:            # 33 = XA_WINDOW
            if dock not in (self._prop(w, "_NET_WM_WINDOW_TYPE", 4) or []):       # 4 = XA_ATOM
                continue
            strut = self._prop(w, "_NET_WM_STRUT_PARTIAL", 6) or self._prop(w, "_NET_WM_STRUT", 6) or []
            for i, v in enumerate(strut[:4]):                                     # 6 = XA_CARDINAL
                out[i] = max(out[i], v)
        return out

    def keep_on_desktop(self, window):
        if not self.d:
            return
        ct = ctypes

        class ClientMessage(ct.Structure):
            _fields_ = [("type", ct.c_int), ("serial", ct.c_ulong), ("send_event", ct.c_int),
                        ("display", ct.c_void_p), ("window", ct.c_ulong),
                        ("message_type", ct.c_ulong), ("format", ct.c_int), ("data", ct.c_long * 5)]

        class XEvent(ct.Union):
            _fields_ = [("xclient", ClientMessage), ("pad", ct.c_long * 24)]

        net_wm_state = self.atom("_NET_WM_STATE")
        for state in self.STATES:
            ev = XEvent()
            ev.xclient.type = 33  # ClientMessage
            ev.xclient.window = window
            ev.xclient.message_type = net_wm_state
            ev.xclient.format = 32
            ev.xclient.data[0] = 1  # _NET_WM_STATE_ADD
            ev.xclient.data[1] = self.atom(state)
            ev.xclient.data[3] = 1  # source: normal application
            self.x.XSendEvent(self.d, self.root, 0, (1 << 20) | (1 << 19), ct.byref(ev))
        self.x.XFlush(self.d)


# ------------------------------------------------------------------------------
# Reader window
# ------------------------------------------------------------------------------
class Reader(QWebEngineView):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pyReader")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnBottomHint)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.x11 = X11()
        self.state = load_state()
        self.config, self.config_error = load_config()
        self.book = None
        self.book_error = None
        self.ready = False

        self.handler = SchemeHandler(self)
        self.interceptor = OfflineInterceptor(self)
        self.profile = QWebEngineProfile(self)  # off-the-record: nothing cached on disk
        self.profile.installUrlSchemeHandler(SCHEME, self.handler)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        page = ReaderPage(self.profile, self)
        page.setBackgroundColor(QColor(BG_COLOR))
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, False)
        self.setPage(page)
        page.loadFinished.connect(self.on_loaded)

        # Hot-reload the config. Editors often save by replacing the file, so
        # the directory is watched too and the file is re-added after changes.
        self.watcher = QFileSystemWatcher([CONFIG_DIR, CONFIG_PATH], self)
        self.watcher.fileChanged.connect(self.config_changed_soon)
        self.watcher.directoryChanged.connect(self.config_changed_soon)
        self.reload_timer = QTimer(self, singleShot=True, interval=250)
        self.reload_timer.timeout.connect(self.reload_config)
        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(lambda: save_state(self.state))
        self.place_timer = QTimer(self, interval=3000)
        self.place_timer.timeout.connect(self.place)
        self.place_timer.start()

        self.setWindowOpacity(float(self.config.get("opacity", 0.97)))
        self.open_book(self.config.get("book", ""))
        self.load(QUrl(APP_URL))

    # -- window placement ------------------------------------------------------
    def target_geometry(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        ratio = screen.devicePixelRatio() or 1
        cfg = self.config
        margin, gap = int(cfg.get("margin", 24)), int(cfg.get("gap", 16))
        full = screen.geometry()
        sl, sr, st, sb = (v / ratio for v in self.x11.dock_struts())
        left = max(area.x(), full.x() + sl) + margin
        top = max(area.y(), full.y() + st) + margin
        bottom = min(area.y() + area.height(), full.y() + full.height() - sb) - margin
        avoid = set(cfg.get("avoid_windows") or [])
        lefts = [x / ratio for (title, x, y, w, h) in self.x11.client_windows()
                 if title in avoid and w > 0 and x / ratio > left + 200]
        if lefts:
            right = min(lefts) - gap
        else:
            right = min(area.x() + area.width(), full.x() + full.width() - sr) - int(cfg.get("reserve_right", 876))
        width = max(420, int(right - left))
        return int(left), int(top), width, max(320, int(bottom - top))

    def place(self):
        x, y, w, h = self.target_geometry()
        if (x, y, w, h) != (self.x(), self.y(), self.width(), self.height()):
            self.setGeometry(x, y, w, h)

    def keep_on_desktop(self):
        self.x11.keep_on_desktop(int(self.winId()))
        self.place()

    # -- config & book -----------------------------------------------------------
    def config_changed_soon(self, *_):
        if os.path.exists(CONFIG_PATH) and CONFIG_PATH not in self.watcher.files():
            self.watcher.addPath(CONFIG_PATH)
        self.reload_timer.start()

    def reload_config(self):
        old = self.config
        self.config, self.config_error = load_config()
        if self.config_error:
            self.js("PYR.toast", self.config_error, "error")
            return
        self.setWindowOpacity(float(self.config.get("opacity", 0.97)))
        self.place()
        if self.config.get("book") != old.get("book") or self.book_error:
            self.open_book(self.config.get("book", ""))
            self.push_book()
        elif self.reader_settings() != self.reader_settings(old):
            self.js("PYR.settings", self.reader_settings())

    def open_book(self, raw_path):
        self.book, self.book_error = None, None
        path = os.path.abspath(os.path.expanduser(str(raw_path).strip())) if raw_path else ""
        if not path:
            self.handler.book = None
            return
        try:
            self.book = Book(path)
            self.state["last_book"] = path
            self.save_timer.start()
        except FileNotFoundError:
            self.book_error = f"Book not found: {path}"
        except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, AttributeError, OSError) as e:
            self.book_error = f"Couldn't open {os.path.basename(path)}: {e}"
        if self.book_error:
            log(self.book_error)
        self.handler.book = self.book

    def reader_settings(self, cfg=None):
        cfg = cfg or self.config
        return {"layout": self.layout_mode(cfg), "bodyFont": cfg.get("body_font", "Noto Serif"),
                "animations": bool(cfg.get("animations", True)), "justify": bool(cfg.get("justify", True)),
                "hyphenate": bool(cfg.get("hyphenate", True)) and pyphen is not None}

    @staticmethod
    def layout_mode(cfg):
        """`pages` (1 / 2 / "auto") -> single / spread / auto. The older
        `layout` key is still honoured when `pages` isn't set."""
        pages = str(cfg.get("pages", "")).strip().lower()
        if pages in ("1", "one", "single"):
            return "single"
        if pages in ("2", "two", "spread"):
            return "spread"
        if pages == "auto" or "layout" not in cfg:
            return "auto"
        layout = str(cfg.get("layout", "auto")).lower()
        return layout if layout in ("auto", "single", "spread") else "auto"

    def push_book(self):
        if not self.ready:
            return
        payload = {"settings": self.reader_settings(), "fontScale": self.state.get("font_scale", 1.0),
                   "configPath": CONFIG_PATH.replace(os.path.expanduser("~"), "~", 1),
                   "error": self.book_error or self.config_error, "book": None}
        if self.book:
            saved = self.state["books"].get(self.book.path, {})
            payload.update(book=self.book.to_js(),
                           position={"chapter": saved.get("chapter", 0),
                                     "fraction": saved.get("fraction", 0.0)},
                           highlights=saved.get("highlights", {}))
        self.js("PYR.open", payload)

    def js(self, fn, *args):
        if self.ready:
            self.page().runJavaScript(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def on_loaded(self, ok):
        if not ok:
            log("Reader UI failed to load")
            return
        self.ready = True
        self.push_book()

    # -- messages from the page ----------------------------------------------------
    def on_message(self, msg):
        kind = msg.get("t")
        entry = self.state["books"].setdefault(self.book.path, {}) if self.book else None
        if kind == "pos" and entry is not None:
            entry["chapter"] = int(msg.get("chapter", 0))
            entry["fraction"] = float(msg.get("fraction", 0.0))
        elif kind == "highlights" and entry is not None:
            hl = entry.setdefault("highlights", {})
            if msg.get("list"):
                hl[msg["href"]] = msg["list"]
            else:
                hl.pop(msg.get("href"), None)
        elif kind == "scale":
            self.state["font_scale"] = float(msg.get("v", 1.0))
        elif kind == "hyph":
            self.js("PYR.hyphenated", msg.get("token"), self.hyphenate(msg.get("lang", ""), msg.get("words", [])))
            return
        elif kind == "open":
            url = QUrl(msg.get("url", ""))
            if url.scheme() in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            return
        else:
            return
        self.save_timer.start()

    def hyphenate(self, lang, words):
        """{word: word with soft hyphens} for the words that can be split."""
        if pyphen is None:
            return {}
        code = pyphen.language_fallback(str(lang or "en").replace("-", "_")) or pyphen.language_fallback("en")
        if not hasattr(self, "_hyph") or self._hyph[0] != code:
            self._hyph = (code, pyphen.Pyphen(lang=code))
        dic = self._hyph[1]
        out = {}
        for w in words[:50000]:
            h = dic.inserted(w, hyphen="\u00ad")
            if h != w:
                out[w] = h
        return out

    def closeEvent(self, event):
        save_state(self.state)
        super().closeEvent(event)


def main():
    load_config()  # creates the default config on first run
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalScheme
                    | QWebEngineUrlScheme.Flag.LocalAccessAllowed
                    | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored)
    QWebEngineUrlScheme.registerScheme(scheme)

    app = QApplication(sys.argv)
    app.setApplicationName("pyReader")
    reader = Reader()
    reader.place()
    reader.show()
    QTimer.singleShot(300, reader.keep_on_desktop)
    QTimer.singleShot(1500, reader.keep_on_desktop)  # re-assert once KWin has settled
    app.aboutToQuit.connect(lambda: save_state(reader.state))
    # Quit cleanly (saving the reading position) when the session ends.
    # The timer lets Python run its signal handlers while Qt's loop is busy.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer(interval=500)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
