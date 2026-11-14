#!/bin/bash
# One 2DGS training run on the DGX Spark (GB10). Usage: spark_train.sh <dataset dir name> <run name> [trainer args]
set -euo pipefail
W=~/ithaca-recon
export PATH=/usr/local/cuda/bin:$PATH CUDA_HOME=/usr/local/cuda
DATA="$W/data/$1"; RUN="$W/runs/$2"; shift 2
mkdir -p "$RUN"
cd "$W/gsplat/examples"
"$W/.venv/bin/python" simple_trainer_2dgs.py --data_dir "$DATA" --result_dir "$RUN" --disable_viewer "$@"
echo TRAIN_DONE
