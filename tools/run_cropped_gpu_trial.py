"""Launch one bounded cropped render process; default trial is one sample.

The watchdog is an operational stop, not a promise of GPU preemption. The
child owns its own exclusion lock. No render batches or automatic retries.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import re
import hashlib

from render_safety import GPU_TRIAL_STATES_FACTOR, schedule_slot, comparison_subset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, choices=range(1, 17), default=1)
    parser.add_argument("--sample-offset", type=int)
    parser.add_argument("--linear-exr", action="store_true")
    parser.add_argument("--cpu-control", action="store_true")
    parser.add_argument("--queue-receipt", type=Path, help="Reuse matching measured queue receipt to avoid synchronizing diagnostic overhead")
    parser.add_argument("--queue-states", type=int, choices=(1024,8192), default=1024)
    args = parser.parse_args()
    subset = comparison_subset(args.sample_offset, args.samples) if args.sample_offset is not None else None
    blender, candidate, output = args.blender.resolve(), args.candidate.resolve(), args.output.resolve()
    manifest = candidate.parent / "manifest.json"
    if not blender.is_file() or not candidate.is_file() or not manifest.is_file():
        parser.error("Blender, cropped scene and its manifest must exist")
    log = output.with_suffix(".log")
    if output.exists() or log.exists():
        parser.error("Use fresh output and log paths")
    with blender.open("rb") as stream:
        blender_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    with candidate.open("rb") as stream:
        candidate_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    prior = None
    if args.queue_receipt:
        prior = json.loads(args.queue_receipt.read_text())
        if (prior.get("blenderSha256") != blender_hash or prior.get("sceneSha256") != candidate_hash
            or not 0 < float(prior.get("queueStatesFactor", "0")) <= 1/128
            or not prior.get("queueStatesObserved") or max(prior["queueStatesObserved"]) > args.queue_states):
            parser.error("Queue receipt does not match binary, cropped scene and queue policy")
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PATH"] = str(blender.parent / "blender.shared") + os.pathsep + environment["PATH"]
    environment["OMP_NUM_THREADS"] = "2"
    environment["CYCLES_CONCURRENT_STATES_FACTOR"] = prior["queueStatesFactor"] if prior else GPU_TRIAL_STATES_FACTOR
    command = [str(blender), "--background", str(candidate), "--threads", "2",
               "--debug-cycles", "--verbose", "2" if prior or args.cpu_control else "4", "--python-exit-code", "1", "--python",
               str(Path(__file__).with_name("render_cropped_gpu_trial.py")), "--",
               "--manifest", str(manifest), "--output", str(output),
               "--edge", "256", "--samples", str(args.samples), "--queue-states", str(args.queue_states)]
    if args.cpu_control:
        command.append("--cpu-control")
    if subset:
        command.extend(["--sample-offset", str(args.sample_offset)])
    if args.linear_exr:
        command.append("--linear-exr")
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0
    with schedule_slot(), log.open("w", encoding="utf-8") as stream:
        started = time.monotonic()
        if args.queue_states == 8192 and not prior and not args.cpu_control:
            queue_file = output.with_suffix(".queue.json")
            preflight = command.copy()
            preflight[preflight.index("--output") + 1] = str(output.with_name(output.name + "-queue-preflight"))
            preflight.extend(["--queue-info-only", str(queue_file)])
            check = subprocess.run(preflight, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                   creationflags=flags, timeout=60)
            if check.returncode:
                raise RuntimeError("CPU scene/selected-CUDA queue preflight failed; no render started")
            queue = json.loads(queue_file.read_text())
            if queue["sceneSha256"] != candidate_hash or queue["expectedStates"] > args.queue_states:
                raise RuntimeError("Queue plan binding failed; no render started")
            environment["CYCLES_CONCURRENT_STATES_FACTOR"] = queue["factor"]
        process = subprocess.Popen(command, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, creationflags=flags)
        print(json.dumps({"pid": process.pid, "log": str(log), "cpuControl": args.cpu_control,
                          "operationalWatchdogSeconds": 180, "hardGpuDeadline": False}), flush=True)
        try:
            code = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("Cropped trial watchdog stopped its process; inspect log, no automatic retry")
        if code:
            raise RuntimeError(f"Cropped trial failed with exit code {code}; inspect {log}")
        elapsed = time.monotonic()-started
    log_text = log.read_text(encoding="utf-8", errors="replace")
    states = [int(v) for v in re.findall(r"GPU queue concurrent states:\s*(\d+)", log_text)]
    if not args.cpu_control and ((states and max(states) > args.queue_states) or (not states and not prior)):
        raise RuntimeError("Actual CUDA queue bound was not confirmed; inspect log before any further GPU job")
    if prior and not states:
        states = prior["queueStatesObserved"]
    completed_samples = max([int(v) for v in re.findall(r"Sample (\d+)/\d+", log_text)], default=0)
    complete = completed_samples == args.samples
    runtime = {"completed": complete, "wallSeconds": elapsed, "queueStatesObserved": states,
               "queueAuditReceipt": str(args.queue_receipt.resolve()) if prior else None,
               "blenderSha256": blender_hash, "sceneSha256": candidate_hash,
               "queueStatesFactor": environment["CYCLES_CONCURRENT_STATES_FACTOR"],
               "queueStatesTarget": args.queue_states,
               "samplesRequested": args.samples, "samplesCompletedLog": completed_samples,
               "proof": str(output / "proof.json"), "log": str(log),
               "hardGpuDeadline": False}
    if subset:
        runtime.update(subset)
    (output / "runtime.json").write_text(json.dumps(runtime, indent=2))
    print(json.dumps(runtime), flush=True)
    if not complete:
        raise RuntimeError(f"Requested {args.samples} samples but log confirms {completed_samples}; result is incomplete")


if __name__ == "__main__":
    main()
