/**
 * Utility functions for the content script check modules.
 */

/**
 * Build a minimal CSS selector for an element (for issue reporting).
 */
export function getSelector(el: Element): string {
  if (el.id) return `#${CSS.escape(el.id)}`;

  const tag = el.tagName.toLowerCase();

  // data-testid is the most stable
  const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
  if (testId) return `[data-testid="${testId}"], [data-test="${testId}"]`;

  // aria-label
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) return `${tag}[aria-label="${CSS.escape(ariaLabel)}"]`;

  // Class-based with nth-child as fallback
  const parent = el.parentElement;
  if (parent) {
    const siblings = Array.from(parent.children).filter(
      (c) => c.tagName === el.tagName
    );
    if (siblings.length === 1) {
      const parentSel = getSelector(parent);
      return `${parentSel} > ${tag}`;
    }
    const idx = siblings.indexOf(el) + 1;
    const parentSel = getSelector(parent);
    return `${parentSel} > ${tag}:nth-of-type(${idx})`;
  }

  return tag;
}

/**
 * Truncate outerHTML for issue reporting.
 */
export function snippetHTML(el: Element, maxLen = 120): string {
  const html = el.outerHTML;
  if (html.length <= maxLen) return html;
  return html.slice(0, maxLen) + "...";
}
