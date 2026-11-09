// FactorBuilder: the child inherits tools and may read existing factors or
// write generated factor files into the Lab library. Python `factor-build`
// remains the authoritative writer/validator if the child only returns code.
const schema = {
  type: "object",
  properties: {
    family: { type: "string" },
    factor_id: { type: "string" },
    version: { type: "string" },
    code: { type: "string" },
    spec: { type: "object" },
    tests: { type: "string" },
    written_path: { type: "string" },
  },
  required: ["family", "factor_id", "version", "code", "spec"],
  additionalProperties: false,
};
phase("factor_builder");
const context = String(args.context || "");
const factor = await agent(
  `你是 Factor Lab 的 FactorBuilder。你可以使用 read / glob / grep / write 工具。
先把 factor idea 变成可执行 factor：
- 可读取 factor_lab/ 下已有因子、spec、测试，避免重复；
- 可写入 factor_lab/factors/generated/<family>/<factor_id>/<version>/ 下的
  factor.py / spec.json / test_factor.py / manifest.json；
- 若你不写文件，只在 JSON 返回 code/spec/tests，由 Python factor-build 落库。
约束：
- 只写 Lab factor library，不碰生产 factors/registry；
- 因子函数签名 factor(frame: pd.DataFrame) -> pd.Series；
- 允许 pandas/numpy；代码必须能 compile；
- 不取数、不评估；不生成 Deep Learning。
上下文：
${context}`,
  { schema, label: "FactorBuilder", phase: "factor_builder" }
);
return factor;
