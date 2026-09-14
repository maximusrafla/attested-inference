#!/bin/bash
# E1b: leak vs window count at fixed verifier duty. Runs on the GPU box. The protocol was fixed before the run.
set -u
P=~/venv/bin/python
O=~/e3; mkdir -p $O
cd ~/t1/bin
{
  echo "E1b start $(date -Is)"
  nvidia-smi --query-gpu=name,driver_version,clocks.sm,clocks.mem --format=csv
  $P -c "import torch;print('torch',torch.__version__,torch.cuda.get_device_name(0))"
  nvidia-smi conf-compute -f 2>/dev/null || true
} > $O/env.txt 2>&1
# Same total challenge time (25 percent of a 60 s run), different numbers of transitions:
# 15 windows of 1.0s, 30 of 0.5s, 60 of 0.25s, 5 of 3.0s as the low-transition control.
for W in 1.0 0.5 0.25 3.0; do
  echo "=== separate, window $W  $(date -Is)"
  $P e1_verifier_duty.py --python $P --window 60 --challenge-seconds $W --settle 0 \
     --duties 0.25 --repeats 6 --warmup 5 --hidden-mode separate --adaptive \
     --label e1b-sep-w$W --out $O/E1b-sep-win$W.json
done
echo "=== inline, window 0.25  $(date -Is)"
$P e1_verifier_duty.py --python $P --window 60 --challenge-seconds 0.25 --settle 0 \
   --duties 0.25 --repeats 6 --warmup 5 --hidden-mode inline --adaptive \
   --label e1b-inl-w0.25 --out $O/E1b-inline-win0.25.json
echo "E1b done $(date -Is)"
