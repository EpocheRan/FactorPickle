// FactorBuilder contract: generate factor code + spec + tests, but do not run
// or fetch data. The Python factor-build command writes it into the Lab library.
// No deep-learning / no tools.
const schema = {
  type: "object",
  properties: {
    family: { type: "string" },
    factor_id: { type: "string" },
    version: { type: "string" },
    code: { type: "string" },
    spec: { type: "object" },
    tests: { type: "string" },
  },
  required: ["family", "factor_id", "version", "code", "spec"],
  additionalProperties: false,
};
phase("factor_builder");
const context = String(args.context || "");
const factor = await agent(
  `你是 Factor Lab 的 FactorBuilder。不要使用任何工具。根据下面的 factor idea 和数据契约，
生成可直接写入 Lab factor library 的 Python factor、spec.json 与（可选）test_factor.py。
约束：
- 只写 Lab factor library，不碰生产 registry；
- 因子函数签名 factor(frame: pd.DataFrame) -> pd.Series；
- 允许 pandas/numpy；代码必须能通过 compile；
- 不取数、不加载 panel、不评估；
- 不生成 Deep Learning。
返回 JSON。
上下文：
${context}`,
  { schema, label: "FactorBuilder", phase: "factor_builder" }
);
return factor;
