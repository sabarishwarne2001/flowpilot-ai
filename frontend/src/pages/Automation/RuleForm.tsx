import React, { useEffect } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import type { SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X, Loader2, Play, Plus } from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { ApiError } from "@/services/api/client";
import {
  AUTOMATION_FIELDS_MAP,
  getCategorizedFields,
  getAllowedOperators,
} from "@/constants/automationFields";
import type {
  AutomationRule,
  AutomationRuleCreateRequest,
  AutomationRuleUpdateRequest,
} from "@/types/automation";

// Centralized query key constant mapping to core rules lists
const RULES_QUERY_KEY = ["automation-rules"] as const;

// Declarative Zod validation schema to govern rule configurations
const ruleFormValidationSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Rule name is required.")
    .max(100, "Rule name cannot exceed 100 characters."),
  priority: z.number().int().min(1, "Priority must be at least 1."),
  event: z.enum([
    "WORK_ITEM_CREATED",
    "WORK_ITEM_COMPLETED",
    "WORK_ITEM_FAILED",
    "WORK_ITEM_REPROCESSED",
  ]),
  conditions: z
    .array(
      z.object({
        field: z.string().trim().min(1, "Field path is required."),
        operator: z.enum([
          "EQUALS",
          "NOT_EQUALS",
          "CONTAINS",
          "GREATER_THAN",
          "LESS_THAN",
          "GREATER_THAN_OR_EQUAL",
          "LESS_THAN_OR_EQUAL",
        ]),
        value: z.string().trim().min(1, "Match value is required."),
      })
    )
    .min(1, "At least one evaluation condition is required."),
  logic_operator: z.enum(["AND", "OR"]),
  action_type: z.literal("SEND_EMAIL"),
  recipient: z
    .string()
    .trim()
    .min(1, "Recipient email address is required.")
    .email("Please enter a valid recipient email address."),
});

type RuleFormInput = z.infer<typeof ruleFormValidationSchema>;

interface RuleFormProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  /**
   * Triggers list refreshes on parent pages upon successfully saving changes.
   */
  readonly onSaveSuccess: () => void;
  /**
   * Optional Rule entity. If supplied, the form operates in edit mode.
   */
  readonly ruleToEdit?: AutomationRule | null;
  /**
   * Optional Rule entity. If supplied, the form operates in duplication/create mode.
   */
  readonly ruleToDuplicate?: AutomationRule | null;
}

// Logic Operator Display mapping
const OPERATOR_LABELS: Record<string, string> = {
  EQUALS: "Equals (==)",
  NOT_EQUALS: "Not Equals (!=)",
  CONTAINS: "Contains",
  GREATER_THAN: "Greater Than (>)",
  LESS_THAN: "Less Than (<)",
  GREATER_THAN_OR_EQUAL: "Greater Than or Equal (>=)",
  LESS_THAN_OR_EQUAL: "Less Than or Equal (<=)",
};

/**
 * Validated Modal Dialog Form for composing or editing trigger-action Automation Rules.
 *
 * Packages dynamic recipient inputs cleanly into provider-agnostic action_config properties,
 * manages dual creation/edition lifecycles, and implements full accessibility bounds.
 */
