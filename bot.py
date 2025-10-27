import logging
import sqlite3
import json
import requests
import html
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ----------------- CONFIG -----------------
TELEGRAM_TOKEN = "8422360803:AAG44u_upD1NCaxOdRR9rfy648xYgdeNsdo"
OPENROUTER_API_KEY = "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"
ADMIN_ID = 7474193095
OPENROUTER_URL = "https://api.openrouter.ai/v1/chat/completions"
OPENROUTER_MODEL = "MiniMax: MiniMax M2"
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Ты — брокер-консультант eToroTrade. Общайся естественно и уверенно, как опытный продавец. "
        "Клиент пришёл после рассылки и проявил интерес. Задача — заинтересовать его в инвестициях "
        "и плавно подвести к согласию перейти в Telegram-бот @eToroTrade_Robot. Общайся по прямой линии "
        "продаж: выстраивай доверие, хвали подход клиента, подбирай биржу под него. Узнай его цель или "
        "мечту (машина, свобода, доход, путешествия) и при отказах мягко напоминай, что это шанс приблизиться "
        "к мечте. Используй короткие, живые ответы, без воды. Сравни eToro с другими биржами (Bybit, Binance) "
        "— подчеркни, что eToro даёт 3 направления: крипта, акции, сырьё, и что с кредитным плечом можно начать с минимума. "
        "В финале объясни, что бот @eToroTrade_Robot в Telegram — это проще и удобнее, и что скоро свяжется специалист для подробностей."
    ),
}

DB_PATH = Path("conversations.sqlite")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
USERS_PER_PAGE = 5  # Количество пользователей на страницу в админ-меню

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ----------------- Database -----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str, first_name: str, last_name: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (id, username, first_name, last_name, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username or "", first_name or "", last_name or "", datetime.utcnow().isoformat()),
        )
        conn.commit()
    conn.close()

def save_message(user_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

def get_users(page: int = 0) -> List[Dict[str, Any]]:
    offset = page * USERS_PER_PAGE
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, first_name, last_name, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (USERS_PER_PAGE, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(id=r[0], username=r[1], first_name=r[2], last_name=r[3], created_at=r[4]) for r in rows]

def get_user_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_conversation(user_id: int, page: int = 0, per_page: int = 10) -> List[Dict[str, str]]:
    offset = page * per_page
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content, created_at FROM messages WHERE user_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
        (user_id, per_page, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(role=r[0], content=r[1], created_at=r[2]) for r in rows]

# ----------------- OpenRouter -----------------
def call_openrouter(messages: List[Dict[str, str]]) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 800,
        "temperature": 0.7,
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0].get("message", {}).get("content")
            if isinstance(content, dict):
                return "\n".join(content.get("parts", [])) if content.get("parts") else str(content)
            return content or ""
        return json.dumps(data, ensure_ascii=False)[:2000]
    except Exception as e:
        logger.exception("OpenRouter request failed")
        return f"Извините, произошла ошибка: {e}"

# ----------------- Telegram Handlers -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, getattr(user, "username", ""), user.first_name, getattr(user, "last_name", ""))
    save_message(user.id, "system", SYSTEM_PROMPT["content"])

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📝 Задать вопрос")], [KeyboardButton("🔙 Назад")]],
        resize_keyboard=True,
    )

    await update.message.reply_html(
        f"Привет, <b>{html.escape(user.first_name or user.username or 'инвестор')}</b>! 👋\n"
        "Я — ваш помощник в мире инвестиций. Задайте вопрос или начните диалог!",
        reply_markup=kb,
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📝 Задать вопрос")], [KeyboardButton("🔙 Назад")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Просто напишите ваш вопрос, и я помогу разобраться в инвестициях!",
        reply_markup=kb,
    )

