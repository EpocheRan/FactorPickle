// Working DSH Director pattern: no-tool, self-contained prompt.
// Pass `args.context` with the latest cycle metrics; Python applies the memo.

const schema = {
  type: "object",
  properties: {
    decision: { type: "string", enum: ["continue", "change_bet", "stop"] },
    rationale: { type: "string" },
    next_bet: { type: "string" },
    next_action: { type: "string" },
    discovery_verdict: {
      type: "string",
      enum: ["transmission_gap", "weak", "no_edge", "discovery"],
    },
  },
  required: ["decision", "rationale", "next_bet", "next_action", "discovery_verdict"],
  additionalProperties: false,
};

phase("director");
const context = String(args.context || "");
const memo = await agent(
  `你是 Factor Lab 的 Director。不要使用任何工具，直接基于以下信息判断并返回 JSON。\n\n${context}\n\n请给出 decision（continue/change_bet/stop）、中文 rationale、next_bet、next_action、discovery_verdict。`,
  { schema, label: "Director", phase: "director" }
);
return memo;
