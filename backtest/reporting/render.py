"""零依赖交互式 HTML 报告：内嵌 JSON + SVG/vanilla JS。

不依赖 Plotly/ECharts/CDN；单文件离线可看。
"""

from __future__ import annotations

import html as html_lib
import json
from typing import Any

_CSS = """
:root{--bg:#0d1117;--panel:#151b23;--line:#222b36;--ink:#e6edf3;--muted:#8b949e;
--pos:#3fb950;--neg:#f85149;--acc:#d4a24c;--blue:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 70px}
header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-end}
h1{font-size:21px;margin:0 0 3px}
.sub{color:var(--muted);font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:1px;
background:var(--line);border:1px solid var(--line);margin:20px 0 16px}
.kpi{background:var(--panel);padding:12px 14px}
.kpi .v{font-size:18px;font-weight:600;white-space:nowrap}
.kpi .k{font-size:11px;color:var(--muted);margin-top:2px}
.kpi .s{font-size:11px;color:var(--muted);margin-top:1px}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--muted)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:16px;overflow-x:auto}
.tab{background:none;border:0;color:var(--muted);padding:9px 13px;cursor:pointer;font-size:13px;white-space:nowrap}
.tab.active{color:var(--acc);border-bottom:2px solid var(--acc)}
.pane{display:none}.pane.active{display:block}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:14px}
.card-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap}
.card-head h3{font-size:12px;color:var(--muted);font-weight:500;letter-spacing:.5px;margin:0;text-transform:uppercase}
.chart{position:relative;width:100%}
.chart svg{width:100%;height:auto;display:block}
.chart .tip{position:absolute;pointer-events:none;background:#0b1016;border:1px solid var(--line);
border-radius:5px;padding:7px 9px;font-size:11.5px;color:var(--ink);display:none;z-index:5;min-width:150px;
box-shadow:0 6px 18px rgba(0,0,0,.45)}
.chart .tip b{display:block;margin-bottom:3px;color:var(--muted);font-weight:500}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:500;background:#11161d;position:sticky;top:0}
table.meta{table-layout:fixed}
table.meta th{width:165px;text-align:left;vertical-align:top}
table.meta td{white-space:normal;overflow-wrap:anywhere;word-break:break-word;vertical-align:top}
.btn{background:#1c2430;border:1px solid var(--line);color:var(--ink);border-radius:4px;
padding:5px 9px;font-size:11.5px;cursor:pointer}
.btn.active{border-color:var(--acc);color:var(--acc)}
.btn:hover{border-color:var(--muted)}
.controls{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:4px}
.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:1px 8px;
margin:2px 4px 0 0;color:var(--muted);font-size:11px}
.heat td{padding:4px 6px;text-align:center;border:1px solid #0d1117;border-radius:3px;cursor:pointer}
.heat th{text-align:center}
.note{color:var(--muted);font-size:11.5px;margin:4px 0 0}
.checks{display:flex;flex-direction:column;gap:7px}
.check{display:flex;gap:8px;align-items:flex-start;font-size:11.8px;line-height:1.45}
.check .badge{flex:0 0 auto;border-radius:3px;padding:0 5px;font-size:10.5px;font-weight:600}
.check.pass .badge{background:rgba(63,185,80,.16);color:var(--pos)}
.check.warn .badge{background:rgba(212,162,76,.16);color:var(--acc)}
.check.fail .badge{background:rgba(248,81,73,.16);color:var(--neg)}
.check .detail{color:var(--muted)}
.warnbox{border:1px solid rgba(212,162,76,.35);background:rgba(212,162,76,.08);color:var(--acc);
border-radius:4px;padding:7px 9px;font-size:11.5px;margin:8px 0 0}
.warnings{display:flex;flex-direction:column;gap:6px;margin:0 0 14px}
.warnbanner{border:1px solid rgba(212,162,76,.35);background:rgba(212,162,76,.08);
color:var(--acc);border-radius:5px;padding:8px 10px;font-size:11.8px}
.scroll{max-height:430px;overflow:auto;border:1px solid var(--line);border-radius:5px}
input.filter{background:#0b1016;border:1px solid var(--line);color:var(--ink);
border-radius:4px;padding:5px 8px;font-size:12px;min-width:150px}
.hbar{height:8px;border-radius:4px;background:#1c2430;overflow:hidden}
.hbar i{display:block;height:100%}
"""

_HEAD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>__CSS__</style></head>
<body><div class="wrap">
<header><div><h1>__TITLE__</h1><div class="sub">__SUBTITLE__</div></div>
<div class="sub">__PERIOD__</div></header>
__KPIS__
<div class="warnings">__WARNINGS__</div>
<nav class="tabs">
<button class="tab active" data-tab="overview">概览</button>
<button class="tab" data-tab="performance">绩效</button>
<button class="tab" data-tab="risk">风险</button>
<button class="tab" data-tab="trades">交易</button>
<button class="tab" data-tab="exposure">暴露</button>
<button class="tab" data-tab="attribution">归因</button>
<button class="tab" data-tab="details">明细</button>
</nav>
<main>
<section class="pane active" id="tab-overview">
  <div class="card"><div class="card-head"><h3>净值 vs 基准（起点=100）</h3>
    <div class="controls">
      <button class="btn" data-range="63">3M</button><button class="btn" data-range="126">6M</button>
      <button class="btn" data-range="252">1Y</button><button class="btn active" data-range="all">全部</button>
      <label class="muted"><input type="checkbox" id="benchToggle" checked> 基准</label>
      <button class="btn" id="equityCsv">下载净值</button>
    </div></div>
    <div class="chart"><svg id="equityChart"></svg><div class="tip" id="equityTip"></div></div>
    <div class="legend"><span><i style="background:var(--acc)"></i>策略</span>
      <span><i style="background:var(--blue)"></i>基准 __BENCH__</span></div>
  </div>
  <div class="card"><div class="card-head"><h3>回撤</h3><span class="muted" id="ddLabel"></span></div>
    <div class="chart"><svg id="drawdownChart"></svg><div class="tip" id="ddTip"></div></div></div>
  <div class="grid2">
    <div class="card"><div class="card-head"><h3>核心指标</h3></div><table class="meta"><tbody>__METRIC_ROWS__</tbody></table></div>
    <div class="card"><div class="card-head"><h3>Alpha / Beta</h3></div><table class="meta"><tbody>__ALPHA_ROWS__</tbody></table>__ALPHA_WARN__</div>
  </div>
  <div class="grid2">
    <div class="card"><div class="card-head"><h3>已实现 / 未实现收益桥</h3></div>
      <table class="meta"><tbody>__BRIDGE_ROWS__</tbody></table></div>
    <div class="card"><div class="card-head"><h3>数据自检</h3></div>
      <div class="checks">__CHECKS__</div></div>
  </div>
</section>
<section class="pane" id="tab-performance">
  <div class="card"><div class="card-head"><h3>月度收益（%）</h3><span class="muted">点击月份筛选交易</span></div>
    <div id="monthlyHeat" class="heat"></div></div>
  <div class="card"><div class="card-head"><h3>月度超额（策略 − 基准，%）</h3>
    <span class="muted">__MONTHLY_SUMMARY__</span></div>
    <div class="scroll"><table><thead><tr><th>年</th><th>月</th><th>策略</th><th>基准</th><th>超额</th></tr></thead>
    <tbody>__MONTHLY_EXCESS_ROWS__</tbody></table></div></div>
  <div class="card"><div class="card-head"><h3>年度收益（%）</h3></div>
    <div class="chart"><svg id="yearlyChart"></svg><div class="tip" id="yearlyTip"></div></div></div>
  <div class="card"><div class="card-head"><h3>样本内 / 样本外（70/30）</h3></div>
    <table><thead><tr><th>区间</th><th>样本</th><th>累计</th><th>年化</th><th>波动</th><th>Sharpe</th><th>MaxDD</th></tr></thead>
    <tbody>__OOS_ROWS__</tbody></table></div>
  <div class="card"><div class="card-head"><h3>滚动收益（12M / 36M）</h3></div>
    <div class="chart"><svg id="rollingReturnChart"></svg><div class="tip" id="rollingReturnTip"></div></div></div>
  <div class="card"><div class="card-head"><h3>Regime / 尾部依赖</h3></div>
    <table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>__REGIME_ROWS__</tbody></table></div>
  <div class="grid2">
    <div class="card"><div class="card-head"><h3>滚动 Alpha / Beta</h3>
      <div class="controls"><button class="btn" data-window="60">60D</button><button class="btn" data-window="120">120D</button>
      <button class="btn" data-window="250">250D</button></div></div>
      <div class="chart"><svg id="rollingChart"></svg><div class="tip" id="rollingTip"></div></div>
      <div class="legend"><span><i style="background:var(--acc)"></i>Alpha %</span><span><i style="background:var(--blue)"></i>Beta</span></div>
    </div>
    <div class="card"><div class="card-head"><h3>日收益 vs 基准</h3></div>
      <div class="chart"><svg id="scatterChart"></svg></div>
      <div class="note" id="scatterNote"></div></div>
  </div>
