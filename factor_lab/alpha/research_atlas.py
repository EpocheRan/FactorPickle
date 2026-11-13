"""HTML renderer for the all-research Research Atlas.

The atlas is a single self-contained HTML file: no external assets, no network
access, no LLM judgement added.  Layout is an app shell with hash-routed views
so that data volume (73 campaigns / 530 ideas / 125 candidates) stays
navigable: a dense sortable campaign rail with a detail pane, filterable flat
tables, a global search palette, deep links and a print ("long document") mode.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from factor_lab.alpha.research_portfolio import ROLES, build_portfolio
from factor_lab.core import config


CSS = r"""
:root{
--bg:#f2f5f9;--panel:#fff;--ink:#0e1622;--muted:#5b6779;--faint:#8792a4;
--line:#e3e8ef;--line2:#eef1f6;--acc:#2563eb;--acc-soft:#ecf2ff;--acc-ink:#1746c8;
--ok:#0a7a54;--ok-soft:#e7f7f0;--bad:#b3262e;--bad-soft:#fdeceb;--warn:#8a5b00;--warn-soft:#fff4dc;
--vio:#5b3ea8;--vio-soft:#f2ecff;--gray-soft:#f3f5f8;
--top:#0a1220;--top-ink:#e9eef7;--mono:ui-monospace,SFMono-Regular,Consolas,"Cascadia Mono",monospace;
--sans:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif;
--topbar:52px;--rail:376px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.55}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
[hidden]{display:none!important}
button{font:inherit;color:inherit}
code{font-family:var(--mono);font-size:12px}
h1,h2,h3,h4{letter-spacing:-.01em}
.topbar{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:12px;height:var(--topbar);padding:0 12px;background:var(--top);color:var(--top-ink);box-shadow:0 1px 0 rgba(255,255,255,.06),0 8px 22px rgba(9,17,30,.18)}
.brand{display:flex;align-items:baseline;gap:8px;white-space:nowrap}
.brand b{font-size:14.5px;letter-spacing:.01em}
.brand span{font-size:10.8px;color:#8fa0b8}
.tabs{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}
.tabs::-webkit-scrollbar{display:none}
.tab{display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border-radius:8px;color:#b4c0d4;font-size:12.4px;white-space:nowrap}
.tab:hover{background:#16233a;color:#eaf0fa;text-decoration:none}
.tab[aria-selected="true"]{background:#22344f;color:#fff;font-weight:600}
.tab .tc{font-size:10px;font-variant-numeric:tabular-nums;background:#2c3d59;color:#c3cfe2;border-radius:99px;padding:0 5px;line-height:15px}
.tab[aria-selected="true"] .tc{background:#3b567f;color:#eaf1ff}
.tools{margin-left:auto;display:flex;align-items:center;gap:6px}
.btn{border:1px solid #2b3b55;background:#142033;color:#d5dfef;border-radius:8px;padding:4px 9px;font-size:11.8px;cursor:pointer;white-space:nowrap}
.btn:hover{background:#1b2b44;border-color:#3b5278}
.btn.ghost{background:transparent;border-color:transparent;color:#9fb0c8}
.btn.ghost:hover{background:#16233a;color:#e6edf8}
.kbd{font:600 10px var(--mono);border:1px solid #33465f;border-bottom-width:2px;border-radius:4px;padding:0 4px;color:#9fb0c8}
main{padding-bottom:50px}
.view{max-width:1520px;margin:0 auto;padding:16px 18px 34px}
.pagehead{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;margin:0 0 12px}
.pagehead h1{margin:0;font-size:21px}
.pagehead .sub{margin:2px 0 0;color:var(--muted);font-size:12.6px;max-width:1100px}
.pagehead .spacer{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:10px 12px}
.kpi b{display:block;font-size:21px;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi span{font-size:11.2px;color:var(--muted)}
.kpi.bad b{color:var(--bad)}.kpi.ok b{color:var(--ok)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:11px 13px}
.card b{font-size:13px}
.card p{margin:4px 0 0;color:var(--muted);font-size:12.3px}
.grid{display:grid;gap:11px}
.grid>*{min-width:0}
.cd-cols>*{min-width:0}
.explorer>*{min-width:0}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.g5{grid-template-columns:repeat(5,minmax(0,1fr))}
td.nw,th.nw{white-space:nowrap}
.c-col{min-width:170px}
.tag{display:inline-block;font-size:10.4px;font-weight:700;padding:1px 6px;border-radius:5px;white-space:nowrap;border:1px solid transparent;line-height:1.55}
.tag.ok{background:var(--ok-soft);color:var(--ok)}
.tag.bad{background:var(--bad-soft);color:var(--bad)}
.tag.warn{background:var(--warn-soft);color:var(--warn)}
.tag.info{background:var(--acc-soft);color:var(--acc-ink)}
.tag.vio{background:var(--vio-soft);color:var(--vio)}
.tag.gray{background:var(--gray-soft);color:#546074}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;vertical-align:1px;background:var(--faint)}
.dot.bad{background:var(--bad)}.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.callout{border-left:3px solid var(--acc);background:var(--acc-soft);border-radius:0 9px 9px 0;padding:8px 11px;margin:9px 0;font-size:12.6px;color:#1d2b45}
.callout.ok{border-color:var(--ok);background:var(--ok-soft)}
.callout.warn{border-color:var(--warn);background:var(--warn-soft);color:#3d2f10}
.callout.bad{border-color:var(--bad);background:var(--bad-soft);color:#4a1619}
.callout b{font-weight:700}
.tablewrap{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:auto;max-height:min(70vh,860px)}
.tablewrap.tall{max-height:min(78vh,1100px)}
.tablewrap.flat{max-height:none}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.4px}
thead th{position:sticky;top:0;z-index:2;background:#f3f6fa;color:#42506a;font-size:10.6px;font-weight:700;text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap;letter-spacing:.02em}
th[data-sort]{cursor:pointer;user-select:none}
th[data-sort]:hover{background:#e9eef7}
th .sa{display:inline-block;width:8px;margin-left:3px;color:#a6b1c3;font-size:8.5px}
th[data-dir] .sa{color:var(--acc)}
tbody td{padding:6px 9px;border-bottom:1px solid var(--line2);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#f8fbff}
tbody tr.flash{animation:flash 1.6s ease-out}
@keyframes flash{0%{background:#fff2bd}100%{background:transparent}}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:var(--mono);font-size:11.4px}
.mini{font-size:11.2px;color:var(--muted)}
.na{color:#b6bfcd}
code.expr{display:inline-block;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom;background:#f1f4f9;border:1px solid #e6ebf3;border-radius:5px;padding:0 5px;font-family:var(--mono);font-size:11px;color:#36445c}
.chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.chip{border:1px solid var(--line);background:#fff;border-radius:99px;padding:2px 9px;font-size:11.2px;color:#44506a;cursor:pointer;line-height:1.75}
.chip:hover{background:#f2f6fd}
.chip[aria-pressed="true"]{background:#132136;border-color:#132136;color:#fff}
.chip b{font-weight:700;font-variant-numeric:tabular-nums;opacity:.75;font-size:10px;margin-left:3px}
input.search,select.select{border:1px solid var(--line);border-radius:8px;background:#fff;padding:5px 8px;font:inherit;font-size:12.2px;min-width:0}
input.search:focus,select.select:focus{outline:2px solid #cfe0ff;border-color:#9dbcf7}
.filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 10px}
.filters .search{min-width:240px;flex:0 1 300px}
.count{font-size:11.4px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.chk{display:inline-flex;align-items:center;gap:4px;font-size:11.2px;color:#44506a;cursor:pointer;white-space:nowrap}
.chk input{width:13px;height:13px;margin:0}
a.card{display:block;color:inherit}
a.card:hover{border-color:#bccbe6;background:#f8fbff;text-decoration:none}
/* explorer */
.explorer{display:grid;grid-template-columns:var(--rail) minmax(0,1fr);gap:12px;align-items:stretch}
@media(min-width:1000px){.explorer{height:calc(100vh - var(--topbar) - 108px);min-height:520px}}
.rail{display:flex;flex-direction:column;min-height:0;background:var(--panel);border:1px solid var(--line);border-radius:11px;overflow:hidden}
.rail-head{padding:8px 9px;border-bottom:1px solid var(--line);background:#fbfcfe;display:grid;gap:5px}
.rail-tools{display:flex;gap:5px}
.rail-tools .search{flex:1}
.rail-tools .select{max-width:120px}
.rail-count{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11.2px;color:var(--muted)}
.rail-list{flex:1;min-height:0;overflow:auto;padding:4px}
.camp-row{display:grid;grid-template-columns:32px minmax(0,1fr) 58px;gap:7px;align-items:center;width:100%;text-align:left;background:none;border:0;border-left:3px solid transparent;border-radius:8px;padding:5px 7px;cursor:pointer;color:inherit}
.camp-row:hover{background:#f4f7fc;text-decoration:none}
.camp-row[aria-current="true"]{background:var(--acc-soft);border-left-color:var(--acc)}
.camp-row .ph{font:700 9.5px var(--mono);color:#fff;background:#3a4b6a;border-radius:5px;padding:2px 0;text-align:center;letter-spacing:.02em}
.camp-row[data-kind="calibration"] .ph{background:#6b7280}
.camp-row[data-kind="ml"] .ph{background:#5b3ea8}
.camp-row[data-kind="discovery"] .ph{background:#0e7490}
.camp-row .lbl{display:block;font-size:12.4px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.camp-row .mt{display:block;font-size:10.6px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.camp-row .st{text-align:right;font-size:10.2px;color:var(--muted);font-variant-numeric:tabular-nums}
.camp-row .st b{display:block;font-size:12.2px;color:var(--ink);font-variant-numeric:tabular-nums}
.camp-row .st b.pos{color:var(--ok)}.camp-row .st b.neg{color:var(--bad)}
.rail-empty{padding:18px 10px;text-align:center;color:var(--muted);font-size:12.3px}
.detail{min-height:0;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:11px}
.camp-detail{padding:14px 16px 22px}
.cd-top{display:flex;align-items:flex-start;gap:9px}
.cd-top h2{font-size:18px;margin:0}
.cd-actions{margin-left:auto;display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.cd-actions .btn{background:#fff;border-color:var(--line);color:#37445c}
.cd-actions .btn:hover{background:#f2f6fd}
#ev-back{display:none;margin:0}
.ev-back-wrap{display:none;padding:10px 12px 0}
.phase{font:800 10.5px var(--mono);color:#fff;background:#33415c;border-radius:6px;padding:3px 6px;white-space:nowrap;margin-top:3px}
.cd-badges{display:flex;flex-wrap:wrap;gap:4px;margin:7px 0 0}
.cd-id{font-size:11.4px;color:var(--muted);margin:5px 0 0}
.cd-kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;margin:12px 0}
.cd-kpi{background:#f7f9fc;border:1px solid var(--line);border-radius:9px;padding:6px 8px}
.cd-kpi b{display:block;font-size:15px;font-variant-numeric:tabular-nums;line-height:1.2}
.cd-kpi span{font-size:10.2px;color:var(--muted)}
.cd-obj{margin:9px 0 0;font-size:12.7px;color:#2c3850;background:#f8fafd;border:1px solid var(--line2);border-radius:9px;padding:8px 10px}
.cd-concl{margin:8px 0 0;font-size:12.7px;border-left:3px solid var(--acc);background:var(--acc-soft);border-radius:0 9px 9px 0;padding:8px 11px}
.cd-concl b{display:inline}
.cd-sec{margin:16px 0 0}
.cd-sec>h3{font-size:13px;margin:0 0 7px;display:flex;align-items:center;gap:6px}
.cd-sec>h3 .n{font-size:10.4px;color:var(--muted);background:var(--gray-soft);border-radius:99px;padding:0 6px;font-weight:600}
.cd-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0 0}
.cd-cols .cd-sec{margin:0}
.cd-foot{margin:14px 0 0;display:flex;flex-wrap:wrap;gap:5px}
details.fold{margin:8px 0 0}
details.fold>summary{cursor:pointer;font-size:11.6px;color:var(--muted);padding:2px 0}
details.fold[open]>summary{color:var(--acc)}
details.fold .fold-body{margin:5px 0 0;font-size:11.5px;color:#47536b;word-break:break-all}
details.fold .fold-body a{display:inline-block;margin:0 6px 3px 0}
/* ideas */
.idea-row{border:1px solid var(--line);border-radius:9px;background:#fff;padding:8px 10px;margin:0 0 7px}
.idea-row .top{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:10.8px;color:var(--muted)}
.idea-row .tt{font-size:12.7px;font-weight:600;margin-top:3px;color:#16213a;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.idea-row .dd{font-size:12px;color:#47536b;margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.idea-row.open .tt,.idea-row.open .dd{-webkit-line-clamp:unset;display:block}
.idea-row .acts{display:flex;gap:10px;align-items:center;margin-top:4px;font-size:11px}
.idea-row.compact{display:grid;grid-template-columns:192px 200px minmax(0,1fr) auto;gap:10px;align-items:center;padding:4px 9px;margin:0 0 3px;border-radius:7px}
.idea-row.compact .i-tags{display:flex;gap:3px;align-items:center;min-width:0;flex-wrap:wrap;row-gap:2px}
.idea-row.compact .i-tags .tag{max-width:100%;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}
.idea-row.compact .i-camp{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.idea-row.compact .tt{font-size:12.2px;font-weight:500;margin:0;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.idea-row.compact .dd{display:none;grid-column:1/-1;margin-top:4px;padding:6px 0 2px;border-top:1px dashed var(--line)}
.idea-row.compact.open .dd{display:block}
.idea-row.compact .dd .i-src{display:block;margin-top:4px}
.ideas-head{position:sticky;top:var(--topbar);z-index:20;background:var(--bg);padding:5px 0 4px;display:grid;grid-template-columns:192px 200px minmax(0,1fr) auto;gap:10px;font-size:10.4px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#8a95a8;border-bottom:1px solid var(--line)}
.cell-tasks{display:block;max-width:130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.linkbtn{border:0;background:none;color:var(--acc);cursor:pointer;padding:0;font-size:11.2px}
.linkbtn:hover{text-decoration:underline}
.ideas-list{max-height:none}
/* matrix */
table.matrix td:first-child,table.matrix th:first-child{position:sticky;left:0;background:#fff;z-index:1;box-shadow:1px 0 0 var(--line)}
table.matrix thead th:first-child{z-index:3;background:#f3f6fa}
.cell-yes{background:#f3fbf7}
.matrix .rolecell{min-width:84px}
/* chart */
.chart{width:100%;height:auto;display:block}
.chart text{font-family:var(--sans);font-size:9.5px;fill:#6b7789}
.chart .grid{stroke:#e8edf4;stroke-width:1}
.chart .zero{stroke:#c3ccd9;stroke-width:1.2;stroke-dasharray:3 3}
.chart .runline{fill:none;stroke:#2563eb;stroke-width:1.4;stroke-dasharray:4 3;opacity:.75}
.chart .dot{stroke:#fff;stroke-width:1.2}
.chart .dot.ok{fill:#0a7a54}.chart .dot.bad{fill:#c2484f}.chart .dot.cal{fill:#9aa6b8}
.legend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:6px;font-size:11.4px;color:var(--muted)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:0}
/* palette */
.pal-back{position:fixed;inset:0;background:rgba(9,16,28,.42);z-index:100;display:flex;align-items:flex-start;justify-content:center;padding:9vh 14px}
.palette{width:min(760px,100%);background:#fff;border-radius:13px;box-shadow:0 24px 60px rgba(9,17,30,.35);overflow:hidden}
.palette .inrow{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);padding:0 14px}
.palette input{flex:1;border:0;padding:13px 0;font:600 14.5px var(--sans);outline:none;background:none}
.palette .res{max-height:min(62vh,560px);overflow:auto;padding:6px}
.pal-empty{padding:18px;color:var(--muted);font-size:12.5px;text-align:center}
.pal-group{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#8a95a8;padding:7px 10px 3px}
.pal-item{display:flex;gap:9px;align-items:center;padding:7px 10px;border-radius:8px;cursor:pointer}
.pal-item[aria-selected="true"]{background:var(--acc-soft)}
.pal-item .g{font-size:9.5px;font-weight:700;color:#fff;background:#5a6b85;border-radius:4px;padding:1px 5px;white-space:nowrap}
.pal-item .lb{font-size:12.8px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pal-item .sb{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pal-foot{border-top:1px solid var(--line);padding:7px 14px;font-size:11px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
.pal-foot .kbd{color:#6b7789;border-color:#d3dae5}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#132136;color:#fff;border-radius:9px;padding:8px 16px;font-size:12.5px;z-index:120;box-shadow:0 10px 30px rgba(9,17,30,.3)}
/* density */
body.dense tbody td{padding:4px 8px}
body.dense .camp-row{padding:3px 6px}
body.dense .idea-row{padding:6px 9px;margin-bottom:5px}
body.dense .cd-sec{margin-top:12px}
/* long document */
body.longdoc .view[hidden]{display:block!important}
body.longdoc .explorer{display:block}
body.longdoc .rail{display:none}
body.longdoc .detail{overflow:visible;border:0;background:none}
body.longdoc .camp-detail[hidden]{display:block!important}
body.longdoc .camp-detail{border:1px solid var(--line);border-radius:11px;background:var(--panel);margin:0 0 12px}
body.longdoc .tablewrap{max-height:none}
body.longdoc thead th{position:static}
/* responsive */
@media(max-width:1240px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.cd-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.g5{grid-template-columns:repeat(3,minmax(0,1fr))}.idea-row.compact{grid-template-columns:150px 170px minmax(0,1fr) auto}.ideas-head{grid-template-columns:150px 170px minmax(0,1fr) auto}}
@media(max-width:999px){
 .view{padding:12px 12px 28px}
 .explorer{grid-template-columns:1fr}
 .rail{max-height:64vh}
 .detail{display:none}
 .explorer.detail-open .rail{display:none}
 .explorer.detail-open .detail{display:block;border:0;background:none}
 .cd-cols{grid-template-columns:1fr}
 .g2,.g3,.g4{grid-template-columns:1fr}
 .g5{grid-template-columns:repeat(2,minmax(0,1fr))}
 .brand span{display:none}
 .tools .btn.ghost{display:none}
 .cd-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}
 .ev-back-wrap{display:block}
 #ev-back{display:inline-flex}
 .idea-row.compact{grid-template-columns:150px minmax(0,1fr) auto}
 .idea-row.compact .i-camp{display:none}
 .ideas-head{display:none}
}
@media(max-width:640px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.g5{grid-template-columns:1fr}.brand b{font-size:13px}.tab{padding:5px 7px}.cd-actions{display:none}}
@media print{
 @page{margin:11mm}
 body{background:#fff;font-size:10pt}
 .topbar,.pal-back,#toast,.cd-actions,.chips,.filters,.rail,.cd-foot{display:none!important}
 main{max-width:none}
 .view{display:block!important;max-width:none;padding:0}
 .view[hidden]{display:block!important}
 .explorer{display:block!important;height:auto!important}
 .detail{display:block!important;border:0;overflow:visible}
 .camp-detail[hidden]{display:block!important}
 .camp-detail{border:0;padding:0 0 10pt;page-break-after:always}
 .tablewrap{max-height:none;overflow:visible;border-color:#ccc}
 thead th{position:static}
 tbody td{padding:3px 6px}
 a{color:inherit;text-decoration:none}
 .tag{border-color:#ccc}
 .idea-row .tt,.idea-row .dd{-webkit-line-clamp:unset;display:block}
}
"""


JS = r"""
(function(){
'use strict';
var doc=document;
var $=function(s,r){return (r||doc).querySelector(s);};
var $$=function(s,r){return Array.prototype.slice.call((r||doc).querySelectorAll(s));};
var VIEWS={overview:1,evolution:1,ideas:1,agents:1,results:1,datasets:1,conclusions:1,method:1};
var state={view:'overview'};
var store={
 get:function(k,d){try{var v=window.localStorage.getItem('atlas.'+k);return v===null?d:v;}catch(e){return d;}},
 set:function(k,v){try{window.localStorage.setItem('atlas.'+k,v);}catch(e){}}
};
var toastTimer=null;
function toast(msg){
 var el=$('#toast'); if(!el) return;
 el.textContent=msg; el.hidden=false;
 window.clearTimeout(toastTimer);
 toastTimer=window.setTimeout(function(){el.hidden=true;},1800);
}
function lowerText(el){
 var cached=el.__lc;
 if(cached===undefined){
   cached=(el.textContent||'').replace(/\s+/g,' ').toLowerCase();
   el.__lc=cached;
 }
 return cached;
}

/* ---------------- routing ---------------- */
function parseHash(){
 var h=(location.hash||'').replace(/^#\/?/,'');
 var qi=h.indexOf('?'); if(qi>=0) h=h.slice(0,qi);
 var parts=h.split('/');
 var v=parts[0]||'overview';
 if(v==='snapshot') v='overview';
 if(!VIEWS[v]) v='overview';
 var cid=null;
 if(parts[1]){ try{cid=decodeURIComponent(parts[1]);}catch(e){cid=parts[1];} }
 return {view:v,cid:cid};
}
function go(view,cid,replace){
 var h='#/'+view+(cid?'/'+encodeURIComponent(cid):'');
 if(location.hash===h){ applyRoute(); return; }
 if(replace){ try{history.replaceState(null,'',h);}catch(e){location.hash=h;} applyRoute(); }
 else { location.hash=h; }
}
var lastView=null;
function applyRoute(){
 var r=parseHash();
 var changed=r.view!==lastView;
 state.view=r.view;
 $$('.view').forEach(function(v){var on=v.getAttribute('data-view')===r.view;if(on){v.removeAttribute('hidden');}else{v.setAttribute('hidden','');}});
 $$('.tab').forEach(function(t){
   if(t.getAttribute('data-view')===r.view){t.setAttribute('aria-selected','true');}
   else{t.removeAttribute('aria-selected');}
 });
 if(r.view==='evolution'){
   var row=null;
   if(r.cid){ row=$('.camp-row[data-id="'+r.cid+'"]'); }
   if(!row){ row=$('.camp-row[aria-current="true"]') || visibleRows()[0] || null; }
   if(!r.cid){ var explorer=$('#ev-explorer'); if(explorer) explorer.classList.remove('detail-open'); }
   selectRow(row?row.getAttribute('data-id'):null,true,!!r.cid);
   if(!r.cid && row){
     try{ history.replaceState(null,'','#/evolution/'+encodeURIComponent(row.getAttribute('data-id'))); }
     catch(e){ /* ignore */ }
   }
 }
 if(changed){ window.scrollTo(0,0); }
 lastView=r.view;
}
window.addEventListener('hashchange',applyRoute);

/* ---------------- campaign rail ---------------- */
function visibleRows(){ return $$('.camp-row').filter(function(r){return !r.hidden;}); }
function selectRow(cid,scroll,openDetail){
 var found=null;
 $$('.camp-row').forEach(function(r){
   if(r.getAttribute('data-id')===cid){r.setAttribute('aria-current','true');found=r;}
   else{r.removeAttribute('aria-current');}
 });
 if(found && found.hidden){ resetFilters(); sortRail(); applyRail(); }
 $$('.camp-detail').forEach(function(c){
   if(c.getAttribute('data-id')===cid){c.removeAttribute('hidden');}else{c.setAttribute('hidden','');}
 });
 var miss=$('#ev-missing'); if(miss){ if(found||!cid){miss.setAttribute('hidden','');} else {miss.removeAttribute('hidden');} }
 if(found){
   if(scroll){ try{found.scrollIntoView({block:'nearest'});}catch(e){} }
   var det=$('#ev-detail'); if(det){ det.scrollTop=0; }
   if(window.matchMedia && window.matchMedia('(max-width:999px)').matches && openDetail !== false){
     var ex=$('#ev-explorer'); if(ex) ex.classList.add('detail-open');
   }
 }
}
function resetFilters(){
 var s=$('#ev-search'); if(s) s.value='';
 ['ev-team','ev-fdr','ev-holdout'].forEach(function(id){var c=document.getElementById(id);if(c)c.checked=false;});
 ['ev-run','ev-audit'].forEach(function(id){var c=document.getElementById(id);if(c)c.value='all';});
 $$('[data-f-kind]').forEach(function(b){b.setAttribute('aria-pressed',String(b.getAttribute('data-f-kind')==='all'));});
 var so=$('#ev-sort'); if(so) so.value='phase';
}
function sortRail(){
 var list=$('#ev-list'); if(!list) return;
 var rows=$$('.camp-row',list);
 var k=($('#ev-sort')&&$('#ev-sort').value)||'phase';
 var num=function(v,d){ var n=parseFloat(v); return isNaN(n)?d:n; };
 var phase=function(r){return num(r.getAttribute('data-phase'),0);};
 var cmp={
   phase:function(a,b){return phase(a)-phase(b);},
   date:function(a,b){var x=a.getAttribute('data-date')||'',y=b.getAttribute('data-date')||'';return x<y?-1:x>y?1:phase(a)-phase(b);},
   nett:function(a,b){var x=num(a.getAttribute('data-lot'),-Infinity),y=num(b.getAttribute('data-lot'),-Infinity);return y-x||phase(a)-phase(b);},
   gate:function(a,b){var x=num(a.getAttribute('data-gate'),0),y=num(b.getAttribute('data-gate'),0);return y-x||phase(a)-phase(b);},
   cands:function(a,b){var x=num(a.getAttribute('data-cands'),0),y=num(b.getAttribute('data-cands'),0);return y-x||phase(a)-phase(b);},
   rounds:function(a,b){var x=num(a.getAttribute('data-rounds'),0),y=num(b.getAttribute('data-rounds'),0);return y-x||phase(a)-phase(b);},
   audit:function(a,b){var x=a.getAttribute('data-audit')==='fail'?0:1,y=b.getAttribute('data-audit')==='fail'?0:1;return x-y||phase(a)-phase(b);}
 }[k] || null;
 if(cmp){ rows.sort(cmp); rows.forEach(function(r){list.appendChild(r);}); }
}
function railFilters(){
 var f={kind:'all',run:'all',audit:'all',team:false,fdr:false,holdout:false};
 $$('[data-f-kind]').forEach(function(b){ if(b.getAttribute('aria-pressed')==='true') f.kind=b.getAttribute('data-f-kind'); });
 var r=$('#ev-run'); if(r) f.run=r.value;
 var a=$('#ev-audit'); if(a) f.audit=a.value;
 var t=$('#ev-team'); if(t) f.team=t.checked;
 var d=$('#ev-fdr'); if(d) f.fdr=d.checked;
 var h=$('#ev-holdout'); if(h) f.holdout=h.checked;
 return f;
}
function applyRail(){
 if(!$('#ev-list')) return;
 var q=(($('#ev-search')&&$('#ev-search').value)||'').toLowerCase().trim();
 var f=railFilters();
 var shown=0, total=0, selVisible=false;
 $$('.camp-row').forEach(function(r){
   total++;
   var d=r;
   var ok=(f.kind==='all'||d.getAttribute('data-kind')===f.kind)
     &&(f.run==='all'||d.getAttribute('data-run')===f.run)
     &&(f.audit==='all'||d.getAttribute('data-audit')===f.audit)
     &&(!f.team||d.getAttribute('data-team')==='1')
     &&(!f.fdr||d.getAttribute('data-fdr')==='1')
     &&(!f.holdout||d.getAttribute('data-holdout')==='1')
     &&(!q||(d.getAttribute('data-search')||'').indexOf(q)>=0);
   if(ok){shown++; if(d.getAttribute('aria-current')==='true') selVisible=true; r.removeAttribute('hidden');}
   else{r.setAttribute('hidden','');}
 });
 var cnt=$('#ev-count'); if(cnt) cnt.textContent=shown+' / '+total+' campaigns';
 var none=$('#ev-none'); if(none){ if(shown){none.setAttribute('hidden','');} else {none.removeAttribute('hidden');} }
 if(!selVisible && shown>0 && state.view==='evolution'){ var first=visibleRows()[0]; if(first) go('evolution',first.getAttribute('data-id'),true); }
}
function wireChips(){
 var kindChips=$$('[data-f-kind]');
 kindChips.forEach(function(b){
   b.addEventListener('click',function(){
     kindChips.forEach(function(o){o.setAttribute('aria-pressed','false');});
     b.setAttribute('aria-pressed','true');
     applyRail();
   });
 });
 var ids=[['ev-search','input'],['ev-sort','change'],['ev-run','change'],['ev-audit','change'],['ev-team','change'],['ev-fdr','change'],['ev-holdout','change']];
 ids.forEach(function(pair){
   var el=document.getElementById(pair[0]);
   if(!el) return;
   el.addEventListener(pair[1],function(){
     if(pair[0]==='ev-sort'){ sortRail(); }
     applyRail();
   });
 });
 var reset=$('#ev-reset');
 if(reset){ reset.addEventListener('click',function(){
   resetFilters();
   sortRail(); applyRail();
 });}
 var back=$('#ev-back');
 if(back){ back.addEventListener('click',function(){var ex=$('#ev-explorer');if(ex)ex.classList.remove('detail-open');}); }
}

/* ---------------- tables ---------------- */
function cellValue(tr,idx){
 var td=tr.children[idx];
 if(!td) return '';
 return td.hasAttribute('data-v')?td.getAttribute('data-v'):(td.textContent||'').trim();
}
function initTables(){
 $$('table[data-sortable]').forEach(function(t){
   var ths=$$('th[data-sort]',t);
   ths.forEach(function(th,idx){
     th.addEventListener('click',function(){
       var dir=th.getAttribute('data-dir')==='asc'?'desc':'asc';
       ths.forEach(function(h){h.removeAttribute('data-dir');h.classList.remove('s-sorted');});
       th.setAttribute('data-dir',dir);
       th.classList.add('s-sorted');
       var sa=$('.sa',th); if(sa) sa.textContent=dir==='asc'?'▲':'▼';
       var tb=t.tBodies[0]; if(!tb) return;
       var rows=$$('tr',tb);
       var type=th.getAttribute('data-sort');
       rows.sort(function(a,b){
         var av=cellValue(a,idx),bv=cellValue(b,idx);
         if(type==='num'){
           var an=parseFloat(av),bn=parseFloat(bv);
           if(isNaN(an)) an=-Infinity;
           if(isNaN(bn)) bn=-Infinity;
           return dir==='asc'?an-bn:bn-an;
         }
         if(type==='date'){
           return dir==='asc'?(av<bv?-1:av>bv?1:0):(av>bv?-1:av<bv?1:0);
         }
         var c=String(av).localeCompare(String(bv),'zh-Hans-CN');
         return dir==='asc'?c:-c;
       });
       rows.forEach(function(r){tb.appendChild(r);});
     });
   });
 });
}

/* ---------------- ideas ---------------- */
function applyIdeas(){
 var scope=$('#view-ideas'); if(!scope) return;
 var q=(($('#idea-search')&&$('#idea-search').value)||'').toLowerCase().trim();
 var role='all';
 $$('[data-i-role]').forEach(function(b){ if(b.getAttribute('aria-pressed')==='true') role=b.getAttribute('data-i-role'); });
 var shown=0,total=0;
 $$('.idea-row',scope).forEach(function(r){
   total++;
   var ok=(role==='all'||r.getAttribute('data-role')===role)&&(!q||lowerText(r).indexOf(q)>=0);
   if(ok){shown++;r.removeAttribute('hidden');}else{r.setAttribute('hidden','');}
 });
 var cnt=$('#idea-count'); if(cnt) cnt.textContent=shown+' / '+total+' ideas';
}
function wireIdeas(){
 $$('[data-i-role]').forEach(function(b){
   b.addEventListener('click',function(){
     $$('[data-i-role]').forEach(function(o){o.setAttribute('aria-pressed','false');});
     b.setAttribute('aria-pressed','true');
     applyIdeas();
   });
 });
 var s=$('#idea-search'); if(s) s.addEventListener('input',applyIdeas);
 applyIdeas();
}

/* ---------------- candidates ---------------- */
function applyCands(){
 var body=$('#cand-body'); if(!body) return;
 var q=(($('#cand-search')&&$('#cand-search').value)||'').toLowerCase().trim();
 var verdict='all';
 $$('[data-c-verdict]').forEach(function(b){ if(b.getAttribute('aria-pressed')==='true') verdict=b.getAttribute('data-c-verdict'); });
 var posOnly=$('#cand-pos'); var needPos=!!(posOnly&&posOnly.checked);
 var shown=0,total=0;
 $$('tr',body).forEach(function(tr){
   total++;
   var ok=(verdict==='all'||tr.getAttribute('data-verdict')===verdict)
     &&(!needPos||parseFloat(tr.getAttribute('data-lot'))>0)
     &&(!q||lowerText(tr).indexOf(q)>=0);
   if(ok){shown++;tr.removeAttribute('hidden');}else{tr.setAttribute('hidden','');}
 });
 var cnt=$('#cand-count'); if(cnt) cnt.textContent=shown+' / '+total+' candidates';
}
function wireCands(){
 $$('[data-c-verdict]').forEach(function(b){
   b.addEventListener('click',function(){
     $$('[data-c-verdict]').forEach(function(o){o.setAttribute('aria-pressed','false');});
     b.setAttribute('aria-pressed','true');
     applyCands();
   });
 });
 var s=$('#cand-search'); if(s) s.addEventListener('input',applyCands);
 var p=$('#cand-pos'); if(p) p.addEventListener('change',applyCands);
 applyCands();
}

/* ---------------- agents ---------------- */
function applyAgents(){
 var tb=$('#agent-body'); if(!tb) return;
 var only=$('#agent-team-only'); var need=!!(only&&only.checked);
 var shown=0,total=0;
 $$('tr',tb).forEach(function(tr){
   total++;
   var ok=!need||tr.getAttribute('data-team')==='1';
   if(ok){shown++;tr.removeAttribute('hidden');}else{tr.setAttribute('hidden','');}
 });
 var cnt=$('#agent-count'); if(cnt) cnt.textContent=shown+' / '+total+' campaigns';
}
function wireAgents(){
 var only=$('#agent-team-only'); if(only) only.addEventListener('change',applyAgents);
 applyAgents();
}

/* ---------------- conclusions / datasets ---------------- */
function wireSimpleFilters(){
 [['#concl-search','.concl-row','#concl-count','rows'],['#axis-search','.axis-row','#axis-count','fields']].forEach(function(cfg){
   var s=$(cfg[0]); if(!s) return;
   var apply=function(){
     var q=(s.value||'').toLowerCase().trim();
     var shown=0,total=0;
     $$(cfg[1]).forEach(function(r){
       total++;
       var ok=!q||(r.getAttribute('data-search')||'').indexOf(q)>=0;
       if(ok){shown++;r.removeAttribute('hidden');}else{r.setAttribute('hidden','');}
     });
     var c=$(cfg[2]); if(c) c.textContent=shown+' / '+total+' '+cfg[3];
   };
   s.addEventListener('input',apply);
   apply();
 });
}

/* ---------------- palette ---------------- */
var palIndex=null, palActive=0, palShown=[];
function buildPalette(){
 palIndex=[];
 $$('[data-pal]').forEach(function(el){
   var kind=el.getAttribute('data-pal');
   var label=el.getAttribute('data-pl-label')||'';
   var sub=el.getAttribute('data-pl-sub')||'';
   var href=el.getAttribute('data-pal-href')||'';
   if(!label && kind==='idea'){
     var tt=el.querySelector('.tt');
     label=tt?(tt.textContent||'').trim():'';
     var ca=el.querySelector('.top a');
     sub=ca?(ca.textContent||'').trim():'';
   }
   if(!label) return;
   var q=(label+' '+sub+' '+(el.getAttribute('data-search')||'')).toLowerCase();
   palIndex.push({kind:kind,label:label,sub:sub,href:href,q:q,factor:el.getAttribute('data-factor')||''});
 });
}
var PAL_LABEL={campaign:'Campaign',candidate:'Factor',idea:'Idea',dataset:'Dataset',axis:'Field'};
var PAL_ORDER=['campaign','candidate','idea','dataset','axis'];
function openPalette(){
 var back=$('#pal-back'); if(!back) return;
 back.hidden=false;
 var i=$('#pal-input'); if(i){i.value='';i.focus();}
 renderPalette('');
}
function closePalette(){
 var back=$('#pal-back'); if(back) back.hidden=true;
}
function renderPalette(q){
 var box=$('#pal-res'); if(!box) return;
 q=(q||'').toLowerCase().trim();
 var groups={};
 palIndex.forEach(function(it){
   if(q && it.q.indexOf(q)<0 && it.label.toLowerCase().indexOf(q)<0) return;
   (groups[it.kind]=groups[it.kind]||[]).push(it);
 });
 var total=0, limit=q?6:12, out='';
 PAL_ORDER.forEach(function(kind){
   var items=groups[kind]||[];
   if(!items.length) return;
   // exact/prefix matches first
   items.sort(function(a,b){
     var ap=a.label.toLowerCase().indexOf(q)===0?0:1, bp=b.label.toLowerCase().indexOf(q)===0?0:1;
     return ap-bp||a.label.length-b.label.length;
   });
   var shown=items.slice(0,limit);
   total+=shown.length;
   out+='<div class="pal-group">'+PAL_LABEL[kind]+' · '+items.length+'</div>';
   shown.forEach(function(it){
     out+='<div class="pal-item" role="option" data-href="'+esc(it.href)+'" data-factor="'+esc(it.factor)+'" aria-selected="false">'
       +'<span class="g">'+PAL_LABEL[kind]+'</span>'
       +'<span style="min-width:0"><span class="lb">'+esc(it.label)+'</span><span class="sb" style="display:block">'+esc(it.sub)+'</span></span>'
       +'</div>';
   });
 });
 if(!total){ out='<div class="pal-empty">没有匹配项</div>'; }
 box.innerHTML=out;
 palShown=$$('.pal-item',box);
 palActive=0;
 highlightPal();
}
function esc(s){
 return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function highlightPal(){
 palShown.forEach(function(it,i){
   if(i===palActive){it.setAttribute('aria-selected','true');}else{it.setAttribute('aria-selected','false');}
 });
 if(palShown[palActive]){
   try{palShown[palActive].scrollIntoView({block:'nearest'});}catch(e){}
 }
}
function choosePal(el){
 if(!el) return;
 var href=el.getAttribute('data-href')||'';
 var factor=el.getAttribute('data-factor')||'';
 closePalette();
 if(href){ location.hash=href.replace(/^#/,''); }
 if(factor){ focusFactor(factor); }
}
function focusFactor(fid){
 if(!fid) return;
 window.setTimeout(function(){
   var row=null;
   $$('tr[data-factor]').forEach(function(tr){ if(tr.getAttribute('data-factor')===fid) row=tr; });
   if(!row) return;
   var card=row.closest('.camp-detail');
   if(card && card.hasAttribute('hidden')){
     location.hash='#/evolution/'+card.getAttribute('data-id');
   }
   window.setTimeout(function(){
     row=null;
     $$('tr[data-factor]').forEach(function(tr){ if(tr.getAttribute('data-factor')===fid) row=tr; });
     if(!row) return;
     row.classList.add('flash');
     try{row.scrollIntoView({block:'center'});}catch(e){}
     window.setTimeout(function(){row.classList.remove('flash');},2000);
   },60);
 },30);
}
function wirePalette(){
 buildPalette();
 var open=$('#btn-pal'); if(open) open.addEventListener('click',openPalette);
 var back=$('#pal-back'); if(back){
   back.addEventListener('click',function(e){ if(e.target===back) closePalette(); });
 }
 var inp=$('#pal-input');
 if(inp){
   inp.addEventListener('input',function(){ renderPalette(inp.value); });
   inp.addEventListener('keydown',function(e){
     if(e.key==='ArrowDown'){ e.preventDefault(); if(palActive<palShown.length-1){palActive++;highlightPal();} }
     else if(e.key==='ArrowUp'){ e.preventDefault(); if(palActive>0){palActive--;highlightPal();} }
     else if(e.key==='Enter'){ e.preventDefault(); choosePal(palShown[palActive]); }
   });
 }
 var res=$('#pal-res');
 if(res){
   res.addEventListener('click',function(e){
     var it=e.target.closest?e.target.closest('.pal-item'):null;
     if(it) choosePal(it);
   });
   res.addEventListener('mousemove',function(e){
     var it=e.target.closest?e.target.closest('.pal-item'):null;
     if(it){ var i=palShown.indexOf(it); if(i>=0&&i!==palActive){palActive=i;highlightPal();} }
   });
 }
}

/* ---------------- global chrome ---------------- */
function wireChrome(){
 var dense=$('#btn-dense');
 if(dense){
   var on=store.get('dense','0')==='1';
   doc.body.classList.toggle('dense',on);
   dense.setAttribute('aria-pressed',String(on));
   dense.addEventListener('click',function(){
     var next=!doc.body.classList.contains('dense');
     doc.body.classList.toggle('dense',next);
     dense.setAttribute('aria-pressed',String(next));
     store.set('dense',next?'1':'0');
   });
 }
 var longBtn=$('#btn-long');
 if(longBtn){
   var lon=store.get('long','0')==='1';
   doc.body.classList.toggle('longdoc',lon);
   longBtn.setAttribute('aria-pressed',String(lon));
   longBtn.textContent=lon?'分页':'长文档';
   longBtn.addEventListener('click',function(){
     var next=!doc.body.classList.contains('longdoc');
     doc.body.classList.toggle('longdoc',next);
     longBtn.setAttribute('aria-pressed',String(next));
     longBtn.textContent=next?'分页':'长文档';
     store.set('long',next?'1':'0');
     if(next){ window.scrollTo(0,0); }
   });
 }
 var printBtn=$('#btn-print');
 if(printBtn) printBtn.addEventListener('click',function(){ closePalette(); window.print(); });
 doc.addEventListener('click',function(e){
   var copy=e.target.closest?e.target.closest('[data-copy]'):null;
   if(copy){
     var id=copy.getAttribute('data-copy');
     var url=location.href.split('#')[0]+'#/evolution/'+encodeURIComponent(id);
     var done=function(){ toast('已复制链接'); };
     if(navigator.clipboard && navigator.clipboard.writeText){
       navigator.clipboard.writeText(url).then(done,function(){ toast('复制失败：'+url); });
     }else{
       var ta=doc.createElement('textarea'); ta.value=url; doc.body.appendChild(ta); ta.select();
       try{doc.execCommand('copy');done();}catch(err){toast('复制失败');}
       doc.body.removeChild(ta);
     }
     return;
   }
   var exp=e.target.closest?e.target.closest('[data-expand]'):null;
   if(exp){
     var row=exp.closest('.idea-row');
     if(row){ var open=row.classList.toggle('open'); exp.textContent=open?'收起':'展开'; }
   }
 });
}
function wireKeyboard(){
 doc.addEventListener('keydown',function(e){
   var t=e.target||{};
   var tag=(t.tagName||'').toLowerCase();
   var typing=tag==='input'||tag==='textarea'||tag==='select'||t.isContentEditable;
   if((e.key==='k'||e.key==='K')&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); openPalette(); return; }
   if(e.key==='/'&&!typing){ e.preventDefault(); openPalette(); return; }
   if(e.key==='Escape'){
     if($('#pal-back') && !$('#pal-back').hidden){ closePalette(); return; }
     if(typing && t.id==='ev-search'){ t.value=''; applyRail(); return; }
   }
   if(state.view==='evolution'&&!typing&&(e.key==='j'||e.key==='k'||e.key==='ArrowDown'||e.key==='ArrowUp')){
     var rows=visibleRows();
     if(!rows.length) return;
     e.preventDefault();
     var cur=null;
     rows.forEach(function(r){ if(r.getAttribute('aria-current')==='true') cur=r; });
     var i=cur?rows.indexOf(cur):-1;
     var next=rows[Math.min(rows.length-1,Math.max(0,(e.key==='j'||e.key==='ArrowDown')?i+1:i-1))];
     if(next) go('evolution',next.getAttribute('data-id'),true);
   }
 });
}
function wireRailClicks(){
 $$('.camp-row').forEach(function(r){
   r.addEventListener('click',function(){
     if(window.matchMedia && window.matchMedia('(max-width:999px)').matches){
       selectRow(r.getAttribute('data-id'),true,true);
     }
   });
 });
}
function init(){
 initTables();
 wireRailClicks();
 wireChips();
 wireIdeas();
 wireCands();
 wireAgents();
 wireSimpleFilters();
 wirePalette();
 wireChrome();
 wireKeyboard();
 if($('#ev-list')){ sortRail(); applyRail(); }
 applyRoute();
}
if(doc.readyState==='loading'){ doc.addEventListener('DOMContentLoaded',init); } else { init(); }
})();
"""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

RUN_LABELS = {
    "nine_hour_run": ("9h run", "info"),
    "post_run_replication": ("replication", "vio"),
    "legacy": ("legacy", "warn"),
}
KIND_SHORT = {"research": "研究", "discovery": "Discovery", "ml": "ML", "calibration": "L0"}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _tag(label: Any, tone: str) -> str:
    return f'<span class="tag {tone}">{_e(label)}</span>'


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.1f}"
    if abs(number) >= 1:
        return f"{number:.{digits}g}"
    return f"{number:.{digits}f}"


def _raw(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number:
        return ""
    return f"{number:.6f}"


def _raw_sort(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _link(path: Any, base: Path, label: str | None = None) -> str:
    if not path:
        return ""
    try:
        target = os.path.relpath(str(path), start=str(base)).replace("\\", "/")
    except Exception:  # noqa: BLE001
        target = str(path)
    return f'<a href="{_e(target)}">{_e(label or Path(str(path)).name)}</a>'


def _search_text(*parts: Any) -> str:
    joined = " ".join(str(p) for p in parts if p)
    return _e(" ".join(joined.lower().split()))


def _verdict_tag(verdict: Any) -> str:
    text = str(verdict or "")
    if text == "pass":
        return _tag("pass", "ok")
    if text in {"fail", "admission_fail"}:
        return _tag(text, "bad")
    if not text:
        return '<span class="na">—</span>'
    return _tag(text, "gray")


def _audit_state(campaign: dict[str, Any]) -> str:
    audit = campaign.get("protocol_audit")
    if not isinstance(audit, dict):
        return "none"
    return "ok" if audit.get("ok") else "fail"


def _run_tag(run_group: Any) -> str:
    label, tone = RUN_LABELS.get(str(run_group or "legacy"), ("legacy", "warn"))
    return _tag(label, tone)


def _edge_total(campaign: dict[str, Any]) -> int:
    edge = campaign.get("edge_status")
    if not isinstance(edge, dict):
        return 0
    return sum(_int(v) for v in edge.values())


def _th(label: str, sort: str = "str", cls: str = "", *, direction: str = "") -> str:
    attr = f' data-dir="{direction}"' if direction else ""
    arrow = "▼" if direction == "desc" else "▲" if direction == "asc" else ""
    solid = " s-sorted" if direction else ""
    return (
        f'<th class="{cls}{solid}" data-sort="{sort}"{attr}>{_e(label)}'
        f'<span class="sa">{arrow}</span></th>'
    )


def _td(content: Any, value: Any = None, cls: str = "") -> str:
    attr = ""
    if value is not None:
        attr = f' data-v="{_e(value if not isinstance(value, float) else f"{value:.6f}")}"'
    return f'<td class="{cls}"{attr}>{content}</td>'


def _empty(msg: str) -> str:
    return f'<p class="mini" style="margin:6px 0">{_e(msg)}</p>'


# ---------------------------------------------------------------------------
# campaign rail + detail
# ---------------------------------------------------------------------------


def _campaign_row(campaign: dict[str, Any]) -> str:
    campaign_id = str(campaign.get("campaign_id") or "")
    phase = _int(campaign.get("phase"))
    kind = str(campaign.get("kind") or "")
    run_group = str(campaign.get("run_group") or "legacy")
    audit = _audit_state(campaign)
    best = campaign.get("best") or {}
    metrics = best.get("metrics") or {}
    primary_t = best.get("long_only_t") or metrics.get("long_only_t") or metrics.get("net_t")
    primary_t_raw = _raw(primary_t)
    primary_t_text = "—" if not primary_t_raw else _num(primary_t, 3)
    tone = "pos" if primary_t_raw and float(primary_t_raw) > 0 else "neg" if primary_t_raw and float(primary_t_raw) < 0 else ""
    date = str(campaign.get("created_at") or "")
    search = " ".join(
        str(p or "").lower()
        for p in (
            campaign_id,
            campaign.get("label"),
            campaign.get("objective"),
            campaign.get("kind_label"),
            kind,
            run_group,
            date,
        )
    )
    dots = ""
    if audit == "fail":
        dots += '<i class="dot bad" title="protocol audit failed"></i>'
    elif audit == "ok":
        dots += '<i class="dot ok" title="protocol audit ok"></i>'
    if campaign.get("holdout"):
        dots += '<i class="dot warn" title="holdout used historically"></i>'
    meta = f"{KIND_SHORT.get(kind, kind)} · {RUN_LABELS.get(run_group, ('legacy', 'warn'))[0]} · {date[5:]}"
    kind_label = str(campaign.get("kind_label") or kind)
    dialog_sub = f"{campaign_id} · {kind_label}"
    return (
        f'<a class="camp-row" role="listitem" href="#/evolution/{_e(campaign_id)}"'
        f' data-id="{_e(campaign_id)}" data-phase="{phase}" data-kind="{_e(kind)}"'
        f' data-run="{_e(run_group)}" data-audit="{audit}" data-team="{1 if campaign.get("has_team") else 0}"'
        f' data-fdr="{1 if campaign.get("fdr_pass") else 0}" data-holdout="{1 if campaign.get("holdout") else 0}"'
        f' data-gate="{_int(campaign.get("gate_pass"))}" data-lot="{primary_t_raw}"'
        f' data-cands="{len(campaign.get("candidates") or [])}"'
        f' data-rounds="{_int(campaign.get("n_rounds"))}" data-date="{_e(date)}"'
        f' data-search="{_search_text(search)}"'
        f' data-pal="campaign" data-pl-label="{_e(campaign.get("label"))}"'
        f' data-pl-sub="{_e(dialog_sub)}"'
        f' data-pal-href="#/evolution/{_e(campaign_id)}">'
        f'<span class="ph">P{phase:02d}</span>'
        f'<span><span class="lbl">{dots}{_e(campaign.get("label"))}</span>'
        f'<span class="mt">{_e(meta)}</span></span>'
        f'<span class="st"><b class="{tone}">LO t {primary_t_text}</b>gate {_int(campaign.get("gate_pass"))}</span>'
        f"</a>"
    )


def _rounds_table(campaign: dict[str, Any]) -> str:
    rounds = campaign.get("rounds") or []
    if not rounds:
        return _empty("该 campaign 没有 round 记录。")
    rows = []
    for row in rounds:
        metrics = row.get("metrics") or {}
        long_only = row.get("long_only") or (row.get("research_result") or {}).get("long_only") or {}
        size = long_only.get("size_screened") or {}
        expression = str(metrics.get("expression") or "")
        schema = str(row.get("schema") or "").replace("factor_lab.alpha.", "").replace(".v1", "")
        agents = row.get("agents") or []
        tasks = row.get("team_tasks") or []
        refs = row.get("factor_refs") or []
        extras = []
        if agents:
            extras.append(f"{len(agents)} agents")
        if tasks:
            extras.append(f"{len(tasks)} tasks")
        if refs:
            extras.append(f"{len(refs)} refs")
        round_cell = f'<b>{_e(row.get("round_id"))}</b><span class="mini" style="display:block">{_e(schema[:30])}</span>'
        if extras:
            round_cell += f'<span class="mini" style="display:block">{_e(" · ".join(extras))}</span>'
        if expression:
            expr_cell = f'<code class="expr" title="{_e(expression)}">{_e(expression)}</code>'
        else:
            expr_cell = '<span class="na">—</span>'
        rows.append(
            "<tr>"
            + _td(round_cell)
            + _td(_tag(row.get("decision"), "gray") if row.get("decision") else '<span class="na">—</span>')
            + _td(_verdict_tag(row.get("verdict")))
            + _td(_num(size.get("long_only_t"), 3), _raw(size.get("long_only_t")), "num")
            + _td(_num(size.get("long_only_net_excess_annualized"), 4), _raw(size.get("long_only_net_excess_annualized")), "num")
            + _td(_num(row.get("long_only_t"), 3), _raw(row.get("long_only_t")), "num")
            + _td(_num(metrics.get("turnover"), 3), _raw(metrics.get("turnover")), "num")
            + _td(expr_cell)
            + "</tr>"
        )
    head = (
        "<tr>"
        + _th("轮次 / schema", "str")
        + _th("决策", "str")
        + _th("Gate", "str")
        + _th("LO t", "num", "num")
        + _th("LO net", "num", "num")
        + _th("LS net_t (diag)", "num", "num")
        + _th("turnover", "num", "num")
        + _th("表达式", "str")
        + "</tr>"
    )
    return (
        '<div class="tablewrap"><table data-sortable><thead>'
        + head
        + "</thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _candidates_table(campaign: dict[str, Any], *, with_campaign: bool = False) -> str:
    rows = list(campaign.get("candidates") or [])
    if not rows:
        return _empty("没有独立 cycle candidate metrics；ML admission 见下方。")
    rows.sort(key=lambda r: _raw_sort(r.get("long_only_t")), reverse=True)
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        long_only_t = row.get("long_only_t")
        long_only_net = row.get("long_only_net_excess_annualized")
        fdr = row.get("program_fdr")
        if fdr is True:
            fdr_html = _tag("pass", "ok")
        elif fdr is False:
            fdr_html = _tag(f"fail p={_num(row.get('program_p'), 3)}", "bad")
        else:
            fdr_html = '<span class="na">—</span>'
        factor_id = str(row.get("factor_id") or "")
        expression = str(row.get("expression") or "")
        factor_cell = f"<code>{_e(factor_id or '—')}</code>"
        if expression:
            factor_cell += f'<span style="display:block;margin-top:2px"><code class="expr" title="{_e(expression)}">{_e(expression)}</code></span>'
        cells = []
        if with_campaign:
            cells.append(
                _td(
                    f'<a href="#/evolution/{_e(campaign.get("campaign_id"))}">{_e(campaign.get("label"))}</a>'
                )
            )
        cells += [
            _td(factor_cell),
            _td(_e(row.get("family") or "—")),
            _td(_verdict_tag(row.get("verdict"))),
            _td(_num(long_only_net, 4), _raw(long_only_net), "num"),
            _td(_num(long_only_t, 3), _raw(long_only_t), "num"),
            _td(_num(metrics.get("net_t"), 3), _raw(metrics.get("net_t")), "num"),
            _td(_num(metrics.get("coverage"), 4), _raw(metrics.get("coverage")), "num"),
            _td(_num(row.get("p_one_sided_positive"), 4) + " / " + _num(row.get("p_two_sided"), 4), "", "num"),
            _td(fdr_html),
            _td(_e(row.get("round_id") or "—")),
        ]
        body.append(
            f'<tr data-factor="{_e(factor_id)}" data-verdict="{_e(row.get("verdict"))}"'
            f' data-lot="{_raw(row.get("long_only_t"))}">'
            + "".join(cells)
            + "</tr>"
        )
    head = "<tr>"
    if with_campaign:
        head += _th("Campaign", "str")
    head += (
        _th("候选 / 表达式", "str")
        + _th("Family", "str")
        + _th("Gate", "str")
        + _th("net", "num", "num")
        + _th("LO t", "num", "num", direction="desc")
        + _th("coverage", "num", "num")
        + _th("p(pos/two)", "num", "num")
        + _th("Program FDR", "str")
        + _th("Round", "str")
        + "</tr>"
    )
    return (
        '<div class="tablewrap"><table data-sortable><thead>'
        + head
        + "</thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _idea_cards(campaign: dict[str, Any], base: Path, *, palette: bool = True, compact: bool = False) -> str:
    ideas = campaign.get("ideas") or []
    if not ideas:
        return _empty("没有 team artifact 或可解析 idea。")
    campaign_id = str(campaign.get("campaign_id") or "")
    campaign_label = str(campaign.get("label") or "")
    out = []
    for idea in ideas:
        role = str(idea.get("agent") or "")
        label = dict(ROLES).get(role, role or "—")
        source = idea.get("source")
        kind = str(idea.get("kind") or "idea")
        palette_attrs = (
            f' data-pal="idea" data-pal-href="#/evolution/{_e(campaign_id)}"'
            if palette
            else ""
        )
        if compact:
            out.append(
                f'<article class="idea-row compact" data-role="{_e(role)}" data-campaign="{_e(campaign_id)}"{palette_attrs}>'
                f'<span class="i-tags">{_tag(label, "info")}{_tag(kind, "vio")}</span>'
                f'<a class="i-camp" href="#/evolution/{_e(campaign_id)}">{_e(campaign_label)}</a>'
                f'<span class="tt" title="{_e(idea.get("title"))}">{_e(idea.get("title"))}</span>'
                '<button class="linkbtn" type="button" data-expand>展开</button>'
                f'<div class="dd">{_e(idea.get("detail")) or "（无明细）"}'
                + (f'<span class="i-src">{_link(source, base, "来源 artifact")}</span>' if source else "")
                + "</div></article>"
            )
            continue
        out.append(
            '<article class="idea-row"'
            f' data-role="{_e(role)}" data-campaign="{_e(campaign_id)}"'
            f"{palette_attrs}>"
            f'<div class="top">{_tag(label, "info")} {_tag(kind, "vio")}'
            f'<a class="mini" href="#/evolution/{_e(campaign_id)}">{_e(campaign_label)}</a></div>'
            f'<div class="tt">{_e(idea.get("title"))}</div>'
            + (f'<div class="dd">{_e(idea.get("detail"))}</div>' if idea.get("detail") else "")
            + '<div class="acts"><button class="linkbtn" type="button" data-expand>展开</button>'
            + (_link(source, base, "来源 artifact") if source else "")
            + "</div></article>"
        )
    return "".join(out)


def _agent_evidence(campaign: dict[str, Any], base: Path) -> str:
    out = []
    for role, label in ROLES:
        evidence = (campaign.get("agents") or {}).get(role) or {}
        if not evidence.get("used"):
            continue
        files = evidence.get("files") or []
        tasks = evidence.get("tasks") or []
        status = evidence.get("status")
        out.append(
            '<div class="idea-row"><div class="top">'
            + _tag(label, "info")
            + (_tag(status, "ok") if status else "")
            + (_tag(f"{len(files)} files", "gray") if files else "")
            + "</div>"
            + (f'<div class="dd">tasks: {_e(", ".join(str(t) for t in tasks))}</div>' if tasks else "")
            + (f'<div class="acts">{_link(files[0], base)}<span class="mini">{_e(f"+{len(files) - 1} more") if len(files) > 1 else ""}</span></div>' if files else "")
            + "</div>"
        )
    return "".join(out) or _empty("该 campaign 没有 team artifact；只有 deterministic records。")


def _dataset_rows(campaign: dict[str, Any], base: Path) -> str:
    seen: dict[tuple, dict[str, Any]] = {}
    for manifest in campaign.get("dataset_manifests") or []:
        if isinstance(manifest, dict):
            key = (
                str(manifest.get("start")),
                str(manifest.get("end")),
                _int(manifest.get("codes")),
                len(manifest.get("fields") or []),
            )
            seen[key] = manifest
    if not seen:
        return _empty("该 campaign 没有记录 data_manifest。")
    body = "".join(
        f"<tr><td>{_e(m.get('start'))}..{_e(m.get('end'))}</td>"
        f"<td class='num'>{_e(m.get('codes'))}</td>"
        f"<td class='num'>{len(m.get('fields') or [])}</td>"
        f"<td>{_e(', '.join(m.get('services') or []))}</td>"
        f"<td class='num'>{_e(m.get('rows'))}</td></tr>"
        for m in seen.values()
    )
    return (
        '<div class="tablewrap flat"><table><thead><tr><th>Range</th><th>Codes</th>'
        "<th>Fields</th><th>Services</th><th>Rows</th></tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def _artifact_fold(title: str, paths: list[Any], base: Path) -> str:
    if not paths:
        return ""
    links = "".join(_link(p, base) for p in paths[:10])
    more = f'<span class="mini">+{len(paths) - 10} more</span>' if len(paths) > 10 else ""
    return (
        f'<details class="fold"><summary>{_e(title)}（{len(paths)}）</summary>'
        f'<div class="fold-body">{links}{more}</div></details>'
    )


def _campaign_detail(campaign: dict[str, Any], base: Path, prev_id: str | None, next_id: str | None) -> str:
    campaign_id = str(campaign.get("campaign_id") or "")
    phase = _int(campaign.get("phase"))
    kind = str(campaign.get("kind") or "")
    run_group = str(campaign.get("run_group") or "legacy")
    audit_data = campaign.get("protocol_audit") if isinstance(campaign.get("protocol_audit"), dict) else None
    best = campaign.get("best") or {}
    best_metrics = best.get("metrics") or {}
    rounds = campaign.get("rounds") or []
    candidates = campaign.get("candidates") or []
    ideas = campaign.get("ideas") or []
    ml = campaign.get("ml_summary")
    badges = [
        _tag(campaign.get("kind_label") or KIND_SHORT.get(kind, kind), "gray"),
        _run_tag(run_group),
        _tag(campaign.get("state") or "—", "gray"),
    ]
    if audit_data is not None:
        badges.append(_tag("audit ok", "ok") if audit_data.get("ok") else _tag("audit FAILED", "bad"))
    if campaign.get("holdout"):
        badges.append(_tag(f"holdout {campaign.get('holdout')}", "warn"))
    if campaign.get("has_team"):
        badges.append(_tag("team evidence", "info"))
    if campaign.get("fdr_pass"):
        badges.append(_tag(f"FDR pass {len(campaign.get('fdr_pass') or [])}", "ok"))
    edge = campaign.get("edge_status") if isinstance(campaign.get("edge_status"), dict) else {}
    edge_text = ", ".join(f"{k} {v}" for k, v in edge.items()) or "—"

    kpis = [
        (str(_int(campaign.get("n_rounds"))), "轮次"),
        (str(len(candidates)), "候选"),
        (str(_int(campaign.get("gate_pass"))), "Gate pass"),
        (_num(best.get("long_only_t"), 3), "best LO t"),
        (str(len(campaign.get("fdr_pass") or [])), "FDR pass"),
        (str(_edge_total(campaign)), "Edge Cards"),
    ]
    kpi_html = "".join(
        f'<div class="cd-kpi"><b>{_e(value)}</b><span>{_e(label)}</span></div>' for value, label in kpis
    )

    audit_note = ""
    if audit_data is not None and not audit_data.get("ok"):
        issues = audit_data.get("issues") or []
        warnings = audit_data.get("warnings") or []
        bits = "".join(f"<li>{_e(item)}</li>" for item in (issues + warnings)[:6])
        audit_note = (
            '<div class="callout bad"><b>Protocol audit failed.</b>'
            f"<ul style='margin:5px 0 0;padding-left:18px;font-size:12px'>{bits}</ul></div>"
        )

    ml_html = ""
    if ml:
        metrics = ml.get("metrics") or {}
        admission = ml.get("admission") or {}
        checks = admission.get("checks") or {}
        failed = [k for k, v in checks.items() if v is False]
        ml_html = (
            '<section class="cd-sec"><h3>ML branch</h3><div class="card">'
            f"<p>OOS rank IC <b>{_num(metrics.get('oos_rank_ic_mean'), 4)}</b> · t <b>{_num(metrics.get('oos_rank_ic_t'), 3)}</b> · "
            f"n_oos_dates {_e(metrics.get('n_oos_dates'))} · n_rows {_e(metrics.get('n_oos_rows'))}</p>"
            f"<p>admission passed=<b>{_e(admission.get('passed'))}</b> · blocked={_e(', '.join(admission.get('blocked_reasons') or []) or '—')}"
            + (f" · failed checks={_e(', '.join(failed))}" if failed else "")
            + "</p>"
            + (f"<p>{_e(ml.get('conclusion'))}</p>" if ml.get("conclusion") else "")
            + "</div></section>"
        )

    team_files = campaign.get("team_files") or []
    eval_files = campaign.get("eval_files") or []
    edge_manifests = campaign.get("edge_manifests") or []
    edge_manifest_note = ""
    if edge_manifests:
        first = edge_manifests[0] if isinstance(edge_manifests[0], dict) else {}
        edge_manifest_note = (
            f'<div class="mini" style="margin-top:4px">Edge Card manifests：{len(edge_manifests)} 个'
            f'（{_e(first.get("category"))} · {_e(first.get("start"))}..{_e(first.get("end"))} · '
            f'{_e(first.get("codes"))} codes · {len(first.get("fields") or [])} fields · '
            f'{_e(", ".join(first.get("services") or []))}）</div>'
        )
    provenance = (
        _artifact_fold("team 文件", team_files, base)
        + _artifact_fold("evaluation 文件", eval_files, base)
        + edge_manifest_note
        + (f'<div class="mini" style="margin-top:6px">stop reason: {_e(campaign.get("stop_reason") or "—")}</div>')
    )

    prev_html = f'<a class="btn" href="#/evolution/{_e(prev_id)}">← 上一个</a>' if prev_id else ""
    next_html = f'<a class="btn" href="#/evolution/{_e(next_id)}">下一个 →</a>' if next_id else ""
    return f"""
<article class="camp-detail" data-id="{_e(campaign_id)}" hidden>
  <div class="cd-top">
    <span class="phase">P{phase:02d}</span>
    <h2>{_e(campaign.get("label"))}</h2>
    <div class="cd-actions">{prev_html}{next_html}<button class="btn" type="button" data-copy="{_e(campaign_id)}">复制链接</button></div>
  </div>
  <div class="cd-badges">{"".join(badges)}</div>
  <p class="cd-id"><code>{_e(campaign_id)}</code> · {_e(campaign.get("created_at"))} · {_e(campaign.get("kind"))} / {_e(run_group)}</p>
  <div class="cd-kpis">{kpi_html}</div>
  <p class="cd-obj"><b>目标：</b>{_e(campaign.get("objective"))}</p>
  <div class="cd-concl"><b>结论：</b>{_e(campaign.get("conclusion"))}</div>
  {audit_note}
  <section class="cd-sec"><h3>轮次 timeline <span class="n">{len(rounds)}</span></h3>{_rounds_table(campaign)}</section>
  <section class="cd-sec"><h3>候选结果 <span class="n">{len(candidates)}</span></h3>{_candidates_table(campaign)}</section>
  <div class="cd-cols">
    <section class="cd-sec"><h3>想法来源 <span class="n">{len(ideas)}</span></h3>{_idea_cards(campaign, base, palette=False)}</section>
    <section class="cd-sec"><h3>Agent 证据 <span class="n">{len(campaign.get("agents_used") or [])}</span></h3>{_agent_evidence(campaign, base)}</section>
  </div>
  {ml_html}
  <section class="cd-sec"><h3>数据集 <span class="n">{len(campaign.get("dataset_manifests") or [])}</span></h3>{_dataset_rows(campaign, base)}</section>
  <section class="cd-sec"><h3>产物与溯源</h3>{provenance}
    <div class="mini" style="margin-top:6px">next step: {_e(campaign.get("next_step") or "—")}</div>
    <div class="mini" style="margin-top:2px">Edge Cards: {_e(edge_text)}</div>
  </section>
  <div class="cd-foot">
    <a class="btn" href="#/ideas">在想法视图查看</a>
    <a class="btn" href="#/results">在结果视图查看</a>
  </div>
</article>
"""


def _page_head(title: str, sub: str, extra: str = "") -> str:
    return (
        '<div class="pagehead"><div>'
        f'<h1>{_e(title)}</h1><p class="sub">{_e(sub)}</p></div>'
        f'<div class="spacer">{extra}</div></div>'
    )


def _net_t_chart(campaigns: list[dict[str, Any]]) -> str:
    points: list[tuple[int, dict[str, Any], float | None]] = []
    for index, campaign in enumerate(campaigns):
        best = campaign.get("best") or {}
        try:
            value = float(best.get("long_only_t"))
        except (TypeError, ValueError):
            value = None
        points.append((index, campaign, value))
    values = [value for _i, _c, value in points if value is not None]
    if not values:
        return _empty("没有 best long-only t 数据。")
    low = min(min(values), 0.0)
    high = max(max(values), 0.0)
    span = (high - low) or 1.0
    low -= span * 0.12
    high += span * 0.12
    width, height = 1040, 250
    left, right, top, bottom = 46, 14, 12, 28
    total = len(points)

    def px(index: int) -> float:
        return left + (width - left - right) * (index / max(1, total - 1))

    def py(value: float) -> float:
        return top + (height - top - bottom) * ((high - value) / (high - low))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="each campaign best long-only t, ordered by phase">'
    ]
    step = 1.0 if (high - low) <= 8 else 2.0
    tick = int(low // step) * step
    while tick <= high + 1e-9:
        y = py(tick)
        cls = "zero" if abs(tick) < 1e-9 else "grid"
        parts.append(f'<line class="{cls}" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end">{_e(f"{tick:g}")}</text>')
        tick = round(tick + step, 10)
    running = -1e18
    run_points = []
    for index, _campaign, value in points:
        if value is None:
            continue
        running = max(running, value)
        run_points.append((px(index), py(running)))
    if len(run_points) > 1:
        path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in run_points)
        parts.append(f'<path class="runline" d="{path}"/>')
    for index, campaign, value in points:
        gate = _int(campaign.get("gate_pass"))
        kind = str(campaign.get("kind") or "")
        phase = _int(campaign.get("phase"))
        label = str(campaign.get("label") or "")
        if value is None:
            cls, radius, title = "cal", 2.0, f"P{phase:02d} · {label} · no long-only t"
        else:
            cls = "cal" if kind == "calibration" else "ok" if gate > 0 else "bad"
            radius = 2.0 if cls == "cal" else 3.2
            title = f"P{phase:02d} · {label} · best long-only t {_num(value, 4)} · gate pass {gate}"
        parts.append(
            f'<circle class="dot {cls}" cx="{px(index):.1f}" cy="{py(value or 0.0):.1f}" '
            f'r="{radius}"><title>{_e(title)}</title></circle>'
        )
    for index, campaign, _value in points:
        if index % 6:
            continue
        phase = _int(campaign.get("phase"))
        parts.append(
            f'<text x="{px(index):.1f}" y="{height - 8}" text-anchor="middle">P{phase:02d}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _overview_view(portfolio: dict[str, Any], campaigns: list[dict[str, Any]], kpis: dict[str, Any], base: Path) -> str:
    holdout = portfolio.get("holdout") or {}
    probes = portfolio.get("probes") or []
    latest = (probes[-1] or {}).get("payload") or {} if probes else {}
    probe = latest.get("conclusion") or {}
    audit_fail = [c for c in campaigns if _audit_state(c) == "fail"]
    ranked = sorted(
        campaigns,
        key=lambda c: _raw_sort((c.get("best") or {}).get("long_only_t")),
        reverse=True,
    )[:8]
    kpi_items = [
        (_int(kpis.get("campaigns")), "campaigns", ""),
        (_int(kpis.get("attempts")), "attempts", ""),
        (_int(kpis.get("ledger_trials")), "ledger trials", ""),
        (_int(kpis.get("edge_cards")), "Edge Cards", ""),
        (_int(kpis.get("validated")), "validated", "bad"),
        (_num(kpis.get("best_long_only_t"), 3), "best long-only t", ""),
    ]
    kpi_html = "".join(
        f'<div class="kpi {cls}"><b>{_e(value)}</b><span>{_e(label)}</span></div>'
        for value, label, cls in kpi_items
    )
    conclusion_cards = "".join(
        f'<div class="card"><b>{_e(item.get("title"))}</b><p>{_e(item.get("body"))}</p></div>'
        for item in portfolio.get("portfolio_conclusions") or []
    )
    top_rows = "".join(
        "<tr>"
        + _td(f'<a href="#/evolution/{_e(c.get("campaign_id"))}">{_e(c.get("label"))}</a>'
              f'<span class="mini" style="display:block">{_e(c.get("kind_label"))}</span>')
        + _td(_num((c.get("best") or {}).get("long_only_t"), 3),
              _raw((c.get("best") or {}).get("long_only_t")), "num")
        + _td(f'<code>{_e(((c.get("best") or {}).get("factor_id")) or "—")}</code>')
        + _td(str(_int(c.get("gate_pass"))), _int(c.get("gate_pass")), "num")
        + _td(str(len(c.get("fdr_pass") or [])), len(c.get("fdr_pass") or []), "num")
        + "</tr>"
        for c in ranked
    )
    audit_rows = "".join(
        f'<div class="idea-row" style="margin-bottom:6px"><div class="tt" style="margin-top:0">'
        f'<a href="#/evolution/{_e(c.get("campaign_id"))}">{_e(c.get("label"))}</a></div>'
        f'<div class="dd">{_e(((c.get("protocol_audit") or {}).get("issues") or ["—"])[0])}</div></div>'
        for c in audit_fail[:6]
    )
    question_cards = "".join(
        f'<a class="card" href="#/{view}" style="display:block"><b>{_e(title)}</b><p>{_e(body)}</p></a>'
        for view, title, body in [
            ("evolution", "01 · 研究迭代", "73 个 campaign 的轮次链、决策与结论；按 gate / long-only t / 审计排序。"),
            ("ideas", "02 · 想法来源", f"{len([i for c in campaigns for i in c.get('ideas') or []])} 条 idea：PathFinder 问题、Gateway 线索、预注册、FactorBuilder 产物。"),
            ("agents", "03 · Agent 调用", "role × campaign 矩阵与每个角色的产出统计。"),
            ("results", "04 · 测试结果", f"{sum(len(c.get('candidates') or []) for c in campaigns)} 个候选的 Gate / long-only t / FDR 平铺表，可按指标排序。"),
            ("datasets", "05 · 数据集", "数据窗口、字段覆盖与候选实际使用到的数据轴。"),
            ("conclusions", "06 · 结论", "全局结论与 69 个 campaign 的结论 / 下一步。"),
        ]
    )
    return f"""
<section class="view" data-view="overview" id="view-overview">
  <div class="pagehead"><div><h1>研究快照</h1>
    <p class="sub">先看全局状态，再按视图下钻。所有内容只读聚合；不引入新的 LLM 判断。</p></div>
    <div class="spacer"><span class="mini">generated {_e(portfolio.get("generated_at"))}</span></div></div>
  <div class="kpis">{kpi_html}</div>
  <div class="callout warn"><b>读法：</b>Gate pass ≠ freeze；family FDR pass ≠ program FDR pass。holdout 仍 {_e("locked" if holdout.get("locked") else "not locked")}；没有 validated / active 卡。</div>
  <div class="callout"><b>P0 probe：</b>{_e(latest.get("probe_id"))} · {_e(probe.get("code"))} · instrument_trusted={_e(probe.get("instrument_trusted"))}</div>
  <div class="grid g2" style="margin-top:11px">{conclusion_cards}</div>
  <section class="cd-sec"><h3>每个 campaign 的 best long-only t <span class="n">按时间顺序</span></h3>
    <div class="card">
      {_net_t_chart(campaigns)}
      <div class="legend"><span><i style="background:#0a7a54"></i>有 Gate pass</span><span><i style="background:#c2484f"></i>无 Gate pass</span><span><i style="background:#9aa6b8"></i>L0 / 无 long-only t</span><span>虚线 = 历史 running best</span></div>
      <p class="mini" style="margin-top:6px">横轴为 campaign 阶段（P01 → P73）。点越靠上 long-only t 越高；颜色只看 Gate，不代表 freeze 或 FDR 通过。</p>
    </div>
  </section>
  <div class="grid g2" style="margin-top:16px">
    <section class="cd-sec" style="margin:0"><h3>long-only t 最高的 campaign <span class="n">top 8</span></h3>
      <div class="tablewrap flat"><table><thead><tr><th>Campaign</th><th class="num">best LO t</th><th>best factor</th><th class="num">gate</th><th class="num">FDR pass</th></tr></thead><tbody>{top_rows}</tbody></table></div>
    </section>
    <section class="cd-sec" style="margin:0"><h3>protocol audit 失败 <span class="n">{len(audit_fail)}</span></h3>
      {audit_rows or _empty("没有审计失败。")}
      {f'<p class="mini">另有 {len(audit_fail) - 6} 个，见「迭代」视图的 audit 筛选。</p>' if len(audit_fail) > 6 else ""}
    </section>
  </div>
  <section class="cd-sec"><h3>六个问题 → 六个视图</h3><div class="grid g3">{question_cards}</div></section>
  <div class="callout" style="margin-top:14px"><b>快捷操作：</b>顶部标签切换视图；<code>/</code> 或 <code>Ctrl/K</code> 打开全局搜索；「迭代」视图里 <code>j</code>/<code>k</code> 上下切换 campaign；<code>复制链接</code> 得到可分享的 campaign 深链接；「打印」输出完整长文档。</div>
</section>
"""


def _evolution_view(campaigns: list[dict[str, Any]], base: Path) -> str:
    kind_counts = {k: sum(1 for c in campaigns if c.get("kind") == k) for k in ("research", "discovery", "ml", "calibration")}
    kind_chips = [("all", "全部", len(campaigns))]
    for key, label in (("research", "研究"), ("discovery", "Discovery"), ("ml", "ML"), ("calibration", "L0")):
        kind_chips.append((key, label, kind_counts.get(key, 0)))
    chips_html = "".join(
        f'<button class="chip" type="button" data-f-kind="{_e(key)}" aria-pressed="{"true" if key == "all" else "false"}">'
        f'{_e(label)}<b>{count}</b></button>'
        for key, label, count in kind_chips
    )
    rows = "".join(_campaign_row(c) for c in campaigns)
    details = []
    for index, campaign in enumerate(campaigns):
        prev_id = str(campaigns[index - 1].get("campaign_id")) if index > 0 else None
        next_id = str(campaigns[index + 1].get("campaign_id")) if index < len(campaigns) - 1 else None
        details.append(_campaign_detail(campaign, base, prev_id, next_id))
    return f"""
<section class="view" data-view="evolution" id="view-evolution" hidden>
  {_page_head("01 · 研究是如何迭代的",
              "左侧是按阶段排序的 campaign 列表，右侧是选中 campaign 的完整证据：轮次、候选、想法、agent、数据与产物。列表支持排序、筛选、搜索；点「复制链接」得到深链接。")}
  <div class="explorer" id="ev-explorer">
    <div class="rail">
      <div class="rail-head">
        <div class="rail-tools">
          <input class="search" id="ev-search" type="search" placeholder="搜索 campaign / 目标 / ID" aria-label="搜索 campaign">
          <select class="select" id="ev-sort" aria-label="排序">
            <option value="phase">时间顺序</option>
            <option value="date">日期</option>
            <option value="nett">best long-only t</option>
            <option value="gate">Gate pass</option>
            <option value="cands">候选数</option>
            <option value="rounds">轮次数</option>
            <option value="audit">审计失败优先</option>
          </select>
        </div>
        <div class="chips">{chips_html}</div>
        <div class="rail-tools">
          <select class="select" id="ev-run" aria-label="run 分组">
            <option value="all">全部 run</option>
            <option value="nine_hour_run">9h run</option>
            <option value="post_run_replication">post-run replication</option>
            <option value="legacy">legacy</option>
          </select>
          <select class="select" id="ev-audit" aria-label="protocol audit">
            <option value="all">全部 audit</option>
            <option value="ok">audit ok</option>
            <option value="fail">audit failed</option>
          </select>
        </div>
        <div class="chips">
          <label class="chk"><input type="checkbox" id="ev-team"> team 证据</label>
          <label class="chk"><input type="checkbox" id="ev-fdr"> FDR pass</label>
          <label class="chk"><input type="checkbox" id="ev-holdout"> holdout</label>
        </div>
        <div class="rail-count"><span id="ev-count">—</span><button class="linkbtn" type="button" id="ev-reset">重置筛选</button></div>
      </div>
      <div class="rail-list" id="ev-list" role="list">{rows}
        <div class="rail-empty" id="ev-none" hidden>没有匹配的 campaign；试试「重置筛选」。</div>
      </div>
    </div>
    <div class="detail" id="ev-detail">
      <div class="ev-back-wrap"><button class="btn" type="button" id="ev-back">← 返回列表</button></div>
      <div id="ev-missing" hidden style="padding:0 16px 20px"><div class="callout bad">没有找到该 campaign；可能链接有误或已被筛选隐藏。</div></div>
      {"".join(details)}
    </div>
  </div>
</section>
"""


def _ideas_view(campaigns: list[dict[str, Any]], base: Path) -> str:
    total = sum(len(c.get("ideas") or []) for c in campaigns)
    role_counts = {}
    for role, _label in ROLES:
        role_counts[role] = sum(
            1 for c in campaigns for idea in (c.get("ideas") or []) if idea.get("agent") == role
        )
    chips = [("all", "全部", total)] + [(role, label, role_counts.get(role, 0)) for role, label in ROLES]
    chips_html = "".join(
        f'<button class="chip" type="button" data-i-role="{_e(key)}" aria-pressed="{"true" if key == "all" else "false"}">'
        f'{_e(label)}<b>{count}</b></button>'
        for key, label, count in chips
    )
    cards = "".join(_idea_cards(c, base, compact=True) for c in campaigns)
    return f"""
<section class="view" data-view="ideas" id="view-ideas" hidden>
  {_page_head("02 · 想法是如何产生的",
              "PathFinder 问题 / Web Gateway 线索 / ML 计划 / pre-registration / FactorBuilder artifact；文本直接来自 team artifact，不做改写。按角色筛选或搜索，展开看全文。")}
  <div class="filters">
    <input class="search" id="idea-search" type="search" placeholder="搜索 idea 标题 / 明细 / campaign" aria-label="搜索 idea">
    <span class="chips">{chips_html}</span>
    <span class="count" id="idea-count">—</span>
  </div>
  <div class="ideas-head"><span>角色 / 类型</span><span>Campaign</span><span>Idea</span><span></span></div>
  <div class="ideas-list" id="idea-list">{cards or _empty("没有想法记录。")}</div>
</section>
"""


def _role_summary_cards(campaigns: list[dict[str, Any]]) -> str:
    out = []
    for role, label in ROLES:
        used = [c for c in campaigns if ((c.get("agents") or {}).get(role) or {}).get("used")]
        files = sum(len(((c.get("agents") or {}).get(role) or {}).get("files") or []) for c in used)
        tasks = sum(len(((c.get("agents") or {}).get(role) or {}).get("tasks") or []) for c in used)
        out.append(
            f'<div class="card"><b>{_e(label)}</b>'
            f'<p>{len(used)} campaigns · {tasks} tasks · {files} files</p>'
            f'<p class="mini">{_e(role)}</p></div>'
        )
    return '<div class="grid g5" style="margin-bottom:12px">' + "".join(out) + "</div>"


def _agents_view(campaigns: list[dict[str, Any]]) -> str:
    head = "<tr>" + _th("Campaign", "str") + "".join(
        _th(label, "num", "num", direction="") for _role, label in ROLES
    ) + "</tr>"
    body = []
    for campaign in campaigns:
        cells = []
        for role, _label in ROLES:
            evidence = (campaign.get("agents") or {}).get(role) or {}
            if not evidence.get("used"):
                cells.append(_td('<span class="na">—</span>', 0))
            else:
                status = evidence.get("status") or "used"
                tasks = evidence.get("tasks") or []
                tasks_text = ", ".join(str(t) for t in tasks[:3])
                extra = f'<span class="mini cell-tasks" title="{_e(tasks_text)}">{_e(tasks_text)}</span>' if tasks else ""
                cells.append(_td(f'<span class="tag ok">{_e(status)}</span>{extra}', 1, "cell-yes rolecell"))
        label = str(campaign.get("label") or "")
        cid = str(campaign.get("campaign_id") or "")
        id_note = f' <span class="mini">{_e(cid)}</span>' if label != cid else ""
        body.append(
            f'<tr data-team="{1 if campaign.get("has_team") else 0}">'
            + _td(f'<a href="#/evolution/{_e(cid)}">{_e(label)}</a>{id_note}')
            + "".join(cells)
            + "</tr>"
        )
    return f"""
<section class="view" data-view="agents" id="view-agents" hidden>
  {_page_head("03 · 调用了哪些 Agent",
              "role × campaign 矩阵；绿色格表示有 team artifact / round provenance / artifact spec 证据。表头可点击排序（按是否有证据）。")}
  {_role_summary_cards(campaigns)}
  <div class="filters">
    <label class="chk"><input type="checkbox" id="agent-team-only"> 仅显示有 team 目录的 campaign</label>
    <span class="count" id="agent-count">—</span>
  </div>
  <div class="tablewrap tall"><table class="matrix" data-sortable><thead>{head}</thead>
    <tbody id="agent-body">{"".join(body)}</tbody></table></div>
  <div class="callout warn" style="margin-top:12px"><b>注意：</b>— 表示没有留下 team 证据，不等于没有研究；新 campaign 必须记录 round agents/team_tasks 并通过 <code>alpha campaign-audit</code>。</div>
</section>
"""


def _results_table(campaigns: list[dict[str, Any]]) -> str:
    ranked = sorted(
        campaigns,
        key=lambda c: _raw_sort((c.get("best") or {}).get("long_only_t")),
        reverse=True,
    )
    body = []
    for campaign in ranked:
        metrics = (campaign.get("best") or {}).get("metrics") or {}
        rows = campaign.get("rounds") or []
        last_round = rows[-1] if rows else {}
        last_result = last_round.get("research_result") or {}
        long_only = last_round.get("long_only") or last_result.get("long_only") or {}
        size_screened = long_only.get("size_screened") or {}
        horizon = metrics.get("horizon") or last_result.get("horizon") or "—"
        audit = _audit_state(campaign)
        edge = campaign.get("edge_status") if isinstance(campaign.get("edge_status"), dict) else {}
        edge_text = " · ".join(f"{k} {v}" for k, v in edge.items()) or "—"
        body.append(
            f'<tr data-audit="{audit}">'
            + _td(f'<a href="#/evolution/{_e(campaign.get("campaign_id"))}">{_e(campaign.get("label"))}</a>'
                  f'<span class="mini" style="display:block">{_e(campaign.get("kind_label"))}</span>')
            + _td(_e(RUN_LABELS.get(str(campaign.get("run_group") or "legacy"), ("legacy", ""))[0]))
            + _td(_e(horizon))
            + _td(str(_int(campaign.get("n_rounds"))), _int(campaign.get("n_rounds")), "num")
            + _td(str(len(campaign.get("candidates") or [])), len(campaign.get("candidates") or []), "num")
            + _td(str(_int(campaign.get("gate_pass"))), _int(campaign.get("gate_pass")), "num")
            + _td(_num((campaign.get("best") or {}).get("long_only_t"), 3), _raw((campaign.get("best") or {}).get("long_only_t")), "num")
            + _td(_num(size_screened.get("long_only_net_excess_annualized"), 4),
                  _raw(size_screened.get("long_only_net_excess_annualized")), "num")
            + _td(_num(size_screened.get("long_only_t"), 3), _raw(size_screened.get("long_only_t")), "num")
            + _td(f'<code>{_e((campaign.get("best") or {}).get("factor_id") or "—")}</code>')
            + _td(str(len(campaign.get("fdr_pass") or [])), len(campaign.get("fdr_pass") or []), "num")
            + _td(_e(edge_text))
            + "</tr>"
        )
    head = (
        "<tr>"
        + _th("Campaign", "str")
        + _th("Run", "str")
        + _th("Horizon", "str")
        + _th("轮次", "num", "num")
        + _th("候选", "num", "num")
        + _th("Gate pass", "num", "num")
        + _th("best LO t", "num", "num", direction="desc")
        + _th("LO ann.", "num", "num")
        + _th("LO t", "num", "num")
        + _th("best factor", "str")
        + _th("FDR pass", "num", "num")
        + _th("Edge Cards", "str")
        + "</tr>"
    )
    return (
        '<div class="tablewrap tall"><table data-sortable><thead>'
        + head
        + "</thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _flat_candidates_table(campaigns: list[dict[str, Any]]) -> str:
    merged: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for campaign in campaigns:
        for row in campaign.get("candidates") or []:
            merged.append((campaign, row))
    merged.sort(
        key=lambda pair: _raw_sort(pair[1].get("long_only_t")),
        reverse=True,
    )
    if not merged:
        return _empty("没有独立 cycle candidate metrics。")
    body = []
    for campaign, row in merged:
        metrics = row.get("metrics") or {}
        factor_id = str(row.get("factor_id") or "")
        expression = str(row.get("expression") or "")
        fdr = row.get("program_fdr")
        if fdr is True:
            fdr_html = _tag("pass", "ok")
        elif fdr is False:
            fdr_html = _tag(f"fail p={_num(row.get('program_p'), 3)}", "bad")
        else:
            fdr_html = '<span class="na">—</span>'
        phase = _int(campaign.get("phase"))
        palette_sub = f'{campaign.get("label")} · {row.get("family") or ""} · LO t {_num(row.get("long_only_t"), 3)}'
        body.append(
            f'<tr data-factor="{_e(factor_id)}" data-verdict="{_e(row.get("verdict"))}"'
            f' data-nett="{_raw(metrics.get("net_t"))}"'
            f' data-search="{_search_text(campaign.get("label"), factor_id, row.get("family"), expression)}"'
            f' data-pal="candidate" data-pl-label="{_e(factor_id)}" data-pl-sub="{_e(palette_sub)}"'
            f' data-pal-href="#/evolution/{_e(campaign.get("campaign_id"))}">'            + _td(f'<span class="mini">P{phase:02d}</span> <a href="#/evolution/{_e(campaign.get("campaign_id"))}">{_e(campaign.get("label"))}</a>')
            + _td(f'<code>{_e(factor_id)}</code>')
            + _td(_e(row.get("family") or "—"))
            + _td(_verdict_tag(row.get("verdict")))
            + _td(_num(metrics.get("net_spread"), 4), _raw(metrics.get("net_spread")), "num")
            + _td(_num(row.get("long_only_t"), 3), _raw(row.get("long_only_t")), "num")
            + _td(_num(metrics.get("coverage"), 4), _raw(metrics.get("coverage")), "num")
            + _td(_num(row.get("p_one_sided_positive"), 4), _raw(row.get("p_one_sided_positive")), "num")
            + _td(_num(row.get("p_two_sided"), 4), _raw(row.get("p_two_sided")), "num")
            + _td(fdr_html)
            + _td(_e(row.get("round_id") or "—"))
            + _td(f'<code class="expr" title="{_e(expression)}">{_e(expression)}</code>' if expression else '<span class="na">—</span>')
            + "</tr>"
        )
    head = (
        "<tr>"
        + _th("Campaign", "str")
        + _th("Factor", "str")
        + _th("Family", "str")
        + _th("Gate", "str")
        + _th("net", "num", "num")
        + _th("LO t", "num", "num", direction="desc")
        + _th("coverage", "num", "num")
        + _th("p(pos)", "num", "num")
        + _th("p(two)", "num", "num")
        + _th("Program FDR", "str")
        + _th("Round", "str")
        + _th("表达式", "str")
        + "</tr>"
    )
    return (
        '<div class="tablewrap tall"><table data-sortable><thead>'
        + head
        + "</thead><tbody id=\"cand-body\">"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _ml_cards(campaigns: list[dict[str, Any]]) -> str:
    cards = []
    for campaign in campaigns:
        ml = campaign.get("ml_summary")
        if not ml:
            continue
        metrics = ml.get("metrics") or {}
        admission = ml.get("admission") or {}
        cards.append(
            f'<div class="card"><b>{_e(campaign.get("label"))} · ML</b>'
            f'<p>OOS rank IC {_num(metrics.get("oos_rank_ic_mean"), 4)} · t {_num(metrics.get("oos_rank_ic_t"), 3)} '
            f'· admission passed={_e(admission.get("passed"))} · blocked={_e(admission.get("blocked_reasons"))}</p>'
            + (f'<p>{_e(ml.get("conclusion"))}</p>' if ml.get("conclusion") else "")
            + "</div>"
        )
    return "".join(cards) or '<div class="card"><b>没有 ML summary</b><p>本 portfolio 没有 ML 研究分支记录。</p></div>'


def _results_view(portfolio: dict[str, Any], campaigns: list[dict[str, Any]]) -> str:
    fdr = portfolio.get("fdr_population") or {}
    kpis = portfolio.get("kpis") or {}
    verdict_chips = [("all", "全部"), ("pass", "pass"), ("fail", "fail")]
    chips_html = "".join(
        f'<button class="chip" type="button" data-c-verdict="{_e(key)}" aria-pressed="{"true" if key == "all" else "false"}">{_e(label)}</button>'
        for key, label in verdict_chips
    )
    return f"""
<section class="view" data-view="results" id="view-results" hidden>
  {_page_head("04 · 测试结果",
              "Gate / metrics / program FDR。表头可点击排序；候选表已把 73 个 campaign 的 125 个候选平铺，支持搜索与 Gate 筛选。")}
  <div class="grid g4" style="margin-bottom:12px">
    <div class="card"><b>全局 observed best</b><p>{_e(kpis.get("best_factor"))} · LS net_t (diagnostic) {_num(kpis.get("best_net_t"))}<br>dev evidence only；不是 freeze。</p></div>
    <div class="card"><b>Program FDR population</b><p>n_trials {_e(fdr.get("n_trials"))} · n_unique {_e(fdr.get("n_unique"))} · n_eff {_e(fdr.get("n_eff"))} · rank1 p {_e(fdr.get("rank1_p"))} · rank1 |t| {_e(fdr.get("rank1_t"))}</p></div>
    <div class="card"><b>FDR 纪律</b><p>program FDR pass={_e("yes" if kpis.get("fdr_any") else "no")} · killed cards={_e(kpis.get("killed"))} · validated/active={_e(kpis.get("validated"))} · unattributed={_e(kpis.get("unattributed_cards"))}</p></div>
    <div class="card"><b>Edge Cards</b><p>共 {_e(kpis.get("edge_cards"))}：{_e(kpis.get("killed"))} killed / {_e(kpis.get("research_cards"))} research / {_e(kpis.get("validated"))} validated</p></div>
  </div>
  <section class="cd-sec" style="margin-top:0"><h3>Campaign 汇总 <span class="n">按 best long-only t 排序</span></h3>{_results_table(campaigns)}</section>
  <section class="cd-sec"><h3>候选平铺表 <span class="n">{sum(len(c.get("candidates") or []) for c in campaigns)}</span></h3>
    <div class="filters">
      <input class="search" id="cand-search" type="search" placeholder="搜索 factor / family / campaign / 表达式" aria-label="搜索候选">
      <span class="chips">{chips_html}</span>
      <label class="chk"><input type="checkbox" id="cand-pos"> 仅 long-only t &gt; 0</label>
      <span class="count" id="cand-count">—</span>
    </div>
    {_flat_candidates_table(campaigns)}
  </section>
  <section class="cd-sec"><h3>ML 研究（独立分支）</h3><div class="grid g2">{_ml_cards(campaigns)}</div>
  <div class="callout warn" style="margin-top:10px"><b>为什么 ML 没有进入后续 factor 结果：</b>ML 先经过 pre-registered plan → admission；本次 P2 admission 未通过，且没有产生 distilled artifact，所以它没有进入后续 Edge Card / FDR / holdout 路径。这不是「ML 被禁用」，而是「没有合格 ML 实验进入 Factor Library」。</div></section>
</section>
"""


def _datasets_view(portfolio: dict[str, Any], campaigns: list[dict[str, Any]]) -> str:
    datasets = portfolio.get("datasets") or []
    axes = (portfolio.get("axes") or {}).get("fields") or {}
    usage = portfolio.get("axis_usage") or []
    holdout = portfolio.get("holdout") or {}
    kpis = portfolio.get("kpis") or {}
    labels = {str(c.get("campaign_id")): str(c.get("label") or c.get("campaign_id")) for c in campaigns}
    phases = {str(c.get("campaign_id")): _int(c.get("phase")) for c in campaigns}

    def campaigns_cell(ids: list[Any]) -> str:
        if not ids:
            return '<span class="na">—</span>'
        sample = [str(i) for i in ids[:3]]
        links = ", ".join(
            f'<a href="#/evolution/{_e(cid)}" title="{_e(labels.get(cid, cid))}">P{phases.get(cid, 0):02d}</a>'
            for cid in sample
        )
        more = f' <span class="mini">+{len(ids) - 3}</span>' if len(ids) > 3 else ""
        return f'<span class="mini">{len(ids)}:</span> {links}{more}'

    dataset_rows = "".join(
        f"<tr><td class='nw'>{_e(d.get('start'))}..{_e(d.get('end'))}</td>"
        f"<td class='nw'>{_e(d.get('window_type') or 'dev')}</td>"
        f"<td class='num'>{_e(d.get('codes'))}</td>"
        f"<td class='num'>{_e(d.get('rows'))}</td>"
        f"<td class='num'>{len(d.get('fields') or [])}</td>"
        f"<td>{_e(', '.join(d.get('services') or []))}</td>"
        f"<td>{campaigns_cell(d.get('campaigns') or [])}</td></tr>"
        for d in datasets
    )
    axis_rows = []
    for name, value in sorted(axes.items()):
        axis_rows.append(
            f'<tr class="axis-row" data-search="{_search_text(name, value.get("axis"), value.get("group"))}">'
            f'<td>{_tag(str(value.get("group", "")).upper(), "info")}</td>'
            f"<td><code>{_e(name)}</code></td><td>{_e(value.get('axis'))}</td>"
            f'<td class="num" data-v="{_raw(value.get("coverage"))}">{_num(value.get("coverage"), 4)}</td>'
            f'<td class="num" data-v="{_raw(value.get("nonzero"))}">{_num(value.get("nonzero"), 4)}</td>'
            f"</tr>"
        )
    usage_rows = "".join(
        f"<tr><td>{_tag(str(row.get('group', '')).upper(), 'info')}</td>"
        f"<td>{_e(row.get('label'))}</td>"
        f"<td class='num'>{len(row.get('fields') or [])}</td>"
        f"<td class='num'>{_e(row.get('candidates'))}</td>"
        f"<td>{_e(', '.join(row.get('campaigns') or [])) or '—'}</td></tr>"
        for row in usage
    )
    return f"""
<section class="view" data-view="datasets" id="view-datasets" hidden>
  {_page_head("05 · 用到了怎样的数据集",
              "来自 data_manifest / P0/P1 data axes / holdout policy；不推断未记录的数据。")}
  <div class="grid g4" style="margin-bottom:12px">
    <div class="card"><b>{len(datasets)}</b><p>唯一 dataset manifests（按窗口/代码数/字段数去重）</p></div>
    <div class="card"><b>{len(axes)}</b><p>P0/P1 数据轴字段覆盖记录</p></div>
    <div class="card"><b>{len(usage)}</b><p>candidate 实际使用到的数据轴</p></div>
    <div class="card"><b>{_e(kpis.get("historical_holdout_datasets"))}</b><p>历史 manifest 中的 non-dev window</p></div>
  </div>
  <section class="cd-sec" style="margin-top:0"><h3>唯一 dataset manifests</h3>
    <div class="tablewrap flat"><table data-sortable><thead><tr><th>Range</th><th>Window</th><th class="num">Codes</th><th class="num">Rows</th><th class="num">Fields</th><th>Services</th><th>Campaigns</th></tr></thead>
    <tbody>{dataset_rows or '<tr><td colspan="7" class="na">没有 data_manifest。</td></tr>'}</tbody></table></div>
  </section>
  <section class="cd-sec"><h3>P0/P1 数据轴覆盖（dev panel） <span class="n">{len(axes)}</span></h3>
    <div class="filters"><input class="search" id="axis-search" type="search" placeholder="搜索 field / axis / group" aria-label="搜索数据轴"><span class="count" id="axis-count">—</span></div>
    <div class="tablewrap tall"><table data-sortable><thead><tr><th>Group</th><th>Field</th><th>Axis</th><th class="num">Coverage</th><th class="num">Nonzero</th></tr></thead>
    <tbody>{''.join(axis_rows) or '<tr><td colspan="5" class="na">没有 P0/P1 数据轴覆盖文件。</td></tr>'}</tbody></table></div>
  </section>
  <section class="cd-sec"><h3>数据轴在 candidate 中的实际使用</h3>
    {('<div class="tablewrap flat"><table data-sortable><thead><tr><th>Group</th><th>Axis</th><th class="num">Fields</th><th class="num">Candidate uses</th><th>Campaigns</th></tr></thead><tbody>' + usage_rows + '</tbody></table></div>') if usage_rows else _empty("没有 candidate expression 可映射到数据轴。")}
  </section>
  <div class="callout warn"><b>使用 vs 可用：</b>P0/P1 字段进入了 Panel/AlphaContext；默认 <code>alpha search</code> 的字段池现已包含这些轴。campaign 中是否使用，要看 candidate expression 的字段；这张表是实际使用证据，不是可用性声明。</div>
  <div class="callout"><b>Holdout policy：</b>{_e(holdout)}</div>
</section>
"""


def _conclusions_view(portfolio: dict[str, Any], campaigns: list[dict[str, Any]]) -> str:
    cards = "".join(
        f'<div class="card"><b>{_e(item.get("title"))}</b><p>{_e(item.get("body"))}</p></div>'
        for item in portfolio.get("portfolio_conclusions") or []
    )
    body = []
    for campaign in campaigns:
        if campaign.get("kind") == "calibration":
            continue
        body.append(
            f'<tr class="concl-row" data-search="{_search_text(campaign.get("label"), campaign.get("conclusion"), campaign.get("next_step"), campaign.get("campaign_id"))}">'
            + _td(f'<a href="#/evolution/{_e(campaign.get("campaign_id"))}">{_e(campaign.get("label"))}</a>'
                  f'<span class="mini" style="display:block">{_e(campaign.get("kind_label"))}</span>', None, "c-col")
            + _td(_e(campaign.get("conclusion")))
            + _td(_e(campaign.get("next_step") or "—"))
            + "</tr>"
        )
    return f"""
<section class="view" data-view="conclusions" id="view-conclusions" hidden>
  {_page_head("06 · 结论",
              "全局结论由结构化状态生成；campaign 结论来自 stop/summary round 或 team artifact。")}
  <div class="grid g2" style="margin-bottom:14px">{cards}</div>
  <div class="filters"><input class="search" id="concl-search" type="search" placeholder="搜索 campaign / 结论 / next step" aria-label="搜索结论"><span class="count" id="concl-count">—</span></div>
  <div class="tablewrap tall"><table><thead><tr><th class="c-col">Campaign</th><th>结论</th><th>下一步</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>
</section>
"""


def _method_view() -> str:
    return """
<section class="view" data-view="method" id="view-method" hidden>
  <div class="pagehead"><div><h1>方法与边界</h1>
  <p class="sub">这份 atlas 是什么、怎么生成、哪些不能从里面推理。</p></div></div>
  <div class="card" style="max-width:980px">
    <ul style="margin:4px 0 0;padding-left:20px;line-height:1.85">
      <li>只读聚合 campaign rounds、team/、memory、families、factor library、probes、data_axes、holdout policy；不读取市场数据、不运行 gate/search。</li>
      <li>campaign 按 created_at 排序（phase 编号即此顺序）；round metrics / decision 是原始记录，不重新解释。</li>
      <li>文本直接来自结构化 artifact；损坏的历史文本只标记，不猜测补全。</li>
      <li>Gate pass / long-only t 不是 freeze 或 allocation；holdout 保持锁定。</li>
      <li>把全部内容展开为一页：右上角「长文档」；打印 / 导出 PDF：右上角「打印」（打印样式总会展开全部视图与全部 campaign 详情）。</li>
    </ul>
    <p class="mini" style="margin-top:12px">重新生成：<code>python -m factor_lab alpha report</code>（同时写出 <code>research_portfolio.json</code>）。</p>
  </div>
</section>
"""


def render_portfolio(portfolio: dict[str, Any]) -> str:
    base = Path(portfolio.get("base_dir") or "output/deliverables")
    campaigns = sorted(
        portfolio.get("campaigns") or [],
        key=lambda c: (_int(c.get("phase")), str(c.get("created_at") or "")),
    )
    kpis = portfolio.get("kpis") or {}
    ideas_total = sum(len(c.get("ideas") or []) for c in campaigns)
    candidates_total = sum(len(c.get("candidates") or []) for c in campaigns)
    axes_total = len((portfolio.get("axes") or {}).get("fields") or {})
    meaningful_total = sum(1 for c in campaigns if c.get("kind") != "calibration")
    tab_counts = {
        "evolution": len(campaigns),
        "ideas": ideas_total,
        "agents": len(ROLES),
        "results": candidates_total,
        "datasets": axes_total,
        "conclusions": meaningful_total,
    }

    def tab(view: str, label: str, count_key: str = "") -> str:
        count = tab_counts.get(count_key or view, 0)
        badge = f'<span class="tc">{count}</span>' if count_key or view in tab_counts else ""
        return (
            f'<a class="tab" href="#/{view}" data-view="{view}" role="tab">'
            f"{_e(label)}{badge}</a>"
        )

    tabs = (
        tab("overview", "概览")
        + tab("evolution", "迭代", "evolution")
        + tab("ideas", "想法", "ideas")
        + tab("agents", "Agent", "agents")
        + tab("results", "结果", "results")
        + tab("datasets", "数据", "datasets")
        + tab("conclusions", "结论", "conclusions")
        + tab("method", "方法")
    )
    views = (
        _overview_view(portfolio, campaigns, kpis, base)
        + _evolution_view(campaigns, base)
        + _ideas_view(campaigns, base)
        + _agents_view(campaigns)
        + _results_view(portfolio, campaigns)
        + _datasets_view(portfolio, campaigns)
        + _conclusions_view(portfolio, campaigns)
        + _method_view()
    )
    shell = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Factor Lab · Research Atlas</title>
<style>{CSS}</style>
</head><body data-view="overview">
<header class="topbar">
  <div class="brand"><b>Research Atlas</b><span>{_e(kpis.get("campaigns"))} campaigns · {_e(kpis.get("attempts"))} attempts · git {_e(str(portfolio.get("git_commit") or "unknown")[:8])}</span></div>
  <nav class="tabs" aria-label="视图切换">{tabs}</nav>
  <div class="tools">
    <button class="btn" type="button" id="btn-pal" title="全局搜索（/ 或 Ctrl+K）">搜索 <span class="kbd">/</span></button>
    <button class="btn ghost" type="button" id="btn-dense" title="紧凑 / 舒适切换">紧凑</button>
    <button class="btn ghost" type="button" id="btn-long" title="把全部视图与全部 campaign 展开成一页">长文档</button>
    <button class="btn ghost" type="button" id="btn-print" title="打印 / 导出 PDF">打印</button>
  </div>
</header>
<main>{views}</main>
<div class="pal-back" id="pal-back" hidden>
  <div class="palette" role="dialog" aria-modal="true" aria-label="全局搜索">
    <div class="inrow"><input id="pal-input" type="search" placeholder="搜索 campaign / factor / idea / dataset / field" autocomplete="off" spellcheck="false"></div>
    <div class="res" id="pal-res" role="listbox"></div>
    <div class="pal-foot"><span><span class="kbd">↑</span><span class="kbd">↓</span> 选择</span><span><span class="kbd">Enter</span> 打开</span><span><span class="kbd">Esc</span> 关闭</span></div>
  </div>
</div>
<noscript><style>
.view[hidden]{{display:block!important}}.detail{{display:block!important}}
.camp-detail[hidden]{{display:block!important}}.rail{{display:none}}
.explorer{{display:block!important}}.filters,.chips{{display:none}}
</style></noscript>
<div id="toast" hidden></div>
<script>{JS}</script>
</body></html>"""
    return shell

def write_portfolio_report(
    out: str | Path | None = None,
    *,
    root: str | Path | None = None,
    json_out: str | Path | None = None,
) -> dict[str, Any]:
    lab_root = Path(root) if root is not None else Path(config.LAB_ROOT)
    target = Path(out) if out is not None else lab_root.parent / "deliverables" / "research_portfolio.html"
    json_target = Path(json_out) if json_out is not None else target.with_suffix(".json")
    portfolio = build_portfolio(lab_root, base_dir=target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_portfolio(portfolio), encoding="utf-8")

    def _jsonable(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return value

    json_target.write_text(
        json.dumps(
            _jsonable({k: v for k, v in portfolio.items() if k != "base_dir"}),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {
        "schema": "factor_lab.alpha.research_portfolio_output.v1",
        "ok": True,
        "path": str(target),
        "json_path": str(json_target),
        "n_campaigns": len(portfolio.get("campaigns") or []),
        "generated_at": portfolio.get("generated_at"),
    }
