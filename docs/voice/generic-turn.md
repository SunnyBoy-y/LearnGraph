# 通用 TURN 语音中继

系统管理员可在「设置 → 语音 → 语音中继」选择「通用 TURN」，配置自建 coturn 或第三方 TURN 服务。填写服务地址、用户名、密码后，点击「保存并测试连接」。配置全系统共享；保存后新建的 WebRTC 通话使用新配置，已有通话不重连。WebSocket 语音通道不使用 ICE/TURN。

地址每行一个，可填写 UDP、TCP 或 TLS，最多 16 个；保存时去重且保留顺序。服务端当前只使用第一个 TURN 地址，浏览器收到全部地址。测试会逐条申请中继候选，总体结果以首个地址为准；若次选地址可用而首选失败，请调整地址顺序后保存。

密码由现有主密钥机制加密写入 `voice_relay_secrets`，管理接口仅回显掩码。编辑时密码留空表示沿用同一类型的已保存凭据；在 Cloudflare 和通用 TURN 之间切换时必须填写对应的新 Token 或密码。手动 STUN 模式保留上一次凭据及其类型，但不会解密或下发该凭据。停用和删除操作会立即清除运行时缓存。

实际服务地址和凭据应通过管理界面或认证接口录入运行数据库，不要放入源码、测试、示例配置或提交记录。以下仅为占位示例。

## 管理接口

沿用现有管理员接口 `PUT /api/v1/voice/relay`（路径以部署 API 前缀为准）：

```json
{
  "mode": "turn",
  "urls": [
    "turn:relay.example.com:3478?transport=udp",
    "turn:relay.example.com:3478?transport=tcp",
    "turns:relay.example.com:5349?transport=tcp"
  ],
  "username": "your-turn-username",
  "secret": "your-turn-password",
  "stun_urls": [],
  "stun_fallback_enabled": false
}
```

- `urls`：TURN 地址数组，也支持换行或逗号分隔的字符串。仅接受 `turn:` / `turns:`，用户名和密码请用独立字段传递。
- `username`：TURN 用户名。
- `secret`：TURN 密码；后续编辑可省略或留空以保留已保存的 TURN 密码。密码两端空格会原样保留。
- `stun_urls` / `stun_fallback_enabled`：可选的独立浏览器 STUN 地址池；不转发媒体，不代表 TURN 可用。

`GET /api/v1/voice/relay` 返回 `username`、`secret_mode`、`secret_configured` 和掩码等配置元数据，不返回密码。`secret_mode` 标识保留凭据的类型（`turn` 或 `cloudflare`），在手动 STUN 模式下也保持有效。

`POST /api/v1/voice/relay/test` 接受同样的配置字段，支持测试尚未保存的草稿，也可提交 `{}` 测试当前配置。草稿测试不会保存密码或覆盖正在使用的配置。只有与当前配置完全匹配的测试才会更新持久化状态。

`GET /api/v1/voice/ice-servers` 将完整 ICE 配置下发给已登录用户，静态 TURN 密码会作为建立连接所必需的 `credential` 下发，响应带有 `Cache-Control: no-store`。此模式使用 TURN 服务提供的现有密码，不会调用 Cloudflare 签发或自动刷新密码。

升级启动时会自动应用 `v1.20.0` 的增量迁移，补充用户名与凭据类型字段；已有 Cloudflare 凭据默认仍标记为 `cloudflare`。
