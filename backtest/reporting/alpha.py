"""CAPM / alpha-beta / 滚动指标 / 月度与回撤。"""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 252.0


def _equity_returns(equity: pd.DataFrame) -> pd.Series:
    if equity is None or equity.empty or "equity" not in equity.columns:
        return pd.Series(dtype="float64")
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    return pd.to_numeric(frame["equity"], errors="coerce").pct_change().dropna()


def _benchmark_returns(benchmark: pd.Series | pd.DataFrame | None) -> pd.Series:
    if benchmark is None:
        return pd.Series(dtype="float64")
    if isinstance(benchmark, pd.DataFrame):
        if benchmark.empty:
            return pd.Series(dtype="float64")
        frame = benchmark.copy()
        if "date" in frame.columns:
            frame = frame.set_index("date")
        column = "close" if "close" in frame.columns else frame.columns[-1]
        series = pd.to_numeric(frame[column], errors="coerce")
    else:
        series = pd.to_numeric(benchmark.copy(), errors="coerce")
    series.index = pd.to_datetime(series.index, errors="coerce").normalize()
    series = series.dropna().sort_index()
    return series.pct_change().dropna()


def _aligned(
    equity: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None,
) -> pd.DataFrame:
    rp = _equity_returns(equity).rename("p")
    rb = _benchmark_returns(benchmark).rename("b")
    if rp.empty or rb.empty:
        return pd.DataFrame(columns=["p", "b"])
    return pd.concat([rp, rb], axis=1).dropna()


def capm_stats(
    equity: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None,
    risk_free_rate: float = 0.0,
) -> dict[str, float]:
    aligned = _aligned(equity, benchmark)
    if len(aligned) < 20:
        return {}
    rf = float(risk_free_rate) / PERIODS_PER_YEAR
    x = (aligned["b"] - rf).to_numpy(dtype=float)
    y = (aligned["p"] - rf).to_numpy(dtype=float)
    var_x = float(np.var(x, ddof=1))
    if var_x <= 0:
        return {}
    beta = float(np.cov(y, x, ddof=1)[0, 1] / var_x)
    alpha_daily = float(y.mean() - beta * x.mean())
    residuals = y - (alpha_daily + beta * x)
    sse = float(np.sum(residuals**2))
    n = len(x)
    sigma2 = sse / max(1, n - 2)
    x_mean = float(x.mean())
    sxx = float(np.sum((x - x_mean) ** 2))
    se_beta = float(np.sqrt(sigma2 / sxx)) if sxx > 0 else float("nan")
    se_alpha = (
        float(np.sqrt(sigma2 * (1.0 / n + x_mean**2 / sxx))) if sxx > 0 else float("nan")
    )
    total_var = float(np.var(y, ddof=1))
    r2 = float(1.0 - sse / (total_var * (n - 1))) if total_var > 0 and n > 1 else float("nan")
    active = aligned["p"] - aligned["b"]
    te = float(active.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR)) if len(active) > 1 else 0.0
    ir = (
        float(active.mean() / active.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR))
        if len(active) > 1 and active.std(ddof=1) > 0
        else float("nan")
    )

    def _capture(mask: pd.Series) -> float:
        if mask.sum() < 2:
            return float("nan")
        up = aligned.loc[mask, "p"].mean()
        bench = aligned.loc[mask, "b"].mean()
        return float(up / bench) if bench != 0 else float("nan")

    def _beta(mask: pd.Series) -> float:
        sub = aligned.loc[mask]
        if len(sub) < 10 or sub["b"].var(ddof=1) <= 0:
            return float("nan")
        return float(sub["p"].cov(sub["b"]) / sub["b"].var(ddof=1))

    return {
        "alpha_annualized_pct": alpha_daily * PERIODS_PER_YEAR * 100.0,
        "alpha_daily": alpha_daily,
        "alpha_t": alpha_daily / se_alpha if se_alpha and np.isfinite(se_alpha) else float("nan"),
        "beta": beta,
        "beta_se": se_beta,
        "r2": r2,
        "tracking_error_pct": te * 100.0,
        "information_ratio": ir,
        "up_capture": _capture(aligned["b"] > 0),
        "down_capture": _capture(aligned["b"] < 0),
        "bull_beta": _beta(aligned["b"] > 0),
        "bear_beta": _beta(aligned["b"] < 0),
        "observations": float(n),
    }


