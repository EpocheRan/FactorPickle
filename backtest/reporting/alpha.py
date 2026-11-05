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


def _benchmark_close(benchmark: pd.Series | pd.DataFrame | None) -> pd.Series:
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
    return series.dropna().sort_index()


def _benchmark_returns(benchmark: pd.Series | pd.DataFrame | None) -> pd.Series:
    return _benchmark_close(benchmark).pct_change().dropna()


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

    def _hac_alpha_t(y_values: np.ndarray, x_values: np.ndarray) -> float:
        n_obs = len(y_values)
        if n_obs < 20:
            return float("nan")
        design = np.column_stack([np.ones(n_obs), x_values])
        try:
            coef = np.linalg.lstsq(design, y_values, rcond=None)[0]
        except np.linalg.LinAlgError:
            return float("nan")
        resid = y_values - design @ coef
        xtx_inv = np.linalg.pinv(design.T @ design)
        lag = max(1, int(4 * (n_obs / 100.0) ** (2.0 / 9.0)))
        scores = design * resid[:, None]
        meat = scores.T @ scores
        for lag_i in range(1, lag + 1):
            weight = 1.0 - lag_i / (lag + 1.0)
            gamma = scores[lag_i:].T @ scores[:-lag_i]
            meat += weight * (gamma + gamma.T)
        cov = xtx_inv @ meat @ xtx_inv
        se = float(np.sqrt(max(cov[0, 0], 0.0)))
        return float(coef[0] / se) if se > 0 else float("nan")

    hac_t = _hac_alpha_t(y, x)

    return {
        "alpha_annualized_pct": alpha_daily * PERIODS_PER_YEAR * 100.0,
        "excess_arithmetic_pct": float((aligned["p"].mean() - aligned["b"].mean()) * PERIODS_PER_YEAR * 100.0),
        "beta_component_pct": float((1.0 - beta) * aligned["b"].mean() * PERIODS_PER_YEAR * 100.0),
        "decomposition_residual_pct": float(
            alpha_daily * PERIODS_PER_YEAR * 100.0
            - (aligned["p"].mean() - aligned["b"].mean()) * PERIODS_PER_YEAR * 100.0
            - (1.0 - beta) * aligned["b"].mean() * PERIODS_PER_YEAR * 100.0
        ),
        "alpha_daily": alpha_daily,
        "alpha_t": alpha_daily / se_alpha if se_alpha and np.isfinite(se_alpha) else float("nan"),
        "alpha_t_hac": hac_t,
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


def normalized_capm(
    equity: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None,
    exposure: list[dict],
    risk_free_rate: float = 0.0,
) -> dict[str, float]:
    """把日收益按当日总仓位归一化到 100%，再看 β/α。"""
    aligned = _aligned(equity, benchmark)
    if aligned.empty or not exposure:
        return {}
    gross = pd.Series(
        {
            pd.Timestamp(item["date"]): float(item.get("gross_pct") or 0.0) / 100.0
            for item in exposure
        }
    )
    gross = gross.reindex(aligned.index).ffill().bfill()
    fallback = float(gross[gross > 0].mean()) if (gross > 0).any() else 0.7
    gross = gross.fillna(fallback).clip(lower=0.05)
    adjusted = aligned["p"] / gross
    eq = pd.DataFrame(
        {
            "date": adjusted.index,
            "equity": (1.0 + adjusted).cumprod().to_numpy() * 1_000_000.0,
        }
    )
    bench = pd.Series(
        (1.0 + aligned["b"]).cumprod().to_numpy() * 1000.0,
        index=adjusted.index,
        name="benchmark",
    )
    stats = capm_stats(eq, bench, risk_free_rate)
    if not stats:
        return {}
    return {
        "beta_neutral": stats.get("beta"),
        "alpha_neutral_pct": stats.get("alpha_annualized_pct"),
        "alpha_neutral_t": stats.get("alpha_t"),
        "avg_gross_pct": float(gross.mean() * 100.0),
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
        bench = _benchmark_close(benchmark)
        if not bench.empty:
            nav = bench.resample("ME").last()
            returns = nav.pct_change()
            if len(nav):
                returns.iloc[0] = nav.iloc[0] / bench.iloc[0] - 1.0
            bench_monthly = returns.dropna() * 100.0
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


def risk_stats(
    equity: pd.DataFrame,
    risk_free_rate: float = 0.0,
    monthly: list[dict] | None = None,
) -> dict[str, float]:
    returns = _equity_returns(equity)
    if returns is None or returns.empty:
        return {}
    n = len(returns)
    annual = float((1.0 + returns).prod() ** (PERIODS_PER_YEAR / n) - 1.0)
    annual_arith = float(returns.mean() * PERIODS_PER_YEAR)
    vol = float(returns.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR))
    rf = float(risk_free_rate)
    mar = rf / PERIODS_PER_YEAR
    downside_dev = float(np.sqrt(np.mean(np.minimum(returns - mar, 0.0) ** 2)) * np.sqrt(PERIODS_PER_YEAR))
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).set_index("date").sort_index()
    values = pd.to_numeric(frame["equity"], errors="coerce")
    dd = values / values.cummax() - 1.0
    max_dd = float(dd.min()) if len(dd) else float("nan")
    sortino = (annual_arith - rf) / downside_dev if downside_dev > 0 else float("nan")
    sharpe_arith = (annual_arith - rf) / vol if vol > 0 else float("nan")
    calmar = annual / abs(max_dd) if max_dd < 0 else float("nan")
    var95 = float(returns.quantile(0.05) * 100.0)
    tail = returns[returns <= returns.quantile(0.05)]
    cvar95 = float(tail.mean() * 100.0) if len(tail) else float("nan")
    monthly_values = [item["ret"] for item in (monthly or []) if item.get("ret") is not None]
    monthly_win = (
        float(sum(1 for value in monthly_values if value > 0) / len(monthly_values) * 100.0)
        if monthly_values
        else float("nan")
    )

    def _period_return(window: int) -> float | None:
        if len(values) <= window:
            # 样本不足一个完整 window：若已接近 12M，用全样本累计收益代替并标注。
            if window == PERIODS_PER_YEAR and len(values) >= 120:
                return (float(values.iloc[-1]) / float(values.iloc[0]) - 1.0) * 100.0
            return None
        base = float(values.iloc[-1 - window])
        return (float(values.iloc[-1]) / base - 1.0) * 100.0 if base else None

    return {
        "annualized_return_pct": annual * 100.0,
        "annualized_return_arithmetic_pct": annual_arith * 100.0,
        "period_return_pct": (float(values.iloc[-1]) / float(values.iloc[0]) - 1.0) * 100.0,
        "return_12m_pct": _period_return(252),
        "return_36m_pct": _period_return(756),
        "volatility_pct": vol * 100.0,
        "downside_vol_pct": downside_dev * 100.0 if np.isfinite(downside_dev) else None,
        "max_drawdown_pct": max_dd * 100.0,
        "sharpe_arithmetic": sharpe_arith,
        "sortino": sortino,
        "calmar": calmar,
        "var95_pct": var95,
        "cvar95_pct": cvar95,
        "skew": float(returns.skew()),
        "kurtosis": float(returns.kurtosis()),
        "monthly_win_rate_pct": monthly_win,
        "observations": n,
        "annualization_note": f"年化按 252 交易日 / 实际 {n} 日折算；区间收益 {((float(values.iloc[-1]) / float(values.iloc[0]) - 1.0) * 100.0):.2f}%",
    }


