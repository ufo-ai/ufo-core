import { expect, test } from "vitest";

import { money } from "@/lib/money";

test("zero spend prints as zero dollars, not as sub-cent", () => {
  expect(money(0)).toBe("$0.00");
});

test("sub-cent spend prints as under a cent instead of six decimals", () => {
  expect(money(1)).toBe("<$0.01");
  expect(money(9_999)).toBe("<$0.01");
});

test("one cent is the boundary where real amounts begin", () => {
  expect(money(10_000)).toBe("$0.01");
});

test("dollar amounts round to cents", () => {
  expect(money(2_000_000)).toBe("$2.00");
  expect(money(34_500)).toBe("$0.03");
});
