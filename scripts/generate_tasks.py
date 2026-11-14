"""Generate the train/test task set.

Each task family is a recurring real-world workflow: a goal (effect types, plus evidence types
the result must be grounded in, e.g. `pd_user_id` = "we actually looked up who is on call"),
the types the user supplies, and several natural phrasings with different entities. Phrasings
were written with Claude and never name tools. A Python solver (catalog.shortest_plan, over the
JSON catalog — not HydraDB) checks each task has a valid chain and records it as `gold_tools`,
which is used ONLY for diagnostic recall.

Split: stratified by family with a fixed seed, so recurring workflows appear in both train
and test (that is what learning can exploit) while test tasks themselves are never trained on.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.catalog import load_catalog, shortest_plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEED = 7

REPOS = ["acme/api", "acme/web", "globex/infra", "initech/billing", "northwind/mobile", "acme/payments", "hooli/search"]
SERVICES = ["payments-api", "checkout", "auth-service", "search-indexer", "notifications", "ledger"]
CHANNELS = ["#incidents", "#eng-alerts", "#release", "#backend", "#support", "#payments-team"]
TEAMS = ["Platform", "Growth", "Mobile", "Billing", "Infra"]
PROJECTS = ["PAY", "OPS", "WEB", "CORE", "MOB"]
EMAILS = ["priya@acme.io", "sam.lee@northwind.com", "jordan@globex.dev", "ana.ruiz@initech.com", "wei.zhang@acme.io", "lena.o@hooli.com"]
PEOPLE = ["Priya Shah", "Sam Lee", "Jordan Kim", "Ana Ruiz", "Wei Zhang", "Lena Otto"]
COMPANIES = ["Northwind", "Globex", "Initech", "Umbrella", "Hooli", "Stark Logistics"]
TABLES = ["orders", "signups", "invoices", "users", "events", "refunds"]
PATHS = ["./reports/weekly.csv", "~/notes/standup.md", "./exports/churn.csv", "~/Desktop/q3-plan.md", "./data/latency.txt"]
DATES = ["next Tuesday afternoon", "this Friday", "tomorrow morning", "sometime next week", "Thursday after lunch"]


def T(*a):
    return [f"type:{x}" for x in a]


# family: (id, given, goal, servers_mentioned, phrasings, n_variants)
# phrasings use {repo} {service} {channel} {team} {project} {email} {person} {company} {table} {path} {date}
FAMILIES = [
    ("oncall_dm_slack", T("service_name"), T("slack_dm_sent", "pd_user_id"), ["pagerduty", "slack"], [
        "Find out who is on call for {service} right now and Slack them a heads-up that latency is spiking.",
        "Ping whoever is on call for {service} with a direct message on Slack asking them to check the dashboard.",
        "Who's carrying the pager for {service}? DM that person on Slack so they know a deploy is going out.",
        "Message the current {service} on-call engineer privately in Slack about the elevated error rate.",
    ], 5),
    ("ci_fail_to_slack", T("repo_path", "channel_name"), T("ci_log", "slack_message_sent"), ["github", "slack"], [
        "The latest GitHub Actions run on {repo} failed. Pull the logs and post the error to {channel} on Slack.",
        "Grab the logs from the most recent failed CI build in {repo} on GitHub and share the stack trace in the Slack channel {channel}.",
        "CI is red on {repo}. Read the failing workflow's log and drop a summary into Slack {channel}.",
        "Post the failure output of the newest GitHub workflow run for {repo} into {channel} in Slack.",
    ], 4),
    ("pipeline_fail_to_discord", T("repo_path", "channel_name"), T("gl_pipeline_ref", "ci_log", "discord_message_sent"), ["gitlab", "discord"], [
        "Our GitLab pipeline for {repo} broke. Fetch the job log and post the error in the Discord channel {channel}.",
        "Read the log of the latest failed GitLab pipeline in {repo} and share it on Discord in {channel}.",
        "Drop the failing GitLab CI output for {repo} into our Discord {channel} channel.",
    ], 3),
    ("linear_issue", T("team_name"), T("linear_issue_created"), ["linear"], [
        "Create a Linear issue for the {team} team: the export button is broken on Safari.",
        "File a ticket in Linear for {team} about flaky login tests.",
        "Open a new Linear issue in the {team} team to track the onboarding email typo.",
        "Log a bug in Linear for team {team}: dark mode resets after refresh.",
    ], 4),
    ("jira_issue", T("project_key"), T("jira_issue_created"), ["jira"], [
        "Create a Jira bug in project {project}: checkout total rounds incorrectly.",
        "Open a Jira task in {project} to rotate the staging API keys.",
        "File a Jira ticket under the {project} project about the broken CSV import.",
        "Add a story to Jira project {project} for adding SSO to the admin panel.",
    ], 4),
    ("gh_issue", T("repo_path"), T("gh_issue_created"), ["github"], [
        "Open a GitHub issue in {repo} saying the README install steps are outdated.",
        "File an issue on GitHub for {repo}: memory leak in the worker pool.",
        "Create a GitHub issue in {repo} to track upgrading Node to v22.",
    ], 3),
    ("gh_review", T("repo_path", "person_name"), T("gh_review_requested"), ["github"], [
        "Ask {person} to review the newest pull request in {repo} on GitHub.",
        "Request a GitHub review from {person} on the latest open PR in {repo}.",
        "Get {person} added as a reviewer on the most recent pull request for {repo}.",
        "Have {person} review the top open GitHub PR in {repo}.",
    ], 5),
    ("gl_review", T("repo_path", "person_name"), T("gl_review_requested"), ["gitlab"], [
        "Assign {person} as reviewer on the newest GitLab merge request in {repo}.",
        "Ask {person} to review the latest MR in {repo} on GitLab.",
        "Put {person} on review duty for the most recent GitLab merge request of {repo}.",
    ], 4),
    ("ack_incident", T("service_name"), T("incident_acknowledged"), ["pagerduty"], [
        "Acknowledge the open PagerDuty incident on {service}.",
        "Ack the current page for {service} so it stops escalating.",
        "There's an alert firing for {service}; acknowledge it in PagerDuty.",
        "Mark the active {service} incident as acknowledged.",
    ], 5),
    ("incident_to_notion", T("service_name"), T("incident_summary", "notion_page_updated"), ["pagerduty", "notion"], [
        "Read the latest incident on {service} and append its timeline to our Notion postmortem page.",
        "Copy the summary of the newest {service} incident into the incident log page in Notion.",
        "Add the details of the most recent {service} outage to the Notion postmortems doc.",
    ], 3),
    ("refund", T("email_address"), T("refund_issued"), ["stripe"], [
        "Refund the most recent charge for the customer {email} in Stripe.",
        "{email} was double-billed. Issue a refund on their latest Stripe charge.",
        "Give {email} their money back for the last payment.",
        "Process a refund for the newest charge on the Stripe customer {email}.",
    ], 5),
    ("cancel_sub", T("email_address"), T("subscription_cancelled"), ["stripe"], [
        "Cancel the Stripe subscription for {email}.",
        "{email} asked to stop their plan; cancel their subscription.",
        "Turn off recurring billing for customer {email}.",
        "End the subscription belonging to {email} in Stripe.",
    ], 5),
    ("resend_invoice", T("email_address"), T("invoice_sent"), ["stripe"], [
        "Resend the latest Stripe invoice to {email}.",
        "{email} never got their invoice; email it to them again.",
        "Send the newest invoice for customer {email} again.",
    ], 4),
    ("schedule_meeting", T("email_address", "date_range"), T("event_created"), ["google_calendar"], [
        "Schedule a 30 minute sync with {email} {date} when we're both free.",
        "Find a free slot {date} and book a meeting with {email}.",
        "Set up a call with {email} {date} on my calendar.",
    ], 3),
    ("send_email", T("email_address"), T("email_sent"), ["gmail"], [
        "Email {email} that the contract is signed.",
        "Send {email} a quick note thanking them for the demo.",
    ], 2),
    ("slack_post", T("channel_name"), T("slack_message_sent"), ["slack"], [
        "Post in Slack {channel} that the release is done.",
        "Let {channel} on Slack know the database maintenance starts at 6pm.",
        "Announce in the Slack channel {channel} that standup is cancelled today.",
    ], 3),
    ("discord_post", T("channel_name"), T("discord_message_sent"), ["discord"], [
        "Post in our Discord {channel} channel that the beta build is live.",
        "Tell the Discord channel {channel} that office hours moved to 4pm.",
    ], 2),
    ("db_read", T("table_name"), T("query_rows"), ["postgres"], [
        "Pull the 20 most recent rows from the {table} table in Postgres.",
        "Query Postgres for yesterday's entries in {table}.",
        "How many rows in {table} have a null email? Run the query.",
    ], 3),
    ("db_export", T("table_name", "local_path"), T("query_rows", "local_file_written"), ["postgres", "filesystem"], [
        "Query the {table} table for last week's records and save them to {path}.",
        "Export this month's {table} rows from Postgres into the local file {path}.",
        "Dump the latest {table} data to {path} on disk.",
    ], 4),
    ("jira_sprint_move", T("project_key"), T("jira_sprint_id", "jira_issue_updated"), ["jira"], [
        "In Jira project {project}, move the first ticket of the active sprint to In Review.",
        "Transition a ticket from {project}'s current sprint to Done in Jira.",
        "Mark the top issue in the active {project} sprint as in progress.",
    ], 3),
    ("jira_assign", T("email_address"), T("jira_issue_assigned"), ["jira"], [
        "Assign the Jira ticket about the login timeout to {email}.",
        "Give the Jira bug on broken password reset to {email}.",
        "Find the Jira issue about slow search and assign it to {email}.",
    ], 4),
    ("linear_assign", T("email_address"), T("linear_issue_assigned"), ["linear"], [
        "Assign the Linear issue about the flaky upload test to {email}.",
        "Find the Linear ticket on the pricing page bug and give it to {email}.",
        "Hand the Linear issue for the iOS crash to {email}.",
    ], 4),
    ("linear_comment", [], T("linear_comment_added"), ["linear"], [
        "Comment on the Linear issue about the onboarding redesign that the mocks are ready.",
        "Add a note to the Linear ticket for API rate limits saying it's blocked on infra.",
    ], 2),
    ("share_doc", T("email_address"), T("drive_file_shared"), ["google_drive"], [
        "Share the Q3 roadmap doc in Google Drive with {email}.",
        "Give {email} access to the pricing proposal in Drive.",
        "Send {email} view access to the onboarding guide on Google Drive.",
    ], 4),
    ("upload_drive", T("local_path"), T("drive_file_uploaded"), ["google_drive"], [
        "Upload {path} to the Finance folder in Google Drive.",
        "Put the local file {path} into our shared Drive folder for reports.",
    ], 2),
    ("crm_note", T("person_name"), T("crm_note_added"), ["hubspot"], [
        "Log a note on {person}'s HubSpot contact that they want a demo next month.",
        "Add a CRM note for {person}: interested in the enterprise tier.",
        "Record in HubSpot that {person} asked about SOC 2.",
    ], 3),
    ("deal_stage", T("company_name"), T("deal_updated"), ["hubspot"], [
        "Move the {company} deal in HubSpot to Closed Won.",
        "Update {company}'s HubSpot deal stage to Contract Sent.",
        "Advance the open deal for {company} to Negotiation.",
        "Set the {company} opportunity to Closed Lost in the CRM.",
    ], 5),
    ("email_company_contact", T("company_name"), T("hubspot_contact_id", "email_sent"), ["hubspot", "gmail"], [
        "Email our main contact at {company} that the renewal quote is ready.",
        "Look up who we know at {company} in HubSpot and email them the onboarding checklist.",
        "Send the account contact at {company} an email about next week's maintenance window.",
    ], 4),
    ("merge_pr", T("repo_path"), T("gh_pr_merged"), ["github"], [
        "Merge the newest open pull request in {repo} on GitHub.",
        "Squash-merge the latest PR in {repo}.",
    ], 2),
    ("open_pr", T("repo_path"), T("gh_pr_created"), ["github"], [
        "Create a new branch in {repo} on GitHub and open a pull request from it.",
        "Start a branch for the hotfix in {repo} and open a GitHub PR.",
    ], 2),
    ("release", T("repo_path"), T("gh_release_created"), ["github"], [
        "Publish a GitHub release for {repo} from the latest commit.",
        "Cut a new release of {repo} on GitHub off the newest commit.",
    ], 2),
    ("diff_to_slack", T("repo_path", "channel_name"), T("code_diff", "slack_message_sent"), ["github", "slack"], [
        "Share the diff of the newest pull request in {repo} with the Slack channel {channel}.",
        "Post the changes from the latest GitHub PR on {repo} to Slack {channel} for review.",
        "Paste the code changes of {repo}'s most recent PR into {channel} on Slack.",
    ], 3),
    ("incident_email", T("service_name", "email_address"), T("incident_summary", "email_sent"), ["pagerduty", "gmail"], [
        "Email {email} a summary of the latest incident on {service}.",
        "Send {email} the timeline of the newest {service} outage by email.",
        "Write to {email} with the details of the current {service} incident.",
    ], 3),
    ("oncall_meeting", T("service_name", "date_range"), T("pd_user_id", "event_created"), ["pagerduty", "google_calendar"], [
        "Book a handoff meeting {date} with whoever is on call for {service}.",
        "Find the {service} on-call engineer and schedule a review with them {date}.",
        "Set up time {date} with the person holding the {service} pager.",
    ], 3),
    ("thread_to_jira", T("channel_name", "project_key"), T("chat_transcript", "jira_issue_created"), ["slack", "jira"], [
        "Turn the recent discussion in Slack {channel} into a Jira ticket in {project}.",
        "Read what people said in {channel} on Slack and file it as a Jira issue under {project}.",
        "Create a {project} Jira bug capturing the latest Slack conversation in {channel}.",
    ], 3),
    ("ci_to_linear", T("repo_path", "team_name"), T("ci_log", "linear_issue_created"), ["github", "linear"], [
        "The GitHub CI on {repo} is failing; read the logs and open a Linear issue for the {team} team with the error.",
        "Create a Linear ticket for {team} from the latest failed GitHub Actions run of {repo}, including the log.",
        "File the newest GitHub workflow failure in {repo} as a Linear issue for team {team}.",
    ], 3),
    ("drive_to_notion", [], T("drive_file_id", "doc_text", "notion_page_created"), ["google_drive", "notion"], [
        "Copy the contents of the launch checklist from Google Drive into a new Notion page under Projects.",
        "Read the hiring plan doc in Drive and create a Notion page with it in the Team wiki.",
    ], 2),
    ("file_to_discord", T("local_path", "channel_name"), T("file_text", "discord_message_sent"), ["filesystem", "discord"], [
        "Read {path} and post its contents in the Discord channel {channel}.",
        "Share what's in the local file {path} with Discord {channel}.",
        "Open {path} and paste it into our {channel} Discord channel.",
    ], 3),
    ("resolve_and_announce", T("service_name", "channel_name"), T("incident_resolved", "slack_message_sent"), ["pagerduty", "slack"], [
        "Resolve the open {service} incident in PagerDuty and tell Slack {channel} it's fixed.",
        "Close out the current {service} page and post an all-clear in the Slack channel {channel}.",
        "Mark the {service} incident resolved and announce it in {channel} on Slack.",
    ], 4),
    ("db_update", T("table_name"), T("db_rows_updated"), ["postgres"], [
        "In Postgres, set status to 'archived' for rows in {table} older than a year.",
        "Backfill the missing region column in the {table} table.",
    ], 2),
    ("crm_add_contact", T("email_address"), T("contact_created"), ["hubspot"], [
        "Add {email} as a new contact in HubSpot.",
        "Create a HubSpot contact for {email} from today's webinar.",
    ], 2),
    ("cancel_meeting", T("date_range"), T("event_cancelled"), ["google_calendar"], [
        "Cancel my meeting {date}.",
        "Delete the calendar event I have {date}.",
    ], 2),
    ("reschedule", T("date_range"), T("event_updated", "time_slot"), ["google_calendar"], [
        "Move my meeting {date} to another time when all attendees are free.",
        "Reschedule the event {date} to a slot that works for everyone invited.",
        "Push the meeting I have {date} to a time when its attendees are available.",
    ], 3),
    ("gl_comment", T("repo_path"), T("gl_comment_posted"), ["gitlab"], [
        "Leave a note on the newest GitLab merge request in {repo} saying tests pass locally.",
        "Comment on the latest MR in {repo} on GitLab that it needs a rebase.",
    ], 2),
    ("slack_topic", T("channel_name"), T("slack_topic_set"), ["slack"], [
        "Change the Slack topic of {channel} to 'Freeze until Monday'.",
        "Update {channel}'s channel topic on Slack with the new on-call rotation link.",
    ], 2),
    ("discord_dm", T("person_name"), T("discord_dm_sent"), ["discord"], [
        "DM {person} on Discord that the stream starts in 10 minutes.",
        "Send {person} a private Discord message with the meeting link.",
    ], 2),
    ("slack_dm_name", T("person_name"), T("slack_dm_sent"), ["slack"], [
        "Send {person} a Slack DM asking for the slide deck.",
        "Message {person} directly on Slack to remind them about the review.",
    ], 2),
]


def fill(tmpl: str, i: int) -> str:
    return tmpl.format(
        repo=REPOS[i % len(REPOS)], service=SERVICES[i % len(SERVICES)], channel=CHANNELS[i % len(CHANNELS)],
        team=TEAMS[i % len(TEAMS)], project=PROJECTS[i % len(PROJECTS)], email=EMAILS[i % len(EMAILS)],
        person=PEOPLE[i % len(PEOPLE)], company=COMPANIES[i % len(COMPANIES)], table=TABLES[i % len(TABLES)],
        path=PATHS[i % len(PATHS)], date=DATES[i % len(DATES)],
    )


def main() -> None:
    cat = load_catalog()
    rng = random.Random(SEED)
    tasks: list[dict] = []
    fam_tasks: dict[str, list[dict]] = {}
    for fam, given, goal, servers, phrasings, n in FAMILIES:
        assert shortest_plan(cat, set(given), set(goal), max_len=6) is not None, f"{fam}: no valid chain"
        # gold = shortest chain using only the servers the task is about (diagnostic only)
        on_servers = {tid for tid, tl in cat.tools.items() if tl.server in servers}
        gold = shortest_plan(cat, set(given), set(goal), max_len=6, tool_ids=on_servers)
        assert gold is not None, f"{fam}: no valid chain on {servers}"
        for v in range(n):
            # entity offset varies per family so entities don't line up across families
            i = v + len(fam)
            t = {
                "family": fam,
                "text": fill(phrasings[v % len(phrasings)], i),
                "given": given,
                "goal": goal,
                "gold_tools": gold,
                "difficulty": len(gold),
                "servers_mentioned": servers,
            }
            fam_tasks.setdefault(fam, []).append(t)
            tasks.append(t)

    # stratified split: ~1/3 of each family to test, at least one to train
    train, test = [], []
    for fam, ts in fam_tasks.items():
        ts = ts[:]
        rng.shuffle(ts)
        k = max(1, round(len(ts) / 3)) if len(ts) > 1 else 0
        test += ts[:k]
        train += ts[k:]
    rng.shuffle(train)
    rng.shuffle(test)
    for i, t in enumerate(train):
        t["id"] = f"tr{i:03d}"
    for i, t in enumerate(test):
        t["id"] = f"te{i:03d}"

    out = ROOT / "data" / "tasks"
    out.mkdir(parents=True, exist_ok=True)
    keys = ["id", "family", "text", "given", "goal", "gold_tools", "difficulty", "servers_mentioned"]
    for name, rows in (("train", train), ("test", test)):
        (out / f"{name}.jsonl").write_text("".join(json.dumps({k: r[k] for k in keys}) + "\n" for r in rows))

    def bucket(d):
        return "1-2" if d <= 2 else ("3" if d == 3 else "4-5")

    allt = train + test
    mix = Counter(bucket(t["difficulty"]) for t in allt)
    print(f"{len(FAMILIES)} families, {len(allt)} tasks: train={len(train)} test={len(test)}")
    print("difficulty mix:", {k: f"{v} ({v / len(allt):.0%})" for k, v in sorted(mix.items())})
    for fam, given, goal, servers, _, n in FAMILIES:
        print(f"  {fam:<24} n={n} len={len(fam_tasks[fam][0]['gold_tools'])}  {' -> '.join(x.split(':')[1] for x in fam_tasks[fam][0]['gold_tools'])}")


if __name__ == "__main__":
    main()
