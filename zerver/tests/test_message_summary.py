from datetime import datetime, timezone
from typing import Any
from unittest import mock

import orjson
import time_machine
from django.conf import settings
from openai.resources.chat.completions import Completions
from openai.types.chat import ChatCompletion
from typing_extensions import override

from analytics.models import UserCount
from zerver.actions.message_flags import do_update_message_flags
from zerver.actions.realm_settings import do_change_realm_permission_group_setting
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import NamedUserGroup, UserMessage
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.groups import SystemGroups
from zerver.models.realms import get_realm

# Fixture file to store recorded responses
LLM_FIXTURES_FILE = "zerver/tests/fixtures/llm/summary.json"


class MessagesSummaryTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user = self.example_user("iago")
        self.topic_name = "New feature launch"
        self.channel_name = "Zulip features"

        self.login_user(self.user)
        self.subscribe(self.user, self.channel_name)
        content = "Zulip just launched a feature to generate summary of messages."
        self.send_stream_message(
            self.user, self.channel_name, content=content, topic_name=self.topic_name
        )

        content = "Sounds awesome! This will **greatly** help me when catching up."
        self.send_stream_message(
            self.user, self.channel_name, content=content, topic_name=self.topic_name
        )

        # Tests fail on the last day of the month due to us capturing the credit usage for that day
        # on the first of the next month, so we need to set the date to a different day.
        not_last_day_of_any_month = datetime(2025, 2, 18, 1, tzinfo=timezone.utc)

        self.mocked_time_patcher = time_machine.travel(not_last_day_of_any_month, tick=False)
        self.mocked_time_patcher.start()
        if settings.GENERATE_LLM_FIXTURES:  # nocoverage
            # Forward calls to the real `create` method via side_effect.
            # autospec=True makes the patched mock honor the descriptor
            # protocol so the action's `client.chat.completions.create(...)`
            # call passes `self` (the action's own Completions instance).
            self.patcher = mock.patch.object(
                Completions, "create", autospec=True, side_effect=Completions.create
            )
            self.mocked_completion = self.patcher.start()

    @override
    def tearDown(self) -> None:
        self.mocked_time_patcher.stop()
        if settings.GENERATE_LLM_FIXTURES:  # nocoverage
            self.patcher.stop()
        super().tearDown()

    def test_summarize_messages_in_topic(self) -> None:
        narrow = orjson.dumps([["channel", self.channel_name], ["topic", self.topic_name]]).decode()

        if settings.GENERATE_LLM_FIXTURES:  # nocoverage
            # NOTE: You need have proper credentials in zproject/dev-secrets.conf
            # to generate the fixtures.
            # Trigger the API call to extract the arguments.
            self.client_get("/json/messages/summary", dict(narrow=narrow))
            call_args = self.mocked_completion.call_args

            # Once we have the arguments, call the original method and save its response.
            response = self.mocked_completion(*call_args.args, **call_args.kwargs)
            with open(LLM_FIXTURES_FILE, "wb") as f:
                fixture_data = {
                    # Only store model and messages.
                    # We don't want to store any secrets.
                    "model": call_args.kwargs["model"],
                    "messages": call_args.kwargs["messages"],
                    "response": response.model_dump(mode="json"),
                }
                f.write(orjson.dumps(fixture_data, option=orjson.OPT_INDENT_2) + b"\n")
            return

        # In this code path, we test using the fixtures.
        with open(LLM_FIXTURES_FILE, "rb") as f:
            fixture_data = orjson.loads(f.read())

        fake_response = ChatCompletion.model_validate(fixture_data["response"])

        # Block summary requests if budget set to 0.
        with self.settings(
            TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
            MAX_PER_USER_MONTHLY_AI_COST=0,
        ):
            response = self.client_get("/json/messages/summary")
            self.assert_json_error_contains(response, "Reached monthly limit for AI credits.")

        # Fake credentials to ensure we crash if actual network
        # requests occur, which would reflect a problem with how the
        # fixtures were set up.
        with self.settings(
            TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
            TOPIC_SUMMARIZATION_API_KEY="test",
        ):
            input_tokens = fixture_data["response"]["usage"]["prompt_tokens"]
            output_tokens = fixture_data["response"]["usage"]["completion_tokens"]
            credits_used = (output_tokens * settings.OUTPUT_COST_PER_GIGATOKEN) + (
                input_tokens * settings.INPUT_COST_PER_GIGATOKEN
            )
            self.assertFalse(
                UserCount.objects.filter(
                    property="ai_credit_usage::day", value=credits_used, user_id=self.user.id
                ).exists()
            )
            with mock.patch.object(Completions, "create", return_value=fake_response):
                payload = self.client_get("/json/messages/summary", dict(narrow=narrow))
                self.assertEqual(payload.status_code, 200)
            # Check that we recorded this usage.
            self.assertTrue(
                UserCount.objects.filter(
                    property="ai_credit_usage::day", value=credits_used, user_id=self.user.id
                ).exists()
            )

        # If we reached the credit usage limit, block summary requests.
        with self.settings(
            TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
            MAX_PER_USER_MONTHLY_AI_COST=credits_used / 1000000000,
        ):
            response = self.client_get("/json/messages/summary")
            self.assert_json_error_contains(response, "Reached monthly limit for AI credits.")

    def test_permission_to_summarize_message_in_topics(self) -> None:
        narrow = orjson.dumps([["channel", self.channel_name], ["topic", self.topic_name]]).decode()

        realm = get_realm("zulip")
        moderators_group = NamedUserGroup.objects.get(
            name=SystemGroups.MODERATORS, realm_for_sharding=realm, is_system_group=True
        )

        do_change_realm_permission_group_setting(
            realm,
            "can_summarize_topics_group",
            moderators_group,
            acting_user=None,
        )

        # In this code path, we test using the fixtures.
        with open(LLM_FIXTURES_FILE, "rb") as f:
            fixture_data = orjson.loads(f.read())

        fake_response = ChatCompletion.model_validate(fixture_data["response"])

        def check_message_summary_permission(user: str, expect_fail: bool = False) -> None:
            self.login(user)
            with (
                self.settings(
                    TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                    TOPIC_SUMMARIZATION_API_KEY="test",
                ),
                mock.patch.object(Completions, "create", return_value=fake_response),
            ):
                result = self.client_get("/json/messages/summary", dict(narrow=narrow))

            if expect_fail:
                self.assert_json_error(result, "Insufficient permission")
            else:
                self.assert_json_success(result)

        check_message_summary_permission("hamlet", expect_fail=True)
        check_message_summary_permission("shiva")

        nobody_group = NamedUserGroup.objects.get(
            name=SystemGroups.NOBODY, realm_for_sharding=realm, is_system_group=True
        )
        do_change_realm_permission_group_setting(
            realm,
            "can_summarize_topics_group",
            nobody_group,
            acting_user=None,
        )

        check_message_summary_permission("desdemona", expect_fail=True)

        hamletcharacters_group = NamedUserGroup.objects.get(
            name="hamletcharacters", realm_for_sharding=realm
        )
        do_change_realm_permission_group_setting(
            realm,
            "can_summarize_topics_group",
            hamletcharacters_group,
            acting_user=None,
        )

        check_message_summary_permission("desdemona", expect_fail=True)
        check_message_summary_permission("othello", expect_fail=True)
        check_message_summary_permission("hamlet")
        check_message_summary_permission("cordelia")

        setting_group = self.create_or_update_anonymous_group_for_setting(
            [self.example_user("othello")], [moderators_group]
        )
        do_change_realm_permission_group_setting(
            realm,
            "can_summarize_topics_group",
            setting_group,
            acting_user=None,
        )

        check_message_summary_permission("cordelia", expect_fail=True)
        check_message_summary_permission("hamlet", expect_fail=True)
        check_message_summary_permission("othello")
        check_message_summary_permission("shiva")
        check_message_summary_permission("desdemona")


class MessagesRecapTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user = self.example_user("iago")
        self.sender = self.example_user("hamlet")
        self.channel_name = "Verona"
        self.topic_name = "recap topic"
        self.subscribe(self.user, self.channel_name)
        self.subscribe(self.sender, self.channel_name)
        self.login_user(self.user)

        # Keep each test independent of unread messages in the common fixtures.
        unread_ids = list(
            UserMessage.objects.filter(user_profile=self.user)
            .extra(where=[UserMessage.where_unread()])  # noqa: S610
            .values_list("message_id", flat=True)
        )
        if unread_ids:
            do_update_message_flags(self.user, "add", "read", unread_ids)

        with open(LLM_FIXTURES_FILE, "rb") as f:
            fixture_data = orjson.loads(f.read())
        self.fake_response = ChatCompletion.model_validate(fixture_data["response"])

    def get_recap(self) -> tuple[dict[str, Any], mock.MagicMock]:
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create", return_value=self.fake_response) as create,
        ):
            response = self.client_get("/json/messages/recap")
        return self.assert_json_success(response), create

    def test_recap_includes_only_current_users_unread_messages(self) -> None:
        unread_id = self.send_stream_message(
            self.sender,
            self.channel_name,
            content="Unread channel content",
            topic_name=self.topic_name,
        )
        read_id = self.send_stream_message(
            self.sender,
            self.channel_name,
            content="Read channel content",
            topic_name=self.topic_name,
        )
        do_update_message_flags(self.user, "add", "read", [read_id])

        other_user = self.example_user("othello")
        self.send_personal_message(self.sender, other_user, "Only Othello should see this")

        result, create = self.get_recap()
        references = result["references"]
        self.assertEqual([reference["message_id"] for reference in references], [unread_id])
        self.assertEqual(references[0]["label"], "#Verona > recap topic")
        self.assertTrue(references[0]["url"].startswith("#narrow/channel/"))
        self.assertTrue(references[0]["url"].endswith(f"/near/{unread_id}"))

        model_messages = create.call_args.kwargs["messages"]
        model_input = orjson.loads(model_messages[1]["content"])
        self.assertEqual(
            model_input,
            [
                {
                    "message_id": unread_id,
                    "sender": self.sender.full_name,
                    "recipient": self.channel_name,
                    "content": "Unread channel content",
                    "topic": self.topic_name,
                }
            ],
        )
        self.assertNotIn("Read channel content", model_messages[1]["content"])
        self.assertNotIn("Only Othello", model_messages[1]["content"])
        self.assertNotEqual(result["summary"], "")

    def test_recap_direct_message_reference(self) -> None:
        message_id = self.send_personal_message(self.sender, self.user, "Unread direct message")

        result, create = self.get_recap()
        reference = result["references"][0]
        self.assertEqual(reference["message_id"], message_id)
        self.assertIn("Direct message with", reference["label"])
        self.assertTrue(reference["url"].startswith("#narrow/dm/"))
        self.assertTrue(reference["url"].endswith(f"/near/{message_id}"))
        model_input = orjson.loads(create.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(model_input[0]["message_id"], message_id)
        self.assertNotIn("topic", model_input[0])

    def test_recap_with_no_unread_messages_skips_llm(self) -> None:
        result, create = self.get_recap()
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["references"], [])
        create.assert_not_called()

    def test_recap_llm_failure(self) -> None:
        self.send_stream_message(
            self.sender, self.channel_name, content="Unread", topic_name=self.topic_name
        )
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create", side_effect=RuntimeError("LLM failed")),
            self.assertRaises(RuntimeError, msg="LLM failed"),
        ):
            self.client_get("/json/messages/recap")

    def test_recap_ai_configuration_and_permission_checks(self) -> None:
        with self.settings(TOPIC_SUMMARIZATION_MODEL=None):
            response = self.client_get("/json/messages/recap")
        self.assert_json_error(response, "AI features are not enabled on this server.")

        nobody_group = NamedUserGroup.objects.get(
            name=SystemGroups.NOBODY,
            realm_for_sharding=self.user.realm,
            is_system_group=True,
        )
        do_change_realm_permission_group_setting(
            self.user.realm,
            "can_summarize_topics_group",
            nobody_group,
            acting_user=None,
        )
        with self.settings(TOPIC_SUMMARIZATION_MODEL="llama-3.3-70b-versatile"):
            response = self.client_get("/json/messages/recap")
        self.assert_json_error(response, "Insufficient permission")

    def test_recap_requires_authentication(self) -> None:
        self.logout()
        response = self.client_get("/json/messages/recap")
        self.assert_json_error(
            response,
            "Not logged in: API authentication or user session required",
            status_code=401,
        )


class TopicTitleSuggestionTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user = self.example_user("iago")
        self.other_user = self.example_user("hamlet")
        self.channel_name = "Verona"
        self.topic_name = "deployment planning"
        self.subscribe(self.user, self.channel_name)
        self.subscribe(self.other_user, self.channel_name)
        self.login_user(self.user)

    def send_topic_messages(self, count: int) -> list[int]:
        return [
            self.send_stream_message(
                self.other_user,
                self.channel_name,
                topic_name=self.topic_name,
                content=f"Recent discussion message {index}",
            )
            for index in range(count)
        ]

    def fake_completion(self, content: str) -> mock.MagicMock:
        response = mock.MagicMock()
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 5
        response.choices[0].message.content = content
        return response

    def assert_no_suggestion(self, result: dict[str, Any]) -> None:
        self.assertFalse(result["drift_detected"])
        self.assertIsNone(result["suggested_title"])

    def post_suggestion(
        self, message_id: int, model_output: str
    ) -> tuple[dict[str, Any], mock.MagicMock]:
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="test-model",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(
                Completions, "create", return_value=self.fake_completion(model_output)
            ) as create,
        ):
            response = self.client_post(f"/json/messages/{message_id}/topic_title_suggestion", {})
        return self.assert_json_success(response), create

    def test_successful_drift_suggestion_and_history_limit(self) -> None:
        message_ids = self.send_topic_messages(15)
        result, create = self.post_suggestion(
            message_ids[-1],
            orjson.dumps(
                {
                    "drift_detected": True,
                    "suggested_title": "  Production deployment issues  ",
                }
            ).decode(),
        )
        self.assertTrue(result["drift_detected"])
        self.assertEqual(result["suggested_title"], "Production deployment issues")

        model_messages = create.call_args.kwargs["messages"]
        self.assertEqual(
            model_messages[1]["content"],
            f'Current topic title: "{self.topic_name}"',
        )
        discussion = orjson.loads(
            model_messages[2]["content"].removeprefix("Recent discussion messages: ")
        )
        self.assert_length(discussion, 12)
        self.assertEqual([message["message_id"] for message in discussion], message_ids[-12:])
        self.assertEqual(
            set(discussion[0]),
            {"message_id", "sender", "content"},
        )

    def test_valid_no_drift_response(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        result, create = self.post_suggestion(
            message_id, '{"drift_detected":false,"suggested_title":null}'
        )
        self.assert_no_suggestion(result)
        create.assert_called_once()

    def test_too_few_messages_skips_llm(self) -> None:
        message_id = self.send_topic_messages(5)[-1]
        result, create = self.post_suggestion(
            message_id, '{"drift_detected":true,"suggested_title":"Unused"}'
        )
        self.assert_no_suggestion(result)
        create.assert_not_called()

    def test_direct_message_anchor_skips_llm(self) -> None:
        message_id = self.send_personal_message(self.other_user, self.user)
        result, create = self.post_suggestion(
            message_id, '{"drift_detected":true,"suggested_title":"Unused"}'
        )
        self.assert_no_suggestion(result)
        create.assert_not_called()

    def test_inaccessible_and_nonexistent_anchor(self) -> None:
        inaccessible_id = self.send_personal_message(self.other_user, self.example_user("othello"))
        with self.settings(TOPIC_SUMMARIZATION_MODEL="test-model"):
            for message_id in [inaccessible_id, 999999999]:
                with self.subTest(message_id=message_id):
                    response = self.client_post(
                        f"/json/messages/{message_id}/topic_title_suggestion", {}
                    )
                    self.assert_json_error(response, "Invalid message(s)")

    def test_malformed_and_wrongly_typed_model_output(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        outputs = [
            "not JSON",
            "[]",
            '{"drift_detected":1,"suggested_title":"New title"}',
            '{"drift_detected":true,"suggested_title":null}',
            '{"drift_detected":false,"suggested_title":"New title"}',
            '{"drift_detected":true,"suggested_title":"New title","extra":1}',
        ]
        for output in outputs:
            with self.subTest(output=output):
                result, create = self.post_suggestion(message_id, output)
                self.assert_no_suggestion(result)
                create.assert_called_once()

    def test_unsafe_or_unhelpful_titles_are_rejected(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        titles = [
            "",
            "   ",
            self.topic_name.upper(),
            "x" * (MAX_TOPIC_NAME_LENGTH + 1),
            "New title\nwith newline",
            "\nNew title",
            "\tNew title",
            "New title\x00with control character",
        ]
        for title in titles:
            with self.subTest(title=title):
                output = orjson.dumps({"drift_detected": True, "suggested_title": title}).decode()
                result, _create = self.post_suggestion(message_id, output)
                self.assert_no_suggestion(result)

    def test_ai_configuration_permission_and_quota(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        url = f"/json/messages/{message_id}/topic_title_suggestion"

        with self.settings(TOPIC_SUMMARIZATION_MODEL=None):
            response = self.client_post(url, {})
        self.assert_json_error(response, "AI features are not enabled on this server.")

        nobody_group = NamedUserGroup.objects.get(
            name=SystemGroups.NOBODY,
            realm_for_sharding=self.user.realm,
            is_system_group=True,
        )
        do_change_realm_permission_group_setting(
            self.user.realm,
            "can_summarize_topics_group",
            nobody_group,
            acting_user=None,
        )
        with self.settings(TOPIC_SUMMARIZATION_MODEL="test-model"):
            response = self.client_post(url, {})
        self.assert_json_error(response, "Insufficient permission")

        everyone_group = NamedUserGroup.objects.get(
            name=SystemGroups.EVERYONE,
            realm_for_sharding=self.user.realm,
            is_system_group=True,
        )
        do_change_realm_permission_group_setting(
            self.user.realm,
            "can_summarize_topics_group",
            everyone_group,
            acting_user=None,
        )
        with self.settings(TOPIC_SUMMARIZATION_MODEL="test-model", MAX_PER_USER_MONTHLY_AI_COST=0):
            response = self.client_post(url, {})
        self.assert_json_error_contains(response, "Reached monthly limit for AI credits.")

    def test_provider_failure(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        with (
            self.settings(
                TOPIC_SUMMARIZATION_MODEL="test-model",
                TOPIC_SUMMARIZATION_API_KEY="test",
            ),
            mock.patch.object(Completions, "create", side_effect=RuntimeError("LLM failed")),
            self.assertRaises(RuntimeError, msg="LLM failed"),
        ):
            self.client_post(f"/json/messages/{message_id}/topic_title_suggestion", {})

    def test_usage_accounting(self) -> None:
        message_id = self.send_topic_messages(6)[-1]
        with self.settings(INPUT_COST_PER_GIGATOKEN=3, OUTPUT_COST_PER_GIGATOKEN=2):
            result, _create = self.post_suggestion(
                message_id, '{"drift_detected":false,"suggested_title":null}'
            )
        self.assert_no_suggestion(result)
        expected_credits = 10 * 3 + 5 * 2
        self.assertTrue(
            UserCount.objects.filter(
                property="ai_credit_usage::day",
                value=expected_credits,
                user_id=self.user.id,
            ).exists()
        )
