import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => ({ workspaceId: 'ws-test' }) }));
import { MemoryRouter } from 'react-router-dom';
import { AnswerIcon } from './answer-icon';
import { TextWithCitations } from './message-part-renderer';
import { LazyStreamdown } from '@/components/ai-elements/lazy-streamdown';

describe('answer icons', () => {
  it('renders a named Fluent icon with accessibility text from local WebP', () => {
    render(<AnswerIcon name="fluent:rocket" label="开始学习" size="large" />);
    const image = screen.getByRole('img', { name: '开始学习' });
    expect(image).toHaveAttribute('src', '/fluent-emoji/1.1.0/1f680.webp');
    expect(image).toHaveClass('answer-icon--large');
    expect(image).toHaveAttribute('loading', 'lazy');
  });
  it('keeps decorative icons out of the accessible name', () => {
    const { container } = render(<h2><AnswerIcon name="fluent:brain" />学习</h2>);
    expect(screen.getByRole('heading', { name: '学习' })).toBeInTheDocument();
    expect(container.querySelector('img')).toHaveAttribute('aria-hidden', 'true');
  });
  it('uses a Unicode fallback after an image error and recovers when the icon changes', () => {
    const { rerender } = render(<AnswerIcon name="fluent:rocket" label="发射" />);
    fireEvent.error(screen.getByRole('img', { name: '发射' }));
    expect(screen.getByRole('img', { name: '发射' })).toHaveTextContent('🚀');
    rerender(<AnswerIcon name="fluent:brain" label="思考" />);
    expect(screen.getByRole('img', { name: '思考' })).toHaveAttribute('src', '/fluent-emoji/1.1.0/1f9e0.webp');
  });
  it('retains registered Lucide icons and safely falls back for unknown names', () => {
    const { container, rerender } = render(<AnswerIcon name="zap" label="快速" />);
    expect(screen.getByRole('img', { name: '快速' }).querySelector('.lucide-zap')).toBeTruthy();
    rerender(<AnswerIcon name="fluent:unknown-new-icon" label="未知" />);
    expect(screen.getByRole('img', { name: '未知' }).querySelector('svg')).toBeTruthy();
    expect(container.querySelector('img')).toBeNull();
  });
  it('does not render an absent icon', () => {
    const { container } = render(<AnswerIcon name={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('Fluent Emoji in the actual Markdown renderer', () => {
  it('renders Unicode emoji inline in prose, links and tables, preserving literal code', async () => {
    const { container } = render(<MemoryRouter><TextWithCitations content={'## 🚀 学习\n\n**🧠 思考**与[📚 阅读](https://example.com)\n\n| 项目 | 状态 |\n| --- | --- |\n| 测试 | ✅ |\n\n`💡 保持代码`\n\n```text\n🚀 保持围栏\n```'} /></MemoryRouter>);
    await waitFor(() => expect(container.querySelectorAll('img.answer-icon')).toHaveLength(4));
    expect(container.querySelector('h2 img.answer-icon')).toBeTruthy();
    expect(container.querySelector('[data-streamdown="strong"] img.answer-icon')).toBeTruthy();
    expect(container.querySelector('a img.answer-icon')).toBeTruthy();
    expect(container.querySelector('td img.answer-icon')).toBeTruthy();
    expect(container.querySelector('code img')).toBeNull();
    expect(container.querySelector('pre img')).toBeNull();
    expect(container.textContent).toContain('💡 保持代码');
    expect(container.textContent).toContain('🚀 保持围栏');
  });
  it('uses the same inline icons in safe Stream UI Markdown without allowing arbitrary images', async () => {
    const { container } = render(<MemoryRouter><TextWithCitations safeDocument content={'💡 内容\n\n![不得加载](https://evil.example/pixel.png)'} /></MemoryRouter>);
    await waitFor(() => expect(container.querySelectorAll('img.answer-icon')).toHaveLength(1));
    expect(container.querySelector('img[src^="https:"]')).toBeNull();
  });
  it('updates split ZWJ sequences and appended prose without duplicating earlier icons', async () => {
    const { container, rerender } = render(<LazyStreamdown>👩</LazyStreamdown>);
    await waitFor(() => expect(container.querySelectorAll('img.answer-icon')).toHaveLength(1));
    rerender(<LazyStreamdown>👩‍💻 正在学习 🚀</LazyStreamdown>);
    await waitFor(() => expect(container.querySelectorAll('img.answer-icon')).toHaveLength(2));
    expect(container.querySelector('img')?.getAttribute('src')).toContain('1f469-200d-1f4bb');
  });
});