</section>
<section class="pane" id="tab-risk">
  <div class="grid2"><div class="card"><div class="card-head"><h3>风险指标</h3></div><table class="meta"><tbody>__RISK_ROWS__</tbody></table>__RISK_NOTE__</div>
  <div class="card"><div class="card-head"><h3>回撤事件</h3></div><div class="scroll"><table><thead><tr><th>开始</th><th>最低</th><th>修复</th><th>深度</th><th>天数</th></tr></thead><tbody>__DD_ROWS__</tbody></table></div></div></div>
</section>
<section class="pane" id="tab-trades">
  <div class="kpis">__TRADE_KPIS__</div>
  <div class="card"><div class="card-head"><h3>收益分布（%）</h3></div>
    <div class="chart"><svg id="histChart"></svg><div class="tip" id="histTip"></div></div></div>
  <div class="card"><div class="card-head"><h3>成本敏感性（年化拖累）</h3>
    <span class="muted">基于当前单边换手 __TURNOVER__</span></div>
    <table><thead><tr><th>单边额外成本（bps）</th><th>年化拖累</th></tr></thead><tbody>__COST_ROWS__</tbody></table></div>
  <div class="card"><div class="card-head"><h3>回合交易</h3>
    <div class="controls"><input class="filter" id="tradeFilter" placeholder="按代码筛选">
    <span class="muted" id="tradeCount"></span><button class="btn" id="tradeCsv">下载 CSV</button></div></div>
    <div class="scroll"><table><thead><tr>
      <th data-sort="symbol">代码</th><th data-sort="entry_date">入场</th><th data-sort="exit_date">出场</th>
      <th data-sort="entry_price">入场价</th><th data-sort="exit_price">出场价</th><th data-sort="shares">股数</th>
      <th data-sort="pnl">净盈亏</th><th data-sort="ret">收益率</th><th data-sort="fees">费用</th><th data-sort="days">天数</th></tr></thead>
      <tbody id="tradeBody"></tbody></table></div>
    <div class="controls" style="margin-top:8px"><button class="btn" id="tradePrev">上一页</button>
      <span class="muted" id="tradePage"></span><button class="btn" id="tradeNext">下一页</button></div>
  </div>
</section>
<section class="pane" id="tab-exposure">
  <div class="card"><div class="card-head"><h3>总仓位 / 现金</h3></div>
    <div class="chart"><svg id="exposureChart"></svg><div class="tip" id="exposureTip"></div></div>
    <div class="legend"><span><i style="background:var(--acc)"></i>总仓位 %</span><span><i style="background:var(--blue)"></i>现金 %</span></div></div>
  <div class="card"><div class="card-head"><h3>集中度（最新）</h3></div>
    <table class="meta"><tbody>__CONC_ROWS__</tbody></table>
    <p class="note">HHI = Σ(w_i²)，w_i 为持仓市值权重；Top10/最大单票同时给“占权益”和“占持仓”两种口径。</p></div>
</section>
<section class="pane" id="tab-attribution">
  <div class="card"><div class="card-head"><h3>基准对比</h3><span class="muted">Alpha / Beta / IR / R²</span></div>
    <table><thead><tr><th>基准</th><th>Alpha(年化)</th><th>Beta</th><th>IR</th><th>R²</th></tr></thead>
    <tbody>__ALT_ROWS__</tbody></table></div>
  <div class="grid2"><div class="card"><div class="card-head"><h3>个股贡献 Top</h3><span class="muted">全样本合计 __ATTR_TOTAL__ · Top __ATTR_TOP__ (__ATTR_SHARE__%)</span></div>
    <table><thead><tr><th>代码</th><th>净盈亏</th><th>回合数</th></tr></thead><tbody>__ATTR_ROWS__</tbody></table></div>
  <div class="card"><div class="card-head"><h3>个股贡献 Bottom</h3></div>
    <table><thead><tr><th>代码</th><th>净盈亏</th><th>回合数</th></tr></thead><tbody>__ATTR_BOTTOM_ROWS__</tbody></table></div></div>
  <div class="card"><div class="card-head"><h3>选股历史</h3><span class="muted">__SEL_COUNT__ 条</span></div>
    <div class="scroll"><table><thead><tr><th>日期</th><th>代码</th><th>score</th><th>rank</th><th>weight</th></tr></thead><tbody>__SEL_ROWS__</tbody></table></div>
    <p class="note">__SEL_FWD__</p></div>
  <div class="grid2"><div class="card"><div class="card-head"><h3>行业暴露（最新）</h3></div>
    <table><thead><tr><th>行业</th><th>占权益</th><th>市值</th></tr></thead><tbody>__IND_ROWS__</tbody></table></div>
  <div class="card"><div class="card-head"><h3>行业贡献</h3></div>
    <table><thead><tr><th>行业</th><th>净盈亏</th><th>回合数</th></tr></thead><tbody>__IND_PNL_ROWS__</tbody></table></div></div>
  <p class="note">行业暴露为期末时点、行业分类来自当前映射；行业贡献为全样本。</p>
  __IND_WARN__
  <div class="grid2"><div class="card"><div class="card-head"><h3>板块暴露（最新选股）</h3></div>
    <table><thead><tr><th>板块</th><th>只数</th><th>占比</th></tr></thead><tbody>__BOARD_ROWS__</tbody></table>
    <p class="note">__BSE_NOTE__</p></div>
  <div class="card"><div class="card-head"><h3>市值对账</h3></div>
    <table class="meta"><tbody>__VAL_ROWS__</tbody></table></div></div>
</section>
<section class="pane" id="tab-details">
  <div class="card"><div class="card-head"><h3>运行信息</h3><button class="btn" id="jsonCsv">下载 report_data.json</button></div>
    <table class="meta"><tbody>__META_ROWS__</tbody></table></div>
  <div class="card"><div class="card-head"><h3>口径</h3></div>
    <table class="meta"><tbody>
      <tr><th>年化</th><td>252 个交易日；回撤天数用日历天</td></tr>
      <tr><th>Sharpe</th><td>算术年化超额 / 年化波动</td></tr>
      <tr><th>Sortino</th><td>算术年化超额 / 下行偏差；MAR=0，下行偏差 = √mean(min(r,0)²)×√252</td></tr>
      <tr><th>Calmar</th><td>几何年化收益 / |最大回撤|；三个比率分母不同，不能直接横比</td></tr>
      <tr><th>偏度/峰度</th><td>日收益偏度；峰度为 Fisher 超额峰度（正态=0）</td></tr>
      <tr><th>IR</th><td>主动收益算术均值 / 跟踪误差；不是 Alpha/TE</td></tr>
      <tr><th>超额</th><td>“超额收益”为累计收益简单差；“几何超额”为 (1+Rp)/(1+Rb)−1</td></tr>
      <tr><th>捕获率</th><td>上涨/下跌日的策略平均收益 ÷ 基准平均收益</td></tr>
      <tr><th>牛熊 β</th><td>上涨/下跌日分别回归的斜率，与捕获率不是同一指标</td></tr>
      <tr><th>回合</th><td>fills 按 FIFO 批次配对，一笔买入可拆成多个回合</td></tr>
      <tr><th>退出原因</th><td>rebalance_out=调仓剔除；rebalance_trim=同标的减仓；rebalance_in=调仓买入；buffer_out 已废弃（旧版非调仓日清仓）</td></tr>
      <tr><th>费用/换手</th><td>双边佣金+印花税+过户费；换手为年化单边买入额 / 平均权益</td></tr>
      <tr><th>Alpha</th><td>日收益 OLS 截距 ×252；R²&lt;0.5 时报告给出警告</td></tr>
    </tbody></table>
    <p class="note">报告由 backtest.reporting 同源生成，不重跑回测；回合交易与净值桥接见“概览”。</p></div>
