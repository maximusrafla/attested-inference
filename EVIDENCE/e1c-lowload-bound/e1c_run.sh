#!/bin/bash
# E1c: same-context bound below 4 percent of a device, denominators bracketed in-session.
set -u
P=~/venv/bin/python
O=~/e1c; mkdir -p $O
cd ~/t1/bin
{ echo "E1c start $(date -Is)"; nvidia-smi --query-gpu=name,driver_version,vbios_version,clocks.sm --format=csv
  $P -c "import torch;print('torch',torch.__version__,torch.cuda.get_device_name(0))"
  nvidia-smi conf-compute -f 2>/dev/null || true; uname -r; } > $O/env.txt 2>&1
echo "=== CAL-before $(date -Is)"
$P contention.py --duty 1.0 --seconds 20 --queue-depth 4 --calibrate-s 3 --out $O/CAL-before.json
echo "=== BOUND $(date -Is)"
$P tier05_bound.py --null-runs 12 --pairs 6 --seconds 10 --duties 0.01,0.02,0.03,0.05,0.10 \
   --modes nonadaptive --out $O/BOUND-lowload.json
echo "=== CAL-after $(date -Is)"
$P contention.py --duty 1.0 --seconds 20 --queue-depth 4 --calibrate-s 3 --out $O/CAL-after.json
echo "E1c done $(date -Is)"
