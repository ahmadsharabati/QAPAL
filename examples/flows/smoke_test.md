# User Flow: Authentication & Dashboard

This PRD defines the critical "Happy Path" for my application.
QAPAL will use this to generate and verify a Playwright test.

## 1. Login Flow (Critical)
- **Navigate** to `/login`
- **Fill** the email field with `{{QAPAL_TEST_USER}}`
- **Fill** the password field with `{{QAPAL_TEST_PASS}}`
- **Click** the "Sign In" button
- **Verify** that the URL contains `/dashboard`
- **Verify** that an element with the text "Welcome back" is visible

## 2. Profile Update
- **Navigate** to `/settings/profile`
- **Change** the "Display Name" field to "QA Test User"
- **Click** "Save Changes"
- **Verify** a success toast or message appears

---

### Tips for Success:
- Use **Role** or **Text** names in your descriptions (e.g., "the Submit button").
- Avoid using implementation details (like CSS classes) in the PRD.
- QAPAL will automatically handle "Element Not Found" errors via its Reliability Layer.
