"use strict";

(() => {
    const LOG = "[Hello Dodo Network Capture]";
    const CONTENT_SOURCE = "hello-dodo-content";
    const NETWORK_SOURCE = "hello-dodo-network";

    const CHATGPT_CONVERSATION_PATTERN =
        "/backend-api/f/conversation";

    const CHATGPT_PREPARE_PATTERN =
        "/backend-api/f/conversation/prepare";

    let armedToken = null;

    function log(...args) {
        console.log(LOG, ...args);
    }

    function sendCaptureResult(requestToken, payload) {
        window.postMessage(
            {
                source: NETWORK_SOURCE,
                type: "HELLO_DODO_CAPTURE_RESULT",
                requestToken,
                ...payload
            },
            window.location.origin
        );
    }

    function isConversationRequest(url) {
        return (
            typeof url === "string" &&
            url.includes(CHATGPT_CONVERSATION_PATTERN) &&
            !url.includes(CHATGPT_PREPARE_PATTERN)
        );
    }

    function getRequestUrl(request) {
        if (typeof request === "string") {
            return request;
        }

        if (request && typeof request.url === "string") {
            return request.url;
        }

        return "";
    }

    function getRequestMethod(request, options) {
        if (
            request &&
            typeof request !== "string" &&
            request.method
        ) {
            return String(request.method).toUpperCase();
        }

        if (options && options.method) {
            return String(options.method).toUpperCase();
        }

        return "GET";
    }

    function parseSseStream(rawText) {
        const events = [];
        const blocks = rawText.split(/\r?\n\r?\n/);

        for (const block of blocks) {
            const lines = block.split(/\r?\n/);
            const dataLines = [];

            for (const line of lines) {
                if (line.startsWith("data:")) {
                    dataLines.push(line.slice(5).trimStart());
                }
            }

            if (!dataLines.length) {
                continue;
            }

            const data = dataLines.join("\n");

            if (data === "[DONE]") {
                events.push({ type: "__DONE__" });
                continue;
            }

            try {
                events.push(JSON.parse(data));
            } catch {
                events.push({
                    type: "__RAW__",
                    data
                });
            }
        }

        return events;
    }

    function extractAssistantResponse(events) {
        if (!Array.isArray(events)) {
            return "";
        }

        const isFinalAssistantMessage = event => {
            const message = event?.v?.message;

            return (
                message &&
                typeof message === "object" &&
                message.author?.role === "assistant" &&
                message.channel === "final"
            );
        };

        const hasFinalAssistantMessage = events.some(
            isFinalAssistantMessage
        );

        if (!hasFinalAssistantMessage) {
            log("No final assistant message found.", {
                eventCount: events.length,
                eventTypes: events.slice(0, 8).map(event => ({
                    type: event?.type || "",
                    path: event?.p || "",
                    operation: event?.o || "",
                    valueType: typeof event?.v
                }))
            });

            return "";
        }

        let responseText = "";
        let responseStreamStarted = false;

        function applyOperation(path, operation, value) {
            if (
                path !== "/message/content/parts/0" ||
                typeof value !== "string"
            ) {
                return false;
            }

            if (operation === "replace" || operation === "set") {
                responseText = value;
                responseStreamStarted = true;
                return true;
            }

            if (operation === "append") {
                responseText += value;
                responseStreamStarted = true;
                return true;
            }

            return false;
        }

        for (const event of events) {
            if (!event || typeof event !== "object") {
                continue;
            }

            if (applyOperation(event.p, event.o, event.v)) {
                continue;
            }

            if (
                event.o === "patch" &&
                Array.isArray(event.v)
            ) {
                for (const operation of event.v) {
                    if (!operation || typeof operation !== "object") {
                        continue;
                    }

                    applyOperation(
                        operation.p,
                        operation.o,
                        operation.v
                    );
                }

                continue;
            }

            if (
                responseStreamStarted &&
                typeof event.v === "string"
            ) {
                responseText += event.v;
            }
        }

        return responseText
            .replace(/\uE200message_reaction\uE202/g, "")
            .trim();
    }

    async function readCaptureBranch(
        stream,
        requestToken,
        responseStatus
    ) {
        try {
            if (!responseStatus.ok) {
                sendCaptureResult(requestToken, {
                    ok: false,
                    error:
                        `ChatGPT conversation request failed: HTTP ${responseStatus.status}`
                });
                return;
            }

            if (!stream) {
                throw new Error(
                    "ChatGPT conversation response did not contain a readable body stream."
                );
            }

            const reader = stream.getReader();
            const decoder = new TextDecoder();
            let rawText = "";

            try {
                while (true) {
                    const { value, done } = await reader.read();

                    if (done) {
                        break;
                    }

                    if (value) {
                        rawText += decoder.decode(value, {
                            stream: true
                        });
                    }
                }

                rawText += decoder.decode();
            } finally {
                try {
                    reader.releaseLock();
                } catch {
                    // Stream already released.
                }
            }

            const events = parseSseStream(rawText);

            log("Stream captured.", {
                requestToken,
                streamLength: rawText.length,
                eventCount: events.length
            });

            const response = extractAssistantResponse(events);

            if (!response) {
                log("Stream ended but no assistant reply was extracted.", {
                    requestToken,
                    eventCount: events.length,
                    streamLength: rawText.length,
                    eventDetails: events.map((event, index) => ({
                        index,
                        keys:
                            event && typeof event === "object"
                                ? Object.keys(event)
                                : [],
                        path: event?.p || "",
                        operation: event?.o || "",
                        valueType: typeof event?.v,
                        valueLength:
                            typeof event?.v === "string"
                                ? event.v.length
                                : null,
                        valuePreview:
                            typeof event?.v === "string"
                                ? event.v.slice(0, 120)
                                : null,
                        role: event?.v?.message?.author?.role || "",
                        channel: event?.v?.message?.channel || ""
                    }))
                });

                sendCaptureResult(requestToken, {
                    ok: false,
                    error:
                        "ChatGPT stream finished, but no final assistant reply was extracted."
                });

                return;
            }

            log("Assistant reply captured.", {
                requestToken,
                characters: response.length,
                response
            });

            sendCaptureResult(requestToken, {
                ok: true,
                response
            });
        } catch (error) {
            log("Capture failed:", error);

            sendCaptureResult(requestToken, {
                ok: false,
                error: error?.message || String(error)
            });
        }
    }

    window.addEventListener("message", event => {
        if (
            event.source !== window ||
            event.origin !== window.location.origin
        ) {
            return;
        }

        const data = event.data;

        if (
            !data ||
            data.source !== CONTENT_SOURCE ||
            data.type !== "HELLO_DODO_ARM_CAPTURE" ||
            typeof data.requestToken !== "string"
        ) {
            return;
        }

        armedToken = data.requestToken;

        log("Armed for the next conversation request.", armedToken);
    });

    const originalFetch = window.fetch;

    if (typeof originalFetch === "function") {
        window.fetch = async function (...args) {
            const request = args[0];
            const options = args[1];

            const requestUrl = getRequestUrl(request);
            const requestMethod = getRequestMethod(request, options);

            const shouldCapture =
                Boolean(armedToken) &&
                requestMethod === "POST" &&
                isConversationRequest(requestUrl);

            if (!shouldCapture) {
                return originalFetch.apply(this, args);
            }

            const requestToken = armedToken;
            armedToken = null;

            log("Intercepted conversation request.", {
                requestToken,
                requestUrl
            });

            let response;

            try {
                response = await originalFetch.apply(this, args);
            } catch (error) {
                sendCaptureResult(requestToken, {
                    ok: false,
                    error:
                        error?.message ||
                        "ChatGPT network request failed."
                });

                throw error;
            }

            if (!response.body) {
                sendCaptureResult(requestToken, {
                    ok: false,
                    error:
                        "ChatGPT returned no readable response stream."
                });

                return response;
            }

            try {
                const [chatGPTStream, captureStream] =
                    response.body.tee();

                void readCaptureBranch(
                    captureStream,
                    requestToken,
                    {
                        ok: response.ok,
                        status: response.status
                    }
                );

                return new Response(chatGPTStream, {
                    status: response.status,
                    statusText: response.statusText,
                    headers: new Headers(response.headers)
                });
            } catch (error) {
                log("Could not split response stream:", error);

                sendCaptureResult(requestToken, {
                    ok: false,
                    error:
                        `Could not split ChatGPT response stream: ${error?.message || error}`
                });

                return response;
            }
        };

        log("Fetch interception installed.");
    } else {
        console.warn(LOG, "window.fetch was unavailable.");
    }

    log("Installed in page context.");
})();