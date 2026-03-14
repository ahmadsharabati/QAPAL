# PRD: Books to Scrape — Online Bookshop

## Overview
Books to Scrape is a bookshop web application. Users can browse books by category, view book details, and add books to a shopping basket.

## Test Scenarios

### 1. Browse the homepage
As a user, I want to land on the homepage and see a list of books with prices and ratings.
- Navigate to the homepage
- Verify books are visible

### 2. Filter by category
As a user, I want to click a book category from the sidebar and see only books in that category.
- Click a category link (e.g. Mystery, Science)
- Verify the page heading changes and books are listed

### 3. View book details
As a user, I want to click on a book and see its full details page.
- Click a book title from the listing page
- Verify the product detail page loads with a price and description

### 4. Add to basket
As a user, I want to add a book to my basket from the homepage listing.
- Navigate to the homepage
- Click the "Add to basket" button on any book in the listing
- Verify the basket mini-count badge is visible (the basket icon in the header shows a count)

### 5. Browse multiple pages
As a user, I want to navigate to the next page of results.
- On the homepage, click the "next" pagination link
- Verify the page 2 of results loads
