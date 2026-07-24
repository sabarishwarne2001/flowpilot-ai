export interface TokenUsage {
    provider: string;
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated_cost: number;
}

export interface AIConnectionTestResponse {
    success: boolean;
    provider: string;
    model: string;
    latency_ms: number;
    response: string;
    token_usage: TokenUsage;
}
