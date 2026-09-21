export interface AIFieldHelp {
  title: string;
  description: string;
  recommended?: string | undefined;
}

// ARCH40-S2:ai-field-help. The three dead fields' help entries are gone
// with the fields.
export const AI_FIELD_HELP = {
  provider: {
    title: "AI Provider",
    description:
      "Select the AI service used to generate responses.",
    recommended: "Use the provider configured by your administrator.",
  },

  model: {
    title: "Model",
    description:
      "Specifies which AI model is used. Different models offer different speeds, costs, and reasoning capabilities.",
    recommended:
      "Use the default model unless you have a specific requirement.",
  },

  temperature: {
    title: "Temperature",
    description:
      "Controls how creative the AI responses are. Lower values produce more consistent answers, while higher values allow more variation.",
    recommended: "0.7 is recommended for most business workflows.",
  },

  top_p: {
    title: "Top P",
    description:
      "Limits how many possible word choices the AI considers before generating a response. Most users should leave this unchanged.",
    recommended: "0.9 works well for almost every use case.",
  },

  max_output_tokens: {
    title: "Max Output Tokens",
    description:
      "Sets the maximum length of the AI response. Increase this only if longer responses are regularly required.",
    recommended: "Keep the default unless responses are being cut off.",
  },

  frequency_penalty: {
    title: "Frequency Penalty",
    description: "Reduces repeated words and phrases in generated responses.",
    recommended: "Leave at 0 unless repetition becomes noticeable.",
  },

  presence_penalty: {
    title: "Presence Penalty",
    description:
      "Encourages the AI to introduce new ideas instead of repeating previous topics.",
    recommended: "Leave at 0 for most business applications.",
  },






  enable_streaming: {
    title: "Streaming Responses",
    description:
      "Displays AI responses as they are generated instead of waiting for the full response.",
    recommended: "Keep enabled for the best user experience.",
  },
};

