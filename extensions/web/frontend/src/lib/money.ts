const CENT_MICRO_USD = 10_000;

export function money(micro: number): string {
  if (micro > 0 && micro < CENT_MICRO_USD) return "<$0.01";
  return "$" + (micro / 1e6).toFixed(2);
}
