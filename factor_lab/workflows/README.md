# Factor Lab research roles

Factor Lab 的研究角色由 DSH durable teammates 承担：

- `pathfinder`：读 Research Context / discovery / campaigns，提出一轮动作。
- `web-gateway`：执行外部检索并返回带 URL 的 digest。
- `factor-builder`：生成并写入 Lab 因子版本；Python `factor-build` 负责校验。
- `runner`：选择 codes/窗口，经 DataInterfaceGateway 执行一次预注册实验。
- `evaluator`：解释评级、审计和口径一致性，给出 caveats。
- `ml-planner`：输出非 Deep Learning 的实验 spec。
- `ml-monitor`：检查 leakage、over-search、成本与可解释性。
- `supervisor`：审计预算、provenance、Gate 与 agent 行为。

Gate Service、DataInterfaceGateway、Research Module/Adapter 是确定性服务，不是 teammate。

Lead 通过 `send_message` 把任务派给 durable teammate；Python runtime 负责数据访问、执行、Gate、预算和 reclaim。

不要 promote、不要绕过 Gate、不要新增直连 QuestDB/SQL 的路径。
