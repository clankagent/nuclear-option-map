"""Explicit CPU-only Open Image Denoise for bounded sRGB offline crops.

No automatic device selection, no Blender denoising flags, no GPU plugins.
Standalone usage: uv run --with Pillow --with numpy python tools/cpu_denoise.py
INPUT OUTPUT --library-directory PATH. Default cap512px; larger jobs require an
explicit --max-edge (up to render_safety.CPU_MAX_EDGE). RT overlap/alignment are
reported, not guessed: production callers must honor these before splitting.
https://www.openimagedenoise.org/documentation.html
"""
from __future__ import annotations
from contextlib import nullcontext
import argparse
import ctypes as c
import json
import hashlib
import os
from pathlib import Path
import time


def _ancestor_in_parents(owner_pid, current_pid, parents, max_hops=8):
    visited = set()
    for _ in range(max_hops):
        if current_pid in visited:
            return False
        visited.add(current_pid)
        current_pid = parents.get(current_pid)
        if current_pid == owner_pid:
            return True
        if not current_pid:
            return False
    return False


def supervised_by(owner_pid):
    """Windows venv Python redirectors add a process between worker and runtime."""
    if owner_pid is None or owner_pid <= 0:
        return False
    if os.name != 'nt':
        return os.getppid() == owner_pid
    from ctypes import wintypes as w
    class Entry(c.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD),
                    ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                    ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, c.c_void_p(-1).value):
        raise OSError(c.get_last_error(), 'Unable to inspect denoiser ancestry')
    try:
        entry = Entry()
        entry.dwSize = c.sizeof(Entry)
        parents = {}
        ready = kernel.Process32FirstW(snapshot, c.byref(entry))
        while ready:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ready = kernel.Process32NextW(snapshot, c.byref(entry))
        return _ancestor_in_parents(owner_pid, os.getpid(), parents)
    finally:
        kernel.CloseHandle(snapshot)


