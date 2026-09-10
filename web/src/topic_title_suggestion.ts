import * as z from "zod/mini";

import render_topic_title_suggestion from "../templates/topic_title_suggestion.hbs";

import * as channel from "./channel.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import * as message_edit from "./message_edit.ts";

const topic_title_suggestion_response_schema = z.object({
    drift_detected: z.boolean(),
    suggested_title: z.nullable(z.string()),
});

const requests_in_flight = new Set<number>();

function show_topic_title_suggestion(message_id: number, suggested_title: string): void {
    feedback_widget.show({
        title_text: $t({defaultMessage: "Topic suggestion"}),
        populate($container) {
            $container.html(render_topic_title_suggestion({suggested_title}));
        },
        undo_button_text: $t({defaultMessage: "Use suggested title"}),
        on_undo() {
            message_edit.move_topic_containing_message_to_stream(
                message_id,
                undefined,
                suggested_title,
                false,
                false,
                "change_all",
            );
        },
    });
}

export function request_topic_title_suggestion(message_id: number): void {
    if (requests_in_flight.has(message_id)) {
        return;
    }
    requests_in_flight.add(message_id);

    void channel.post({
        url: `/json/messages/${message_id}/topic_title_suggestion`,
        data: {},
        success(response_data) {
            requests_in_flight.delete(message_id);
            const parsed = topic_title_suggestion_response_schema.safeParse(response_data);
            if (
                !parsed.success ||
                !parsed.data.drift_detected ||
                parsed.data.suggested_title === null
            ) {
                return;
            }
            show_topic_title_suggestion(message_id, parsed.data.suggested_title);
        },
        error() {
            // The original message has already been sent successfully. A title
            // suggestion failure should therefore be silent and non-disruptive.
            requests_in_flight.delete(message_id);
        },
    });
}
