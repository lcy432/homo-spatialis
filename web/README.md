# web：E5-A 离线界面原型

用户在浏览器里走完“填写 → 确认 → 运行前说明 → 进度 → 结果”。**全部是 E3 的合成公共样例**（`contracts/product/v2/samples/`），没有后台、没有网络接口、没有真实模型。
规则见 [ADR0036](../DECISIONS/0036-e5a-offline-ui.md)，技术说明见 [docs/design/e5-a-offline-ui.md](../docs/design/e5-a-offline-ui.md)。

## 怎样跑

需要 Node 22（只在开发与构建时用）。在 `web/` 目录里：

```bash
npm ci
```

```bash
npm run dev
```

打开 http://localhost:5173/fill/1 。开发模式右上角有“样例面板”（/dev/samples）：切换样例场景、模拟断网与回执丢失。

```bash
npm test
```

```bash
npx playwright install chromium
```

```bash
npm run test:e2e
```

```bash
npm run build
```

## 规矩

* `src/contract/generated/` 由 `npm run contract:gen` 生成，**不要手改**。共享结构由 E3 维护（`src/common/product_contracts.py` → `contracts/product/v2/`）；`npm run contract:check`
  在 `build` 与 `test` 之前自动执行，结构一变就失败。字段有缺口，写进 [给 E3 的接口需求](../docs/handoffs/e5-to-e3-interface-requests.md)，不在前端另加字段。
* 页面只通过 `src/api/types.ts` 的 `ProductApi` 取数据。`SampleAdapter` 只挑选并原样返回样例，不做职业匹配、金额换算、审阅、合计判定或 C / E / M。
* 数字一律显示 E3 给的 `display`；排序、比较、合计都不用显示值；不在前端计算 J、均值、比例或范围。
* 用户写的文字原样保存；答案只存在当前标签页。
* 界面自己写的中文集中在 `src/copy/zh.ts`；E3 消息里的中文原样显示。

## 目录

```text
scripts/        contract-gen.mjs、contract-check.mjs（共用 contract-lib.mjs）
src/contract/   生成的类型、Ajv 校验、样例与 schema 的读取、待 E3 的临时显示文字
src/api/        ProductApi、SampleAdapter、HttpAdapter 空壳、样例场景、网络模拟
src/draft/      草稿状态更新、撤销、精确分数辅助、标签页存储
src/flow/       确认画像、预检查、知悉项、幂等键
src/pages/      四步填写、确认、进度、结果、分身故事、样例面板
src/components/ 分配器、审阅、预检查、结果、分身故事、进度、对话框
tests/          Vitest（单元与组件）
e2e/            Playwright（手机 360×740 与桌面 1280×800；截图、导出的草稿与可访问性报告写进本机的 runs/web-e2e/（runs/ 不进 git），每次覆盖）
tests/fixtures/drafts/  界面生成的草稿样本，接入层用 tests/product/test_web_drafts.py 核对 E3 读到的答案状态
                        （有意改了草稿写法之后：UPDATE_DRAFT_FIXTURES=1 npx vitest run tests/draft-fixtures.test.ts）
```
