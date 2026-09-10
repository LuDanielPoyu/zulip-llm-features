# Zulip-LLM-Features

**Role:** Full-Stack Engineer — Machine Learning in Production Course Project  

**When:** Sep. 2026 · **Location:** Pittsburgh, PA  

> **Project Context:** Developed as part of Carnegie Mellon University's
> Machine Learning in Production course using the existing Zulip codebase.
> This portfolio highlights my implementation, architecture, and engineering contributions.


---

## Projects at a Glance

| Feature | Problem | What I Built | Result | Stack |
|---|---|---|---|---|
| **F1. Unread Message Recap** | Users with many unread Zulip messages must manually scan conversations to understand what they missed | LLM-powered unread-message summarization workflow integrated into the Zulip Inbox | Users can generate concise recaps and navigate back to relevant source messages | **Python**, **Django**, **TypeScript**, **REST APIs**, **LLM Integration**, **Zulip** |
| **F2. Topic Title Suggestion** | Users may need to manually create concise and descriptive topic titles while composing messages | LLM-assisted topic-title generation integrated into the message compose workflow | Users can generate suggested topic titles directly within the existing Zulip interface | **Python**, **Django**, **TypeScript**, **REST APIs**, **LLM Integration**, **Zulip** |
| **F3. Full-Stack Integration & Testing** | New AI functionality must behave reliably within an established production-scale codebase | Authenticated backend APIs, frontend state handling, error handling, and automated backend/frontend tests | Integrated LLM functionality while preserving existing application behavior and user workflows | **Django**, **TypeScript**, **Automated Testing**, **API Design**, **Git** |


---

## Project Details

### F1 — Unread Message Recap

**Problem:**  
Zulip users can accumulate a large number of unread messages across conversations.
Understanding what was missed requires opening and reading messages individually.

**Solution:**  
Built an **LLM-powered unread-message recap feature** that retrieves unread messages,
sends the relevant content through a backend summarization workflow, and presents
the generated recap inside the Zulip Inbox interface.

**Software/ML-oriented highlights:**
- **Unread-message retrieval:** Integrated with Zulip's existing unread-message workflow to identify messages matching unread state.
- **Backend API:** Developed authenticated **Django endpoints** responsible for retrieving relevant messages, preparing model input, invoking the LLM workflow, and returning structured responses.
- **LLM integration:** Converted conversation content into prompts suitable for summarization while keeping the feature integrated with existing Zulip application logic.
- **Frontend integration:** Built **TypeScript UI interactions** inside the Inbox so users can request and view recaps without leaving the application.
- **Source navigation:** Included links from generated recap content back to the corresponding source messages so users can inspect the original conversation context.
- **UI state management:** Handled **loading, success, empty, and error states** so the feature behaves predictably across different user situations.
- **Testing:** Added backend and frontend test coverage to validate API behavior and interface logic.

**Impact:**
- Added an end-to-end AI-assisted workflow for understanding unread conversations.
- Reduced the need to manually scan every unread message before identifying relevant discussions.
- Demonstrated integration of an LLM feature across backend APIs, frontend interfaces, and an existing production-scale application architecture.

```mermaid
flowchart LR
    U["User opens Zulip Inbox"]
    U --> R["Request unread recap"]

    R --> FE["TypeScript frontend"]
    FE --> API["Authenticated Django API"]

    API --> UM["Retrieve unread messages"]
    UM --> PP["Prepare message context"]

    PP --> LLM["LLM summarization"]
    LLM --> RES["Structured recap response"]

    RES --> FE
    FE --> UI["Display recap in Inbox"]

    UI --> SRC["Source-message links"]
    SRC --> MSG["Original Zulip messages"]

    FE --> STATE["UI state handling"]
    STATE --> LOAD["Loading"]
    STATE --> EMPTY["Empty"]
    STATE --> ERR["Error"]
