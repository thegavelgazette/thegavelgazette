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
EDIT_FIELD, EDIT_VALUE = range(7, 9)

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
            "Send /edit to edit an already-published article.\n"
            "Send /delete to remove an already-published article.\n"
            "Send /cancel any time to abort a post you're writing."
        )
    else:
        await update.message.reply_text(
            "Hi! This bot publishes articles for The Gavel Gazette, "
            "but your Telegram account isn't approved yet. Ask an admin to add your user ID."
        )


# ---------------------------------------------------------------------------
# /delete — list recent articles, confirm, remove
# ---------------------------------------------------------------------------

async def delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    recent = sorted(articles, key=lambda a: a.get("date", ""), reverse=True)[:12]
    keyboard = [[InlineKeyboardButton(a["title"][:45], callback_data=f"delask:{a['id']}")] for a in recent]
    await update.message.reply_text(
        "Tap an article to delete it (you'll be asked to confirm):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    article_id = query.data.split(":", 1)[1]
    try:
        articles, _ = github_get_file()
        target = next((a for a in articles if a["id"] == article_id), None)
        if target is None:
            await query.edit_message_text("That article couldn't be found anymore.")
            return
        keyboard = [[
            InlineKeyboardButton("✅ Yes, delete it", callback_data=f"delyes:{article_id}"),
            InlineKeyboardButton("❌ No, keep it", callback_data="delno"),
        ]]
        await query.edit_message_text(
            f"Delete this permanently?\n\n\"{target['title']}\"",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Something went wrong: {e}")


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not is_allowed(user.id):
        await query.answer("Not approved to do this.", show_alert=True)
        return
    await query.answer()

    if query.data == "delno":
        await query.edit_message_text("Cancelled — nothing was deleted.")
        return

    article_id = query.data.split(":", 1)[1]
    try:
        articles, sha = github_get_file()
        target = next((a for a in articles if a["id"] == article_id), None)
        if target is None:
            await query.edit_message_text("That article couldn't be found anymore (maybe already deleted).")
            return
        remaining = [a for a in articles if a["id"] != article_id]
        github_put_file(remaining, sha, f"Delete article via Telegram: {target['title']}")
        await query.edit_message_text(f"🗑️ Deleted: {target['title']}")
    except Exception as e:
        log.exception("Delete failed")
        await query.edit_message_text(f"❌ Something went wrong: {e}")


# ---------------------------------------------------------------------------
# /edit — pick an article, pick a field, send a new value, repeat, then save
# ---------------------------------------------------------------------------

EDIT_FIELDS = [
    ("title", "Title"),
    ("author", "Author"),
    ("excerpt", "Excerpt"),
    ("content", "Full text"),
    ("category", "Category"),
    ("pinned", "Toggle pinned"),
]


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Sorry, this Telegram account isn't approved to manage posts.")
        return ConversationHandler.END
    try:
        articles, _ = github_get_file()
    except Exception as e:
        await update.message.reply_text(f"Couldn't load articles: {e}")
        return ConversationHandler.END
    if not articles:
        await update.message.reply_text("There are no articles yet.")
        return ConversationHandler.END

    recent = sorted(articles, key=lambda a: a.get("date", ""), reverse=True)[:12]
    keyboard = [[InlineKeyboardButton(a["title"][:45], callback_data=f"editpick:{a['id']}")] for a in recent]
    await update.message.reply_text(
        "Which article do you want to edit?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_FIELD


def _edit_field_menu_text(d):
    a = d["working"]
    return (
        f"Editing: *{a['title']}*\n\n"
        f"Category: {a['category']}\n"
        f"Author: {a['author']}\n"
        f"Pinned: {'Yes' if a.get('pinned') else 'No'}\n"
        f"Excerpt: {a['excerpt'][:80]}{'...' if len(a['excerpt']) > 80 else ''}\n\n"
        f"What do you want to change?"
    )


async def edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    article_id = query.data.split(":", 1)[1]
    try:
        articles, sha = github_get_file()
    except Exception as e:
        await query.edit_message_text(f"Couldn't load articles: {e}")
        return ConversationHandler.END

    target = next((a for a in articles if a["id"] == article_id), None)
    if target is None:
        await query.edit_message_text("That article couldn't be found anymore.")
        return ConversationHandler.END

    context.user_data["edit"] = {"id": article_id, "sha": sha, "working": dict(target)}

    keyboard = [[InlineKeyboardButton(label, callback_data=f"editfield:{key}")] for key, label in EDIT_FIELDS]
    keyboard.append([InlineKeyboardButton("💾 Save changes", callback_data="editfield:save")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="editfield:cancel")])
    await query.edit_message_text(
        _edit_field_menu_text(context.user_data["edit"]),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_FIELD


async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]
    d = context.user_data.get("edit")
    if d is None:
        await query.edit_message_text("Session expired — start again with /edit.")
        return ConversationHandler.END

    if field == "cancel":
        context.user_data.pop("edit", None)
        await query.edit_message_text("Cancelled — no changes were saved.")
        return ConversationHandler.END

    if field == "save":
        try:
            articles, fresh_sha = github_get_file()
            idx = next((i for i, a in enumerate(articles) if a["id"] == d["id"]), None)
            if idx is None:
                await query.edit_message_text("That article couldn't be found anymore (maybe deleted).")
                return ConversationHandler.END
            articles[idx] = d["working"]
            github_put_file(articles, fresh_sha, f"Edit article via Telegram: {d['working']['title']}")
            await query.edit_message_text(f"✅ Saved changes to \"{d['working']['title']}\"")
        except Exception as e:
            log.exception("Edit save failed")
            await query.edit_message_text(f"❌ Something went wrong saving: {e}")
        context.user_data.pop("edit", None)
        return ConversationHandler.END

    if field == "pinned":
        d["working"]["pinned"] = not d["working"].get("pinned", False)
        keyboard = [[InlineKeyboardButton(label, callback_data=f"editfield:{key}")] for key, label in EDIT_FIELDS]
        keyboard.append([InlineKeyboardButton("💾 Save changes", callback_data="editfield:save")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="editfield:cancel")])
        await query.edit_message_text(
            _edit_field_menu_text(d), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_FIELD

    if field == "category":
        keyboard = [[InlineKeyboardButton(c, callback_data=f"editcat:{c}")] for c in CATEGORIES]
        keyboard.append([InlineKeyboardButton("« Back", callback_data="editfield:back")])
        await query.edit_message_text("Pick a new category:", reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_FIELD

    if field == "back":
        keyboard = [[InlineKeyboardButton(label, callback_data=f"editfield:{key}")] for key, label in EDIT_FIELDS]
        keyboard.append([InlineKeyboardButton("💾 Save changes", callback_data="editfield:save")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="editfield:cancel")])
        await query.edit_message_text(
            _edit_field_menu_text(d), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_FIELD

    # title / author / excerpt / content -> ask for a text reply
    d["pending_field"] = field
    await query.edit_message_text(f"Send the new {field} as a message:")
    return EDIT_VALUE


async def edit_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = context.user_data.get("edit")
    if d is None:
        await query.edit_message_text("Session expired — start again with /edit.")
        return ConversationHandler.END
    new_category = query.data.split(":", 1)[1]
    d["working"]["category"] = new_category
    keyboard = [[InlineKeyboardButton(label, callback_data=f"editfield:{key}")] for key, label in EDIT_FIELDS]
    keyboard.append([InlineKeyboardButton("💾 Save changes", callback_data="editfield:save")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="editfield:cancel")])
    await query.edit_message_text(
        _edit_field_menu_text(d), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_FIELD


async def edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data.get("edit")
    if d is None:
        await update.message.reply_text("Session expired — start again with /edit.")
        return ConversationHandler.END
    field = d.pop("pending_field", None)
    if field:
        d["working"][field] = update.message.text.strip()

    keyboard = [[InlineKeyboardButton(label, callback_data=f"editfield:{key}")] for key, label in EDIT_FIELDS]
    keyboard.append([InlineKeyboardButton("💾 Save changes", callback_data="editfield:save")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="editfield:cancel")])
    await update.message.reply_text(
        _edit_field_menu_text(d), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_FIELD


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("edit", None)
    await update.message.reply_text("Cancelled — no changes were saved.")
    return ConversationHandler.END


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
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_FIELD: [
                CallbackQueryHandler(edit_pick, pattern="^editpick:"),
                CallbackQueryHandler(edit_category_chosen, pattern="^editcat:"),
                CallbackQueryHandler(edit_field_chosen, pattern="^editfield:"),
            ],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_received)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pin", pin_list))
    app.add_handler(CallbackQueryHandler(togglepin_callback, pattern="^togglepin:"))
    app.add_handler(CommandHandler("delete", delete_list))
    app.add_handler(CallbackQueryHandler(delete_ask, pattern="^delask:"))
    app.add_handler(CallbackQueryHandler(delete_confirm, pattern="^(delyes:|delno$)"))
    app.add_handler(edit_conv)
    app.add_handler(conv)

    log.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
