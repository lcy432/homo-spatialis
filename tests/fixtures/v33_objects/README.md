# 旧版（v3.3）生成的原样对象

这些文件不是用新版对象改版本号、重算身份做出来的，而是旧版代码真实写下的输出，**不要改写**（改了就失去意义）：

* `draft.json`、`review.json`、`review_display.json`、`profile.json`、`profile_display.json`：提交 `a851db3`（共享结构
  `e3a-product-schema-v3.3`、问卷 v0.3）的托管服务代码路径（HTTP → 业务函数 → worker，合成资料包，离线 mock）为一位合成人物写进
  服务库的草稿、审阅、确认画像与它们的显示绑定（`*_display.json` 绑定的是当时的显示资源 `e3-display-res-v1-a7813e49b0e3fbdc`，
  仍冻结在 `data/display/` 里）。生成脚本是本机证据 `runs/e3-compat-fix-20261025/upgrade_probe.py old`；里面没有任何登录凭证。
  文件末尾的换行是仓库的提交检查加上的（服务库里的正文没有），JSON 内容原样。
* `sample_04_normal_confirmed.json`、`sample_05_normal_preflight.json`、`sample_10_normal_result.json`：`git show a851db3:contracts/product/v3/samples/<同名>.json`
  原样（旧版导出的公共样例）。

用途：`tests/product/test_legacy_objects.py` 核对旧对象读回之后的 JSON、内容身份、草稿指纹与显示绑定的消息摘要都和当初一样
（DECISIONS/0046 的兼容读取），真正改过的旧画像仍被拒绝。