async def worker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Доступно только администратору.")
        return

    context.user_data["admin_page"] = 0
    await show_admin_menu(update, context)

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = context.user_data.get("admin_page", 0)
    users = get_users(page)
    total_users = get_user_count()
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE

    if not users:
        await update.message.reply_text("Пока нет пользователей.")
        return

    keyboard = []
    for u in users:
        title = u["username"] or f"{u['first_name']} {u['last_name']}".strip() or str(u["id"])
        keyboard.append([InlineKeyboardButton(title, callback_data=f"worker_user:{u['id']}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Пред", callback_data=f"admin_page:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("След ➡️", callback_data=f"admin_page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])

    text = f"Пользователи (страница {page + 1}/{total_pages}):"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back":
        await show_admin_menu(update, context)

    elif data.startswith("admin_page:"):
        page = int(data.split(":", 1)[1])
        context.user_data["admin_page"] = page
        await query.message.delete()
        await show_admin_menu(query, context)

    elif data.startswith("worker_user:"):
        target_id = int(data.split(":", 1)[1])
        user_row = next((u for u in get_users() if u["id"] == target_id), None)
        if not user_row:
            await query.edit_message_text("Пользователь не найден.")
            return

        title = user_row["username"] or f"{user_row['first_name']} {user_row['last_name']}".strip() or str(user_row["id"])
        keyboard = [
            [InlineKeyboardButton("📋 Отправить тег", callback_data=f"worker_copy:{target_id}"),
             InlineKeyboardButton("📖 Прочитать диалог", callback_data=f"worker_read:{target_id}:0")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")],
        ]
        await query.edit_message_text(f"Пользователь: {title}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("worker_copy:"):
        target_id = int(data.split(":", 1)[1])
        u = next((x for x in get_users() if x["id"] == target_id), None)
        if not u:
            await query.edit_message_text("Пользователь не найден.")
            return
        tag = f"@{u['username']}" if u["username"] else f"tg://user?id={u['id']}"
        await query.edit_message_text(f"Тег пользователя: <code>{tag}</code>", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data=f"worker_user:{target_id}")]
        ]))

    elif data.startswith("worker_read:"):
        target_id, page = map(int, data.split(":", 2)[1:])
        conv = get_conversation(target_id, page)
        if not conv:
            await query.edit_message_text("Диалог пуст.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"worker_user:{target_id}")]
            ]))
            return

        lines = []
        for m in conv:
            ts = m.get("created_at", "")
            role = m.get("role").upper()
            content = m.get("content")
            lines.append(f"[{ts}] {role}: {content}")
        txt = "\n\n".join(lines)

        keyboard = []
        total_msgs = len(get_conversation(target_id, 0, 1000))  # Примерное количество
        total_pages = (total_msgs + 9) // 10
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Пред", callback_data=f"worker_read:{target_id}:{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("След ➡️", callback_data=f"worker_read:{target_id}:{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"worker_user:{target_id}")])

        if len(txt) > 3500:
            p = Path("tmp_dialog.txt")
            p.write_text(txt, encoding="utf-8")
            await query.message.reply_document(
                document=InputFile(str(p)),
                filename=f"dialog_{target_id}_page_{page+1}.txt",
                caption=f"Диалог пользователя (страница {page+1})",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            p.unlink(missing_ok=True)
            await query.message.delete()
        else:
            safe = html.escape(txt)
            await query.edit_message_text(
                f"<pre>{safe}</pre>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    add_user(user.id, getattr(user, "username", ""), user.first_name, getattr(user, "last_name", ""))

    if text == "🔙 Назад":
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📝 Задать вопрос")], [KeyboardButton("🔙 Назад")]],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "Вернулся в главное меню. Задайте вопрос или начните диалог!",
            reply_markup=kb,
        )
        return

    # Сохраняем сообщение пользователя
    save_message(user.id, "user", text)

    # Собираем контекст
    conv = get_conversation(user.id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT["content"]}]
    for m in conv[-20:]:
        messages.append({"role": m["role"], "content": m["content"]})

    # Отправляем в OpenRouter
    await update.message.chat.action("typing")
    ai_response = call_openrouter(messages)

    # Сохраняем ответ AI
    save_message(user.id, "assistant", ai_response)

    # Кнопки
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Перейти в @eToroTrade_Robot", url="https://t.me/eToroTrade_Robot")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ])

    # Отправляем ответ
    if len(ai_response) > 3000:
        p = Path(f"reply_{user.id}.txt")
        p.write_text(ai_response, encoding="utf-8")
        await update.message.reply_document(
            document=InputFile(str(p)),
            filename="response.txt",
            caption="Ответ от eToroTrade:",
            reply_markup=kb,
        )
        p.unlink(missing_ok=True)
    else:
        await update.message.reply_text(ai_response, reply_markup=kb)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📝 Задать вопрос")], [KeyboardButton("🔙 Назад")]],
        resize_keyboard=True,
    )
    await query.message.reply_text(
        "Вернулся в главное меню. Задайте вопрос или начните диалог!",
        reply_markup=kb,
    )
    await query.message.delete()

# ----------------- Main -----------------
def main():
    init_db()
    if TELEGRAM_TOKEN.startswith("YOUR_"):
        logger.error("В файле не заполнен TELEGRAM_TOKEN.")
        return
    if OPENROUTER_API_KEY.startswith("YOUR_"):
        logger.error("В файле не заполнен OPENROUTER_API_KEY.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("worker", worker_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler, pattern="^(admin_|worker_|main_menu)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Запуск бота...")
    app.run_polling()

if __name__ == "__main__":
    main()