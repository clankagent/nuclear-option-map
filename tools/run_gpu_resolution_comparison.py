"""Six fixed-world-scale cropped GPU previews; sequential bounded jobs only.

No candidate preparation, installation or publishing. Run only after the caller
has reviewed the plan and released any active CPU baker. No automatic retries.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from render_safety import schedule_slot

SCALES = (3, 2.5, 2, 1.5, 1, .5)
CENTER_X, CENTER_Z, METERS = 10570.0, 8150.0, 360.0


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolution_plan():
    rows = []
    for mpp in SCALES:
        width = int(METERS / mpp)
        grid = math.ceil(width / 256)
        if width % grid:
            raise ValueError('Resolution must split into equal integer pieces')
        edge, meters = width // grid, METERS / grid
        pieces = [dict(column=column, rowFromSouth=row, edge=edge,
                       centerX=CENTER_X-METERS/2+(column+.5)*meters,
                       centerZ=CENTER_Z-METERS/2+(row+.5)*meters,
                       footprintMeters=meters)
                  for row in range(grid) for column in range(grid)]
        rows.append(dict(scale=mpp, width=width, height=width, grid=grid,
                         footprintMeters=METERS, pieces=pieces))
    return rows


def verify_piece(output, piece, scene_hash, blender_hash):
    runtime = json.loads((output/'runtime.json').read_text())
    proof = json.loads((output/'proof.json').read_text())
    raw = output/'raw.exr'
    if (runtime.get('completed') is not True or runtime.get('samplesCompletedLog') != 4
            or runtime.get('samplesRequested') != 4 or runtime.get('sceneSha256') != scene_hash
            or runtime.get('blenderSha256') != blender_hash or proof.get('sceneSha256') != scene_hash
            or proof.get('outputSha256') != digest(raw) or proof.get('device') != 'GPU'
            or proof.get('samples') != 4 or proof.get('width') != piece['edge']
            or proof.get('height') != piece['edge'] or proof.get('denoising') is not False
            or len(proof.get('deviceSelection', [])) != 1
            or proof['deviceSelection'][0].get('type') != 'CUDA'
            or any(key in runtime for key in ('sampleOffset', 'sampleSubsetLength'))
            or proof.get('sampleSubsetEnabled', False)
            or not runtime.get('queueStatesObserved') or max(runtime['queueStatesObserved']) > 8192):
        raise RuntimeError('GPU output does not match completed four-sample, hash-bound trial')
    for field in ('centerX', 'centerZ', 'footprintMeters'):
        if not math.isclose(proof.get(field, float('nan')), piece[field], abs_tol=1e-6):
            raise RuntimeError('GPU output window differs from the comparison plan')
    return dict(**piece, path=str(raw.resolve()), sha256=digest(raw), samples=4,
                renderSeconds=proof['seconds'], gpuJobSeconds=runtime['wallSeconds'])


def forecasts(seconds):
    return {name: dict(widthMeters=width, heightMeters=height,
                       equivalent360MeterPatches=width*height/(METERS*METERS),
                       extrapolatedSeconds=seconds*width*height/(METERS*METERS))
            for name, width, height in (('Heartland', 81920, 81920), ('Ignus', 163840, 81920))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--queue-receipt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--png-url-prefix', help='URL serving output folder; caller must establish the route')
    args = parser.parse_args()
    plan = resolution_plan()
    if args.plan_only:
        print(json.dumps(plan, indent=2)); return
    args.blender, args.candidate, args.output = (p.resolve() for p in (args.blender, args.candidate, args.output))
    if args.output.exists() and not args.resume:
        parser.error('Use a fresh output directory, or explicit --resume for verified completed outputs')
    if not args.blender.is_file() or not args.candidate.is_file() or not args.queue_receipt.is_file():
        parser.error('Existing Blender, CPU-prepared candidate and verified queue receipt are required')
    scene_hash, blender_hash = digest(args.candidate), digest(args.blender)
    args.output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['OMP_NUM_THREADS'] = '2'
    environment['PATH'] = str(args.blender.parent/'blender.shared')+os.pathsep+environment['PATH']
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0
    rows = []
    for spec in plan:
        directory = args.output / ('mpp-'+str(spec['scale']).replace('.', '_'))
        directory.mkdir(exist_ok=True)
        pieces = []
        for piece in spec['pieces']:
            target = directory/f"piece-{piece['rowFromSouth']}-{piece['column']}"
            wall_file = directory/(target.name+'-wall.json')
            reused = target.exists()
            if not reused:
                command = [sys.executable, str(Path(__file__).with_name('run_cropped_gpu_trial.py')),
                           '--blender', str(args.blender), '--candidate', str(args.candidate),
                           '--queue-receipt', str(args.queue_receipt.resolve()), '--queue-states', '8192',
                           '--output', str(target), '--samples', '4', '--linear-exr',
                           '--edge', str(piece['edge']), '--window-center-x', str(piece['centerX']),
                           '--window-center-z', str(piece['centerZ']), '--window-meters', str(piece['footprintMeters'])]
                started = time.monotonic()
                subprocess.run(command, check=True, env=environment, creationflags=flags, timeout=240)
                verified = verify_piece(target, piece, scene_hash, blender_hash)
                wall_file.write_text(json.dumps(dict(completeJobWallSeconds=time.monotonic()-started,
                                                    rawSha256=verified['sha256'])), encoding='utf-8')
            verified = verify_piece(target, piece, scene_hash, blender_hash)
            wall = json.loads(wall_file.read_text())
            if wall.get('rawSha256') != verified['sha256'] or not 0 < wall.get('completeJobWallSeconds', 0) < 300:
                raise RuntimeError('Missing or mismatched measured total GPU job wall receipt; preserve output and use a fresh run')
            pieces.append(dict(**verified, **wall, reused=reused))
        chunks = directory/'pieces.json'
        chunks.write_text(json.dumps({**spec, 'pieces':pieces, 'samples':4, 'sceneSha256':scene_hash}, indent=2))
        denoised = directory/'denoised'
        denoise_wall_file = directory/'denoise-wall.json'
        reused_denoise = denoised.exists()
        if not reused_denoise:
            command = [str(args.blender), '--background', str(args.candidate), '--threads', '2',
                       '--python-exit-code', '1', '--python',
                       str(Path(__file__).with_name('denoise_resolution_comparison.py').resolve()), '--',
                       '--pieces', str(chunks), '--output', str(denoised),
                       '--library-directory', str(args.blender.parent/'blender.shared')]
            started = time.monotonic()
            with schedule_slot(), (directory/'denoise.log').open('w', encoding='utf-8') as stream:
                subprocess.run(command, check=True, env=environment, creationflags=flags,
                               stdout=stream, stderr=subprocess.STDOUT, timeout=300)
            proof = json.loads((denoised/'proof.json').read_text())
            denoise_wall_file.write_text(json.dumps(dict(completeCpuJobWallSeconds=time.monotonic()-started,
                                                        pngSha256=proof['sha256'])))
        proof = json.loads((denoised/'proof.json').read_text())
        cpu_wall = json.loads(denoise_wall_file.read_text())
        png = denoised/'denoised.png'
        if (proof.get('completed') is not True or proof.get('sha256') != digest(png)
                or cpu_wall.get('pngSha256') != proof['sha256'] or proof.get('sceneSha256') != scene_hash
                or proof.get('pieces') != [{k:v for k,v in p.items() if k != 'reused'} for p in pieces]
                or proof.get('width') != spec['width'] or proof.get('samples') != 4):
            raise RuntimeError('CPU-denoised output receipt differs from verified pieces')
        job_wall = sum(p['completeJobWallSeconds'] for p in pieces)
        full_wall = job_wall + cpu_wall['completeCpuJobWallSeconds']
        row = dict(scale=spec['scale'], width=spec['width'], height=spec['height'], samples=4,
                   footprintMeters=METERS, pieces=len(pieces), pngPath=str(png),
                   nativePngUrl=(args.png_url_prefix.rstrip('/')+'/'+png.relative_to(args.output).as_posix()
                                 if args.png_url_prefix else None), sha256=proof['sha256'],
                   completePreviewWallSeconds=full_wall, gpuCompleteJobsWallSeconds=job_wall,
                   gpuJobSeconds=sum(p['gpuJobSeconds'] for p in pieces),
                   renderSeconds=sum(p['renderSeconds'] for p in pieces),
                   cpuCompleteJobWallSeconds=cpu_wall['completeCpuJobWallSeconds'],
                   stages=proof['stages'], reusedGpuPieces=sum(p['reused'] for p in pieces),
                   reusedDenoised=reused_denoise, forecasts=forecasts(full_wall))
        rows.append(row)
        result = dict(completed=len(rows)==len(plan), rows=rows, sceneSha256=scene_hash,
                      blenderSha256=blender_hash, candidatePreparation='Existing CPU-prepared candidate reused unchanged; preparation excluded',
                      forecastScope='Extrapolated cropped-prototype only: full area divided by 360m square times complete measured preview wall. Excludes future production preparation/tiling and landscape complexity.',
                      timingScope='Original measured GPU parent process wall (including startup/hash validation) plus CPU assembly/denoise/save process wall; resumed work retains original timing. Inter-job orchestration is excluded.')
        (args.output/'comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
