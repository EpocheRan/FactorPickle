"""Squeeze Momentum Indicator（LazyBear）的 Python 实现。

对应 Pine Script::

    study("Squeeze Momentum Indicator [LazyBear]", overlay=false)
    length  = input(20)      // BB Length
    mult    = input(2.0)     // BB MultFactor（原脚本实际未使用）
    lengthKC= input(20)      // KC Length
    multKC  = input(1.5)     // KC MultFactor

    basis   = sma(close, length)
    dev     = multKC * stdev(close, length)      // 注意：原脚本用 multKC
    upperBB = basis + dev
    lowerBB = basis - dev

    ma      = sma(close, lengthKC)
    range   = useTrueRange ? tr : high - low
    rangema = sma(range, lengthKC)
    upperKC = ma + rangema * multKC
    lowerKC = ma - rangema * multKC

    sqzOn  = lowerBB > lowerKC and upperBB < upperKC
    sqzOff = lowerBB < lowerKC and upperBB > upperKC
    noSqz  = not sqzOn and not sqzOff

    val = linreg(close - avg(avg(highest(high, lengthKC), lowest(low, lengthKC)),
                             sma(close, lengthKC)), lengthKC, 0)

    bcolor = val > 0 ? (val > nz(val[1]) ? lime : green)
                     : (val < nz(val[1]) ? red  : maroon)
    scolor = noSqz ? blue : sqzOn ? black : gray

口径说明：
- Pine 的 ``stdev`` 是总体标准差（除以 N），对应 ``pandas.std(ddof=0)``；
- Pine 的 ``linreg(src, n, 0)`` 返回最近 n 根最小二乘回归线在当前 bar 的取值；
- 原脚本的 ``mult`` 输入没有被使用，BB 宽度实际用的是 ``multKC``；为保持
  与 TradingView 原图一致，默认复刻这一行为，可通过 ``bb_mult`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .atr import true_range

# LazyBear 原图配色
COLOR_LIME = "#00ff00"
COLOR_GREEN = "#008000"
COLOR_RED = "#ff0000"
COLOR_MAROON = "#800000"
COLOR_BLUE = "#0000ff"
COLOR_BLACK = "#000000"
COLOR_GRAY = "#808080"


def _series(value) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.astype("float64")
    return pd.Series(value, dtype="float64")


def linreg(series, length: int, offset: int = 0) -> pd.Series:
    """Pine ``linreg(source, length, offset)``。

    对最近 ``length`` 根做最小二乘直线拟合，返回该直线在“当前 bar 往前
    ``offset`` 根”处的取值；``offset=0`` 即当前 bar 的回归值（Pine 默认）。

    Pine 的窗口内 x 取 0..length-1，因此当前 bar 位于 x=length-1。
    """
    values = _series(series)
    n = max(1, int(length))
    if n == 1:
        return values.shift(-int(offset))

    x = np.arange(n, dtype="float64")
    x_mean = (n - 1) / 2.0
    sxx = float(((x - x_mean) ** 2).sum())

    y_mean = values.rolling(n, min_periods=n).mean()
    dot = values.rolling(n, min_periods=n).apply(lambda w: float(np.dot(w, x)), raw=True)
    slope = (dot - n * x_mean * y_mean) / sxx

    # 回归线在 x = (n-1-offset) 处的取值
    position = (n - 1 - int(offset)) - x_mean
    result = y_mean + slope * position
    result.name = f"linreg_{n}_{offset}"
    return result


@dataclass(frozen=True)
class SqueezeResult:
    """一次计算的完整输出，字段与 Pine 变量一一对应。"""

    basis: pd.Series
    upper_bb: pd.Series
    lower_bb: pd.Series
    ma_kc: pd.Series
    range_ma: pd.Series
    upper_kc: pd.Series
    lower_kc: pd.Series
    sqz_on: pd.Series
    sqz_off: pd.Series
    no_sqz: pd.Series
    val: pd.Series
    bar_color: pd.Series
    dot_color: pd.Series

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "basis": self.basis,
                "upper_bb": self.upper_bb,
                "lower_bb": self.lower_bb,
                "ma_kc": self.ma_kc,
                "range_ma": self.range_ma,
                "upper_kc": self.upper_kc,
                "lower_kc": self.lower_kc,
                "sqz_on": self.sqz_on,
                "sqz_off": self.sqz_off,
                "no_sqz": self.no_sqz,
                "val": self.val,
                "bar_color": self.bar_color,
                "dot_color": self.dot_color,
            }
        )


def squeeze_momentum(
    high,
    low,
    close,
    length: int = 20,
    mult: float = 2.0,
    length_kc: int = 20,
    mult_kc: float = 1.5,
    use_tr: bool = True,
    *,
    bb_mult: float | None = None,
) -> SqueezeResult:
    """计算 Squeeze Momentum。

    Args:
        high/low/close: 等长的价格序列（list 或 pandas Series）。
        length: BB 周期，默认 20。
        mult: BB 倍数输入；原脚本未使用，仅用于 API 对齐。
        length_kc: KC 周期，默认 20。
        mult_kc: KC 倍数，默认 1.5；原脚本同时拿它算 BB 宽度。
        use_tr: KC 的 range 是否用 True Range，默认 True。
        bb_mult: 覆盖 BB 宽度倍数。默认 ``None`` 表示复刻 Pine 原脚本
            （用 ``mult_kc``）；传 ``mult`` 可得到“修正版”。

    Returns:
        ``SqueezeResult``，``to_frame()`` 得到逐 bar 的 DataFrame。
    """
    high_s = _series(high)
    low_s = _series(low)
    close_s = _series(close)

    bb_width = (mult if bb_mult is not None else mult_kc)

    # --- BB ---
    basis = close_s.rolling(length, min_periods=length).mean()
    dev = bb_width * close_s.rolling(length, min_periods=length).std(ddof=0)
    upper_bb = basis + dev
    lower_bb = basis - dev

    # --- KC ---
    ma_kc = close_s.rolling(length_kc, min_periods=length_kc).mean()
    price_range = true_range(high_s, low_s, close_s) if use_tr else high_s - low_s
    range_ma = price_range.rolling(length_kc, min_periods=length_kc).mean()
    upper_kc = ma_kc + range_ma * mult_kc
    lower_kc = ma_kc - range_ma * mult_kc

    # --- squeeze state ---
    sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)
    no_sqz = ~sqz_on & ~sqz_off

    # --- momentum ---
    hh = high_s.rolling(length_kc, min_periods=length_kc).max()
    ll = low_s.rolling(length_kc, min_periods=length_kc).min()
    midline = ((hh + ll) / 2.0 + close_s.rolling(length_kc, min_periods=length_kc).mean()) / 2.0
    val = linreg(close_s - midline, length_kc, 0)

    # --- colors ---
    prev = val.shift(1).fillna(0.0)
    positive = val > 0
    rising = val > prev
    bar_color = np.select(
        [positive & rising, positive & ~rising, ~positive & ~rising, ~positive & rising],
        [COLOR_LIME, COLOR_GREEN, COLOR_RED, COLOR_MAROON],
        default=COLOR_MAROON,
    )
    dot_color = np.select(
        [no_sqz, sqz_on],
        [COLOR_BLUE, COLOR_BLACK],
        default=COLOR_GRAY,
    )

    index = close_s.index
    bar_color = pd.Series(bar_color, index=index, name="bar_color").where(val.notna(), None)
    dot_color = pd.Series(dot_color, index=index, name="dot_color").where(basis.notna(), None)
    return SqueezeResult(
        basis=basis.rename("basis"),
        upper_bb=upper_bb.rename("upper_bb"),
        lower_bb=lower_bb.rename("lower_bb"),
        ma_kc=ma_kc.rename("ma_kc"),
        range_ma=range_ma.rename("range_ma"),
        upper_kc=upper_kc.rename("upper_kc"),
        lower_kc=lower_kc.rename("lower_kc"),
        sqz_on=pd.Series(sqz_on, index=index, name="sqz_on"),
        sqz_off=pd.Series(sqz_off, index=index, name="sqz_off"),
        no_sqz=pd.Series(no_sqz, index=index, name="no_sqz"),
        val=val.rename("val"),
        bar_color=bar_color,
        dot_color=dot_color,
    )


class SqueezeMomentum:
    """面向 DataFrame 的封装，沿用 ``ATR.calculate`` 的调用方式。"""

    def __init__(
        self,
        length: int = 20,
        mult: float = 2.0,
        length_kc: int = 20,
        mult_kc: float = 1.5,
        use_tr: bool = True,
        bb_mult: float | None = None,
    ) -> None:
        self.length = int(length)
        self.mult = float(mult)
        self.length_kc = int(length_kc)
        self.mult_kc = float(mult_kc)
        self.use_tr = bool(use_tr)
        self.bb_mult = bb_mult

    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(data, pd.DataFrame):
            raise TypeError("SqueezeMomentum.calculate expects a pandas DataFrame")
        frame = data.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        missing = {"high", "low", "close"} - set(frame.columns)
        if missing:
            raise ValueError(f"SqueezeMomentum data missing columns: {sorted(missing)}")
        return squeeze_momentum(
            frame["high"],
            frame["low"],
            frame["close"],
            length=self.length,
            mult=self.mult,
            length_kc=self.length_kc,
            mult_kc=self.mult_kc,
            use_tr=self.use_tr,
            bb_mult=self.bb_mult,
        ).to_frame()
