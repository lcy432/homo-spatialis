# 运行与复现

[English](REPRODUCING.en.md) · 简体中文

先用下面的离线示例，在合成人物、合成城市和确定性 mock 规划器上跑通完整产品流程，再按[真实模型运行说明](#real-models)使用自己的 API 密钥。离线示例检验软件行为，不验证真实城市排序或真实 LLM 的能力。软件版本见根目录 `pyproject.toml`；首发目标为 `0.1.0`，发布进度见 [项目索引](INDEX.md)。

## 环境与源码

需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python。仓库 `.python-version` 固定为 Python 3.13；依赖版本由 `uv.lock` 固定。首次安装依赖需要联网，离线示例无需模型密钥，也不访问模型或外部数据端点。

```text
git clone https://github.com/lcy432/homo-spatialis.git
cd homo-spatialis
uv sync --locked
```

正式发布后，在安装前执行 `git checkout v0.1.0`；发布标签尚不存在时，使用发布准备提交并记录 `git rev-parse HEAD`，不要把浮动的 `main` 当成固定版本。也可以下载对应 Release 的源码 ZIP，解压后在根目录运行同样命令。

本项目通过源码目录运行，不依赖将它安装成可导入的同名 Python 包。离线示例无需配置 `.env`、启动网页、下载原始城市资料或执行 `make check-env`。请保留下面的 `run-offline` 子命令；另一个入口 `run_product_full.py` 若未加 `--mock` 会请求付费模型。

## 成功示例

```text
uv run --frozen python scripts/product_e3a.py run-offline --out runs/reproduce-v0.1.0
```

输出目录必须不存在；重跑请换目录名。流程为：答案草稿 → 审阅与确认 → 资料编译 → 运行前检查 → 幂等提交 → mock 年度规划与逐月结算 → 评价与产品结果。

输入主要来自 `configs/product/e3a_synthetic_pack.yaml`、`configs/life/*_product.yaml` 及 `src/product/fixtures.py`。空间标识 `US:SYNTH2026:K51` 与 `US:SYNTH2026:M52` 代表虚构城市，不对应真实美国城市。

应得到：

| 文件 | 应检查什么 |
|---|---|
| `runs/reproduce-v0.1.0/summary.json` | `status: completed`；两个分身均 `completed`、各 3 年；`planner_calls: 6`；`real_model_calls: 0`；`provider_calls_recorded: false` |
| `messages/result.json` | 两座合成城市的评价、人数分母、限制与出处信息 |
| `messages/receipt.json` 与 `messages/duplicate_receipt.json` | 相同运行标识，重复提交未新建另一场模拟 |
| `workspace/compiled/normal/` 与 `workspace/archive/normal/` | 编译配置、输入快照与运行归档 |

保存本次命令、源码提交与 `summary.json`，即可留下可核对的运行记录。复现比较的是固定输入下的业务结果；路径、运行时间等非业务元数据不要求逐字节一致。不要将示例中的分数当成现实福利量表或真实城市优劣证据。

## 失败示例

```text
uv run --frozen python scripts/product_e3a.py reject-example --out runs/reproduce-v0.1.0-rejections
```

查看 `rejections.json`：设备报价存在但寿命未知时，设备不进入可执行目录（`device_in_e1_catalog: false`）；职业在本地无可匹配岗位时，记录预检查问题；金额无法换算到规定币种和基年时，报告 `money_conversion_missing`。这些是受控输入的预期拒绝，不是现实发生率估计。

<a id="real-models"></a>
## 使用自己的模型密钥运行

完成前面的环境安装后，可以将离线规划器换成千问。下面的命令会让样例人物在所选资料包的所有城市生活，默认每城四个分身、模拟三年。

### 配置密钥和端点

在[阿里云百炼](https://help.aliyun.com/zh/model-studio/get-api-key)创建按量付费的 API Key。如果仓库根目录还没有 `.env`，将 [`.env.example`](../.env.example) 复制为 `.env`；已有文件则直接编辑，填入：

~~~dotenv
DASHSCOPE_API_KEY=your_dashscope_api_key_here
~~~

将占位文字换成自己的密钥。也可以设置系统环境变量 `DASHSCOPE_API_KEY`，并删除 `.env` 中对应的那一行。已有环境变量不会被覆盖；两处值不一致时，模型初始化会报配置冲突。密钥文件不要提交到 Git。使用仓库中的资料包无需 BEA 密钥，也不用下载原始数据。

仓库当前通过 `product_planner` 角色选择 `qwen3.8-flash`，默认端点为北京地域的 `https://dashscope.aliyuncs.com/compatible-mode/v1`。不同地域的密钥、端点和可用模型不能混用。使用其他地域的密钥时，按照[官方接入说明](https://help.aliyun.com/zh/model-studio/get-api-key)选择对应端点，并**同时修改两处**：

| 文件 | 配置项 |
|---|---|
| [`configs/models.yaml`](../configs/models.yaml) | `product_planner.base_url` |
| [`configs/life/run_product_flash.yaml`](../configs/life/run_product_flash.yaml) | `run.planner.llm.base_url` |

两处的供应商和模型名称也必须一致。确认账户能使用所选模型，并核对 `run.paid_budget.price` 的地域与报价。全规模启动脚本目前使用人民币预算；只把报价币种改成美元会无法通过检查，脚本不会自动换汇。

### 启动模拟

下面的命令会产生付费模型请求。先用合成资料包试跑：

~~~sh
uv run --frozen python scripts/run_product_full.py --out runs/first-llm-synthetic --max-cost-cny 20
~~~

也可以使用冻结的美国六城资料包：

~~~sh
uv run --frozen python scripts/run_product_full.py --out runs/first-llm-six-cities --pack configs/product/e3b_us_v1_pack.yaml --abilities edu_at_least_hs --max-cost-cny 20
~~~

第二条命令明确确认样例人物具有高中学历，这是开局岗位需要的资格，不会从职称自动推断。

两条命令都使用内置样例人物。要填写自己的资料并查看可视化结果，请使用[网页](../web/README.zh-CN.md)。本地网页使用 mock，即用于测试的确定性离线规划器；它既能跑内置样例，也能跑自己填写的资料。要让真实 LLM 按自己的资料规划，需要配置下文的 HTTPS 服务。

每次使用新的输出目录。`--max-cost-cny 20` 将本次运行的估算费用限制为 20 元，也可以调低。程序在发送请求前预留估算费用，下一次预留会超限时就停止。这是按配置报价计算的应用内上限，不是供应商账单上限。每次新运行都会建立新账本，不会跨运行累计额度。在任一命令后加上 `--mock`，即可用同一资料包和输入流程做离线检查，不调用模型。

### 查看结果

以下路径都位于所选的输出目录中：

| 路径 | 内容 |
|---|---|
| `summary.json` | 批次状态、城市、分身、已完成年数与进度 |
| `ws/compiled/full/` | 编译后的输入和实际使用的 `run_flash_full.yaml` |
| `ws/archive/full/` | 计划、模型调用记录与逐月生活记录 |
| `ws/budget/ledger.sqlite` | 请求预留、已记录用量与估算费用 |

### 在网页中使用真实模型

要让网页中填写的个人资料交由真实模型规划，需要使用生产服务配置、通过 HTTPS 访问站点、在服务端配置模型密钥，并开启 `live_calls_enabled`；API 与 worker 也需要启动。部署和邀请设置见[服务说明](design/e3c-service.md)。上面的命令行示例无需网站或 HTTPS 部署。

## 自动化检查

```text
uv run --frozen pytest -n auto
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
```

默认测试排除 `live`（真实端点）和 `slow`（研究线）标记，因此测试通过不能表述为“研究实验全部完成”。请记录本次测试数量、平台和结果；模块核验进度见[项目索引](INDEX.md#status)，开发与网页检查见[开发指南](DEVELOPMENT.md)。Linux CI 的结果必须以 GitHub 实际运行记录为准，不能由 Windows 本地通过推断。

## 复现边界

- 稳定复现入口是上述 Python 离线流程；网页与托管服务继续开发，首版不承诺其部署体验。
- 仓库含六个美国都市圈的资料配置与导出物，但资料完整度、地理口径、代理映射和合成补充各有局限；默认示例不以它们作现实预测验证。
- 依赖锁定不等于离线打包了全部依赖；首次安装需要依赖源可访问。已核验的平台以相应运行记录为准。
- 真实模型服务可能变化或不可用；滚动型号、采样与供应商差异会限制重复结果。真实运行应另记模型实际返回版本、配置、种子、费用和失败记录。
- 研究使用请保存所用软件提交 / 版本、资料包及其内容身份、配置、规划器、输出和失败记录，并分别引用软件与数据来源。
