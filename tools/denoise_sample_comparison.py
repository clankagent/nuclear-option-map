"""CPU-only HDR accumulation/denoising of bounded, already flipped EXR chunks.

Run inside Blender with the original candidate loaded. No rendering is performed.
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_safety import configure_scene, lower_process_priority, render_slot

MILESTONES = (1, 4, 16, 64, 128)


def validate_chunks(chunks):
    if not isinstance(chunks, list) or not chunks:
        raise ValueError('Require a nonempty ordered chunk list')
    total = 0
    for chunk in chunks:
        count = chunk.get('count')
        if type(count) is not int or not 1 <= count <= 16:
            raise ValueError('Chunk count must be an integer in 1..16')
        if 'offset' in chunk and chunk['offset'] != total:
            raise ValueError('Chunk offsets must be contiguous')
        next_total = total + count
        if any(total < target < next_total for target in MILESTONES):
            raise ValueError('Chunk crosses a comparison milestone')
        if not Path(chunk['path']).is_file() or Path(chunk['path']).suffix.lower() != '.exr':
            raise ValueError('Every chunk must identify an existing EXR')
        total = next_total
    if total != MILESTONES[-1]:
        raise ValueError('Require exactly 128 accumulated samples')


def denoise_hdr(color, directory, timeout=240):
    import numpy as np
    color = np.ascontiguousarray(color, dtype=np.float32)
    if color.shape != (256, 256, 3) or not np.isfinite(color).all() or (color < 0).any():
        raise ValueError('Require finite nonnegative 256-square linear RGB')
    output = np.empty_like(color)
    directory = Path(directory).resolve()
    for backend in ('SYCL', 'CUDA', 'HIP', 'METAL'):
        os.environ['OIDN_DEVICE_' + backend] = '0'
    os.environ['OIDN_NUM_THREADS'] = os.environ['OMP_NUM_THREADS'] = '2'
    search = os.add_dll_directory(str(directory)) if os.name == 'nt' else None
    cpu_module = c.CDLL(str(directory / 'OpenImageDenoise_device_cpu.dll'))
    lib = c.CDLL(str(directory / 'OpenImageDenoise.dll'))
    ptr, string, integer, size = c.c_void_p, c.c_char_p, c.c_int, c.c_size_t
    def api(name, args, result=None):
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
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
    commit_filter = api('oidnCommitFilter', [ptr])
    execute = api('oidnExecuteFilter', [ptr])
    release_filter = api('oidnReleaseFilter', [ptr])
    release_device = api('oidnReleaseDevice', [ptr])
    callback_type = c.CFUNCTYPE(c.c_bool, ptr, c.c_double)
    progress = api('oidnSetFilterProgressMonitorFunction', [ptr, callback_type, ptr])
    def check(device):
        message = string()
        code = error(device, c.byref(message))
        if code:
            raise RuntimeError(f'OIDN error {code}: {message.value!r}')
    started = time.monotonic()
    callback = callback_type(lambda unused, fraction: time.monotonic() - started < timeout)
    device = filter_handle = None
    try:
        device = new_device(1)
        if not device:
            raise RuntimeError('OIDN CPU device unavailable')
        check(device)
        set_device(device, b'numThreads', 2)
        commit_device(device)
        check(device)
        if get_device(device, b'type') != 1 or get_device(device, b'numThreads') != 2:
            raise RuntimeError('OIDN CPU/thread verification failed')
        filter_handle = new_filter(device, b'RT')
        check(device)
        for key, array in ((b'color', color), (b'output', output)):
            set_image(filter_handle, key, array.ctypes.data, 3, 256, 256, 0, 0, 0)
        set_bool(filter_handle, b'hdr', True)
        set_bool(filter_handle, b'srgb', False)
        set_int(filter_handle, b'maxMemoryMB', 256)
        progress(filter_handle, callback, None)
        commit_filter(filter_handle)
        check(device)
        execute(filter_handle)
        check(device)
        if not np.isfinite(output).all():
            raise RuntimeError('OIDN produced nonfinite output')
        receipt = dict(device='CPU', threads=2, filter='RT', hdr=True, srgb=False,
                       auxiliaryFeatures=False, filterMemoryRequestMB=256,
                       hardMemoryCap=False, progressTimeoutSeconds=timeout,
                       seconds=time.monotonic()-started,
                       explicitBufferBytes=color.nbytes+output.nbytes,
                       libraryHashes={name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                      for name in ('OpenImageDenoise.dll', 'OpenImageDenoise_core.dll',
                                                   'OpenImageDenoise_device_cpu.dll')})
        return output, receipt
    finally:
        if filter_handle:
            release_filter(filter_handle)
        if device:
            release_device(device)
        if search:
            search.close()


def main(argv):
    import bpy
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chunks', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--library-directory', required=True, type=Path)
    args = parser.parse_args(argv)
    chunks = json.loads(args.chunks.read_text(encoding='utf-8-sig'))
    validate_chunks(chunks)
    if args.output.exists():
        parser.error('Use a fresh output directory')
    scene = bpy.context.scene
    configure_scene(scene, device='cpu', samples=1, threads=2)
    scene.view_settings.view_transform = 'AgX'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'
    started = time.monotonic()
    results = []
    with render_slot():
        lower_process_priority()
        args.output.mkdir(parents=True)
        accumulated = np.zeros((256, 256, 4), dtype=np.float64)
        count = 0
        for chunk in chunks:
            if 'sha256' in chunk and hashlib.sha256(Path(chunk['path']).read_bytes()).hexdigest() != chunk['sha256']:
                raise ValueError('EXR hash does not match its completed render receipt')
            source = bpy.data.images.load(str(Path(chunk['path']).resolve()), check_existing=False)
            try:
                if tuple(source.size) != (256, 256) or source.channels != 4 or not source.is_float:
                    raise ValueError('Require 256-square float RGBA EXR')
                pixels = np.empty(256*256*4, dtype=np.float32)
                source.pixels.foreach_get(pixels)
                pixels = pixels.reshape((256, 256, 4))
                if not np.isfinite(pixels).all():
                    raise ValueError('EXR contains nonfinite pixels')
                accumulated += pixels.astype(np.float64) * chunk['count']
                count += chunk['count']
            finally:
                bpy.data.images.remove(source)
            if count not in MILESTONES:
                continue
            average = (accumulated/count).astype(np.float32)
            color, receipt = denoise_hdr(average[:, :, :3], args.library_directory)
            average[:, :, :3] = color
            image = bpy.data.images.new(f'cpu-denoised-{count}', width=256, height=256,
                                        alpha=True, float_buffer=True)
            try:
                image.colorspace_settings.name = 'Linear Rec.709'
                image.pixels.foreach_set(average.ravel())
                image.update()
                target = args.output/f'samples-{count}-denoised.png'
                image.save_render(str(target.resolve()), scene=scene)
            finally:
                bpy.data.images.remove(image)
            results.append(dict(samples=count, image=str(target.resolve()), width=256, height=256,
                                sha256=hashlib.sha256(target.read_bytes()).hexdigest(), denoiser=receipt))
            print('COMPARISON_DENOISED', json.dumps(results[-1]), flush=True)
        proof = dict(completed=True, samples=count, results=results, seconds=time.monotonic()-started,
                     chunks=chunks, orientation='EXR pixels retained; no additional flip',
                     viewTransform=scene.view_settings.view_transform,
                     look=scene.view_settings.look, exposure=scene.view_settings.exposure,
                     gamma=scene.view_settings.gamma,
                     implementationSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        (args.output/'proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        print('COMPARISON_DENOISE_COMPLETE', json.dumps(proof), flush=True)


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--')+1:])