export const RuleForm: React.FC<RuleFormProps> = ({
  isOpen,
  onClose,
  onSaveSuccess,
  ruleToEdit = null,
  ruleToDuplicate = null,
}) => {
  const queryClient = useQueryClient();
  const isEditMode = ruleToEdit !== null;
  const categorizedFields = getCategorizedFields();

  // Initialize input validator controllers with focus boundaries
  const {
    register,
    handleSubmit,
    reset,
    control,
    watch,
    setValue,
    formState: { errors },
  } = useForm<RuleFormInput>({
    resolver: zodResolver(ruleFormValidationSchema),
    shouldFocusError: true,
    defaultValues: {
      name: "",
      priority: 100,
      event: "WORK_ITEM_COMPLETED",
      conditions: [
        {
          field: "classification_details.document_classification",
          operator: "EQUALS",
          value: "",
        },
      ],
      logic_operator: "AND",
      action_type: "SEND_EMAIL",
      recipient: "",
    },
  });

  // Dynamic conditions list tracking helper
  const { fields, append, remove } = useFieldArray({
    control,
    name: "conditions",
  });

  // Watch currently configured field pathways to load placeholders and metadata
  const watchedConditions = watch("conditions") ?? [];

  // Hydrate form defaults dynamically whenever edit or duplicate contexts change
  useEffect(() => {
    if (ruleToEdit) {
      reset({
        name: ruleToEdit.name,
        priority: ruleToEdit.priority ?? 100,
        event: ruleToEdit.event,
        conditions: ruleToEdit.conditions.map((c) => ({
          field: c.field,
          operator: c.operator,
          value: c.value,
        })),
        logic_operator: ruleToEdit.logic_operator ?? "AND",
        action_type: ruleToEdit.action_type,
        recipient: (ruleToEdit.action_config?.recipient as string) ?? "",
      });
    } else if (ruleToDuplicate) {
      reset({
        name: `${ruleToDuplicate.name} (Copy)`,
        priority: ruleToDuplicate.priority ?? 100,
        event: ruleToDuplicate.event,
        conditions: ruleToDuplicate.conditions.map((c) => ({
          field: c.field,
          operator: c.operator,
          value: c.value,
        })),
        logic_operator: ruleToDuplicate.logic_operator ?? "AND",
        action_type: ruleToDuplicate.action_type,
        recipient: (ruleToDuplicate.action_config?.recipient as string) ?? "",
      });
    } else {
      reset({
        name: "",
        priority: 100,
        event: "WORK_ITEM_COMPLETED",
        conditions: [
          {
            field: "classification_details.document_classification",
            operator: "EQUALS",
            value: "",
          },
        ],
        logic_operator: "AND",
        action_type: "SEND_EMAIL",
        recipient: "",
      });
    }
  }, [ruleToEdit, ruleToDuplicate, reset]);

  // Escape key handler to close the modal drawer safely
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isOpen, onClose]);

  // 1. Transaction mutation mapping Rule creations
  const { mutateAsync: runCreateMutation, isPending: isCreating } = useMutation(
    {
      mutationFn: automationApi.createAutomationRule,
      onSuccess: async () => {
        toast.success(
          ruleToDuplicate
            ? "Rule duplicated successfully."
            : "Automation rule compiled successfully."
        );
        await queryClient.invalidateQueries({ queryKey: RULES_QUERY_KEY });
        onSaveSuccess();
        onClose();
      },
      onError: (err: unknown) => {
        if (err instanceof ApiError) {
          toast.error(err.message || "Failed to create automation rule.");
        } else {
          toast.error("An unexpected validation failure occurred.");
        }
      },
    }
  );

  // 2. Transaction mutation mapping Rule updates
  const { mutateAsync: runUpdateMutation, isPending: isUpdating } = useMutation(
    {
      mutationFn: ({
        id,
        payload,
      }: {
        id: string;
        payload: AutomationRuleUpdateRequest;
      }) => automationApi.updateAutomationRule(id, payload),
      onSuccess: async () => {
        toast.success("Automation rule updated.");
        await queryClient.invalidateQueries({ queryKey: RULES_QUERY_KEY });
        onSaveSuccess();
        onClose();
      },
      onError: (err: unknown) => {
        if (err instanceof ApiError) {
          toast.error(err.message || "Failed to update automation rule.");
        } else {
          toast.error("An unexpected verification failure occurred.");
        }
      },
    }
  );

  const onFormSubmit: SubmitHandler<RuleFormInput> = async (data) => {
    // Compile dynamic parameters into the standard provider-agnostic JSON format
    const compiledPayload: AutomationRuleCreateRequest = {
      name: data.name.trim(),
      priority: data.priority,
      event: data.event,
      conditions: data.conditions,
      logic_operator: data.logic_operator,
      action_type: data.action_type,
      action_config: {
        recipient: data.recipient.trim(),
      },
    };

    if (isEditMode && ruleToEdit) {
      await runUpdateMutation({ id: ruleToEdit.id, payload: compiledPayload });
    } else {
      await runCreateMutation(compiledPayload);
    }
  };

  if (!isOpen) {
    return null;
  }

  const isProcessing = isCreating || isUpdating;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rule-form-title"
    >
      {/* Centered Form Dialog Card Frame */}
      <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg overflow-hidden flex flex-col animate-scale-in">
        {/* Header Block */}
        <header className="h-16 border-b border-border/40 flex items-center justify-between px-6 bg-muted/5 select-none">
          <h2
            id="rule-form-title"
            className="font-extrabold text-sm uppercase tracking-wider"
          >
            {isEditMode
              ? "Modify Automation Rule"
              : ruleToDuplicate
              ? "Duplicate Automation Rule"
              : "Configure New Rule"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={isProcessing}
            className="p-1.5 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all focus:outline-none"
            aria-label="Close dialog"
          >
            <X className="h-4.5 w-4.5" />
          </button>
        </header>

        {/* Input Form Body Wrapper */}
        <form
          onSubmit={handleSubmit(onFormSubmit)}
          className="p-6 space-y-4 max-h-[75vh] overflow-y-auto scrollbar"
          noValidate
        >
          {/* Rule Name & Priority Row */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="sm:col-span-2 space-y-1.5">
              <label
                htmlFor="name"
                className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none"
              >
                Rule Name
              </label>
              <input
                {...register("name")}
                id="name"
                type="text"
                disabled={isProcessing}
                placeholder="e.g. Email Accounting Dept"
                className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                  ${
                    errors.name
                      ? "border-destructive focus:border-destructive"
                      : "border-border focus:border-primary"
                  }`}
              />
              {errors.name && (
                <p
                  className="text-xs text-destructive font-semibold pt-0.5"
                  role="alert"
                >
                  {errors.name.message}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="priority"
                className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none"
              >
                Priority
              </label>
              <input
                {...register("priority", { valueAsNumber: true })}
                id="priority"
                type="number"
                min="1"
                disabled={isProcessing}
                placeholder="100"
                className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                  ${
                    errors.priority
                      ? "border-destructive focus:border-destructive"
                      : "border-border focus:border-primary"
                  }`}
              />
              {errors.priority && (
                <p
                  className="text-xs text-destructive font-semibold pt-0.5"
                  role="alert"
                >
                  {errors.priority.message}
                </p>
              )}
            </div>
          </div>

          {/* Trigger Event Selector Field */}
          <div className="space-y-1.5">
            <label
              htmlFor="event"
              className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none"
            >
              Trigger Event
            </label>
            <select
              {...register("event")}
              id="event"
              disabled={isProcessing}
              className="w-full px-3 py-2 bg-background border border-border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary cursor-pointer"
            >
              <option value="WORK_ITEM_COMPLETED">
                Document Completed (WORK_ITEM_COMPLETED)
              </option>
              <option value="WORK_ITEM_CREATED">
                Document Uploaded (WORK_ITEM_CREATED)
              </option>
              <option value="WORK_ITEM_FAILED">
                Processing Failed (WORK_ITEM_FAILED)
              </option>
              <option value="WORK_ITEM_REPROCESSED">
                Document Reprocessed (WORK_ITEM_REPROCESSED)
              </option>
            </select>
          </div>

          {/* Symmetrical Evaluation Rule Constraints (IF criteria supporting multiple conditions) */}
          <div className="border border-border/60 rounded-xl p-4 bg-muted/10 space-y-4">
            {/* Logic Group Match Options header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-border/40 pb-2 gap-2 select-none">
              <span className="text-xs font-black uppercase tracking-wider text-primary flex items-center">
                <Play className="h-3.5 w-3.5 mr-1.5 fill-primary text-primary flex-shrink-0 animate-pulse" />
                Condition Constraint Criteria
              </span>

              <div className="flex items-center space-x-3.5 bg-background border border-border/60 px-2 py-1.5 rounded-lg text-xs font-bold shadow-sm">
                <span className="text-[10px] text-muted-foreground">
                  Match:
                </span>
                <label className="flex items-center space-x-1.5 cursor-pointer">
                  <input
                    type="radio"
                    value="AND"
                    disabled={isProcessing}
                    {...register("logic_operator")}
                    className="text-primary focus:ring-primary/20 cursor-pointer h-3.5 w-3.5"
                  />
                  <span>ALL (AND)</span>
                </label>
                <label className="flex items-center space-x-1.5 cursor-pointer">
                  <input
                    type="radio"
                    value="OR"
                    disabled={isProcessing}
                    {...register("logic_operator")}
                    className="text-primary focus:ring-primary/20 cursor-pointer h-3.5 w-3.5"
                  />
                  <span>ANY (OR)</span>
                </label>
              </div>
              {errors.logic_operator && (
                <p
                  className="text-xs text-destructive font-semibold"
                  role="alert"
                >
                  {errors.logic_operator.message}
                </p>
              )}
            </div>

            {/* Dynamic Conditions List Timeline stack */}
            <div className="space-y-4">
              {fields.map((fieldItem: typeof fields[number], idx: number) => {
                const currentFieldPath = watchedConditions[idx]?.field ?? "";
                const currentMeta = AUTOMATION_FIELDS_MAP[currentFieldPath];
                const allowedOps = getAllowedOperators(currentFieldPath);

                return (
                  <div
                    key={fieldItem.id}
                    className="p-4 bg-background border border-border/60 rounded-xl space-y-3 relative group transition-all duration-200 hover:border-border hover:shadow-sm"
                  >
                    {/* Item row details */}
                    <div className="flex justify-between items-center select-none border-b border-border/10 pb-1.5">
                      <span className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">
                        Condition #{idx + 1}
                      </span>

                      {fields.length > 1 && (
                        <button
                          type="button"
                          disabled={isProcessing}
                          onClick={() => remove(idx)}
                          className="p-1 rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-all"
                          title="Remove condition"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {/* Field Path Selector */}
                      <div className="space-y-1.5">
                        <label
                          htmlFor={`field-${idx}`}
                          className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none"
                        >
                          Evaluated Field
                        </label>
                        <select
                          id={`field-${idx}`}
                          disabled={isProcessing}
                          className="w-full px-3 py-2 bg-background border border-border rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 cursor-pointer"
                          {...register(`conditions.${idx}.field`)}
                          onChange={(e) => {
                            const val = e.target.value;

                            setValue(`conditions.${idx}.field`, val);

                            const ops = getAllowedOperators(val);

                            const currentOperator =
                              watchedConditions[idx]?.operator;

                            if (
                              ops.length > 0 &&
                              (!currentOperator ||
                                !ops.includes(currentOperator))
                            ) {
                              setValue(`conditions.${idx}.operator`, ops[0]!);
                            }
                          }}
                        >
                          {Object.entries(categorizedFields).map(
                            ([category, itemFields]) => (
                              <optgroup key={category} label={category}>
                                {itemFields.map((f) => (
                                  <option key={f.value} value={f.value}>
                                    {f.label}
                                  </option>
                                ))}
                              </optgroup>
                            )
                          )}
                        </select>
                        {currentMeta?.description && (
                          <p className="text-[10px] text-muted-foreground leading-relaxed select-none">
                            {currentMeta.description}
                          </p>
                        )}
                        {errors.conditions?.[idx]?.field && (
                          <p
                            className="text-[10px] text-destructive font-semibold"
                            role="alert"
                          >
                            {errors.conditions[idx]?.field?.message}
                          </p>
                        )}
                      </div>

                      {/* Logic Operator dropdown */}
                      <div className="space-y-1.5">
                        <label
                          htmlFor={`operator-${idx}`}
                          className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none"
                        >
                          Logic Operator
                        </label>
                        <select
                          id={`operator-${idx}`}
                          disabled={isProcessing || allowedOps.length === 0}
                          className="w-full px-3 py-2 bg-background border border-border rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 cursor-pointer disabled:opacity-50"
                          {...register(`conditions.${idx}.operator`)}
                        >
                          {allowedOps.map((op) => (
                            <option key={op} value={op}>
                              {OPERATOR_LABELS[op] ?? op}
                            </option>
                          ))}
                        </select>
                        {errors.conditions?.[idx]?.operator && (
                          <p
                            className="text-[10px] text-destructive font-semibold"
                            role="alert"
                          >
                            {errors.conditions[idx]?.operator?.message}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Match Value constraints */}
                    <div className="space-y-1.5">
                      <label
                        htmlFor={`value-${idx}`}
                        className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none"
                      >
                        Target Match Value
                      </label>
                      <input
                        id={`value-${idx}`}
                        type="text"
                        disabled={isProcessing}
                        placeholder={
                          currentMeta
                            ? `e.g. ${currentMeta.example}`
                            : "e.g. Match value"
                        }
                        className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                          ${
                            errors.conditions?.[idx]?.value
                              ? "border-destructive focus:border-destructive"
                              : "border-border focus:border-primary"
                          }`}
                        {...register(`conditions.${idx}.value`)}
                      />
                      {errors.conditions?.[idx]?.value && (
                        <p
                          className="text-xs text-destructive font-semibold"
                          role="alert"
                        >
                          {errors.conditions[idx]?.value?.message}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Append conditions activator */}
            <button
              type="button"
              disabled={isProcessing}
              onClick={() =>
                append({
                  field: "classification_details.document_classification",
                  operator: "EQUALS",
                  value: "",
                })
              }
              className="w-full py-2.5 bg-background border border-dashed border-border/80 hover:border-primary/50 text-muted-foreground hover:text-primary transition-all rounded-xl text-xs font-bold flex items-center justify-center space-x-1.5 focus:outline-none focus:ring-2 focus:ring-primary/10 select-none disabled:opacity-50"
            >
              <Plus className="h-4 w-4" />
              <span>Add Condition</span>
            </button>
          </div>

          {/* Action Dispatch Configurations Panel (THEN actions) */}
          <div className="space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">
              Action Configurations
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Locked Dispatcher Channels Type */}
              <div className="space-y-1.5">
                <label
                  htmlFor="action_type"
                  className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none"
                >
                  Action Type
                </label>
                <select
                  {...register("action_type")}
                  id="action_type"
                  disabled
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm font-semibold cursor-not-allowed opacity-80"
                >
                  <option value="SEND_EMAIL">Send SMTP Email</option>
                </select>
              </div>

              {/* Dynamic Recipient email Field */}
              <div className="space-y-1.5">
                <label
                  htmlFor="recipient"
                  className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none"
                >
                  Recipient Email
                </label>
                <input
                  {...register("recipient")}
                  id="recipient"
                  type="email"
                  disabled={isProcessing}
                  placeholder="billing@company.com"
                  className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                    ${
                      errors.recipient
                        ? "border-destructive focus:border-destructive"
                        : "border-border focus:border-primary"
                    }`}
                />
                {errors.recipient && (
                  <p
                    className="text-xs text-destructive font-semibold pt-0.5"
                    role="alert"
                  >
                    {errors.recipient.message}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Footer Interactive Trigger Panels */}
          <footer className="flex items-center justify-end space-x-3 pt-4 border-t border-border/40 select-none">
            <button
              type="button"
              onClick={onClose}
              disabled={isProcessing}
              className="px-4 py-2 bg-background border border-border text-muted-foreground hover:text-foreground font-semibold text-xs rounded-lg hover:bg-muted/40 transition-all disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isProcessing}
              className="px-5 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none flex items-center justify-center min-w-[100px]"
            >
              {isProcessing ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
                  Saving...
                </>
              ) : isEditMode ? (
                "Update Rule"
              ) : ruleToDuplicate ? (
                "Duplicate Rule"
              ) : (
                "Create Rule"
              )}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
};

export default RuleForm;
