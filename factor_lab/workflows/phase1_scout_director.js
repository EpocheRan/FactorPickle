// DSH workflow: Scout -> Runner -> Director for one Phase 1 cycle.
// Run this as the `script` argument of the DSH workflow tool.
// The workflow script has no filesystem; the agents do the I/O.

const planSchema = {
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

const runSchema = {
  type: "object",
  properties: {
    status: { type: "string" },
    cycle_id: { type: "string" },
    stdout_tail: { type: "string" },
  },
  required: ["status", "cycle_id", "stdout_tail"],
  additionalProperties: false,
};

const memoSchema = {
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

phase("scout");
const plan = await agent(
  `You are the Scout agent for Factor Lab Phase 1.
Read D:\\FinBridge\\output\\factor_lab\\phase1\\phase1_report.json and D:\\FinBridge\\factor_lab\\cycle_ideas.py.
The 4 completed cycles have 16 factors, 0 G3 pass, 1 iterate, 3 high-IC-negative-spread, 3 G0.5 failures.
The only fresh factors left are:
- risk.vol_of_vol_120d
- risk.high_low_ratio_60d
Choose 1-2 of these for one final exploratory cycle. Prefer a defensible reason. Return the requested JSON only.`,
  { schema: planSchema, label: "Scout", phase: "scout" }
);

phase("run");
const run = await agent(
  `You are the Runner agent. Use the write tool to write exactly this JSON to
D:\\FinBridge\\output\\factor_lab\\phase1\\agent_plan.json :
${JSON.stringify(plan)}

Then run this PowerShell command:
python -m factor_lab phase1-agent --plan D:\\FinBridge\\output\\factor_lab\\phase1\\agent_plan.json --heartbeat-interval 2

Return status, cycle_id, and a short stdout tail. Do not invent a cycle_id; read it from the command output or D:\\FinBridge\\output\\factor_lab\\phase1\\agent_cycle.json.`,
  { schema: runSchema, label: "Runner", phase: "run" }
);

phase("director");
const memo = await agent(
  `You are the Director agent. Read D:\\FinBridge\\output\\factor_lab\\phase1\\agent_cycle.json and
D:\\FinBridge\\output\\factor_lab\\phase1\\phase1_report.json.
The run status is ${run.status}, cycle ${run.cycle_id}.
Decide whether to continue, change bet, or stop. Write a concise Chinese rationale and one next action.
Return the requested JSON only.`,
  { schema: memoSchema, label: "Director", phase: "director" }
);

return { plan, run, memo };
