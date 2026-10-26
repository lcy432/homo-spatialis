// 样例画廊（只在开发构建里有）：用正式页面的同一套组件渲染 E3 的公共样例，检查各种边界情况怎样显示——全 C、含 E、含 M、C = 0、
// 未生成、部分结果、真实六城；运行前检查的通过 / 阻断；各种进度。这里不是用户的数据，不连服务，也不提交任何东西。
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { PageTitle } from "../../components/Layout";
import { PreflightView, ProgressView } from "../../components/RunViews";
import { SAMPLE_IDS, sample, samplePayload } from "../../contract/samples";
import type { GeographyDisplayBundle, ProductCapabilities, ProductPreflight, ProductResult, RunProgress } from "../../contract/types";
import { Zh } from "../../i18n/biz";
import { CityDetail, CloneDetail, ResultsOverview } from "../results/ResultsPages";

const byMessage = (m: string) => SAMPLE_IDS.filter((id) => sample(id).message === m);
const synthCaps = () => samplePayload<ProductCapabilities>("01");

/** 合成资料包的样例用合成能力清单；真实资料包的样例不配能力清单（名称取结果里的标签）。 */
function capsFor(res: { meta: { data_pack: { status: string } } }): ProductCapabilities | null {
  return res.meta.data_pack.status === "synthetic_fixture_only" ? synthCaps() : null;
}

/** 公共地理清单样例（123 真实六城、124 合成资料包）里指纹与这份结果相同的那一份；没有就不画点。 */
function geographyFor(res: ProductResult): GeographyDisplayBundle | null {
  for (const id of ["123", "124"]) {
    if (!SAMPLE_IDS.includes(id)) continue;
    const g = samplePayload<GeographyDisplayBundle>(id);
    if (g.pack_fingerprint === res.meta.data_pack.content_fingerprint) return g;
  }
  return null;
}

export function GalleryIndex() {
  const { t } = useTranslation();
  const groups: [string, string[]][] = [
    ["ProductResult", byMessage("ProductResult")],
    ["ProductPreflight", byMessage("ProductPreflight")],
    ["RunProgress", byMessage("RunProgress")],
  ];
  return (
    <div>
      <PageTitle>{t("dev.gallery")}</PageTitle>
      <p className="box note">{t("dev.galleryNote")}</p>
      {groups.map(([m, ids]) => (
        <section key={m}>
          <h2>{m}</h2>
          <ul className="plain stack">
            {ids.map((id) => (
              <li key={id}>
                <Link className="tap" to={`/dev/gallery/${id}`}>
                  {id}
                </Link>{" "}
                <Zh pointer="/__sample_description__">{sample(id).description_zh}</Zh>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function GallerySample() {
  const { t } = useTranslation();
  const { id = "", cityRef, cloneRef } = useParams();
  if (!SAMPLE_IDS.includes(id)) return <p className="box warn">{t("common.none")}</p>;
  const s = sample(id);
  const base = `/dev/gallery/${id}`;
  const header = (
    <p className="box note" data-gallery={id}>
      {t("dev.gallery")} · {s.file} — <Zh pointer="/__sample_description__">{s.description_zh}</Zh>{" "}
      <Link className="tap" to="/dev/gallery">
        {t("common.back")}
      </Link>
    </p>
  );
  if (s.message === "ProductResult") {
    const res = samplePayload<ProductResult>(id);
    const caps = capsFor(res);
    return (
      <div>
        {header}
        {cloneRef ? (
          <CloneDetail res={res} cloneRef={cloneRef} base={base} />
        ) : cityRef ? (
          <CityDetail res={res} caps={caps} cityRef={cityRef} boundPreflight={null} base={base} />
        ) : (
          <ResultsOverview res={res} caps={caps} settings={null} geography={geographyFor(res)} base={base} />
        )}
      </div>
    );
  }
  if (s.message === "ProductPreflight") {
    const p = samplePayload<ProductPreflight>(id);
    return (
      <div>
        {header}
        <PreflightView preflight={p} caps={capsFor(p)} />
      </div>
    );
  }
  if (s.message === "RunProgress") {
    const p = samplePayload<RunProgress>(id);
    return (
      <div>
        {header}
        <ProgressView progress={p} caps={capsFor(p)} />
      </div>
    );
  }
  return (
    <div>
      {header}
      <pre className="small">{JSON.stringify(s.payload, null, 1).slice(0, 4000)}</pre>
    </div>
  );
}
