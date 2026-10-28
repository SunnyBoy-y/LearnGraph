# Duplex voice over WebSocket

## Architecture

The browser opens one same-origin WebSocket connection for both microphone
audio and assistant audio. On an HTTPS page its URL is `wss://`; on local HTTP
development it is `ws://`. This path does not create an RTCPeerConnection,
perform ICE, or contact STUN/TURN.

The socket terminates at the existing API:

```
Browser microphone
  -> AudioWorklet -> Opus/PCM -> WebSocket
  -> Pipecat FastAPIWebsocketTransport -> existing run_bot
  -> existing ASR / Agent / TTS / generation gate
  -> WebSocket -> Opus/PCM decoder -> browser playback
```

The input/output transports change, not the business pipeline. RTVI envelopes
are sent as text messages over the same socket instead of a WebRTC data
channel. Caption events, typed voice turns, interruption, task results, durable
replay, ownership leases, and peer-generation fencing keep their existing paths.
Audio is decoded to mono PCM inside Pipecat; no internal WebRTC hop is needed.

## Selection and rollback

- Install the existing backend `voice` extra; no new codec package is required.
- Session create/read responses advertise `websocket_url` for the embedded
  runtime. New frontend connections prefer that URL when available.
- Opus is used only if both browser AudioEncoder and AudioDecoder report
  support. Otherwise the connection negotiates PCM.
- Set `LEARNGRAPH_VOICE_WSS_ENABLED=false` and restart the API to disable WSS.
  The Docker Compose environment forwards this setting.
- Alternatively rebuild the frontend with `VITE_VOICE_TRANSPORT=webrtc`.
- The existing offer/ICE routes remain intact for older clients and rollback.
  An advertised WSS connection does not silently switch back to a TURN path
  on failure; normal bounded reconnect stays on WSS.
- No production service is restarted by the implementation or probe scripts.

The WebSocket URL is same-origin. Cross-origin API deployments must publish
this path through the frontend's reverse proxy as well. Vite's existing `/api`
proxy already has `ws: true`. A reverse proxy must support WebSocket Upgrade
and an idle timeout longer than the 15-second client heartbeat. TLS should
terminate at the public entry point. The backend port need not be public.

If the public entry point itself still depends on an unreliable Cloudflare
path, WSS does not remove that dependency; it removes the separate TURN/ICE
dependency. Verify the actual entry path from the user's network.

## Wire protocol and limits

Endpoint: `/api/v1/voice/sessions/{voice_session_id}/api/audio`.

The first text frame is `voice.start`, protocol version `1`, with the session
token, workspace id, codec (`opus` or `pcm`), and reconnect flag. Tokens are
never put into query strings. Identity/revocation, password-change policy,
workspace permissions/maintenance state, voice-session owner, and runtime
lease are checked before a runner is allocated. Browser Origin is checked.

The server sends `voice.ready` before media. Subsequent text messages carry
the unchanged RTVI objects, `voice.ping/pong`, or `voice.clear`.

Binary audio has a 16-byte big-endian header:

- Bytes 0-1: ASCII `LG`.
- Byte 2: protocol version `1`.
- Byte 3: codec `1` (Opus) or `2` (PCM).
- Bytes 4-7: connection-local audio sequence.
- Bytes 8-11: capture timestamp in milliseconds, modulo 2^32.
- Bytes 12-15: assistant playback generation (zero for microphone input).

Opus payloads are raw packets preceded by a 16-bit big-endian length, not
WebM/Ogg recordings. Uplink packets contain one 20 ms Opus frame. PCM fallback
uses signed little-endian 16-bit mono, 16 kHz uplink and 24 kHz downlink.
The requested Opus bitrate is 32 kbit/s and its sample rate is 48 kHz.

Messages are limited to 16 KiB by the application. Handshakes have an 8 KiB
limit and 10-second timeout. Writers have a 2-second send timeout, readers a
45-second inactivity timeout. Client encoder/send/playback queues are bounded.
Stale microphone frames are dropped; persistent staleness forces reconnect.
Interruption increments the server playback generation and clears scheduled
browser audio; queued older-generation audio is ignored.

Reconnect reuses the existing durable voice session and event cursor. It
does not replay buffered microphone audio or automatically replay tool calls.
This does not promise exactly-once external tool execution.

## Local verification and preview

The optional, loopback-only probe replaces ASR/Agent/TTS with a deterministic
audio echo. It uses the production codec serializer, socket IO wrapper, and
Pipecat input/output transports. It has no business database or model calls.

From `backend`:

```
.\.venv\Scripts\python.exe -m scripts.voice_wss_probe_server
```

It listens only on `127.0.0.1:18768`. Serve
`frontend/voice-wss-preview.html` through a development server whose `/api`
WebSocket proxy points to that port. The normal production build has only
`index.html` as an entry; this diagnostic HTML is not a production app route.
Start with the generated test tone; choose Microphone explicitly for device
testing. Monitor is off by default to avoid speaker feedback.

Unit/API tests cover codecs, protocol limits, control envelopes, auth scope,
Origin, rollback, fencing, interruption, and heartbeat. Browser echo tests
must independently check native Opus and forced PCM, a nonzero decoded signal,
continued media after a heartbeat, and cleanup after Stop.

Local loopback on HTTP does not validate a deployed TLS certificate, the
public reverse proxy, carrier networks, physical microphone echo cancellation,
or the real ASR/Agent/TTS provider path. These still require target-environment
acceptance, including Wi-Fi/mobile network switching.

The implementation verification passed 296 frontend voice tests and 58 focused
backend tests. A broader backend run also found four failures in existing
voice-foundation assertions about memory-job counts; all four reproduced with
WSS explicitly disabled. They are not counted as passing and were left outside
this transport change.
