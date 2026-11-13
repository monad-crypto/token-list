#!/usr/bin/env node

/**
 * Codegen script: reads the generated token list JSON files and produces
 * TypeScript source files that embed the data with full type information.
 */

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

const mainnet = readTokenListJson("tokenlist-mainnet.json");
const testnet = readTokenListJson("tokenlist-testnet.json");

writeGeneratedModule(
  join(SOURCE_DIRECTORY, "mainnet.ts"),
  buildTokenListModule({
    json: mainnet.json,
    typeName: "MainnetTokenList",
    exportName: "mainnetTokenList",
  }),
);

writeGeneratedModule(
  join(SOURCE_DIRECTORY, "testnet.ts"),
  buildTokenListModule({
    json: testnet.json,
    typeName: "TestnetTokenList",
    exportName: "testnetTokenList",
  }),
);

writeGeneratedModule(join(SOURCE_DIRECTORY, "index.ts"), [
  'export { mainnetTokenList } from "./mainnet.js";',
  'export { testnetTokenList } from "./testnet.js";',
  'export type * from "./types.js";',
]);

writeCommonJsMarker(PACKAGE_DIRECTORY);
const version = syncPackageVersion(PACKAGE_DIRECTORY, mainnet.version);

console.log(
  `codegen: wrote src/mainnet.ts, src/testnet.ts, src/index.ts, dist/cjs/package.json, package.json (v${version})`,
);
