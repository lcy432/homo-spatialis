# 网页开发与本地试用

[English](README.en.md) · 简体中文

[项目首页](../README.zh-CN.md) · [文档索引](../docs/INDEX.md) · [开发指南](../docs/DEVELOPMENT.md) · [网页设计](../docs/design/e5-web-ui.md)

用户在浏览器里走完“专属链接 → 开局设置 → 填写与审阅 → 本次设置 → 确认并开始 → 进度 → 城市比较 → 单城详情 → 分身年度生活”。
偏好输入使用 v3.5 的独立评分：每项 0–100，不必凑满 100；审阅时分别展示原始评分与服务换算的权重。

网页从 E3C 服务（`/api/v1`）读取业务数据。用户确认开始后，服务完成检查并按授权启动，网页展示服务返回的阶段和进度。下列本地配置使用离线 mock，不需要模型密钥；输出用于检查流程，不代表真实模型的表现。

## 本地运行（不需要任何密钥，不调用真实模型）

需要 Node.js 22 与仓库的 Python 环境（在根目录运行 `uv sync --locked`）。四个终端：

1. 在仓库根目录起服务的接口进程（只绑定 127.0.0.1:8765，离线 mock）：

   ```bash
   uv run python scripts/product_service.py api --config configs/product/service_local_mock.yaml
   ```

2. 在仓库根目录起 worker：

   ```bash
   uv run python scripts/product_service.py worker --config configs/product/service_local_mock.yaml
   ```

3. 在 `web/` 里起页面（`/api` 代理到 8765，页面来源是 `http://localhost:5173`，与服务配置的 `site_origin` 一致）：

   ```bash
   npm ci
   ```

   ```bash
   npm run dev
   ```

4. 在仓库根目录建一个专属链接（只显示一次，直接在浏览器里打开；不要贴进日志或截图）：

   ```bash
   uv run python scripts/product_service.py invite create --label 本地试用 --config configs/product/service_local_mock.yaml
   ```

打开链接之后，页面会立刻把秘密从地址栏抹掉。用 `http://127.0.0.1:5173` 打开时，要把服务配置的 `site_origin` 跟着改（不放宽成多个来源）。
本地配置默认使用资料包的全部六个都会区、每城 4 个分身；当前服务开放三年模拟，开始前可按服务允许的范围调整分身数。以“本次设置”预览为准，耗时随运行规模和机器而变。服务配置见 [`service_local_mock.yaml`](../configs/product/service_local_mock.yaml)。

## 测试

```bash
npm test
```

```bash
npx playwright install chromium
```

```bash
npm run test:e2e
```

* `npm test`：先核对共享结构（哈希与生成的类型），再跑单元与组件测试。
* `npm run test:e2e`：Playwright 自己起一套隔离的服务（127.0.0.1:8775，数据目录 `runs/web-e2e/service` 每次清空，来源
  `http://localhost:5180`，Origin / CSRF / 限流都是默认值）和 Vite（5180），跑主闭环、会话与身份、草稿冲突、键盘、地球、
  无 WebGL、手机 360×740。截图与可访问性报告写进本机的 `runs/web-e2e/`（不进 git，每次覆盖）。
* `npm run typecheck` 检查类型；`npm run build` 核对共享结构、检查类型并生成构建产物。

默认测试当前工作区。需要接另一份后端代码时，可设置 `E5_SERVICE_REPO`，并用 `E5_CONTRACTS_DIR` 指向对应的 `contracts/product/v3/`；确保网页实现、生成类型和后端共享结构相匹配。路径相对于执行命令时的 `web/` 目录解析。

## 规矩

* `src/contract/generated/` 由 `npm run contract:gen` 生成，**不要手改**。共享结构由 E3 维护（`src/common/product_contracts.py` →
  `contracts/product/v3/`）；`contract:check` 在 `build` 与 `test` 之前自动执行，结构一变就失败。字段有缺口，写进
  [E5 → E3 交接单](../docs/handoffs/e5-bilingual-map-service-requests.md)，不在前端另加字段。
* 结果数字显示 E3 给的 `display`，不在前端另算 J、均值、比例或范围。评分输入的临时权重预览用于填写反馈，正式换算由服务完成；页面不另排城市名次或生成推荐。
* 浏览器本地存储保存显示语言、外观，以及“一次开始”的找回记录。找回记录包含确认画像编号、草稿编号、运行选项、同意身份、知悉项、幂等键和时间，用于断线或刷新后找回同一次请求；收到成功回复、退出或切换身份时清除。问卷答案正文和结果正文不写入本地存储，邀请秘密和会话凭证也不写入其中。实现见 [`startRecord.ts`](src/draft/startRecord.ts)。
* 界面文字在 `src/i18n/zh.ts`、`en.ts`。业务文字已接 E3 的双语显示信封：按对象与字段位置取得英文，并核对对应原文；缺译时保留中文并标注“未翻译”。实现见 [`biz.tsx`](src/i18n/biz.tsx) 与 [`display/`](src/display/)。
* 底图与地球纹理的来源、许可与处理方法见 [`SOURCES.md`](public/geo/SOURCES.md)；重新生成底图：`node scripts/build-basemap.mjs --zh … --en …`。

## 目录

```text
scripts/        contract-gen.mjs、contract-check.mjs（共用 contract-lib.mjs）、build-basemap.mjs（国家底图）
public/geo/     国家底图（TopoJSON，中文 / 英文两种立场）、地球纹理、来源说明
src/contract/   生成的类型、Ajv 校验、样例读取（测试用）
src/api/        HTTP 客户端、接口、会话与身份、查询与轮询
src/draft/      状态更新函数、自动保存、草稿编辑器、审阅、开始请求找回
src/display/    双语显示信封、字段定位与名字解析
src/i18n/       界面字典与业务文字的显示
src/pages/      会话、首页、开局设置、填写步骤、运行意图、结果三层、我的运行、设置
src/components/ 独立评分、城市列表、地图与地球、设置与开始、进度、比较图、年度记录
tests/          Vitest；tests/fixtures/drafts/ 是给接入层核对的草稿样本
e2e/            Playwright（隔离服务的配置与启动脚本、各场景）
```
