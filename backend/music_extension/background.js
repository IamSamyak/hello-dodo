
"use strict";

const BACKEND_URL = "http://127.0.0.1:8000";
const POLL_INTERVAL_MS = 1500;
const REQUEST_TIMEOUT_MS = 10000;

let polling = false;
let lastPollAt = 0;

async function backendRequest(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(
        () => controller.abort(),
        REQUEST_TIMEOUT_MS
    );

    try {
        const response = await fetch(`${BACKEND_URL}${path}`, {
            ...options,
            signal: controller.signal,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {})
            }
        });

        const body = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(
                body.detail || `Backend returned HTTP ${response.status}`
            );
        }

        return body;
    } finally {
        clearTimeout(timeout);
    }
}

async function findMusicTab() {
    const tabs = await chrome.tabs.query({
        url: "https://music.youtube.com/*"
    });

    return tabs.length ? tabs[0] : null;
}

function waitForTabComplete(tabId, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
        let finished = false;

        const cleanup = () => {
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timeout);
        };

        const finish = (error) => {
            if (finished) return;
            finished = true;
            cleanup();

            if (error) {
                reject(error);
            } else {
                resolve();
            }
        };

        const onUpdated = (updatedTabId, changeInfo) => {
            if (
                updatedTabId === tabId &&
                changeInfo.status === "complete"
            ) {
                finish();
            }
        };

        const timeout = setTimeout(() => {
            finish(new Error("Timed out waiting for YouTube Music to load."));
        }, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);

        chrome.tabs.get(tabId)
            .then((tab) => {
                if (tab.status === "complete") {
                    finish();
                }
            })
            .catch(finish);
    });
}

async function getOrCreateMusicTab() {
    let tab = await findMusicTab();

    if (tab) {
        return tab;
    }

    tab = await chrome.tabs.create({
        url: "https://music.youtube.com/",
        active: true
    });

    await waitForTabComplete(tab.id);
    return tab;
}

async function sendMusicAction(tabId, action, payload = {}) {
    try {
        return await chrome.tabs.sendMessage(tabId, {
            type: "EXECUTE_MUSIC_COMMAND",
            action,
            payload
        });
    } catch {
        throw new Error(
            "Could not reach YouTube Music. Make sure its tab is loaded."
        );
    }
}

async function executeMusicCommand(command) {
    const action = String(command.action || "").toUpperCase();
    const payload = command.payload || {};

    if (action === "PLAY") {
        const query = String(payload.query || "").trim();

        if (!query) {
            throw new Error("PLAY requires a non-empty query.");
        }

        let tab = await findMusicTab();

        const searchUrl =
            "https://music.youtube.com/search?q=" +
            encodeURIComponent(query);


        if (!tab) {
            tab = await chrome.tabs.create({
                url: searchUrl,
                active: true
            });

            await waitForTabComplete(tab.id);
        } else {
            await navigateTabAndWait(tab.id, searchUrl);
        }

        return await sendMusicAction(
            tab.id,
            "PLAY_FIRST_RESULT",
            { query }
        );
    }

    if (action === "SET_VOLUME") {
        const volume = Number(payload.volume);

        if (
            !Number.isInteger(volume) ||
            volume < 0 ||
            volume > 100
        ) {
            throw new Error("Volume must be an integer from 0 to 100.");
        }
    }

    const tab = await getOrCreateMusicTab();

    return await sendMusicAction(
        tab.id,
        action,
        payload
    );
}

async function reportCommandResult(commandId, result) {
    await backendRequest("/music/result", {
        method: "POST",
        body: JSON.stringify({
            command_id: commandId,
            result
        })
    });
}

async function pollMusicCommands() {
    if (polling) return;

    polling = true;

    try {
        const response = await backendRequest("/music/next");
        const command = response.command;

        if (!command || !command.command_id) {
            return;
        }

        console.info(
            "[Hello Dodo Music] Command received:",
            command.action
        );

        let result;

        try {
            const execution = await executeMusicCommand(command);

            result = {
                success: Boolean(execution && execution.success),
                message: execution?.message ||
                    (execution?.success
                        ? "Music command completed."
                        : "Music command failed."),
                data: execution?.data || {}
            };
        } catch (error) {
            result = {
                success: false,
                message: error?.message || "Music command failed.",
                error_code: "MUSIC_EXTENSION_FAILED"
            };
        }

        try {
            await reportCommandResult(command.command_id, result);
        } catch (error) {
            console.error(
                "[Hello Dodo Music] Could not report result:",
                error
            );
        }
    } catch (error) {
        const now = Date.now();

        if (now - lastPollAt > 10000) {
            console.warn(
                "[Hello Dodo Music] Backend unavailable:",
                error.message
            );
            lastPollAt = now;
        }
    } finally {
        polling = false;
    }
}


function navigateTabAndWait(tabId, url) {
    return new Promise((resolve, reject) => {
        let finished = false;

        const cleanup = () => {
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timeout);
        };

        const finish = (error) => {
            if (finished) return;
            finished = true;
            cleanup();

            if (error) {
                reject(error);
            } else {
                resolve();
            }
        };

        const onUpdated = (updatedTabId, changeInfo) => {
            if (
                updatedTabId === tabId &&
                changeInfo.status === "complete"
            ) {
                finish();
            }
        };

        const timeout = setTimeout(() => {
            finish(new Error("Timed out waiting for YouTube Music search."));
        }, 20000);

        chrome.tabs.onUpdated.addListener(onUpdated);

        chrome.tabs.update(tabId, {
            url,
            active: true
        }).catch(finish);
    });
}

chrome.runtime.onInstalled.addListener(() => {
    console.info("[Hello Dodo Music] Extension installed.");
    pollMusicCommands();
});

chrome.runtime.onStartup.addListener(() => {
    pollMusicCommands();
});

setInterval(pollMusicCommands, POLL_INTERVAL_MS);
pollMusicCommands();