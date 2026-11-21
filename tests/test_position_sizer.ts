import assert from 'node:assert';
import { calculatePositionMetrics } from '../src/chart/positionSizer.ts';

console.log("Running Position Sizer Unit Tests...");

// 1. Standard Long Test
{
  const res = calculatePositionMetrics({
    accountSize: 100000,
    riskPercent: 1.0,           // $1,000 risk
    maxAllocationPercent: 25.0, // max $25,000
    entryPrice: 100,
    stopPrice: 95,              // $5 risk per share
    targetPrice: 115,           // $15 reward per share -> 3.0 R:R
    adr20Percent: 5.0           // 5% ADR -> stop = 5% -> 1.0x ADR
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.side, 'LONG');
  assert.strictEqual(res.riskPerShare, 5);
  assert.strictEqual(res.shares, 200); // 1000 / 5 = 200 shares ($20,000 <= $25,000)
  assert.strictEqual(res.positionValue, 20000);
  assert.strictEqual(res.allocationPercent, 20);
  assert.strictEqual(res.actualRiskDollars, 1000);
  assert.strictEqual(res.cappedByAllocation, false);
  assert.strictEqual(res.rrRatio, 3);
  assert.strictEqual(res.adrMultiplier, 1.0);
  assert.strictEqual(res.adrStatus, 'ok');
  console.log("✔ Test 1 passed: Standard Long");
}

// 2. Standard Short Test
{
  const res = calculatePositionMetrics({
    accountSize: 50000,
    riskPercent: 0.5,           // $250 risk
    maxAllocationPercent: 20.0, // max $10,000
    entryPrice: 50,
    stopPrice: 52,              // $2 risk per share
    targetPrice: 44,            // $6 reward per share -> 3.0 R:R
    adr20Percent: 4.0           // stop = 4% -> 1.0x ADR
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.side, 'SHORT');
  assert.strictEqual(res.riskPerShare, 2);
  assert.strictEqual(res.shares, 125); // 250 / 2 = 125 shares ($6,250 <= $10,000)
  assert.strictEqual(res.actualRiskDollars, 250);
  assert.strictEqual(res.cappedByAllocation, false);
  assert.strictEqual(res.rrRatio, 3);
  assert.strictEqual(res.adrMultiplier, 1.0);
  assert.strictEqual(res.adrStatus, 'ok');
  console.log("✔ Test 2 passed: Standard Short");
}

// 3. Division by Zero / Entry === Stop Test
{
  const res = calculatePositionMetrics({
    accountSize: 100000,
    riskPercent: 1.0,
    maxAllocationPercent: 25.0,
    entryPrice: 100,
    stopPrice: 100,             // entry === stop
    targetPrice: 110,
    adr20Percent: 4.0
  });
  assert.strictEqual(res.valid, false);
  assert.strictEqual(res.shares, 0);
  assert.strictEqual(res.actualRiskDollars, 0);
  assert.strictEqual(res.adrStatus, 'none');
  console.log("✔ Test 3 passed: Division by Zero Guard");
}

// 4. Max Allocation % Cap Test (Preventing Leverage Catastrophe)
{
  // Tight stop: $0.20 stop on $100 stock with $1,000 risk would be 5,000 shares ($500,000 = 500% leverage!)
  // With maxAllocationPercent = 25% ($25,000 cap), max shares should be 250!
  const res = calculatePositionMetrics({
    accountSize: 100000,
    riskPercent: 1.0,           // $1,000 risk
    maxAllocationPercent: 25.0, // max $25,000
    entryPrice: 100,
    stopPrice: 99.80,           // $0.20 risk per share
    targetPrice: 105,
    adr20Percent: 4.0
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.shares, 250); // capped at 250 shares!
  assert.strictEqual(res.cappedByAllocation, true);
  assert.strictEqual(res.positionValue, 25000);
  assert.strictEqual(res.allocationPercent, 25.0);
  assert.ok(Math.abs(res.actualRiskDollars - 50) < 1e-4); // actual risk is $50 due to allocation cap
  console.log("✔ Test 4 passed: Max Allocation Cap Guard");
}

// 5. Tight Stop (< 0.5x ADR) Yellow Warning
{
  const res = calculatePositionMetrics({
    accountSize: 100000,
    riskPercent: 1.0,
    maxAllocationPercent: 25.0,
    entryPrice: 100,
    stopPrice: 99.0,            // 1% stop
    targetPrice: 105,
    adr20Percent: 4.0           // 1% / 4% = 0.25x ADR -> tight
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.adrStatus, 'tight');
  assert.strictEqual(res.adrMultiplier, 0.25);
  console.log("✔ Test 5 passed: Tight Stop ADR Warning (< 0.5x)");
}

// 6. Wide Stop (> 1.5x ADR) Red Warning
{
  const res = calculatePositionMetrics({
    accountSize: 100000,
    riskPercent: 1.0,
    maxAllocationPercent: 50.0,
    entryPrice: 100,
    stopPrice: 92.0,            // 8% stop
    targetPrice: 120,
    adr20Percent: 4.0           // 8% / 4% = 2.0x ADR -> wide
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.adrStatus, 'wide');
  assert.strictEqual(res.adrMultiplier, 2.0);
  console.log("✔ Test 6 passed: Wide Stop ADR Warning (> 1.5x)");
}

console.log("\nAll 6 Position Sizer unit tests passed successfully!");
