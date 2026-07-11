import apiClient from "@/services/api/client";
import type {
  AutomationRule,
  AutomationLog,
  AutomationRuleCreateRequest,
  AutomationRuleUpdateRequest,
} from "@/types/automation";

/**
 * Automation Route Path Endpoints Configurations.
 */
const AUTOMATION_ENDPOINTS = {
  BASE: "/automation",
  RULE: (id: string) => `/automation/${id}`,
  LOGS: "/automation/logs",
} as const;

/**
 * Dispatches a request to compile and persist a new user-defined Automation Rule.
 *
 * @param data - Input schema validating event triggers and JSON action configs.
 * @returns Serialized Rule metadata returned by the PostgreSQL transaction engine.
 */
export const createAutomationRule = async (
  data: AutomationRuleCreateRequest,
): Promise<AutomationRule> => {
  const response = await apiClient.post<AutomationRule>(
    AUTOMATION_ENDPOINTS.BASE,
    data,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Retrieves a list of active and inactive Automation Rules owned by the user.
 *
 * @returns Un-sorted array list of custom rules configured in the workspace.
 */
export const getAutomationRules = async (): Promise<readonly AutomationRule[]> => {
  const response = await apiClient.get<readonly AutomationRule[]>(
    AUTOMATION_ENDPOINTS.BASE,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Retrieves detailed configurations of a specific Automation Rule by primary UUID.
 *
 * @param ruleId - Primary key UUID identifying the target rule.
 * @returns Complete Rule parameters.
 */
export const getAutomationRuleDetails = async (
  ruleId: string,
): Promise<AutomationRule> => {
  const response = await apiClient.get<AutomationRule>(
    AUTOMATION_ENDPOINTS.RULE(ruleId),
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Executes a partial patch update on an existing Automation Rule (e.g. toggling active states).
 *
 * @param ruleId - Primary key UUID identifying the target rule.
 * @param data - Updated schema fields to merge with the existing DB rule entry.
 * @returns The updated Automation Rule parameters.
 */
export const updateAutomationRule = async (
  ruleId: string,
  data: AutomationRuleUpdateRequest,
): Promise<AutomationRule> => {
  const response = await apiClient.patch<AutomationRule>(
    AUTOMATION_ENDPOINTS.RULE(ruleId),
    data,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

/**
 * Removes a specific Automation Rule from PostgreSQL, cascading deletes to logs.
 *
 * @param ruleId - Primary key UUID identifying the target rule.
 */
export const deleteAutomationRule = async (ruleId: string): Promise<void> => {
  await apiClient.delete(AUTOMATION_ENDPOINTS.RULE(ruleId), {
    headers: {
      "Accept": "application/json",
    },
  });
};

/**
 * Retrieves a chronological log matrix detailing all rule-matching execution runs.
 *
 * @returns Execution history list documenting run dates and status traces.
 */
export const getAutomationLogs = async (): Promise<readonly AutomationLog[]> => {
  const response = await apiClient.get<readonly AutomationLog[]>(
    AUTOMATION_ENDPOINTS.LOGS,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

// Export unified API namespace wrapper
export const automationApi = {
  createAutomationRule,
  getAutomationRules,
  getAutomationRuleDetails,
  updateAutomationRule,
  deleteAutomationRule,
  getAutomationLogs,
};

export default automationApi;
