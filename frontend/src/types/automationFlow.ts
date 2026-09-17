/**
 * ARCH-37 — the flow builder's contract with the server.
 *
 * Everything the builder may offer (triggers, actions, operators, fields,
 * endpoints, destinations, profiles, roles) arrives in one catalog response
 * from GET /workspaces/{id}/automation/catalog. The console holds no trigger or
 * action list of its own; `verify_arch37.py` fails if one appears.
 */

export type FlowFieldType = "string" | "number" | "boolean" | "array" | "date";

export interface FlowField {
  readonly key: string;
  readonly label: string;
  readonly type: FlowFieldType;
  readonly example: string;
  readonly description: string;
  readonly source: "event" | "document";
}

export interface FlowTrigger {
  readonly key: string;
  readonly label: string;
  readonly category: string;
  readonly description: string;
  readonly event_types: readonly string[];
  readonly fields: readonly FlowField[];
  readonly capability: string | null;
  readonly has_document: boolean;
  readonly excluded_actions: readonly string[];
  readonly runs_during_review: boolean;
  readonly available: boolean;
}

export interface JsonSchemaProperty {
  readonly type?: string;
  readonly title?: string;
  readonly description?: string;
  readonly default?: unknown;
  readonly enum?: readonly string[];
  readonly minimum?: number;
  readonly maximum?: number;
  readonly maxLength?: number;
  readonly items?: JsonSchemaProperty;
  readonly anyOf?: readonly JsonSchemaProperty[];
  readonly format?: string;
}

export interface FlowActionSchema {
  readonly properties?: Readonly<Record<string, JsonSchemaProperty>>;
  readonly required?: readonly string[];
}

export type FlowMinimumRole = "WORKSPACE_ADMIN" | "ORGANIZATION_ADMIN";

export interface FlowAction {
  readonly type: string;
  readonly label: string;
  readonly description: string;
  readonly category: string;
  readonly capability: string | null;
  readonly addon: string | null;
  readonly minimum_role: FlowMinimumRole;
  readonly available: boolean;
  readonly unavailable_reason: string | null;
  readonly commercial: boolean;
  readonly needs_document: boolean;
  readonly template_fields: readonly string[];
  readonly aliases: readonly string[];
  readonly config_schema: FlowActionSchema;
}

export interface FlowEndpointOption {
  readonly id: string;
  readonly label: string;
  readonly host: string;
}

export interface FlowDestinationOption {
  readonly id: string;
  readonly label: string;
  readonly kind: string;
}

export interface FlowCatalog {
  readonly triggers: readonly FlowTrigger[];
  readonly actions: readonly FlowAction[];
  readonly operators: Readonly<Record<FlowFieldType, readonly string[]>>;
  readonly valueless_operators: readonly string[];
  readonly template_variables: readonly string[];
  readonly document_fields: readonly FlowField[];
  readonly resources: {
    readonly webhook_endpoints: readonly FlowEndpointOption[];
    readonly warehouse_destinations: readonly FlowDestinationOption[];
    readonly redaction_profiles: readonly string[];
    readonly organization_roles: readonly string[];
    readonly export_datasets: readonly string[];
    readonly mutable_fields: readonly string[];
  };
  readonly limits: {
    readonly triggers: number;
    readonly groups: number;
    readonly conditions_per_group: number;
    readonly actions_per_branch: number;
  };
}

export interface FlowCondition {
  readonly field: string;
  readonly operator: string;
  readonly value: string;
}

export interface FlowConditionGroup {
  readonly logic_operator: "AND" | "OR";
  readonly conditions: readonly FlowCondition[];
}

export interface FlowActionEntry {
  readonly action_type: string;
  readonly config: Readonly<Record<string, unknown>>;
}

/** One row of GET .../automation/executions/{id}/nodes. */
export interface AutomationNodeRun {
  readonly id: string;
  readonly node_key: string | null;
  readonly node_type: string | null;
  readonly sequence: number;
  readonly status: string;
  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly attempt: number;
  readonly error: string | null;
  readonly action_type: string | null;
  readonly external_ref: string | null;
  readonly duration_ms: number | null;
  readonly outcome: string | null;
}
