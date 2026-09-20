# User guide

This guide walks through FinPilot from the first sign-in. For how the numbers are calculated, see [calculation-methods.md](calculation-methods.md).

FinPilot is a planning tool. It does not move real money, and its results are estimates, not financial, tax or legal advice.

## Create your workspace

1. Open FinPilot and choose **Create an account**.
2. Enter your name, email address and a password of at least 12 characters.
3. Leave **Start with sample data to explore** unchecked for your own private, empty workspace. Check it to explore a separate workspace filled with fictional data.
4. Choose **Create workspace**.

A sample workspace can simulate payments but can never connect real banks. To use your own accounts later, create another account without sample data. Return later with **Sign in**; sessions last 12 hours.

## Find your way around

The sections are Overview, Accounts, Paycheck plan, Bills & payments, Cash flow, Savings goals, Debt & credit, Recurring rules, Tax & protection and AI workspace. Charts and review items open the details behind them.

Open **Workspace settings** from your profile button to switch between light and dark themes, check whether the AI model is available, or sign out.

## Add your finances

- **Accounts:** add cash, card, loan, investment and asset accounts with their balances and terms. Each account has Summary, Activity, Analytics and Account details tabs.
- **Income:** use **Add income source** for a recurring paycheck, and **Add income event** to record an expected or received deposit.
- **Bills:** add each bill with its amount, due date, schedule and funding account.
- **Savings goals:** set a target, an account and an optional date. Mark a goal as protected to keep that money out of your spending allowance.
- **Recurring rules:** set fixed, percentage or target amounts that move toward goals, bills or debts. You can pause a rule, resume it or skip its next run.
- **Tax assumptions:** use **Edit tax assumptions** to enter your marginal rates. They are labelled approximate until verified.

Every form can be edited later, for example with **Edit bill** or **Edit savings goal**.

## Import transaction history

1. Choose **Import transaction history** and select the account.
2. Pick a UTF-8 CSV file, or paste its contents.
3. Choose the date format, `YYYY-MM-DD` or `MM/DD/YYYY`.
4. If the columns are not detected automatically, open the column mapping and match date, description and amount. Category and kind are optional.
5. Choose **Preview import**. Review the rows, duplicates and errors.
6. Confirm the import. Duplicates are skipped, and your recorded account balances do not change.

You can also upload a CSV inside a chat conversation; the assistant shows the exact rows and waits for your confirmation.

## Plan each paycheck

**Paycheck plan** shows how each deposit this month is split across bills, reserves, debts and goals, with the reason for every amount.

- Anything due before your next paycheck is funded from the current one.
- Required bills and protected reserves come before optional goals and percentage rules.
- When a paycheck cannot cover everything required, the plan names the shortfall and its consequence instead of hiding it.

## Check cash flow and spending

- **Cash flow** projects each account's daily balance and marks its lowest point and any day it would go negative.
- The spending allowance is how much you can spend over the next days while still covering bills and protected savings. It is based on your lowest projected balance, not your ending balance.

## Debt, cards and protection

- **Debt & credit** compares payoff strategies with the same budget, shows the effect of extra payments, models mortgage choices and suggests which card to use for a purchase.
- **Tax & protection** compares saving with paying down debt after tax, shows how quickly your money can be reached, and estimates deposit insurance coverage.

## Ask FinPilot

1. Choose **Ask FinPilot** from any page, or open the AI workspace. The assistant knows which page or account you are viewing.
2. Ask a question, such as "How much can I spend this week?" or "Which card should I use for an $80 dinner?"
3. Answers use FinPilot's calculations. When no AI model is available, you still get calculator answers.

**Every figure comes from FinPilot's calculations.** When an AI model words an answer, it writes placeholders that FinPilot fills with the exact figures. If the wording adds a number, investment or product advice, or a claim that something was done, you get FinPilot's calculator wording instead. Requests unrelated to your finances get a short reply that says what FinPilot can help with.

**See where an answer comes from.** Under an answer, **Record sources** links to the accounts, bills, cards, debts, goals, rules and transactions behind it; choose one to open it. The confidence label, such as **High confidence**, summarizes how complete and current that evidence is. Open **View sources and assumptions** to see the reasons and which calculation field each figure came from.

**Conversations** lists your saved conversations so you can resume or delete one, and **New conversation** starts a fresh one. Conversations are stored in your account, not in your browser.

**Changes need your approval.** When you ask for a change, such as updating a bill, the assistant prepares a review card showing exactly what will change. Choose **Confirm** to apply it, **Edit** to adjust it or **Cancel** to discard it. Nothing changes until you confirm. A review expires after 15 minutes, and one request can include up to four changes. Confirmed changes leave a receipt in the conversation.

[ai-capabilities.md](ai-capabilities.md) lists everything the assistant can do.

## Use your documents

1. Open **Documents & sources**, then **Documents**, and upload PDF, TXT or Markdown files of up to 2 MB. PDFs need selectable text; scanned images are not read.
2. Select up to ten documents for a conversation, then ask a question in chat.
3. Choose a citation in the answer to see the original excerpt.

Documents inform answers but never change your financial records. Deleting a document removes it from future answers.

## Connect outside sources

- **Connections:** choose **Connect source** for a source your workspace operator has approved, browse its read operations and use **Import as document** or **Import resource** to save the result as a private document.
- **MCP access:** choose **Create read access** to get an expiring read-only token for your own MCP client. The token is shown once. Use **Create client configuration** and **Copy configuration** to set up the client, and **Revoke access** to end it. Access lasts at most 30 days and can never change your records.

[MCP.md](../MCP.md) has the details.

## Link a bank

When your operator has configured bank linking, **Connected banks** lets you link supported US checking, savings, money-market, credit card, auto loan, personal loan, mortgage and student loan accounts. Sync imports balance snapshots, transactions sorted into FinPilot categories, and card and loan terms when your bank shares them. Balances are cached, not realtime. A debt without a reported interest rate or required payment still counts in your totals; enter its terms on the account to include it in payoff plans. For a linked mortgage, enter principal and interest and escrow separately. Accounts that sync from your bank do not accept CSV imports, so transactions are not counted twice. Disconnecting removes the stored bank authorization and keeps the records already imported. Sample workspaces cannot link banks.

## Simulate payments in a sample workspace

In a sample workspace you can build payment drafts from your paycheck plan, review their checks and confirm a simulated payment. Simulations use fictional money and never touch a real account. Pausing all execution stops every simulated payment until you resume.
