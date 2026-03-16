# Books to Scrape — Test Suite

## Feature: Browse and discover books

### TC001 — Homepage loads with books
Navigate to the bookstore homepage.
Assert the page title contains "Books".
Assert at least one book is visible on the page.

### TC002 — Browse a category
Navigate to the bookstore homepage.
Click on the "Mystery" category in the sidebar.
Assert the URL contains "mystery".
Assert books are visible in the results.

### TC003 — View book details
Navigate to the bookstore homepage.
Click on the first book shown on the homepage.
Assert the URL contains "/catalogue/".
Assert the book title heading is visible on the page.
