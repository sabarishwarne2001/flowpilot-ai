export type AIProvider =
    | "GROQ"
    | "GEMINI"
    | "OPENAI"
    | "CLAUDE";

export interface AISettings {
    id: string;
    user_id: string;

    provider: AIProvider;

    model: string;

    temperature: number;

    max_output_tokens: number;

    top_p: number;

    frequency_penalty: number;

    presence_penalty: number;

    input_cost_per_1k_tokens: number;

    output_cost_per_1k_tokens: number;

    system_prompt_version: string;

    prompt_version: string;

    enable_token_tracking: boolean;

    enable_streaming: boolean;

    created_at: string;

    updated_at: string;
}

export type UpdateAISettingsRequest = Omit<
    AISettings,
    "id" | "user_id" | "created_at" | "updated_at"
>;
