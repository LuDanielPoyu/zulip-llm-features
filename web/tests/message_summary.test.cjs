"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const {$} = require("./lib/zjquery.cjs");

const channel = mock_esm("../src/channel");
const dialog_widget = mock_esm("../src/dialog_widget");
mock_esm("../src/rendered_markdown", {update_elements: noop});

let unread_count = 0;
mock_esm("../src/unread", {
    get_unread_message_count: () => unread_count,
    topic_has_any_unread: () => false,
});

const message_summary = zrequire("message_summary");

run_test("unread recap button and successful request", ({mock_template}) => {
    mock_template("message_recap.hbs", true, (_args, html) => html);

    let launch_count = 0;
    let dialog_config;
    dialog_widget.launch = (config) => {
        launch_count += 1;
        dialog_config = config;
    };

    message_summary.initialize();
    const $button = $("#recap-unread-messages-button");
    assert.equal($button.prop("disabled"), true);

    unread_count = 2;
    message_summary.update_recap_button_state();
    assert.equal($button.prop("disabled"), false);

    const click_handler = $("body").get_on_handler("click", "#recap-unread-messages-button");
    click_handler();
    assert.equal(launch_count, 1);
    assert.equal($button.prop("disabled"), true);
    assert.ok(dialog_config.modal_content_html.includes("Generating a recap"));

    // A second click while the first request is running is ignored.
    click_handler();
    assert.equal(launch_count, 1);

    let request_options;
    channel.get = (options) => {
        request_options = options;
    };
    dialog_config.post_render();
    assert.equal(request_options.url, "/json/messages/recap");
    assert.deepEqual(request_options.data, {});

    request_options.success({
        summary: "<p>A concise recap.</p>",
        references: [
            {
                message_id: 123,
                label: "#Verona > recap topic",
                url: "#narrow/channel/1-Verona/topic/recap-topic/near/123",
            },
        ],
    });

    const content = $("#message-recap-modal .modal__content").html();
    assert.ok(content.includes("A concise recap."));
    assert.ok(content.includes("#Verona &gt; recap topic"));
    assert.ok(content.includes('href="#narrow/channel/1-Verona/topic/recap-topic/near/123"'));
    assert.equal($button.prop("disabled"), false);

    click_handler();
    dialog_config.post_render();
    request_options.success({summary: "", references: []});
    assert.ok(
        $("#message-recap-modal .modal__content").html().includes("no unread messages to recap"),
    );
});

run_test("unread recap API error", ({mock_template}) => {
    mock_template("message_recap.hbs", true, (_args, html) => html);

    let dialog_config;
    dialog_widget.launch = (config) => {
        dialog_config = config;
    };
    message_summary.get_unread_messages_recap();

    let request_options;
    channel.get = (options) => {
        request_options = options;
    };
    dialog_config.post_render();
    request_options.error();

    const content = $("#message-recap-modal .modal__content").html();
    assert.ok(content.includes("Unable to generate a recap"));
    assert.equal($("#recap-unread-messages-button").prop("disabled"), false);
});
