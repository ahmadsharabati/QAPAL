# Product Requirements Document: Checkboxes Demo

## Feature: Checkbox Interaction
As a user, I want to be able to toggle checkboxes and verify their state so that I can ensure the UI reflects my selections.

## Acceptance Criteria:
1. The user navigates to the Checkboxes demo (`https://demo.playwright.dev/checkboxes/`).
2. The page displays a list of checkboxes.
3. The user clicks the first checkbox.
4. The test asserts that the first checkbox is now checked.
5. The user clicks the second checkbox.
6. The test asserts that the second checkbox is now unchecked (if it was initially checked) or checked (if initially unchecked).

## Steps and Assertions:
- **Step 1**: `navigate` to `https://demo.playwright.dev/checkboxes/`
- **Step 2**: `click` on the first checkbox element (locator: `role=checkbox >> nth=0`)
- **Assertion 1**: `element_checked` on the first checkbox (`role=checkbox >> nth=0`)
- **Step 3**: `click` on the second checkbox element (locator: `role=checkbox >> nth=1`)
- **Assertion 2**: `element_checked` on the second checkbox (`role=checkbox >> nth=1`)
