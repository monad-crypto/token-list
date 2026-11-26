#!/usr/bin/env node

/**
 * Codegen script: reads tokenlist-equities.json and produces TypeScript source
 * files that embed the data. Token types are copied from the token-list
 * package so both packages describe tokens identically.
 */

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildTokenListModule,
  readTokenListJson,
  syncPackageVersion,
  writeCommonJsMarker,
  writeGeneratedModule,
} from "../../scripts/codegen-shared.mjs";

const PACKAGE_DIRECTORY = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_DIRECTORY = join(PACKAGE_DIRECTORY, "src");

mkdirSync(SOURCE_DIRECTORY, { recursive: true });
copyFileSync(
  join(PACKAGE_DIRECTORY, "..", "token-list", "src", "types.ts"),
  join(SOURCE_DIRECTORY, "types.ts"),
);

const equities = readTokenListJson("tokenlist-equities.json");

writeGeneratedModule(
  join(SOURCE_DIRECTORY, "equities.ts"),
  buildTokenListModule({
    json: equities.json,
    typeName: "MainnetTokenList",
    exportName: "equitiesTokenList",
  }),
);

writeGeneratedModule(join(SOURCE_DIRECTORY, "index.ts"), [
  'export { equitiesTokenList } from "./equities.js";',
  'export type * from "./types.js";',
]);

writeCommonJsMarker(PACKAGE_DIRECTORY);
const version = syncPackageVersion(PACKAGE_DIRECTORY, equities.version);

console.log(
  `codegen: wrote src/types.ts, src/equities.ts, src/index.ts, dist/cjs/package.json, package.json (v${version})`,
);
