# 开发指南

从[项目索引](INDEX.md)找到本次工作的模块，再读对应设计。整体调用关系见[架构说明](ARCHITECTURE.md)，贡献流程见[贡献指南](../CONTRIBUTING.md)。编码代理同时遵守根目录的 [CLAUDE.md](../CLAUDE.md)。

## 准备环境

在仓库根目录运行：

```sh
uv sync --locked
```

Python 版本由 [`.python-version`](../.python-version) 指定，依赖由 [`uv.lock`](../uv.lock) 固定。应用直接从源码运行，入口脚本通过 `scripts/_bootstrap.py` 加载 `src/`。首次体验与输出说明见[复现指南](REPRODUCING.md)。

网页使用 Node.js 22。在 `web/` 中运行 `npm ci`；服务、worker 和网页的启动方式见 [web/README.md](../web/README.md)。

## 代码与文档一起定位

| 要改什么 | 从哪里开始 | 对应说明 |
|---|---|---|
| 月度执行、资源、评分与多年评价 | `src/econ/life_*`、`tests/life/` | [生活模型](design/e1-3-life-model.md)、[评价](design/e1-6-evaluation.md) |
| 代理观察、计划、模型传输 | `src/agentlayer/`、`src/orchestration/` | [规划闭环](design/e1-7-agent-loop.md) |
| 输入与确认、城市资料、结果投影 | `src/product/`、`tests/product/` | [输入](design/e3a-product-input.md)、[资料指南](DATA.md) |
| 后台流程、会话、队列、恢复 | `src/workflows/`、`src/product/service/`、`tests/product_service/` | [工作流](design/langgraph-product-workflow.md)、[服务](design/e3c-service.md) |
| 网页表单与结果 | `web/src/`、`web/tests/`、`web/e2e/` | [网页设计](design/e5-web-ui.md) |
| 静态福利模型与研究测量 | `src/worlds/`、`src/eval/`、`tests/eval/` | [研究方法](RESEARCH.md) |

经济公式集中在 `src/econ/`。改变环境数值、资料映射或评价方式时，同步更新[参数与假设目录](design/parameter-assumption-register.md)。调整经济机制前按 [CLAUDE.md](../CLAUDE.md) 说明数学含义；普通接口与展示逻辑不要另写一份经济计算。

## 自动化检查

Python 的日常离线套件与格式检查：

```sh
uv run --frozen pytest -n auto
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
```

默认套件排除 `live` 和 `slow`。研究线测试用 `uv run --frozen pytest -m slow` 单独运行；真实端点测试带 `live` 标记，可能访问网络或消耗模型额度。测试范围由 [`pyproject.toml`](../pyproject.toml) 和 [`tests/conftest.py`](../tests/conftest.py) 定义。耗时取决于机器、并发数和所运行的资料包。

定位单个问题时可以直接指定文件，例如：

```sh
uv run --frozen pytest tests/life/test_settlement.py
```

网页检查在 `web/` 内执行：

```sh
npm test
npm run typecheck
npm run build
```

浏览器端到端测试的依赖和命令见[网页测试](../web/README.md#测试)。修改之后记录实际运行的检查及结果，区分本机验证、CI 和历史报告。

## 接口与生成文件

共享消息的 Python 定义在 [`src/common/product_contracts.py`](../src/common/product_contracts.py)，JSON Schema 与公共样例在 [`contracts/product/v3/`](../contracts/product/v3/README.md)。改变字段先更新权威定义，再重新导出结构与前端类型。具体导出命令见[共享结构说明](../contracts/product/v3/README.md)。

前端 `src/contract/generated/` 中的类型由 `npm run contract:gen` 生成；`npm test` 和 `npm run build` 会先检查生成内容与结构的一致性。新状态还需要相应的显示文字、输入样例或结果样例。

## 维护文档

- README 只介绍项目、试用入口、范围和方向；中英文的事实与路线图保持一致。
- 代码职责写在 [ARCHITECTURE](ARCHITECTURE.md)，公式与映射细节放入现有 [design/](design/README.md)。
- 当前模块状态和默认入口继续维护在 [INDEX](INDEX.md)，版本方向写在 [PLAN](../PLAN.md)。
- 文献依据写在 [REFERENCES](REFERENCES.md)，真实设计决定写入 [DECISIONS](../DECISIONS/README.md)。
- 链接使用相对于所在文件的位置；更名或移动后检查入口、锚点及引用它的文档。旧章节编号通过[历史导航](history/README.md)查找。

多个会话共用工作区时，修改前核对当前差异，只提交本次工作的文件。提交与推送的约定见 [CLAUDE.md](../CLAUDE.md)。
