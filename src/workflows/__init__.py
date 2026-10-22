"""产品运行的外层流程（LangGraph 原型）。

只管“流程”：运行前检查 → 等待用户确认本次模拟 → 幂等提交 → 启动或恢复 E1 → 交付结果，以及出问题时停下等人处理。
钱、病情、资格、年度评分等生活状态全部留在 E1 的运行归档里；画像、预检查、进度与结果的公共结构由 E3 维护
（``common.product_contracts``）。本包只保存这些对象的引用与内容身份，不复制一套契约。

模块：

* ``state``：外层流程状态（只有引用、阶段、幂等键与运行编号）与公开代码表；
* ``ports``：流程要调用的四个服务接口（E3 预检查、E3 提交、E1 运行、E3 投影）；
* ``graph``：LangGraph 的 StateGraph；
* ``runtime``：带 SQLite 持久化的操作入口（开始、确认、拒绝、修改、恢复、处理、查询）；
* ``e3_adapters``：真实 E3 / E1 的接入层（2026-10-18 接入 E3-A 最终版；规划器只接受资料包声明的离线 mock）；
* ``synthetic``：合成替身（文件持久化、可注入故障），只用于检验流程本身，不是 E1 或 E3。
* ``data_build``：真实资料构建流程（intake-02）：来源配置 → 获取或读缓存 → 标准化 → A/B 映射 → 目录与菜单 → 检查 → 冻结；与用户模拟流程无关。

依赖方向：本包可以调用 ``product`` 与 ``orchestration``；``common``、``econ``、``ledger``、``agentlayer``、``product``、
``orchestration`` 都不得反过来依赖本包或 LangGraph（``tests/workflows/test_isolation.py`` 检查）。
"""
