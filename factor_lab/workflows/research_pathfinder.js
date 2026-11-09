// PathFinder round contract. DSH subagents inherit the parent composition by
// default, so this agent may use read / glob / grep / web_search tools to read
// research history, discovery maps and campaign traces. It must not run
// experiments or modify code; the Python campaign runtime owns lifecycle,
// budget and stop rules.
const schema = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["factor", "ml", "stop"] },
    reason: { type: "string" },
    escape_mode: { type: "string", enum: ["exploit", "explore", "wildcard", "red_team"] },
    novelty_vector: { type: "array", items: { type: "string" } },
    factor_idea: { type: "object" },
    ml_idea: { type: "object" },
  },
  required: ["action", "reason"],
  additionalProperties: false,
};

phase("pathfinder");
const context = String(args.context || "");
const proposal = await agent(
  `你是 Factor Lab 的 PathFinder。你可以使用工具（read / glob / grep / web_search）读取当前研究状态：
- output/factor_lab/research_history.jsonl
- output/factor_lab/discovery/discovery_map.json
- output/factor_lab/campaigns/
- 需要外部机制时可用 web_search 检索，但必须保留来源 URL。
不要运行研究、不要修改代码、不要写实验产物；你只提出下一轮动作。

路径阅读顺序：History → discovery_map → 上一轮 ML result；如果没有文件，再使用下面内联上下文。
禁止 Deep Learning。factor/ml 动作必须带 escape_mode 和 novelty_vector。
上下文：
${context}
返回 JSON；证据不足或达到停止条件时 action=stop 并说明 reason。`,
  { schema, label: "PathFinder", phase: "pathfinder" }
);
return proposal;
