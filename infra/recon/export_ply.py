"""A 2DGS checkpoint from simple_trainer_2dgs.py as a standard 3DGS .ply (what Spark and most viewers read).

2DGS Gaussians are flat disks: the trainer ignores the third scale, so it is set thin here and a
3DGS renderer draws the same surfels. Gaussians that are nearly transparent, non-finite, or (with
--box) outside the room's box are dropped. The frame is the twin's interior frame (Y up, meters),
the same as interior.glb, because training ran with --no-normalize-world-space.

    python export_ply.py runs/<run>/ckpts/ckpt_29999.pt out.ply [--box data/<set>/volume.json or data/<set>] [--sh 0..3]
"""

import argparse
import json

import numpy as np
import torch
from gsplat.exporter import export_splats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ckpt")
    ap.add_argument("out")
    ap.add_argument("--box", help="volume.json with lo/hi corners; keep Gaussians inside it, grown by --margin")
    ap.add_argument("--margin", type=float, default=0.3)
    ap.add_argument("--min_opacity", type=float, default=0.01)
    ap.add_argument("--sh", type=int, default=3, help="SH degree to keep (0 makes a much smaller file)")
    ap.add_argument("--thin", type=float, default=1e-3, help="thickness (m) given to the unused third scale")
    a = ap.parse_args()

    s = torch.load(a.ckpt, map_location="cpu", weights_only=False)["splats"]
    means, scales, quats, opac = s["means"], s["scales"].clone(), s["quats"], s["opacities"]
    keep = torch.isfinite(means).all(1) & torch.isfinite(scales).all(1) & torch.isfinite(quats).all(1)
    keep &= torch.isfinite(opac) & (torch.sigmoid(opac) >= a.min_opacity)
    if a.box:
        if a.box.endswith(".json"):
            v = json.load(open(a.box))
            lo, hi = torch.tensor(v["lo"]), torch.tensor(v["hi"])
        else:  # a dataset dir: the box of its initial points (the room crop from gs_export.py)
            pts = np.loadtxt(f"{a.box}/sparse/0/points3D.txt", usecols=(1, 2, 3), dtype=np.float32)
            lo, hi = torch.from_numpy(pts.min(0)), torch.from_numpy(pts.max(0))
        lo, hi = lo - a.margin, hi + a.margin
        keep &= ((means >= lo) & (means <= hi)).all(1)
    scales[:, 2] = torch.log(torch.tensor(a.thin))
    k = (a.sh + 1) ** 2 - 1
    export_splats(means[keep], scales[keep], quats[keep], opac[keep], s["sh0"][keep], s["shN"][keep][:, :k],
                  format="ply", save_to=a.out)
    print(f"{int(keep.sum()):,} of {len(means):,} Gaussians -> {a.out}")


if __name__ == "__main__":
    main()
