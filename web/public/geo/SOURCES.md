# 国家底图的来源

由 `web/scripts/build-basemap.mjs` 生成，不要手改。

* 数据：[Natural Earth](https://www.naturalearthdata.com/) 1:10m Cultural Vectors · Admin 0 – Countries（v5 系列，GeoJSON 取自
  <https://github.com/nvkelso/natural-earth-vector/tree/master/geojson>）。Natural Earth 是公有领域数据，不需要许可。
* 中文界面用中国立场版（point-of-view: CHN，`ne_10m_admin_0_countries_chn`），英文界面用默认版（实际控制线，Natural Earth 的默认）。
* 处理：只保留国家代码（ISO 3166-1 二位码；没有的用 ADM0_A3）与中英名称；TopoJSON 量化 100000，按面积权重简化
  （按球面三角形面积保留约 4% 的顶点，去掉球面面积小于 0.000001 球面度的小岛）。简化只删顶点，不移动保留下来的顶点。
  简化后绕行方向反了的多边形（球面面积超过半球，d3 会当成“除它以外的整个地球”）把环倒过来，表里列出是哪几个。
* 用途：只说明“国家在哪里”，不是模拟范围。城市的显示锚点、都会区身份与支持范围由 E3 提供。

## 地球纹理（`earth-blue-marble-4096.jpg`）

* 来源：NASA Visible Earth · Blue Marble Next Generation w/ Topography and Bathymetry（2004 年 12 月），
  <https://visibleearth.nasa.gov/images/73909>，原图 `world.topo.bathy.200412.3x5400x2700.jpg`
  （SHA-256 `a9f0088972dee0254610af851c4d6838ca3f2cf79176987e0a5713e2c15ec042`）。NASA 影像属于公有领域（引用请注明 NASA）。
* 处理：用 Pillow 缩到 4096 × 2048（LANCZOS，JPEG 质量 85，等距圆柱投影不变），产物 SHA-256
  `6520baa5fd4fc393e56405fea199fcfde49c447d8daa831453cc52bf235c2085`。纹理只作背景，不作地理真值。

| 产物 | 输入 | 输入的 SHA-256 | 输入的国家数 | 简化后还有边界的 | 倒过来的多边形 | 大小 |
|---|---|---|---|---|---|---|
| countries-zh.json | ne_10m_admin_0_countries_chn.geojson | `a13bf5f310fde87bc0a5f994f8ce9bd706cc198d8ee37d221e61c2546b945372` | 246 | 229 | ESB | 289 KB |
| countries-en.json | ne_10m_admin_0_countries.geojson | `239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255` | 258 | 237 | — | 291 KB |
