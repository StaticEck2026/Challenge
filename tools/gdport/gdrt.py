"""Runtime shim for GDScript code transpiled by gd2py.py.

Provides the subset of the Godot 4 API that godot/host/*.gd, the generated
godot/recomp/*.gd and the legacy interpreter (legacy_emu/) use: packed arrays,
String/Array/Dictionary methods (dispatched by gdcall), FileAccess, DirAccess,
Time, and the global helper functions.  It is a test harness, not a Godot
replacement: semantics follow the Godot documentation for the calls used.
"""
import datetime
import os
import shutil

TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING = 0, 1, 2, 3, 4
TYPE_DICTIONARY, TYPE_ARRAY = 27, 28
OK = 0
FAILED = 1
KEY_NONE = 0


def typeof(v):
    if v is None:
        return TYPE_NIL
    if isinstance(v, bool):
        return TYPE_BOOL
    if isinstance(v, int):
        return TYPE_INT
    if isinstance(v, float):
        return TYPE_FLOAT
    if isinstance(v, str):
        return TYPE_STRING
    if isinstance(v, dict):
        return TYPE_DICTIONARY
    if isinstance(v, list):
        return TYPE_ARRAY
    return 99


def mini(a, b):
    return min(a, b)


def maxi(a, b):
    return max(a, b)


def clampi(v, lo, hi):
    return max(lo, min(hi, v))


def absi(v):
    return abs(v)


def char(c):
    return chr(c)


def fmod(a, b):
    import math
    return math.fmod(a, b)


def String(v):
    return gd_str(v)


