"""Shared matplotlib style for all dk1-t2t plots (one consistent look in docs/img)."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

# Categorical colours (colour-blind-safe ordering) used consistently across plots:
# HW / fabric = C0, SW / ARM = C1, model / prediction = C2.
PALETTE = ["#2364aa", "#e07a1f", "#3a9e5f", "#8f4fb1", "#c4413a", "#6b6b6b"]
HW, SW, MODEL = PALETTE[0], PALETTE[1], PALETTE[2]


def apply_style() -> None:
    plt.rcParams.update({
        "figure.dpi": 110,
        "savefig.dpi": 150,
        "axes.prop_cycle": plt.cycler(color=PALETTE),
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.6,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.titleweight": "bold",
        "legend.frameon": False,
    })


def save(fig, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
