# Login Feature Test

## Feature: User Authentication

### Scenario 1: Successful login
Navigate to the login page at https://the-internet.herokuapp.com/login.
Fill in the username field with "tomsmith".
Fill in the password field with "SuperSecretPassword!".
Click the Login button.
After login, the user is redirected to a secure area page.
Assert the URL contains "/secure".
Assert a success flash message is visible on the page confirming the login.

### Scenario 2: Failed login with wrong credentials
Navigate to the login page at https://the-internet.herokuapp.com/login.
Fill in the username field with "wronguser".
Fill in the password field with "wrongpass".
Click the Login button.
Assert the user remains on the login page (URL still contains "/login").
Assert an error flash message is displayed indicating invalid credentials.
