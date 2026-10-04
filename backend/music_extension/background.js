 "use strict";

const BACKEND_URL = "http://127.0.0.1:8000";
const POLL_INTERVAL_MS = 1500;
const REQUEST_TIMEOUT_MS = 10000;
const TAB_LOAD_TIMEOUT_MS = 20000;

let polling = false;
let lastPollAt = 0;
let pollCount = 0;

function log(...args) {
    console.info("[Hello Dodo Music]", ...args);
}

function warn(...args) {
    console.warn("[Hello Dodo Music]", ...args);
}

function errorLog(...args) {
    console.error("[Hello Dodo Music]", ...args);
}

function errorDetails(error) {
    return {
        name: error?.name || "Error",
        message: error?.message || String(error),
        stack: error?.stack || null
    };
}

async function backendRequest(path, options = {}) {
    const startedAt = Date.now();
    const method = options.method || "GET";
    const requestUrl = `${BACKEND_URL}${path}`;
    const controller = new AbortController();

    const timeout = setTimeout(
        () => controller.abort(),
        REQUEST_TIMEOUT_MS
    );

    log("BACKEND_REQUEST_START", {
        method,
        path
    });

    try {
        const response = await fetch(requestUrl, {
            ...options,
            signal: controller.signal,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {})
            }
        });

        const rawBody = await response.text();
        let body = {};

        if (rawBody) {
            try {
                body = JSON.parse(rawBody);
            } catch {
                body = { raw_response: rawBody };
            }
        }

        log("BACKEND_REQUEST_END", {
            method,
            path,
            status: response.status,
            ok: response.ok,
            elapsedMs: Date.now() - startedAt
        });

        if (!response.ok) {
            throw new Error(
                body.detail ||
                `Backend returned HTTP ${response.status}`
            );
        }

        return body;
    } catch (error) {
        errorLog("BACKEND_REQUEST_FAILED", {
            method,
            path,
            elapsedMs: Date.now() - startedAt,
            ...errorDetails(error)
        });

        throw error;
    } finally {
        clearTimeout(timeout);
    }
}

async function findMusicTab() {
    const tabs = await chrome.tabs.query({
        url: "https://music.youtube.com/*"
    });

    log("MUSIC_TAB_SEARCH", {
        found: tabs.length > 0,
        count: tabs.length,
        tabs: tabs.map((tab) => ({
            id: tab.id,
            url: tab.url,
            status: tab.status
        }))
    });

    return tabs.length ? tabs[0] : null;
}

function waitForTabComplete(tabId, timeoutMs = TAB_LOAD_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
        let finished = false;

        log("TAB_LOAD_WAIT_START", {
            tabId,
            timeoutMs
        });

        const cleanup = () => {
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timeout);
        };

        const finish = (err) => {
            if (finished) return;

            finished = true;
            cleanup();

            if (err) {
                errorLog("TAB_LOAD_WAIT_FAILED", {
                    tabId,
                    ...errorDetails(err)
                });
                reject(err);
            } else {
                log("TAB_LOAD_WAIT_COMPLETE", { tabId });
                resolve();
            }
        };

        const onUpdated = (updatedTabId, changeInfo, tab) => {
            if (updatedTabId !== tabId) return;

            if (changeInfo.status) {
                log("TAB_STATUS_CHANGED", {
                    tabId,
                    status: changeInfo.status,
                    url: tab?.url
                });
            }

            if (changeInfo.status === "complete") {
                finish();
            }
        };

        const timeout = setTimeout(() => {
            finish(
                new Error(
                    `Timed out waiting for tab ${tabId} to load after ${timeoutMs}ms.`
                )
            );
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
        log("MUSIC_TAB_REUSED", {
            tabId: tab.id,
            url: tab.url,
            status: tab.status
        });

        return tab;
    }

    log("MUSIC_TAB_CREATE_START");

    tab = await chrome.tabs.create({
        url: "https://music.youtube.com/",
        active: true
    });

    log("MUSIC_TAB_CREATED", {
        tabId: tab.id,
        url: tab.url
    });

    await waitForTabComplete(tab.id);

    return tab;
}

