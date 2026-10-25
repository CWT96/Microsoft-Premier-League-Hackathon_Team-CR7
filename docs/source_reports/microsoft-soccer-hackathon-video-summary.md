# Microsoft × Premier League Hackathon：视频与赛事总结

整理日期：2026-10-20  
活动：Inside the Game: Developer Hackathon  
视频：Build AI-powered soccer insights apps with AKS and ACA  
主讲人：Brian Benz  
直播日期：2026-10-14

- [Microsoft Reactor 活动页面](https://developer.microsoft.com/en-us/reactor/events/27536/)
- [YouTube 视频](https://www.youtube.com/watch?v=EQ187ohgCbo)

## 资料范围

本总结依据视频章节、部分演示画面、直播聊天中的官方答复、讲师配套代码仓库和比赛官方规则。YouTube 完整字幕未能加载，因此视频部分不是逐句听写，也不代表已完整核对所有口头说明。

以下赛事信息截至整理日期；后续变更以官方规则和参赛平台通知为准。文中明确标注的应用建议属于分析判断，不是官方指定题目。

## 1. 视频主要讲了什么？

这是一场约 64 分钟的技术分享，演示如何构建和部署足球 AI 分析应用。

| 视频位置 | 主要内容 | 对参赛的帮助 |
|---|---|---|
| [2:56 起](https://www.youtube.com/watch?v=EQ187ohgCbo&t=176s) | 展示足球数据分析仪表盘，探索约 150 年国际足球历史 | 了解可运行产品如何组织问题、数据和界面 |
| [9:17 起](https://www.youtube.com/watch?v=EQ187ohgCbo&t=557s) | 应用架构、数据导入、计算与 AI 的分工 | 理解从数据到回答的完整流程 |
| [14:43 起](https://www.youtube.com/watch?v=EQ187ohgCbo&t=883s) | 身份认证、安全、Azure 资源，以及数据名称匹配问题 | 让演示具有实际部署基础 |
| [31:13 起](https://www.youtube.com/watch?v=EQ187ohgCbo&t=1873s) | AKS 与 ACA 的比较、扩容和版本更新 | 选择合适的部署方式 |
| [42:41 起](https://www.youtube.com/watch?v=EQ187ohgCbo&t=2561s) | 用 GitHub Copilot 构建应用、组织生成提示词 | 借鉴开发流程和项目要求的写法 |

示例项目最值得借鉴的原则是：**Python/SQL 负责计算数字，AI 负责解释数字。**

模型调用分析工具后再生成回答；系统检查回答中是否出现工具没有返回的数字。示例问题包括主场优势、不同年代的强队、举办大赛与成绩之间的关系等。

来源：[讲师配套仓库](https://github.com/bbenz/football-results-dashboard)。

## 2. 比赛的目标是什么？

比赛的核心，是把足球事件数据变成观众看得懂、直播用得上、还能按个人偏好调整的 AI 产品。

比赛面向演播室、赛事转播、流媒体和球迷，希望作品能够：

- 实时分析比赛事件。
- 解释关键时刻及战术模式。
- 生成故事、精彩时刻描述和赛后总结。
- 提供多语言、个性化体验。
- 创建或扩展适合 AI 与分析的足球数据集。

Microsoft 的开发和云服务是主办方重点鼓励使用的工具。

来源：[官方比赛介绍](https://events.microsoft.com/flow/microsoft/plmshackathon/registration/page/landing)。

可以把产品目标理解为：

**比赛发生了什么 → 数据说明了什么 → 为什么值得关注 → 怎样呈现给这位观众。**

例如，“连续三次夺回球权”只是事件；把它解释为一段有证据支持的压迫变化，再给普通球迷和专业分析者显示不同深度的信息，才形成有用的观看体验。这是根据比赛目标做的具体化示例。

## 3. 评选标准与期待的应用

### 官方评选标准

先进行主题与技术应用的通过／不通过筛选，再按以下五项评分，**每项都是 20%**。

| 评分项 | 关注点 |
|---|---|
| 技术实现 | 合成数据质量、微软技术的有效使用、代码与文档质量 |
| Agent 设计与创新 | 工具调用、任务编排、协作方式是否有价值 |
| 实际影响 | 问题是否重要，能否落地 |
| 用户体验与展示 | 界面直观，演示清楚，前后端完整 |
| 类别契合度 | 是否符合所选奖项方向 |

类别奖包括 Foundry、企业应用、多 Agent 系统、Azure 云原生集成。

提交需要：

- 公开 GitHub 仓库。
- 可测试作品。
- 英文材料或英文翻译。
- **不到两分钟的运行演示视频。**

来源：[官方评审与提交规则](https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md)。

### 应用方向建议

以下是结合比赛要求的选题判断，不是官方指定题目：

| 应用方向 | 可以实现的功能 |
|---|---|
| 直播解说助手 | 识别值得讲解的时刻，生成简短解说，并显示支撑判断的事件和指标 |
| 个性化观赛面板 | 同一场比赛可切换新球迷、战术分析、指定球队或球员模式 |
| 赛后复盘工具 | 自动整理关键转折、对应证据和多语言摘要，允许编辑人员核对与修改 |

结合应用经济学研究背景，最有发挥空间的是**指标设计、数据质量和解释可信度**。例如，清楚区分“某项指标与表现相关”和“它导致了表现变化”，会让分析更可靠。

做一个范围明确、证据可追溯的功能，更容易在短演示里说明价值。

## 4. 可以利用的资源

### 讲师的开源示例

[Football Results Dashboard 仓库](https://github.com/bbenz/football-results-dashboard)包含：

- 应用示例与架构说明。
- 分析方法和数据处理说明。
- 本地运行与 Azure 部署文档。
- AKS／ACA 比较。
- 提示词。
- 用于扩展项目的 Hackathon Guide。

可以借鉴它的数据计算、AI 解释和部署结构。

视频在 [57:00 左右](https://www.youtube.com/watch?v=EQ187ohgCbo&t=3420s)给出的部署建议是：

- 想快速上线、团队不熟悉 Kubernetes、应用主要是几个网页服务和批处理任务时，可以先考虑 **ACA（Azure Container Apps）**。
- 需要 Kubernetes 的细粒度控制时，再考虑 **AKS（Azure Kubernetes Service）**。

### 官方学习资源

| 学习资源 | 主要用途 |
|---|---|
| GitHub Copilot | 编程、调试和开发 |
| Microsoft Foundry | 构建、评估和管理 AI 应用 |
| Foundry Agents | 让 Agent 使用工具、完成任务 |
| Agentic Workflows | 组织多步骤 AI 工作流 |
| Data | 数据处理与分析基础 |

- [官方学习资源清单](https://github.com/microsoft/insidethegamehackathon/blob/main/RESOURCE%20PLAYLISTS.md)
- [Microsoft Reactor 技术分享系列](https://developer.microsoft.com/en-us/reactor/series/s-1709/)

### 数据、云额度与参赛平台

**数据：** 比赛要求使用符合足球逻辑的合成数据，主办方不向参赛者分发比赛数据或录像。讲师演示使用历史足球数据，不能直接将其理解为比赛指定数据集。

来源：[官方数据要求](https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md#47-use-of-footage)。

**云额度：** 截至整理日期，已查阅的公开资料中没有找到比赛统一赠送 Azure credits 或免费模型额度的明确承诺。报名免费，不等于运行云服务免费；额度支持仍需以参赛平台或官方通知为准。

**参赛平台：** 报名后，Microsoft 会发送 Innovation Studio 官方参赛平台的邀请。

来源：[报名与参与说明](https://events.microsoft.com/flow/microsoft/plmshackathon/registration/page/landing)。

## 5. 关键时间

以下时间均为 2026 年。纽约与太平洋时间在这些日期分别使用 EDT 和 PDT。

| 节点 | 官方太平洋时间 | 纽约时间 |
|---|---|---|
| 报名截止 | 10 月 20 日 12:00 | **10 月 20 日 15:00** |
| 作品提交截止 | 10 月 27 日 23:59 | **10 月 28 日 02:59** |
| 前六名项目通知 | 11 月 10 日前 | 官方未在报名介绍中给出具体通知时刻 |

来源：[官方时间安排](https://events.microsoft.com/flow/microsoft/plmshackathon/registration/page/landing)。
