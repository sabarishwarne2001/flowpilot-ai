#!/usr/bin/env node
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..", "src");

const CONTRACTS = [
  {
    tsType: "OrganizationCreateRequest",
    file: "tenancy.ts",
    schema: "OrganizationCreate",
  },
  {
    tsType: "OrganizationUpdateRequest",
    file: "tenancy.ts",
    schema: "OrganizationUpdate",
  },
  {
    tsType: "WorkspaceInvitationCreateRequest",
    file: "tenancy.ts",
    schema: "OrganizationInvitationCreate",
  },
  {
    tsType: "WorkspaceGrantInput",
    file: "tenancy.ts",
    schema: "WorkspaceGrantInput",
  },
  {
    tsType: "OrganizationMemberRoleUpdateRequest",
    file: "tenancy.ts",
    schema: "OrganizationMemberRoleUpdate",
  },
  {
    tsType: "SpendLimitUpdateRequest",
    file: "usage.ts",
    schema: "SpendLimitUpdate",
  },
  {
    tsType: "SupplierInvoiceCreateRequest",
    file: "cogs.ts",
    schema: "SupplierInvoiceCreate",
  },
  { tsType: "ReconcileRequest", file: "cogs.ts", schema: "ReconcileRequest" },
  {
    tsType: "AcceptVarianceRequest",
    file: "cogs.ts",
    schema: "AcceptVarianceRequest",
  },
  {
    tsType: "DataResidencyUpdateRequest",
    file: "compliance.ts",
    schema: "DataResidencyUpdate",
  },
  {
    tsType: "RetentionPolicyUpdateRequest",
    file: "compliance.ts",
    schema: "RetentionPolicyUpdate",
  },
  {
    tsType: "ErasureRequestPayload",
    file: "compliance.ts",
    schema: "ErasureRequest",
  },
];

const argv = process.argv.slice(2);
const schemaFlag = argv.indexOf("--schema");
const SCHEMA_SOURCE =
  schemaFlag !== -1 && argv[schemaFlag + 1]
    ? argv[schemaFlag + 1]
    : "http://localhost:8000/api/v1/openapi.json";

async function loadSchema(source) {
  if (/^https?:\/\//.test(source)) {
    const response = await fetch(source);
    if (!response.ok) {
      throw new Error(
        `${source} responded ${response.status}. Is the API running?`,
      );
    }
    return response.json();
  }
  const path = resolve(process.cwd(), source);
  if (!existsSync(path)) {
    throw new Error(`No such schema file: ${path}`);
  }
  return JSON.parse(readFileSync(path, "utf8"));
}

const sourceCache = new Map();

function sourceFor(file) {
  if (!sourceCache.has(file)) {
    const path = resolve(SRC, "types", file);
    if (!existsSync(path)) {
      throw new Error(`No such types file: ${path}`);
    }
    sourceCache.set(
      file,
      ts.createSourceFile(
        path,
        readFileSync(path, "utf8"),
        ts.ScriptTarget.Latest,
        true,
      ),
    );
  }
  return sourceCache.get(file);
}

function readInterface(file, name) {
  const source = sourceFor(file);
  let found = null;

  const visit = (node) => {
    if (ts.isInterfaceDeclaration(node) && node.name.text === name) {
      found = node;
      return;
    }
    if (
      ts.isTypeAliasDeclaration(node) &&
      node.name.text === name &&
      ts.isTypeLiteralNode(node.type)
    ) {
      found = node.type;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(source);

  if (!found) {
    return null;
  }

  const properties = new Map();
  for (const member of found.members) {
    if (!ts.isPropertySignature(member) || !member.name) {
      continue;
    }
    const key = ts.isIdentifier(member.name)
      ? member.name.text
      : ts.isStringLiteral(member.name)
        ? member.name.text
        : null;
    if (key) {
      properties.set(key, { optional: Boolean(member.questionToken) });
    }
  }
  return properties;
}

function readSchema(document, name) {
  const schema = document?.components?.schemas?.[name];
  if (!schema) {
    return null;
  }
  const required = new Set(schema.required ?? []);
  const properties = new Map();
  for (const key of Object.keys(schema.properties ?? {})) {
    properties.set(key, { optional: !required.has(key) });
  }
  return properties;
}

function compare(contract, tsProps, apiProps) {
  const ignore = new Set(Object.keys(contract.ignore ?? {}));
  const problems = [];

  for (const key of tsProps.keys()) {
    if (ignore.has(key)) continue;
    if (!apiProps.has(key)) {
      problems.push(
        `TypeScript declares "${key}", the server has no such field. ` +
          `Sending it means Pydantic drops it silently (or 422s where the ` +
          `model is fenced).`,
      );
    }
  }

  for (const key of apiProps.keys()) {
    if (ignore.has(key)) continue;
    if (!tsProps.has(key)) {
      const optional = apiProps.get(key).optional;
      problems.push(
        `The server declares "${key}"${optional ? " (optional)" : " (REQUIRED)"}, ` +
          `TypeScript does not. ${
            optional
              ? "Callers cannot set it."
              : "Every request from this client is incomplete."
          }`,
      );
    }
  }

  for (const [key, tsMeta] of tsProps) {
    if (ignore.has(key) || !apiProps.has(key)) continue;
    const apiMeta = apiProps.get(key);
    if (tsMeta.optional && !apiMeta.optional) {
      problems.push(
        `"${key}" is optional in TypeScript and REQUIRED on the server. ` +
          `Omitting it typechecks and fails at runtime.`,
      );
    }
  }

  return problems;
}

async function main() {
  const document = await loadSchema(SCHEMA_SOURCE);
  const schemaCount = Object.keys(document?.components?.schemas ?? {}).length;

  console.log(`API contract check — ${SCHEMA_SOURCE}`);
  console.log(`${schemaCount} schemas in the document\n`);

  let failures = 0;
  let checked = 0;

  for (const contract of CONTRACTS) {
    const label = `${contract.tsType} ↔ ${contract.schema}`;

    const tsProps = readInterface(contract.file, contract.tsType);
    if (!tsProps) {
      console.log(`  ✗ ${label}`);
      console.log(
        `      No interface "${contract.tsType}" in src/types/${contract.file}. ` +
          `Renamed or removed — update CONTRACTS.\n`,
      );
      failures += 1;
      continue;
    }

    const apiProps = readSchema(document, contract.schema);
    if (!apiProps) {
      console.log(`  ✗ ${label}`);
      console.log(
        `      No schema "${contract.schema}" in the OpenAPI document.\n`,
      );
      failures += 1;
      continue;
    }

    const problems = compare(contract, tsProps, apiProps);
    checked += 1;

    if (problems.length === 0) {
      console.log(`  ✓ ${label}  (${tsProps.size} fields)`);
    } else {
      console.log(`  ✗ ${label}`);
      for (const problem of problems) {
        console.log(`      ${problem}`);
      }
      console.log("");
      failures += 1;
    }
  }

  console.log("");
  if (failures > 0) {
    console.log(
      `FAILED — ${failures} of ${CONTRACTS.length} contracts disagree with the server.`,
    );
    process.exit(1);
  }
  console.log(`PASSED — ${checked} contracts agree with the server.`);
}

main().catch((error) => {
  console.error(`\nContract check could not run: ${error.message}`);
  process.exit(1);
});
