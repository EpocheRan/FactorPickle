// Phase 1 Scout. DSH subagents inherit the parent composition by default, so
// this child may use read / glob / grep to inspect phase1 artifacts. Context is
// still passed inline for a self-contained decision; tool use is optional.
const schema = {
  type: "object",
  properties: {
    selected: { type: "array", items: { type: "string" } },
    family_id: { type: "string" },
    rationale: { type: "string" },
    next_bet: { type: "string" },
    discovery_verdict: {
      type: "string",
      enum: ["transmission_gap", "weak", "no_edge", "discovery"],
    },
  },
  required: ["selected", "family_id", "rationale", "next_bet", "discovery_verdict"],
  additionalProperties: false,
};

phase("scout");
const context = String(args.context || "");
const plan = await agent(
  `你是 Factor Lab 的 Scout agent。你可以使用 read / glob / grep 读取
output/factor_lab/phase1、experiments、research_history 等产物；如果上下文已经足够，也可以直接判断。
不要运行实验、不要修改代码。
基于已有结果和 fresh catalog 选择 2–4 个新因子，返回 JSON：
${context}`,
  { schema, label: "Scout", phase: "scout" }
);
return plan;
