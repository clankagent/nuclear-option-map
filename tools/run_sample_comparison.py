"""Sequential bounded GPU subsets and CPU-denoised comparison milestones."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from render_safety import schedule_slot


def sample_plan():
    return [(0, 1), (1, 3), (4, 12)] + [(offset, 16) for offset in range(16, 128, 16)]


def verify_chunk(output, offset, count, scene_hash, blender_hash):
    receipt = json.loads((output/'runtime.json').read_text())
    proof = json.loads((output/'proof.json').read_text())
    raw = output/'raw.exr'
    raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if (receipt.get('completed') is not True or receipt.get('samplesCompletedLog') != count
            or receipt.get('samplesRequested') != count or receipt.get('sampleOffset') != offset
            or receipt.get('sampleSubsetLength') != count or receipt.get('sampleSequenceTotal') != 128
            or receipt.get('sceneSha256') != scene_hash or receipt.get('blenderSha256') != blender_hash
            or proof.get('sceneSha256') != scene_hash or proof.get('outputSha256') != raw_hash
            or proof.get('planning', {}).get('sampleOffset') != offset
            or proof.get('planning', {}).get('sampleSubsetLength') != count):
        raise RuntimeError('Completed sample subset receipt does not match the comparison inputs')
    return dict(path=str(raw.resolve()), count=count, offset=offset, sha256=raw_hash,
                renderSeconds=proof['seconds'], processSeconds=receipt['wallSeconds'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--queue-receipt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    scene_hash = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    blender_hash = hashlib.sha256(args.blender.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    chunks = []
    for offset, count in sample_plan():
        output = args.output / f'pass-{offset:03d}'
        runtime = output / 'runtime.json'
        if not runtime.is_file():
            command = [sys.executable, str(Path(__file__).with_name('run_cropped_gpu_trial.py')),
                       '--blender', str(args.blender), '--candidate', str(args.candidate),
                       '--queue-receipt', str(args.queue_receipt), '--queue-states', '8192',
                       '--output', str(output), '--samples', str(count),
                       '--sample-offset', str(offset), '--linear-exr']
            subprocess.run(command, check=True, timeout=200)
            time.sleep(.25)  # Release the device between bounded invocations.
        chunks.append(verify_chunk(output, offset, count, scene_hash, blender_hash))
    chunks_file = args.output/'chunks.json'
    chunks_file.write_text(json.dumps(chunks, indent=2))
    denoised = args.output/'denoised'
    if denoised.exists():
        raise RuntimeError('Denoised output already exists; preserve it and use a fresh run')
    command = [str(args.blender.resolve()), '--background', str(args.candidate.resolve()),
               '--threads', '2', '--python-exit-code', '1', '--python',
               str(Path(__file__).with_name('denoise_sample_comparison.py').resolve()), '--',
               '--chunks', str(chunks_file.resolve()), '--output', str(denoised.resolve()),
               '--library-directory', str(args.blender.resolve().parent/'blender.shared')]
    environment = os.environ.copy()
    environment['OMP_NUM_THREADS'] = '2'
    environment['PATH'] = str(args.blender.resolve().parent/'blender.shared') + os.pathsep + environment['PATH']
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0
    with schedule_slot(), (args.output/'denoise.log').open('w') as stream:
        subprocess.run(command, check=True, env=environment, creationflags=flags,
                       stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    print(json.dumps({'completed': True, 'proof': str(denoised/'proof.json')}), flush=True)


if __name__ == '__main__':
    main()
