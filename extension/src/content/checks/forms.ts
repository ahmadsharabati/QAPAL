/**
 * Form quality checks — validates input types, required attrs, autocomplete.
 *
 * Rules:
 *   forms/input-type    — Wrong or missing input types
 *   forms/required      — Missing required on critical fields
 *   forms/autocomplete  — Missing autocomplete attribute
 *   forms/password      — Password inputs missing autocomplete hint
 */

import type { RawIssue } from "../types";
import { getSelector, snippetHTML } from "../utils";

const CATEGORY = "forms";

// Heuristic: input names/ids that suggest email
const EMAIL_PATTERNS = /email|e-mail|correo/i;
const PHONE_PATTERNS = /phone|tel|mobile|cell|fono/i;
const URL_PATTERNS = /website|url|homepage|site/i;

export function checkInputTypes(): RawIssue[] {
  const issues: RawIssue[] = [];
  const inputs = document.querySelectorAll("input");

  for (const input of inputs) {
    const type = input.type.toLowerCase();
    const name = (input.name || input.id || "").toLowerCase();
    const placeholder = (input.placeholder || "").toLowerCase();
    const label = name + " " + placeholder;

    if (type === "text" || type === "") {
      if (EMAIL_PATTERNS.test(label)) {
        issues.push({
          ruleId: "forms/input-type",
          severity: "medium",
          category: CATEGORY,
          title: "Email input should use type=\"email\"",
          description: `Input "${name || "unnamed"}" appears to be an email field but uses type="text". Using type="email" enables mobile keyboards and browser validation.`,
          selector: getSelector(input),
          element: snippetHTML(input),
        });
      }

      if (PHONE_PATTERNS.test(label)) {
        issues.push({
          ruleId: "forms/input-type",
          severity: "medium",
          category: CATEGORY,
          title: "Phone input should use type=\"tel\"",
          description: `Input "${name || "unnamed"}" appears to be a phone field but uses type="text". Using type="tel" enables the phone keyboard on mobile.`,
          selector: getSelector(input),
          element: snippetHTML(input),
        });
      }

      if (URL_PATTERNS.test(label)) {
        issues.push({
          ruleId: "forms/input-type",
          severity: "medium",
          category: CATEGORY,
          title: "URL input should use type=\"url\"",
          description: `Input "${name || "unnamed"}" appears to be a URL field but uses type="text". Using type="url" enables browser validation and correct mobile keyboard.`,
          selector: getSelector(input),
          element: snippetHTML(input),
        });
      }
    }
  }

  return issues;
}

export function checkRequired(): RawIssue[] {
  const issues: RawIssue[] = [];
  const forms = document.querySelectorAll("form");

  for (const form of forms) {
    // Check for email-like inputs without required
    const inputs = form.querySelectorAll("input, select, textarea");
    for (const input of inputs) {
      const el = input as HTMLInputElement;
      const type = el.type?.toLowerCase();
      if (type === "hidden" || type === "submit" || type === "button" || type === "reset") continue;

      const name = (el.name || el.id || "").toLowerCase();
      const isLikelyCritical =
        EMAIL_PATTERNS.test(name) ||
        type === "email" ||
        type === "password";

      if (isLikelyCritical && !el.required && !el.getAttribute("aria-required")) {
        issues.push({
          ruleId: "forms/required",
          severity: "medium",
          category: CATEGORY,
          title: `Critical field missing required attribute`,
          description: `The ${type || "text"} input "${name || "unnamed"}" looks critical but has no required attribute. This allows empty submissions.`,
          selector: getSelector(el),
          element: snippetHTML(el),
        });
      }
    }
  }

  return issues;
}

export function checkAutocomplete(): RawIssue[] {
  const issues: RawIssue[] = [];

  // Common fields that benefit from autocomplete
  const autocompletable = document.querySelectorAll(
    'input[type="text"], input[type="email"], input[type="tel"], input[type="url"], input[name*="name"], input[name*="address"], input[name*="city"], input[name*="zip"], input[name*="postal"]'
  );

  for (const input of autocompletable) {
    const el = input as HTMLInputElement;
    if (el.getAttribute("autocomplete")) continue;

    const name = (el.name || el.id || "").toLowerCase();
    // Only flag inputs that clearly map to autocomplete tokens
    const knownFields = /^(name|email|tel|phone|address|city|state|zip|postal|country|given|family|cc-)/;
    if (knownFields.test(name)) {
      issues.push({
        ruleId: "forms/autocomplete",
        severity: "minor",
        category: CATEGORY,
        title: "Missing autocomplete attribute",
        description: `Input "${name}" has no autocomplete attribute. Adding autocomplete helps browsers auto-fill user data.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }

  return issues;
}

export function checkPasswordInputs(): RawIssue[] {
  const issues: RawIssue[] = [];
  const passwords = document.querySelectorAll('input[type="password"]');

  for (const input of passwords) {
    const el = input as HTMLInputElement;
    const ac = el.getAttribute("autocomplete");

    if (!ac || (ac !== "new-password" && ac !== "current-password")) {
      issues.push({
        ruleId: "forms/password",
        severity: "medium",
        category: CATEGORY,
        title: "Password input missing autocomplete hint",
        description: `Password input has no autocomplete="current-password" or autocomplete="new-password". This prevents password managers from working correctly.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }

  return issues;
}

// ── Export all checks ──────────────────────────────────────────────────

export function runFormsChecks(): RawIssue[] {
  return [
    ...checkInputTypes(),
    ...checkRequired(),
    ...checkAutocomplete(),
    ...checkPasswordInputs(),
  ];
}