def rolling_stats(
    equity: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None,
    risk_free_rate: float = 0.0,
    windows: tuple[int, ...] = (60, 120, 250),
) -> list[dict]:
    aligned = _aligned(equity, benchmark)
    if aligned.empty:
        return []
    rf = float(risk_free_rate) / PERIODS_PER_YEAR
    x = aligned["b"] - rf
    y = aligned["p"] - rf
    rows: list[dict] = []
    for window in windows:
        if len(aligned) < window:
            continue
        beta = y.rolling(window).cov(x) / x.rolling(window).var()
        alpha = y.rolling(window).mean() - beta * x.rolling(window).mean()
        alpha_ann = alpha * PERIODS_PER_YEAR * 100.0
        for dt, b, a in zip(aligned.index, beta, alpha_ann):
            if pd.isna(b) or pd.isna(a):
                continue
            rows.append(
                {
                    "date": str(pd.Timestamp(dt).date()),
                    "window": int(window),
                    "beta": float(b),
                    "alpha_pct": float(a),
                }
            )
    return rows


def monthly_returns(
    equity: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None = None,
) -> list[dict]:
    if equity is None or equity.empty:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    monthly_nav = frame["equity"].resample("ME").last()
    monthly = monthly_nav.pct_change()
    if len(monthly_nav):
        monthly.iloc[0] = monthly_nav.iloc[0] / frame["equity"].iloc[0] - 1.0
    monthly = monthly.dropna() * 100.0
    bench_monthly = None
    if benchmark is not None:
        br = _benchmark_returns(benchmark)
        if not br.empty:
            # 由日收益重建净值，再取月末
            nav = (1.0 + br).cumprod()
            bench_monthly = nav.resample("ME").last().pct_change().dropna() * 100.0
    rows = []
    for dt, value in monthly.items():
        row = {"year": int(dt.year), "month": int(dt.month), "ret": float(value)}
        if bench_monthly is not None and dt in bench_monthly.index:
            row["bench_ret"] = float(bench_monthly.loc[dt])
        rows.append(row)
    return rows


def yearly_returns(equity: pd.DataFrame) -> list[dict]:
    if equity is None or equity.empty:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    yearly_nav = frame["equity"].resample("YE").last()
    yearly = yearly_nav.pct_change()
    if len(yearly_nav):
        yearly.iloc[0] = yearly_nav.iloc[0] / frame["equity"].iloc[0] - 1.0
    yearly = yearly.dropna() * 100.0
    return [{"year": int(dt.year), "ret": float(value)} for dt, value in yearly.items()]


def drawdown_episodes(equity: pd.DataFrame, top: int = 5) -> list[dict]:
    if equity is None or equity.empty:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    values = pd.to_numeric(frame["equity"], errors="coerce")
    peak = values.cummax()
    dd = values / peak - 1.0
    episodes = []
    start = None
    for dt, value in dd.items():
        if value < -1e-12 and start is None:
            start = dt
        elif value >= -1e-12 and start is not None:
            segment = dd.loc[start:dt]
            trough = segment.idxmin()
            episodes.append(
                {
                    "start": str(start.date()),
                    "trough": str(pd.Timestamp(trough).date()),
                    "end": str(dt.date()),
                    "depth_pct": float(segment.min() * 100.0),
                    "days": int((dt - start).days),
                }
            )
            start = None
    if start is not None:
        segment = dd.loc[start:]
        trough = segment.idxmin()
        episodes.append(
            {
                "start": str(start.date()),
                "trough": str(pd.Timestamp(trough).date()),
                "end": None,
                "depth_pct": float(segment.min() * 100.0),
                "days": int((dd.index[-1] - start).days),
            }
        )
    episodes.sort(key=lambda item: item["depth_pct"])
    return episodes[:top]


def exposure_table(equity: pd.DataFrame) -> list[dict]:
    if equity is None or equity.empty:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    rows = []
    for row in frame.itertuples(index=False):
        total = float(getattr(row, "equity", 0.0) or 0.0)
        market = float(getattr(row, "market_value", 0.0) or 0.0)
        cash = float(getattr(row, "cash", 0.0) or 0.0)
        rows.append(
            {
                "date": str(pd.Timestamp(row.date).date()),
                "gross_pct": market / total * 100.0 if total else 0.0,
                "cash_pct": cash / total * 100.0 if total else 0.0,
                "positions": int(getattr(row, "positions", 0) or 0),
            }
        )
    return rows
