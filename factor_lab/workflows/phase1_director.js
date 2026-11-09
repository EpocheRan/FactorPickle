// Phase 1 Director. DSH subagents inherit the parent composition by default, so
// this child may use read / glob / grep to inspect the latest artifacts; the
// inline context is kept for deterministic, self-contained decisions.
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
  `你是 Factor Lab 的 Director。你可以使用 read / glob / grep 读取最新的
decision_brief、audit、research_queue、research_history；如果上下文足够，也可直接判断。
不要运行实验、不要修改代码、不要改 Gate。
基于以下信息给出 JSON：
${context}`,
  { schema, label: "Director", phase: "director" }
);
return memo;