def gd_str(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def PackedByteArray(init=None):
    return bytearray(init or [])


class PackedIntArray(list):
    """Packed int arrays: resize() fills with 0 (plain Arrays fill with null)."""


def PackedInt32Array(init=None):
    return PackedIntArray(init or [])


def PackedInt64Array(init=None):
    return PackedIntArray(init or [])


def PackedStringArray(init=None):
    return list(init or [])


def Array(init=None):
    return list(init or [])


def gdint(v):
    if isinstance(v, float):
        return int(v)     # truncation, like GDScript int()
    return int(v)


# ------------------------------------------------------------- method calls
def gdcall(obj, name, *args):
    if isinstance(obj, str):
        return _str_method(obj, name, args)
    if isinstance(obj, bytearray):
        return _pba_method(obj, name, args)
    if isinstance(obj, list):
        return _list_method(obj, name, args)
    if isinstance(obj, dict):
        return _dict_method(obj, name, args)
    if name == "call":
        if callable(obj):
            return obj(*args)
        return getattr(obj, args[0])(*args[1:])
    return getattr(obj, name)(*args)


def _str_method(s, name, a):
    if name == "begins_with":
        return s.startswith(a[0])
    if name == "ends_with":
        return s.endswith(a[0])
    if name == "to_upper":
        return s.upper()
    if name == "to_lower":
        return s.lower()
    if name == "length":
        return len(s)
    if name == "substr":
        start = a[0]
        ln = a[1] if len(a) > 1 else -1
        return s[start:] if ln < 0 else s[start:start + ln]
    if name == "find":
        return s.find(a[0], *(a[1:]))
    if name == "split":
        sep = a[0]
        allow_empty = a[1] if len(a) > 1 else True
        parts = s.split(sep)
        return parts if allow_empty else [p for p in parts if p != ""]
    if name == "rpad":
        return s.ljust(a[0], a[1] if len(a) > 1 else " ")
    if name == "unicode_at":
        return ord(s[a[0]])
    if name == "to_ascii_buffer":
        return bytearray(s.encode("latin-1", "replace"))
    if name == "to_utf8_buffer":
        return bytearray(s.encode("utf-8"))
    if name == "strip_edges":
        left = a[0] if len(a) > 0 else True
        right = a[1] if len(a) > 1 else True
        if left and right:
            return s.strip()
        if left:
            return s.lstrip()
        if right:
            return s.rstrip()
        return s
    if name == "repeat":
        return s * a[0]
    if name == "get_file":
        return s.rsplit("/", 1)[-1]
    if name == "replace":
        return s.replace(a[0], a[1])
    if name == "join":
        return s.join(gd_str(x) for x in a[0])
    if name == "hex_decode":
        return bytearray.fromhex(s)
    if name == "is_empty":
        return s == ""
    return _str_method_ui(s, name, a)


def _pba_method(b, name, a):
    if name == "size":
        return len(b)
    if name == "resize":
        n = a[0]
        if n < len(b):
            del b[n:]
        else:
            b.extend(bytes(n - len(b)))
        return OK
    if name == "fill":
        b[:] = bytes([a[0] & 0xFF]) * len(b)
        return None
    if name == "append":
        b.append(a[0] & 0xFF)
        return None
    if name == "append_array":
        b.extend(a[0])
        return None
    if name == "slice":
        end = a[1] if len(a) > 1 else len(b)
        return bytearray(b[a[0]:end])
    if name == "decode_u16":
        return b[a[0]] | (b[a[0] + 1] << 8)
    if name == "decode_u32":
        return int.from_bytes(b[a[0]:a[0] + 4], "little")
    if name == "is_empty":
        return len(b) == 0
    if name == "remove_at":
        del b[a[0]]
        return None
    if name == "duplicate":
        return bytearray(b)
    if name == "get_string_from_ascii":
        z = b.find(0)
        return bytes(b if z < 0 else b[:z]).decode("latin-1")
    if name == "hex_encode":
        return b.hex()
    raise AttributeError("PackedByteArray.%s" % name)


def _list_method(lst, name, a):
    if name == "size":
        return len(lst)
    if name == "resize":
        n = a[0]
        if n < len(lst):
            del lst[n:]
        else:
            lst.extend([0 if isinstance(lst, PackedIntArray) else None] * (n - len(lst)))
        return OK
    if name == "fill":
        for i in range(len(lst)):
            lst[i] = a[0]
        return None
    if name == "append":
        lst.append(a[0])
        return None
    if name == "append_array":
        lst.extend(a[0])
        return None
    if name == "is_empty":
        return len(lst) == 0
    if name == "remove_at":
        del lst[a[0]]
        return None
    if name == "pop_back":
        return lst.pop() if lst else None
    if name == "slice":
        end = a[1] if len(a) > 1 else len(lst)
        return lst[a[0]:end]
    if name == "duplicate":
        return list(lst)
    if name == "sort_custom":
        import functools
        f = a[0]
        lst.sort(key=functools.cmp_to_key(lambda x, y: -1 if f(x, y) else (1 if f(y, x) else 0)))
        return None
    if name == "has":
        return a[0] in lst
    return _list_method_ui(lst, name, a)


def _dict_method(d, name, a):
    if name == "has":
        return a[0] in d
    if name == "get":
        return d.get(a[0], a[1] if len(a) > 1 else None)
    if name == "erase":
        return d.pop(a[0], None) is not None
    if name == "merge":
        for k, v in a[0].items():
            if len(a) > 1 and a[1] or k not in d:
                d[k] = v
        return None
    if name == "size":
        return len(d)
    if name == "is_empty":
        return len(d) == 0
    if name == "keys":
        return list(d.keys())
    if name == "values":
        return list(d.values())
    if name == "duplicate":
        import copy
        return copy.deepcopy(d) if a and a[0] else dict(d)
    if name == "clear":
        d.clear()
        return None
    raise AttributeError("Dictionary.%s" % name)


# ------------------------------------------------------------ file system
VFS = {"res://": None, "user://": None}


def globalize(path):
    for prefix, real in VFS.items():
        if path.startswith(prefix):
            if real is None:
                raise RuntimeError("no mapping for " + prefix)
            return os.path.join(real, path[len(prefix):])
    return path


class FileAccess:
    READ, WRITE, READ_WRITE, WRITE_READ = 1, 2, 3, 7

    def __init__(self, fp):
        self.fp = fp

    @staticmethod
    def open(path, mode):
        p = globalize(path)
        try:
            m = {1: "rb", 2: "wb", 3: "r+b", 7: "w+b"}[mode]
            return FileAccess(open(p, m))
        except OSError:
            return None

    @staticmethod
    def file_exists(path):
        return os.path.isfile(globalize(path))

    @staticmethod
    def get_file_as_bytes(path):
        try:
            return bytearray(open(globalize(path), "rb").read())
        except OSError:
            return bytearray()

    def get_buffer(self, n):
        return bytearray(self.fp.read(n))

    def store_buffer(self, data):
        self.fp.write(bytes(data))

    def store_8(self, v):
        self.fp.write(bytes([v & 0xFF]))

    def seek(self, pos):
        self.fp.seek(pos)

    def seek_end(self, pos=0):
        self.fp.seek(pos, 2)

    def get_position(self):
        return self.fp.tell()

    def get_length(self):
        pos = self.fp.tell()
        self.fp.seek(0, 2)
        n = self.fp.tell()
        self.fp.seek(pos)
        return n

    def flush(self):
        self.fp.flush()

    def close(self):
        self.fp.close()


class DirAccess:
    @staticmethod
    def dir_exists_absolute(path):
        return os.path.isdir(globalize(path))

    @staticmethod
    def get_files_at(path):
        p = globalize(path)
        return sorted(n for n in os.listdir(p) if os.path.isfile(os.path.join(p, n)))

    @staticmethod
    def get_directories_at(path):
        p = globalize(path)
        return sorted(n for n in os.listdir(p) if os.path.isdir(os.path.join(p, n)))

    @staticmethod
    def make_dir_absolute(path):
        try:
            os.mkdir(globalize(path))
            return OK
        except OSError:
            return FAILED

    @staticmethod
    def make_dir_recursive_absolute(path):
        os.makedirs(globalize(path), exist_ok=True)
        return OK

    @staticmethod
    def remove_absolute(path):
        p = globalize(path)
        try:
            if os.path.isdir(p):
                os.rmdir(p)
            else:
                os.remove(p)
            return OK
        except OSError:
            return FAILED

    @staticmethod
    def rename_absolute(a, b):
        try:
            os.rename(globalize(a), globalize(b))
            return OK
        except OSError:
            return FAILED

    @staticmethod
    def copy_absolute(a, b):
        try:
            shutil.copy(globalize(a), globalize(b))
            return OK
        except OSError:
            return FAILED


class Time:
    @staticmethod
    def get_datetime_dict_from_unix_time(t):
        d = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=t)
        return {"year": d.year, "month": d.month, "day": d.day, "weekday": (d.weekday() + 1) % 7,
                "hour": d.hour, "minute": d.minute, "second": d.second}

    @staticmethod
    def get_ticks_usec():
        import time
        return int(time.time() * 1e6)


