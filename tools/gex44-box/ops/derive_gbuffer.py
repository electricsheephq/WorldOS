# Derive a depth + view-space normal map FROM an image (Depth-Anything-V2), matching the diffuse by construction
# → no structure-drift artifacts, works on a frame-filling high-Q diffuse. Output encoding matches WOSRelight.shader.
import sys, numpy as np
from PIL import Image
from transformers import pipeline
inp, out_depth, out_normal = sys.argv[1], sys.argv[2], sys.argv[3]
pipe = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf")
img = Image.open(inp).convert("RGB")
res = pipe(img)
d = np.array(res["depth"], dtype=np.float32)
d = (d - d.min()) / (d.max() - d.min() + 1e-6)   # 0..1, near=1 (Depth-Anything: larger=closer)
# depth png (grayscale) — scaled so nearer=smaller lin (invert to match greybox LinDepth where near=small)
depth_lin = 1.0 - d                               # near -> small (matches greybox -view.z/80 near-small)
Image.fromarray((depth_lin*255).astype(np.uint8)).convert("RGB").save(out_depth)
# normal from depth gradient (Sobel-ish). z toward camera (view space), rgb = n*0.5+0.5
gy, gx = np.gradient(d.astype(np.float32))
STR = 6.0
nx, ny, nz = -gx*STR, gy*STR, np.ones_like(d)
ln = np.sqrt(nx*nx + ny*ny + nz*nz) + 1e-6
nx, ny, nz = nx/ln, ny/ln, nz/ln
nrm = np.stack([nx*0.5+0.5, ny*0.5+0.5, nz*0.5+0.5], axis=-1)
Image.fromarray((nrm*255).astype(np.uint8)).save(out_normal)
print("DERIVED", out_depth, out_normal, "shape", d.shape)
