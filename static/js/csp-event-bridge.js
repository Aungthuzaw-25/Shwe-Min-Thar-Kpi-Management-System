(function () {
    "use strict";

    function splitArguments(value) {
        const result = [];
        let current = "";
        let quote = null;
        let depth = 0;
        for (const character of value) {
            if (quote) {
                current += character;
                if (character === quote && current[current.length - 2] !== "\\") {
                    quote = null;
                }
            } else if (character === "'" || character === '"') {
                quote = character;
                current += character;
            } else if (character === "(" || character === "[" || character === "{") {
                depth += 1;
                current += character;
            } else if (character === ")" || character === "]" || character === "}") {
                depth -= 1;
                current += character;
            } else if (character === "," && depth === 0) {
                result.push(current.trim());
                current = "";
            } else {
                current += character;
            }
        }
        if (current.trim()) result.push(current.trim());
        return result;
    }

    function parseQuotedArgument(value) {
        const quote = value[0];
        if ((quote !== "'" && quote !== '"') || value[value.length - 1] !== quote) return null;

        let result = "";
        for (let index = 1; index < value.length - 1; index += 1) {
            const character = value[index];
            if (character === quote) return null;
            if (character !== "\\") {
                result += character;
                continue;
            }
            if (index + 1 >= value.length - 1) return null;
            const escaped = value[++index];
            result += escaped === "'" || escaped === '"' || escaped === "\\"
                ? escaped
                : `\\${escaped}`;
        }
        return result;
    }

    function resolveArgument(value, element, event) {
        if (value[0] === "'" || value[0] === '"') {
            const parsed = parseQuotedArgument(value);
            return parsed === null ? undefined : parsed;
        }
        if (!isIdentifier(value)) return undefined;
        if (value === "event") return event;
        if (value === "this") return element;
        return undefined;
    }

    function isIdentifier(value) {
        if (!value) return false;
        for (let index = 0; index < value.length; index += 1) {
            const code = value.codePointAt(index);
            const isLetter = (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
            if (index === 0) {
                if (!isLetter && code !== 95 && code !== 36) return false;
            } else if (!isLetter && (code < 48 || code > 57) && code !== 95 && code !== 36) {
                return false;
            }
        }
        return true;
    }

    function invokeCall(expression, element, event) {
        const call = expression.trim();
        const openingParen = call.indexOf("(");
        if (openingParen < 1 || call[call.length - 1] !== ")") return false;

        const functionName = call.slice(0, openingParen);
        if (!isIdentifier(functionName) || typeof window[functionName] !== "function") return false;

        const argumentValues = splitArguments(call.slice(openingParen + 1, -1));
        const args = argumentValues.map((value) => resolveArgument(value, element, event));
        if (args.some((value, index) => argumentValues[index] && value === undefined)) return false;
        window[functionName](...args);
        return true;
    }

    function dispatchHandler(element, event, attribute) {
        const source = element.getAttribute(`data-csp-${attribute}`);
        if (!source) return;
        event.preventDefault();
        for (const expression of source.split(";")) {
            const trimmed = expression.trim();
            if (!trimmed) continue;
            if (trimmed === "this.parentElement.remove()") {
                element.parentElement?.remove();
                continue;
            }
            if (trimmed.startsWith("if(") || trimmed.startsWith("if (")) {
                const closingBrace = trimmed.indexOf("}");
                const call = closingBrace === -1 ? "" : trimmed.slice(closingBrace + 1).trim();
                if (call && (event.key === "Enter" || event.key === " ")) {
                    invokeCall(call, element, event);
                }
                continue;
            }
            invokeCall(trimmed, element, event);
        }
    }

    function normalizeElement(element) {
        ["onclick", "onsubmit", "onchange", "onkeydown"].forEach((attribute) => {
            const source = element.getAttribute(attribute);
            if (source) {
                element.setAttribute(`data-csp-${attribute}`, source);
                element.removeAttribute(attribute);
            }
        });
    }

    function normalizeTree(root) {
        if (root.nodeType !== Node.ELEMENT_NODE) return;
        normalizeElement(root);
        root.querySelectorAll("[onclick], [onsubmit], [onchange], [onkeydown]").forEach(normalizeElement);
    }

    normalizeTree(document.documentElement);
    new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach(normalizeTree);
        });
    }).observe(document.documentElement, { childList: true, subtree: true });

    document.addEventListener("click", (event) => {
        const element = event.target.closest?.("[data-csp-onclick]");
        if (element) dispatchHandler(element, event, "onclick");
    }, true);
    document.addEventListener("submit", (event) => {
        const element = event.target.closest?.("[data-csp-onsubmit]");
        if (element) dispatchHandler(element, event, "onsubmit");
    }, true);
    document.addEventListener("change", (event) => {
        const element = event.target.closest?.("[data-csp-onchange]");
        if (element) dispatchHandler(element, event, "onchange");
    }, true);
    document.addEventListener("keydown", (event) => {
        const element = event.target.closest?.("[data-csp-onkeydown]");
        if (element) dispatchHandler(element, event, "onkeydown");
    }, true);
}());
