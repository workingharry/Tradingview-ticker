# Project: Stock Website Review Findings Cleanup

## Architecture
- Frontend: Single-page application / client-side charting (vanilla JS / Lightweight Charts, HTML, CSS).
- Testing: Python unit test suite (`python3 -m unittest discover tests/ -v`).

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| R1 | Date Range / Intraday Res Sync | Bind range buttons to auto-switch resolution or clamp range | M1 | ORIGINAL_REQUEST | DONE |
| R2 | Drawing Toolbar Removal | Remove dummy drawing buttons and dead handler logic, reclaim 48px | M1 | ORIGINAL_REQUEST | DONE |
| R3 | Symbol Selector Redundancy Cleanup | Remove redundant `#symbolSelector` tab row from toolbar | M1 | ORIGINAL_REQUEST | DONE |
| R4 | Engine Selector Streamlining | Auto-fallback between Scheme A & Scheme B; clean up UI selector | M1 | ORIGINAL_REQUEST | DONE |
| R5 | Price Scale Mode Sync | Ensure Log/Percent operates as mutually exclusive radio state across resize/reset | M1 | ORIGINAL_REQUEST | DONE |
| R6 | RS Benchmark Control Consolidation | Consolidate duplicate RS Benchmark controls to a single clean UI | M1 | ORIGINAL_REQUEST | DONE |
| R7 | Poteto Mode Standards | Bias toward deletion, minimal change, test verification | M1 | ORIGINAL_REQUEST | DONE |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Six Review Findings Fix & Verify | Implement R1-R6 via /poteto-mode; verify unit tests & UI integrity | none | DONE |

## Acceptance Criteria
- [x] Unit tests pass: `python3 -m unittest discover tests/ -v` (145/145 passing)
- [x] No regressions or console errors across viewport widths
- [x] Clean, minimal changes adhering to /poteto-mode (ponytail philosophy)