</section>
</main>
<p class="note">generated __GENERATED__ · __RUN_ID__</p>
</div>
<script>const REPORT = __DATA__;</script>
<script>__JS__</script>
</body></html>"""

_JS = r"""
const $=id=>document.getElementById(id);
const D=REPORT;
const fmtPct=(v,d=2)=>v==null?'—':(v>=0?'+':'')+Number(v).toFixed(d)+'%';
const fmtNum=(v,d=2)=>v==null?'—':Number(v).toFixed(d);
const fmtMoney=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
const cls=v=>v==null?'':v>0?'pos':v<0?'neg':'';
const dates=D.equity.map(x=>x.date);
const eq=D.equity.map(x=>x.equity);
const dd=D.equity.map(x=>x.drawdown);
const benchMap={};(D.benchmark.series||[]).forEach(x=>benchMap[x.date]=x.close);
const benchDates=D.benchmark.series?D.benchmark.series.map(x=>x.date):[];
const benchClose=D.benchmark.series?D.benchmark.series.map(x=>x.close):[];
let state={range:'all',showBench:true,window:60,tradeFilter:'',tradeSort:{key:'exit_date',dir:-1},page:0};

function tab(name){
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  if(name==='performance'){drawMonthly();drawRolling();drawScatter();drawRollingReturns();}
  if(name==='trades'){renderTrades();}
  if(name==='exposure'){drawExposure();}
  if(location.hash.slice(1)!==name) history.replaceState(null,'','#'+name);
}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>tab(b.dataset.tab));

function rangeStart(n){
  if(n==='all'||!n) return 0;
  const k=Number(n); return Math.max(0,dates.length-k);
}
function lineChart(svg,tip,labels,series,opt={}){
  const W=900,H=opt.height||280,pl=54,pr=16,pt=16,pb=28;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const n=labels.length;
  if(!n){svg.innerHTML='<text x="20" y="30" fill="#8b949e">无数据</text>';return;}
  let vals=[];series.forEach(s=>s.values.forEach(v=>{if(Number.isFinite(v))vals.push(v);}));
  let lo=Math.min(...vals),hi=Math.max(...vals);
  if(!Number.isFinite(lo)||!Number.isFinite(hi)){svg.innerHTML='';return;}
  if(hi===lo){hi+=1;lo-=1;}
  const pad=(hi-lo)*0.06;lo-=pad;hi+=pad;
  const x=i=>pl+(W-pl-pr)*(n===1?0.5:i/(n-1));
  const y=v=>pt+(H-pt-pb)*(1-(v-lo)/(hi-lo));
  let g='';
  for(let k=0;k<=4;k++){
    const yy=pt+(H-pt-pb)*k/4, val=hi-(hi-lo)*k/4;
    g+=`<line x1="${pl}" y1="${yy}" x2="${W-pr}" y2="${yy}" stroke="#222b36" stroke-width="1"/>`;
    g+=`<text x="${pl-7}" y="${yy+4}" text-anchor="end" fill="#8b949e" font-size="10">${opt.yFmt?opt.yFmt(val):fmtNum(val)}</text>`;
  }
  g+=`<text x="${pl}" y="${H-8}" fill="#8b949e" font-size="10">${labels[0]||''}</text>`;
  g+=`<text x="${W-pr}" y="${H-8}" text-anchor="end" fill="#8b949e" font-size="10">${labels[n-1]||''}</text>`;
  series.forEach(s=>{
    let path='';
    s.values.forEach((v,i)=>{if(Number.isFinite(v))path+=(path?'L':'M')+x(i).toFixed(1)+','+y(v).toFixed(1);});
    if(s.area&&path){
      const first=s.values.findIndex(v=>Number.isFinite(v));
      const last=s.values.length-1-s.values.slice().reverse().findIndex(v=>Number.isFinite(v));
      g+=`<path d="${path}L${x(last)},${y(0)}L${x(first)},${y(0)}Z" fill="${s.color}" opacity="0.12"/>`;
    }
    g+=`<path d="${path}" fill="none" stroke="${s.color}" stroke-width="${s.width||1.8}"/>`;
  });
  g+=`<rect x="${pl}" y="${pt}" width="${W-pl-pr}" height="${H-pt-pb}" fill="transparent"/>`;
  svg.innerHTML=g;
  const NS='http://www.w3.org/2000/svg';
  const layer=document.createElementNS(NS,'g');
  layer.setAttribute('pointer-events','none');
  const vline=document.createElementNS(NS,'line');
  vline.setAttribute('stroke','#8b949e');
  vline.setAttribute('stroke-dasharray','3 3');
  vline.setAttribute('stroke-width','1');
  vline.style.display='none';
  layer.appendChild(vline);
  const dots=series.map(s=>{
    const dot=document.createElementNS(NS,'circle');
    dot.setAttribute('r','4.2');
    dot.setAttribute('fill',s.color);
    dot.setAttribute('stroke','#0d1117');
    dot.setAttribute('stroke-width','1.5');
    dot.style.display='none';
    layer.appendChild(dot);
    return dot;
  });
  svg.appendChild(layer);
  const wrap=svg.parentElement;
  const rect=()=>svg.getBoundingClientRect();
  svg.onmousemove=e=>{
    const r=rect(), mx=e.clientX-r.left;
    if(mx<pl||mx>W-pr){tip.style.display='none';vline.style.display='none';dots.forEach(d=>d.style.display='none');return;}
    const i=Math.max(0,Math.min(n-1,Math.round((mx-pl)/((W-pl-pr)/Math.max(1,n-1)))));
    vline.setAttribute('x1',x(i));vline.setAttribute('x2',x(i));
    vline.setAttribute('y1',pt);vline.setAttribute('y2',H-pb);
    vline.style.display='';
    series.forEach((s,j)=>{
      const v=s.values[i];
      if(Number.isFinite(v)){
        dots[j].setAttribute('cx',x(i));
        dots[j].setAttribute('cy',y(v));
        dots[j].style.display='';
      }else{
        dots[j].style.display='none';
      }
    });
    let html=`<b>${labels[i]}</b>`;
    series.forEach(s=>{if(Number.isFinite(s.values[i]))html+=`<div style="color:${s.color}">${s.name}: ${opt.tipFmt?opt.tipFmt(s.values[i]):fmtNum(s.values[i])}</div>`;});
    tip.innerHTML=html;tip.style.display='block';
    const left=(x(i)/W)*r.width;
    tip.style.left=Math.min(r.width-170,Math.max(0,left+10))+'px';
    tip.style.top=(e.clientY-r.top+10)+'px';
  };
  svg.onmouseleave=()=>{tip.style.display='none';vline.style.display='none';dots.forEach(d=>d.style.display='none');};
}

function drawEquity(){
  const start=rangeStart(state.range);
  const labels=dates.slice(start), eqs=eq.slice(start);
  const base=eqs[0]||1;
  const series=[{name:'策略',color:'#d4a24c',values:eqs.map(v=>v/base*100)}];
  if(state.showBench&&benchClose.length){
    const bm={};D.benchmark.series.forEach(x=>bm[x.date]=x.close);
    const b0=bm[labels[0]];
    series.push({name:'基准',color:'#58a6ff',values:labels.map(d=>bm[d]==null?null:bm[d]/b0*100)});
  }
  lineChart($('equityChart'),$('equityTip'),labels,series,{height:300,
    tipFmt:v=>fmtNum(v,2)});
  const ddv=dd.slice(start);
  lineChart($('drawdownChart'),$('ddTip'),labels,[{name:'回撤',color:'#f85149',values:ddv,area:true}],
    {height:150,yFmt:v=>fmtNum(v*100,1)+'%',tipFmt:v=>fmtNum(v*100,2)+'%'});
  const min=Math.min(...ddv);
  $('ddLabel').textContent='区间最低 '+fmtNum(min*100,2)+'%';
}

