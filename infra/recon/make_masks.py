"""Per-image masks for 2DGS training: pixels the room's point cloud covers (seen from that camera),
grown a little. Photos from neighboring sweeps mostly show other rooms or the outdoors, which the
cropped cloud does not hold; without a mask the trainer chases them with floaters.

    python make_masks.py <data_dir> <factor>   ->  <data_dir>/masks_<factor>/<image>.png (255 = train)
"""
import os, sys, numpy as np, torch, torch.nn.functional as F, imageio.v2 as imageio
sys.path.insert(0, os.path.expanduser("~/ithaca-recon/gsplat/examples"))
from datasets.colmap import Parser
from gsplat.rendering import rasterization_2dgs
torch.set_grad_enabled(False)
data, factor = sys.argv[1], int(sys.argv[2])
p = Parser(data_dir=data, factor=factor, normalize=False, test_every=10**9)
out = os.path.join(data, f"masks_{factor}"); os.makedirs(out, exist_ok=True)
dev = "cuda"
pts = torch.from_numpy(p.points).float().to(dev)
N = len(pts)
scales = torch.full((N, 3), 0.012, device=dev)
quats = F.normalize(torch.randn(N, 4, device=dev), dim=-1)
opac = torch.full((N,), 0.99, device=dev)
ones = torch.ones(1, N, 3, device=dev)
def grow(m, r):  # binary dilation by a square of radius r
    return F.max_pool2d(m, 2 * r + 1, 1, r)
cover = []
for i, name in enumerate(p.image_names):
    cid = p.camera_ids[i]
    K = torch.from_numpy(p.Ks_dict[cid]).float().to(dev)
    W, H = p.imsize_dict[cid]
    _, a, *_ = rasterization_2dgs(means=pts, quats=quats, scales=scales, opacities=opac, colors=ones,
        viewmats=torch.linalg.inv(torch.from_numpy(p.camtoworlds[i]).float().to(dev))[None], Ks=K[None],
        width=W, height=H, near_plane=0.05)
    m = (a[0, ..., 0] > 0.5).float()[None, None]
    r = max(2, W // 256)
    m = 1 - grow(1 - grow(m, r), r)   # close small holes
    m = grow(m, r)                     # then a margin
    m = m[0, 0].cpu().numpy() > 0.5
    cover.append(m.mean())
    imageio.imwrite(os.path.join(out, os.path.splitext(name)[0] + ".png"), (m * 255).astype(np.uint8))
print(f"{len(cover)} masks in {out}; coverage median {np.median(cover):.2f}, min {np.min(cover):.2f}")
