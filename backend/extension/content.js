
"use strict";

(() => {
    const LOG = "[Hello Dodo]";

    const READY_MESSAGE = "HELLO_DODO_CHATGPT_READY";
    const PING_MESSAGE = "HELLO_DODO_CHATGPT_PING";
    const PROMPT_MESSAGE = "HELLO_DODO_PROMPT";

    const CONTENT_SOURCE = "hello-dodo-content";
    const NETWORK_SOURCE = "hello-dodo-network";

    const COMPOSER_TIMEOUT_MS = 30000;
    const SEND_BUTTON_TIMEOUT_MS = 10000;
    const CAPTURE_TIMEOUT_MS = 150000;

    let promptRunning = false;

    let captureResolver = null;
    let captureRejecter = null;
    let captureTimeout = null;
    let captureMessageHandler = null;

    function log(...args) {
        console.log(LOG, ...args);
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function normalizeText(value) {
        return (value || "")
            .replace(/\u200B/g, "")
            .replace(/\u00A0/g, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function isVisible(element) {
        if (!element) {
            return false;
        }

        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();

        return (
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            style.pointerEvents !== "none" &&
            rect.width > 0 &&
            rect.height > 0
        );
    }

    function isUsableComposer(element) {
        if (
            !element ||
            element === document.body ||
            element === document.documentElement ||
            !isVisible(element)
        ) {
            return false;
        }

        const tag = element.tagName?.toLowerCase();

        const supported =
            tag === "textarea" ||
            tag === "input" ||
            element.isContentEditable === true ||
            element.getAttribute("contenteditable") === "true";

        return (
            supported &&
            !element.disabled &&
            !element.readOnly
        );
    }

    function findComposer() {
        const activeElement = document.activeElement;

        // Follow the working Codex Lite approach:
        // prefer the actual focused editable element.
        if (isUsableComposer(activeElement)) {
            return activeElement;
        }

        const selectors = [
            '#prompt-textarea[contenteditable="true"]',
            '[data-testid="prompt-textarea"][contenteditable="true"]',
            '[role="textbox"][aria-label="Chat with ChatGPT"][contenteditable="true"]',
            'textarea[data-testid="prompt-textarea"]',
            "#prompt-textarea"
        ];

        for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
                if (isUsableComposer(element)) {
                    return element;
                }
            }
        }

        // Prefer visible contenteditable elements, as ChatGPT
        // commonly uses a rich-text editor for its composer.
        for (const element of document.querySelectorAll(
            '[contenteditable="true"]'
        )) {
            if (isUsableComposer(element)) {
                return element;
            }
        }

        for (const element of document.querySelectorAll("textarea")) {
            if (isUsableComposer(element)) {
                return element;
            }
        }

        for (const element of document.querySelectorAll("input")) {
            if (isUsableComposer(element)) {
                return element;
            }
        }

        return null;
    }

    async function waitForComposer() {
        const deadline = Date.now() + COMPOSER_TIMEOUT_MS;

        while (Date.now() < deadline) {
            const composer = findComposer();

            if (composer) {
                return composer;
            }

            await sleep(250);
        }

        throw new Error(
            "ChatGPT composer did not appear within 30 seconds. URL: " +
            location.href
        );
    }

    function enterPrompt(composer, prompt) {
        composer.focus();

        if (
            composer instanceof HTMLTextAreaElement ||
            composer instanceof HTMLInputElement
        ) {
            const prototype =
                composer instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;

            const setter = Object.getOwnPropertyDescriptor(
                prototype,
                "value"
            )?.set;

            if (!setter) {
                throw new Error(
                    "Could not update the ChatGPT text field."
                );
            }

            setter.call(composer, prompt);

            composer.dispatchEvent(
                new InputEvent("input", {
                    bubbles: true,
                    inputType: "insertText",
                    data: prompt
                })
            );

            composer.dispatchEvent(
                new Event("change", {
                    bubbles: true
                })
            );

            return;
        }

        if (composer.isContentEditable) {
            const selection = window.getSelection();
            const range = document.createRange();

            range.selectNodeContents(composer);
            selection.removeAllRanges();
            selection.addRange(range);

            const inserted = document.execCommand(
                "insertText",
                false,
                prompt
            );

            selection.removeAllRanges();

            // Some Chromium editor versions may report false
            // even when text was inserted. Verify the real content
            // before deciding whether insertion failed.
            const actualText = normalizeText(
                composer.innerText ||
                composer.textContent ||
                ""
            );

            const expectedPrefix = normalizeText(prompt).slice(0, 80);

            if (
                !actualText ||
                !expectedPrefix ||
                !actualText.startsWith(expectedPrefix)
            ) {
                log("Composer insertion failed.", {
                    execCommandResult: inserted,
                    expectedPrefix,
                    actualPrefix: actualText.slice(0, 160),
                    actualLength: actualText.length
                });

                throw new Error(
                    "Prompt insertion failed. ChatGPT composer text was not updated."
                );
            }

            return;
        }

        throw new Error("Unsupported ChatGPT composer element.");
    }

    function findSendButton() {
        const selectors = [
            'button[data-testid="send-button"]',
            'button[data-testid="composer-submit-button"]',
            "#composer-submit-button",
            'button[aria-label="Send prompt"]',
            'button[aria-label="Send message"]'
        ];

        for (const selector of selectors) {
            for (const button of document.querySelectorAll(selector)) {
                if (
                    isVisible(button) &&
                    !button.disabled &&
                    button.getAttribute("aria-disabled") !== "true"
                ) {
                    return button;
                }
            }
        }

        return null;
    }

    async function waitForSendButton() {
        const deadline = Date.now() + SEND_BUTTON_TIMEOUT_MS;

        while (Date.now() < deadline) {
            const button = findSendButton();

            if (button) {
                return button;
            }

            await sleep(200);
        }

        throw new Error(
            "ChatGPT Send button did not become enabled. Prompt remains in composer."
        );
    }

    function cleanupCaptureWaiter() {
        if (captureTimeout !== null) {
            clearTimeout(captureTimeout);
            captureTimeout = null;
        }

        if (captureMessageHandler) {
            window.removeEventListener(
                "message",
                captureMessageHandler
            );

            captureMessageHandler = null;
        }

        captureResolver = null;
        captureRejecter = null;
    }

    function cancelCaptureWaiter() {
        cleanupCaptureWaiter();
    }

    function waitForNetworkResponse(requestToken) {
        cancelCaptureWaiter();

        return new Promise((resolve, reject) => {
            captureResolver = resolve;
            captureRejecter = reject;

            captureMessageHandler = function onMessage(event) {
                if (
                    event.source !== window ||
                    event.origin !== location.origin
                ) {
                    return;
                }

                const message = event.data;

                if (
                    !message ||
                    message.source !== NETWORK_SOURCE ||
                    message.type !== "HELLO_DODO_CAPTURE_RESULT" ||
                    message.requestToken !== requestToken
                ) {
                    return;
                }

                const resolver = captureResolver;
                const rejecter = captureRejecter;

                cleanupCaptureWaiter();

                if (
                    message.ok === true &&
                    typeof message.response === "string" &&
                    message.response.trim()
                ) {
                    log(
                        "Network response captured.",
                        "Characters:",
                        message.response.length
                    );

                    resolver?.(message.response.trim());
                } else {
                    rejecter?.(
                        new Error(
                            message.error ||
                            "ChatGPT returned an empty or invalid network response."
                        )
                    );
                }
            };

            window.addEventListener(
                "message",
                captureMessageHandler
            );

            captureTimeout = setTimeout(() => {
                const rejecter = captureRejecter;

                cleanupCaptureWaiter();

                rejecter?.(
                    new Error(
                        "Timed out waiting for ChatGPT network response."
                    )
                );
            }, CAPTURE_TIMEOUT_MS);
        });
    }

    async function askChatGPT(prompt) {
        if (promptRunning) {
            throw new Error(
                "Another Hello Dodo prompt is already running."
            );
        }

        if (typeof prompt !== "string" || !prompt.trim()) {
            throw new Error("The prompt is empty.");
        }

        promptRunning = true;

        let responsePromise = null;

        try {
            const cleanPrompt = prompt.trim();

            log("Waiting for ChatGPT composer.");

            const composer = await waitForComposer();

            log("Actual composer found.", {
                tag: composer.tagName,
                contentEditable: composer.isContentEditable,
                id: composer.id || "",
                testId: composer.getAttribute("data-testid") || ""
            });

            const requestToken = crypto.randomUUID();

            // Install the response waiter before arming capture
            // and submitting the prompt.
            responsePromise = waitForNetworkResponse(requestToken);

            window.postMessage(
                {
                    source: CONTENT_SOURCE,
                    type: "HELLO_DODO_ARM_CAPTURE",
                    requestToken
                },
                location.origin
            );

            enterPrompt(composer, cleanPrompt);

            await sleep(500);

            const visibleText = normalizeText(
                composer.innerText ||
                composer.textContent ||
                composer.value ||
                ""
            );

            const expectedPrefix = normalizeText(cleanPrompt).slice(0, 80);

            if (
                !visibleText ||
                !expectedPrefix ||
                !visibleText.startsWith(expectedPrefix)
            ) {
                log("Composer text verification failed.", {
                    expectedPrefix,
                    actualPrefix: visibleText.slice(0, 160),
                    actualLength: visibleText.length
                });

                throw new Error(
                    "Prompt text could not be verified in the composer; refusing to submit."
                );
            }

            log("Prompt text verified.", {
                actualLength: visibleText.length
            });

            const sendButton = await waitForSendButton();

            // Verify the requested prompt is still present immediately
            // before submission.
            const currentComposer = findComposer();

            const finalText = normalizeText(
                currentComposer?.value ||
                currentComposer?.innerText ||
                currentComposer?.textContent ||
                ""
            );

            if (!currentComposer || !finalText.startsWith(expectedPrefix)) {
                log("Final composer verification failed.", {
                    originalComposerConnected: composer.isConnected,
                    currentComposerFound: Boolean(currentComposer),
                    currentComposerTag: currentComposer?.tagName || null,
                    currentComposerValue: currentComposer?.value || "",
                    currentComposerText: currentComposer?.innerText ||
                        currentComposer?.textContent || "",
                    expectedPrefix
                });

                throw new Error(
                    "Current composer no longer contains the prompt; refusing to click Send."
                );
            }

            if (
                !isVisible(sendButton) ||
                sendButton.disabled ||
                sendButton.getAttribute("aria-disabled") === "true"
            ) {
                throw new Error(
                    "ChatGPT Send button became unavailable before submission."
                );
            }

            sendButton.click();

            log("Actual ChatGPT Send button clicked.");

            const response = await responsePromise;

            if (
                typeof response !== "string" ||
                !response.trim()
            ) {
                throw new Error(
                    "ChatGPT returned an empty network response."
                );
            }

            log("Network response received.", {
                characters: response.length
            });

            return {
                ok: true,
                response: response.trim()
            };
        } catch (error) {
            cancelCaptureWaiter();

            throw new Error(
                error?.message || String(error)
            );
        } finally {
            promptRunning = false;
        }
    }

    chrome.runtime.onMessage.addListener(
        (message, sender, sendResponse) => {
            if (!message) {
                return;
            }

            if (message.type === PING_MESSAGE) {
                sendResponse({ ok: true });
                return;
            }

            if (message.type === READY_MESSAGE) {
                sendResponse({ ok: true });
                return;
            }

            if (message.type !== PROMPT_MESSAGE) {
                return;
            }

            log("Prompt message received.");

            askChatGPT(message.prompt)
                .then(result => {
                    sendResponse(result);
                })
                .catch(error => {
                    console.error(
                        LOG,
                        "ChatGPT request failed:",
                        error
                    );

                    sendResponse({
                        ok: false,
                        error: error?.message || String(error)
                    });
                });

            // Keep the extension message channel open for the
            // asynchronous response.
            return true;
        }
    );

    chrome.runtime.sendMessage(
        { type: READY_MESSAGE },
        response => {
            if (chrome.runtime.lastError) {
                log(
                    "Ready notification failed:",
                    chrome.runtime.lastError.message
                );

                return;
            }

            log("Content script ready.", response);
        }
    );
})();