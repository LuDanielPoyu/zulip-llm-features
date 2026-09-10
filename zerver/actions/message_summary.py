import time
from typing import Any, Literal, TypedDict

import orjson
from django.conf import settings
from django.utils.timezone import now as timezone_now
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from analytics.lib.counts import COUNT_STATS, do_increment_logging_stat
from zerver.lib.exceptions import JsonableError
from zerver.lib.markdown import markdown_convert
from zerver.lib.message import messages_for_ids
from zerver.lib.narrow import (
    LARGER_THAN_MAX_MESSAGE_ID,
    AnchorInfo,
    NarrowParameter,
    clean_narrow_for_message_fetch,
    fetch_messages,
)
from zerver.lib.string_validation import check_stream_topic
from zerver.lib.topic import get_topic_from_message_info
from zerver.lib.url_encoding import message_link_url
from zerver.models import Message, UserProfile
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.realms import MessageEditHistoryVisibilityPolicyEnum

# Maximum number of messages that can be summarized in a single request.
MAX_MESSAGES_SUMMARIZED = 100

# Recaps include the most recent unread messages up to this limit. This keeps
# model context size and per-request cost bounded for users with large backlogs.
MAX_UNREAD_MESSAGES_RECAP = 100

MAX_RECENT_MESSAGES_FOR_TOPIC_TITLE_SUGGESTION = 12
MIN_MESSAGES_FOR_TOPIC_TITLE_SUGGESTION = 6


class MessageRecapReference(TypedDict):
    message_id: int
    label: str
    url: str


class MessageRecapResult(TypedDict):
    summary: str
    references: list[MessageRecapReference]


class TopicTitleSuggestionResult(TypedDict):
    drift_detected: bool
    suggested_title: str | None


ai_time_start = 0.0
ai_total_time = 0.0
ai_total_requests = 0


def get_ai_time() -> float:
    return ai_total_time


def ai_stats_start() -> None:
    global ai_time_start
    ai_time_start = time.time()


def get_ai_requests() -> int:
    return ai_total_requests


def ai_stats_finish() -> None:
    global ai_total_time, ai_total_requests
    ai_total_requests += 1
    ai_total_time += time.time() - ai_time_start


def format_zulip_messages_for_model(zulip_messages: list[dict[str, Any]]) -> str:
    # Format the Zulip messages for processing by the model.
    #
    # - We don't need to encode the recipient, since that's the same for
    #   every message in the conversation.
    # - We use full names to reference senders, since we want the
    #   model to refer to users by name. We may want to experiment
    #   with using silent-mention syntax for users if we move to
    #   Markdown-rendering what the model returns.
    # - We don't include timestamps, since experiments with current models
    #   suggest they do not make relevant use of them.
    # - We haven't figured out a useful way to include reaction metadata (either
    #   the emoji themselves or just the counter).
    # - Polls/TODO widgets are currently sent to the model as empty messages,
    #   since this logic doesn't inspect SubMessage objects.
    zulip_messages_list = [
        {"sender": message["sender_full_name"], "content": message["content"]}
        for message in zulip_messages
    ]
    return orjson.dumps(zulip_messages_list).decode()


def format_unread_messages_for_model(zulip_messages: list[dict[str, Any]]) -> str:
    messages = []
    for message in zulip_messages:
        formatted_message = {
            "message_id": message["id"],
            "sender": message["sender_full_name"],
            "recipient": message["display_recipient"],
            "content": message["content"],
        }
        if message["type"] == "stream":
            formatted_message["topic"] = get_topic_from_message_info(message)
        messages.append(formatted_message)
    return orjson.dumps(messages).decode()


def get_message_recap_reference(
    user_profile: UserProfile, message: dict[str, Any]
) -> MessageRecapReference:
    if message["type"] == "stream":
        label = f"#{message['display_recipient']} > {get_topic_from_message_info(message)}"
    else:
        recipient_names = [recipient["full_name"] for recipient in message["display_recipient"]]
        label = "Direct message with " + ", ".join(recipient_names)

    absolute_url = message_link_url(user_profile.realm, message)
    relative_url = absolute_url.removeprefix(f"{user_profile.realm.url}/")
    return {"message_id": message["id"], "label": label, "url": relative_url}


