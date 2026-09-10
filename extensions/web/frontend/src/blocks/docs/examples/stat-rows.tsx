import { Sparkline, StatRow, StatRows } from "@/blocks/stat";

const HOLDINGS = [
  { name: "Microsoft", shares: "450 Shares", history: [12, 26, 18, 34] },
  { name: "Coca-Cola", shares: "1,200 Shares", history: [30, 14, 22, 19] },
  { name: "Johnson & Johnson", shares: "310 Shares", history: [9, 17, 28, 24] },
  { name: "Procter & Gamble", shares: "780 Shares", history: [21, 11, 15, 32] },
];

export function StatRowsExample() {
  return (
    <StatRows fade>
      {HOLDINGS.map((holding) => (
        <StatRow key={holding.name} title={holding.name} sub={holding.shares}>
          <Sparkline values={holding.history} kind="bars" />
        </StatRow>
      ))}
    </StatRows>
  );
}