async function sendMusicAction(tabId, action, payload = {}) {
    const startedAt = Date.now();

    log("CONTENT_MESSAGE_START", {
        tabId,
        action,
        payload
    });

    try {
        const response = await chrome.tabs.sendMessage(tabId, {
            type: "EXECUTE_MUSIC_COMMAND",
            action,
            payload
        });

        log("CONTENT_MESSAGE_RESPONSE", {
            tabId,
            action,
            elapsedMs: Date.now() - startedAt,
            response
        });

        if (!response || typeof response !== "object") {
            throw new Error(
                "Content script returned an empty or invalid response."
            );
        }

        return response;
    } catch (error) {
        errorLog("CONTENT_MESSAGE_FAILED", {
            tabId,
            action,
            elapsedMs: Date.now() - startedAt,
            ...errorDetails(error)
        });

        throw new Error(
            `Could not execute ${action} in YouTube Music tab ${tabId}: ` +
            `${error?.message || String(error)}`
        );
    }
}

async function executeMusicCommand(command) {
    const action = String(command.action || "").toUpperCase();
    const payload = command.payload || {};
    const startedAt = Date.now();

    log("EXECUTION_START", {
        commandId: command.command_id,
        action,
        payload
    });

    try {
        if (action === "PLAY") {
            const query = String(payload.query || "").trim();

            if (!query) {
                throw new Error("PLAY requires a non-empty query.");
            }

            const searchUrl =
                "https://music.youtube.com/search?q=" +
                encodeURIComponent(query);

            log("PLAY_SEARCH_START", {
                query,
                searchUrl
            });

            let tab = await findMusicTab();

            if (!tab) {
                log("PLAY_SEARCH_CREATE_TAB", { searchUrl });

                tab = await chrome.tabs.create({
                    url: searchUrl,
                    active: true
                });

                log("PLAY_SEARCH_TAB_CREATED", {
                    tabId: tab.id,
                    url: tab.url
                });

                await waitForTabComplete(tab.id);
            } else {
                log("PLAY_SEARCH_REUSE_TAB", {
                    tabId: tab.id,
                    currentUrl: tab.url
                });

                await navigateTabAndWait(tab.id, searchUrl);
            }

            log("PLAY_SEARCH_PAGE_READY", {
                tabId: tab.id,
                searchUrl
            });

            const result = await sendMusicAction(
                tab.id,
                "PLAY_FIRST_RESULT",
                { query }
            );

            log("PLAY_EXECUTION_RESULT", {
                commandId: command.command_id,
                elapsedMs: Date.now() - startedAt,
                result
            });

            return result;
        }

        if (action === "SET_VOLUME") {
            const volume = Number(payload.volume);

            if (
                !Number.isInteger(volume) ||
                volume < 0 ||
                volume > 100
            ) {
                throw new Error(
                    "Volume must be an integer from 0 to 100."
                );
            }
        }

        const tab = await getOrCreateMusicTab();

        const result = await sendMusicAction(
            tab.id,
            action,
            payload
        );

        log("EXECUTION_RESULT", {
            commandId: command.command_id,
            action,
            elapsedMs: Date.now() - startedAt,
            result
        });

        return result;
    } catch (error) {
        errorLog("EXECUTION_FAILED", {
            commandId: command.command_id,
            action,
            elapsedMs: Date.now() - startedAt,
            ...errorDetails(error)
        });

        throw error;
    }
}

async function reportCommandResult(commandId, result) {
    log("RESULT_REPORT_START", {
        commandId,
        result
    });

    try {
        const response = await backendRequest("/tools/result", {
            method: "POST",
            body: JSON.stringify({
                command_id: commandId,
                result
            })
        });

        log("RESULT_REPORT_SUCCESS", {
            commandId,
            response
        });

        return response;
    } catch (error) {
        errorLog("RESULT_REPORT_FAILED", {
            commandId,
            result,
            ...errorDetails(error)
        });

        throw error;
    }
}

