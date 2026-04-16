# AGENT.md

## 1. 目的

本文件是本项目中 **Claude Code（开发）** 与 **Codex（审核）** 的统一协作协议。  
目标不是共享聊天记忆，而是共享同一套仓库内的事实、任务、记录、状态与验收口径。

---

## 2. 项目根路径

本项目唯一工作目录固定为：

`/Users/ala/工作项目/agent`

所有任务文档、开发记录、审核记录、验证记录，均必须存放在该目录内指定位置。  
禁止在其他路径创建影子版本。  
禁止把任务状态只保留在聊天中而不落盘。

---

## 3. 核心原则

### 3.1 仓库文档是真相源
Claude 和 Codex 不得把聊天内容视为最终依据。最终依据只能是仓库内以下内容：

- 当前代码
- 当前任务卡
- 当前开发记录
- 当前审核记录
- 当前验证记录
- 当前项目基线文档

### 3.2 开发与审核分离
- **Claude Code** 负责开发，不负责宣布“审核通过”
- **Codex** 负责审核，不负责擅自扩大开发范围
- **Ala** 负责最终决策、优先级与是否采纳

### 3.3 必须落盘
任何一次有效开发或审核，必须形成文档记录。没有落盘，等于没做完。

### 3.4 小步快跑，禁止扩散
每个任务都必须有明确边界。禁止为了“顺手优化”而无边界重构。禁止把一个任务偷偷扩展成多个任务。

### 3.5 审核通过不等于可交付
代码审核通过，仅代表实现基本符合要求。若任务要求包含运行验证、设备验证、ADB 安装验证，则必须继续完成对应验证并落盘。

---

## 4. 角色定义

### 4.1 Claude Code（开发角色）
职责：
1. 读取任务文档与项目基线文档
2. 理解目标、约束、涉及文件、验收标准
3. 在允许范围内实施代码修改
4. 创建并更新开发记录
5. 在任务完成后更新任务状态
6. 明确写出本次改动的风险、未完成项、验证方式

不得：
- 自行宣布审核通过
- 省略开发记录
- 假装已经验证但没有证据
- 擅自重构未授权模块
- 修改审核记录

### 4.2 Codex（审核角色）
职责：
1. 读取任务文档
2. 读取开发记录
3. 读取相关代码与 diff
4. 审核“需求—实现—记录”三者是否一致
5. 创建并更新审核记录
6. 给出通过、退回修改、风险等级、必改项、建议项

不得：
- 把开发记录当作事实而不核对代码
- 只给泛泛意见，不落到文件和问题点
- 擅自修改开发记录
- 未经明确授权直接重写大量代码
- 越过任务边界提出无关的大重构要求

### 4.3 Ala（负责人）
负责：
- 提出任务目标
- 拍板优先级
- 决定是否采纳审核意见
- 决定是否继续下一轮开发
- 决定是否进入真实环境、设备、ADB 验证
- 决定是否合并/发布

---

## 5. 目录结构约定

```text
/Users/ala/工作项目/agent
  ├── AGENT.md
  ├── CLAUDE_CODEX_README.md
  ├── project-docs
  │   ├── 00_project
  │   │   ├── project_brief.md
  │   │   ├── architecture_baseline.md
  │   │   ├── acceptance_baseline.md
  │   │   └── known_issues.md
  │   ├── 01_tasks
  │   │   ├── TASK-YYYYMMDD-001.md
  │   │   └── ...
  │   ├── 02_dev_logs
  │   │   ├── DEVLOG-YYYYMMDD-001.md
  │   │   └── ...
  │   ├── 03_review_logs
  │   │   ├── REVIEW-YYYYMMDD-001.md
  │   │   └── ...
  │   ├── 04_validation
  │   │   ├── VALIDATION-YYYYMMDD-001.md
  │   │   ├── adb_logs
  │   │   ├── runtime_logs
  │   │   └── screenshots
  │   └── 05_index
  │       └── TASK_INDEX.md
  └── scripts
      └── taskctl.py
```

---

## 6. 必须存在的基线文档

### `project-docs/00_project/project_brief.md`
记录项目简述，包括项目目标、平台与环境、主要模块、当前技术路线、不允许违反的长期约束、当前协作方式。

### `project-docs/00_project/architecture_baseline.md`
记录当前认可的架构基线，包括模块边界、主链路、关键接口、状态机、日志规范、不允许破坏的架构约束。

### `project-docs/00_project/acceptance_baseline.md`
记录统一验收标准，包括编译、功能、日志、性能、风险、设备验证通过条件。

### `project-docs/00_project/known_issues.md`
记录当前明确存在的问题、临时绕过方案、不要重复修的旧问题、尚未排期但必须记住的风险。

---

## 7. Claude 必须创建和维护的文档

- `project-docs/01_tasks/TASK-YYYYMMDD-XXX.md`
- `project-docs/02_dev_logs/DEVLOG-YYYYMMDD-XXX.md`
- `project-docs/04_validation/VALIDATION-YYYYMMDD-XXX.md`（如任务要求）
- `project-docs/05_index/TASK_INDEX.md`

---

## 8. Codex 必须创建和维护的文档

- `project-docs/03_review_logs/REVIEW-YYYYMMDD-XXX.md`
- `project-docs/05_index/TASK_INDEX.md`

---

## 9. 共同约定

### 9.1 同一任务编号
同一个任务的所有文档必须使用同一编号，例如：

- `TASK-20260401-001.md`
- `DEVLOG-20260401-001.md`
- `REVIEW-20260401-001.md`
- `VALIDATION-20260401-001.md`

### 9.2 同一事实源
事实读取优先级：

1. 当前代码
2. 当前任务卡
3. 当前项目基线文档
4. 当前开发记录 / 审核记录 / 验证记录
5. 聊天说明

### 9.3 不允许伪完成
以下情况都视为未完成：
- 没有任务卡
- 没有开发记录
- 没有审核记录
- 任务状态未更新
- 声称已验证但没有验证记录或日志证据
- 审核意见没有落到具体文件或问题点

### 9.4 一次只推进一个明确状态
每轮动作都必须明确推进任务状态。

### 9.5 审核只针对本任务范围
Codex 只能审核本任务涉及范围。

### 9.6 开发记录必须真实
Claude 必须写清楚：
- 做了什么
- 没做什么
- 怎么验证
- 验证结果是什么
- 风险在哪里

### 9.7 审核结论必须明确
Codex 的审核结论只能是以下之一：
- `APPROVED`
- `CHANGES_REQUIRED`
- `BLOCKED`

---

## 10. 任务状态机

所有任务必须遵循以下状态机：

- `DRAFT`
- `IN_DEV`
- `DEV_DONE`
- `IN_REVIEW`
- `CHANGES_REQUIRED`
- `APPROVED`
- `VERIFIED`
- `CLOSED`
- `BLOCKED`

---

## 11. 任务流转规则

标准流程：

1. 创建任务卡 → `DRAFT`
2. Claude 开始开发 → `IN_DEV`
3. Claude 完成本轮开发与开发记录 → `DEV_DONE`
4. 提交 Codex 审核 → `IN_REVIEW`
5. Codex 给出结论：
   - 需要修改 → `CHANGES_REQUIRED`
   - 审核通过 → `APPROVED`
6. 若任务要求验证，则继续：
   - 验证通过 → `VERIFIED`
7. Ala 确认任务完成 → `CLOSED`

如环境不满足、依赖缺失、ADB 设备不可用、关键信息不足，则可进入：
- `BLOCKED`

---

## 12. 一条铁律

**聊天只是协作入口，仓库文档才是工程事实。**
