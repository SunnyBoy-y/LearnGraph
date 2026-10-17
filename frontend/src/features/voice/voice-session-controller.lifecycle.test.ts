import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), baseUrl: '/api/v1' }));
const voice = vi.hoisted(() => ({ getVoiceCapabilities: vi.fn(), getVoiceIceServers: vi.fn(), getVoiceSession: vi.fn(), getVoiceTask: vi.fn(), startVoiceTask: vi.fn(), cancelVoiceTask: vi.fn(), approveVoiceTask: vi.fn(), reviseVoiceTask: vi.fn(), deliverVoiceResult: vi.fn() }));
const noise = vi.hoisted(() => ({ createVoiceNoiseInput: vi.fn() }));
vi.mock('@/api/client', () => ({ apiClient: api, ApiError: class ApiError extends Error {} }));
vi.mock('@/api/voice', () => voice);
vi.mock('./rnnoise/input', () => ({ ...noise, idleNoiseStatus: { mode: 'idle' } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
class Peer {
  static instances: Peer[] = [];
  connectionState = 'connected';
  iceConnectionState = 'connected';
  onconnectionstatechange: (() => void) | null = null;
  oniceconnectionstatechange: (() => void) | null = null;
  onicecandidate: unknown = null;
  ontrack: ((event: { streams: MediaStream[] }) => void) | null = null;
  channel = { readyState: 'open', onmessage: null as ((event: { data: string }) => void) | null, onopen: null as (() => void) | null, send: vi.fn(), close: vi.fn() };
  close = vi.fn();
  restartIce = vi.fn();
  addTrack = vi.fn();
  createOffer = vi.fn().mockResolvedValue({ sdp: 'offer', type: 'offer' });
  setLocalDescription = vi.fn().mockResolvedValue(undefined);
  setRemoteDescription = vi.fn().mockResolvedValue(undefined);
  createDataChannel() { return this.channel; }
  constructor() { Peer.instances.push(this); }
  state(state: string) { this.connectionState = state; this.iceConnectionState = state; this.onconnectionstatechange?.(); this.oniceconnectionstatechange?.(); }
  event(type: string, session = 'remote-A', seq = 1, payload: Record<string, unknown> = {}) {
    this.channel.onmessage?.({ data: JSON.stringify({ label: 'rtvi-ai', type: 'server-message', data: { event: { event_id: `${session}-${seq}`, event_seq: seq, session_id: session, session_epoch: 1, type, payload } } }) });
  }
}
function media() {
  const track = { stop: vi.fn(), enabled: true };
  return { track, stream: { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream };
}
let controller: typeof import('./voice-session-controller')['voiceSessionController'];
let mic: ReturnType<typeof media>;
let getUserMedia: ReturnType<typeof vi.fn>;
const caps = { agent_capabilities: [], voice_provider_ready: true };
const remote = (id: string) => ({ id, runtime_ready: true, signaling_url: '/offer', session_epoch: 1 });

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetModules();
  vi.clearAllMocks();
  for (const fn of Object.values(voice)) fn.mockReset();
  api.get.mockReset(); api.post.mockReset(); api.patch.mockReset(); api.delete.mockReset();
  localStorage.clear();
  Peer.instances = [];
  mic = media();
  noise.createVoiceNoiseInput.mockReset();
  noise.createVoiceNoiseInput.mockImplementation(async (stream: MediaStream) => ({ stream, close: vi.fn() }));
  getUserMedia = vi.fn().mockResolvedValue(mic.stream);
  vi.stubGlobal('RTCPeerConnection', Peer);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  voice.getVoiceCapabilities.mockResolvedValue(caps);
  voice.getVoiceIceServers.mockResolvedValue({ iceServers: [] });
  voice.getVoiceSession.mockResolvedValue({ tasks: [], results: [] });
  voice.getVoiceTask.mockResolvedValue({ task_id: 'task-A', status: 'running', events: [] });
  api.get.mockImplementation(async (path: string) => path.includes('/events?') ? { events: [] } : path.includes('/transcript?') ? { turns: [] } : remote(path.split('/').pop()!));
  api.post.mockImplementation(async (path: string, data: Record<string, unknown>) => path === '/voice/sessions' ? remote(`remote-${data.session_id}`) : path === '/offer' ? { sdp: 'answer', type: 'answer', pc_id: 'pc' } : {});
  api.patch.mockResolvedValue({}); api.delete.mockResolvedValue({});
  controller = (await import('./voice-session-controller')).voiceSessionController;
  controller.open('workspace', 'A');
  await flush();
});
afterEach(() => { controller?.disconnect(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('voice connection ownership', () => {
  it('ends scene focus with its playback turn and ignores a stale focus replay', async () => {
    await controller.connect(); await flush();
    controller.upsertTask({taskId:'task-A',title:'result',status:'ready'});
    const peer = Peer.instances[0];
    peer.event('scene.focus','remote-A',10,{task_id:'task-A',anchor_id:'visual-table',turn_id:'explain'});
    expect(controller.getSnapshot().tasks[0].focusId).toBe('remote-A-10');
    peer.event('turn.finalized','remote-A',11,{turn_id:'older',role:'assistant'});
    expect(controller.getSnapshot().tasks[0].focusId).toBe('remote-A-10');
    peer.event('turn.finalized','remote-A',12,{turn_id:'explain',role:'assistant'});
    expect(controller.getSnapshot().tasks[0].focusId).toBeUndefined();
    peer.event('scene.focus','remote-A',13,{task_id:'task-A',anchor_id:'visual-table',turn_id:'explain'});
    expect(controller.getSnapshot().tasks[0].focusId).toBeUndefined();
  });
  it('publishes the denoised track and releases both capture and processing on disconnect', async () => {
    const processed = media(); const closeNoise = vi.fn();
    noise.createVoiceNoiseInput.mockResolvedValueOnce({ stream: processed.stream, close: closeNoise });
    await controller.connect();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: {
      channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 },
      echoCancellation: true, noiseSuppression: true, autoGainControl: false,
    } });
    expect(Peer.instances[0].addTrack).toHaveBeenCalledWith(processed.track, processed.stream);
    expect(Peer.instances[0].addTrack).not.toHaveBeenCalledWith(mic.track, mic.stream);
    controller.setMuted(true);
    expect(mic.track.enabled).toBe(false);
    expect(processed.track.enabled).toBe(false);
    controller.setMuted(false);
    expect(mic.track.enabled).toBe(true);
    expect(processed.track.enabled).toBe(true);
    controller.disconnect();
    expect(closeNoise).toHaveBeenCalled();
    expect(mic.track.stop).toHaveBeenCalled();
    expect(processed.track.stop).toHaveBeenCalled();
  });

  it('closes a late noise graph after a session switch without publishing its track', async () => {
    const gate = deferred<{ stream: MediaStream; close: () => void }>();
    const processed = media(); const closeNoise = vi.fn();
    noise.createVoiceNoiseInput.mockReturnValueOnce(gate.promise);
    const pending = controller.connect(); await flush();
    controller.open('workspace', 'B');
    gate.resolve({ stream: processed.stream, close: closeNoise }); await pending;
    expect(closeNoise).toHaveBeenCalled();
    expect(Peer.instances[0].addTrack).not.toHaveBeenCalled();
    expect(controller.getSnapshot().sessionId).toBe('B');
  });

  it('does not let a reconnect journal replay demote a healthy live peer', async () => {
    await controller.connect(); await flush();
    Peer.instances[0].event('session.reconnecting');
    expect(controller.getSnapshot().transport).toBe('connected');
    Peer.instances[0].state('disconnected');
    Peer.instances[0].event('session.reconnecting', 'remote-A', 2);
    expect(controller.getSnapshot().transport).toBe('reconnecting');
  });
  it.each(['approveTask', 'cancelTask'] as const)('releases %s locks during a same-remote reconnect', async (method) => {
    await controller.connect();
    const old = deferred<Record<string, unknown>>();
    const request = method === 'approveTask' ? voice.approveVoiceTask : voice.cancelVoiceTask;
    request.mockReturnValueOnce(old.promise);
    const pending = controller[method]('task-A');
    Peer.instances[0].state('failed');
    await vi.advanceTimersByTimeAsync(1000);
    expect(Peer.instances).toHaveLength(2);
    expect(controller.getSnapshot().busyTaskIds).toEqual([]);
    request.mockResolvedValueOnce({ task_id: 'task-A', status: 'running' });
    expect(await controller[method]('task-A')).toBe(true);
    expect(request).toHaveBeenCalledTimes(2);
    old.resolve({ task_id: 'old', status: 'running' });
    expect(await pending).toBe(false);
    expect(controller.getSnapshot().tasks.some((t) => t.taskId === 'old')).toBe(false);
  });
  it('resumes both poll loops if a ready replay arrives during disconnection', async () => {
    await controller.connect(); await flush();
    controller.upsertTask({ taskId: 'task-A', title: 'task', status: 'running' });
    const gate = deferred<{ events: unknown[] }>(); const original = api.get.getMockImplementation()!;
    let held = false;
    api.get.mockImplementation((path: string) => {
      if (path.includes('/events?') && !held) { held = true; return gate.promise; }
      return original(path);
    });
    await vi.advanceTimersByTimeAsync(1500);
    const peer = Peer.instances[0]; peer.state('disconnected');
    gate.resolve({ events: [{ event_id: 'ready-late', event_seq: 1, session_id: 'remote-A', type: 'session.ready' }] }); await flush();
    expect(controller.getSnapshot().transport).toBe('reconnecting');
    peer.state('connected'); api.get.mockClear(); voice.getVoiceTask.mockClear();
    await vi.advanceTimersByTimeAsync(3200);
    expect(api.get.mock.calls.filter(([p]) => String(p).includes('/events?'))).toHaveLength(2);
    expect(voice.getVoiceTask.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
  it('starts a fresh remote cursor at zero after a high-cursor call', async () => {
    await controller.connect(); await flush(); Peer.instances[0].event('session.created', 'remote-A', 100);
    expect(controller.getSnapshot().lastEventSeq).toBe(100); controller.disconnect();
    const original = api.post.getMockImplementation()!;
    api.post.mockImplementation((path: string, data: Record<string, unknown>) => path === '/voice/sessions' ? Promise.resolve(remote('remote-new')) : original(path, data));
    api.get.mockClear(); await controller.connect(); await flush();
    expect(api.get).toHaveBeenCalledWith('/voice/sessions/remote-new/events?after_event_seq=0');
    Peer.instances[1].event('session.created', 'remote-new', 1);
    expect(controller.getSnapshot().lastEventSeq).toBe(1);
  });
  it('resets cursor when restoring an expired remote falls back to creation', async () => {
    await controller.connect(); await flush(); Peer.instances[0].event('session.created', 'remote-A', 100);
    const { ApiError } = await import('@/api/client');
    const missing = Object.assign(Object.create(ApiError.prototype), { status: 404, message: 'expired' });
    const original = api.get.getMockImplementation()!;
    api.get.mockImplementation((path: string) => path === '/voice/sessions/remote-A' ? Promise.reject(missing) : original(path));
    Peer.instances[0].state('failed'); await vi.advanceTimersByTimeAsync(1000);
    expect(controller.getSnapshot().lastEventSeq).toBe(0);
    const create = api.post.mock.calls.filter(([path]) => path === '/voice/sessions').at(-1)!;
    expect(create[1]).toMatchObject({ after_event_seq: 0, last_event_seq: 0 });
  });
  it('does not repeat a failed old DELETE when a new call reuses that remote', async () => {
    const gate = deferred<MediaStream>(); const late = media(); const next = media();
    getUserMedia.mockReturnValueOnce(gate.promise).mockResolvedValueOnce(next.stream);
    const pending = controller.connect(); await flush();
    api.delete.mockRejectedValueOnce(new Error('offline')); controller.disconnect();
    await controller.connect();
    const newPeer = Peer.instances[1]; expect(controller.getSnapshot().sessionIdRemote).toBe('remote-A');
    gate.resolve(late.stream); await pending;
    expect(api.delete).toHaveBeenCalledTimes(1);
    expect(newPeer.close).not.toHaveBeenCalled(); expect(next.track.stop).not.toHaveBeenCalled();
    expect(late.track.stop).toHaveBeenCalled();
  });
  it('locks before discovery and does not resurrect after cancellation', async () => {
    const gate = deferred<typeof caps>();
    voice.getVoiceCapabilities.mockReturnValueOnce(gate.promise);
    const pending = controller.connect();
    expect(controller.getSnapshot().transport).toBe('connecting');
    await controller.connect();
    controller.disconnect();
    gate.resolve(caps); await pending;
    expect(controller.getSnapshot().transport).toBe('idle');
    expect(api.post).not.toHaveBeenCalled();
    expect(getUserMedia).not.toHaveBeenCalled();
  });
  it('closes a session whose creation returns after cancellation', async () => {
    const gate = deferred<ReturnType<typeof remote>>();
    api.post.mockReturnValueOnce(gate.promise);
    const pending = controller.connect(); await flush();
    controller.disconnect(); gate.resolve(remote('late')); await pending;
    expect(api.delete).toHaveBeenCalledWith('/voice/sessions/late');
    expect(Peer.instances).toHaveLength(0);
  });
  it('cancels during ICE loading before opening microphone', async () => {
    const gate = deferred<{ iceServers: [] }>();
    voice.getVoiceIceServers.mockReturnValueOnce(gate.promise);
    const pending = controller.connect(); await flush();
    controller.disconnect(); gate.resolve({ iceServers: [] }); await pending;
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(api.delete).toHaveBeenCalledWith('/voice/sessions/remote-A');
    expect(controller.getSnapshot().transport).toBe('idle');
  });
  it('stops a late microphone without clearing a newer connection', async () => {
    const gate = deferred<MediaStream>();
    const late = media(); const next = media();
    getUserMedia.mockReturnValueOnce(gate.promise).mockResolvedValueOnce(next.stream);
    const pending = controller.connect(); await flush();
    const oldPeer = Peer.instances[0];
    controller.open('workspace', 'B'); await controller.connect();
    const newPeer = Peer.instances[1];
    gate.resolve(late.stream); await pending;
    expect(late.track.stop).toHaveBeenCalled();
    expect(oldPeer.addTrack).not.toHaveBeenCalled();
    expect(newPeer.close).not.toHaveBeenCalled();
    expect(next.track.stop).not.toHaveBeenCalled();
    expect(controller.getSnapshot()).toMatchObject({ sessionId: 'B', transport: 'connected' });
  });
  it.each(['resolve', 'reject'])('ignores an old offer %s after a new call connects', async (mode) => {
    const gate = deferred<{ sdp: string; type: string }>();
    const original = api.post.getMockImplementation()!;
    let held = false;
    api.post.mockImplementation((path: string, data: Record<string, unknown>) => {
      if (path === '/offer' && !held) { held = true; return gate.promise; }
      return original(path, data);
    });
    const pending = controller.connect(); await flush();
    const oldPeer = Peer.instances[0];
    controller.open('workspace', 'B');
    const next = media(); getUserMedia.mockResolvedValue(next.stream);
    await controller.connect(); const newPeer = Peer.instances[1];
    if (mode === 'reject') gate.reject(new Error('old failure')); else gate.resolve({ sdp: 'late', type: 'answer' });
    await pending;
    expect(oldPeer.setRemoteDescription).not.toHaveBeenCalled();
    expect(newPeer.close).not.toHaveBeenCalled();
    expect(next.track.stop).not.toHaveBeenCalled();
    expect(controller.getSnapshot()).toMatchObject({ sessionId: 'B', transport: 'connected', error: null });
  });
  it('ends A before switching to B and ignores queued A channel events', async () => {
    await controller.connect(); const oldPeer = Peer.instances[0]; const queued = oldPeer.channel.onmessage!;
    controller.open('workspace', 'B');
    expect(mic.track.stop).toHaveBeenCalled();
    expect(oldPeer.close).toHaveBeenCalled();
    queued({ data: JSON.stringify({ label: 'rtvi-ai', type: 'bot-llm-started', data: {} }) });
    expect(controller.getSnapshot()).toMatchObject({ sessionId: 'B', state: 'ready', tasks: [], streamingAssistantText: '' });
  });
  it('ignores a mismatched session before advancing the cursor', async () => {
    await controller.connect(); Peer.instances[0].event('session.closed', 'other', 999);
    expect(controller.getSnapshot()).toMatchObject({ transport: 'connected', lastEventSeq: 0 });
    expect(mic.track.stop).not.toHaveBeenCalled();
  });
  it('releases local media on remote close and cancels all polling', async () => {
    await controller.connect(); await flush(); const peer = Peer.instances[0];
    peer.event('session.closed');
    expect(controller.getSnapshot()).toMatchObject({ transport: 'idle', state: 'closed', sessionIdRemote: null });
    expect(mic.track.stop).toHaveBeenCalled(); expect(peer.close).toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
    api.get.mockClear(); await vi.advanceTimersByTimeAsync(20000);
    expect(api.get).not.toHaveBeenCalled();
  });
  it('recovers the UI and resumes polling when ICE recovers', async () => {
    await controller.connect(); await flush(); const peer = Peer.instances[0];
    peer.state('disconnected'); expect(controller.getSnapshot().transport).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(2000);
    peer.state('connected'); expect(controller.getSnapshot().transport).toBe('connected');
    api.get.mockClear(); await vi.advanceTimersByTimeAsync(1600);
    expect(api.get.mock.calls.some(([p]) => String(p).includes('/events?'))).toBe(true);
    expect(Peer.instances).toHaveLength(1);
  });
  it('performs bounded full reconnect when ICE does not recover', async () => {
    await controller.connect(); Peer.instances[0].state('disconnected');
    await vi.advanceTimersByTimeAsync(6000);
    expect(Peer.instances).toHaveLength(2);
    expect(controller.getSnapshot().transport).toBe('connected');
  });
  it('does not merge stale recovery tasks or transcript after switching', async () => {
    const transcript = deferred<{ turns: unknown[] }>(); const tasks = deferred<{ tasks: unknown[] }>();
    const original = api.get.getMockImplementation()!;
    api.get.mockImplementation((path: string) => path.includes('remote-A/transcript') ? transcript.promise : original(path));
    voice.getVoiceSession.mockReturnValueOnce(tasks.promise);
    await controller.connect(); controller.open('workspace', 'B'); await controller.connect();
    transcript.resolve({ turns: [{ turn_id: 'old', user_text: 'stale' }] });
    tasks.resolve({ tasks: [{ task_id: 'old', status: 'running', title: 'stale' }] }); await flush();
    expect(controller.getSnapshot().tasks).toEqual([]); expect(controller.getSnapshot().transcript).toEqual([]);
  });
  it('does not let a late event poll pollute B or spawn another loop', async () => {
    await controller.connect(); await flush();
    const gate = deferred<{ events: unknown[] }>(); const original = api.get.getMockImplementation()!;
    api.get.mockImplementation((path: string) => path.includes('remote-A/events') ? gate.promise : original(path));
    await vi.advanceTimersByTimeAsync(1500);
    controller.open('workspace', 'B'); await controller.connect(); await flush();
    api.get.mockClear();
    gate.resolve({ events: [{ event_id: 'late', event_seq: 999, session_id: 'remote-A', type: 'session.closed' }] }); await flush();
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.get.mock.calls.filter(([p]) => String(p).includes('remote-B/events'))).toHaveLength(1);
    expect(controller.getSnapshot()).toMatchObject({ sessionId: 'B', transport: 'connected', lastEventSeq: 0 });
  });
  it.each(['resolve', 'reject'])('isolates old task approval %s and busy flags', async (mode) => {
    await controller.connect(); const gate = deferred<Record<string, unknown>>();
    voice.approveVoiceTask.mockReturnValueOnce(gate.promise);
    const pending = controller.approveTask('same-task');
    controller.open('workspace', 'B'); await controller.connect();
    const nextGate = deferred<Record<string, unknown>>(); voice.approveVoiceTask.mockReturnValueOnce(nextGate.promise);
    const next = controller.approveTask('same-task');
    if (mode === 'reject') gate.reject(new Error('stale')); else gate.resolve({ task_id: 'old', status: 'running' });
    expect(await pending).toBe(false);
    expect(controller.getSnapshot()).toMatchObject({ tasks: [], error: null, busyTaskIds: ['same-task'] });
    nextGate.resolve({ task_id: 'same-task', status: 'running' }); await next;
    expect(controller.getSnapshot().busyTaskIds).toEqual([]);
  });
});
