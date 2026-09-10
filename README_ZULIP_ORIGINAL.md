# MLiP Individual Assignment 1: Building LLM-Enabled Features

This repository contains my implementation of two LLM-enabled features for Zulip:

1. **Message Recap** — generates a recap of a user's unread messages with references back to the original messages.
2. **Topic Title Improver** — detects when a channel topic has drifted and suggests a more appropriate topic title.

## Development environment

This implementation uses the standard Zulip development environment with Vagrant.

### Requirements

- Git
- Vagrant
- Docker Desktop
- An API key for an OpenAI-compatible LLM provider

No additional project dependencies were added beyond those installed by the normal Zulip provisioning process.

## Setup

Clone this repository and check out the submitted commit.

From the repository root, start/provision the development environment:

```bash
vagrant up
```

## Configure the LLM

The implementation reuses Zulip's topic-summarization LLM configuration. The relevant Django settings are:

- `TOPIC_SUMMARIZATION_MODEL`
- `TOPIC_SUMMARIZATION_API_KEY`
- `TOPIC_SUMMARIZATION_API_BASE`
- `TOPIC_SUMMARIZATION_PARAMETERS`

For development, create the Git-ignored file:

```text
zproject/custom_dev_settings.py
```

For an OpenAI API key, an example configuration is:

```python
TOPIC_SUMMARIZATION_MODEL = "gpt-4o-mini"
TOPIC_SUMMARIZATION_API_KEY = "YOUR_API_KEY"
TOPIC_SUMMARIZATION_API_BASE = None
TOPIC_SUMMARIZATION_PARAMETERS = {}

# Keep the development quota high enough to exercise the two features.
MAX_PER_USER_MONTHLY_AI_COST = 10
```

If using another OpenAI-compatible provider, replace the model name and set
`TOPIC_SUMMARIZATION_API_BASE` to that provider's OpenAI-compatible API base URL.

**Do not commit an actual API key.** `zproject/custom_dev_settings.py` is ignored by Git in the Zulip development repository.

## Run the application

Start the development server:

```bash
vagrant ssh -c 'tools/run-dev'
```

Then open the Zulip development site in a browser, normally at:

```text
http://zulipdev.com:9991
```

`http://localhost:9991` may also be usable depending on the local setup.

Frontend changes may require a browser refresh. If configuration changes are made, restart `tools/run-dev`.

## Feature 1: Message Recap

The Message Recap feature requests a recap of the current user's unread messages. The backend gathers the unread messages that the requesting user is allowed to access, sends them to the configured LLM, and returns a concise recap containing references to the original Zulip messages.

The recap can be requested through the implemented recap UI. References in the result are clickable and narrow Zulip to the corresponding original message.

Backend route:

```text
GET /json/messages/recap
```

## Feature 2: Topic Title Improver

The Topic Title Improver runs after a channel message is successfully sent. It examines a limited recent history of that topic. If the LLM detects sustained topic drift, the UI displays a suggested replacement title.

The user can choose **Use suggested title** to rename the topic. A suggestion is never applied automatically.

Backend route:

```text
POST /json/messages/<message_id>/topic_title_suggestion
```

Direct messages do not trigger topic-title suggestions.

## Tests

Run the backend tests:

```bash
vagrant ssh -c 'tools/test-backend zerver.tests.test_message_summary'
```

Run the focused Topic Title Improver frontend test:

```bash
vagrant ssh -c 'tools/test-js-with-node web/tests/topic_title_suggestion.test.cjs'
```

Run the compose frontend tests:

```bash
vagrant ssh -c 'tools/test-js-with-node web/tests/compose.test.cjs'
```

Check formatting/whitespace before submission:

```bash
git diff --check
```

The main implementation files include:

```text
zerver/actions/message_summary.py
zerver/views/message_summary.py
zerver/tests/test_message_summary.py
zproject/urls.py
web/src/compose.ts
web/src/topic_title_suggestion.ts
web/templates/topic_title_suggestion.hbs
web/tests/compose.test.cjs
web/tests/topic_title_suggestion.test.cjs
```
