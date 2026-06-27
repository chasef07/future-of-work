# Agent OS Approval Policy

This policy is intentionally conservative.

## Automatic

Agents may do these without asking:

- Read local files, code, logs, and safe command output.
- Create local drafts and plans.
- Run focused tests and validation commands.
- Prepare worker prompts.
- Record events, tasks, runs, artifacts, briefs, and proposed knowledge.

## Approval Required

Agents must ask before:

- Sending email or messages externally.
- Publishing, posting, or customer-facing communication.
- Spending money or changing billing.
- Deploying, releasing, merging, or pushing production changes.
- Deleting data, archiving mailbox state, unsubscribing, or mutating external apps.
- Accessing sensitive personal, financial, legal, medical, or credential material
  beyond the minimum needed for the approved task.

## Done Means Proof

A task is not done until there is proof, such as:

- Test output.
- PR or commit link.
- Draft text.
- Sent receipt.
- Screenshot.
- Provider response.
- File path and summary.
- Written conclusion with evidence.

## Learning

Agents may propose new rules, facts, preferences, and playbooks. Major rules
become active only after human approval.
