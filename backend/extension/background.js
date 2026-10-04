
"use strict";

const LOG = "[Hello Dodo]";

const BRIDGE_URL = "http://127.0.0.1:8766";
const CHATGPT_URL = "https://chatgpt.com/";
const CHATGPT_URL_PATTERN = "https://chatgpt.com/*";

const READY_MESSAGE = "HELLO_DODO_CHATGPT_READY";
const PING_MESSAGE = "HELLO_DODO_CHATGPT_PING";
const PROMPT_MESSAGE = "HELLO_DODO_PROMPT";

const READY_TIMEOUT_MS = 30000;
const RETRY_DELAY_MS = 500;
const BRIDGE_POLL_INTERVAL_MS = 1000;

let chatGPTTabId = null;
let pollInProgress = false;
let ensurePromise = null;

let activeRequestId = null;

function log(...args) {
    console.log(LOG, ...args);
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

log("Dedicated bridge worker loaded.");
log("Bridge URL:", BRIDGE_URL);

chrome.runtime.onMessage.addListener(
    (message, sender, sendResponse) => {
        if (message?.type === PING_MESSAGE) {
            sendResponse({ ok: true });
            return;
        }

        if (message?.type === READY_MESSAGE) {
            const tabId = sender?.tab?.id;
            const tabUrl = sender?.tab?.url || "";

            if (
                typeof tabId === "number" &&
                tabUrl.startsWith(CHATGPT_URL)
            ) {
                chatGPTTabId = tabId;

                log("Dedicated ChatGPT tab ready:", tabId);

                sendResponse({ ok: true });
                return;
            }

            sendResponse({
                ok: false,
                error: "Not a valid Hello Dodo ChatGPT tab."
            });
        }
    }
);

chrome.tabs.onRemoved.addListener(tabId => {
    if (tabId === chatGPTTabId) {
        log("Managed ChatGPT tab closed:", tabId);
        chatGPTTabId = null;
    }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (
        tabId === chatGPTTabId &&
        changeInfo.status === "loading"
    ) {
        log("Managed ChatGPT tab loading:", tabId);
    }
});

async function isReady(tabId) {
    try {
        const tab = await chrome.tabs.get(tabId);

        if (!tab.url?.startsWith(CHATGPT_URL)) {
            return false;
        }

        const response = await chrome.tabs.sendMessage(tabId, {
            type: PING_MESSAGE
        });

        return response?.ok === true;
    } catch {
        return false;
    }
}

async function waitUntilReady(tabId) {
    const deadline = Date.now() + READY_TIMEOUT_MS;

    while (Date.now() < deadline) {
        if (await isReady(tabId)) {
            return true;
        }

        await sleep(RETRY_DELAY_MS);
    }

    return false;
}

async function ensureChatGPTTab() {
    if (ensurePromise) {
        return ensurePromise;
    }

    ensurePromise = ensureChatGPTTabInternal();

    try {
        return await ensurePromise;
    } finally {
        ensurePromise = null;
    }
}

async function ensureChatGPTTabInternal() {
    // Reuse the managed tab whenever it remains available.
    if (typeof chatGPTTabId === "number") {
        try {
            const managedTab = await chrome.tabs.get(chatGPTTabId);

            if (managedTab.url?.startsWith(CHATGPT_URL)) {
                if (await isReady(managedTab.id)) {
                    return managedTab;
                }

                // The page may be reloading. Wait for this same tab
                // instead of opening another tab immediately.
                if (await waitUntilReady(managedTab.id)) {
                    return await chrome.tabs.get(managedTab.id);
                }
            }
        } catch {
            log("Managed ChatGPT tab is unavailable.");
        }

        chatGPTTabId = null;
    }

    // Reuse an existing ChatGPT tab in this Brave profile.
    const tabs = await chrome.tabs.query({
        url: CHATGPT_URL_PATTERN
    });

    log("Existing ChatGPT tabs found:", tabs.length);

    const existingTab =
        tabs.find(tab => tab.active) ||
        tabs.find(tab => typeof tab.id === "number");

    if (existingTab && typeof existingTab.id === "number") {
        chatGPTTabId = existingTab.id;

        log("Checking existing ChatGPT tab:", chatGPTTabId);

        if (await waitUntilReady(existingTab.id)) {
            return await chrome.tabs.get(existingTab.id);
        }

        // Keep the same managed tab reference so the next attempt
        // does not create another tab during page initialization.
        log(
            "Existing ChatGPT tab is not ready yet:",
            existingTab.id
        );

        throw new Error(
            "Existing ChatGPT tab did not become ready. " +
            "Wait for ChatGPT to finish loading and retry."
        );
    }

    // Open a tab only if no ChatGPT tab exists in this profile.
    log("No existing ChatGPT tab found. Opening one managed tab.");

    const newTab = await chrome.tabs.create({
        url: CHATGPT_URL,
        active: false
    });

    if (typeof newTab.id !== "number") {
        throw new Error("Could not open a ChatGPT tab.");
    }

    chatGPTTabId = newTab.id;

    log("Managed ChatGPT tab created:", newTab.id);

    if (!await waitUntilReady(newTab.id)) {
        // Retain the tab ID. A retry should wait for this tab,
        // not create a new tab for every bridge poll.
        throw new Error(
            "ChatGPT tab did not become ready within 30 seconds. " +
            "Check login, extension permissions, and reload ChatGPT."
        );
    }

    return await chrome.tabs.get(newTab.id);
}

async function sendResult(requestId, result) {
    const response = await fetch(
        `${BRIDGE_URL}/result?id=${encodeURIComponent(requestId)}`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ result })
        }
    );

    if (!response.ok) {
        const responseText = await response.text();

        throw new Error(
            `Hello Dodo bridge rejected result: HTTP ${response.status} ` +
            responseText
        );
    }

    log("Result delivered to bridge:", requestId);
}

