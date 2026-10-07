(function () {
    "use strict";

    const argumentPattern = /^(?:'((?:\\.|[^'])*)'|"((?:\\.|[^"])*)"|([A-Za-z_$][\w$]*))$/;

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

    function resolveArgument(value, element, event) {
        const match = value.match(argumentPattern);
        if (!match) return undefined;
        if (match[1] !== undefined || match[2] !== undefined) {
            return (match[1] ?? match[2]).replace(/\\(['"\\])/g, "$1");
        }
        if (match[3] === "event") return event;
        if (match[3] === "this") return element;
        return undefined;
    }

    function invokeCall(expression, element, event) {
        const match = expression.trim().match(/^([A-Za-z_$][\w$]*)\((.*)\)$/);
        if (!match || typeof window[match[1]] !== "function") return false;
        const args = splitArguments(match[2]).map((value) => resolveArgument(value, element, event));
        if (args.some((value, index) => splitArguments(match[2])[index] && value === undefined)) return false;
        window[match[1]](...args);
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
                const call = trimmed.match(/}\s*(.+)$/);
                if (call && (event.key === "Enter" || event.key === " ")) {
                    invokeCall(call[1], element, event);
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
