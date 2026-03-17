from playwright.sync_api import Page, expect

def test_ast_torture(page: Page):
    # 1. Obscure spacing and comments
    page.locator(
        # comment here
        ".class" # another comment
        + # plus op
        "-suffix" 
    ).click()

    # 2. Variable assignments (current AST might miss this)
    selector_var = "div#main"
    page.locator(selector_var).hover()

    # 3. List comprehension / indirect usage
    items = ["#item1", "#item2"]
    for item in items:
        page.locator(item).click()

    # 4. Deeply nested calls
    page.get_by_role(
        "button", 
        name=get_label_from_function("submit")
    ).click()

    # 5. Locators as arguments to other functions
    validate_element(page.get_by_text("Confirm Order"))

def get_label_from_function(key):
    return key.upper()

def validate_element(locator):
    expect(locator).to_be_visible()
