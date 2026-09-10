from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json, NonNegativeInt

from analytics.lib.counts import COUNT_STATS
from zerver.actions.message_summary import (
    do_recap_unread_messages,
    do_suggest_topic_title,
    do_summarize_narrow,
    no_topic_title_suggestion,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.narrow import NarrowParameter
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint, typed_endpoint_without_parameters
from zerver.models import UserProfile


def check_ai_summary_access(user_profile: UserProfile) -> None:
    if settings.TOPIC_SUMMARIZATION_MODEL is None:
        raise JsonableError(_("AI features are not enabled on this server."))

    if not user_profile.can_summarize_topics():
        raise JsonableError(_("Insufficient permission"))

    if settings.MAX_PER_USER_MONTHLY_AI_COST is not None:
        used_credits = COUNT_STATS["ai_credit_usage::day"].current_month_accumulated_count_for_user(
            user_profile
        )
        if used_credits >= settings.MAX_PER_USER_MONTHLY_AI_COST * 1000000000:
            raise JsonableError(_("Reached monthly limit for AI credits."))


@typed_endpoint
def get_messages_summary(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    narrow: Json[list[NarrowParameter] | None] = None,
) -> HttpResponse:
    check_ai_summary_access(user_profile)

    summary = do_summarize_narrow(user_profile, narrow)
    if summary is None:  # nocoverage
        raise JsonableError(_("No messages in conversation to summarize"))

    return json_success(request, {"summary": summary})


@typed_endpoint_without_parameters
def get_messages_recap(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    check_ai_summary_access(user_profile)
    return json_success(request, do_recap_unread_messages(user_profile))


@typed_endpoint
def get_topic_title_suggestion(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    message_id: PathOnly[NonNegativeInt],
) -> HttpResponse:
    message = access_message(user_profile, message_id, is_modifying_message=False)
    if not message.is_channel_message:
        return json_success(request, no_topic_title_suggestion())

    check_ai_summary_access(user_profile)
    return json_success(request, do_suggest_topic_title(user_profile, message))