def make_message(
    content: str, role: Literal["user", "system"] = "user"
) -> ChatCompletionMessageParam:
    if role == "system":
        return {"content": content, "role": "system"}
    return {"content": content, "role": "user"}


def get_max_summary_length(conversation_length: int) -> int:
    # Longer summaries work better for longer conversation.
    # TODO: Test more with message content length.
    return min(6, 4 + int((conversation_length - 10) / 10))


def do_summarize_narrow(
    user_profile: UserProfile,
    narrow: list[NarrowParameter] | None,
) -> str | None:
    model = settings.TOPIC_SUMMARIZATION_MODEL
    if model is None:  # nocoverage
        return None

    # TODO: This implementation does not attempt to make use of
    # caching previous summaries of the same conversation or rolling
    # summaries. Doing so correctly will require careful work around
    # invalidation of caches when messages are edited, moved, or sent.
    narrow = clean_narrow_for_message_fetch(narrow, user_profile.realm, user_profile)
    query_info = fetch_messages(
        narrow=narrow,
        user_profile=user_profile,
        realm=user_profile.realm,
        is_web_public_query=False,
        anchor_info=AnchorInfo(type="message_id", value=LARGER_THAN_MAX_MESSAGE_ID),
        include_anchor=True,
        num_before=MAX_MESSAGES_SUMMARIZED,
        num_after=0,
    )

    if len(query_info.rows) == 0:  # nocoverage
        return None

    result_message_ids: list[int] = []
    user_message_flags: dict[int, list[str]] = {}
    for row in query_info.rows:
        message_id = row[0]
        result_message_ids.append(message_id)
        # We skip populating flags, since they would be ignored below anyway.
        user_message_flags[message_id] = []

    message_list = messages_for_ids(
        message_ids=result_message_ids,
        user_message_flags=user_message_flags,
        search_fields={},
        # We currently prefer the plain-text content of messages to
        apply_markdown=False,
        # Avoid wasting resources computing gravatars.
        client_gravatar=True,
        allow_empty_topic_name=False,
        # Avoid fetching edit history, which won't be passed to the model.
        message_edit_history_visibility_policy=MessageEditHistoryVisibilityPolicyEnum.none.value,
        user_profile=user_profile,
        realm=user_profile.realm,
    )

    # IDEA: We could consider translating input and output text to
    # English to improve results when using a summarization model that
    # is primarily trained on English.
    conversation_length = len(message_list)
    max_summary_length = get_max_summary_length(conversation_length)
    intro = "The following is a chat conversation in the Zulip team chat app."
    topic: str | None = None
    channel: str | None = None
    if narrow and len(narrow) == 2:
        for term in narrow:
            assert not term.negated
            if term.operator == "channel":
                channel = term.operand
            if term.operator == "topic":
                topic = term.operand
    if channel:
        intro += f" channel: {channel}"
    if topic:
        intro += f", topic: {topic}"

    formatted_conversation = format_zulip_messages_for_model(message_list)
    prompt = (
        f"Succinctly summarize this conversation based only on the information provided, "
        f"in up to {max_summary_length} sentences, for someone who is familiar with the context. "
        f"Mention key conclusions and actions, if any. Refer to specific people as appropriate. "
        f"Don't use an intro phrase. You can use Zulip's CommonMark based formatting."
    )
    messages = [
        make_message(intro, "system"),
        make_message(formatted_conversation),
        make_message(prompt),
    ]

    # Stats for database queries are tracked separately.
    ai_stats_start()

    # TODO when implementing user plans:
    # - Before querying the model, check whether we've enough tokens left using
    # an estimated token count.
    # - Then increase the `LoggingCountStat` using the estimated token count.
    # (These first two steps should be a short database transaction that
    # locks the `LoggingCountStat` row).
    # - Then query the model.
    # - Then adjust the `LoggingCountStat` by `(actual - estimated)`,
    # being careful to avoid doing this to the next day if the query
    # happened milliseconds before midnight; changing the
    # `LoggingCountStat` we added the estimate to.
    # That way, you can't easily get extra tokens by sending
    # 25 requests all at once when you're just below the limit.

    client = OpenAI(
        api_key=settings.TOPIC_SUMMARIZATION_API_KEY,
        base_url=settings.TOPIC_SUMMARIZATION_API_BASE,
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **settings.TOPIC_SUMMARIZATION_PARAMETERS,
    )
    assert response.usage is not None
    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens

    # Divide by 1 billion to get actual cost in USD.
    credits_used = (output_tokens * settings.OUTPUT_COST_PER_GIGATOKEN) + (
        input_tokens * settings.INPUT_COST_PER_GIGATOKEN
    )
    ai_stats_finish()

    do_increment_logging_stat(
        user_profile, COUNT_STATS["ai_credit_usage::day"], None, timezone_now(), credits_used
    )

    summary = response.choices[0].message.content
    assert summary is not None
    # TODO: This may want to fetch `MentionData`, in order to be able
    # to process channel or user mentions that might be in the
    # content. Requires a prompt that supports it.
    rendered_summary = markdown_convert(summary, message_realm=user_profile.realm).rendered_content
    return rendered_summary


