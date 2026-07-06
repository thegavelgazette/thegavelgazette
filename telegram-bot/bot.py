"""
The Gavel Gazette — Telegram Publishing Bot
=============================================
Lets an approved team member write a new article entirely inside Telegram,
then pushes it straight to GitHub as a commit to articles.json. Since your
host (Cloudflare Pages / Netlify) is connected to that GitHub repo, the push
automatically triggers a live redeploy — no one ever touches code.

SETUP
-----
1. pip install -r requirements.txt
2. Copy .env.example to .env and fill in:
     TELEGRAM_BOT_TOKEN   - from @BotFather on Telegram
     GITHUB_TOKEN         - a GitHub Personal Access Token (see README.md)
     GITHUB_REPO          - e.g. "yourname/the-gavel-gazette"
     GITHUB_BRANCH        - usually "main"
     GITHUB_FILE_PATH     - usually "articles.json"
     ALLOWED_USER_IDS     - comma-separated Telegram numeric user IDs allowed to publish
3. Run: python bot.py
4. This process needs to keep running 24/7 to receive Telegram messages —
   see README.md for free/cheap hosting options (Railway, Render, a small VPS).

HOW IT WORKS
------------
/newpost starts a short guided conversation:
  category -> title -> author -> excerpt -> full content -> pin? -> preview -> confirm
On confirm, the bot:
  1. Fetches the current articles.json from GitHub (with its file SHA)
  2. Prepends the new article
  3. Commits the updated file back to the same branch
Anyone not in ALLOWED_USER_IDS is politely refused.
"""

import os
import json
import base64
import logging
from datetime import date

import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]              # "owner/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH", "articles.json")
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}

CATEGORIES = ["Case Commentary", "Opinion", "Article", "Legal News", "Global Perspective"]

