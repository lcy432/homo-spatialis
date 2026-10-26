# 复现首版模拟引擎

[English](REPRODUCING.en.md) · 简体中文

本说明的目标是用仓库自带的合成人物、合成城市和确定性 mock 规划器复现一条完整产品流程。它检验工程行为，不验证真实城市排序或真实 LLM 的能力。软件版本见根目录 `pyproject.toml`；首发目标为 `0.1.0`，发布进度见 [项目索引](INDEX.md)。

## 环境与源码

需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python。仓库 `.python-version` 固定为 Python 3.13；依赖版本由 `uv.lock` 固定。首次安装依赖需要联网，下面的两个模拟命令无需模型密钥，也不访问模型或外部数据端点。

```text
git clone https://github.com/lcy432/homo-spatialis.git
cd homo-spatialis
uv sync --locked
```

正式发布后，在安装前执行 `git checkout v0.1.0`；发布标签尚不存在时，使用发布准备提交并记录 `git rev-parse HEAD`，不要把浮动的 `main` 当成固定版本。也可以下载对应 Release 的源码 ZIP，解压后在根目录运行同样命令。

本项目通过源码目录运行，不依赖将它安装成可导入的同名 Python 包。无需配置 `.env`、启动网页、下载原始城市资料或执行 `make check-env`。请保留下面的 `run-offline` 子命令；另一个入口 `run_product_full.py` 若未加 `--mock` 会请求付费模型。

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
