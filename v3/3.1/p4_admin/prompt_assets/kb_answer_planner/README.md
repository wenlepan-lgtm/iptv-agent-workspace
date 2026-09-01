# KB Answer Planner Prompt Asset

`v1.json` 是 2026-09-01 在候选链路通过固定语义测试的酒店知识回答规划 Prompt 资产。

它不是酒店知识格式化 Skill，也不允许模型自由编写酒店答案。运行时只允许模型从本次请求提供的已发布事实卡中选择 `cards` 和 `intent`；最终文字由 Gateway 使用已发布字段和回答变体确定性生成。无可靠事实、模型超时、结构无效或 Safety 不通过时使用固定安全话术。

当前代码消费者为 `p4_admin/v3_candidate_backend.py` 中的 `_KB_PLAN_SYS_ZH`、`_KB_PLAN_SYS_EN`、`_kb_plan_prompt`、`_kb_parse_plan` 和 `_kb_compose_plan`。在后台模型管理完成 Prompt Bundle 接线前，修改本资产时必须同步修改代码常量并保持内容一致。

后续每次 LLM 模型升级都必须把模型制品、Runtime 版本、该 Prompt 资产、输出 Schema、生成参数和固定语义测试视为一个不可拆分的发布单元；验证通过后一起应用，失败时一起回滚。酒店角色只能查看生效版本，不能覆盖平台核心 Prompt。

本目录禁止保存真实酒店资料、凭据、API Key 或测试服务器密码。
