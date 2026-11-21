import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CommandPalette } from './CommandPalette';

describe('CommandPalette', () => {
  let mockContainer: any;
  let mockWindowListeners: Record<string, ((e: any) => void)[]> = {};
  let onSelectSymbol: any;
  let onSelectTimeframe: any;
  let onAction: any;

  beforeEach(() => {
    mockWindowListeners = {};
    (globalThis as any).window = {
      addEventListener: vi.fn((event: string, handler: any) => {
        if (!mockWindowListeners[event]) mockWindowListeners[event] = [];
        mockWindowListeners[event].push(handler);
      }),
      removeEventListener: vi.fn()
    };
    (globalThis as any).requestAnimationFrame = vi.fn((cb) => cb(0));

    // Simple mock DOM
    const createMockElement = (tag: string) => {
      const children: any[] = [];
      const listeners: Record<string, ((e: any) => void)[]> = {};
      const classList = new Set<string>();
      const attrs: Record<string, string> = {};

      const el: any = {
        tagName: tag.toUpperCase(),
        classList: {
          add: (cls: string) => classList.add(cls),
          remove: (cls: string) => classList.delete(cls),
          contains: (cls: string) => classList.has(cls),
          toggle: (cls: string, force?: boolean) => {
            if (force === true) classList.add(cls);
            else if (force === false) classList.delete(cls);
            else if (classList.has(cls)) classList.delete(cls);
            else classList.add(cls);
          }
        },
        setAttribute: (k: string, v: string) => { attrs[k] = v; },
        getAttribute: (k: string) => attrs[k] || null,
        appendChild: (child: any) => {
          child.parentNode = el;
          children.push(child);
        },
        removeChild: (child: any) => {
          const idx = children.indexOf(child);
          if (idx !== -1) children.splice(idx, 1);
        },
        addEventListener: (event: string, fn: any) => {
          if (!listeners[event]) listeners[event] = [];
          listeners[event].push(fn);
        },
        dispatchEvent: (event: { type: string }) => {
          (listeners[event.type] || []).forEach(fn => fn(event));
        },
        querySelector: (sel: string) => {
          if (sel === '.command-palette-backdrop') return createMockElement('div');
          if (sel === '.command-palette-input') {
            const input = createMockElement('input');
            input.value = '';
            input.focus = vi.fn();
            return input;
          }
          if (sel === '.command-palette-list') return createMockElement('ul');
          return null;
        },
        querySelectorAll: () => [],
        scrollIntoView: vi.fn(),
        focus: vi.fn(),
        innerHTML: ''
      };
      return el;
    };

    (globalThis as any).document = {
      createElement: (tag: string) => createMockElement(tag),
      activeElement: null,
      body: createMockElement('body')
    };

    mockContainer = (globalThis as any).document.createElement('div');
    onSelectSymbol = vi.fn();
    onSelectTimeframe = vi.fn();
    onAction = vi.fn();
  });

  it('initializes in closed state', () => {
    const palette = new CommandPalette({
      container: mockContainer,
      symbols: ['NVDA', 'TSLA'],
      onSelectSymbol,
      onSelectTimeframe,
      onAction
    });

    expect(palette.isOpen()).toBe(false);
  });

  it('opens and closes via public API', () => {
    const palette = new CommandPalette({
      container: mockContainer,
      symbols: ['NVDA', 'TSLA'],
      onSelectSymbol,
      onSelectTimeframe,
      onAction
    });

    palette.open();
    expect(palette.isOpen()).toBe(true);

    palette.close();
    expect(palette.isOpen()).toBe(false);
  });

  it('toggles open state via Cmd+K hotkey', () => {
    const palette = new CommandPalette({
      container: mockContainer,
      symbols: ['NVDA', 'TSLA'],
      onSelectSymbol,
      onSelectTimeframe,
      onAction
    });

    const keydownHandlers = mockWindowListeners['keydown'] || [];
    expect(keydownHandlers.length).toBeGreaterThan(0);

    // Simulate Cmd+K
    const cmdKEvent = {
      metaKey: true,
      key: 'k',
      preventDefault: vi.fn()
    };
    keydownHandlers[0](cmdKEvent);
    expect(cmdKEvent.preventDefault).toHaveBeenCalled();
    expect(palette.isOpen()).toBe(true);

    // Escape closes palette
    const escEvent = {
      key: 'Escape',
      preventDefault: vi.fn()
    };
    keydownHandlers[0](escEvent);
    expect(palette.isOpen()).toBe(false);
  });

  it('updates symbols dynamically', () => {
    const palette = new CommandPalette({
      container: mockContainer,
      symbols: ['NVDA'],
      onSelectSymbol,
      onSelectTimeframe,
      onAction
    });

    palette.updateSymbols(['NVDA', 'PLTR', 'SMH']);
    // Symbols should be updated internally without throwing
    expect(palette.isOpen()).toBe(false);
  });

  it('destroys cleanly and removes event listeners', () => {
    const palette = new CommandPalette({
      container: mockContainer,
      symbols: ['NVDA'],
      onSelectSymbol,
      onSelectTimeframe,
      onAction
    });

    palette.destroy();
    expect((globalThis as any).window.removeEventListener).toHaveBeenCalled();
  });
});
