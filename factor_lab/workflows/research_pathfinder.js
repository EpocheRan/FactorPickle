// PathFinder round contract. The Python campaign runtime owns rounds,
// budgets, duplicate detection and stop rules; this agent only proposes.
// Pass args.context with History, discovery_map, previous ML results, and
// campaign limits. Do not use tools.

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
  `你是 Factor Lab 的 PathFinder。不要使用任何工具，只根据下面的上下文提出下一轮研究动作。
禁止 Deep Learning。必须带 escape_mode 和 novelty_vector（只对 factor/ml 动作）。
上下文：
${context}
返回 JSON；如果当前证据不足或已经达到停止条件，action=stop 并说明 reason。`,
  { schema, label: "PathFinder", phase: "pathfinder" }
);
return proposal;
