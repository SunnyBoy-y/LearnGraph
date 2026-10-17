import { useSyncExternalStore } from 'react';
import { idleNoiseStatus, type VoiceNoiseStatus } from './rnnoise/input';

let status: VoiceNoiseStatus = idleNoiseStatus;
const listeners = new Set<() => void>();
export function setVoiceNoiseStatus(next: VoiceNoiseStatus) {
  status = next;
  listeners.forEach((listener) => listener());
}
export function resetVoiceNoiseStatus() { setVoiceNoiseStatus(idleNoiseStatus); }
export function getVoiceNoiseStatus() { return status; }
export function useVoiceNoiseStatus() {
  return useSyncExternalStore((listener) => {
    listeners.add(listener); return () => listeners.delete(listener);
  }, () => status, () => idleNoiseStatus);
}
