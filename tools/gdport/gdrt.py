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
    raise AttributeError("String.%s" % name)


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
    raise AttributeError("Array.%s" % name)


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
