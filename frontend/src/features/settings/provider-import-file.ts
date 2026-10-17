// File credentials live only in component memory. Server validation and
// duplicate detection remain authoritative when importing the selection.
type JsonObject = Record<string, unknown>;
export interface FileConnection {
  id: string;
  name: string;
  base_url: string;
  reason?: string;
  config: JsonObject;
}
function object(value: unknown): JsonObject {
  if (typeof value === 'string') {
    try { return object(JSON.parse(value)); } catch { return {}; }
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}
function text(...values: unknown[]): string {
  return values.find((value): value is string => typeof value === 'string' && Boolean(value.trim()))?.trim() ?? '';
}
export function parseConnectionFile(content: string): FileConnection[] {
  let root: JsonObject;
  try { root = object(JSON.parse(content.replace(/^\uFEFF/, ''))); }
  catch { throw new Error('文件不是有效的 JSON 配置'); }
  const persist = object(root['persist:cherry-studio'] ?? object(root.localStorage)['persist:cherry-studio']);
  const llm = object(root.llm ?? persist.llm);
  const raw = root.providers ?? llm.providers;
  const entries = Array.isArray(raw) ? raw : Object.values(object(raw));
  if (!entries.length) throw new Error('未找到连接，请选择 CC Switch 或 Cherry Studio 导出的 JSON 配置');
  if (entries.length > 200) throw new Error('单次最多导入 200 项连接，请拆分配置文件');
  const identities = new Set<string>();
  return entries.map((value, index) => {
    const item = object(value);
    const settings = object(item.settingsConfig ?? item.settings_config ?? item);
    const env = object(settings.env);
    const auth = object(settings.auth ?? settings.authContents);
    const config = object(settings.config ?? settings.configContents);
    const modelProviders = object(config.model_providers);
    const modelProvider = object(modelProviders[text(config.model_provider)] ?? (Object.keys(modelProviders).length === 1 ? Object.values(modelProviders)[0] : undefined));
    const anthropic = item.app_type === 'claude' || item.app_type === 'anthropic' || item.type === 'anthropic'
      || Boolean(env.ANTHROPIC_BASE_URL || env.ANTHROPIC_API_KEY || env.ANTHROPIC_AUTH_TOKEN);
    const key = anthropic
      ? text(env.ANTHROPIC_AUTH_TOKEN, env.ANTHROPIC_API_KEY, settings.apiKey)
      : text(modelProvider.experimental_bearer_token, auth.OPENAI_API_KEY, env.OPENAI_API_KEY, settings.apiKey);
    const address = anthropic
      ? text(env.ANTHROPIC_BASE_URL, settings.apiHost, settings.baseUrl)
      : text(modelProvider.base_url, env.OPENAI_BASE_URL, settings.apiHost, settings.baseUrl);
    let url = '';
    let reason: string | undefined;
    try {
      const parsed = new URL(address);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || address.length > 500) throw new Error();
      url = parsed.href.replace(/\/+$/, '');
    } catch { reason = address ? '地址格式不支持' : '缺少 Base URL'; }
    if (!reason && (!key || /^(PROXY_MANAGED|PLACEHOLDER|YOUR_API_KEY|DUMMY)$/i.test(key))) reason = '缺少可用的 API Key';
    if (!reason && key.length > 32_768) reason = '凭据长度超过限制';
    if (typeof (settings.config ?? settings.configContents) === 'string') reason = '请使用 JSON 格式的连接配置';
    const wire = modelProvider.wire_api;
    if (wire !== undefined && wire !== 'responses' && wire !== 'chat') reason = '暂不支持此连接类型';
    if (item.type && !['openai', 'anthropic', 'openai-response', 'openai-responses'].includes(String(item.type))) reason = '暂不支持此连接类型';
    const responses = wire === 'responses' || item.type === 'openai-response' || item.type === 'openai-responses' || (item.app_type === 'codex' && wire === undefined);
    const identity = JSON.stringify([anthropic, responses, url, key]);
    if (!reason && identities.has(identity)) reason = '文件中存在相同连接';
    if (!reason) identities.add(identity);
    const name = text(item.name, item.label).slice(0, 140) || `连接 ${index + 1}`;
    return {
      id: `file-${index}`, name, base_url: url, reason,
      config: { name, app_type: anthropic ? 'claude' : responses ? 'codex' : '', settingsConfig: {
        env: anthropic ? { ANTHROPIC_BASE_URL: url, ANTHROPIC_AUTH_TOKEN: key } : { OPENAI_BASE_URL: url, OPENAI_API_KEY: key },
        model: text(settings.model, config.model, env.ANTHROPIC_MODEL),
        ...(responses ? { config: { model: config.model, model_provider: 'imported', model_providers: { imported: { base_url: url, wire_api: 'responses' } } } } : {}),
      } },
    };
  });
}
