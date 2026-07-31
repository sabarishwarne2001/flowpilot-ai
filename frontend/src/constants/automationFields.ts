import type { AutomationOperator } from "@/types/automation";

/**
 * Supported categories of workflow automation target fields.
 */
export type AutomationFieldCategory =
  | "Classification"
  | "Document"
  | "Resume"
  | "Contact"
  | "Invoice";

/**
 * Expected runtime schema of an evaluated entity field value.
 */
export type AutomationFieldDataType = "string" | "number" | "array";

/**
 * Metadata definition for user-friendly automation target fields.
 */
export interface AutomationField {
  readonly category: AutomationFieldCategory;
  readonly label: string;
  readonly value: string;
  readonly dataType: AutomationFieldDataType;
  readonly description: string;
  readonly example: string;
  readonly allowedOperators: readonly AutomationOperator[];
}

/**
 * Centralized list of supported target fields mapped to user-friendly contexts.
 * Fields are sorted alphabetically within each semantic category block.
 */
export const AUTOMATION_FIELDS: readonly AutomationField[] = [
  // --- Classification ---
  {
    category: "Classification",
    label: "Classification Confidence",
    value: "classification_details.confidence",
    dataType: "number",
    description: "Confidence level of the automated classification model.",
    example: "0.98",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "GREATER_THAN",
      "LESS_THAN",
      "GREATER_THAN_OR_EQUAL",
      "LESS_THAN_OR_EQUAL",
      "BETWEEN",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Classification",
    label: "Document Type",
    value: "classification_details.document_classification",
    dataType: "string",
    description: "AI detected document category or class.",
    example: "Invoice",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },

  // --- Document ---
  {
    category: "Document",
    label: "AI Summary",
    value: "summary",
    dataType: "string",
    description: "AI generated executive summary of the processed document.",
    example: "Payment overdue",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },

  // --- Resume ---
  {
    category: "Resume",
    label: "Degree",
    value: "entities.degree",
    dataType: "string",
    description: "Highest academic degree or qualification detected.",
    example: "B.Tech",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Resume",
    label: "Experience",
    value: "entities.experience_years",
    dataType: "number",
    description: "Number of years of professional work experience.",
    example: "3",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "GREATER_THAN",
      "LESS_THAN",
      "GREATER_THAN_OR_EQUAL",
      "LESS_THAN_OR_EQUAL",
      "BETWEEN",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Resume",
    label: "Skills",
    value: "entities.skills",
    dataType: "array",
    description: "Extracted list of professional and technical skills.",
    example: "React",
    allowedOperators: [
      "CONTAINS",
      "NOT_CONTAINS",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },

  // --- Contact ---
  {
    category: "Contact",
    label: "Email Address",
    value: "entities.email",
    dataType: "string",
    description: "Extracted contact email address.",
    example: "john@example.com",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Contact",
    label: "Organization",
    value: "entities.organization",
    dataType: "string",
    description: "Name of the associated organization or business entity.",
    example: "OpenAI",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Contact",
    label: "Phone Number",
    value: "entities.phone",
    dataType: "string",
    description: "Extracted contact phone number.",
    example: "+1 555-123-4567",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },

  // --- Invoice ---
  {
    category: "Invoice",
    label: "Customer Name",
    value: "entities.customer_name",
    dataType: "string",
    description: "Name of the purchasing client or customer.",
    example: "Acme Corp",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Invoice",
    label: "Invoice Number",
    value: "entities.invoice_number",
    dataType: "string",
    description: "Reference number extracted from the billing document.",
    example: "INV-2025-001",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Invoice",
    label: "Invoice Total",
    value: "entities.total_amount",
    dataType: "number",
    description: "Extracted total monetary amount on the invoice.",
    example: "1250.50",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "GREATER_THAN",
      "LESS_THAN",
      "GREATER_THAN_OR_EQUAL",
      "LESS_THAN_OR_EQUAL",
      "BETWEEN",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
  {
    category: "Invoice",
    label: "Vendor Name",
    value: "entities.vendor_name",
    dataType: "string",
    description: "Name of the merchant or issuing vendor.",
    example: "Microsoft",
    allowedOperators: [
      "EQUALS",
      "NOT_EQUALS",
      "CONTAINS",
      "NOT_CONTAINS",
      "STARTS_WITH",
      "ENDS_WITH",
      "IN",
      "NOT_IN",
      "EXISTS",
      "IS_EMPTY",
      "IS_NOT_EMPTY",
    ],
  },
];

/**
 * Key-value mapping dictionary structured for O(1) field property lookups.
 */
export const AUTOMATION_FIELDS_MAP: Record<string, AutomationField> =
  AUTOMATION_FIELDS.reduce<Record<string, AutomationField>>((acc, field) => {
    acc[field.value] = field;
    return acc;
  }, {});

/**
 * Resolves the user-friendly label associated with a specific dot-notation path.
 * Falls back to the raw path if no mapping matches.
 *
 * @param path - The raw backend JSON dot-notation field path.
 * @returns User-friendly text representing the target field.
 */
export const getFriendlyFieldName = (path: string): string => {
  return AUTOMATION_FIELDS_MAP[path]?.label ?? path;
};

/**
 * Resolves the allowed evaluation operators associated with a specific field path.
 * Falls back to an empty array list if no mapping matches.
 *
 * @param path - The raw backend JSON dot-notation field path.
 * @returns Array list of operators permitted for user selection.
 */
export const getAllowedOperators = (
  path: string
): readonly AutomationOperator[] => {
  return AUTOMATION_FIELDS_MAP[path]?.allowedOperators ?? [];
};

/**
 * Groups all configured automation fields under their respective category tags.
 * Pre-initializes all key categories to guarantee a complete type-safe record structure.
 *
 * @returns Map containing categorized field arrays.
 */
export const getCategorizedFields = (): Record<
  AutomationFieldCategory,
  AutomationField[]
> => {
  const acc: Record<AutomationFieldCategory, AutomationField[]> = {
    Classification: [],
    Document: [],
    Resume: [],
    Contact: [],
    Invoice: [],
  };

  for (const field of AUTOMATION_FIELDS) {
    acc[field.category].push(field);
  }

  return acc;
};

/**
 * Filters and returns automation fields matching a given data type constraint.
 *
 * @param dataType - Target data type to filter fields by.
 * @returns Filtered array list of automation fields.
 */
export const getFieldsByDataType = (
  dataType: AutomationFieldDataType
): readonly AutomationField[] => {
  return AUTOMATION_FIELDS.filter((field) => field.dataType === dataType);
};