def oos_stats(equity: pd.DataFrame, split: float = 0.7) -> dict[str, Any]:
    if equity is None or equity.empty or "equity" not in equity.columns:
        return {}
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).sort_values("date").reset_index(drop=True)
    if len(frame) < 60:
        return {}
    cut = max(20, min(len(frame) - 20, int(len(frame) * float(split))))
    split_date = str(pd.Timestamp(frame.iloc[cut]["date"]).date())

    def _segment(sub: pd.DataFrame) -> dict[str, float]:
        values = pd.to_numeric(sub["equity"], errors="coerce")
        returns = values.pct_change().dropna()
        total = float(values.iloc[-1] / values.iloc[0] - 1.0) if len(values) else float("nan")
        years = max(len(returns) / PERIODS_PER_YEAR, 1.0 / PERIODS_PER_YEAR)
        annual = float((1.0 + total) ** (1.0 / years) - 1.0) if total > -1 else float("nan")
        vol = float(returns.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR)) if len(returns) > 1 else float("nan")
        sharpe = annual / vol if vol and np.isfinite(vol) and vol > 0 else float("nan")
        if len(returns) < 120:
            annual = float("nan")
            sharpe = float("nan")
        dd = values / values.cummax() - 1.0
        return {
            "observations": int(len(returns)),
            "total_return_pct": total * 100.0,
            "annualized_return_pct": annual * 100.0,
            "volatility_pct": vol * 100.0 if np.isfinite(vol) else float("nan"),
            "sharpe": sharpe,
            "max_drawdown_pct": float(dd.min() * 100.0) if len(dd) else float("nan"),
        }

    return {
        "split": float(split),
        "split_date": split_date,
        "in_sample": _segment(frame.iloc[:cut]),
        "out_of_sample": _segment(frame.iloc[cut:]),
    }