class Callable:
    """Callable() (invalid) or a wrapper around a Python callable."""

    def __init__(self, fn=None, method=None):
        if method is not None:
            fn = getattr(fn, method)
        self.fn = fn

    def is_valid(self):
        return self.fn is not None

    def call(self, *a):
        return self.fn(*a)

    def __call__(self, *a):
        return self.fn(*a)


class Mutex:
    """Godot Mutex on threading.RLock (Godot mutexes are recursive)."""

    def __init__(self):
        import threading
        self._m = threading.RLock()

    def lock(self):
        self._m.acquire()

    def unlock(self):
        self._m.release()


class Semaphore:
    def __init__(self):
        import threading
        self._s = threading.Semaphore(0)

    def post(self):
        self._s.release()

    def wait(self):
        self._s.acquire()


class OS:
    @staticmethod
    def delay_usec(n):
        import time
        time.sleep(n / 1e6)

    @staticmethod
    def delay_msec(n):
        import time
        time.sleep(n / 1e3)


# ---------------------------------------------------- Modern-UI additions
# (String/Array methods and global functions used by godot/ui/*.gd; see
# gd2py.py --ui.  Semantics follow the Godot 4 documentation.)
import math as _math

PI = _math.pi
TAU = _math.tau
INF = float("inf")


def _str_method_ui(s, name, a):
    if name == "contains":
        return a[0] in s
    if name == "count":
        return s.count(a[0])
    if name == "lstrip":
        return s.lstrip(a[0])
    if name == "rstrip":
        return s.rstrip(a[0])
    if name == "pad_zeros":
        neg = s.startswith("-")
        body = s[1:] if neg else s
        ip, dot, fp = body.partition(".")
        return ("-" if neg else "") + ip.rjust(a[0], "0") + dot + fp
    if name == "to_int":
        import re
        m = re.match(r"\s*[-+]?\d+", s)
        return int(m.group()) if m else 0
    if name == "to_float":
        try:
            return float(s)
        except ValueError:
            return 0.0
    if name == "is_valid_int":
        import re
        return re.fullmatch(r"[-+]?\d+", s) is not None
    if name == "left":
        n = a[0]
        return s[:n] if n >= 0 else s[:len(s) + n]
    if name == "right":
        n = a[0]
        return s[len(s) - n:] if n >= 0 else s[-n:]
    if name == "xml_escape":
        out = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if a and a[0]:
            out = out.replace('"', "&quot;").replace("'", "&apos;")
        return out
    if name == "capitalize":
        return " ".join(w[:1].upper() + w[1:].lower() for w in s.replace("_", " ").split())
    if name == "trim_prefix":
        return s[len(a[0]):] if s.startswith(a[0]) else s
    if name == "trim_suffix":
        return s[:-len(a[0])] if a[0] and s.endswith(a[0]) else s
    if name == "rfind":
        return s.rfind(a[0])
    if name == "get_slice":
        parts = s.split(a[0])
        return parts[a[1]] if a[1] < len(parts) else ""
    raise AttributeError("String.%s" % name)


