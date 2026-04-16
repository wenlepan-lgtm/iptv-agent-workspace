# CLAUDE_CODEX_README.md

## 1. 先读什么

Claude Code 和 Codex 在接手本项目时，必须先按以下顺序读取：

1. `AGENT.md`
2. `project-docs/00_project/project_brief.md`
3. `project-docs/00_project/architecture_baseline.md`
4. `project-docs/00_project/acceptance_baseline.md`
5. `project-docs/00_project/known_issues.md`
6. `project-docs/05_index/TASK_INDEX.md`
7. 本轮对应的 `TASK-xxxx.md`
8. 如存在，再读对应的 `DEVLOG-xxxx.md` / `REVIEW-xxxx.md` / `VALIDATION-xxxx.md`

## 2. 各自做什么

### Claude Code
- 负责开发
- 负责更新任务卡状态
- 负责写开发记录
- 若任务要求验证，负责写验证记录
- 不得宣布审核通过
- 不得修改审核记录

### Codex
- 负责审核
- 负责写审核记录
- 负责更新任务索引中的审核结果状态
- 不得把开发记录当作事实而不核对代码
- 不得跳过问题分级

## 3. 共同约定

- 不以聊天历史作为最终事实源
- 以仓库文档为准
- 一任务一编号
- 同编号对应一组文档
- 不允许伪验证
- 不允许伪完成
- 不允许模糊结论

## 4. 工作流程

1. 新建任务卡
2. Claude 开发
3. Claude 写 DEVLOG
4. Claude 更新任务状态为 `DEV_DONE`
5. Codex 审核
6. Codex 写 REVIEW
7. Codex 更新任务状态为 `APPROVED` 或 `CHANGES_REQUIRED`
8. 若任务要求验证，则补 VALIDATION 并更新为 `VERIFIED`
9. Ala 拍板关闭任务为 `CLOSED`

## 5. 必须落盘的文件

### 项目级长期文档
- `AGENT.md`
- `project-docs/00_project/project_brief.md`
- `project-docs/00_project/architecture_baseline.md`
- `project-docs/00_project/acceptance_baseline.md`
- `project-docs/00_project/known_issues.md`
- `project-docs/05_index/TASK_INDEX.md`

### 每个任务必须对应
- `project-docs/01_tasks/TASK-xxxx.md`
- `project-docs/02_dev_logs/DEVLOG-xxxx.md`
- `project-docs/03_review_logs/REVIEW-xxxx.md`
- `project-docs/04_validation/VALIDATION-xxxx.md`（如需要）

## 6. 结论格式要求

### Claude
必须明确写出：
- 做了什么
- 没做什么
- 改了哪些文件
- 怎么验证
- 验证结果
- 风险是什么

### Codex
必须明确写出：
- 审核范围
- 审核结论
- 风险等级
- 通过项
- 问题项
- 是否允许进入下一步

## 7. 一句总规则

**聊天只是入口，仓库文档才是事实。**
