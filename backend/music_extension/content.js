
"use strict";

const sleep = (ms) =>
    new Promise((resolve) => setTimeout(resolve, ms));

async function waitForElement(selectors, timeoutMs = 12000) {
    const selectorList = Array.isArray(selectors)
        ? selectors
        : [selectors];

    const startedAt = Date.now();

    while (Date.now() - startedAt < timeoutMs) {
        for (const selector of selectorList) {
            const element = document.querySelector(selector);

            if (element) {
                return element;
            }
        }

        await sleep(300);
    }

    throw new Error(
        `YouTube Music control not found: ${selectorList.join(", ")}`
    );
}

function findButtonByLabel(patterns) {
    const buttons = Array.from(
        document.querySelectorAll(
            "button, tp-yt-paper-icon-button, yt-icon-button"
        )
    );

    return buttons.find((button) => {
        const label = [
            button.getAttribute("aria-label"),
            button.getAttribute("title"),
            button.getAttribute("aria-label") ||
                button.querySelector("[aria-label]")?.getAttribute("aria-label")
        ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

        return patterns.some((pattern) => pattern.test(label));
    });
}

async function playFirstResult() {
    const firstResult = await waitForElement([
        "ytmusic-responsive-list-item-renderer",
        "ytmusic-video-renderer",
        "ytmusic-two-row-item-renderer"
    ]);

    const playButton =
        firstResult.querySelector(
            "ytmusic-play-button-renderer button"
        ) ||
        firstResult.querySelector("#play-button button") ||
        firstResult.querySelector("button[aria-label*='Play']");

    if (playButton) {
        playButton.click();
    } else {
        const songLink =
            firstResult.querySelector("a[href*='/watch']") ||
            firstResult.querySelector("a[href*='watch?v=']");

        if (!songLink) {
            throw new Error(
                "Search results loaded, but no playable result was found."
            );
        }

        songLink.click();
    }

    await sleep(1200);

    const player = document.querySelector("ytmusic-player-bar");

    if (!player) {
        throw new Error(
            "Clicked the search result, but the music player was not detected."
        );
    }

    return {
        success: true,
        message: "Selected the first matching YouTube Music result.",
        data: {}
    };
}

async function clickPlayerButton(patterns, actionName) {
    const player = await waitForElement("ytmusic-player-bar");
    const buttons = Array.from(
        player.querySelectorAll(
            "button, tp-yt-paper-icon-button, yt-icon-button"
        )
    );

    const button = buttons.find((item) => {
        const labels = [
            item.getAttribute("aria-label"),
            item.getAttribute("title"),
            item.querySelector("[aria-label]")?.getAttribute("aria-label"),
            item.querySelector("[title]")?.getAttribute("title")
        ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

        return patterns.some((pattern) => pattern.test(labels));
    });

    if (!button) {
        throw new Error(`${actionName} control was not found.`);
    }

    button.click();

    return {
        success: true,
        message: `${actionName} command sent.`,
        data: {}
    };
}

async function setVolume(volume) {
    const player = await waitForElement("ytmusic-player-bar");

    const slider =
        player.querySelector("#volume-slider") ||
        player.querySelector('input[type="range"][aria-label*="volume" i]') ||
        document.querySelector('input[type="range"][aria-label*="volume" i]');

    if (!slider) {
        throw new Error(
            "Volume slider was not found. YouTube Music may have changed its interface."
        );
    }

    const min = Number(slider.min || 0);
    const max = Number(slider.max || 100);
    const target = min + ((max - min) * volume) / 100;

    const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value"
    )?.set;

    if (setter) {
        setter.call(slider, String(target));
    } else {
        slider.value = String(target);
    }

    slider.dispatchEvent(new Event("input", { bubbles: true }));
    slider.dispatchEvent(new Event("change", { bubbles: true }));

    return {
        success: true,
        message: `Volume set to ${volume}%.`,
        data: { volume }
    };
}

async function executeCommand(action, payload) {
    switch (action) {
        case "PLAY_FIRST_RESULT":
            return await playFirstResult();

        case "PAUSE":
            return await clickPlayerButton(
                [/^pause(?: playback)?$/],
                "Pause"
            );

        case "RESUME":
            return await clickPlayerButton(
                [/^play(?: playback)?$/, /^play$/],
                "Resume"
            );

        case "NEXT":
            return await clickPlayerButton(
                [/^next(?: song| track)?$/, /next/],
                "Next"
            );

        case "PREVIOUS":
            return await clickPlayerButton(
                [/^previous(?: song| track)?$/, /previous/],
                "Previous"
            );

        case "STOP": {
            const pauseButton = findButtonByLabel([
                /^pause(?: playback)?$/
            ]);

            if (pauseButton) {
                pauseButton.click();
            }

            return {
                success: true,
                message: pauseButton
                    ? "Playback paused."
                    : "Playback was already paused or no pause control was found.",
                data: {}
            };
        }

        case "SET_VOLUME":
            return await setVolume(Number(payload.volume));

        default:
            throw new Error(`Unsupported music action: ${action}`);
    }
}

chrome.runtime.onMessage.addListener(
    (message, sender, sendResponse) => {
        if (message?.type !== "EXECUTE_MUSIC_COMMAND") {
            return;
        }

        executeCommand(
            String(message.action || "").toUpperCase(),
            message.payload || {}
        )
            .then(sendResponse)
            .catch((error) => {
                sendResponse({
                    success: false,
                    message: error?.message || "Music command failed.",
                    data: {}
                });
            });

        return true;
    }
);