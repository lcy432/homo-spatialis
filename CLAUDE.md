# CLAUDE.md

本文件是 Claude Code 每次会话自动加载的项目说明。设计文档在 `README.md`，本文件只讲工程约定与硬规则。

---

## 项目一句话

一个可计算的空间福利模型（产品模式：算出一个人在哪座城市过得最好），并用它作为真值标尺检验大语言模型能否回收已知偏好（研究模式）。

**开工前必读 `README.md` 的第 3、7、11 章。** 第 3 章是信息边界，第 7 章是福利模型的全部数学，第 11 章是研究模式。这三章决定代码结构，不读会写错。

---

## 概念裁决（已收敛，不要再问）

Claude Code 此前提出的四个概念问题已全部裁决，答案在 README：

| 问题 | 裁决位置 |
|---|---|
| 效用函数形式与权重映射 | §7.1、§7.2 |
| 舒适度基数化与参照总体 | §5.3、§7.3 |
| 重复计入 | §7.4（财务与居住已移出舒适度向量） |
| 离散度比的参考熵与 σ | §11.6（第三档按精确并列构造，σ 从主指标消失） |
| 权重回收的识别单位 | §11.7（混合 logit 估协方差）、§11.9（人设轴与世界轴正交） |
| 研究模式跑在哪套城市上 | §11.3（合成城市；真实都会区只服务产品模式） |

**唯一尚未裁决的**：研究模式一千个人设的权重抽样分布。均匀单纯形抽样会产出大量"七维都差不多"的人，而那些人区分度最低。先按 Dirichlet(α=0.5) 实现（偏向稀疏、有主导维度的权重），把分布参数做成配置项，等实验设计定了再调。

---

## 硬规则

1. **绝不把 `GroundTruth` 送进任何 prompt。** 代理层适配器的签名只接受 `Observable`。这条由类型系统守，并有自动泄漏测试（README §10.2）。
2. **绝不提交任何密钥。** `.env` 必须在 `.gitignore` 里，仓库里只放 `.env.example`。
3. **未经询问不得修改 `src/econ/`。** 它是真值的唯一来源，任何改动都会让此前跑过的实验作废。要改先说明理由并等确认。
4. **每个原始数据列在效用计算图里只能被引用一次**（README §7.4）。有断言测试，不要绕过它。
5. **语言模型不生成数值。** 数据转化环节只做模式映射，最终表里每个值必须可溯源（README §5.4 规则一）。
6. **模型版本用带日期戳的快照，不用滚动别名。** 版本字符串写进每一条日志。
7. **不写记忆流、不写多轮对话。** 每代理每年常数次调用，这是成本设计的硬约束（README §11.9）。

---

## 技术栈

```
Python 3.11+
uv                依赖与虚拟环境管理
pandas            M1 数据抓取与清洗（读 BLS 的 XLSX 用它，容错和文档比 polars 好）
polars            下游分析可选，parquet 作交换格式
numpy / scipy     M2 福利模型、M5 估计
pydantic          配置与结构化输出校验
pyyaml            实验配置
httpx             HTTP
tenacity          重试与指数退避
python-dotenv     本地密钥加载
openai            调用 DashScope 的 OpenAI 兼容端点
pytest            测试
ruff              格式与静态检查
matplotlib        出图
```

估计层的条件 logit 与混合 logit：**先手写似然用 `scipy.optimize.minimize` 求解**，再用 `xlogit` 或 `pylogit` 交叉验证结果一致。不要一上来就调包。

---

## 目录结构

