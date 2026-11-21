export interface PaletteItem {
  id: string;
  title: string;
  subtitle?: string;
  category: 'Symbol' | 'Timeframe' | 'Action';
  shortcut?: string;
  onSelect: () => void;
}

export interface CommandPaletteOptions {
  container?: HTMLElement;
  symbols: string[];
  timeframes?: string[];
  onSelectSymbol: (symbol: string) => void;
  onSelectTimeframe: (timeframe: string) => void;
  onAction: (actionId: string) => void;
}

export class CommandPalette {
  private container: HTMLElement;
  private symbols: string[];
  private timeframes: string[];
  private onSelectSymbol: (symbol: string) => void;
  private onSelectTimeframe: (timeframe: string) => void;
  private onAction: (actionId: string) => void;

  private overlayElement: HTMLDivElement | null = null;
  private inputElement: HTMLInputElement | null = null;
  private listElement: HTMLUListElement | null = null;

  private isVisible = false;
  private previousActiveElement: HTMLElement | null = null;
  private selectedIndex = 0;
  private filteredItems: PaletteItem[] = [];
  private windowKeydownHandler: ((e: KeyboardEvent) => void) | null = null;

  constructor(options: CommandPaletteOptions) {
    this.container = options.container || document.body;
    this.symbols = options.symbols;
    this.timeframes = options.timeframes || ['15m', '1h', '1D', '1W'];
    this.onSelectSymbol = options.onSelectSymbol;
    this.onSelectTimeframe = options.onSelectTimeframe;
    this.onAction = options.onAction;

    this.buildDOM();
    this.attachGlobalHotkeys();
  }

  public updateSymbols(newSymbols: string[]): void {
    this.symbols = [...newSymbols];
    if (this.isVisible) {
      this.filterItems(this.inputElement?.value || '');
    }
  }

  public isOpen(): boolean {
    return this.isVisible;
  }

  public open(): void {
    if (this.isVisible || !this.overlayElement || !this.inputElement) return;

    this.previousActiveElement = document.activeElement as HTMLElement | null;
    this.isVisible = true;
    this.overlayElement.classList.add('visible');
    this.inputElement.value = '';
    this.filterItems('');

    // Force focus into search input
    requestAnimationFrame(() => {
      this.inputElement?.focus();
    });
  }

  public close(): void {
    if (!this.isVisible || !this.overlayElement) return;

    this.isVisible = false;
    this.overlayElement.classList.remove('visible');

    if (this.previousActiveElement && typeof this.previousActiveElement.focus === 'function') {
      this.previousActiveElement.focus();
    }
    this.previousActiveElement = null;
  }

  public toggle(): void {
    if (this.isVisible) {
      this.close();
    } else {
      this.open();
    }
  }

  public destroy(): void {
    if (this.windowKeydownHandler) {
      window.removeEventListener('keydown', this.windowKeydownHandler);
      this.windowKeydownHandler = null;
    }
    if (this.overlayElement && this.overlayElement.parentNode) {
      this.overlayElement.parentNode.removeChild(this.overlayElement);
    }
    this.overlayElement = null;
    this.inputElement = null;
    this.listElement = null;
    this.filteredItems = [];
  }

  private buildDOM(): void {
    const overlay = document.createElement('div');
    overlay.className = 'command-palette-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Command Palette');

    overlay.innerHTML = `
      <div class="command-palette-backdrop"></div>
      <div class="command-palette-modal">
        <div class="command-palette-header">
          <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="text" class="command-palette-input" placeholder="Search ticker, timeframe, or action (e.g. NVDA, 1D, Risk/Reward)..." autocomplete="off" spellcheck="false" />
          <kbd class="esc-badge">ESC</kbd>
        </div>
        <ul class="command-palette-list" role="listbox"></ul>
        <div class="command-palette-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> select</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    `;

    this.overlayElement = overlay;
    this.inputElement = overlay.querySelector('.command-palette-input');
    this.listElement = overlay.querySelector('.command-palette-list');

    const backdrop = overlay.querySelector('.command-palette-backdrop');
    backdrop?.addEventListener('click', () => this.close());

    this.inputElement?.addEventListener('input', (e) => {
      const val = (e.target as HTMLInputElement).value;
      this.filterItems(val);
    });

    this.inputElement?.addEventListener('keydown', (e) => {
      this.handleInputKeydown(e);
    });

