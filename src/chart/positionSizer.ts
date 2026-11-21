export interface PositionSizerInput {
  accountSize: number;          // 帳戶淨值 (USD)
  riskPercent: number;          // 單筆最大風險 % (例如 0.5 或 1.0)
  maxAllocationPercent: number; // 單一標的最大倉位上限 % (例如 25 或 100)
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  adr20Percent: number;         // 20 日 ADR% (例如 4.5 代表 4.5%)
}

export interface PositionSizerMetrics {
  valid: boolean;
  side: 'LONG' | 'SHORT';
  riskPerShare: number;
  stopPercent: number;
  shares: number;
  positionValue: number;
  allocationPercent: number;
  actualRiskDollars: number;
  cappedByAllocation: boolean;
  rrRatio: number;
  adrMultiplier: number | null;
  adrStatus: 'tight' | 'ok' | 'wide' | 'none';
}

export function calculatePositionMetrics(input: PositionSizerInput): PositionSizerMetrics {
  const { accountSize, riskPercent, maxAllocationPercent, entryPrice, stopPrice, targetPrice, adr20Percent } = input;
  const side: 'LONG' | 'SHORT' = entryPrice >= stopPrice ? 'LONG' : 'SHORT';
  const riskPerShare = Math.abs(entryPrice - stopPrice);

  if (
    !Number.isFinite(accountSize) || accountSize <= 0 ||
    !Number.isFinite(entryPrice) || entryPrice <= 0 ||
    !Number.isFinite(stopPrice) || stopPrice <= 0 ||
    !Number.isFinite(riskPerShare) || riskPerShare < 1e-6
  ) {
    return {
      valid: false, side, riskPerShare: 0, stopPercent: 0, shares: 0,
      positionValue: 0, allocationPercent: 0, actualRiskDollars: 0,
      cappedByAllocation: false, rrRatio: 0, adrMultiplier: null, adrStatus: 'none'
    };
  }

  const maxRiskDollars = accountSize * (Math.max(0, riskPercent) / 100);
  const maxPositionDollars = accountSize * (Math.max(0, maxAllocationPercent) / 100);

  const rawRiskShares = Math.floor(maxRiskDollars / riskPerShare);
  const maxAllocShares = Math.floor(maxPositionDollars / entryPrice);
  const shares = Math.max(0, Math.min(rawRiskShares, maxAllocShares));
  const cappedByAllocation = rawRiskShares > maxAllocShares;

  const positionValue = shares * entryPrice;
  const allocationPercent = (positionValue / accountSize) * 100;
  const actualRiskDollars = shares * riskPerShare;
  const stopPercent = (riskPerShare / entryPrice) * 100;

  const rewardPerShare = side === 'LONG' ? (targetPrice - entryPrice) : (entryPrice - targetPrice);
  const rrRatio = rewardPerShare / riskPerShare;

  const adrMultiplier = adr20Percent > 0 ? stopPercent / adr20Percent : null;
  let adrStatus: 'tight' | 'ok' | 'wide' | 'none' = 'none';
  if (adrMultiplier !== null) {
    if (adrMultiplier < 0.5) adrStatus = 'tight';
    else if (adrMultiplier > 1.5) adrStatus = 'wide';
    else adrStatus = 'ok';
  }

  return {
    valid: true,
    side,
    riskPerShare,
    stopPercent,
    shares,
    positionValue,
    allocationPercent,
    actualRiskDollars,
    cappedByAllocation,
    rrRatio,
    adrMultiplier,
    adrStatus
  };
}
