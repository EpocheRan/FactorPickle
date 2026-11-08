// ML Engine planner contract. The deterministic runtime executes the spec.
// No deep learning; no tools.
const schema = {
  type: "object",
  properties: {
    name: { type: "string" },
    features: { type: "array", items: { type: "string" } },
    model: {
      type: "string",
      enum: ["ridge", "lasso", "elasticnet", "random_forest", "extra_trees", "hist_gradient_boosting"],
    },
    target: { type: "string" },
    n_splits: { type: "integer" },
    embargo: { type: "integer" },
    min_train_dates: { type: "integer" },
    cost_bps: { type: "number" },
  },
  required: ["name", "features", "model", "target"],
  additionalProperties: false,
};
phase("ml_planner");
const context = String(args.context || "");
const spec = await agent(
  `你是 Factor Lab 的 ML Engine planner。不要使用任何工具。只允许非 Deep Learning 模型：
  ridge/lasso/elasticnet/random_forest/extra_trees/hist_gradient_boosting。
  根据下面的 campaign 结果与数据契约，选择一个下一轮 ML 实验 spec，返回 JSON。
  禁止 MLP/CNN/RNN/Transformer。
  上下文：
  ${context}`,
  { schema, label: "ML Planner", phase: "ml_planner" }
);
return spec;
