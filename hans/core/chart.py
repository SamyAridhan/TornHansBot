"""Weekly stat-growth chart (PNG via matplotlib — the only non-stdlib dep)."""

from __future__ import annotations

import io


def render_png(series: list[dict]) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    BG, FG, GRID, ACCENT = "#0f1420", "#e6e6e6", "#2a2f3a", "#5aa9ff"
    SUB = {"strength": "#ff7a7a", "defense": "#7ad1ff",
           "speed": "#8affc1", "dexterity": "#ffd27a"}
    xs = [r["dt"] for r in series]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for key, color in SUB.items():
        ax.plot(xs, [r[key] for r in series], color=color, linewidth=1.2,
                alpha=0.8, label=key.capitalize())
    ax.plot(xs, [r["total"] for r in series], color=ACCENT, linewidth=2.6,
            label="Total", zorder=5)
    ax.set_title("Hans's Ledger — stat growth", color=FG, fontsize=14, pad=12)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=FG, labelsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.autofmt_xdate(rotation=0, ha="center")
    leg = ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8,
                    loc="upper left", framealpha=0.6)
    for t in leg.get_texts():
        t.set_color(FG)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