    // Modal focus trap for accessibility
    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') {
        const focusable = overlay.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    });

    this.container.appendChild(overlay);
  }

  private attachGlobalHotkeys(): void {
    this.windowKeydownHandler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        this.toggle();
      } else if (e.key === 'Escape' && this.isVisible) {
        e.preventDefault();
        this.close();
      }
    };
    window.addEventListener('keydown', this.windowKeydownHandler);
  }

  private handleInputKeydown(e: KeyboardEvent): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (this.filteredItems.length > 0) {
        this.selectedIndex = (this.selectedIndex + 1) % this.filteredItems.length;
        this.renderList();
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (this.filteredItems.length > 0) {
        this.selectedIndex = (this.selectedIndex - 1 + this.filteredItems.length) % this.filteredItems.length;
        this.renderList();
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (this.filteredItems.length > 0 && this.selectedIndex < this.filteredItems.length) {
        const item = this.filteredItems[this.selectedIndex];
        item.onSelect();
        this.close();
      }
    }
  }

  private getAllItems(): PaletteItem[] {
    const items: PaletteItem[] = [];

    // 1. Symbol Items
    for (const sym of this.symbols) {
      items.push({
        id: `sym_${sym}`,
        title: sym,
        subtitle: `Watchlist Symbol`,
        category: 'Symbol',
        shortcut: '↵',
        onSelect: () => this.onSelectSymbol(sym)
      });
    }

    // 2. Timeframe Items
    for (const tf of this.timeframes) {
      items.push({
        id: `tf_${tf}`,
        title: `Timeframe: ${tf}`,
        subtitle: `Switch chart resolution to ${tf}`,
        category: 'Timeframe',
        onSelect: () => this.onSelectTimeframe(tf)
      });
    }

    // 3. Actions
    items.push({
      id: 'action_attach_rr',
      title: 'Attach Risk/Reward Position Box',
      subtitle: 'Calculate shares, sizing & R:R ratios on chart',
      category: 'Action',
      shortcut: 'Shift+R',
      onSelect: () => this.onAction('attach_rr')
    });

    items.push({
      id: 'action_clear_drawings',
      title: 'Clear All Drawings on Current Symbol',
      subtitle: 'Remove all saved lines and risk/reward boxes for current ticker',
      category: 'Action',
      onSelect: () => this.onAction('clear_drawings')
    });

    items.push({
      id: 'action_fit_content',
      title: 'Fit Chart Content / Reset Zoom',
      subtitle: 'Fit all visible candles to window',
      category: 'Action',
      onSelect: () => this.onAction('fit_content')
    });

    items.push({
      id: 'action_flip_next',
      title: 'Next Symbol in Watchlist',
      subtitle: 'Flip to next stock in active watchlist',
      category: 'Action',
      shortcut: 'Space / ↓',
      onSelect: () => this.onAction('flip_next')
    });

    items.push({
      id: 'action_flip_prev',
      title: 'Previous Symbol in Watchlist',
      subtitle: 'Flip to previous stock in active watchlist',
      category: 'Action',
      shortcut: 'Shift+Space / ↑',
      onSelect: () => this.onAction('flip_prev')
    });

    return items;
  }

  private filterItems(query: string): void {
    const q = query.trim().toUpperCase();
    const all = this.getAllItems();

    if (!q) {
      this.filteredItems = all;
    } else {
      const matched = all.filter((item) => {
        return (
          item.title.toUpperCase().includes(q) ||
          (item.subtitle && item.subtitle.toUpperCase().includes(q)) ||
          item.category.toUpperCase().includes(q)
        );
      });

      // If user typed a ticker not currently in the watchlist, add dynamic entry
      const exactMatch = all.some((item) => item.category === 'Symbol' && item.title === q);
      if (!exactMatch && /^[A-Z0-9.\-:]+$/.test(q)) {
        matched.unshift({
          id: `sym_custom_${q}`,
          title: q,
          subtitle: `Open Custom Symbol`,
          category: 'Symbol',
          shortcut: '↵',
          onSelect: () => this.onSelectSymbol(q)
        });
      }

      this.filteredItems = matched;
    }

    this.selectedIndex = 0;
    this.renderList();
  }

  private renderList(): void {
    if (!this.listElement) return;

    if (this.filteredItems.length === 0) {
      this.listElement.innerHTML = `
        <li class="palette-empty-state">No matching commands or symbols found.</li>
      `;
      return;
    }

    let html = '';
    let lastCategory = '';

    for (let i = 0; i < this.filteredItems.length; i++) {
      const item = this.filteredItems[i];
      const isSelected = i === this.selectedIndex;

      if (item.category !== lastCategory) {
        html += `<li class="palette-group-header">${item.category}</li>`;
        lastCategory = item.category;
      }

      html += `
        <li class="palette-item ${isSelected ? 'active' : ''}" data-index="${i}" role="option" aria-selected="${isSelected}">
          <div class="palette-item-left">
            <span class="palette-item-title">${item.title}</span>
            ${item.subtitle ? `<span class="palette-item-subtitle">${item.subtitle}</span>` : ''}
          </div>
          ${item.shortcut ? `<kbd class="palette-shortcut">${item.shortcut}</kbd>` : ''}
        </li>
      `;
    }

    this.listElement.innerHTML = html;

    // Attach click listeners to items
    const items = this.listElement.querySelectorAll('.palette-item');
    items.forEach((el) => {
      el.addEventListener('click', () => {
        const idx = Number(el.getAttribute('data-index'));
        if (!isNaN(idx) && idx >= 0 && idx < this.filteredItems.length) {
          this.filteredItems[idx].onSelect();
          this.close();
        }
      });
      el.addEventListener('mouseenter', () => {
        const idx = Number(el.getAttribute('data-index'));
        if (!isNaN(idx) && idx >= 0 && idx < this.filteredItems.length) {
          this.selectedIndex = idx;
          this.highlightSelectedItem(false);
        }
      });
    });

    this.highlightSelectedItem(true);
  }

  private highlightSelectedItem(scrollIntoView: boolean): void {
    if (!this.listElement) return;
    const items = this.listElement.querySelectorAll('.palette-item');
    items.forEach((el) => {
      const idx = Number(el.getAttribute('data-index'));
      const active = idx === this.selectedIndex;
      el.classList.toggle('active', active);
      el.setAttribute('aria-selected', active ? 'true' : 'false');

      if (active && scrollIntoView) {
        (el as HTMLElement).scrollIntoView({ block: 'nearest' });
      }
    });
  }
}
