# web：E5 网页（接 E3C 本地服务）

用户在浏览器里走完“专属链接 → 开局设置 → 五步填写与确认 → 运行前说明 → 进度 → 城市比较 → 单城详情 → 分身年度生活”。
数据全部来自 E3C 服务（`/api/v1`），页面不另做计数、队列或业务判断。规划器是离线 mock 时，结果不代表模型能力，也不排名真实城市。
技术说明见 [docs/design/e5-web-ui.md](../docs/design/e5-web-ui.md)，状态见 [docs/INDEX.md](../docs/INDEX.md)。

## 本地运行（不需要任何密钥，不调用真实模型）

需要 Node 22 与仓库的 Python 环境（`uv sync`）。四个终端：

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
一次模拟（每城 1 个分身、3 年、两座城）在本机大约要 4 分钟：检查约 1 分钟，运行约 3 分钟。

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
  `http://localhost:5180`，Origin / CSRF / 限流都是默认值）和 Vite（5180），跑主闭环（约 5–7 分钟）、会话与身份、草稿冲突、键盘、地球、
  无 WebGL、手机 360×740。截图与可访问性报告写进本机的 `runs/web-e2e/`（不进 git，每次覆盖）。
* 几个会话共用工作区时，别的会话可能已经改了服务代码或 schema 却还没提交。用一个只含已提交代码的工作树验证：

  ```bash
  git worktree add --detach ../hs-verify HEAD
  ```

  然后在 `web/` 里设 `E5_SERVICE_REPO=../../hs-verify`（服务从那里起）与 `E5_CONTRACTS_DIR=../../hs-verify/contracts/product/v3`
  （页面与契约核对读那里的 schema），再跑 `npm test` 与 `npm run test:e2e`。

## 规矩

* `src/contract/generated/` 由 `npm run contract:gen` 生成，**不要手改**。共享结构由 E3 维护（`src/common/product_contracts.py` →
  `contracts/product/v3/`）；`contract:check` 在 `build` 与 `test` 之前自动执行，结构一变就失败。字段有缺口，写进
  [E5 → E3 交接单](../docs/handoffs/e5-bilingual-map-service-requests.md)，不在前端另加字段。
* 数字一律显示 E3 给的 `display`；不在前端计算 J、均值、比例或范围；不排序城市、不写推荐。
* 浏览器存储只放显示语言与外观；答案、会话与结果都不进浏览器存储。
* 界面自己的文字在 `src/i18n/zh.ts`、`en.ts`；E3 消息里的文字原样显示（英文界面里标明中文原文，等 E3 的显示附层）。
* 底图与地球纹理的来源、许可与处理方法见 `public/geo/SOURCES.md`；重新生成底图：`node scripts/build-basemap.mjs --zh … --en …`。

## 目录

```text
scripts/        contract-gen.mjs、contract-check.mjs（共用 contract-lib.mjs）、build-basemap.mjs（国家底图）
public/geo/     国家底图（TopoJSON，中文 / 英文两种立场）、地球纹理、来源说明
src/contract/   生成的类型、Ajv 校验、样例读取（测试用）、待 E3 的临时文字
src/api/        HTTP 客户端、接口、会话与身份、查询与轮询
src/draft/      状态更新函数、自动保存、草稿编辑器、审阅
src/i18n/       界面字典与业务文字的显示
src/pages/      会话、首页、开局设置、填写步骤、运行意图、结果三层、我的运行、设置
src/components/ 分配器、城市列表、地图与地球、运行前检查、进度、比较图、年度记录
tests/          Vitest；tests/fixtures/drafts/ 是给接入层核对的草稿样本
e2e/            Playwright（隔离服务的配置与启动脚本、各场景）
```
