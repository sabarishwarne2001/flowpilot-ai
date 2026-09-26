# ARCH47-S1:schemas — published schemas vendored for validation

Vendored byte-for-byte; never edited. Validation loads them through
`app/services/erp/formats/xsd.py`, which supplies — in memory, never on disk —
the `schemaLocation` of the few namespace-only imports in the copies below
(the OASIS originals name those files; the redistributors resolve them with a
catalog). Nothing here is fetched at run time.

## `ubl21/` — OASIS Universal Business Language 2.1

| Files | Source | Licence |
|---|---|---|
| `maindoc/UBL-Invoice-2.1.xsd`, `maindoc/UBL-Order-2.1.xsd`, `maindoc/UBL-ReceiptAdvice-2.1.xsd`, `common/UBL-*.xsd` | OASIS UBL 2.1 (CS1, 2013), as redistributed unmodified in content by `phax/ph-ubl` (`ph-ubl21/src/main/resources/external/schemas/ubl21`, commit `5c607671d879dbf4e9689077b110c838ccebc4a4`) | Copyright © OASIS Open 2013, OASIS IPR policy (the schemas may be copied and redistributed) |
| `common/CCTS_CCT_SchemaModule.xsd` | UN/CEFACT Core Component Types module referenced by UBL 2.1, from `phax/ph-xsds` (`ph-xsds-ccts-cct-schemamodule`, commit `e0b4f5ea103223ff5a76881104d983f2cbe67a52`) | UN/CEFACT |
| `common/xmldsig-core-schema.xsd` | W3C XML Signature schema, from `phax/ph-xsds` (`ph-xsds-xmldsig`) | W3C Software and Document License |
| `common/XAdES01903v132-201601.xsd`, `common/XAdES01903v141-201601.xsd` | ETSI XAdES schemas, from `phax/ph-xsds` (`ph-xsds-xades132`, `ph-xsds-xades141`) | ETSI; redistributed by ph-xsds under Apache-2.0 headers |

The signature and XAdES modules are only compiled because UBL's extension
content imports them; FlowPilot never writes a UBL extension or signature.

## `ooxml/` — ECMA-376 Office Open XML (SpreadsheetML and OPC)

| Files | Source | Licence |
|---|---|---|
| `sml.xsd`, `shared-*.xsd`, `dml-*.xsd`, `opc/opc-contentTypes.xsd`, `opc/opc-relationships.xsd` | ECMA-376 1st edition schemas as redistributed by `python-openxml/python-docx` (`ref/xsd`, commit `e45454602b53e8e572b179ccf1c91093ec9f4ed7`) | Ecma International (ECMA-376 is freely available; its schemas are redistributable) |

Used by `verify_arch47` to validate every XLSX golden file part by part.

## Not here, and why

* **ASC X12 (810, 850, 856, 997).** The X12 standards are licensed by ASC X12;
  no schema is freely published. `formats/x12.py` encodes the 004010 segments
  FlowPilot writes (element ids, minimum and maximum lengths, data types,
  mandatory elements, code values and segment order) from public
  implementation guides, and its validator re-parses every file with an
  independent reader (envelope arithmetic: ISA width 106, GS/GE and ST/SE
  counts and control numbers).
* **Tally Prime XML.** Tally publishes a documented XML import format but no
  schema. `tally/tally-voucher-import.xsd` is FlowPilot's own XSD of that
  documented structure (written for this milestone, not Tally's), and the
  validator adds Tally's accounting rules (entry amounts sum to zero;
  ISDEEMEDPOSITIVE agrees with the sign).
* **REST / OData presets.** QuickBooks Online, Zoho Books, Business Central,
  S/4HANA and NetSuite publish API references, not machine schemas that can be
  vendored here; `formats/jsonapi.py` carries JSON Schemas transcribed from
  those references for the request bodies FlowPilot sends. Verified against the
  transcriptions and mock servers, never against a live ERP (that needs each
  customer's sandbox).
