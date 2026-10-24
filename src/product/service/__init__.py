"""E3C 托管服务（DECISIONS/0043）：专属链接与会话、次数、草稿与确认、运行意图、单个 worker、删除与过期。

* ``config``：不含秘密的服务配置（``configs/product/service.yaml``）；
* ``store``：服务端 SQLite——邀请、会话、草稿、确认、意图、次数事件、提交、任务与发送许可的权威记录；
* ``api``：FastAPI（认证、业务请求、只读查询；不调用 ``chdir``，不推进流程）；
* ``actions``：业务操作（API 调用它们）；``views``、``errors``：公开视图与统一安全错误；``tokens``、``clock``：秘密与可注入时钟。

worker（单个独立进程，串行推进模拟：排队检查、确认开始、恢复、删除收尾、过期清理，复用现有 LangGraph 与 E1）在
``workflows.service_worker`` 与 ``workflows.service_runs``：本包与其他核心包一样不依赖 LangGraph（``tests/workflows/test_isolation.py``）。

生活、评分、L / V / J 与 C / E / M 的数学都沿用 E1；这里只管身份、次数、排队与可见性。
"""