```
homo-spatialis/
├── README.md              设计文档（活文档）
├── CLAUDE.md              本文件
├── PLAN.md                实施计划
├── pyproject.toml
├── Makefile
├── .env.example
├── .gitignore
├── src/
│   ├── data/              M1  抓取与清洗
│   ├── ledger/            M1.5 字段台账、参照总体、分位表
│   ├── econ/              M2  福利模型（真值，改动需询问）
│   ├── worlds/            M3  合成世界与人设生成
│   ├── agentlayer/        M4  语言模型接口（不叫 agents：PyPI 上 openai-agents 装成顶层 agents，会静默冲突）
│   ├── eval/              M5  评估
│   └── ui/                M6  问卷与结果展示
├── tests/
├── configs/               YAML 实验配置
├── scripts/               一次性脚本
├── data/
│   ├── raw/               gitignore
│   ├── processed/         提交进仓库（见下）
│   └── frozen/            参照总体与分位表，提交进仓库
├── runs/                  按 run_id 分目录，gitignore
└── notebooks/
```

---

## 命令约定

```
make setup      uv sync
make test       pytest
make lint       ruff check && ruff format --check
make data       抓取并清洗（幂等，已有文件跳过）
make freeze     构建并冻结参照总体与分位表
make demo       跑一个最小的端到端示例
```

---

## 数据获取决策

- **OEWS 走批量下载**，不走 API。BLS 的 API 主要服务时间序列，取横截面很别扭。下载脚本存原始文件到 `data/raw/oews/<vintage>/` 并记录 URL 与下载日期。
- **BEA 走 API**，需注册免费 key，RPP 在里面。key 名 `BEA_API_KEY`。
- **处理后的 parquet 直接提交进仓库。** 三十个都会区、八个职业的文件只有几兆。**不引入 git-lfs 或 DVC**，这个阶段少学一个工具的收益大于一切。超过五十兆再说。
- **分位表与参照总体一旦冻结就不再变**，文件名带 `reference_id`（如 `US_MSA_2024.parquet`）。绝不在查询时现算分位。

---

## 语言模型配置

**两条完全独立的凭证路径，不要混淆：**

**路径一，Claude Code 自己用的**：由用户在客户端层面配置，与本项目代码无关。

**路径二，项目代码在运行时调用的被测基座**：走 DashScope 的 **OpenAI 兼容端点**，密钥从环境变量读取。

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
```

国际版 base URL 为 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`，与 API Key 所属地域必须匹配。

**两个基座**（README §10.4 要求不同家族、不同来源国）：

| 角色 | 提供方 | 配置项 |
|---|---|---|
| 基座 A | 前沿模型 | `BASE_MODEL_A`，带日期戳快照 |
| 基座 B | DashScope 的 Qwen | `BASE_MODEL_B`，带日期戳快照 |

模型名从百炼控制台的模型列表取**带日期后缀的快照版本**，不要用 `qwen-plus` 这类滚动别名。选定后把完整字符串写进 `configs/models.yaml` 并在每条日志里记录。

**统一适配器**：两个基座都通过同一个 OpenAI 兼容客户端调用，只换 `base_url`、`api_key`、`model`。不要为不同厂商写两套代码路径，否则你无法保证两个基座收到的是同一个 prompt，而那会毁掉对比。

**调用规范**：
- 结构化输出用 JSON schema 加 `pydantic` 二次校验，不要用正则解析自由文本
- 并发用 `asyncio` 加 `asyncio.Semaphore` 限流
- 重试用 `tenacity` 指数退避，默认三次
- 每次调用的请求、响应、耗时、token 数、`run_id`、seed、模型版本写成一行 JSONL 存进 `runs/<run_id>/calls.jsonl`

---

## 工作方式

- **权限模式用 Plan**，不用 Auto。先出计划、等批准，再动代码。
- **任务粒度要小。** "实现 M2" 是坏任务；"写一个函数，输入 Cobb-Douglas 参数与城市属性向量，返回间接效用，附三个单元测试" 是好任务。
- **每次动手前先 commit。**
- **M2 与 M5 的核心数学由人类先写第一版**，Claude Code 负责 review 与补测试。M0、M1、M4 的工程部分可以放手。

---

## 实现顺序

M0 → M2 → M3 → M4 → M5，M1 与 M1.5 并行推进。

**M1 不在关键路径上**：合成世界的城市属性是生成的，不需要真实数据。先跑通合成链条，M1 作为后期替换的数据源接入。这样任何时点都有一个能演示的东西。