def do_recap_unread_messages(user_profile: UserProfile) -> MessageRecapResult:
    model = settings.TOPIC_SUMMARIZATION_MODEL
    assert model is not None

    query_info = fetch_messages(
        narrow=[NarrowParameter(operator="is", operand="unread")],
        user_profile=user_profile,
        realm=user_profile.realm,
        is_web_public_query=False,
        anchor_info=AnchorInfo(type="message_id", value=LARGER_THAN_MAX_MESSAGE_ID),
        include_anchor=True,
        num_before=MAX_UNREAD_MESSAGES_RECAP,
        num_after=0,
    )
    if not query_info.rows:
        return {"summary": "", "references": []}

    message_ids = [row[0] for row in query_info.rows]
    message_list = messages_for_ids(
        message_ids=message_ids,
        user_message_flags={message_id: [] for message_id in message_ids},
        search_fields={},
        apply_markdown=False,
        client_gravatar=True,
        allow_empty_topic_name=True,
        message_edit_history_visibility_policy=MessageEditHistoryVisibilityPolicyEnum.none.value,
        user_profile=user_profile,
        realm=user_profile.realm,
    )

    messages = [
        make_message(
            "The following are unread messages for a user of the Zulip team chat app. "
            "Message IDs are source identifiers, not URLs.",
            "system",
        ),
        make_message(format_unread_messages_for_model(message_list)),
        make_message(
            "Write a concise recap based only on the supplied messages. Mention key decisions, "
            "questions, and action items. Cite relevant sources using message IDs in square "
            "brackets, for example [123]. Do not create URLs. You can use Zulip's CommonMark "
            "based formatting."
        ),
    ]

    ai_stats_start()
    client = OpenAI(
        api_key=settings.TOPIC_SUMMARIZATION_API_KEY,
        base_url=settings.TOPIC_SUMMARIZATION_API_BASE,
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **settings.TOPIC_SUMMARIZATION_PARAMETERS,
    )
    assert response.usage is not None
    credits_used = (response.usage.completion_tokens * settings.OUTPUT_COST_PER_GIGATOKEN) + (
        response.usage.prompt_tokens * settings.INPUT_COST_PER_GIGATOKEN
    )
    ai_stats_finish()
    do_increment_logging_stat(
        user_profile, COUNT_STATS["ai_credit_usage::day"], None, timezone_now(), credits_used
    )

    summary = response.choices[0].message.content
    assert summary is not None
    rendered_summary = markdown_convert(summary, message_realm=user_profile.realm).rendered_content
    references = [get_message_recap_reference(user_profile, message) for message in message_list]
    return {"summary": rendered_summary, "references": references}


def no_topic_title_suggestion() -> TopicTitleSuggestionResult:
    return {"drift_detected": False, "suggested_title": None}


