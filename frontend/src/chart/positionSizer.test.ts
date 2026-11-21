import { describe, it, expect } from 'vitest';
import { calculatePositionMetrics } from './positionSizer';

describe('positionSizer', () => {
  it('calculates standard long trade metrics correctly', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,           // $1,000 risk
      maxAllocationPercent: 25.0, // max $25,000
      entryPrice: 100,
      stopPrice: 95,              // $5 risk per share
      targetPrice: 115,           // $15 reward per share -> 3.0 R:R
      adr20Percent: 5.0           // 5% ADR -> stop = 5% -> 1.0x ADR
    });
    expect(res.valid).toBe(true);
    expect(res.side).toBe('LONG');
    expect(res.riskPerShare).toBe(5);
    expect(res.shares).toBe(200);
    expect(res.positionValue).toBe(20000);
    expect(res.allocationPercent).toBe(20);
    expect(res.actualRiskDollars).toBe(1000);
    expect(res.cappedByAllocation).toBe(false);
    expect(res.rrRatio).toBe(3);
    expect(res.adrMultiplier).toBe(1.0);
    expect(res.adrStatus).toBe('ok');
  });

  it('calculates standard short trade metrics correctly', () => {
    const res = calculatePositionMetrics({
      accountSize: 50000,
      riskPercent: 0.5,           // $250 risk
      maxAllocationPercent: 20.0, // max $10,000
      entryPrice: 50,
      stopPrice: 52,              // $2 risk per share
      targetPrice: 44,            // $6 reward per share -> 3.0 R:R
      adr20Percent: 4.0           // stop = 4% -> 1.0x ADR
    });
    expect(res.valid).toBe(true);
    expect(res.side).toBe('SHORT');
    expect(res.riskPerShare).toBe(2);
    expect(res.shares).toBe(125);
    expect(res.actualRiskDollars).toBe(250);
    expect(res.cappedByAllocation).toBe(false);
    expect(res.rrRatio).toBe(3);
    expect(res.adrMultiplier).toBe(1.0);
    expect(res.adrStatus).toBe('ok');
  });

  it('handles division by zero when entry equals stop', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 100,
      targetPrice: 110,
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.shares).toBe(0);
    expect(res.actualRiskDollars).toBe(0);
    expect(res.adrStatus).toBe('none');
    expect(res.reason).toBe('entry_equals_stop');
  });

  it('strictly caps shares to maxAllocationPercent to prevent overleveraged blowup', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,           // $1,000 risk
      maxAllocationPercent: 25.0, // max $25,000
      entryPrice: 100,
      stopPrice: 99.80,           // $0.20 risk per share (raw risk shares = 5,000!)
      targetPrice: 105,
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(true);
    expect(res.shares).toBe(250); // Capped at $25,000 / $100 = 250 shares!
    expect(res.cappedByAllocation).toBe(true);
    expect(res.positionValue).toBe(25000);
    expect(res.allocationPercent).toBe(25.0);
    expect(Math.abs(res.actualRiskDollars - 50)).toBeLessThan(1e-4);
  });

  it('triggers tight stop yellow warning when stop distance < 0.5x ADR', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 99.0,            // 1% stop
      targetPrice: 105,
      adr20Percent: 4.0           // 1% / 4% = 0.25x ADR -> tight
    });
    expect(res.valid).toBe(true);
    expect(res.adrStatus).toBe('tight');
    expect(res.adrMultiplier).toBe(0.25);
  });

  it('triggers wide stop red warning when stop distance > 1.5x ADR', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,
      maxAllocationPercent: 50.0,
      entryPrice: 100,
      stopPrice: 92.0,            // 8% stop
      targetPrice: 120,
      adr20Percent: 4.0           // 8% / 4% = 2.0x ADR -> wide
    });
    expect(res.valid).toBe(true);
    expect(res.adrStatus).toBe('wide');
    expect(res.adrMultiplier).toBe(2.0);
  });

  // --- Adversarial & Boundary Validation Tests ---

  it('rejects NaN or non-finite inputs without polluting outputs', () => {
    const res = calculatePositionMetrics({
      accountSize: NaN,
      riskPercent: 1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 95,
      targetPrice: 110,
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.shares).toBe(0);
    expect(res.reason).toBe('invalid_inputs');

    const res2 = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: NaN,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 95,
      targetPrice: 110,
      adr20Percent: 4.0
    });
    expect(res2.valid).toBe(false);
    expect(res2.shares).toBe(0);
    expect(res2.reason).toBe('invalid_inputs');
  });

  it('rejects negative or zero riskPercent / maxAllocationPercent', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: -1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 95,
      targetPrice: 110,
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.reason).toBe('invalid_inputs');

    const resZero = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 95,
      targetPrice: 110,
      adr20Percent: 4.0
    });
    expect(resZero.valid).toBe(false);
    expect(resZero.reason).toBe('invalid_inputs');
  });

  it('rejects inverted target price direction (Long target <= entry)', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 95,
      targetPrice: 98, // Inverted: Target below entry on LONG!
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.side).toBe('LONG');
    expect(res.reason).toBe('target_below_or_equal_entry');
  });

  it('rejects inverted target price direction (Short target >= entry)', () => {
    const res = calculatePositionMetrics({
      accountSize: 100000,
      riskPercent: 1.0,
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 105,
      targetPrice: 102, // Inverted: Target above entry on SHORT!
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.side).toBe('SHORT');
    expect(res.reason).toBe('target_above_or_equal_entry');
  });

  it('marks valid=false when calculated shares is zero due to insufficient risk budget', () => {
    const res = calculatePositionMetrics({
      accountSize: 500,           // Very small account
      riskPercent: 0.5,           // $2.50 max risk dollars
      maxAllocationPercent: 25.0,
      entryPrice: 100,
      stopPrice: 90,              // $10 risk per share -> rawRiskShares = floor(2.5/10) = 0!
      targetPrice: 130,
      adr20Percent: 4.0
    });
    expect(res.valid).toBe(false);
    expect(res.shares).toBe(0);
    expect(res.actualRiskDollars).toBe(0);
    expect(res.reason).toBe('zero_shares_insufficient_budget');
  });
});