def denoise_srgb(source, destination, library_directory, *, max_edge=512, timeout_seconds=240, owns_slot=False, crop=None, owner_pid=None):
    import numpy as np
    from PIL import Image
    from render_safety import CPU_MAX_EDGE, lower_process_priority, render_slot
    if not 1 <= max_edge <= CPU_MAX_EDGE or not 0 < timeout_seconds <= 240:
        raise ValueError('Require bounded edge and deadline <=240 seconds')
    if source is None:
        width = height = 1
        color = np.zeros((1, 1, 3), dtype=np.float32)
    else:
        with Image.open(source) as image:
            width, height = image.size
            if max(width, height) > max_edge:
                raise ValueError(f'Denoise input exceeds explicit {max_edge}px cap')
            color = np.ascontiguousarray(np.asarray(image.convert('RGB')).astype(np.float32) / 255)
    if owns_slot:
        from render_satellite_tiles import parent_alive
        if not supervised_by(owner_pid) or not parent_alive(owner_pid):
            raise RuntimeError('Denoiser child is not supervised by its locked worker')
        # Only the synchronous child of an already locked Blender worker may
        # omit a second acquisition. Fail closed if no OS lock is held.
        try:
            with render_slot():
                pass
        except RuntimeError as exc:
            if not isinstance(exc.__cause__, OSError) or exc.__cause__.errno not in (11, 13):
                raise
        else:
            raise RuntimeError('Denoiser worker requested an unowned resource slot')
    output = np.empty_like(color)
    directory = Path(library_directory).resolve()
    for name in ('SYCL', 'CUDA', 'HIP', 'METAL'):
        os.environ['OIDN_DEVICE_' + name] = '0'
    os.environ['OIDN_NUM_THREADS'] = '2'
    os.environ['OMP_NUM_THREADS'] = '2'
    os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
    search_handle = os.add_dll_directory(str(directory)) if os.name == 'nt' else None
    # Explicit load needed for the Blender Windows distribution's module discovery.
    cpu_module = c.CDLL(str(directory / 'OpenImageDenoise_device_cpu.dll'))
    library = c.CDLL(str(directory / 'OpenImageDenoise.dll'))
    ptr, string, integer, size = c.c_void_p, c.c_char_p, c.c_int, c.c_size_t
    def api(name, arguments, result=None):
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
        return function
    new_device = api('oidnNewDevice', [integer], ptr)
    set_device = api('oidnSetDeviceInt', [ptr, string, integer])
    get_device = api('oidnGetDeviceInt', [ptr, string], integer)
    commit_device = api('oidnCommitDevice', [ptr])
    error = api('oidnGetDeviceError', [ptr, c.POINTER(string)], integer)
    new_filter = api('oidnNewFilter', [ptr, string], ptr)
    set_image = api('oidnSetSharedFilterImage', [ptr, string, ptr, integer, size, size, size, size, size])
    set_bool = api('oidnSetFilterBool', [ptr, string, c.c_bool])
    set_int = api('oidnSetFilterInt', [ptr, string, integer])
    get_int = api('oidnGetFilterInt', [ptr, string], integer)
    commit_filter = api('oidnCommitFilter', [ptr])
    execute = api('oidnExecuteFilter', [ptr])
    release_filter = api('oidnReleaseFilter', [ptr])
    release_device = api('oidnReleaseDevice', [ptr])
    callback_type = c.CFUNCTYPE(c.c_bool, ptr, c.c_double)
    set_progress = api('oidnSetFilterProgressMonitorFunction', [ptr, callback_type, ptr])
    def check(device):
        message = string()
        code = error(device, c.byref(message))
        if code:
            raise RuntimeError(f'OIDN error {code}: {message.value!r}')
    started = time.monotonic()
    callback = callback_type(lambda unused, progress: time.monotonic() - started < timeout_seconds
                             and (not owns_slot or parent_alive(owner_pid)))
    device, filter_handle = None, None
    try:
        with nullcontext() if owns_slot else render_slot():
            lower_process_priority()
            device = new_device(1)  # OIDN_DEVICE_TYPE_CPU, never DEFAULT.
            check(device)
            if not device:
                raise RuntimeError('OIDN CPU device unavailable')
            set_device(device, b'numThreads', 2)
            commit_device(device)
            check(device)
            if get_device(device, b'type') != 1 or get_device(device, b'numThreads') != 2:
                raise RuntimeError('OIDN CPU/thread verification failed')
            filter_handle = new_filter(device, b'RT')
            check(device)
            for key, array in ((b'color', color), (b'output', output)):
                set_image(filter_handle, key, array.ctypes.data, 3, width, height, 0, 0, 0)
            set_bool(filter_handle, b'hdr', False)
            set_bool(filter_handle, b'srgb', True)
            set_int(filter_handle, b'maxMemoryMB', 256)  # Soft request, not a hard allocation cap.
            set_progress(filter_handle, callback, None)
            commit_filter(filter_handle)
            check(device)
            alignment = get_int(filter_handle, b'tileAlignment')
            overlap = get_int(filter_handle, b'tileOverlap')
            profile = dict(backendLibraries={name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                                for name in ('OpenImageDenoise.dll', 'OpenImageDenoise_core.dll',
                                                             'OpenImageDenoise_device_cpu.dll')},
                           librarySha256=hashlib.sha256((directory / 'OpenImageDenoise.dll').read_bytes()).hexdigest(),
                           implementationSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           backend='OpenImageDenoise', device='CPU', threads=2,
                           version='.'.join(str(get_device(device, key)) for key in
                                            (b'versionMajor', b'versionMinor', b'versionPatch')),
                           filter='RT', colorSpace='sRGB', hdr=False, auxiliaryFeatures=False,
                           filterMemoryRequestMB=256, tileAlignment=alignment, tileOverlap=overlap)
            if source is None:
                return profile
            execute(filter_handle)
            check(device)
        if not np.isfinite(output).all():
            raise RuntimeError('OIDN produced nonfinite pixels')
        result = Image.fromarray(np.clip(output * 255, 0, 255).astype(np.uint8))
        if crop is not None:
            left, top, right, bottom = crop
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError('Denoise crop exceeds rendered input')
            result = result.crop(crop)
        result.save(destination)
        return dict(profile, width=width, height=height, outputWidth=result.width, outputHeight=result.height,
                    explicitBufferBytes=color.nbytes + output.nbytes, ownerPid=owner_pid,
                    seconds=time.monotonic() - started)
    finally:
        if filter_handle:
            release_filter(filter_handle)
        if device:
            release_device(device)
        if search_handle:
            search_handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--library-directory', required=True, type=Path)
    parser.add_argument('--max-edge', type=int, default=512)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--render-slot-owned', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--owner-pid', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--crop', type=int, nargs=4)
    arguments = parser.parse_args()
    if not arguments.inspect and (arguments.input is None or arguments.output is None):
        parser.error('Input and output required')
    receipt = denoise_srgb(arguments.input, arguments.output, arguments.library_directory,
                          max_edge=arguments.max_edge, owns_slot=arguments.render_slot_owned,
                          crop=arguments.crop, owner_pid=arguments.owner_pid)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