def parse_topic_title_suggestion(raw_output: str, current_topic: str) -> TopicTitleSuggestionResult:
    try:
        parsed = orjson.loads(raw_output)
    except orjson.JSONDecodeError:
        return no_topic_title_suggestion()

    if not isinstance(parsed, dict) or set(parsed) != {"drift_detected", "suggested_title"}:
        return no_topic_title_suggestion()

    drift_detected = parsed["drift_detected"]
    suggested_title = parsed["suggested_title"]
    if not isinstance(drift_detected, bool):
        return no_topic_title_suggestion()
    if not drift_detected:
        if suggested_title is not None:
            return no_topic_title_suggestion()
        return no_topic_title_suggestion()
    if not isinstance(suggested_title, str):
        return no_topic_title_suggestion()

    if "\n" in suggested_title or "\r" in suggested_title:
        return no_topic_title_suggestion()
    try:
        # Validate before trimming so leading or trailing control characters
        # cannot be removed and accidentally accepted.
        check_stream_topic(suggested_title)
    except JsonableError:
        return no_topic_title_suggestion()

    suggested_title = suggested_title.strip()
    if (
        not suggested_title
        or len(suggested_title) > MAX_TOPIC_NAME_LENGTH
        or suggested_title.casefold() == current_topic.casefold()
    ):
        return no_topic_title_suggestion()

    return {"drift_detected": True, "suggested_title": suggested_title}


def do_suggest_topic_title(
    user_profile: UserProfile, anchor_message: Message
) -> TopicTitleSuggestionResult:
    assert anchor_message.is_channel_message
    model = settings.TOPIC_SUMMARIZATION_MODEL
    assert model is not None

    current_topic = anchor_message.topic_name()
    channel_id = anchor_message.recipient.type_id
    narrow = clean_narrow_for_message_fetch(
        [
            NarrowParameter(operator="channel", operand=channel_id),
            NarrowParameter(operator="topic", operand=current_topic),
        ],
        user_profile.realm,
        user_profile,
    )
    query_info = fetch_messages(
        narrow=narrow,
        user_profile=user_profile,
        realm=user_profile.realm,
        is_web_public_query=False,
        anchor_info=AnchorInfo(type="message_id", value=anchor_message.id),
        include_anchor=True,
        num_before=MAX_RECENT_MESSAGES_FOR_TOPIC_TITLE_SUGGESTION - 1,
        num_after=0,
    )
    if len(query_info.rows) < MIN_MESSAGES_FOR_TOPIC_TITLE_SUGGESTION:
        return no_topic_title_suggestion()

    message_ids = [row[0] for row in query_info.rows]
    message_list = messages_for_ids(
        message_ids=message_ids,
        user_message_flags={message_id: [] for message_id in message_ids},
        search_fields={},
        apply_markdown=False,
        client_gravatar=True,
        allow_empty_topic_name=True,
        message_edit_history_visibility_policy=MessageEditHistoryVisibilityPolicyEnum.none.value,
        user_profile=user_profile,
        realm=user_profile.realm,
    )
    formatted_messages = orjson.dumps(
        [
            {
                "message_id": message["id"],
                "sender": message["sender_full_name"],
                "content": message["content"],
            }
            for message in message_list
        ]
    ).decode()
    messages = [
        make_message(
            "You evaluate whether a Zulip discussion has drifted away from its current topic "
            "title. Return strict JSON only, with exactly the keys drift_detected (boolean) and "
            "suggested_title (string or null).",
            "system",
        ),
        make_message(f"Current topic title: {orjson.dumps(current_topic).decode()}"),
        make_message(f"Recent discussion messages: {formatted_messages}"),
        make_message(
            "Set drift_detected to true only when the discussion has clearly moved to a "
            "different subject and a clearer title is justified. If false, suggested_title "
            "must be null. If true, provide a concise title no longer than 60 characters."
        ),
    ]

    ai_stats_start()
    client = OpenAI(
        api_key=settings.TOPIC_SUMMARIZATION_API_KEY,
        base_url=settings.TOPIC_SUMMARIZATION_API_BASE,
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **settings.TOPIC_SUMMARIZATION_PARAMETERS,
    )
    assert response.usage is not None
    credits_used = (response.usage.completion_tokens * settings.OUTPUT_COST_PER_GIGATOKEN) + (
        response.usage.prompt_tokens * settings.INPUT_COST_PER_GIGATOKEN
    )
    ai_stats_finish()
    do_increment_logging_stat(
        user_profile, COUNT_STATS["ai_credit_usage::day"], None, timezone_now(), credits_used
    )

    raw_output = response.choices[0].message.content
    if raw_output is None:
        return no_topic_title_suggestion()
    return parse_topic_title_suggestion(raw_output, current_topic)