function drawRollingReturns(){
  const rows=D.rolling_returns||[];
  const w12=rows.filter(x=>x.window===252);
  const w36=rows.filter(x=>x.window===756);
  if(!w12.length){$('rollingReturnChart').innerHTML='<text x="20" y="30" fill="#8b949e">样本不足 12M</text>';return;}
  const labels=w12.map(x=>x.date);
  const map36={};w36.forEach(x=>map36[x.date]=x.ret);
  const values36=labels.map(d=>map36[d]==null?null:map36[d]);
  lineChart($('rollingReturnChart'),$('rollingReturnTip'),labels,[
    {name:'12M',color:'#d4a24c',values:w12.map(x=>x.ret)},
    {name:'36M',color:'#58a6ff',values:values36}
  ],{height:250,yFmt:v=>fmtNum(v,0)+'%',tipFmt:v=>fmtNum(v,2)+'%'});
}

function drawMonthly(){
  const el=$('monthlyHeat');
  const data=D.monthly||[];
  if(!data.length){el.innerHTML='<p class="muted">无月度数据</p>';return;}
  const years=[...new Set(data.map(x=>x.year))].sort();
  const map={};data.forEach(x=>map[x.year+'-'+x.month]=x);
  let h='<table><thead><tr><th>年</th>';
  for(let m=1;m<=12;m++)h+=`<th>${String(m).padStart(2,'0')}</th>`;
  h+='</tr></thead><tbody>';
  years.forEach(y=>{
    h+=`<tr><th>${y}</th>`;
    for(let m=1;m<=12;m++){
      const x=map[y+'-'+m];
      if(!x){h+='<td class="muted">—</td>';continue;}
      const v=x.ret;
      const bg=v>0?`rgba(63,185,80,${Math.min(0.65,0.12+Math.abs(v)/12)})`:
        `rgba(248,81,73,${Math.min(0.65,0.12+Math.abs(v)/12)})`;
      h+=`<td style="background:${bg}" title="${y}-${String(m).padStart(2,'0')}: ${fmtPct(v)}" onclick="filterMonth(${y},${m})">${fmtNum(v,1)}</td>`;
    }
    h+='</tr>';
  });
  el.innerHTML=h+'</tbody></table>';
}
function filterMonth(y,m){
  state.tradeFilter=`${y}-${String(m).padStart(2,'0')}`;
  $('tradeFilter').value=state.tradeFilter;
  state.page=0;tab('trades');
}

function drawRolling(){
  const w=state.window;
  const rows=(D.rolling||[]).filter(x=>x.window===w);
  if(!rows.length){$('rollingChart').innerHTML='<text x="20" y="30" fill="#8b949e">样本不足</text>';return;}
  lineChart($('rollingChart'),$('rollingTip'),rows.map(x=>x.date),[
    {name:'Alpha %',color:'#d4a24c',values:rows.map(x=>x.alpha_pct)},
    {name:'Beta',color:'#58a6ff',values:rows.map(x=>x.beta)}
  ],{height:250,tipFmt:v=>fmtNum(v,3)});
}

function drawScatter(){
  const svg=$('scatterChart');const W=560,H=320,pl=58,pr=16,pt=16,pb=56;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!D.benchmark||!D.benchmark.series||!D.benchmark.series.length){
    svg.innerHTML='<text x="20" y="30" fill="#8b949e">未配置基准</text>';return;
  }
  const bm={};D.benchmark.series.forEach(x=>bm[x.date]=x.close);
  const bd=benchDates;const pts=[];
  const em={};D.equity.forEach(x=>em[x.date]=x.ret);
  for(let i=1;i<bd.length;i++){
    const r=bm[bd[i]]/bm[bd[i-1]]-1, s=em[bd[i]]/100;
    if(Number.isFinite(r)&&Number.isFinite(s))pts.push([r*100,s*100]);
  }
  if(pts.length<10){svg.innerHTML='<text x="20" y="30" fill="#8b949e">无基准/样本不足</text>';return;}
  const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
  let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const pad=1;x0-=pad;x1+=pad;y0-=pad;y1+=pad;
  const X=v=>pl+(W-pl-pr)*(v-x0)/(x1-x0), Y=v=>pt+(H-pt-pb)*(1-(v-y0)/(y1-y0));
  let g='';
  for(let k=0;k<=4;k++){
    const yy=pt+(H-pt-pb)*k/4;
    g+=`<line x1="${pl}" y1="${yy}" x2="${W-pr}" y2="${yy}" stroke="#222b36"/>`;
    const xv=x0+(x1-x0)*k/4, xx=X(xv);
    g+=`<line x1="${xx}" y1="${pt}" x2="${xx}" y2="${H-pb}" stroke="#1b232d"/>`;
    g+=`<text x="${xx}" y="${H-pb+16}" text-anchor="middle" fill="#8b949e" font-size="10">${fmtNum(xv,1)}%</text>`;
    const yv=y1-(y1-y0)*k/4;
    g+=`<text x="${pl-7}" y="${Y(yv)+4}" text-anchor="end" fill="#8b949e" font-size="10">${fmtNum(yv,1)}%</text>`;
  }
  g+=`<line x1="${pl}" y1="${H-pb}" x2="${W-pr}" y2="${H-pb}" stroke="#555"/>`;
  g+=`<line x1="${pl}" y1="${pt}" x2="${pl}" y2="${H-pb}" stroke="#555"/>`;
  if(x0<0&&x1>0)g+=`<line x1="${X(0)}" y1="${pt}" x2="${X(0)}" y2="${H-pb}" stroke="#3a4654"/>`;
  if(y0<0&&y1>0)g+=`<line x1="${pl}" y1="${Y(0)}" x2="${W-pr}" y2="${Y(0)}" stroke="#3a4654"/>`;
  g+=`<text x="${(pl+W-pr)/2}" y="${H-6}" text-anchor="middle" fill="#8b949e" font-size="10.5">基准日收益 %</text>`;
  g+=`<text transform="translate(13 ${(pt+H-pb)/2}) rotate(-90)" text-anchor="middle" fill="#8b949e" font-size="10.5">策略日收益 %</text>`;
  pts.forEach(p=>{g+=`<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="1.7" fill="#58a6ff" opacity=".55"/>`;});
  const n=pts.length,sx=xs.reduce((a,b)=>a+b,0),sy=ys.reduce((a,b)=>a+b,0);
  const sxx=xs.reduce((a,b)=>a+b*b,0),sxy=pts.reduce((a,p)=>a+p[0]*p[1],0);
  const slope=(n*sxy-sx*sy)/(n*sxx-sx*sx||1),inter=(sy-slope*sx)/n;
  g+=`<line x1="${X(x0)}" y1="${Y(slope*x0+inter)}" x2="${X(x1)}" y2="${Y(slope*x1+inter)}" stroke="#d4a24c" stroke-width="1.6"/>`;
  svg.innerHTML=g;
  $('scatterNote').textContent=`斜率 ${fmtNum(slope,2)} · 截距 ${fmtNum(inter,3)}% · ${n} 个交易日`;
}

function drawYearly(){
  const rows=D.yearly||[];const svg=$('yearlyChart');
  const W=900,H=220,pl=50,pr=14,pt=14,pb=26;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!rows.length){svg.innerHTML='';return;}
  const vals=rows.map(x=>x.ret);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);
  const span=hi-lo||1;hi+=span*.08;lo-=span*.08;
  const y=v=>pt+(H-pt-pb)*(1-(v-lo)/(hi-lo));
  const bw=(W-pl-pr)/rows.length*.6;
  let g=`<line x1="${pl}" y1="${y(0)}" x2="${W-pr}" y2="${y(0)}" stroke="#444"/>`;
  rows.forEach((x,i)=>{
    const cx=pl+(W-pl-pr)*(i+.5)/rows.length, yy=y(x.ret), y0=y(0);
    g+=`<rect x="${cx-bw/2}" y="${Math.min(yy,y0)}" width="${bw}" height="${Math.max(1,Math.abs(yy-y0))}" fill="${x.ret>=0?'#3fb950':'#f85149'}" opacity=".8"/>`;
    g+=`<text x="${cx}" y="${H-8}" text-anchor="middle" fill="#8b949e" font-size="10">${x.year}</text>`;
    g+=`<text x="${cx}" y="${yy-4}" text-anchor="middle" fill="#8b949e" font-size="10">${fmtNum(x.ret,1)}</text>`;
  });
  svg.innerHTML=g;
}

