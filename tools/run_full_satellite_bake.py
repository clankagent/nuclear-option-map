"""Bake and audit both private full-texture satellite pyramids sequentially.

Never changes live variants; completed outputs await visual acceptance.
Run with uv supplying Pillow and numpy. Source assets remain private.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from render_safety import lower_process_priority


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-directory', required=True, type=Path)
    parser.add_argument('--output-directory', required=True, type=Path)
    parser.add_argument('--state-directory', required=True, type=Path)
    parser.add_argument('--max-level', type=int, choices=range(2, 7), default=6)
    args = parser.parse_args()
    lower_process_priority()
    args.state_directory.mkdir(parents=True, exist_ok=True)
    state_path = args.state_directory/'sequence-status.json'
    tools = Path(__file__).resolve().parent
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0
    started = time.time()
    def status(stage, **values):
        state = dict(stage=stage, updatedUnix=time.time(), startedUnix=started,
                     supervisorPid=os.getpid(), samples=4, denoised=True,
                     maxLevel=args.max_level, **values)
        temp = state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(state_path)
        print(json.dumps(state), flush=True)
    try:
        for family, map_id, width in [('heartland', 'heartland-46396', 81920),
                                      ('ignus', 'ignus-archipelago-46395', 163840)]:
            output = args.output_directory/f'{family}-4s-linear'
            tile_count = sum((width//81920)*4**z for z in range(args.max_level+1))
            command = [sys.executable, str(tools/'render_satellite_tiles.py'),
                       '--blend', str(args.source_directory/f'{family}.blend'),
                       '--output', str(output), '--world-width', str(width),
                       '--world-depth', '81920', '--max-level', str(args.max_level),
                       '--tile-budget', str(tile_count), '--chunk-tiles', '4',
                       '--derive-parents', '--samples', '4', '--threads', '2',
                       '--linear-denoise', '--reuse-uniform-water']
            with (args.state_directory/f'{family}-bake.log').open('a', encoding='utf-8') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=flags)
                status('baking', map=family, childPid=process.pid, dataset=str(output.resolve()))
                code = process.wait()
                if code:
                    raise RuntimeError(f'{family} bake exited {code}; inspect its log and preserved chunks')
            status('auditing', map=family, dataset=str(output.resolve()))
            subprocess.run([sys.executable, str(tools/'audit_satellite_dataset.py'), str(output),
                            '--map-id', map_id, '--max-level', str(args.max_level), '--samples', '4',
                            '--output', str(args.state_directory/f'{family}-complete.json')],
                           check=True, creationflags=flags)
        status('complete-awaiting-visual-check', liveVariantsChanged=False)
    except Exception as error:
        status('failed', error=str(error), liveVariantsChanged=False)
        raise


if __name__ == '__main__':
    main()
