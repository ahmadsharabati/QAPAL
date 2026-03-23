# DemoQA — Form Submission & Bookstore Navigation

## Target URL
https://demoqa.com

## Test Cases

### TC1: Text Box Form Submission
**Goal:** Fill out the text box form and submit it.
**Preconditions:** None.
**Steps:**
1. Navigate to https://demoqa.com/text-box
2. Fill "Full Name" field with "QAPal Test User"
3. Fill "Email" field with "qapal@test.com"
4. Fill "Current Address" field with "123 Test Street"
5. Fill "Permanent Address" field with "456 Main Avenue"
6. Click the "Submit" button
**Expected:**
- The output area should appear showing the submitted data
- The output should contain "QAPal Test User"

### TC2: Check Box Interaction
**Goal:** Expand and select check boxes in the tree.
**Preconditions:** None.
**Steps:**
1. Navigate to https://demoqa.com/checkbox
2. Click the expand arrow to open the tree
3. Click the "Home" checkbox to select all items
**Expected:**
- The result text should appear showing selected items
- The URL should contain "checkbox"

### TC3: Radio Button Selection
**Goal:** Select a radio button option.
**Preconditions:** None.
**Steps:**
1. Navigate to https://demoqa.com/radio-button
2. Click the "Impressive" radio button
**Expected:**
- A success message should appear confirming the selection
- The text "Impressive" should be displayed

### TC4: Web Tables Interaction
**Goal:** Navigate to the web tables page and verify the table is loaded.
**Preconditions:** None.
**Steps:**
1. Navigate to https://demoqa.com/webtables
**Expected:**
- The page should load with a table containing data
- A search box should be visible
- An "Add" button should be present

### TC5: Bookstore Navigation
**Goal:** Navigate through the bookstore pages.
**Preconditions:** None.
**Steps:**
1. Navigate to https://demoqa.com/books
2. Verify the book list is visible
3. Navigate to https://demoqa.com/elements
**Expected:**
- The books page should load with a list of books
- Elements page should load after navigation
- The URL should contain "elements"
