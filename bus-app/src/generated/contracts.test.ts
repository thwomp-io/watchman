import { expect, it } from "vitest";
import schema from "./contracts.schema.json";
import committed from "./contracts.ts?raw";
// @ts-expect-error — a plain .mjs script without type declarations
import { render } from "../../scripts/gen-types.mjs";

it("contracts.ts is current with the committed schema (run `npm run gen:types`)", async () => {
  expect(await render(schema)).toBe(committed);
});
