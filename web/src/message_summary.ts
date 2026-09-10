import {$} from "jquery";
import * as z from "zod/mini";

import render_message_recap from "../templates/message_recap.hbs";
import render_topic_summary from "../templates/topic_summary.hbs";

import * as channel from "./channel.ts";
import * as dialog_widget from "./dialog_widget.ts";
import {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import * as message_fetch from "./message_fetch.ts";
import * as rendered_markdown from "./rendered_markdown.ts";
import * as unread from "./unread.ts";
import * as unread_ops from "./unread_ops.ts";
import * as util from "./util.ts";

const message_recap_reference_schema = z.object({
    message_id: z.number(),
    label: z.string(),
    url: z.string(),
});
const message_recap_response_schema = z.object({
    summary: z.string(),
    references: z.array(message_recap_reference_schema),
});

let recap_request_in_progress = false;

export function update_recap_button_state(): void {
    $("#recap-unread-messages-button").prop(
        "disabled",
        recap_request_in_progress || unread.get_unread_message_count() === 0,
    );
}

function finish_recap_request(): void {
    recap_request_in_progress = false;
    update_recap_button_state();
}

export function get_unread_messages_recap(): void {
    if (recap_request_in_progress || unread.get_unread_message_count() === 0) {
        return;
    }

    recap_request_in_progress = true;
    update_recap_button_state();
    dialog_widget.launch({
        modal_title_text: $t({defaultMessage: "Unread messages recap"}),
        modal_content_html: render_message_recap({loading: true}),
        modal_submit_button_text: $t({defaultMessage: "Close"}),
        single_footer_button: true,
        close_on_submit: true,
        id: "message-recap-modal",
        footer_minor_text: $t({defaultMessage: "AI recaps may have errors."}),
        post_render() {
            void channel.get({
                url: "/json/messages/recap",
                data: {},
                success(response_data) {
                    const data = message_recap_response_schema.parse(response_data);
                    const $content = $("#message-recap-modal .modal__content");
                    $content.html(
                        render_message_recap({
                            summary: data.summary,
                            references: data.references,
                            has_references: data.references.length > 0,
                            empty: data.summary === "" && data.references.length === 0,
                        }),
                    );
                    rendered_markdown.update_elements($content);
                    finish_recap_request();
                },
                error() {
                    $("#message-recap-modal .modal__content").html(
                        render_message_recap({error: true}),
                    );
                    finish_recap_request();
                },
            });
        },
    });
}

export function initialize(): void {
    $("body").on("click", "#recap-unread-messages-button", get_unread_messages_recap);
    update_recap_button_state();
}

export function get_narrow_summary(channel_id: number, topic_name: string): void {
    const filter = new Filter([
        {operator: "channel", operand: `${channel_id}`},
        {operator: "topic", operand: topic_name},
    ]);
    const data = {narrow: message_fetch.get_narrow_for_message_fetch(filter)};
    const display_topic_name = util.get_final_topic_display_name(topic_name);
    const unread_topic_params = {
        modal_submit_button_text: $t({defaultMessage: "Mark topic as read"}),
        modal_exit_button_text: $t({defaultMessage: "Close"}),
        on_click() {
            unread_ops.mark_topic_as_read(channel_id, topic_name);
        },
        single_footer_button: false,
    };

    let params = {
        modal_submit_button_text: $t({defaultMessage: "Close"}),
        on_click() {
            // Just close the modal, there is nothing else to do.
        },
        single_footer_button: true,
    };
    if (unread.topic_has_any_unread(channel_id, topic_name)) {
        params = {
            ...params,
            ...unread_topic_params,
        };
    }
    dialog_widget.launch({
        modal_title_text: display_topic_name,
        modal_content_html: "<div></div>", // TODO: Add a loading indicator here instead of a placeholder.
        close_on_submit: true,
        id: "topic-summary-modal",
        footer_minor_text: $t({defaultMessage: "AI summaries may have errors."}),
        ...params,
        on_show() {
            $("#topic-summary-modal .modal__content").addClass("hide");
        },
        post_render() {
            const close_on_success = false;
            dialog_widget.submit_api_request(
                channel.get,
                "/json/messages/summary",
                data,
                {
                    success_continuation(response_data) {
                        const data = z.object({summary: z.string()}).parse(response_data);
                        const summary_markdown = data.summary;
                        const summary_html = render_topic_summary({
                            summary_markdown,
                        });
                        $("#topic-summary-modal .modal__content")
                            .removeClass("hide")
                            .addClass("rendered_markdown");
                        $("#topic-summary-modal .modal__content").html(summary_html);
                        rendered_markdown.update_elements(
                            $("#topic-summary-modal .modal__content"),
                        );
                    },
                },
                close_on_success,
            );
        },
    });
}
