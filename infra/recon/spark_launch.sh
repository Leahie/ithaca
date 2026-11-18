#!/bin/bash
# The Living Room 2DGS run on the DGX Spark. Needs spark_build.sh done and gsplat_local.patch applied
# (cd gsplat && git apply ../gsplat_local.patch), and make_masks.py, export_ply.py next to this file.
#
# Why the patch: this gsplat's fused 2DGS kernel computes normals-from-depth on every step, and where
# a photo shows nothing the room's cloud holds (other rooms, outdoors) that gives a zero vector whose
# normalization is NaN in the backward pass, even while the normal loss weight is still 0. The first
# run (Nov 14) never zeroed those: by step 7k 70% of the Gaussians were NaN and it rendered black
# (PSNR 3). The patch zeros non-finite gradients before Adam and densification, and reads per-image
# masks, so pixels outside the room's cloud are not trained on or scored.
set -euo pipefail
cd ~/ithaca-recon
export PATH=/usr/local/cuda/bin:$PATH CUDA_HOME=/usr/local/cuda
D=onXQLXhfyQv_cdz3fkt3 RUN=living_2k_v2
[ -d data/$D/masks_2 ] || .venv/bin/python make_masks.py data/$D 2
bash spark_train.sh $D $RUN --data_factor 2 --no-normalize-world-space --normal_loss --dist_loss \
  --test_every 8 --max_steps 30000 --save_steps 7000 15000 30000 --eval_steps 7000 15000 30000 > train_$RUN.log 2>&1
(cd gsplat && ../.venv/bin/python ../export_ply.py ../runs/$RUN/ckpts/ckpt_29999.pt ../runs/$RUN/$RUN.ply --box ../data/$D)
