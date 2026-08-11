import { ProvidersPage } from "./provider-pages";
import { WebFetchSettingsCard } from "./web-fetch-settings-card";

/**
 * Provider 管理页壳层：在页面顶部常驻展示「网页抓取」设置卡片
 * （沙箱抓取开关 + 通道优先级），下方为原有的服务与模型列表。
 */
export function ProvidersPageWithWebFetch() {
  return (
    <>
      <div className="mx-auto w-full max-w-[1180px] px-5 pt-5 sm:px-7">
        <WebFetchSettingsCard />
      </div>
      <ProvidersPage />
    </>
  );
}
