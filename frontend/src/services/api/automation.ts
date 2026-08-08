import apiClient from "@/services/api/client";
import { AUTOMATION_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AutomationRule,
  AutomationLog,
  AutomationRuleCreateRequest,
  AutomationRuleUpdateRequest,
  AutomationRuleTestResponse,
} from "@/types/automation";

export const createAutomationRule = async (
  workspaceId: string,
  data: AutomationRuleCreateRequest,
): Promise<AutomationRule> => {
  const response = await apiClient.post<AutomationRule>(
    AUTOMATION_ENDPOINTS.rules(workspaceId),
    data,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const getAutomationRules = async (
  workspaceId: string,
): Promise<readonly AutomationRule[]> => {
  const response = await apiClient.get<readonly AutomationRule[]>(
    AUTOMATION_ENDPOINTS.rules(workspaceId),
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const getAutomationRuleDetails = async (
  workspaceId: string,
  ruleId: string,
): Promise<AutomationRule> => {
  const response = await apiClient.get<AutomationRule>(
    AUTOMATION_ENDPOINTS.rule(workspaceId, ruleId),
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const updateAutomationRule = async (
  workspaceId: string,
  ruleId: string,
  data: AutomationRuleUpdateRequest,
): Promise<AutomationRule> => {
  const response = await apiClient.patch<AutomationRule>(
    AUTOMATION_ENDPOINTS.rule(workspaceId, ruleId),
    data,
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const deleteAutomationRule = async (
  workspaceId: string,
  ruleId: string,
): Promise<void> => {
  await apiClient.delete(AUTOMATION_ENDPOINTS.rule(workspaceId, ruleId), {
    headers: {
      "Accept": "application/json",
    },
  });
};

export const getAutomationLogs = async (
  workspaceId: string,
): Promise<readonly AutomationLog[]> => {
  const response = await apiClient.get<readonly AutomationLog[]>(
    AUTOMATION_ENDPOINTS.logs(workspaceId),
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const testAutomationRule = async (
  workspaceId: string,
  ruleId: string,
  workItemId: string,
): Promise<AutomationRuleTestResponse> => {
  const response = await apiClient.post<AutomationRuleTestResponse>(
    `${AUTOMATION_ENDPOINTS.rule(workspaceId, ruleId)}/test`,
    { work_item_id: workItemId },
    {
      headers: {
        "Accept": "application/json",
      },
    },
  );
  return response.data;
};

export const automationApi = {
  createAutomationRule,
  getAutomationRules,
  getAutomationRuleDetails,
  updateAutomationRule,
  deleteAutomationRule,
  getAutomationLogs,
  testAutomationRule,
};

export default automationApi;
