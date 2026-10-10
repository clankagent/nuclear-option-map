"""CPU-assemble north-up cropped EXRs and HDR-denoise the entire mosaic once."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from denoise_sample_comparison import denoise_hdr
from render_safety import configure_scene, lower_process_priority, render_slot


def validate_plan(plan):
    width, grid = plan.get('width'), plan.get('grid')
    if type(width) is not int or not 1 <= width <= 720 or type(grid) is not int or not 1 <= grid <= 3:
        raise ValueError('Bounded comparison requires width<=720 and grid<=3')
    if width % grid or width // grid > 256 or plan.get('height') != width:
        raise ValueError('Require equal square pieces at most 256 pixels')
    pieces = plan.get('pieces', [])
    cells = {(p.get('rowFromSouth'), p.get('column')) for p in pieces}
    if len(pieces) != grid*grid or cells != {(r,c) for r in range(grid) for c in range(grid)}:
        raise ValueError('Require every mosaic cell exactly once')
    for piece in pieces:
        if piece.get('edge') != width//grid or not Path(piece['path']).is_file() or Path(piece['path']).suffix.lower() != '.exr':
            raise ValueError('Require existing EXRs with matching bounded edges')
    return width, grid


def place_piece(mosaic, pixels, piece):
    """Blender rows are bottom-up: low world Z goes into the first mosaic row."""
    edge = piece['edge']
    y, x = piece['rowFromSouth']*edge, piece['column']*edge
    mosaic[y:y+edge, x:x+edge] = pixels


def verified_sample_count(plan):
    """Derive the mosaic sample count from completed, hash-bound GPU jobs."""
    counts = set()
    for piece in plan['pieces']:
        source = Path(piece['path'])
        proof = json.loads((source.parent/'proof.json').read_text())
        runtime = json.loads((source.parent/'runtime.json').read_text())
        count = proof.get('samples')
        with source.open('rb') as stream:
            source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if (type(count) is not int or not 1 <= count <= 16
                or runtime.get('completed') is not True
                or runtime.get('samplesRequested') != count
                or runtime.get('samplesCompletedLog') != count
                or proof.get('device') != 'GPU'
                or proof.get('denoising') is not False
                or proof.get('sampleSubsetEnabled', False)
                or any(key in runtime for key in ('sampleOffset', 'sampleSubsetLength'))
                or proof.get('width') != piece['edge'] or proof.get('height') != piece['edge']
                or proof.get('sceneSha256') != plan['sceneSha256']
                or runtime.get('sceneSha256') != plan['sceneSha256']
                or proof.get('outputSha256') != source_hash or piece.get('sha256') != source_hash):
            raise ValueError('Mosaic piece lacks a matching completed GPU sample receipt')
        if 'samples' in piece and piece['samples'] != count:
            raise ValueError('Piece sample metadata differs from its GPU receipt')
        counts.add(count)
    if len(counts) != 1:
        raise ValueError('Mosaic pieces must have the same completed sample count')
    count = counts.pop()
    if 'samples' in plan and plan['samples'] != count:
        raise ValueError('Mosaic sample metadata differs from its GPU receipts')
    return count


def main(argv):
    import bpy
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pieces', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--library-directory', required=True, type=Path)
    args = parser.parse_args(argv)
    plan = json.loads(args.pieces.read_text())
    width, grid = validate_plan(plan)
    samples = verified_sample_count(plan)
    if args.output.exists():
        parser.error('Use a fresh output directory')
    with Path(bpy.data.filepath).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != plan['sceneSha256']:
            raise ValueError('Candidate scene hash changed')
    scene = bpy.context.scene
    configure_scene(scene, device='cpu', samples=1, threads=2)
    scene.view_settings.view_transform = 'AgX'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'
    with render_slot():
        lower_process_priority()
        args.output.mkdir(parents=True)
        started = time.monotonic()
        mosaic = np.zeros((width, width, 4), dtype=np.float32)
        for piece in plan['pieces']:
            source_path = Path(piece['path'])
            if hashlib.sha256(source_path.read_bytes()).hexdigest() != piece['sha256']:
                raise ValueError('EXR hash differs from completed GPU receipt')
            source = bpy.data.images.load(str(source_path), check_existing=False)
            try:
                edge = piece['edge']
                if tuple(source.size) != (edge, edge) or source.channels not in (3, 4) or not source.is_float:
                    raise ValueError('Require bounded scene-linear RGB/RGBA float EXR')
                # Blender image pixels always expose RGBA, including RGB EXRs.
                pixels = np.empty(edge*edge*4, dtype=np.float32)
                source.pixels.foreach_get(pixels)
                pixels = pixels.reshape((edge,edge,4))
                if not np.isfinite(pixels).all():
                    raise ValueError('EXR contains nonfinite values')
                place_piece(mosaic, pixels, piece)
            finally:
                bpy.data.images.remove(source)
        assembled = time.monotonic()
        color, receipt = denoise_hdr(mosaic[:,:,:3], args.library_directory)
        mosaic[:,:,:3] = color
        denoised = time.monotonic()
        image = bpy.data.images.new('cpu-denoised-resolution', width=width, height=width, alpha=True, float_buffer=True)
        target = args.output/'denoised.png'
        try:
            image.colorspace_settings.name = 'Linear Rec.709'
            image.pixels.foreach_set(mosaic.ravel())
            image.update()
            image.save_render(str(target.resolve()), scene=scene)
        finally:
            bpy.data.images.remove(image)
        saved = time.monotonic()
        proof = dict(completed=True, width=width, height=width, samples=samples,
                     sceneSha256=plan['sceneSha256'], sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                     pieces=[{k:v for k,v in p.items() if k != 'reused'} for p in plan['pieces']],
                     stages=dict(assemblySeconds=assembled-started, cpuDenoiseSeconds=denoised-assembled,
                                 saveSeconds=saved-denoised, assemblyDenoiseSaveSeconds=saved-started),
                     denoiser=receipt, orientation='Already Y-flipped EXRs; rows assembled low-to-high world Z in Blender bottom-up pixels; no additional flip; PNG north-up',
                     viewTransform=scene.view_settings.view_transform, look=scene.view_settings.look,
                     exposure=scene.view_settings.exposure, gamma=scene.view_settings.gamma)
        (args.output/'proof.json').write_text(json.dumps(proof, indent=2))
        print('RESOLUTION_DENOISE_COMPLETE', json.dumps(proof), flush=True)


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--')+1:])
