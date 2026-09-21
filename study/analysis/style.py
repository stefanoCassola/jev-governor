"""Shared figure style (validated categorical order: blue, orange, aqua, yellow; neutral gray for defaults)."""
import matplotlib.pyplot as plt

C_DEF, C_ORA, C_HEU, C_JEV, C_4 = "#8a8984", "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
C_RED = "#e34948"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e0"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def setup():
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 8.5, "axes.edgecolor": INK2,
                         "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                         "grid.color": GRID, "grid.linewidth": 0.6, "legend.frameon": False,
                         "savefig.bbox": "tight", "savefig.dpi": 200, "pdf.fonttype": 42,
                         "axes.titlesize": 8.5, "axes.linewidth": 0.6})