# Conversation states
CATEGORY, TITLE, AUTHOR, EXCERPT, CONTENT, PIN, CONFIRM = range(7)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def is_allowed(user_id: int) -> bool:
    # If no allowlist is configured, allow no one — safer default than allowing everyone.
    return user_id in ALLOWED_USER_IDS


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def github_get_file():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    res = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
    res.raise_for_status()
    data = res.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def github_put_file(new_articles, sha, commit_message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    new_content = json.dumps(new_articles, indent=2, ensure_ascii=False)
    payload = {
        "message": commit_message,
        "content": base64.b64encode(new_content.encode("utf-8")).decode("utf-8"),
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    res = requests.put(url, headers=headers, json=payload)
    res.raise_for_status()
    return res.json()


def next_docket(articles):
    year = date.today().strftime("%y")
    seq = str(len(articles) + 1).zfill(4)
    return f"No. {year}-{seq}"


# ---------------------------------------------------------------------------
# Conversation handlers
# ---------------------------------------------------------------------------

async def pin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Sorry, this Telegram account isn't approved to manage posts.")
        return

    try:
        articles, _ = github_get_file()
    except Exception as e:
        await update.message.reply_text(f"Couldn't load articles: {e}")
        return

    if not articles:
        await update.message.reply_text("There are no articles yet.")
        return

    # Show the 12 most recently dated articles, easiest to scan
    recent = sorted(articles, key=lambda a: a.get("date", ""), reverse=True)[:12]

    keyboard = []
    for a in recent:
        label = ("📌 " if a.get("pinned") else "") + a["title"][:45]
        keyboard.append([InlineKeyboardButton(label, callback_data=f"togglepin:{a['id']}")])

    await update.message.reply_text(
        "Tap an article to pin or unpin it (📌 = currently pinned):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def togglepin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not is_allowed(user.id):
        await query.answer("Not approved to do this.", show_alert=True)
        return
    await query.answer()

    article_id = query.data.split(":", 1)[1]
    try:
        articles, sha = github_get_file()
        target = next((a for a in articles if a["id"] == article_id), None)
        if target is None:
            await query.edit_message_text("That article couldn't be found anymore (maybe already changed).")
            return

        target["pinned"] = not target.get("pinned", False)
        github_put_file(
            articles, sha,
            f"{'Pin' if target['pinned'] else 'Unpin'} article via Telegram: {target['title']}"
        )
        state = "📌 Pinned" if target["pinned"] else "Unpinned"
        await query.edit_message_text(f"{state}: {target['title']}\n\nUse /pin again to manage more.")
    except Exception as e:
        log.exception("Pin toggle failed")
        await query.edit_message_text(f"❌ Something went wrong: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_allowed(user.id):
        await update.message.reply_text(
            "Welcome to The Gavel Gazette publishing bot.\n\n"
            "Send /newpost to write and publish a new article.\n"
            "Send /pin to pin or unpin an already-published article.\n"
            "Send /cancel any time to abort a post you're writing."
        )
    else:
        await update.message.reply_text(
            "Hi! This bot publishes articles for The Gavel Gazette, "
            "but your Telegram account isn't approved yet. Ask an admin to add your user ID."
        )


async def newpost_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text(
            "Sorry, this Telegram account isn't approved to publish. "
            "Ask an admin to add your user ID to the bot's allowlist."
        )
        return ConversationHandler.END

    context.user_data.clear()
    keyboard = [[InlineKeyboardButton(c, callback_data=c)] for c in CATEGORIES]
    await update.message.reply_text(
        "Let's write a new piece for The Gavel Gazette.\n\nFirst — which category is this?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CATEGORY


async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data
    await query.edit_message_text(f"Category: {query.data}\n\nWhat's the title of the piece?")
    return TITLE


async def title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text("Got it. Who's the author (your name)?")
    return AUTHOR


async def author_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["author"] = update.message.text.strip()
    await update.message.reply_text(
        "Now write a short excerpt — one or two sentences that'll show on the article card."
    )
    return EXCERPT


async def excerpt_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["excerpt"] = update.message.text.strip()
    await update.message.reply_text(
        "Now paste the full text of the piece. You can send it as one long message."
    )
    return CONTENT


async def content_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["content"] = update.message.text.strip()
    keyboard = [[
        InlineKeyboardButton("Yes, pin it", callback_data="pin_yes"),
        InlineKeyboardButton("No", callback_data="pin_no"),
    ]]
    await update.message.reply_text(
        "Should this be pinned to the top of the issue?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PIN


async def pin_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["pinned"] = query.data == "pin_yes"

    d = context.user_data
    preview = (
        f"*Preview*\n\n"
        f"Category: {d['category']}\n"
        f"Title: {d['title']}\n"
        f"Author: {d['author']}\n"
        f"Pinned: {'Yes' if d['pinned'] else 'No'}\n\n"
        f"Excerpt:\n{d['excerpt']}\n\n"
        f"Content:\n{d['content'][:500]}{'...' if len(d['content']) > 500 else ''}\n\n"
        f"Publish this now?"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Publish", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel", callback_data="confirm_no"),
    ]]
    await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM


async def confirm_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        await query.edit_message_text("Cancelled. Nothing was published. Use /newpost to start again.")
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text("Publishing… this takes a few seconds.")

    try:
        articles, sha = github_get_file()
        d = context.user_data
        new_article = {
            "id": f"a-{int(__import__('time').time())}",
            "docket": next_docket(articles),
            "category": d["category"],
            "title": d["title"],
            "author": d["author"],
            "date": date.today().isoformat(),
            "excerpt": d["excerpt"],
            "content": d["content"],
            "pinned": d["pinned"],
        }
        articles.insert(0, new_article)
        github_put_file(articles, sha, f"New article via Telegram: {d['title']}")

        await query.edit_message_text(
            f"✅ Published \"{d['title']}\"\n\n"
            f"Your site will update automatically in a minute or two as your host redeploys."
        )
    except Exception as e:
        log.exception("Publish failed")
        await query.edit_message_text(f"❌ Something went wrong publishing this: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def main():
    # Python 3.14 removed automatically creating an event loop when none exists
    # for the current thread. python-telegram-bot's run_polling() still expects
    # the old behavior, so we create and set one explicitly before it runs.
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("newpost", newpost_start)],
        states={
            CATEGORY: [CallbackQueryHandler(category_chosen)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, title_received)],
            AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, author_received)],
            EXCERPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, excerpt_received)],
            CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, content_received)],
            PIN: [CallbackQueryHandler(pin_chosen)],
            CONFIRM: [CallbackQueryHandler(confirm_chosen)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pin", pin_list))
    app.add_handler(CallbackQueryHandler(togglepin_callback, pattern="^togglepin:"))
    app.add_handler(conv)

    log.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