def rolling_returns(equity: pd.DataFrame, windows: tuple[int, ...] = (252, 756)) -> list[dict]:
    if equity is None or equity.empty or "equity" not in equity.columns:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date", "equity"]).sort_values("date").reset_index(drop=True)
    values = pd.to_numeric(frame["equity"], errors="coerce").to_numpy(dtype=float)
    dates = list(frame["date"])
    rows = []
    for window in windows:
        for index in range(window, len(values)):
            base = values[index - window]
            if not np.isfinite(base) or base <= 0:
                continue
            rows.append(
                {
                    "date": str(pd.Timestamp(dates[index]).date()),
                    "window": int(window),
                    "ret": float((values[index] / base - 1.0) * 100.0),
                }
            )
    return rows


def concentration_table(
    positions: pd.DataFrame | None,
    equity: pd.DataFrame,
) -> list[dict]:
    if positions is None or positions.empty or "date" not in positions.columns:
        return []
    pos = positions.copy()
    pos["date"] = pd.to_datetime(pos["date"], errors="coerce").dt.normalize()
    pos = pos.dropna(subset=["date"])
    value_col = "market_value" if "market_value" in pos.columns else "close"
    pos["_value"] = pd.to_numeric(pos[value_col], errors="coerce").fillna(0.0)
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"], errors="coerce").dt.normalize()
    eq = eq.dropna(subset=["date"]).set_index("date")["equity"].astype(float)
    rows = []
    for dt, group in pos.groupby("date"):
        values = group["_value"].clip(lower=0.0)
        total = float(values.sum())
        equity_value = float(eq.get(dt, 0.0) or 0.0)
        if total <= 0 or equity_value <= 0:
            continue
        weights = values / total
        top10_mv = float(values.nlargest(10).sum())
        hhi = float((weights**2).sum())
        rows.append(
            {
                "date": str(pd.Timestamp(dt).date()),
                "top10_pct": top10_mv / equity_value * 100.0,
                "top10_pct_gross": top10_mv / total * 100.0,
                "hhi": hhi,
                "max_position_pct": float(values.max()) / equity_value * 100.0,
                "max_position_pct_gross": float(weights.max() * 100.0),
                "positions": int((values > 0).sum()),
                "gross_pct": total / equity_value * 100.0,
            }
        )
    return rows


def exposure_table(
    equity: pd.DataFrame,
    orders: pd.DataFrame | None = None,
    positions: pd.DataFrame | None = None,
) -> list[dict]:
    if equity is None or equity.empty:
        return []
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).sort_values("date")
    dates = list(frame["date"])
    counts: dict[pd.Timestamp, int] = {}
    if positions is not None and not positions.empty and "date" in positions.columns:
        pos = positions.copy()
        pos["date"] = pd.to_datetime(pos["date"], errors="coerce").dt.normalize()
        if "shares" in pos.columns:
            pos = pos[pd.to_numeric(pos["shares"], errors="coerce").fillna(0.0) > 0]
        counts = pos.groupby("date")["symbol"].nunique().to_dict()
    elif orders is not None and not orders.empty and {"date", "symbol", "type", "shares"} <= set(orders.columns):
        frame_orders = orders.copy()
        frame_orders["date"] = pd.to_datetime(frame_orders["date"], errors="coerce").dt.normalize()
        frame_orders = frame_orders.dropna(subset=["date"]).sort_values("date")
        events: dict[pd.Timestamp, list[tuple[str, float]]] = {}
        for row in frame_orders.itertuples(index=False):
            shares = float(getattr(row, "shares", 0.0) or 0.0)
            signed = shares if str(row.type).lower() == "buy" else -shares
            events.setdefault(pd.Timestamp(row.date), []).append((str(row.symbol), signed))
        holdings: dict[str, float] = {}
        for dt in dates:
            for symbol, signed in events.get(dt, []):
                holdings[symbol] = holdings.get(symbol, 0.0) + signed
            counts[dt] = sum(1 for value in holdings.values() if value > 1e-9)
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
                "positions": int(counts.get(pd.Timestamp(row.date), 0)),
            }
        )
    return rows
