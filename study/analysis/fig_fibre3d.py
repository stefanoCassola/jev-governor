"""3D renderings of fibre microstructures with streamlines, and mid-plane slices of the velocity magnitude.

usage: fig_fibre3d.py <fields_dir> <out_prefix>
<fields_dir> holds the .npz files written by controller/permeability/fg_voxelize.py (U, p, fluid mask on the
320^3 voxel grid). Rendering is single-process and light; images are composed with matplotlib.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyvista as pv
from matplotlib.colors import LinearSegmentedColormap, LogNorm

from style import INK2, SEQ_BLUE, setup

setup()
src, out = Path(sys.argv[1]), sys.argv[2]
CMAP = LinearSegmentedColormap.from_list("seqblue", SEQ_BLUE)
CASES = [  # file, flow axis, title, camera offset (domain edges), fibres removed along, half kept
    ("base_jevrr.npz", "x", "Base case: flow along the fibres ($x$)", (-1.9, -2.3, 1.7), "z", "lower"),
    ("fg_p90_x_jevrr.npz", "x", "90th-percentile case: fibres at 45$^\\circ$ to the flow ($x$)", (-1.2, -2.2, 2.4), "z", "lower"),
    ("fg_med_z_jevrr.npz", "z", "Median $z$ case: flow across the fibre layers ($z$)", (2.3, -2.3, 1.2), "y", "upper"),
]


def render(npz, axis, cam, cut, keep, n_seeds=260):
    z = np.load(npz)
    U, fluid, h = z["U"], z["fluid"], float(z["h"])
    n = fluid.shape[0]
    mag = np.linalg.norm(U, axis=-1); um = float(mag[fluid].mean())
    ax_i = "xyz".index(axis); L = n * h * 1e6; hm = h * 1e6
    grid = pv.ImageData(dimensions=(n, n, n), spacing=(hm,) * 3, origin=(hm / 2,) * 3)
    grid["U"] = (U / um).reshape(-1, 3, order="F"); grid["speed"] = (mag / um).ravel(order="F")
    ci = "xyz".index(cut)
    sl = [slice(None)] * 3; sl[ci] = slice(0, n // 2) if keep == "lower" else slice(n // 2, n)
    solid = np.pad((~fluid[tuple(sl)]).astype(np.float32), 1)        # pad: fibres are closed at the block faces
    org = np.full(3, -hm / 2); org[ci] += 0 if keep == "lower" else (n // 2) * hm
    blk = pv.ImageData(dimensions=solid.shape, spacing=(hm,) * 3, origin=tuple(org)); blk["solid"] = solid.ravel(order="F")
    fib = blk.contour([0.5], scalars="solid").smooth(n_iter=20, relaxation_factor=0.1)
    rng = np.random.default_rng(1)
    cand = np.argwhere(fluid.take(2, axis=ax_i)); w = mag.take(2, axis=ax_i)[cand[:, 0], cand[:, 1]]
    pick = cand[rng.choice(len(cand), n_seeds, replace=False, p=w / w.sum())]      # flux-weighted seeds on the inflow plane
    other = [i for i in range(3) if i != ax_i]
    pts = np.zeros((n_seeds, 3)); pts[:, ax_i] = 2.5 * hm
    pts[:, other[0]] = (pick[:, 0] + 0.5) * hm; pts[:, other[1]] = (pick[:, 1] + 0.5) * hm
    lines = grid.streamlines_from_source(pv.PolyData(pts), vectors="U", integration_direction="forward", max_length=6 * L,
                                         initial_step_length=0.5, max_step_length=1.0, terminal_speed=1e-4, max_steps=20000)
    pl = pv.Plotter(off_screen=True, window_size=(1700, 1500)); pl.set_background("white")
    pl.add_mesh(fib, color="#dcdad2", specular=0.15, smooth_shading=True)
    pl.add_mesh(lines.tube(radius=0.35), scalars="speed", cmap=CMAP, clim=(0, 6), show_scalar_bar=False, smooth_shading=True)
    pl.add_mesh(grid.outline(), color="#52514e", line_width=2)
    arrow_start = np.full(3, L / 2); arrow_start[ax_i] = -0.28 * L; arrow_start[2 if ax_i != 2 else 0] = 0 if ax_i != 2 else L / 2
    d = np.zeros(3); d[ax_i] = 1
    pl.add_mesh(pv.Arrow(start=arrow_start, direction=d, scale=0.22 * L, tip_radius=0.12, shaft_radius=0.045), color="#eb6834")
    pl.enable_anti_aliasing("ssaa")
    pl.camera_position = [tuple(L * 0.5 + L * np.array(cam)), (L / 2, L / 2, L * 0.45), (0, 0, 1)]
    img = pl.screenshot(None, return_img=True); pl.close()
    sl2 = np.where(fluid.take(n // 2, axis=ax_i), mag.take(n // 2, axis=ax_i) / um, np.nan)
    print(npz.name, "fluid fraction %.3f" % fluid.mean(), "triangles", fib.n_cells, "streamlines", lines.n_lines, flush=True)
    return img, sl2, L, other


def crop(img):
    m = np.argwhere((img[..., :3] < 250).any(axis=-1)); (r0, c0), (r1, c1) = m.min(0), m.max(0)
    return img[max(r0 - 10, 0):r1 + 10, max(c0 - 10, 0):c1 + 10]


fig = plt.figure(figsize=(7.4, 5.6))
gs = fig.add_gridspec(2, 3, height_ratios=[1.15, 1], hspace=0.22, wspace=0.28)
cm2 = CMAP.copy(); cm2.set_bad("#dcdad2")
for k, (f, axis, title, cam, cut, keep) in enumerate(CASES):
    img, sl2, L, other = render(src / f, axis, cam, cut, keep)
    a0 = fig.add_subplot(gs[0, k]); a0.imshow(crop(img)); a0.axis("off")
    a0.set_anchor("S")
    a0.set_title(f"({'abc'[k]}) " + title.split(": ")[0] + "\n" + title.split(": ")[1], loc="left", fontsize=8, linespacing=1.3)
    a1 = fig.add_subplot(gs[1, k])
    im = a1.imshow(sl2.T, origin="lower", extent=(0, L, 0, L), cmap=cm2, norm=LogNorm(vmin=0.02, vmax=20), interpolation="nearest")
    a1.set_xlabel(f"${'xyz'[other[0]]}$ (µm)", labelpad=1); a1.set_ylabel(f"${'xyz'[other[1]]}$ (µm)", labelpad=1); a1.grid(False)
    a1.tick_params(labelsize=7); a1.set_xticks([0, 40, 80, 120, 160]); a1.set_yticks([0, 40, 80, 120, 160])
    a1.set_title(f"({'def'[k]}) mid-plane normal to ${axis}$", loc="left", fontsize=8.5)
cb = fig.colorbar(im, ax=fig.axes, orientation="horizontal", fraction=0.025, pad=0.085, aspect=50)
cb.set_label(r"velocity magnitude relative to its pore-space mean, $|U|/\langle|U|\rangle$ (log scale; fibres grey)", fontsize=7.5)
cb.outline.set_visible(False); cb.ax.tick_params(labelsize=7)
fig.savefig(out + ".pdf", dpi=250); fig.savefig(out + ".png", dpi=170)
