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
  it('temporarily reveals a speaking task then restores its collapsed preference', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    view.rerender(<VoiceTaskCanvas tasks={[{...a,focusId:'focus',focusTurnId:'turn',focusAnchor:'summary'}]} />);
    expect(detail('A')).toHaveAttribute('open');
    expect(detail('A')).toHaveAttribute('data-voice-revealed','true');
    view.rerender(<VoiceTaskCanvas tasks={[a]} />);
    expect(detail('A')).not.toHaveAttribute('open');
  });
  it('preserves a previously expanded task after the explanation finishes', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    toggle('A',true);
    view.rerender(<VoiceTaskCanvas tasks={[{...a,focusId:'focus',focusTurnId:'turn',focusAnchor:'summary'}]} />);
    view.rerender(<VoiceTaskCanvas tasks={[a]} />);
    expect(detail('A')).toHaveAttribute('open');
  });
  it('honors manual changes during speech without reopening on the next component', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[{...a,focusId:'one',focusTurnId:'turn',focusAnchor:'summary'}]} />);
    toggle('A',false);
    view.rerender(<VoiceTaskCanvas tasks={[{...a,focusId:'two',focusTurnId:'turn',focusAnchor:'visual-table'}]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    toggle('A',true);
    view.rerender(<VoiceTaskCanvas tasks={[a]} />);
    expect(detail('A')).toHaveAttribute('open');
  });
  it('preserves manual collapse on both own progress and unrelated updates', () => {
    const a = task('A'), b = task('B');
    const view = render(<VoiceTaskCanvas tasks={[a, b]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    toggle('B', true);
    toggle('A', true);
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[a, { ...b, progress: 0.4 }]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, progress: 0.7 }, b]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    expect(detail('B')).toHaveAttribute('open');
  });
  it('keeps new active tasks collapsed and preserves the original order', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[task('B'), a]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    expect(detail('B')).not.toHaveAttribute('open');
    expect([...view.container.querySelectorAll('h3')].map((node) => node.textContent)).toEqual(['Task A', 'Task B']);
  });
  it('does not open a task when its status changes', () => {
    const a = task('A', { status: 'ready' }); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'running' }]} />);
    expect(detail('A')).not.toHaveAttribute('open');
    toggle('A', true);
    toggle('A', false);
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'ready' }]} />);
    view.rerender(<VoiceTaskCanvas tasks={[{ ...a, status: 'running' }]} />);
    expect(detail('A')).not.toHaveAttribute('open');
  });
  it('forgets removed task preferences and keeps inactive new tasks last', () => {
    const a = task('A'); const view = render(<VoiceTaskCanvas tasks={[a]} />);
    toggle('A', false); view.rerender(<VoiceTaskCanvas tasks={[]} />);
    view.rerender(<VoiceTaskCanvas tasks={[a]} />); expect(detail('A')).not.toHaveAttribute('open');
    view.rerender(<VoiceTaskCanvas tasks={[task('B', { status: 'ready' }), a]} />);
    expect([...view.container.querySelectorAll('h3')].map((node) => node.textContent)).toEqual(['Task A', 'Task B']);
  });
});
