import { z } from "zod";

/**
 * ==========================================================
 * Reusable Validators
 * ==========================================================
 */

const emailSchema = z
  .string()
  .trim()
  .min(1, "Email is required.")
  .email("Please enter a valid email address.")
  .max(255, "Email cannot exceed 255 characters.");

const passwordSchema = z
  .string()
  .min(8, "Password must be at least 8 characters.")
  .max(128, "Password cannot exceed 128 characters.");

/**
 * ==========================================================
 * Login
 * ==========================================================
 */

export const loginSchema = z.object({
  email: emailSchema,
  password: z
    .string()
    .min(1, "Password is required."),
});

export type LoginInput =
  z.infer<typeof loginSchema>;

/**
 * ==========================================================
 * Register
 * ==========================================================
 */

export const registerSchema = z
  .object({
    email: emailSchema,
    password: passwordSchema,
    confirmPassword: z
      .string()
      .min(
        1,
        "Confirm password is required.",
      ),
  })
  .refine(
    (data) =>
      data.password ===
      data.confirmPassword,
    {
      message: "Passwords do not match.",
      path: ["confirmPassword"],
    },
  );

export type RegisterInput =
  z.infer<typeof registerSchema>;

  /**
 * ==========================================================
 * Common Validators
 * ==========================================================
 */

export const uuidSchema = z
  .string()
  .uuid("Invalid identifier.");

export const requiredStringSchema = z
  .string()
  .trim()
  .min(1, "This field is required.");

export const optionalStringSchema = z
  .string()
  .trim()
  .optional();

export const positiveIntegerSchema = z
  .number()
  .int()
  .positive();

export {
  emailSchema,
  passwordSchema,
};
