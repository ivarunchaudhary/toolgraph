"""Build the tool catalog + taxonomy from one compact, reviewable spec.

Outputs:
  data/taxonomy.json            DataTypes + Capabilities (+ which types users can supply)
  data/catalog/<server>.json    MCP-style tool definitions with consumes/produces/capabilities

Annotations (consumes/produces/capabilities) were authored with Claude against a fixed
taxonomy and reviewed by hand; they are the ground truth the simulator executes against.
Untyped string params (text, title, query, ...) are free text the agent can always supply.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# DataTypes: name -> (param_name, description, user_suppliable)
# user_suppliable = the value can appear in a user's request (repo names, emails, ...)
# ---------------------------------------------------------------------------
TYPES: dict[str, tuple[str, str, bool]] = {
    # values a user can mention directly
    "repo_path": ("repo", "Repository path such as owner/name, valid on GitHub or GitLab", True),
    "email_address": ("email", "An email address of a person", True),
    "person_name": ("name", "A person's display name", True),
    "service_name": ("service", "Name of a production service, e.g. payments-api", True),
    "channel_name": ("channel_name", "Human-readable chat channel name such as #incidents", True),
    "team_name": ("team_name", "Name of an engineering or product team", True),
    "project_key": ("project_key", "Jira project key such as PAY", True),
    "local_path": ("path", "A path on the local filesystem", True),
    "company_name": ("company", "Name of a customer company", True),
    "table_name": ("table", "Name of a Postgres table", True),
    "date_range": ("date_range", "A date or time range, e.g. next Tuesday or last 7 days", True),
    # GitHub
    "gh_pr_ref": ("pull_number", "GitHub pull request number within a repository", False),
    "gh_issue_ref": ("issue_number", "GitHub issue number within a repository", False),
    "gh_branch": ("branch", "Name of a branch that exists on GitHub", False),
    "gh_run_ref": ("run_id", "GitHub Actions workflow run id", False),
    "gh_user_login": ("username", "GitHub user login", False),
    "gh_commit_sha": ("sha", "Git commit SHA on GitHub", False),
    "gh_pr_created": ("", "A GitHub pull request was opened", False),
    "gh_pr_merged": ("", "A GitHub pull request was merged", False),
    "gh_review_requested": ("", "A GitHub review was requested", False),
    "gh_issue_created": ("", "A GitHub issue was created", False),
    "gh_comment_posted": ("", "A comment was posted on GitHub", False),
    "gh_release_created": ("", "A GitHub release was published", False),
    # GitLab
    "gl_mr_ref": ("merge_request_iid", "GitLab merge request IID", False),
    "gl_issue_ref": ("issue_iid", "GitLab issue IID", False),
    "gl_branch": ("branch", "Name of a branch that exists on GitLab", False),
    "gl_pipeline_ref": ("pipeline_id", "GitLab CI pipeline id", False),
    "gl_username": ("username", "GitLab username", False),
    "gl_mr_created": ("", "A GitLab merge request was opened", False),
    "gl_mr_merged": ("", "A GitLab merge request was merged", False),
    "gl_review_requested": ("", "A GitLab reviewer was assigned", False),
    "gl_issue_created": ("", "A GitLab issue was created", False),
    "gl_comment_posted": ("", "A note was posted on GitLab", False),
    # shared content
    "code_diff": ("diff", "Unified diff of a code change", False),
    "ci_log": ("log", "Log output from a CI job", False),
    "file_text": ("content", "Text content of a file", False),
    "chat_transcript": ("transcript", "Recent messages from a chat channel or thread", False),
    "doc_text": ("doc_text", "Body text of a document or page", False),
    "email_body": ("email_body", "Body text of an email", False),
    "ticket_details": ("ticket", "Title, description and status of a ticket", False),
    "incident_summary": ("incident", "Summary and timeline of an incident", False),
    "query_rows": ("rows", "Rows returned by a database query", False),
    "invoice_details": ("invoice", "Line items and status of an invoice", False),
    # Slack
    "slack_channel_id": ("channel", "Slack channel id like C0123", False),
    "slack_user_id": ("user", "Slack user id like U0123", False),
    "slack_message_ref": ("ts", "Slack message timestamp reference", False),
    "slack_message_sent": ("", "A message was posted in a Slack channel or thread", False),
    "slack_dm_sent": ("", "A Slack direct message was sent", False),
    "slack_reaction_added": ("", "A reaction was added on Slack", False),
    "slack_topic_set": ("", "A Slack channel topic was updated", False),
    # Discord
    "discord_channel_id": ("channel_id", "Discord channel snowflake id", False),
    "discord_user_id": ("user_id", "Discord user snowflake id", False),
    "discord_message_ref": ("message_id", "Discord message id", False),
    "discord_message_sent": ("", "A message was posted in a Discord channel", False),
    "discord_dm_sent": ("", "A Discord direct message was sent", False),
    "discord_thread_created": ("", "A Discord thread was started", False),
    "discord_reaction_added": ("", "A reaction was added on Discord", False),
    # Linear
    "linear_team_id": ("team_id", "Linear team id", False),
    "linear_issue_ref": ("issue_id", "Linear issue identifier like ENG-123", False),
    "linear_user_id": ("assignee_id", "Linear user id", False),
    "linear_issue_created": ("", "A Linear issue was created", False),
    "linear_issue_updated": ("", "A Linear issue's state was changed", False),
    "linear_issue_assigned": ("", "A Linear issue was assigned", False),
    "linear_comment_added": ("", "A comment was added to a Linear issue", False),
    # Jira
    "jira_project_id": ("project_id", "Jira project id", False),
    "jira_issue_key": ("issue_key", "Jira issue key like PAY-42", False),
    "jira_account_id": ("account_id", "Jira user account id", False),
    "jira_sprint_id": ("sprint_id", "Jira sprint id", False),
    "jira_issue_created": ("", "A Jira issue was created", False),
    "jira_issue_updated": ("", "A Jira issue was transitioned", False),
    "jira_issue_assigned": ("", "A Jira issue was assigned", False),
    "jira_comment_added": ("", "A comment was added to a Jira issue", False),
    # Notion
    "notion_page_id": ("page_id", "Notion page id", False),
    "notion_db_id": ("database_id", "Notion database id", False),
    "notion_page_created": ("", "A Notion page was created", False),
    "notion_page_updated": ("", "Content was appended to a Notion page", False),
    "notion_comment_added": ("", "A comment was added to a Notion page", False),
    # Google Drive
    "drive_file_id": ("file_id", "Google Drive file id", False),
    "drive_folder_id": ("folder_id", "Google Drive folder id", False),
    "drive_doc_created": ("", "A Google Doc was created", False),
    "drive_file_shared": ("", "A Drive file was shared with someone", False),
    "drive_file_uploaded": ("", "A file was uploaded to Drive", False),
    # Gmail
    "gmail_message_id": ("message_id", "Gmail message id", False),
    "gmail_draft_id": ("draft_id", "Gmail draft id", False),
    "email_sent": ("", "An email was sent", False),
    "email_reply_sent": ("", "A reply was sent on an email thread", False),
    "gmail_labeled": ("", "A label was applied to an email", False),
    # Calendar
    "gcal_event_id": ("event_id", "Google Calendar event id", False),
    "time_slot": ("slot", "A concrete free time slot with start and end", False),
    "event_created": ("", "A calendar event was created", False),
    "event_updated": ("", "A calendar event was changed", False),
    "event_cancelled": ("", "A calendar event was cancelled", False),
    "attendee_added": ("", "An attendee was added to a calendar event", False),
    # PagerDuty
    "pd_service_id": ("service_id", "PagerDuty service id", False),
    "pd_user_id": ("user_id", "PagerDuty user id", False),
    "pd_incident_id": ("incident_id", "PagerDuty incident id", False),
    "incident_acknowledged": ("", "A PagerDuty incident was acknowledged", False),
    "incident_resolved": ("", "A PagerDuty incident was resolved", False),
    "incident_created": ("", "A PagerDuty incident was triggered", False),
    "incident_note_added": ("", "A note was added to a PagerDuty incident", False),
    # Postgres
    "table_schema": ("schema", "Column names and types of a table", False),
    "db_rows_updated": ("", "Rows were written to the database", False),
    "table_stats": ("stats", "Row count and size of a table", False),
    # Stripe
    "stripe_customer_id": ("customer", "Stripe customer id cus_...", False),
    "stripe_invoice_id": ("invoice", "Stripe invoice id in_...", False),
    "stripe_charge_id": ("charge", "Stripe charge id ch_...", False),
    "stripe_subscription_id": ("subscription", "Stripe subscription id sub_...", False),
    "refund_issued": ("", "A Stripe refund was issued", False),
    "subscription_cancelled": ("", "A Stripe subscription was cancelled", False),
    "invoice_sent": ("", "A Stripe invoice was emailed", False),
    # HubSpot
    "hubspot_contact_id": ("contact_id", "HubSpot contact id", False),
    "hubspot_company_id": ("company_id", "HubSpot company id", False),
    "hubspot_deal_id": ("deal_id", "HubSpot deal id", False),
    "crm_note_added": ("", "A note was logged on a HubSpot record", False),
    "deal_updated": ("", "A HubSpot deal stage was changed", False),
    "contact_created": ("", "A HubSpot contact was created", False),
    # Filesystem
    "local_file_written": ("", "A file was written on the local filesystem", False),
}

# ---------------------------------------------------------------------------
# Capabilities: server-agnostic verbs used to seed retrieval
# ---------------------------------------------------------------------------
CAPS: dict[str, str] = {
    "code.repo.search": "find or search code repositories",
    "code.pr.list": "list or find open pull requests or merge requests",
    "code.pr.read": "read a pull request or merge request diff and changes",
    "code.pr.create": "open a new pull request or merge request",
    "code.pr.merge": "merge a pull request or merge request",
    "code.review.request": "ask someone to review a pull request or merge request",
    "code.comment": "comment on a pull request, merge request or code issue",
    "code.branch.create": "create a git branch",
    "code.file.read": "read a file from a repository",
    "code.commit.list": "list recent commits",
    "code.release.create": "publish a release or tag a version",
    "ci.runs.list": "list CI builds, workflow runs or pipelines",
    "ci.logs.read": "read logs of a failing CI build or job",
    "issue.list": "list or search bugs, issues or tickets",
    "issue.create": "create or file a bug report, issue or ticket",
    "issue.read": "read the details of an issue or ticket",
    "issue.update": "change the status of a ticket, move or close an issue",
    "issue.assign": "assign a ticket or issue to a person",
    "issue.comment": "add a comment on a ticket or issue",
    "sprint.read": "see what is in the current sprint or cycle",
    "team.lookup": "look up a team or project",
    "chat.channel.find": "find a chat channel",
    "chat.message.post": "post or send a message to a chat channel or team",
    "chat.dm.send": "send a direct message to a person in chat",
    "chat.history.read": "read recent chat messages or a discussion thread",
    "chat.thread.reply": "reply in a chat thread",
    "chat.reaction.add": "react to a chat message with an emoji",
    "chat.topic.set": "update a chat channel topic",
    "user.lookup": "find a person's account or user id",
    "user.contact.get": "get a person's email or contact details",
    "oncall.lookup": "find who is on call for a service",
    "incident.list": "list open incidents or alerts",
    "incident.read": "read details of an incident",
    "incident.ack": "acknowledge an incident or page",
    "incident.resolve": "resolve or close an incident",
    "incident.create": "trigger or open a new incident or page someone",
    "incident.note": "add a note to an incident",
    "doc.search": "search documents, pages or wiki",
    "doc.read": "read a document, page or spec",
    "doc.create": "write or create a new document or page",
    "doc.update": "append to or edit a document or page",
    "doc.share": "share a document with someone",
    "doc.comment": "comment on a document or page",
    "file.upload": "upload a file to cloud storage",
    "email.search": "search an inbox for emails",
    "email.read": "read an email",
    "email.send": "send or reply to an email",
    "email.draft": "draft an email",
    "email.label": "label or tag an email",
    "calendar.events.list": "list calendar events or meetings",
    "calendar.event.create": "schedule a meeting or create a calendar event",
    "calendar.event.update": "reschedule or change a meeting, add attendees",
    "calendar.event.cancel": "cancel a meeting",
    "calendar.availability": "find a free time slot for people",
    "db.schema.read": "inspect database tables and schemas",
    "db.query.read": "run a read-only SQL query and get data",
    "db.query.write": "update or insert rows in a database",
    "payments.customer.find": "find a billing customer",
    "payments.invoice.read": "look up invoices and billing history",
    "payments.invoice.send": "send an invoice",
    "payments.refund": "refund a payment or charge",
    "payments.subscription.manage": "cancel or change a subscription",
    "crm.contact.find": "find a contact or lead in the CRM",
    "crm.contact.create": "add a new contact to the CRM",
    "crm.company.find": "find a customer company account in the CRM",
    "crm.deal.read": "see deals or opportunities for an account",
    "crm.deal.update": "move a deal to a new stage",
    "crm.note.add": "log a note or activity on a CRM record",
    "file.read": "read a local file",
    "file.write": "save or write a local file",
    "file.search": "find files on the local disk",
}

SERVERS: dict[str, str] = {
    "github": "GitHub: repositories, pull requests, issues and Actions",
    "gitlab": "GitLab: projects, merge requests, issues and pipelines",
    "slack": "Slack: channels, messages and direct messages",
    "discord": "Discord: server channels, messages and members",
    "linear": "Linear: issue tracking for product teams",
    "jira": "Jira: issue tracking, projects and sprints",
    "notion": "Notion: pages, wikis and databases",
    "google_drive": "Google Drive: files, folders and Google Docs",
    "gmail": "Gmail: email inbox, drafts and sending",
    "google_calendar": "Google Calendar: events and availability",
    "pagerduty": "PagerDuty: on-call schedules and incidents",
    "postgres": "Postgres: database schemas and SQL queries",
    "stripe": "Stripe: customers, invoices, charges and subscriptions",
    "hubspot": "HubSpot: CRM contacts, companies and deals",
    "filesystem": "Local filesystem: read, write and search files",
}

TOOLS: list[tuple] = []


def T(server, name, desc, consumes, produces, caps, free=None):
    TOOLS.append((server, name, desc, consumes, produces, caps, free or {}))


# ---------------- GitHub ----------------
T("github", "search_repositories", "Search GitHub repositories by keyword.", [], ["repo_path"], ["code.repo.search"], {"query": "Search keywords"})
T("github", "list_pull_requests", "List pull requests in a GitHub repository, newest first, with CI status.", ["repo_path"], ["gh_pr_ref"], ["code.pr.list"], {"state": "open, closed or all"})
T("github", "get_pull_request_diff", "Get the unified diff of a GitHub pull request.", ["repo_path", "gh_pr_ref"], ["code_diff"], ["code.pr.read"])
T("github", "get_pull_request", "Get title, head branch and status of a GitHub pull request.", ["repo_path", "gh_pr_ref"], ["gh_branch", "gh_commit_sha"], ["code.pr.read"])
T("github", "create_pull_request", "Open a pull request from a head branch into a base branch.", ["repo_path", "gh_branch"], ["gh_pr_ref", "gh_pr_created"], ["code.pr.create"], {"title": "PR title", "base": "Base branch"})
T("github", "merge_pull_request", "Merge a GitHub pull request.", ["repo_path", "gh_pr_ref"], ["gh_pr_merged"], ["code.pr.merge"], {"method": "merge, squash or rebase"})
T("github", "request_reviewers", "Request a review on a GitHub pull request from a user.", ["repo_path", "gh_pr_ref", "gh_user_login"], ["gh_review_requested"], ["code.review.request"])
T("github", "add_pull_request_comment", "Comment on a GitHub pull request.", ["repo_path", "gh_pr_ref"], ["gh_comment_posted"], ["code.comment"], {"body": "Comment text"})
T("github", "list_issues", "List issues in a GitHub repository.", ["repo_path"], ["gh_issue_ref"], ["issue.list"], {"labels": "Comma-separated labels"})
T("github", "get_issue", "Read a GitHub issue's title, body and labels.", ["repo_path", "gh_issue_ref"], ["ticket_details"], ["issue.read"])
T("github", "create_issue", "Create an issue in a GitHub repository.", ["repo_path"], ["gh_issue_created"], ["issue.create"], {"title": "Issue title", "body": "Issue body"})
T("github", "add_issue_comment", "Comment on a GitHub issue.", ["repo_path", "gh_issue_ref"], ["gh_comment_posted"], ["issue.comment", "code.comment"], {"body": "Comment text"})
T("github", "list_workflow_runs", "List GitHub Actions workflow runs for a repository, newest first.", ["repo_path"], ["gh_run_ref"], ["ci.runs.list"], {"status": "Filter such as failure"})
T("github", "get_workflow_run_logs", "Download logs of a GitHub Actions workflow run.", ["repo_path", "gh_run_ref"], ["ci_log"], ["ci.logs.read"])
T("github", "create_branch", "Create a new branch in a GitHub repository.", ["repo_path"], ["gh_branch"], ["code.branch.create"], {"branch_name": "New branch name"})
T("github", "get_file_contents", "Read a file from a GitHub repository.", ["repo_path"], ["file_text"], ["code.file.read"], {"file_path": "Path inside the repo"})
T("github", "list_commits", "List recent commits on a GitHub repository branch.", ["repo_path"], ["gh_commit_sha"], ["code.commit.list"])
T("github", "search_users", "Find a GitHub user login by display name.", ["person_name"], ["gh_user_login"], ["user.lookup"])
T("github", "get_user", "Get a GitHub user's public profile including email.", ["gh_user_login"], ["email_address"], ["user.contact.get"])
T("github", "create_release", "Publish a GitHub release from a commit.", ["repo_path", "gh_commit_sha"], ["gh_release_created"], ["code.release.create"], {"tag": "Version tag"})

# ---------------- GitLab ----------------
T("gitlab", "search_projects", "Search GitLab projects by keyword.", [], ["repo_path"], ["code.repo.search"], {"query": "Search keywords"})
T("gitlab", "list_merge_requests", "List merge requests in a GitLab project.", ["repo_path"], ["gl_mr_ref"], ["code.pr.list"], {"state": "opened, merged or all"})
T("gitlab", "get_merge_request_changes", "Get the diff of a GitLab merge request.", ["repo_path", "gl_mr_ref"], ["code_diff"], ["code.pr.read"])
T("gitlab", "create_merge_request", "Open a GitLab merge request from a source branch.", ["repo_path", "gl_branch"], ["gl_mr_ref", "gl_mr_created"], ["code.pr.create"], {"title": "MR title"})
T("gitlab", "accept_merge_request", "Merge a GitLab merge request.", ["repo_path", "gl_mr_ref"], ["gl_mr_merged"], ["code.pr.merge"])
T("gitlab", "create_branch", "Create a branch in a GitLab project.", ["repo_path"], ["gl_branch"], ["code.branch.create"], {"branch_name": "New branch name"})
T("gitlab", "list_issues", "List issues in a GitLab project.", ["repo_path"], ["gl_issue_ref"], ["issue.list"])
T("gitlab", "create_issue", "Create an issue in a GitLab project.", ["repo_path"], ["gl_issue_created"], ["issue.create"], {"title": "Issue title"})
T("gitlab", "create_merge_request_note", "Add a note to a GitLab merge request.", ["repo_path", "gl_mr_ref"], ["gl_comment_posted"], ["code.comment"], {"body": "Note text"})
T("gitlab", "list_pipelines", "List CI pipelines for a GitLab project.", ["repo_path"], ["gl_pipeline_ref"], ["ci.runs.list"], {"status": "Filter such as failed"})
T("gitlab", "get_pipeline_job_log", "Read the job log of a GitLab pipeline.", ["repo_path", "gl_pipeline_ref"], ["ci_log"], ["ci.logs.read"])
T("gitlab", "find_user", "Find a GitLab username by display name.", ["person_name"], ["gl_username"], ["user.lookup"])
T("gitlab", "assign_reviewer", "Assign a reviewer to a GitLab merge request.", ["repo_path", "gl_mr_ref", "gl_username"], ["gl_review_requested"], ["code.review.request"])
T("gitlab", "get_file", "Read a file from a GitLab project.", ["repo_path"], ["file_text"], ["code.file.read"], {"file_path": "Path inside the repo"})

# ---------------- Slack ----------------
T("slack", "find_channel", "Find a Slack channel id by its name.", ["channel_name"], ["slack_channel_id"], ["chat.channel.find"])
T("slack", "post_message", "Post a message to a Slack channel.", ["slack_channel_id"], ["slack_message_ref", "slack_message_sent"], ["chat.message.post"], {"text": "Message text"})
T("slack", "send_dm", "Send a direct message to a Slack user.", ["slack_user_id"], ["slack_dm_sent"], ["chat.dm.send"], {"text": "Message text"})
T("slack", "lookup_user_by_email", "Find a Slack user id from an email address.", ["email_address"], ["slack_user_id"], ["user.lookup"])
T("slack", "search_users", "Find a Slack user id by display name.", ["person_name"], ["slack_user_id"], ["user.lookup"])
T("slack", "get_user_profile", "Get a Slack user's profile including email.", ["slack_user_id"], ["email_address"], ["user.contact.get"])
T("slack", "get_channel_history", "Read recent messages in a Slack channel.", ["slack_channel_id"], ["chat_transcript", "slack_message_ref"], ["chat.history.read"], {"limit": "Max messages"})
T("slack", "get_thread_replies", "Read replies in a Slack thread.", ["slack_channel_id", "slack_message_ref"], ["chat_transcript"], ["chat.history.read"])
T("slack", "reply_in_thread", "Reply in a Slack thread.", ["slack_channel_id", "slack_message_ref"], ["slack_message_sent"], ["chat.thread.reply"], {"text": "Reply text"})
T("slack", "add_reaction", "Add an emoji reaction to a Slack message.", ["slack_channel_id", "slack_message_ref"], ["slack_reaction_added"], ["chat.reaction.add"], {"emoji": "Emoji name"})
T("slack", "list_channel_members", "List members of a Slack channel.", ["slack_channel_id"], ["slack_user_id"], ["user.lookup"])
T("slack", "set_channel_topic", "Set the topic of a Slack channel.", ["slack_channel_id"], ["slack_topic_set"], ["chat.topic.set"], {"topic": "New topic"})

# ---------------- Discord ----------------
T("discord", "find_channel", "Find a Discord channel id by name.", ["channel_name"], ["discord_channel_id"], ["chat.channel.find"])
T("discord", "send_message", "Send a message to a Discord channel.", ["discord_channel_id"], ["discord_message_ref", "discord_message_sent"], ["chat.message.post"], {"content": "Message text"})
T("discord", "send_dm", "Send a direct message to a Discord member.", ["discord_user_id"], ["discord_dm_sent"], ["chat.dm.send"], {"content": "Message text"})
T("discord", "search_members", "Find a Discord member id by name.", ["person_name"], ["discord_user_id"], ["user.lookup"])
T("discord", "read_messages", "Read recent messages in a Discord channel.", ["discord_channel_id"], ["chat_transcript", "discord_message_ref"], ["chat.history.read"])
T("discord", "create_thread", "Start a Discord thread from a message.", ["discord_channel_id", "discord_message_ref"], ["discord_thread_created"], ["chat.thread.reply"], {"name": "Thread name"})
T("discord", "add_reaction", "React to a Discord message with an emoji.", ["discord_channel_id", "discord_message_ref"], ["discord_reaction_added"], ["chat.reaction.add"], {"emoji": "Emoji"})

# ---------------- Linear ----------------
T("linear", "find_team", "Find a Linear team id by team name.", ["team_name"], ["linear_team_id"], ["team.lookup"])
T("linear", "create_issue", "Create a Linear issue in a team.", ["linear_team_id"], ["linear_issue_created"], ["issue.create"], {"title": "Issue title", "description": "Details"})
T("linear", "search_issues", "Search Linear issues by text.", [], ["linear_issue_ref"], ["issue.list"], {"query": "Search text"})
T("linear", "get_issue", "Read a Linear issue's details and status.", ["linear_issue_ref"], ["ticket_details"], ["issue.read"])
T("linear", "update_issue_state", "Move a Linear issue to a new workflow state.", ["linear_issue_ref"], ["linear_issue_updated"], ["issue.update"], {"state": "Target state"})
T("linear", "find_user", "Find a Linear user id by email.", ["email_address"], ["linear_user_id"], ["user.lookup"])
T("linear", "assign_issue", "Assign a Linear issue to a user.", ["linear_issue_ref", "linear_user_id"], ["linear_issue_assigned"], ["issue.assign"])
T("linear", "add_comment", "Comment on a Linear issue.", ["linear_issue_ref"], ["linear_comment_added"], ["issue.comment"], {"body": "Comment text"})
T("linear", "list_cycle_issues", "List issues in a Linear team's current cycle.", ["linear_team_id"], ["linear_issue_ref"], ["sprint.read", "issue.list"])

# ---------------- Jira ----------------
T("jira", "get_project", "Get a Jira project id from its key.", ["project_key"], ["jira_project_id"], ["team.lookup"])
T("jira", "create_issue", "Create a Jira issue in a project.", ["jira_project_id"], ["jira_issue_created"], ["issue.create"], {"summary": "Summary", "issue_type": "Bug, Task or Story"})
T("jira", "search_issues", "Search Jira issues with JQL.", [], ["jira_issue_key"], ["issue.list"], {"jql": "JQL query"})
T("jira", "get_issue", "Read a Jira issue's fields and status.", ["jira_issue_key"], ["ticket_details"], ["issue.read"])
T("jira", "transition_issue", "Transition a Jira issue to a new status.", ["jira_issue_key"], ["jira_issue_updated"], ["issue.update"], {"status": "Target status"})
T("jira", "find_user", "Find a Jira account id by email.", ["email_address"], ["jira_account_id"], ["user.lookup"])
T("jira", "assign_issue", "Assign a Jira issue to an account.", ["jira_issue_key", "jira_account_id"], ["jira_issue_assigned"], ["issue.assign"])
T("jira", "add_comment", "Comment on a Jira issue.", ["jira_issue_key"], ["jira_comment_added"], ["issue.comment"], {"body": "Comment text"})
T("jira", "get_active_sprint", "Get the active sprint of a Jira project.", ["jira_project_id"], ["jira_sprint_id"], ["sprint.read"])
T("jira", "list_sprint_issues", "List issues in a Jira sprint.", ["jira_sprint_id"], ["jira_issue_key"], ["sprint.read", "issue.list"])

# ---------------- Notion ----------------
T("notion", "search", "Search Notion pages and databases by title.", [], ["notion_page_id"], ["doc.search"], {"query": "Search text"})
T("notion", "get_page_content", "Read the blocks of a Notion page.", ["notion_page_id"], ["doc_text"], ["doc.read"])
T("notion", "create_page", "Create a Notion page under a parent page.", ["notion_page_id"], ["notion_page_created", "notion_page_id"], ["doc.create"], {"title": "Page title", "content": "Markdown body"})
T("notion", "append_blocks", "Append content to a Notion page.", ["notion_page_id"], ["notion_page_updated"], ["doc.update"], {"content": "Markdown to append"})
T("notion", "find_database", "Find a Notion database by name.", [], ["notion_db_id"], ["doc.search"], {"query": "Database name"})
T("notion", "query_database", "Query rows of a Notion database.", ["notion_db_id"], ["notion_page_id"], ["doc.search"], {"filter": "Filter expression"})
T("notion", "add_comment", "Comment on a Notion page.", ["notion_page_id"], ["notion_comment_added"], ["doc.comment"], {"text": "Comment"})

# ---------------- Google Drive ----------------
T("google_drive", "search_files", "Search Google Drive files by name or content.", [], ["drive_file_id"], ["doc.search"], {"query": "Search text"})
T("google_drive", "read_file", "Export a Drive file's text content.", ["drive_file_id"], ["doc_text"], ["doc.read"])
T("google_drive", "find_folder", "Find a Drive folder id by name.", [], ["drive_folder_id"], ["doc.search"], {"name": "Folder name"})
T("google_drive", "create_document", "Create a Google Doc in a folder.", ["drive_folder_id"], ["drive_file_id", "drive_doc_created"], ["doc.create"], {"title": "Doc title", "content": "Body"})
T("google_drive", "share_file", "Share a Drive file with an email address.", ["drive_file_id", "email_address"], ["drive_file_shared"], ["doc.share"], {"role": "reader or writer"})
T("google_drive", "upload_file", "Upload a local file into a Drive folder.", ["local_path", "drive_folder_id"], ["drive_file_id", "drive_file_uploaded"], ["file.upload"])
T("google_drive", "list_folder", "List files in a Drive folder.", ["drive_folder_id"], ["drive_file_id"], ["doc.search"])

# ---------------- Gmail ----------------
T("gmail", "search_messages", "Search the Gmail inbox.", [], ["gmail_message_id"], ["email.search"], {"query": "Gmail search query"})
T("gmail", "read_message", "Read an email's body and sender.", ["gmail_message_id"], ["email_body", "email_address"], ["email.read"])
T("gmail", "send_email", "Send an email.", ["email_address"], ["email_sent"], ["email.send"], {"subject": "Subject", "body": "Body"})
T("gmail", "reply_to_message", "Reply to an email thread.", ["gmail_message_id"], ["email_reply_sent"], ["email.send"], {"body": "Reply body"})
T("gmail", "create_draft", "Create an email draft.", ["email_address"], ["gmail_draft_id"], ["email.draft"], {"subject": "Subject", "body": "Body"})
T("gmail", "send_draft", "Send an existing Gmail draft.", ["gmail_draft_id"], ["email_sent"], ["email.send"])
T("gmail", "add_label", "Apply a label to an email.", ["gmail_message_id"], ["gmail_labeled"], ["email.label"], {"label": "Label name"})

# ---------------- Google Calendar ----------------
T("google_calendar", "list_events", "List calendar events in a date range.", ["date_range"], ["gcal_event_id"], ["calendar.events.list"])
T("google_calendar", "get_event", "Get an event's time and attendee emails.", ["gcal_event_id"], ["email_address"], ["calendar.events.list"])
T("google_calendar", "find_free_slot", "Find a time slot when attendees are free.", ["email_address", "date_range"], ["time_slot"], ["calendar.availability"], {"duration_minutes": "Meeting length"})
T("google_calendar", "create_event", "Create a calendar event and invite attendees.", ["time_slot", "email_address"], ["gcal_event_id", "event_created"], ["calendar.event.create"], {"title": "Event title"})
T("google_calendar", "update_event", "Move an event to a new time slot.", ["gcal_event_id", "time_slot"], ["event_updated"], ["calendar.event.update"])
T("google_calendar", "add_attendee", "Add an attendee to an event.", ["gcal_event_id", "email_address"], ["attendee_added"], ["calendar.event.update"])
T("google_calendar", "delete_event", "Cancel a calendar event.", ["gcal_event_id"], ["event_cancelled"], ["calendar.event.cancel"])

# ---------------- PagerDuty ----------------
T("pagerduty", "get_service", "Get a PagerDuty service id by service name.", ["service_name"], ["pd_service_id"], ["incident.list", "oncall.lookup"])
T("pagerduty", "list_oncalls", "List who is currently on call for a service.", ["pd_service_id"], ["pd_user_id"], ["oncall.lookup"])
T("pagerduty", "get_user", "Get a PagerDuty user's name and email.", ["pd_user_id"], ["email_address"], ["user.contact.get"])
T("pagerduty", "list_incidents", "List open incidents for a service.", ["pd_service_id"], ["pd_incident_id"], ["incident.list"])
T("pagerduty", "get_incident", "Read an incident's summary and timeline.", ["pd_incident_id"], ["incident_summary"], ["incident.read"])
T("pagerduty", "acknowledge_incident", "Acknowledge a PagerDuty incident.", ["pd_incident_id"], ["incident_acknowledged"], ["incident.ack"])
T("pagerduty", "resolve_incident", "Resolve a PagerDuty incident.", ["pd_incident_id"], ["incident_resolved"], ["incident.resolve"])
T("pagerduty", "create_incident", "Trigger a new incident on a service.", ["pd_service_id"], ["incident_created"], ["incident.create"], {"title": "Incident title"})
T("pagerduty", "add_incident_note", "Add a note to an incident.", ["pd_incident_id"], ["incident_note_added"], ["incident.note"], {"note": "Note text"})

# ---------------- Postgres ----------------
T("postgres", "list_tables", "List tables in the database.", [], ["table_name"], ["db.schema.read"])
T("postgres", "describe_table", "Get columns and types of a table.", ["table_name"], ["table_schema"], ["db.schema.read"])
T("postgres", "run_select", "Run a read-only SQL SELECT written against a known schema.", ["table_schema"], ["query_rows"], ["db.query.read"], {"sql": "SELECT statement"})
T("postgres", "run_update", "Run an INSERT or UPDATE statement against a known schema.", ["table_schema"], ["db_rows_updated"], ["db.query.write"], {"sql": "SQL statement"})
T("postgres", "table_stats", "Get row count and on-disk size of a table.", ["table_name"], ["table_stats"], ["db.schema.read"])

# ---------------- Stripe ----------------
T("stripe", "find_customer", "Find a Stripe customer by email.", ["email_address"], ["stripe_customer_id"], ["payments.customer.find"])
T("stripe", "list_invoices", "List invoices for a Stripe customer.", ["stripe_customer_id"], ["stripe_invoice_id"], ["payments.invoice.read"])
T("stripe", "get_invoice", "Read a Stripe invoice's line items and status.", ["stripe_invoice_id"], ["invoice_details"], ["payments.invoice.read"])
T("stripe", "send_invoice", "Email a Stripe invoice to the customer.", ["stripe_invoice_id"], ["invoice_sent"], ["payments.invoice.send"])
T("stripe", "list_charges", "List charges for a Stripe customer.", ["stripe_customer_id"], ["stripe_charge_id"], ["payments.invoice.read"])
T("stripe", "create_refund", "Refund a Stripe charge.", ["stripe_charge_id"], ["refund_issued"], ["payments.refund"], {"amount": "Amount in cents, optional"})
T("stripe", "list_subscriptions", "List a customer's Stripe subscriptions.", ["stripe_customer_id"], ["stripe_subscription_id"], ["payments.subscription.manage"])
T("stripe", "cancel_subscription", "Cancel a Stripe subscription.", ["stripe_subscription_id"], ["subscription_cancelled"], ["payments.subscription.manage"])

# ---------------- HubSpot ----------------
T("hubspot", "search_contacts", "Search HubSpot contacts by name.", ["person_name"], ["hubspot_contact_id"], ["crm.contact.find"])
T("hubspot", "get_contact", "Read a HubSpot contact including email.", ["hubspot_contact_id"], ["email_address"], ["crm.contact.find", "user.contact.get"])
T("hubspot", "create_contact", "Create a HubSpot contact from an email.", ["email_address"], ["contact_created"], ["crm.contact.create"], {"firstname": "First name"})
T("hubspot", "find_company", "Find a HubSpot company by name.", ["company_name"], ["hubspot_company_id"], ["crm.company.find"])
T("hubspot", "list_company_contacts", "List contacts at a HubSpot company.", ["hubspot_company_id"], ["hubspot_contact_id"], ["crm.contact.find"])
T("hubspot", "list_company_deals", "List deals for a HubSpot company.", ["hubspot_company_id"], ["hubspot_deal_id"], ["crm.deal.read"])
T("hubspot", "update_deal_stage", "Move a HubSpot deal to a new stage.", ["hubspot_deal_id"], ["deal_updated"], ["crm.deal.update"], {"stage": "Target stage"})
T("hubspot", "create_note", "Log a note on a HubSpot contact.", ["hubspot_contact_id"], ["crm_note_added"], ["crm.note.add"], {"body": "Note text"})

# ---------------- Filesystem ----------------
T("filesystem", "read_file", "Read a local text file.", ["local_path"], ["file_text"], ["file.read"])
T("filesystem", "write_file", "Write text to a local file.", ["local_path"], ["local_file_written"], ["file.write"], {"content": "File content"})
T("filesystem", "search_files", "Find local files matching a pattern.", [], ["local_path"], ["file.search"], {"pattern": "Glob pattern"})
T("filesystem", "list_directory", "List entries in a local directory.", ["local_path"], ["local_path"], ["file.search"])


def tool_id(server: str, name: str) -> str:
    return f"tool:{server}.{name}"


def build() -> None:
    by_server: dict[str, list[dict]] = {}
    seen = set()
    for server, name, desc, consumes, produces, caps, free in TOOLS:
        tid = tool_id(server, name)
        assert tid not in seen, tid
        seen.add(tid)
        for t in consumes + produces:
            assert t in TYPES, f"{tid}: unknown type {t}"
        for c in caps:
            assert c in CAPS, f"{tid}: unknown cap {c}"
        assert server in SERVERS
        props, required = {}, []
        for t in consumes:
            pname, pdesc, _ = TYPES[t]
            props[pname] = {"type": "string", "description": pdesc}
            required.append(pname)
        for pname, pdesc in free.items():
            props[pname] = {"type": "string", "description": pdesc}
        by_server.setdefault(server, []).append({
            "id": tid,
            "server": server,
            "name": name,
            "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": required},
            "consumes": [f"type:{t}" for t in consumes],
            "produces": [f"type:{t}" for t in produces],
            "capabilities": [f"cap:{c}" for c in caps],
        })
    cat = DATA / "catalog"
    cat.mkdir(parents=True, exist_ok=True)
    for f in cat.glob("*.json"):
        f.unlink()
    for server, tools in by_server.items():
        (cat / f"{server}.json").write_text(json.dumps(tools, indent=1) + "\n")

    used = {t for tools in by_server.values() for tl in tools for t in tl["consumes"] + tl["produces"]}
    taxonomy = {
        "servers": [{"id": f"server:{k}", "name": k, "description": v} for k, v in SERVERS.items()],
        "datatypes": [
            {"id": f"type:{k}", "name": k, "description": d, "user_suppliable": u}
            for k, (_, d, u) in TYPES.items() if f"type:{k}" in used
        ],
        "capabilities": [{"id": f"cap:{k}", "name": k, "description": v} for k, v in CAPS.items()],
    }
    (DATA / "taxonomy.json").write_text(json.dumps(taxonomy, indent=1) + "\n")

    n_tools = sum(len(v) for v in by_server.values())
    producers: dict[str, int] = {}
    for tools in by_server.values():
        for tl in tools:
            for p in tl["produces"]:
                producers[p] = producers.get(p, 0) + 1
    refs = [k for k in producers if not any(k == f"type:{t}" and TYPES[t][0] == "" for t in TYPES)]
    multi = sum(1 for k in refs if producers[k] > 1)
    print(f"{n_tools} tools / {len(by_server)} servers / {len(taxonomy['datatypes'])} types / {len(CAPS)} capabilities")
    print(f"reference types with >1 producer: {multi}/{len(refs)}")


if __name__ == "__main__":
    build()