async function pollMusicCommands() {
    if (polling) {
        return;
    }

    polling = true;
    pollCount += 1;

    try {
        const response = await backendRequest(
            "/tools/next?tool_name=play_music"
        );

        const command = response.command;

        if (!command || !command.command_id) {
            return;
        }

        log("COMMAND_RECEIVED", {
            pollCount,
            commandId: command.command_id,
            toolName: command.tool_name,
            action: command.action,
            payload: command.payload
        });

        // Never execute commands intended for other tools.
        if (command.tool_name !== "play_music") {
            warn("NON_MUSIC_COMMAND_RECEIVED", {
                commandId: command.command_id,
                toolName: command.tool_name
            });

            return;
        }

        let result;

        try {
            const execution = await executeMusicCommand(command);

            log("EXECUTION_RESPONSE_RAW", {
                commandId: command.command_id,
                execution
            });

            const success = Boolean(
                execution && execution.success === true
            );

            result = {
                success,
                message: execution?.message || (
                    success
                        ? "Music command completed."
                        : "Music command failed: execution returned no success confirmation."
                ),
                data: execution?.data || {}
            };

            if (execution?.error_code) {
                result.error_code = execution.error_code;
            }

            if (!success) {
                warn("COMMAND_RETURNED_FAILURE", {
                    commandId: command.command_id,
                    action: command.action,
                    execution,
                    reportedResult: result
                });
            }
        } catch (error) {
            errorLog("COMMAND_EXECUTION_EXCEPTION", {
                commandId: command.command_id,
                action: command.action,
                ...errorDetails(error)
            });

            result = {
                success: false,
                message: error?.message || "Music command failed.",
                error_code: "MUSIC_EXTENSION_FAILED",
                data: {
                    error_name: error?.name || "Error"
                }
            };
        }

        log("COMMAND_FINAL_RESULT", {
            commandId: command.command_id,
            action: command.action,
            result
        });

        try {
            await reportCommandResult(command.command_id, result);
        } catch (error) {
            errorLog("COMMAND_RESULT_COULD_NOT_BE_REPORTED", {
                commandId: command.command_id,
                result,
                ...errorDetails(error)
            });
        }
    } catch (error) {
        const now = Date.now();

        if (now - lastPollAt > 10000) {
            errorLog("POLL_FAILED", {
                pollCount,
                ...errorDetails(error)
            });

            lastPollAt = now;
        }
    } finally {
        polling = false;
    }
}

function navigateTabAndWait(tabId, url) {
    return new Promise((resolve, reject) => {
        let finished = false;

        log("TAB_NAVIGATION_START", {
            tabId,
            url
        });

        const cleanup = () => {
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timeout);
        };

        const finish = (err) => {
            if (finished) return;

            finished = true;
            cleanup();

            if (err) {
                errorLog("TAB_NAVIGATION_FAILED", {
                    tabId,
                    url,
                    ...errorDetails(err)
                });
                reject(err);
            } else {
                log("TAB_NAVIGATION_COMPLETE", {
                    tabId,
                    url
                });
                resolve();
            }
        };

        const onUpdated = (updatedTabId, changeInfo, tab) => {
            if (updatedTabId !== tabId) return;

            if (changeInfo.status) {
                log("NAVIGATION_TAB_STATUS", {
                    tabId,
                    status: changeInfo.status,
                    url: tab?.url
                });
            }

            if (changeInfo.status === "complete") {
                finish();
            }
        };

        const timeout = setTimeout(() => {
            finish(
                new Error(
                    `Timed out waiting for YouTube Music search tab ${tabId}.`
                )
            );
        }, TAB_LOAD_TIMEOUT_MS);

        chrome.tabs.onUpdated.addListener(onUpdated);

        chrome.tabs.update(tabId, {
            url,
            active: true
        }).then((tab) => {
            log("TAB_UPDATE_ACCEPTED", {
                tabId,
                url: tab?.url
            });

            if (tab?.status === "complete") {
                finish();
            }
        }).catch(finish);
    });
}

chrome.runtime.onInstalled.addListener((details) => {
    log("EXTENSION_INSTALLED_OR_UPDATED", {
        reason: details?.reason,
        previousVersion: details?.previousVersion
    });

    pollMusicCommands();
});

chrome.runtime.onStartup.addListener(() => {
    log("BROWSER_STARTUP");
    pollMusicCommands();
});

chrome.tabs.onRemoved.addListener((tabId) => {
    log("TAB_REMOVED", { tabId });
});

chrome.runtime.onSuspend.addListener(() => {
    warn("SERVICE_WORKER_SUSPENDING");
});

log("SERVICE_WORKER_STARTED", {
    backendUrl: BACKEND_URL,
    pollIntervalMs: POLL_INTERVAL_MS
});

setInterval(pollMusicCommands, POLL_INTERVAL_MS);

pollMusicCommands();