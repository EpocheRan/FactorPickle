// DSH LLM Gateway summarizer. Web search itself is performed by the DSH
// session's web_search tool; do not use tools in this child. Pass args.context
// containing the research question, dead ends and raw search results.
const schema = {
  type: "object",
  properties: {
    summary: { type: "string" },
    gaps: { type: "array", items: { type: "string" } },
    leads: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string" },
          mechanism: { type: "string" },
          tags: { type: "array", items: { type: "string" } },
          confidence: { type: "number" },
          sources: {
            type: "array",
            items: {
              type: "object",
              properties: {
                title: { type: "string" },
                url: { type: "string" },
              },
              required: ["url"],
              additionalProperties: false,
            },
          },
        },
        required: ["title", "mechanism", "sources"],
        additionalProperties: false,
      },
    },
  },
  required: ["summary", "leads"],
  additionalProperties: false,
};

phase("gateway");
const context = String(args.context || "");
const digest = await agent(
  `你是 Factor Lab 的 Web Gateway LLM。不要使用任何工具。你只负责把 DSH 已经检索到的网页结果总结成可引用的机制线索。
要求：
- 不编造来源；每个 lead 必须带 sources.url；
- 区分事实与推测；给 confidence 0-1；
- 标记 gaps（没有证据的方向）；
- 不做因子/模型判断，只提供机制与数据线索。
上下文：
${context}`,
  { schema, label: "Web Gateway", phase: "gateway" }
);
return digest;
