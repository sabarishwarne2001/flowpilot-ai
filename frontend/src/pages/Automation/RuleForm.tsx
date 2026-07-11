import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X, Loader2, Play } from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { ApiError } from "@/services/api/client";
import type { AutomationRule, AutomationRuleCreateRequest, AutomationRuleUpdateRequest } from "@/types/automation";

// Centralized query key constant mapping to core rules lists
const RULES_QUERY_KEY = ["automation-rules"] as const; // Matches the registered list cache keys

// Declarative Zod validation schema to govern rule configurations
const ruleFormValidationSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Rule name is required.")
    .max(100, "Rule name cannot exceed 100 characters."),
  event: z.enum([
    "WORK_ITEM_CREATED",
    "WORK_ITEM_COMPLETED",
    "WORK_ITEM_FAILED",
    "WORK_ITEM_REPROCESSED"
  ]),
  field: z
    .string()
    .trim()
    .min(1, "Target comparison field is required.")
    .max(100, "Field label cannot exceed 100 characters."),
  operator: z.enum([
    "EQUALS",
    "NOT_EQUALS",
    "CONTAINS",
    "GREATER_THAN",
    "LESS_THAN",
    "GREATER_THAN_OR_EQUAL",
    "LESS_THAN_OR_EQUAL"
  ]),
  value: z
    .string()
    .trim()
    .min(1, "Comparison value is required.")
    .max(255, "Value cannot exceed 255 characters."),
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
}

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
}) => {
  const queryClient = useQueryClient();
  const isEditMode = ruleToEdit !== null;

  // Initialize input validator controllers with focus boundaries
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<RuleFormInput>({
    resolver: zodResolver(ruleFormValidationSchema),
    shouldFocusError: true,
    defaultValues: {
      name: "",
      event: "WORK_ITEM_COMPLETED",
      field: "classification_details.document_classification",
      operator: "EQUALS",
      value: "",
      action_type: "SEND_EMAIL",
      recipient: "",
    },
  });

  // Hydrate form defaults dynamically whenever edit contexts change
  useEffect(() => {
    if (ruleToEdit) {
      reset({
        name: ruleToEdit.name,
        event: ruleToEdit.event,
        field: ruleToEdit.field,
        operator: ruleToEdit.operator,
        value: ruleToEdit.value,
        action_type: ruleToEdit.action_type,
        recipient: (ruleToEdit.action_config?.recipient as string) ?? "",
      });
    } else {
      reset({
        name: "",
        event: "WORK_ITEM_COMPLETED",
        field: "classification_details.document_classification",
        operator: "EQUALS",
        value: "",
        action_type: "SEND_EMAIL",
        recipient: "",
      });
    }
  }, [ruleToEdit, reset]);

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
  const { mutateAsync: runCreateMutation, isPending: isCreating } = useMutation({
    mutationFn: automationApi.createAutomationRule,
    onSuccess: async () => {
      toast.success("Automation rule compiled successfully.");
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
  });

  // 2. Transaction mutation mapping Rule updates
  const { mutateAsync: runUpdateMutation, isPending: isUpdating } = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AutomationRuleUpdateRequest }) =>
      automationApi.updateAutomationRule(id, payload),
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
  });

  const onFormSubmit = async (data: RuleFormInput): Promise<void> => {
    // Compile dynamic parameters into the standard provider-agnostic JSON format
    const compiledPayload: AutomationRuleCreateRequest  = {
      name: data.name.trim(),
      event: data.event,
      field: data.field.trim(),
      operator: data.operator,
      value: data.value.trim(),
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
          <h2 id="rule-form-title" className="font-extrabold text-sm uppercase tracking-wider">
            {isEditMode ? "Modify Automation Rule" : "Configure New Rule"}
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
        <form onSubmit={handleSubmit(onFormSubmit)} className="p-6 space-y-4 max-h-[75vh] overflow-y-auto scrollbar" noValidate>
          {/* Rule Name Field */}
          <div className="space-y-1.5">
            <label htmlFor="name" className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">Rule Name</label>
            <input
              {...register("name")}
              id="name"
              type="text"
              disabled={isProcessing}
              placeholder="e.g. Email Accounting Dept"
              className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                ${errors.name ? "border-destructive focus:border-destructive" : "border-border focus:border-primary"}`}
            />
            {errors.name && <p className="text-xs text-destructive font-semibold pt-0.5" role="alert">{errors.name.message}</p>}
          </div>

          {/* Trigger Event Selector Field */}
          <div className="space-y-1.5">
            <label htmlFor="event" className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">Trigger Event</label>
            <select
              {...register("event")}
              id="event"
              disabled={isProcessing}
              className="w-full px-3 py-2 bg-background border border-border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary cursor-pointer"
            >
              <option value="WORK_ITEM_COMPLETED">Document Completed (WORK_ITEM_COMPLETED)</option>
              <option value="WORK_ITEM_CREATED">Document Uploaded (WORK_ITEM_CREATED)</option>
              <option value="WORK_ITEM_FAILED">Processing Failed (WORK_ITEM_FAILED)</option>
              <option value="WORK_ITEM_REPROCESSED">Document Reprocessed (WORK_ITEM_REPROCESSED)</option>
            </select>
          </div>

          {/* Symmetrical Evaluation Rule Constraints (IF criteria) */}
          <div className="p-4 bg-muted/20 border border-border rounded-xl space-y-4">
            <h3 className="text-xs font-black uppercase tracking-wider text-primary select-none flex items-center">
              <Play className="h-3.5 w-3.5 mr-1.5 fill-primary text-primary flex-shrink-0" />
              Condition Constraint Criteria
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Target JSON Path Field */}
              <div className="space-y-1.5">
                <label htmlFor="field" className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none">Evaluated Field</label>
                <input
                  {...register("field")}
                  id="field"
                  type="text"
                  disabled={isProcessing}
                  placeholder="e.g. total_amount"
                  className={`w-full px-3.5 py-2 bg-background border rounded-lg text-xs font-mono font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                    ${errors.field ? "border-destructive focus:border-destructive" : "border-border focus:border-primary"}`}
                />
                {errors.field && <p className="text-xs text-destructive font-semibold pt-0.5" role="alert">{errors.field.message}</p>}
              </div>

              {/* Match Operators Dropdown */}
              <div className="space-y-1.5">
                <label htmlFor="operator" className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none">Logic Operator</label>
                <select
                  {...register("operator")}
                  id="operator"
                  disabled={isProcessing}
                  className="w-full px-3 py-2 bg-background border border-border rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary cursor-pointer"
                >
                  <option value="EQUALS">Equals (==)</option>
                  <option value="NOT_EQUALS">Not Equals (!=)</option>
                  <option value="CONTAINS">Contains</option>
                  <option value="GREATER_THAN">Greater Than (&gt;)</option>
                  <option value="LESS_THAN">Less Than (&lt;)</option>
                  <option value="GREATER_THAN_OR_EQUAL">Greater Than or Equal (&gt;=)</option>
                  <option value="LESS_THAN_OR_EQUAL">Less Than or Equal (&lt;=)</option>
                </select>
              </div>
            </div>

            {/* Target Value Input */}
            <div className="space-y-1.5">
              <label
                htmlFor="value"
                className="text-[10px] font-black uppercase tracking-wider text-muted-foreground select-none"
              >
                Target Match Value
              </label>
              <input
                {...register("value")}
                id="value"
                type="text"
                disabled={isProcessing}
                placeholder="e.g. Invoice"
                className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                  ${errors.value ? "border-destructive focus:border-destructive" : "border-border focus:border-primary"}`}
              />
              {errors.value && <p className="text-xs text-destructive font-semibold pt-0.5" role="alert">{errors.value.message}</p>}
            </div>
          </div>

          {/* Action Dispatch Configurations Panel (THEN actions) */}
          <div className="space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">Action Configurations</h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Locked Dispatcher Channels Type */}
              <div className="space-y-1.5">
                <label htmlFor="action_type" className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">Action Type</label>
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
                <label htmlFor="recipient" className="text-xs font-bold uppercase tracking-wider text-muted-foreground select-none">Recipient Email</label>
                <input
                  {...register("recipient")}
                  id="recipient"
                  type="email"
                  disabled={isProcessing}
                  placeholder="billing@company.com"
                  className={`w-full px-3.5 py-2 bg-background border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/20
                    ${errors.recipient ? "border-destructive focus:border-destructive" : "border-border focus:border-primary"}`}
                />
                {errors.recipient && <p className="text-xs text-destructive font-semibold pt-0.5" role="alert">{errors.recipient.message}</p>}
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
              ) : (
                "Save Rules"
              )}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
};

export default RuleForm;
