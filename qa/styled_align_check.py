"""Global alignment of a styled plate vs its depth-proven flux base (kit pipeline styled-layer gate v0).
Pure numpy: FFT phase correlation for translation at several candidate scales; reports the best
(scale, dx, dy) and the response strength. Styled plate inherits the base's registration iff the
best transform is near-identity (|dx|,|dy| <= 10px ~ 0.2 cell at ortho 11.79, scale within 2%)."""
import sys, numpy as np
from PIL import Image

def luma(p, size=(1344, 768)):
    im = Image.open(p).convert('L')
    if im.size != size: im = im.resize(size, Image.LANCZOS)
    a = np.asarray(im, dtype=np.float64)
    a -= a.mean(); s = a.std();
    return a / s if s > 0 else a

def phasecorr(a, b):
    # hann window to suppress edge wrap
    wy = np.hanning(a.shape[0])[:, None]; wx = np.hanning(a.shape[1])[None, :]
    A = np.fft.rfft2(a * wy * wx); B = np.fft.rfft2(b * wy * wx)
    R = A * np.conj(B); R /= np.abs(R) + 1e-9
    r = np.fft.irfft2(R, a.shape)
    peak = np.unravel_index(np.argmax(r), r.shape)
    dy, dx = peak
    if dy > a.shape[0] // 2: dy -= a.shape[0]
    if dx > a.shape[1] // 2: dx -= a.shape[1]
    return dx, dy, float(r.max())

def scaled(img, s):
    if abs(s - 1.0) < 1e-6: return img
    h, w = img.shape
    im = Image.fromarray(((img - img.min()) / (np.ptp(img) + 1e-9) * 255).astype(np.uint8))
    sw, sh = int(round(w * s)), int(round(h * s))
    im = im.resize((sw, sh), Image.LANCZOS)
    a = np.asarray(im, dtype=np.float64)
    # center-crop or pad back to (h, w)
    out = np.zeros((h, w)); ys = max(0, (sh - h)//2); xs = max(0, (sw - w)//2)
    yd = max(0, (h - sh)//2); xd = max(0, (w - sw)//2)
    ch = min(h, sh); cw = min(w, sw)
    out[yd:yd+ch, xd:xd+cw] = a[ys:ys+ch, xs:xs+cw]
    out -= out.mean(); s2 = out.std()
    return out / s2 if s2 > 0 else out

base = luma(sys.argv[1])
for cand_path in sys.argv[2:]:
    cand = luma(cand_path)
    best = None
    for s in (0.94, 0.97, 1.0, 1.03, 1.06):
        dx, dy, resp = phasecorr(scaled(cand, s), base)
        if best is None or resp > best[3]: best = (s, dx, dy, resp)
    s, dx, dy, resp = best
    ok = abs(dx) <= 10 and abs(dy) <= 10 and abs(s - 1.0) <= 0.02
    name = cand_path.split('/')[-1]
    print(f"{name}: scale={s} dx={dx} dy={dy} resp={resp:.3f} -> {'ALIGNED' if ok else 'GLOBAL-TRANSFORM'}")
