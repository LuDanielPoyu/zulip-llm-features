"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const {$} = require("./lib/zjquery.cjs");

const channel = mock_esm("../src/channel");
const feedback_widget = mock_esm("../src/feedback_widget");
const message_edit = mock_esm("../src/message_edit");

const topic_title_suggestion = zrequire("topic_title_suggestion");

run_test("topic title suggestion request and UI", ({mock_template}) => {
    mock_template("topic_title_suggestion.hbs", true, (_args, html) => html);

    const requests = [];
    channel.post = (options) => {
        requests.push(options);
    };

    const feedback_calls = [];
    feedback_widget.show = (options) => {
        feedback_calls.push(options);
    };

    // Starting the request returns before any response and displays nothing.
    assert.equal(topic_title_suggestion.request_topic_title_suggestion(42), undefined);
    assert.equal(requests.length, 1);
    assert.equal(feedback_calls.length, 0);
    assert.equal(requests[0].url, "/json/messages/42/topic_title_suggestion");
    assert.deepEqual(requests[0].data, {});

    // A duplicate request for the same message is suppressed while in flight.
    topic_title_suggestion.request_topic_title_suggestion(42);
    assert.equal(requests.length, 1);

    requests[0].success({
        result: "success",
        msg: "",
        drift_detected: false,
        suggested_title: null,
    });
    assert.equal(feedback_calls.length, 0);

    // Completion clears the in-flight state, allowing another request.
    topic_title_suggestion.request_topic_title_suggestion(42);
    assert.equal(requests.length, 2);
    requests[1].success({
        result: "success",
        msg: "",
        drift_detected: true,
        suggested_title: "Safer <script>alert('x')</script> title",
    });
    assert.equal(feedback_calls.length, 1);

    const feedback = feedback_calls[0];
    assert.equal(feedback.title_text, "translated: Topic suggestion");
    assert.equal(feedback.undo_button_text, "translated: Use suggested title");
    const $content = $.create("topic-title-suggestion-content");
    feedback.populate($content);
    assert.ok($content.html().includes("This discussion may have drifted."));
    assert.ok($content.html().includes("Safer &lt;script&gt;alert"));
    assert.ok(!$content.html().includes("<script>"));

    let rename_args;
    message_edit.move_topic_containing_message_to_stream = (...args) => {
        rename_args = args;
    };
    feedback.on_undo();
    assert.deepEqual(rename_args, [
        42,
        undefined,
        "Safer <script>alert('x')</script> title",
        false,
        false,
        "change_all",
    ]);
});

run_test("topic title suggestion API failure is quiet", () => {
    let request;
    channel.post = (options) => {
        request = options;
    };
    let feedback_count = 0;
    feedback_widget.show = () => {
        feedback_count += 1;
    };

    topic_title_suggestion.request_topic_title_suggestion(99);
    assert.doesNotThrow(() => request.error());
    assert.equal(feedback_count, 0);

    // Failure also clears the in-flight state.
    topic_title_suggestion.request_topic_title_suggestion(99);
    assert.ok(request);
});
