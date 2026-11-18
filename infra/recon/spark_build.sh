#!/bin/bash
# Build gsplat on the DGX Spark (aarch64, GB10 = sm_121). 2DGS only: the 3DGS and 3DGUT kernels are
# compiled out (one of them took over 25 min in cicc), 4 files at a time (each takes up to ~7 GB).
cd ~/ithaca-recon
export PATH=~/ithaca-recon/.venv/bin:/usr/local/cuda/bin:$PATH CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST="12.1" MAX_JOBS=4 BUILD_3DGS=0 BUILD_3DGUT=0
P=~/ithaca-recon/.venv/bin/pip
which ninja
(cd gsplat && git apply --check ../gsplat_local.patch 2>/dev/null && git apply ../gsplat_local.patch && echo "applied gsplat_local.patch")
rm -rf gsplat/build
(cd gsplat && $P install --no-build-isolation --no-deps -e . 2>&1 | grep -iE "error|Successfully" | tail -20)
$P install -q jaxtyping  # nerfview imports it; missing broke the first build check
$P install -q --no-build-isolation --no-deps "git+https://github.com/rahul-goel/fused-ssim@a7c48d6dd7ac6dc39a7958c7c4452e0b10418f38" 2>&1 | grep -iE "error" | head -5
.venv/bin/python -c "import torch, gsplat; from gsplat.rendering import rasterization_2dgs; import pycolmap, nerfview, tyro, torchmetrics, fused_ssim; print('gsplat', gsplat.__version__, torch.__version__, 'imports ok')"
echo BUILD_DONE
