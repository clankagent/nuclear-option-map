"""Execute <=16 tiny windows sequentially in one loaded cropped Blender scene.

Each window passes through the existing controlled-trial main and every safety
check again. This does not enable persistent-data rendering or save the scene.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_cropped_gpu_trial import camera_window, main as render_trial


def validate_windows(pieces, manifest, samples):
    if type(samples) is not int or not 1 <= samples <= 16:
        raise ValueError('Each GPU invocation requires 1..16 samples')
    if not isinstance(pieces, list) or not 1 <= len(pieces) <= 16:
        raise ValueError('A loaded-scene batch requires 1..16 windows')
    cells = set()
    for piece in pieces:
        if type(piece.get('edge')) is not int or not 1 <= piece['edge'] <= 256:
            raise ValueError('Every window edge must be an integer in 1..256')
        for key in ('rowFromSouth', 'column'):
            if type(piece.get(key)) is not int or not 0 <= piece[key] < 16:
                raise ValueError('Require bounded integer mosaic cell coordinates')
        cell = piece['rowFromSouth'], piece['column']
        if cell in cells:
            raise ValueError('Duplicate mosaic cell')
        cells.add(cell)
        camera_window(manifest, piece.get('centerX'), piece.get('centerZ'), piece.get('footprintMeters'))
        if any(piece.get(key) is None for key in ('centerX','centerZ','footprintMeters')):
            raise ValueError('Require all explicit window coordinates')


def main(argv):
    import bpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pieces', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=4)
    args = parser.parse_args(argv)
    plan = json.loads(args.pieces.read_text())
    manifest = json.loads(args.manifest.read_text())
    validate_windows(plan['pieces'], manifest, args.samples)
    source = bpy.data.filepath
    scene = bpy.context.scene
    if not scene.camera or scene.camera.data.type != 'ORTHO':
        raise ValueError('Require the original orthographic candidate camera')
    original_location = scene.camera.location.copy()
    original_scale = scene.camera.data.ortho_scale
    timings = []
    for index, piece in enumerate(plan['pieces']):
        if bpy.data.filepath != source:
            raise RuntimeError('Loaded candidate source filepath changed')
        scene.camera.location = original_location
        scene.camera.data.ortho_scale = original_scale
        target = args.output/f"piece-{piece['rowFromSouth']}-{piece['column']}"
        print('GPU_PATCH_BEGIN', json.dumps(dict(index=index, output=str(target))), flush=True)
        started = time.monotonic()
        render_trial(['--manifest', str(args.manifest), '--output', str(target),
                      '--samples', str(args.samples), '--edge', str(piece['edge']),
                      '--window-center-x', str(piece['centerX']), '--window-center-z', str(piece['centerZ']),
                      '--window-meters', str(piece['footprintMeters']), '--linear-exr', '--queue-states', '8192'])
        entry = dict(index=index, output=str(target), loadedJobWallSeconds=time.monotonic()-started)
        timings.append(entry)
        print('GPU_PATCH_END', json.dumps(entry), flush=True)
    scene.camera.location = original_location
    scene.camera.data.ortho_scale = original_scale
    (args.output/'worker-timings.json').write_text(json.dumps(timings, indent=2))


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--')+1:])
