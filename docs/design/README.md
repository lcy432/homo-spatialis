# 技术设计说明

[English](README.en.md) · 简体中文

这里放供人和编码代理反复查阅的技术说明：一个机制怎样变成数据、公式、代码与测试，省略了什么、影响谁。**进度与状态不写在这里**，见 [docs/INDEX.md](../INDEX.md)。2026-10-18 之前带状态的旧版本索引存档在 [docs/history/pre-dev-refactor-2026-10-18/docs-design-README.md](../history/pre-dev-refactor-2026-10-18/docs-design-README.md)。

第一次阅读可从[方法概览](../METHODS.md)开始；找代码看[架构](../ARCHITECTURE.md)，了解城市资料看[数据与映射](../DATA.md)，安装与修改看[开发指南](../DEVELOPMENT.md)。研究测量及其文献已从主 README 移到[研究方法](../RESEARCH.md)和[参考文献](../REFERENCES.md)。全部入口见[项目索引](../INDEX.md#reading-routes)。

## 按层

| 层 | 说明 |
|---|---|
| 生活引擎 | [生活条件与满足感（E1-3，含日常生活便利第 21 节）](e1-3-life-model.md)、[时间与精力（E1-4）](e1-4-time-energy.md)、[职业、储蓄、风险（E1-5）](e1-5-career-savings-risk.md)、[跨期评价与城市汇总（E1-6）](e1-6-evaluation.md)、[生活扩展：食品方案、享受、装备与爱好](e1-8-life-extension.md) |
| 代理层 | [代理规划闭环与真实模型（E1-7；第 18 节是 Haiku 测试）](e1-7-agent-loop.md) |
| 接入层 | [E3/E5 共享接口与设计约定](e3-e5-product-contract.md)、[E3-A 用户输入与 E1 适配](e3a-product-input.md)、[intake-02 真实资料与机会数据库](intake-02-real-data.md)、[六城生活资料包](e3b-life-pack.md)、[住房、岗位与日常支持映射](e3b-mapping-and-daily-support.md)、[通勤资料](e3b-commute-data.md)、[生活机会与职业](e3b-items-data.md)、[LangGraph 外层运行流程](langgraph-product-workflow.md)、[E3C 托管服务](e3c-service.md) |
| 界面层 | [当前网页与服务接入](e5-web-ui.md)、[E5-A 离线界面原型（历史）](e5-a-offline-ui.md) |
| 研究线 | [经济标尺与实验方法](../RESEARCH.md)、[文献与设计来源](../REFERENCES.md) |
| 跨层 | [参数与假设目录](parameter-assumption-register.md) |

文件名里的 e1-3、e3a 等是旧编号，新旧对照见 INDEX 第 1 节；文件名暂不改，免得断链。

## 交接说明

[E3 交接](../handoffs/e3-data-input-handoff.md)、[E5 交接](../handoffs/e5-product-handoff.md)、[E5 对 E3 的接口需求](../handoffs/e5-to-e3-interface-requests.md)、[日常生活便利交给接入层](../handoffs/e1-daily-convenience-to-e3.md)、[生活扩展交给界面层](../handoffs/e3-to-e5-life-extension.md)、[E3C 托管服务交给界面层](../handoffs/e3c-to-e5-service.md)。

## 历史

- [历史导航](../history/README.md)：旧 README 章节、工程计划和历次文档存档。
- [history/](history/)：阶段设计讨论（2026-10-06 E1-3 设计备忘、2026-10-12 E1-7 文献与设计）。
- [prompts/](prompts/)：过去各轮交给编码代理的任务原文。开发期不再每轮归档。

## 写说明的约定

- 每篇回答：做什么、怎样计算、数据从哪来、代码在哪、怎样验证、省略了什么、影响哪些人。
- 不写"某日补记""待复核"这类状态；状态只在 INDEX 写一次。
- 改了参数或规则，同步更新参数目录的相关条目。
- 说明文档不是另一套实现：代码说明当前实际行为，文档说明目标与约束。
