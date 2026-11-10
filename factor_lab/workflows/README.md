# Factor Lab research roles

Factor Lab 的研究角色由 DSH durable teammates 承担：

- `pathfinder`：读 History / discovery map / campaigns，提出一轮动作。
- `web-gateway`：执行外部检索并返回带 URL 的 digest。
- `ml-planner`：输出非 Deep Learning 的实验 spec。
- `factor-builder`：生成并写入 Lab 因子版本；Python `factor-build` 负责校验。
- `supervisor`：审计预算、provenance、Gate 与 agent 行为。

本目录的 JS 文件只定义角色输出契约，不承担执行调度。Lead 通过 `send_message`
把任务派给 durable teammate；Python runtime 负责数据访问、执行、Gate、预算和 reclaim。

不要 promote、不要绕过 Gate、不要新增直连 QuestDB/SQL 的路径。
