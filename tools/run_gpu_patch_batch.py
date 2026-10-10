"""One finite loaded-worker batch; tiny GPU windows retain all trial bounds.

The scheduler stays exclusive for the whole subprocess. Every child trial owns
the renderer slot separately. No retries or resume of a partial batch.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from render_safety import schedule_slot
from render_gpu_patch_batch import validate_windows
from run_gpu_resolution_comparison import digest


def validate_queue(prior, scene_hash, binary_hash):
    factor = float(prior.get('queueStatesFactor', 0))
    states = prior.get('queueStatesObserved')
    if (prior.get('completed') is not True or prior.get('sceneSha256') != scene_hash
            or prior.get('blenderSha256') != binary_hash or not math.isfinite(factor)
            or not 0 < factor <= 1/128 or not isinstance(states,list) or not states
            or any(type(v) is not int or not 1 <= v <= 8192 for v in states)):
        raise ValueError('Require a completed queue audit matching binary, scene, factor and <=8192 actual states')


def segment_samples(log, count, samples):
    entries = list(re.finditer(r'^GPU_PATCH_(BEGIN|END) (.+)$', log, re.MULTILINE))
    if len(entries) != count*2:
        raise RuntimeError('Missing or duplicated loaded-worker boundaries')
    results = []
    for index in range(count):
        begin, end = entries[index*2:index*2+2]
        if (begin.group(1) != 'BEGIN' or end.group(1) != 'END'
                or json.loads(begin.group(2)).get('index') != index
                or json.loads(end.group(2)).get('index') != index):
            raise RuntimeError('Loaded-worker job boundaries are not contiguous')
        section = log[begin.end():end.start()]
        completed = max([int(v) for v in re.findall(r'Sample (\d+)/\d+', section)], default=0)
        if completed != samples or 'CROPPED_TRIAL_COMPLETE ' not in section:
            raise RuntimeError('A loaded-worker window did not complete its requested samples')
        results.append(dict(samplesCompletedLog=completed,
                            states=[int(v) for v in re.findall(r'GPU queue concurrent states:\s*(\d+)', section)]))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--queue-receipt', required=True, type=Path)
    parser.add_argument('--pieces', required=True, type=Path, help='Existing resolution comparison pieces.json; image paths ignored')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=4, choices=range(1,17))
    args = parser.parse_args()
    started = time.monotonic()
    blender, candidate, output = (p.resolve() for p in (args.blender,args.candidate,args.output))
    log = output.with_suffix('.log')
    if output.exists() or log.exists():
        parser.error('Use a fresh batch output and log path; preserve partial batches')
    manifest_path = candidate.parent/'manifest.json'
    plan = json.loads(args.pieces.read_text())
    manifest = json.loads(manifest_path.read_text())
    validate_windows(plan['pieces'],manifest,args.samples)
    scene_hash, binary_hash = digest(candidate), digest(blender)
    if manifest.get('outputSha256') != scene_hash or manifest.get('gpuInitialized') is not False:
        parser.error('Require matching CPU-prepared candidate manifest')
    prior = json.loads(args.queue_receipt.read_text())
    validate_queue(prior, scene_hash, binary_hash)
    output.mkdir(parents=True)
    # Strip historical output paths and receipts: this worker only accepts windows.
    windows = [{key:piece[key] for key in ('edge','centerX','centerZ','footprintMeters','column','rowFromSouth')}
               for piece in plan['pieces']]
    windows_file = output/'windows.json'
    windows_file.write_text(json.dumps(dict(pieces=windows), indent=2))
    environment = os.environ.copy()
    environment['PATH'] = str(blender.parent/'blender.shared')+os.pathsep+environment['PATH']
    environment['OMP_NUM_THREADS'] = '2'
    environment['CYCLES_CONCURRENT_STATES_FACTOR'] = str(prior['queueStatesFactor'])
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0
    command = [str(blender),'--background',str(candidate),'--threads','2','--debug-cycles','--verbose','2',
               '--python-exit-code','1','--python',str(Path(__file__).with_name('render_gpu_patch_batch.py').resolve()),
               '--','--pieces',str(windows_file),'--manifest',str(manifest_path),'--output',str(output),
               '--samples',str(args.samples)]
    with schedule_slot(), log.open('w', encoding='utf-8') as stream:
        process_started = time.monotonic()
        process = subprocess.Popen(command, env=environment, creationflags=flags, stdout=stream,stderr=subprocess.STDOUT)
        print(json.dumps(dict(pid=process.pid,log=str(log),operationalWatchdogSeconds=180,hardGpuDeadline=False)),flush=True)
        try:
            code = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise RuntimeError('Loaded-worker watchdog stopped process; no automatic retry')
        if code:
            raise RuntimeError(f'Loaded-worker failed with exit {code}; inspect {log}')
        process_seconds = time.monotonic()-process_started
    segments = segment_samples(log.read_text(encoding='utf-8',errors='replace'),len(windows),args.samples)
    timings = json.loads((output/'worker-timings.json').read_text())
    if len(timings) != len(windows):
        raise RuntimeError('Worker timings do not match windows')
    pieces = []
    for index,(window,segment,timing) in enumerate(zip(windows,segments,timings)):
        target = output/f"piece-{window['rowFromSouth']}-{window['column']}"
        proof = json.loads((target/'proof.json').read_text())
        raw = target/'raw.exr'
        states = segment['states'] or prior['queueStatesObserved']
        if (max(states)>8192 or proof.get('sceneSha256') != scene_hash or proof.get('outputSha256') != digest(raw)
                or proof.get('device') != 'GPU' or proof.get('samples') != args.samples
                or proof.get('width') != window['edge'] or proof.get('height') != window['edge']
                or proof.get('denoising') is not False or len(proof.get('deviceSelection',[])) != 1
                or proof['deviceSelection'][0].get('type') != 'CUDA'
                or timing.get('index') != index or timing.get('loadedJobWallSeconds',0) <= 0):
            raise RuntimeError('Loaded-worker proof does not match bounded GPU job')
        if any(not math.isclose(proof.get(key,float('nan')),window[key],abs_tol=1e-6)
               for key in ('centerX','centerZ','footprintMeters')):
            raise RuntimeError('Loaded-worker proof window mismatch')
        runtime = dict(completed=True,wallSeconds=timing['loadedJobWallSeconds'],
                       queueStatesObserved=states,queueAuditReceipt=str(args.queue_receipt.resolve()),
                       blenderSha256=binary_hash,sceneSha256=scene_hash,queueStatesFactor=prior['queueStatesFactor'],
                       queueStatesTarget=8192,samplesRequested=args.samples,samplesCompletedLog=segment['samplesCompletedLog'],
                       proof=str(target/'proof.json'),log=str(log),loadedWorkerBatch=True,hardGpuDeadline=False)
        (target/'runtime.json').write_text(json.dumps(runtime,indent=2))
        pieces.append(dict(**window,path=str(raw),sha256=proof['outputSha256'],samples=args.samples,
                           renderSeconds=proof['seconds'],gpuJobSeconds=timing['loadedJobWallSeconds']))
    (output/'pieces.json').write_text(json.dumps({**plan,'pieces':pieces,'samples':args.samples,'sceneSha256':scene_hash},indent=2))
    receipt = dict(completed=True,windows=len(pieces),samplesPerWindow=args.samples,
                   sceneSha256=scene_hash,blenderSha256=binary_hash,processWallSeconds=process_seconds,
                   completeBatchWallSeconds=time.monotonic()-started,
                   loadedJobsWallSeconds=sum(t['loadedJobWallSeconds'] for t in timings),
                   renderSeconds=sum(p['renderSeconds'] for p in pieces),
                   candidatePreparation='Unchanged existing CPU-prepared candidate reused; preparation excluded',
                   reuse='One loaded Blender scene; persistent render data remains disabled; each window revalidates inputs',
                   operationalWatchdogSeconds=180,hardGpuDeadline=False,queueStatesObserved=prior['queueStatesObserved'])
    (output/'runtime.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt),flush=True)


if __name__ == '__main__':
    main()
