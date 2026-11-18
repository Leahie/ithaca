#!/bin/bash
# The Living Room 2DGS run on the DGX Spark. Needs spark_build.sh done and gsplat_local.patch applied
# (cd gsplat && git apply ../gsplat_local.patch), and make_masks.py, export_ply.py next to this file.
#
# The dataset: python -m services.api.twin.gs_export onXQLXhfyQv --room cdz3fkt38kae7tapstpt0eaeb --fuse
#   --out out/gs/onXQLXhfyQv_cdz3fkt3_v3, then python infra/recon/fuse_depth.py prompt <that dir>,
#   then rsync it (without cloud.npz) to data/ here.
#
# Why the patch: this gsplat's fused 2DGS kernel computes normals-from-depth on every step, and where
# a photo shows nothing the room's cloud holds (other rooms, outdoors) that gives a zero vector whose
# normalization is NaN in the backward pass, even while the normal loss weight is still 0. The first
# run (Nov 14) never zeroed those: by step 7k 70% of the Gaussians were NaN and it rendered black
# (PSNR 3). The patch zeros non-finite gradients before Adam and densification, and reads per-image
# masks, so pixels outside the room's cloud are not trained on or scored.
#
# Why depth (v3, Nov 18): v2 (no depth) scored PSNR 20 but smeared the floor and left floaters, as
# 2DGS does from 35 tripod spots with no parallax inside a panorama. The patch also reads the dense
# depth fuse_depth.py rendered from the scanner's cloud (prompt/<image>.npy) as --depth_loss's target.
# v3 also has the ceiling (gs_export.py used to crop it off) and the upward faces that see it.
# v2 was killed at step 24k with no 30k checkpoint, so v3 saves every 5k.
set -euo pipefail
cd ~/ithaca-recon
export PATH=/usr/local/cuda/bin:$PATH CUDA_HOME=/usr/local/cuda
D=onXQLXhfyQv_cdz3fkt3_v3 RUN=living_v3_depth
[ -d data/$D/masks_2 ] || .venv/bin/python make_masks.py data/$D 2
bash spark_train.sh $D $RUN --data_factor 2 --no-normalize-world-space --normal_loss --dist_loss \
  --depth_loss --depth_lambda 0.05 \
  --test_every 8 --max_steps 30000 --save_steps 5000 10000 15000 20000 25000 30000 \
  --eval_steps 7000 15000 30000 > train_$RUN.log 2>&1
(cd gsplat && ../.venv/bin/python ../export_ply.py ../runs/$RUN/ckpts/ckpt_29999.pt ../runs/$RUN/$RUN.ply --box ../data/$D)