async function reportFailure(requestId, error) {
    try {
        await sendResult(requestId, {
            ok: false,
            error: error?.message || String(error)
        });
    } catch (sendError) {
        console.error(
            LOG,
            "Could not report failure to bridge:",
            sendError
        );
    }
}

async function pollBridge() {
    // Do not consume another request while one is still being handled.
    if (pollInProgress) {
        return;
    }

    pollInProgress = true;

    let request = null;

    try {
        const response = await fetch(`${BRIDGE_URL}/next`);

        if (!response.ok) {
            throw new Error(
                `/next returned HTTP ${response.status}`
            );
        }

        const data = await response.json();
        request = data.request;

        if (!request) {
            return;
        }

        // Do not process the same active request more than once.
        if (activeRequestId === request.id) {
            log("Request already active; ignoring duplicate:", request.id);
            return;
        }

        activeRequestId = request.id;

        log("Dedicated request received:", request.id);

        try {
            const tab = await ensureChatGPTTab();

            log("Using dedicated ChatGPT tab:", tab.id);

            const result = await chrome.tabs.sendMessage(
                tab.id,
                {
                    type: PROMPT_MESSAGE,
                    prompt: request.prompt
                }
            );

            if (!result || typeof result.ok !== "boolean") {
                throw new Error(
                    "ChatGPT content script returned an invalid response."
                );
            }

            if (!result.ok) {
                throw new Error(
                    result.error || "ChatGPT request failed."
                );
            }

            if (
                typeof result.response !== "string" ||
                !result.response.trim()
            ) {
                throw new Error(
                    "ChatGPT returned an empty response."
                );
            }

            await sendResult(request.id, result);
        } catch (error) {
            console.error(LOG, "Request failed:", error);

            await reportFailure(request.id, error);
        } finally {
            activeRequestId = null;
        }
    } catch (error) {
        console.error(LOG, "Bridge polling error:", error);
    } finally {
        pollInProgress = false;
    }
}

setInterval(pollBridge, BRIDGE_POLL_INTERVAL_MS);
pollBridge();