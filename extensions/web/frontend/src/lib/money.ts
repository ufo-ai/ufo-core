export function money(micro: number): string {
  return "$" + (micro / 1e6).toFixed(6);
}
