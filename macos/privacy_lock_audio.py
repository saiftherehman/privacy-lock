"""
privacy_lock_audio (macOS) - the little of CoreAudio and CoreMediaIO the lock needs,
through ctypes so the background process needs no extra packages:

  * is any camera running (some app has it open)?
  * input devices: running? mute them, turn their volume to 0, put it back;
  * macOS 14 and newer: which processes are recording right now.
"""

import ctypes
import struct

_CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_CMIO = ctypes.CDLL("/System/Library/Frameworks/CoreMediaIO.framework/CoreMediaIO")


def fourcc(code):
    return struct.unpack(">I", code.encode())[0]


class Address(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32),
                ("mElement", ctypes.c_uint32)]


SYSTEM_OBJECT = 1
GLOBAL, INPUT = fourcc("glob"), fourcc("inpt")
MAIN = 0
DEVICES = fourcc("dev#")
STREAMS = fourcc("stm#")
RUNNING_SOMEWHERE = fourcc("gone")
VOLUME = fourcc("volm")
MUTE = fourcc("mute")
PROCESS_LIST = fourcc("prs#")        # macOS 14+
PROCESS_PID = fourcc("ppid")
PROCESS_RUNNING_INPUT = fourcc("piri")

u32 = ctypes.c_uint32
P = ctypes.POINTER

_CA.AudioObjectHasProperty.argtypes = [u32, P(Address)]
_CA.AudioObjectHasProperty.restype = ctypes.c_ubyte
_CA.AudioObjectGetPropertyDataSize.argtypes = [u32, P(Address), u32, ctypes.c_void_p, P(u32)]
_CA.AudioObjectGetPropertyData.argtypes = [u32, P(Address), u32, ctypes.c_void_p, P(u32),
                                           ctypes.c_void_p]
_CA.AudioObjectSetPropertyData.argtypes = [u32, P(Address), u32, ctypes.c_void_p, u32,
                                           ctypes.c_void_p]
_CA.AudioObjectIsPropertySettable.argtypes = [u32, P(Address), P(ctypes.c_ubyte)]
_CMIO.CMIOObjectGetPropertyDataSize.argtypes = [u32, P(Address), u32, ctypes.c_void_p, P(u32)]
_CMIO.CMIOObjectGetPropertyData.argtypes = [u32, P(Address), u32, ctypes.c_void_p, u32, P(u32),
                                            ctypes.c_void_p]


# ---------------------------------------------------------------- CoreAudio

def _ids(obj, selector, scope=GLOBAL, lib=_CA, cmio=False):
    addr = Address(selector, scope, MAIN)
    size = u32(0)
    get_size = lib.CMIOObjectGetPropertyDataSize if cmio else lib.AudioObjectGetPropertyDataSize
    if get_size(obj, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0 or not size.value:
        return []
    arr = (u32 * (size.value // 4))()
    if cmio:
        used = u32(0)
        err = lib.CMIOObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, size.value,
                                            ctypes.byref(used), arr)
    else:
        err = lib.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                             ctypes.byref(size), arr)
    return list(arr) if err == 0 else []


def _get_u32(obj, selector, scope=GLOBAL, element=MAIN):
    addr = Address(selector, scope, element)
    if not _CA.AudioObjectHasProperty(obj, ctypes.byref(addr)):
        return None
    value, size = u32(0), u32(4)
    err = _CA.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                         ctypes.byref(value))
    return value.value if err == 0 else None


def _get_float(obj, selector, scope, element):
    addr = Address(selector, scope, element)
    if not _CA.AudioObjectHasProperty(obj, ctypes.byref(addr)):
        return None
    value, size = ctypes.c_float(0), u32(4)
    err = _CA.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                         ctypes.byref(value))
    return value.value if err == 0 else None


def _settable(obj, selector, scope, element):
    addr = Address(selector, scope, element)
    if not _CA.AudioObjectHasProperty(obj, ctypes.byref(addr)):
        return False
    flag = ctypes.c_ubyte(0)
    return _CA.AudioObjectIsPropertySettable(obj, ctypes.byref(addr), ctypes.byref(flag)) == 0 \
        and bool(flag.value)


def _set(obj, selector, scope, element, value):
    addr = Address(selector, scope, element)
    return _CA.AudioObjectSetPropertyData(obj, ctypes.byref(addr), 0, None,
                                          ctypes.sizeof(value), ctypes.byref(value)) == 0


def input_devices():
    """Audio devices that have at least one input stream."""
    return [d for d in _ids(SYSTEM_OBJECT, DEVICES) if _ids(d, STREAMS, INPUT)]


def mic_running():
    return any(_get_u32(d, RUNNING_SOMEWHERE) for d in input_devices())


CHANNELS = range(0, 9)  # element 0 is the main control; 1..8 single channels


def silence_inputs():
    """Mute every input and set its volume to 0. Returns what was there before,
    {device id: {"mute": m, "volume": {element: v}}}, for restore_inputs()."""
    before = {}
    for dev in input_devices():
        saved = {"mute": _get_u32(dev, MUTE, INPUT), "volume": {}}
        if _settable(dev, MUTE, INPUT, MAIN):
            _set(dev, MUTE, INPUT, MAIN, u32(1))
        for ch in CHANNELS:
            if _settable(dev, VOLUME, INPUT, ch):
                saved["volume"][ch] = _get_float(dev, VOLUME, INPUT, ch)
                _set(dev, VOLUME, INPUT, ch, ctypes.c_float(0.0))
        before[dev] = saved
    return before


def inputs_silenced():
    for dev in input_devices():
        if _get_u32(dev, MUTE, INPUT) == 0:
            return False
        for ch in CHANNELS:
            v = _get_float(dev, VOLUME, INPUT, ch)
            if v is not None and v > 0.0:
                return False
    return True


def restore_inputs(before):
    for dev in input_devices():
        saved = before.get(str(dev)) or before.get(dev) or {}
        if _settable(dev, MUTE, INPUT, MAIN):
            _set(dev, MUTE, INPUT, MAIN, u32(saved.get("mute") or 0))
        volumes = {int(k): v for k, v in (saved.get("volume") or {}).items()}
        for ch in CHANNELS:
            if _settable(dev, VOLUME, INPUT, ch):
                v = volumes.get(ch)
                _set(dev, VOLUME, INPUT, ch, ctypes.c_float(0.75 if v is None or v <= 0 else v))


def recording_pids():
    """Processes recording from any input right now (macOS 14 and newer; [] before)."""
    pids = []
    for proc in _ids(SYSTEM_OBJECT, PROCESS_LIST):
        if _get_u32(proc, PROCESS_RUNNING_INPUT):
            pid = _get_u32(proc, PROCESS_PID)
            if pid:
                pids.append(pid)
    return pids


# ---------------------------------------------------------------- CoreMediaIO

def cameras():
    return _ids(SYSTEM_OBJECT, DEVICES, lib=_CMIO, cmio=True)


def camera_running():
    for dev in cameras():
        addr = Address(RUNNING_SOMEWHERE, GLOBAL, MAIN)
        value, used = u32(0), u32(0)
        if _CMIO.CMIOObjectGetPropertyData(dev, ctypes.byref(addr), 0, None, 4,
                                           ctypes.byref(used), ctypes.byref(value)) == 0 \
                and value.value:
            return True
    return False
