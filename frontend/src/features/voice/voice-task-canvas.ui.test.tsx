import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { VoiceTaskCanvas } from './voice-task-canvas';
import type { VoiceTask } from './voice-tasks';

const task = (id: string, patch: Partial<VoiceTask> = {}): VoiceTask => ({
  taskId: id, status: 'running', kind: 'tool', title: `Task ${id}`,
  createdAt: '2026-09-27T00:00:00Z', updatedAt: '2026-09-27T00:00:00Z',
  requirementVersion: 1, deliveryStatus: 'pending_validation', evidenceRefs: [],
  sources: [], artifacts: [], limitations: [], resultAvailable: false, ...patch,
});
const detail = (id: string) => screen.getByText(`Task ${id}`).closest('details')!;
function toggle(id: string, open: boolean) {
  const element = detail(id);
  act(() => { element.open = open; fireEvent(element, new Event('toggle')); });
}

describe('task expansion intent', () => {
  it('preserves manual collapse on both own progress and unrelated updates', () => {
    const a = task('A'), b = task('B');
    const view = render(<VoiceTaskCanvas tasks={[a, b]} />);
    expect(detail('A')).toHaveAttribute('open');
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[a, { ...b, progress: 0.4 }]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, progress: 0.7 }, b]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    expect(detail('B')).toHaveAttribute('open');
  });
  it('automatically opens new active tasks without reopening old ones', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[task('B'), a]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    expect(detail('B')).toHaveAttribute('open');
    expect([...view.container.querySelectorAll('h3')].map((node) => node.textContent)).toEqual(['Task A', 'Task B']);
  });
  it('opens a newly active task only if the user has not chosen its state', () => {
    const a = task('A', { status: 'ready' }); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'running' }]} />);
    expect(detail('A')).toHaveAttribute('open');
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'ready' }]} />);
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'running' }]} />);
    expect(detail('A')).not.toHaveAttribute('open');
  });
  it('forgets removed task preferences and keeps inactive new tasks last', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    toggle('A', false); view.rerender(<VoiceTaskCanvas tasks={[]} />);
    view.rerender(<VoiceTaskCanvas tasks={[a]} />); expect(detail('A')).toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[task('B', { status: 'ready' }), a]} />);
    expect([...view.container.querySelectorAll('h3')].map((node) => node.textContent)).toEqual(['Task A', 'Task B']);
  });
});
