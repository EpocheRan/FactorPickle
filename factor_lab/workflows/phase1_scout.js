// Working DSH Scout pattern: no-tool, self-contained prompt.
// Tool-using subagents were observed to hang; the Python Runner is kept outside.
// Pass `args.context` with the current metrics and fresh-factor list.

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
  `你是 Factor Lab 的 Scout agent。不要使用任何工具，直接基于以下信息判断并返回 JSON。\n\n${context}\n\n请选择 2-4 个新因子，给出 family_id、中文 rationale、next_bet、discovery_verdict。`,
  { schema, label: "Scout", phase: "scout" }
);
return plan;
