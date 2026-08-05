#!/usr/bin/env python3
"""Rider b(i): can anything read device profiling counters while the GPU is in CC-On?

The H100 confidential-computing whitepaper says the profiling counters are disabled in full
CC-On mode, and re-enabled only in CC-DevTools. The Azure CGPU image ships neither Nsight
Compute nor Nsight Systems, so the direct ncu/nsys probe cannot run there. PyTorch's profiler
reaches the same counters through CUPTI (via Kineto), so it is the probe that is actually
available on this image.

Reported separately from the NVML card-health read on purpose. NVML utilization comes down a
different path and may well return numbers in CC-On; a working NVML read is NOT evidence that
the profiling counters are enabled, and conflating the two would overstate the counters-disabled finding.
"""

import json
import sys

import torch


def main():
    out = {"torch": torch.__version__, "device": torch.cuda.get_device_name(0)}

    a = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)

    # 1. CUPTI kernel tracing (activity API)
    try:
        from torch.profiler import ProfilerActivity, profile
        with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
            for _ in range(5):
                _ = a @ b
            torch.cuda.synchronize()
        events = [e for e in prof.key_averages() if e.self_device_time_total > 0]
        out["cupti_kernel_trace"] = {
            "raised": False,
            "cuda_events_with_device_time": len(events),
            "sample": [e.key for e in events[:3]],
        }
    except Exception as e:
        out["cupti_kernel_trace"] = {"raised": True, "error": f"{type(e).__name__}: {e}"}

    # 2. CUPTI hardware metric collection (the counters the whitepaper names)
    try:
        from torch.profiler import ProfilerActivity, profile
        with profile(activities=[ProfilerActivity.CUDA], with_flops=True,
                     experimental_config=torch._C._profiler._ExperimentalConfig(
                         profiler_metrics=["kineto__tensor_core_insts",
                                           "dram__bytes_read.sum"],
                         profiler_measure_per_kernel=True)) as prof:
            for _ in range(5):
                _ = a @ b
            torch.cuda.synchronize()
        txt = prof.key_averages().table(row_limit=5)
        out["cupti_hardware_metrics"] = {"raised": False, "table_head": txt[:400]}
    except Exception as e:
        out["cupti_hardware_metrics"] = {"raised": True, "error": f"{type(e).__name__}: {e}"}

    # 3. the CUDA profiler start/stop API
    try:
        torch.cuda.profiler.start()
        _ = a @ b
        torch.cuda.synchronize()
        torch.cuda.profiler.stop()
        out["cuda_profiler_api"] = {"raised": False}
    except Exception as e:
        out["cuda_profiler_api"] = {"raised": True, "error": f"{type(e).__name__}: {e}"}

    print(json.dumps(out, indent=2, sort_keys=True))
    if len(sys.argv) > 1:
        open(sys.argv[1], "w").write(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