function drawHist(){
  const trades=D.trades||[];const svg=$('histChart');const tip=$('histTip');
  const W=900,H=230,pl=40,pr=14,pt=14,pb=30;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!trades.length){svg.innerHTML='';return;}
  const vals=trades.map(t=>(Number.isFinite(t.ret)?t.ret*100:0));
  let lo=Math.floor(Math.min(...vals)/2)*2, hi=Math.ceil(Math.max(...vals)/2)*2;
  if(hi-lo<4){hi=lo+4;}
  const bins={};for(let b=lo;b<hi;b+=2)bins[b]=0;
  vals.forEach(v=>{
    let b=Math.floor(v/2)*2;
    if(b<lo)b=lo; if(b>=hi)b=hi-2;
    bins[b]=(bins[b]||0)+1;
  });
  const keys=Object.keys(bins).map(Number).sort((a,b)=>a-b);
  const max=Math.max(...Object.values(bins),1);
  const bw=(W-pl-pr)/keys.length;
  const labelEvery=Math.max(1,Math.ceil(keys.length/10));
  let g=`<line x1="${pl}" y1="${H-pb}" x2="${W-pr}" y2="${H-pb}" stroke="#444"/>`;
  keys.forEach((k,i)=>{
    const h=(H-pt-pb)*bins[k]/max, x=pl+i*bw+1;
    g+=`<rect class="hist-bar" data-bin="${k}" data-count="${bins[k]}" x="${x}" y="${H-pb-h}" width="${bw-2}" height="${h}" fill="${k>=0?'#3fb950':'#f85149'}" opacity=".82" style="cursor:pointer"/>`;
    if(i%labelEvery===0)g+=`<text x="${x+bw/2}" y="${H-8}" text-anchor="middle" fill="#8b949e" font-size="9">${k}%</text>`;
  });
  svg.innerHTML=g;
  const total=trades.length;
  svg.querySelectorAll('rect.hist-bar').forEach(rect=>{
    rect.addEventListener('mousemove',e=>{
      const bin=Number(rect.dataset.bin), count=Number(rect.dataset.count);
      const pct=total?count/total*100:0;
      tip.innerHTML=`<b>${bin}% ~ ${bin+2}%</b><div>交易数：${count}</div><div>占比：${pct.toFixed(1)}%</div>`;
      tip.style.display='block';
      const box=svg.parentElement.getBoundingClientRect();
      tip.style.left=Math.min(box.width-150,Math.max(0,e.clientX-box.left+12))+'px';
      tip.style.top=Math.max(0,e.clientY-box.top-8)+'px';
      rect.setAttribute('opacity','1');
    });
    rect.addEventListener('mouseleave',()=>{tip.style.display='none';rect.setAttribute('opacity','.82');});
  });
}

function drawExposure(){
  const rows=D.exposure||[];
  if(!rows.length){$('exposureChart').innerHTML='<text x="20" y="30" fill="#8b949e">无数据</text>';return;}
  lineChart($('exposureChart'),$('exposureTip'),rows.map(x=>x.date),[
    {name:'总仓位 %',color:'#d4a24c',values:rows.map(x=>x.gross_pct)},
    {name:'现金 %',color:'#58a6ff',values:rows.map(x=>x.cash_pct)}
  ],{height:300,yFmt:v=>fmtNum(v,0)+'%',tipFmt:v=>fmtNum(v,1)+'%'});
}

function renderTrades(){
  let rows=(D.trades||[]).slice();
  const f=state.tradeFilter.trim();
  if(f)rows=rows.filter(t=>String(t.symbol).includes(f)||String(t.exit_date||'').startsWith(f)||String(t.entry_date||'').startsWith(f));
  const key=state.tradeSort.key,dir=state.tradeSort.dir;
  rows.sort((a,b)=>{const av=a[key],bv=b[key];return (av>bv?1:av<bv?-1:0)*dir;});
  const pageSize=40;const pages=Math.max(1,Math.ceil(rows.length/pageSize));
  state.page=Math.max(0,Math.min(pages-1,state.page));
  const slice=rows.slice(state.page*pageSize,(state.page+1)*pageSize);
  $('tradeBody').innerHTML=slice.map(t=>`<tr>
    <td>${t.symbol}</td><td>${t.entry_date||''}</td><td>${t.exit_date||''}</td>
    <td>${fmtNum(t.entry_price,2)}</td><td>${fmtNum(t.exit_price,2)}</td><td>${fmtNum(t.shares,0)}</td>
    <td class="${cls(t.pnl)}">${fmtMoney(t.pnl)}</td><td class="${cls(t.ret)}">${fmtPct((t.ret||0)*100)}</td>
    <td>${fmtMoney(t.fees)}</td><td>${t.days??''}</td></tr>`).join('');
  $('tradeCount').textContent=`${rows.length} 笔`;
  $('tradePage').textContent=`${state.page+1}/${pages}`;
}
document.querySelectorAll('th[data-sort]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.sort;state.tradeSort.dir=state.tradeSort.key===k?-state.tradeSort.dir:-1;
  state.tradeSort.key=k;state.page=0;renderTrades();
});
$('tradeFilter').oninput=e=>{state.tradeFilter=e.target.value;state.page=0;renderTrades();};
$('tradePrev').onclick=()=>{state.page=Math.max(0,state.page-1);renderTrades();};
$('tradeNext').onclick=()=>{state.page+=1;renderTrades();};

document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{
  state.range=b.dataset.range;document.querySelectorAll('[data-range]').forEach(x=>x.classList.toggle('active',x===b));drawEquity();
});
$('benchToggle').onchange=e=>{state.showBench=e.target.checked;drawEquity();};
document.querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>{
  state.window=Number(b.dataset.window);document.querySelectorAll('[data-window]').forEach(x=>x.classList.toggle('active',x===b));drawRolling();
});
function download(name,text,type='application/json'){
  const blob=new Blob([text],{type});const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download=name;a.click();URL.revokeObjectURL(a.href);
}
$('equityCsv').onclick=()=>{
  let csv='date,equity,drawdown,ret\n';D.equity.forEach(x=>csv+=`${x.date},${x.equity},${x.drawdown},${x.ret}\n`);download('equity.csv',csv,'text/csv');
};
$('tradeCsv').onclick=()=>{
  const rows=D.trades||[];let csv='symbol,entry_date,exit_date,entry_price,exit_price,shares,pnl,ret,days,fees,reason\n';
  rows.forEach(t=>csv+=`${t.symbol},${t.entry_date},${t.exit_date},${t.entry_price},${t.exit_price},${t.shares},${t.pnl},${t.ret},${t.days},${t.fees},"${(t.reason||'').replace(/"/g,'""')}"\n`);
  download('trades.csv',csv,'text/csv');
};
$('jsonCsv').onclick=()=>download('report_data.json',JSON.stringify(D,null,2));