def _list_method_ui(lst, name, a):
    if name == "push_back":
        lst.append(a[0])
        return None
    if name == "back":
        return lst[-1] if lst else None
    if name == "front":
        return lst[0] if lst else None
    if name == "insert":
        lst.insert(a[0], a[1])
        return OK
    if name == "clear":
        del lst[:]
        return None
    if name == "count":
        return lst.count(a[0])
    if name == "find":
        try:
            return lst.index(a[0], *(a[1:]))
        except ValueError:
            return -1
    if name == "reverse":
        lst.reverse()
        return None
    if name == "sort":
        lst.sort()
        return None
    if name == "max":
        return max(lst) if lst else None
    if name == "min":
        return min(lst) if lst else None
    raise AttributeError("Array.%s" % name)


def floorf(v):
    return float(_math.floor(v))


def ceilf(v):
    return float(_math.ceil(v))


def roundf(v):
    # Godot rounds half away from zero
    return float(_math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


def roundi(v):
    return int(roundf(v))


def floori(v):
    return int(_math.floor(v))


def absf(v):
    return abs(float(v))


def minf(a, b):
    return float(min(a, b))


def maxf(a, b):
    return float(max(a, b))


def clampf(v, lo, hi):
    return float(max(lo, min(hi, v)))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def lerpf(a, b, t):
    return a + (b - a) * t


def lerp(a, b, t):
    return a + (b - a) * t


def sqrt(v):
    return _math.sqrt(v)


def sin(v):
    return _math.sin(v)


def cos(v):
    return _math.cos(v)


def atan2(y, x):
    return _math.atan2(y, x)


def deg_to_rad(v):
    return _math.radians(v)


def rad_to_deg(v):
    return _math.degrees(v)


def posmod(a, b):
    return a % b


def fposmod(a, b):
    return _math.fmod(_math.fmod(a, b) + b, b)


def signf(v):
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)


def signi(v):
    return 1 if v > 0 else (-1 if v < 0 else 0)


def is_equal_approx(a, b):
    return abs(a - b) <= 1e-5 * max(1.0, abs(a), abs(b))


def float_(v):
    return float(v)


class JSON:
    @staticmethod
    def parse_string(text):
        import json
        try:
            return json.loads(text)
        except ValueError:
            return None

    @staticmethod
    def stringify(v, indent="", sort_keys=True):
        import json
        return json.dumps(v, indent=indent or None, sort_keys=sort_keys)


def _file_as_string(path):
    try:
        return open(globalize(path), "rb").read().decode("utf-8")
    except OSError:
        return ""


FileAccess.get_file_as_string = staticmethod(_file_as_string)


class Thread:
    """Godot Thread on threading.Thread."""

    def __init__(self):
        self._t = None

    def start(self, fn, priority=1):
        import threading
        f = fn if callable(fn) else fn.call
        self._t = threading.Thread(target=f, daemon=True)
        self._t.start()
        return OK

    def is_alive(self):
        return self._t is not None and self._t.is_alive()

    def wait_to_finish(self):
        if self._t is not None:
            self._t.join()


class Image:
    """The few Image calls the smoke mode uses (RGB8 -> PNG)."""
    FORMAT_RGB8 = 4

    def __init__(self, w=0, h=0, data=b""):
        self.w, self.h, self.data = w, h, bytes(data)

    @staticmethod
    def create_from_data(w, h, mipmaps, fmt, data):
        return Image(w, h, data)

    def get_width(self):
        return self.w

    def get_height(self):
        return self.h

    def save_png(self, path):
        import struct
        import zlib
        raw = b"".join(b"\0" + self.data[y * self.w * 3:(y + 1) * self.w * 3] for y in range(self.h))

        def chunk(tag, body):
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
        with open(globalize(path), "wb") as f:
            f.write(png)
        return OK
