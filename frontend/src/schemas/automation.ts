import { z } from "zod";
import type { AutomationRule } from "@/types/automation";

/**
 * Generates the validation schema dynamic rules engine configurations.
 * Enforces real-time duplicity checks, priority integers, non-null items, and duplicate prevention.
 */
export const createRuleFormSchema = (
  ruleToEditId?: string,
  existingRules: readonly AutomationRule[] = []
) =>
  z.object({
    name: z
      .string()
      .trim()
      .min(1, "Rule name is required.")
      .max(100, "Rule name cannot exceed 100 characters.")
      .refine((val) => val.trim().length > 0, {
        message: "Rule name cannot contain only spaces.",
      })
      .refine(
        (val) => {
          const normalized = val.trim().toLowerCase();
          return !existingRules.some(
            (rule) =>
              rule.name.trim().toLowerCase() === normalized &&
              rule.id !== ruleToEditId
          );
        },
        {
          message: "A rule with this name already exists.",
        }
      ),
    priority: z
      .number()
      .int("Priority must be a whole number.")
      .min(1, "Priority must be at least 1.")
      .max(9999, "Priority cannot exceed 9999."),
    event: z.enum([
      "WORK_ITEM_CREATED",
      "WORK_ITEM_COMPLETED",
      "WORK_ITEM_FAILED",
      "WORK_ITEM_REPROCESSED",
    ]),
    conditions: z
      .array(
        z
          .object({
            field: z.string().trim().min(1, "Field path is required."),
            operator: z.enum([
              "EQUALS",
              "NOT_EQUALS",
              "CONTAINS",
              "NOT_CONTAINS",
              "STARTS_WITH",
              "ENDS_WITH",
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
            ]),
            value: z.string().trim(),
          })
          .superRefine((data, ctx) => {
            const valueRequiredOperators = [
              "EQUALS",
              "NOT_EQUALS",
              "CONTAINS",
              "NOT_CONTAINS",
              "STARTS_WITH",
              "ENDS_WITH",
              "GREATER_THAN",
              "LESS_THAN",
              "GREATER_THAN_OR_EQUAL",
              "LESS_THAN_OR_EQUAL",
              "BETWEEN",
              "IN",
              "NOT_IN",
            ];
            // Enforce required match value verification only for non-existence operators
            if (valueRequiredOperators.includes(data.operator)) {
              if (!data.value || data.value.length === 0) {
                ctx.addIssue({
                  code: z.ZodIssueCode.custom,
                  message: "Match value is required for this operator.",
                  path: ["value"],
                });
              }
            }
          })
      )
      .min(1, "At least one evaluation condition is required.")
      .refine(
        (conds) => {
          const seen = new Set<string>();
          for (const c of conds) {
            const key = `${c.field.trim()}|${c.operator}|${c.value.trim()}`;
            if (seen.has(key)) return false;
            seen.add(key);
          }
          return true;
        },
        {
          message: "Duplicate conditions are not permitted.",
        }
      ),
    logic_operator: z.enum(["AND", "OR"]),
    actions: z
      .array(
        z.object({
          action_type: z.literal("SEND_EMAIL"),
          config: z.object({
            recipient: z
              .string()
              .trim()
              .min(1, "Recipient email address is required.")
              .email("Please enter a valid recipient email address."),
          }),
        })
      )
      .min(1, "At least one action is required.")
      .refine(
        (acts) => {
          const seen = new Set<string>();
          for (const a of acts) {
            const recipient = a.config?.recipient?.trim().toLowerCase() ?? "";
            const key = `${a.action_type}|${recipient}`;
            if (seen.has(key)) return false;
            seen.add(key);
          }
          return true;
        },
        {
          message: "Duplicate actions are not permitted.",
        }
      ),
  });

export type RuleFormInput = z.infer<ReturnType<typeof createRuleFormSchema>>;
