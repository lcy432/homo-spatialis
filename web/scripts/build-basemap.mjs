// 生成界面用的国家底图（TopoJSON），只在换数据源或简化参数时手工运行一次，产物提交进 web/public/geo/。
//
//   node scripts/build-basemap.mjs --zh <ne_10m_admin_0_countries_chn.geojson> --en <ne_10m_admin_0_countries.geojson>
//
// 数据：Natural Earth 1:10m Admin 0 – Countries（公有领域）。中文界面用中国立场版（point-of-view: CHN），英文界面用默认版
// （实际控制线，Natural Earth 的默认）。只保留国家代码与中英名称；几何按面积权重简化（不改变任何保留下来的顶点的位置）。
// 底图只说明“国家在哪里”，不是模拟范围；城市的显示锚点、都会区身份与支持范围由 E3 提供，不在这里。
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { geoArea } from "d3-geo";
import { feature } from "topojson-client";
import { topology } from "topojson-server";
import { filter, filterWeight, presimplify, quantile, simplify, sphericalRingArea, sphericalTriangleArea } from "topojson-simplify";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "..", "public", "geo");
const args = process.argv.slice(2);
const arg = (name) => {
  const i = args.indexOf(name);
  if (i < 0 || !args[i + 1]) throw new Error(`缺少 ${name} <路径>`);
  return path.resolve(args[i + 1]);
};

/**
 * 简化：在没有量化的经纬度上按球面三角形面积给每个顶点算权重，只保留权重最大的约 KEEP_FRACTION 的顶点
 * （topojson-simplify 的 quantile(p) 返回“保留约 p 比例的点”的门槛）；球面面积小于 MIN_RING_SR 的小岛去掉。
 */
const KEEP_FRACTION = 0.04;
const MIN_RING_SR = 1e-6;
const QUANTIZATION = 1e5;

/** 简化之后每个点带着权重（第三维）：去掉它，再重新量化与压缩编码。 */
function strip(coords) {
  return typeof coords[0] === "number" ? [coords[0], coords[1]] : coords.map(strip);
}

/**
 * d3 按绕行方向判断多边形的内外。很小的多边形简化之后绕行方向可能反过来，d3 就会把它当成“除了这一小块以外的整个地球”
 * （2026-10-24 中文版的 ESB 就是这样：平面地图上它的路径铺满整个投影平面）。球面面积超过半球的多边形，把它的每个环倒过来。
 */
function rewindInverted(f) {
  const g = f.geometry;
  const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
  let n = 0;
  for (const poly of polys) {
    if (geoArea({ type: "Polygon", coordinates: poly }) > 2 * Math.PI) {
      for (const ring of poly) ring.reverse();
      n += 1;
    }
  }
  return n;
}

function iso2(p) {
  for (const k of ["ISO_A2_EH", "ISO_A2", "WB_A2"]) {
    const v = p[k];
    if (typeof v === "string" && /^[A-Z]{2}$/.test(v)) return v;
  }
  return null;
}

function build(file, label) {
  const bytes = readFileSync(file);
  const geo = JSON.parse(bytes.toString("utf8"));
  const features = geo.features.map((f) => ({
    type: "Feature",
    id: iso2(f.properties) ?? f.properties.ADM0_A3,
    properties: {
      iso2: iso2(f.properties),
      a3: f.properties.ADM0_A3,
      name_zh: f.properties.NAME_ZH ?? null,
      name_en: f.properties.NAME_EN ?? f.properties.NAME ?? null,
    },
    geometry: f.geometry,
  }));
  let topo = topology({ countries: { type: "FeatureCollection", features } });
  topo = presimplify(topo, sphericalTriangleArea);
  topo = filter(topo, filterWeight(topo, MIN_RING_SR, sphericalRingArea));
  topo = simplify(topo, quantile(topo, KEEP_FRACTION));
  const simplified = feature(topo, topo.objects.countries);
  const rewound = [];
  for (const f of simplified.features) {
    if (f.geometry) f.geometry.coordinates = strip(f.geometry.coordinates);
    if (rewindInverted(f)) rewound.push(f.properties.iso2 ?? f.properties.a3);
  }
  const out_topo = topology({ countries: simplified }, QUANTIZATION);
  const text = JSON.stringify(out_topo);
  const out = path.join(OUT, `countries-${label}.json`);
  writeFileSync(out, `${text}\n`, "utf8");
  return {
    label,
    input: path.basename(file),
    input_sha256: createHash("sha256").update(bytes).digest("hex"),
    output: path.basename(out),
    output_bytes: Buffer.byteLength(text),
    countries: features.length,
    with_borders: simplified.features.filter((f) => f.geometry).length,
    rewound,
  };
}

mkdirSync(OUT, { recursive: true });
const zh = build(arg("--zh"), "zh");
const en = build(arg("--en"), "en");
const rows = [zh, en]
  .map(
    (r) =>
      `| ${r.output} | ${r.input} | \`${r.input_sha256}\` | ${r.countries} | ${r.with_borders} | ${r.rewound.join("、") || "—"} | ${(r.output_bytes / 1024).toFixed(0)} KB |`,
  )
  .join("\n");
writeFileSync(
  path.join(OUT, "SOURCES.md"),
  `# 国家底图的来源

由 \`web/scripts/build-basemap.mjs\` 生成，不要手改。

* 数据：[Natural Earth](https://www.naturalearthdata.com/) 1:10m Cultural Vectors · Admin 0 – Countries（v5 系列，GeoJSON 取自
  <https://github.com/nvkelso/natural-earth-vector/tree/master/geojson>）。Natural Earth 是公有领域数据，不需要许可。
* 中文界面用中国立场版（point-of-view: CHN，\`ne_10m_admin_0_countries_chn\`），英文界面用默认版（实际控制线，Natural Earth 的默认）。
* 处理：只保留国家代码（ISO 3166-1 二位码；没有的用 ADM0_A3）与中英名称；TopoJSON 量化 ${QUANTIZATION}，按面积权重简化
  （按球面三角形面积保留约 ${KEEP_FRACTION * 100}% 的顶点，去掉球面面积小于 ${MIN_RING_SR} 球面度的小岛）。简化只删顶点，不移动保留下来的顶点。
  简化后绕行方向反了的多边形（球面面积超过半球，d3 会当成“除它以外的整个地球”）把环倒过来，表里列出是哪几个。
* 用途：只说明“国家在哪里”，不是模拟范围。城市的显示锚点、都会区身份与支持范围由 E3 提供。

## 地球纹理（\`earth-blue-marble-4096.jpg\`）

* 来源：NASA Visible Earth · Blue Marble Next Generation w/ Topography and Bathymetry（2004 年 12 月），
  <https://visibleearth.nasa.gov/images/73909>，原图 \`world.topo.bathy.200412.3x5400x2700.jpg\`
  （SHA-256 \`a9f0088972dee0254610af851c4d6838ca3f2cf79176987e0a5713e2c15ec042\`）。NASA 影像属于公有领域（引用请注明 NASA）。
* 处理：用 Pillow 缩到 4096 × 2048（LANCZOS，JPEG 质量 85，等距圆柱投影不变），产物 SHA-256
  \`6520baa5fd4fc393e56405fea199fcfde49c447d8daa831453cc52bf235c2085\`。纹理只作背景，不作地理真值。

| 产物 | 输入 | 输入的 SHA-256 | 输入的国家数 | 简化后还有边界的 | 倒过来的多边形 | 大小 |
|---|---|---|---|---|---|---|
${rows}
`,
  "utf8",
);
console.log(JSON.stringify([zh, en], null, 1));