drawEquity();drawYearly();drawHist();
const initialTab=location.hash.slice(1);
if(initialTab && document.getElementById('tab-'+initialTab)) tab(initialTab);
"""


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else "—"))


def _num(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _plain(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _kpis(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or {}
    alpha = data.get("alpha_beta") or {}
    summary = data.get("trade_summary") or {}
    cards = [
        ("累计收益", _num(metrics.get("total_return_pct"), 2, "%"), ""),
        ("年化", _num(metrics.get("annualized_return_pct"), 2, "%"), ""),
        ("最大回撤", _num(metrics.get("max_drawdown_pct"), 2, "%"), ""),
        ("Sharpe", _plain(metrics.get("sharpe"), 2), ""),
        ("Alpha(年化)", _num(alpha.get("alpha_annualized_pct"), 2, "%"), f"t={_plain(alpha.get('alpha_t'), 2)} (HAC {_plain(alpha.get('alpha_t_hac'), 2)})"),
        ("Beta", _plain(alpha.get("beta"), 2), f"R²={_plain(alpha.get('r2'), 2)}"),
        ("信息比率", _plain(alpha.get("information_ratio"), 2), f"TE={_num(alpha.get('tracking_error_pct'), 2, '%')}"),
        ("胜率", _plain(summary.get("win_rate_pct"), 1, "%"), f"{summary.get('count', 0)} 笔"),
    ]
    cards = [(label.replace("胜率", "回合胜率"), value, sub) for label, value, sub in cards]
    return "<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='v {cls_name(value)}'>{value}</div>"
        f"<div class='k'>{label}</div><div class='s'>{sub}</div></div>"
        for label, value, sub in cards
    ) + "</div>"


def cls_name(value: str) -> str:
    text = str(value)
    if text.startswith("+"):
        return "pos"
    if text.startswith("-"):
        return "neg"
    return ""


def _rows(rows: list[tuple[str, Any]]) -> str:
    return "".join(f"<tr><th>{_esc(key)}</th><td>{value}</td></tr>" for key, value in rows)


def _table(headers: list[str], rows: list[str], empty: str = "无数据") -> str:
    if not rows:
        return f"<p class='muted'>{empty}</p>"
    head = "".join(f"<th>{_esc(item)}</th>" for item in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _bridge_rows(data: dict[str, Any]) -> str:
    bridge = data.get("pnl_bridge") or {}
    if not bridge:
        return "<tr><td class='muted'>无足够数据</td></tr>"
    rows = [
        ("期初权益", _plain(bridge.get("start_equity"), 0)),
        ("已实现回合盈亏", _num(bridge.get("realized_pnl"), 2)),
        ("未实现浮盈", _num(bridge.get("unrealized_pnl"), 2)),
        ("未平仓费用（减项）", _num(-float(bridge.get("open_fees") or 0.0), 2)),
        ("残差", f"{_num(bridge.get('residual'), 2)}（{_num(bridge.get('residual_pct'), 3)}%）"),
        ("期末权益", _plain(bridge.get("end_equity"), 0)),
        ("总费用（含已平仓）", _plain(bridge.get("fees_total"), 2)),
        ("期末持仓", f"{_plain(bridge.get('open_positions'), 0)} 只 / {_plain(bridge.get('open_shares'), 0)} 股"),
    ]
    return _rows(rows)


def _checks_html(data: dict[str, Any]) -> str:
    checks = data.get("checks") or []
    if not checks:
        return "<p class='muted'>无自检结果</p>"
    labels = {"pass": "通过", "warn": "警告", "fail": "失败"}
    return "".join(
        f"<div class='check {_esc(item.get('status'))}'>"
        f"<span class='badge'>{_esc(labels.get(item.get('status'), item.get('status')))}</span>"
        f"<span>{_esc(item.get('name'))}<span class='detail'> · {_esc(item.get('detail'))}</span></span>"
        "</div>"
        for item in checks
    )


def _warnings_html(data: dict[str, Any]) -> str:
    return "".join(
        f"<div class='warnbanner'>{_esc(item.get('text'))}</div>"
        for item in (data.get("warnings") or [])
    )


def render_report(data: dict[str, Any]) -> str:
    meta = data.get("meta") or {}
    metrics = data.get("metrics") or {}
    alpha = data.get("alpha_beta") or {}
    summary = data.get("trade_summary") or {}
    title = f"回测报告 · {meta.get('strategy', meta.get('run_id', ''))}"
    subtitle = (
        f"{meta.get('run_id', '')} · {meta.get('engine', '—')} · {meta.get('freq', '—')} · "
        f"初始资金 {_plain(meta.get('initial_cash'), 0)}"
    )
    period = f"{meta.get('start', '')} → {meta.get('end', '')} · 基准 {meta.get('benchmark', '—')}"
    metric_rows = _rows(
        [
            ("累计收益", _num(metrics.get("total_return_pct"), 2, "%")),
            ("年化收益", _num(metrics.get("annualized_return_pct"), 2, "%")),
            ("最大回撤", _num(metrics.get("max_drawdown_pct"), 2, "%")),
            ("年化波动", _num(metrics.get("volatility_pct"), 2, "%")),
            ("Sharpe", _plain(metrics.get("sharpe"), 2)),
            ("期末权益", _plain(metrics.get("final_equity"), 0)),
            ("基准收益", _num(metrics.get("benchmark_return_pct"), 2, "%")),
            ("超额收益", _num(metrics.get("excess_return_pct"), 2, "%")),
            ("几何超额", _num(metrics.get("excess_geometric_pct"), 2, "%")),
        ]
    )
    alpha_rows = _rows(
        [
            ("Alpha（年化）", _num(alpha.get("alpha_annualized_pct"), 2, "%")),
            ("算术超额", _num(alpha.get("excess_arithmetic_pct"), 2, "%")),
            ("基准算术年化", _num(alpha.get("benchmark_mean_annual_pct"), 2, "%")),
            ("(1−β)×基准", _num(alpha.get("beta_component_pct"), 2, "%")),
            ("分解残差", _num(alpha.get("decomposition_residual_pct"), 2, "%")),
            ("Alpha t（OLS）", f"{_plain(alpha.get('alpha_t'), 2)} / p={_plain(alpha.get('alpha_p'), 3)}"),
            ("Alpha t（HAC）", f"{_plain(alpha.get('alpha_t_hac'), 2)} / p={_plain(alpha.get('alpha_p_hac'), 3)}"),
            ("月度 t / p", f"{_plain((data.get('monthly_summary') or {}).get('t_stat'), 2)} / p={_plain((data.get('monthly_summary') or {}).get('p_value'), 3)}"),
            ("Beta", _plain(alpha.get("beta"), 2)),
            ("仓位中性 β/α", f"{_plain(alpha.get('beta_neutral'), 2)} / {_num(alpha.get('alpha_neutral_pct'), 2, '%')}"),
            ("平均仓位", _plain(alpha.get("avg_gross_pct"), 1, "%")),
            ("R²", _plain(alpha.get("r2"), 2)),
            ("跟踪误差", _num(alpha.get("tracking_error_pct"), 2, "%")),
            ("信息比率", _plain(alpha.get("information_ratio"), 2)),
            ("上行捕获", _plain(alpha.get("up_capture"), 2)),
            ("下行捕获", _plain(alpha.get("down_capture"), 2)),
            ("牛/熊 Beta", f"{_plain(alpha.get('bull_beta'), 2)} / {_plain(alpha.get('bear_beta'), 2)}"),
        ]
    )
    risk = data.get("risk") or {}
    risk_rows = _rows(
        [
            ("年化收益（几何）", _num(risk.get("annualized_return_pct"), 2, "%")),
            ("年化收益（算术）", _num(risk.get("annualized_return_arithmetic_pct"), 2, "%")),
            ("区间收益", _num(risk.get("period_return_pct"), 2, "%")),
            ("12M 收益", _num(risk.get("return_12m_pct"), 2, "%")),
            ("36M 收益", _num(risk.get("return_36m_pct"), 2, "%")),
            ("年化波动", _num(risk.get("volatility_pct"), 2, "%")),
            ("下行偏差", _num(risk.get("downside_vol_pct"), 2, "%")),
            ("最大回撤", _num(risk.get("max_drawdown_pct"), 2, "%")),
            ("Sharpe（算术）", _plain(risk.get("sharpe_arithmetic"), 2)),
            ("Sortino（算术）", _plain(risk.get("sortino"), 2)),
            ("Calmar（几何）", _plain(risk.get("calmar"), 2)),
            ("平均仓位", _plain(risk.get("avg_gross_pct"), 1, "%")),
            ("现金拖累（年化）", _num(risk.get("cash_drag_pct"), 2, "%")),
            ("VaR95（日）", _num(risk.get("var95_pct"), 2, "%")),
            ("CVaR95（日）", _num(risk.get("cvar95_pct"), 2, "%")),
            ("偏度 / 峰度", f"{_plain(risk.get('skew'), 2)} / {_plain(risk.get('kurtosis'), 2)}"),
            ("月度正收益胜率", _plain(risk.get("monthly_win_rate_pct"), 1, "%")),
            ("样本数", _plain(risk.get("observations"), 0)),
        ]
    )
    risk_note = f"<p class='note'>{_esc(risk.get('annualization_note'))}</p>" if risk.get("annualization_note") else ""
    alpha_r2 = alpha.get("r2")
    alpha_warn = ""
    if alpha_r2 is not None and float(alpha_r2) < 0.5:
        alpha_warn = (
            f"<p class='warnbox'>R²={_plain(alpha_r2, 2)}：基准只解释 "
            f"{_plain(float(alpha_r2) * 100.0, 1)}% 的组合波动，Alpha 的解释力有限；"
            "建议换基准或重点看超额、捕获率与 IR。</p>"
        )
    dd_rows = "".join(
        f"<tr><td>{_esc(item.get('start'))}</td><td>{_esc(item.get('trough'))}</td>"
        f"<td>{_esc(item.get('end') or '未修复')}</td><td class='neg'>{_num(item.get('depth_pct'), 2, '%')}</td>"
        f"<td>{_plain(item.get('days'), 0)}</td></tr>"
        for item in (data.get("drawdowns") or [])
    )
    trade_kpis = "".join(
        f"<div class='kpi'><div class='v'>{value}</div><div class='k'>{label}</div></div>"
        for label, value in [
            ("回合数", _plain(summary.get("count"), 0)),
            ("回合胜率", _plain(summary.get("win_rate_pct"), 1, "%")),
            ("Profit Factor", _plain(summary.get("profit_factor"), 2)),
            ("平均盈亏比", _plain(summary.get("payoff"), 2)),
            ("平均盈利", _num(summary.get("avg_win_pct"), 2, "%")),
            ("平均亏损", _num(summary.get("avg_loss_pct"), 2, "%")),
            ("期望", _num(summary.get("expectancy_pct"), 2, "%")),
            ("平均持仓", _plain(summary.get("avg_days"), 1, "天")),
            ("总费用", _plain(summary.get("fees_total"), 0)),
            ("年化单边换手", _plain(summary.get("turnover_one_way_pct"), 1, "%")),
        ]
    )
    attr_rows = "".join(
        f"<tr><td>{_esc(item.get('symbol'))}</td><td class='{cls_name(_num(item.get('pnl'), 2))}'>"
        f"{_num(item.get('pnl'), 2)}</td><td>{_plain(item.get('trades'), 0)}</td></tr>"
        for item in ((data.get("attribution") or {}).get("by_symbol") or [])
    )
    attr_bottom_rows = "".join(
        f"<tr><td>{_esc(item.get('symbol'))}</td><td class='{cls_name(_num(item.get('pnl'), 2))}'>"
        f"{_num(item.get('pnl'), 2)}</td><td>{_plain(item.get('trades'), 0)}</td></tr>"
        for item in ((data.get("attribution") or {}).get("bottom") or [])
    )
    attr_total = _num((data.get("attribution") or {}).get("total_pnl"), 2)
    attr_top = _num((data.get("attribution") or {}).get("top_total"), 2)
    attr_share = _plain((data.get("attribution") or {}).get("top_share_pct"), 1)
    sel_rows = "".join(
        f"<tr><td>{_esc(item.get('date'))}</td><td>{_esc(item.get('symbol'))}</td>"
        f"<td>{_plain(item.get('score'), 3)}</td>"
        f"<td>{_plain(item.get('rank'), 0)}</td><td>{_plain(item.get('weight'), 4)}</td></tr>"
        for item in (data.get("selections") or [])
    )
    sel_count = len(data.get("selections") or [])
    sel_fwd = data.get("selection_forward") or {}
    sel_fwd_text = (
        f"前瞻收益：{_num(sel_fwd.get('mean_ret_pct'), 2)}%（中位 "
        f"{_num(sel_fwd.get('median_ret_pct'), 2)}%），胜率 "
        f"{_plain(sel_fwd.get('win_rate_pct'), 1)}%；覆盖 "
        f"{_plain(sel_fwd.get('observations'), 0)}/{_plain(sel_fwd.get('selected_total'), 0)}"
        f"（{_plain(sel_fwd.get('coverage_pct'), 1)}%），优势在尾部而非命中率。"
        if sel_fwd
        else "选股前瞻收益：持仓行情不足，暂未计算"
    )
    ind_rows = "".join(
        f"<tr><td>{_esc(item.get('industry'))}</td><td>{_plain(item.get('weight_pct'), 1, '%')}</td>"
        f"<td>{_plain(item.get('market_value'), 0)}</td></tr>"
        for item in (data.get("industry_exposure") or [])
    )
    ind_pnl_rows = "".join(
        f"<tr><td>{_esc(item.get('industry'))}</td>"
        f"<td class='{cls_name(_num(item.get('pnl'), 2))}'>{_num(item.get('pnl'), 2)}</td>"
        f"<td>{_plain(item.get('trades'), 0)}</td></tr>"
        for item in (data.get("industry_attribution") or [])
    )
    monthly_excess = data.get("monthly_excess") or []
    monthly_summary = data.get("monthly_summary") or {}
    monthly_excess_rows = "".join(
        f"<tr><td>{int(item.get('year'))}</td><td>{int(item.get('month')):02d}</td>"
        f"<td class='{cls_name(_num(item.get('ret'), 2))}'>{_num(item.get('ret'), 2)}</td>"
        f"<td class='{cls_name(_num(item.get('bench_ret'), 2))}'>{_num(item.get('bench_ret'), 2)}</td>"
        f"<td class='{cls_name(_num(item.get('excess'), 2))}'>{_num(item.get('excess'), 2)}</td></tr>"
        for item in monthly_excess
    )
    monthly_text = (
        f"均值 {_num(monthly_summary.get('mean_pct'), 2)}% · 月度超额胜率 "
        f"{_plain(monthly_summary.get('win_rate_pct'), 1)}% · 月度 IR "
        f"{_plain(monthly_summary.get('ir'), 2)} · {_plain(monthly_summary.get('months'), 0)} 个月"
        if monthly_summary
        else "无基准月度数据"
    )
    cost_rows = "".join(
        f"<tr><td>{_plain(item.get('bps_per_side'), 0)}</td>"
        f"<td>-{_plain(item.get('annual_drag_pct'), 2)}%</td></tr>"
        for item in (data.get("cost_sensitivity") or [])
    )
    oos = data.get("oos") or {}
    oos_rows = ""
    for label, key in (("样本内", "in_sample"), ("样本外", "out_of_sample")):
        part = oos.get(key) or {}
        if part:
            oos_rows += (
                f"<tr><td>{label}</td><td>{_plain(part.get('observations'), 0)}</td>"
                f"<td>{_num(part.get('total_return_pct'), 2, '%')}</td>"
                f"<td>{_num(part.get('annualized_return_pct'), 2, '%')}</td>"
                f"<td>{_num(part.get('volatility_pct'), 2, '%')}</td>"
                f"<td>{_plain(part.get('sharpe'), 2)}</td>"
                f"<td>{_num(part.get('max_drawdown_pct'), 2, '%')}</td></tr>"
            )
    alt_rows = f"<tr><td>{_esc(meta.get('benchmark') or '000300')}</td>" \
        f"<td>{_num(alpha.get('alpha_annualized_pct'), 2, '%')}</td>" \
        f"<td>{_plain(alpha.get('beta'), 2)}</td>" \
        f"<td>{_plain(alpha.get('information_ratio'), 2)}</td>" \
        f"<td>{_plain(alpha.get('r2'), 2)}</td></tr>"
    for code, stats in (data.get("alternative_benchmarks") or {}).items():
        alt_rows += f"<tr><td>{_esc(code)}</td>" \
            f"<td>{_num(stats.get('alpha_annualized_pct'), 2, '%')}</td>" \
            f"<td>{_plain(stats.get('beta'), 2)}</td>" \
            f"<td>{_plain(stats.get('information_ratio'), 2)}</td>" \
            f"<td>{_plain(stats.get('r2'), 2)}</td></tr>"
    concentration = data.get("concentration") or []
    last_conc = concentration[-1] if concentration else {}
    conc_rows = _rows(
        [
            ("Top10 占权益", _plain(last_conc.get("top10_pct"), 1, "%")),
            ("Top10 占持仓", _plain(last_conc.get("top10_pct_gross"), 1, "%")),
            ("HHI", _plain(last_conc.get("hhi"), 3)),
            ("最大单票（占权益）", _plain(last_conc.get("max_position_pct"), 1, "%")),
            ("最大单票（占持仓）", _plain(last_conc.get("max_position_pct_gross"), 1, "%")),
            ("持仓只数", _plain(last_conc.get("positions"), 0)),
            ("总仓位", _plain(last_conc.get("gross_pct"), 1, "%")),
        ]
    )
    valuation = data.get("valuation") or {}
    val_rows = _rows(
        [
            ("日期", _esc(valuation.get("date"))),
            ("持仓市值", _plain(valuation.get("positions_mv"), 0)),
            ("权益市值", _plain(valuation.get("equity_mv"), 0)),
            ("差异", f"{_plain(valuation.get('gap'), 0)}（{_plain(valuation.get('gap_pct'), 2)}%）"),
        ]
    )
    board_rows = "".join(
        f"<tr><td>{_esc(item.get('board'))}</td><td>{_plain(item.get('count'), 0)}</td>"
        f"<td>{_plain(item.get('weight_pct'), 1, '%')}</td></tr>"
        for item in (data.get("board_exposure") or [])
    )
    bse_months = data.get("bse_months") or []
    industry = data.get("industry_exposure") or []
    max_ind = max((float(item.get("weight_pct") or 0.0) for item in industry), default=0.0)
    ind_warn = (
        f"<p class='warnbox'>行业集中度：单一行业最高 {max_ind:.1f}% 权益，注意结构性暴露。</p>"
        if max_ind > 30
        else ""
    )
    bse_note = (
        "920 号段为数据源的当前统一编码；北交所 30% 涨跌停，冲击成本未建模。出现月份："
        + "、".join(f"{item['month']}({item['count']})" for item in bse_months[:8])
        if bse_months
        else "最新选股无北交所标的。"
    )
    regime = data.get("regime") or {}
    regime_rows = _rows(
        [
            ("2024 收益", _num(regime.get("return_2024_pct"), 2, "%")),
            ("2024 对数收益占比", _num(regime.get("log_share_2024_pct"), 1, "%")),
            ("剔除 2024 后累计", _num(regime.get("ex_2024_return_pct"), 2, "%")),
            ("2023-11 超额", _num(regime.get("nov_2023_excess_pct"), 2, "%")),
            ("2023-11 超额占比", _num(regime.get("nov_2023_excess_share_pct"), 1, "%")),
            ("60D β 区间", f"{_plain(regime.get('rolling_beta_min'), 2)} ~ {_plain(regime.get('rolling_beta_max'), 2)}"),
            ("60D α 区间", f"{_num(regime.get('rolling_alpha_min_pct'), 1, '%')} ~ {_num(regime.get('rolling_alpha_max_pct'), 1, '%')}"),
            ("|日收益|>7% 天数", _plain(regime.get("days_abs_gt_7pct"), 0)),
        ]
    )
    meta_rows = _rows(
        [
            ("run_id", _esc(meta.get("run_id"))),
            ("策略", _esc(meta.get("strategy"))),
            ("模式", _esc(meta.get("mode"))),
            ("频率", _esc(meta.get("freq"))),
            ("引擎", _esc(meta.get("engine"))),
            ("bar 频率", _esc(meta.get("bar_frequency"))),
            ("基准", _esc(meta.get("benchmark"))),
            ("基准可用", _esc(meta.get("benchmark_used"))),
            ("手续费/成交", _esc(meta.get("fill"))),
            ("目标仓位上限", _plain(meta.get("max_gross_exposure"), 2)),
            ("股票池", f"{_esc(meta.get('universe'))} / {_plain(meta.get('universe_size'), 0)} 只"),
            ("每日有效覆盖", _plain(meta.get("eligible_count"), 0)),
            ("交易候选并集", _plain(meta.get("candidate_count"), 0)),
            ("调仓", _esc(meta.get("rebalance"))),
            ("权重/TopN", f"{_esc(meta.get('weighting'))} / {_plain(meta.get('top_n'), 0)}"),
            ("因子", _esc(", ".join(meta.get("factors") or []) or "—")),
            ("参数", _esc(meta.get("params"))),
            ("公司行为", _esc(meta.get("corporate_actions"))),
            ("成本模型", _esc(meta.get("cost_model_text") or meta.get("cost_model"))),
            ("代码 commit", _esc(meta.get("code_commit"))),
            ("数据源", _esc(meta.get("data_source"))),
            ("score 定义", _esc(meta.get("score_note"))),
            ("订单/成交", f"{_plain(meta.get('orders'), 0)} / {_plain(meta.get('trades'), 0)}"),
            ("无风险利率", _plain(meta.get("risk_free_rate"), 3)),
        ]
    )
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = (
        _HEAD.replace("__CSS__", _CSS)
        .replace("__TITLE__", _esc(title))
        .replace("__SUBTITLE__", _esc(subtitle))
        .replace("__PERIOD__", _esc(period))
        .replace("__KPIS__", _kpis(data))
        .replace("__WARNINGS__", _warnings_html(data))
        .replace("__METRIC_ROWS__", metric_rows)
        .replace("__ALPHA_ROWS__", alpha_rows)
        .replace("__ALPHA_WARN__", alpha_warn)
        .replace("__BRIDGE_ROWS__", _bridge_rows(data))
        .replace("__CHECKS__", _checks_html(data))
        .replace("__CONC_ROWS__", conc_rows)
        .replace("__VAL_ROWS__", val_rows)
        .replace("__BOARD_ROWS__", board_rows or "<tr><td colspan='3' class='muted'>无选股数据</td></tr>")
        .replace("__BSE_NOTE__", _esc(bse_note))
        .replace("__REGIME_ROWS__", regime_rows)
        .replace("__RISK_ROWS__", risk_rows)
        .replace("__RISK_NOTE__", risk_note)
        .replace("__DD_ROWS__", dd_rows or "<tr><td colspan='5' class='muted'>无回撤</td></tr>")
        .replace("__TRADE_KPIS__", trade_kpis)
        .replace("__ATTR_ROWS__", attr_rows)
        .replace("__ATTR_BOTTOM_ROWS__", attr_bottom_rows)
        .replace("__ATTR_TOTAL__", attr_total)
        .replace("__ATTR_TOP__", attr_top)
        .replace("__ATTR_SHARE__", attr_share)
        .replace("__ALT_ROWS__", alt_rows)
        .replace("__SEL_ROWS__", sel_rows or "<tr><td colspan='5' class='muted'>无选股快照</td></tr>")
        .replace("__SEL_COUNT__", str(sel_count))
        .replace("__SEL_FWD__", sel_fwd_text)
        .replace("__IND_ROWS__", ind_rows or "<tr><td colspan='3' class='muted'>行业映射未接入</td></tr>")
        .replace("__IND_PNL_ROWS__", ind_pnl_rows or "<tr><td colspan='3' class='muted'>无行业映射</td></tr>")
        .replace("__IND_WARN__", ind_warn)
        .replace("__MONTHLY_EXCESS_ROWS__", monthly_excess_rows or "<tr><td colspan='5' class='muted'>无基准月度数据</td></tr>")
        .replace("__MONTHLY_SUMMARY__", monthly_text)
        .replace("__COST_ROWS__", cost_rows or "<tr><td colspan='2' class='muted'>无换手数据</td></tr>")
        .replace("__TURNOVER__", _plain(summary.get("turnover_one_way_pct"), 1, "%"))
        .replace("__OOS_ROWS__", oos_rows or "<tr><td colspan='7' class='muted'>样本不足</td></tr>")
        .replace("__META_ROWS__", meta_rows)
        .replace("__BENCH__", _esc(meta.get("benchmark") or "—"))
        .replace("__GENERATED__", _esc(meta.get("generated_at")))
        .replace("__RUN_ID__", _esc(meta.get("run_id")))
        .replace("__DATA__", payload)
        .replace("__JS__", _JS)
    )
    return html
