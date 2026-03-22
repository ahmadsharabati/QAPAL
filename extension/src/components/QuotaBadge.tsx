/**
 * QuotaBadge — shows remaining scan quota.
 */

import React from "react";
import type { QuotaInfo } from "../types";

interface QuotaBadgeProps {
  quota: QuotaInfo | null;
  tier: string;
}

export function QuotaBadge({ quota, tier }: QuotaBadgeProps) {
  if (!quota) return null;

  const isUnlimited = quota.limit < 0;
  const remaining = isUnlimited ? Infinity : quota.limit - quota.used;
  const isLow = !isUnlimited && remaining <= 1;

  return (
    <div style={styles.container}>
      <span style={styles.tier}>{tier}</span>
      <span style={{ ...styles.count, color: isLow ? "#dc2626" : "#6b7280" }}>
        {isUnlimited
          ? `${quota.used} scans used`
          : `${quota.used}/${quota.limit} scans`}
      </span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 0",
  },
  tier: {
    padding: "2px 8px",
    background: "#ede9fe",
    color: "#5b21b6",
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
  },
  count: { fontSize: 12 },
};
