// Generate src/generated/contracts.ts from the engine's JSON Schema (src/harness/console/contracts.py).
//   npm run gen:types            # regenerate the schema (via uv) and the TS
//   node scripts/gen-types.mjs --from-schema   # TS only, from the committed schema
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const SCHEMA = new URL("../src/generated/contracts.schema.json", import.meta.url);
const OUT = new URL("../src/generated/contracts.ts", import.meta.url);

export async function render(schema) {
  const ts = await compile(schema, "Contracts", {
    bannerComment: "// GENERATED from src/harness/console/contracts.py by scripts/gen-types.mjs — do not edit.",
    additionalProperties: false,
    declareExternallyReferenced: true,
    format: false,
  });
  // The compiler leaves "* " on blank doc-comment lines. Strip trailing whitespace so the committed
  // file survives whitespace-normalizing tools (the public publish transform trims it) and the
  // drift test still compares equal.
  return ts.replace(/[ \t]+$/gm, "");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  if (!process.argv.includes("--from-schema")) {
    const json = execFileSync("uv", ["run", "python", "-m", "harness.console.contracts"], { cwd: new URL("../..", import.meta.url) });
    writeFileSync(SCHEMA, json);
  }
  writeFileSync(OUT, await render(JSON.parse(readFileSync(SCHEMA, "utf8"))));
  console.log("wrote src/generated/contracts.ts");
}
