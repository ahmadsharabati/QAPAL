# PRD: Automation Exercise — E-Commerce Practice Site

## Overview
AutomationExercise.com is a full-featured e-commerce practice site for automation engineers.
It has product browsing, category filters, product search, product detail pages, cart, and account registration/login.

## Test Scenarios

### 1. Browse homepage products
As a user, I want to land on the homepage and see a list of featured products.
- Navigate to the homepage
- Verify featured products are listed with names and prices

### 2. Navigate to products page
As a user, I want to go to the full product catalogue.
- Click the "Products" link in the navigation
- Verify the products listing page loads and shows multiple products

### 3. Search for a product
As a user, I want to search for a specific product by keyword.
- On the products page, type "top" into the search box
- Click the search submit button
- Verify search results are shown

### 4. View product details
As a user, I want to click on a product and see its full detail page.
- On the products page, click "View Product" on any item
- Verify the product detail page loads with a product name, category, price, and availability

### 5. Add product to cart
As a user, I want to add a product to my cart from the product detail page.
- Navigate to a product detail page (e.g. /product_details/1)
- Click the "Add to cart" button
- Verify a confirmation modal or cart update is shown
