# Implementation

## Feature 1: Message Recap

The Message Recap backend is implemented primarily in `zerver/actions/message_summary.py`, with the HTTP view in `zerver/views/message_summary.py` and route registration in `zproject/urls.py`. The recap action collects unread messages belonging to the requesting user and only includes messages that the user can access. If there are no unread messages, the implementation returns without making an unnecessary LLM request. The selected messages are converted into a compact representation and sent to the LLM using Zulip's existing OpenAI-compatible topic-summarization configuration. The LLM output is then returned for display in the recap UI. Backend behavior, including authentication, unread-message filtering, provider failures, and LLM configuration, is covered in `zerver/tests/test_message_summary.py`.

A central requirement is making the recap useful as navigation rather than just plain generated text. For channel messages, my implementation creates references using the Zulip narrow URL structure containing the channel, topic, and original message ID, following the form `#narrow/channel/<channel_id>-<channel_name>/topic/<topic>/near/<message_id>`. This lets a user click a reference in the recap and jump directly to the source message. Direct-message references are also handled so the recap can refer back to unread DMs.

The frontend requests `/json/messages/recap` and presents the generated recap to the user. The UI keeps the generated text connected to the underlying product by making the source references clickable instead of asking the user to trust an ungrounded summary.

A production version would need more work around very large unread sets, prompt-size limits, caching, retries, and evaluation of summary quality. An LLM can omit or misstate important information, so retaining links to original messages is an important safety and usability feature.

**Demo video:** [Watch the demo video](https://www.youtube.com/watch?v=LJ3asgrqHu0)

---

## Feature 2: Topic Title Improver

The Topic Title Improver backend is implemented in `zerver/actions/message_summary.py`. The endpoint is exposed from `zerver/views/message_summary.py` and `zproject/urls.py` as `POST /json/messages/<message_id>/topic_title_suggestion`. The newly sent message acts as the authoritative anchor: the backend verifies access to it, rejects direct-message cases, derives its channel and topic, and retrieves only a limited recent history of that topic. The implementation skips the LLM call when there are too few messages to make a meaningful drift decision.

The LLM is prompted to return structured drift information. The backend parses and validates the result and only returns a suggested title when the response is well formed and useful. Invalid, unchanged, unsafe, or otherwise unsuitable titles are rejected. Tests for successful drift detection, the history limit, insufficient history, malformed model output, unsafe titles, permissions, failures, and usage accounting are in `zerver/tests/test_message_summary.py`.

Frontend integration begins in `web/src/compose.ts` after a channel message has successfully been sent. The suggestion request is fire-and-forget, so LLM latency or provider failure does not delay normal message sending. `web/src/topic_title_suggestion.ts` handles the request/result and `web/templates/topic_title_suggestion.hbs` renders the suggestion. If drift is detected, the user is shown the proposed title and may explicitly accept it. The existing Zulip topic-editing path is reused with `change_all`, so the user remains in control and the model never silently renames a topic.

For latency, cost, and scalability, the implementation limits the amount of history sent to the model, skips calls with insufficient context, and reuses Zulip's existing AI usage/quota accounting. Triggering after a successful send also keeps the core send path responsive. At production scale, I would additionally add stronger per-topic rate limiting/cooldowns, caching, background-job execution, monitoring, and evaluation of false-positive drift suggestions. These would reduce provider cost and prevent an LLM call for every eligible message at very high traffic volumes.

**Demo video:** [Watch the demo video](https://www.youtube.com/watch?v=LJ3asgrqHu0)
