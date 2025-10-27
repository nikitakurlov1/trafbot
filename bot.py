
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

# ----------------- CONFIG (вставьте свои значения) -----------------
TELEGRAM_TOKEN = "8422360803:AAG44u_upD1NCaxOdRR9rfy648xYgdeNsdo"
OPENROUTER_API_KEY = "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"
# telegram numeric id админа (например 123456789)
ADMIN_ID = 7474193095

# OpenRouter endpoint и модель — при необходимости поменяйте
OPENROUTER_URL = "https://api.openrouter.ai/v1/chat/completions"
OPENROUTER_MODEL = "MiniMax: MiniMax M2"  # пример
# System prompt (тот, который вы прислали)
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
# --------------------------------------------------------------------

DB_PATH = Path("conversations.sqlite")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ----------------- База данных -----------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, -- telegram user id
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
            role TEXT, -- user / assistant / system
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


def get_users() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, username, first_name, last_name, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(id=r[0], username=r[1], first_name=r[2], last_name=r[3], created_at=r[4]) for r in rows]


def get_conversation(user_id: int) -> List[Dict[str, str]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT role, content, created_at FROM messages WHERE user_id = ? ORDER BY id ASC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(role=r[0], content=r[1], created_at=r[2]) for r in rows]


# ----------------- OpenRouter interaction -----------------

def call_openrouter(messages: List[Dict[str, str]]) -> str:
    """Отправляет список сообщений (в формате role/content) в OpenRouter и возвращает ответ текста."""
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
        # структура ответа может отличаться в зависимости от провайдера/версии
        # ожидаем, что ответ содержит choices[0].message.content
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0].get("message", {}).get("content")
            if isinstance(content, dict):
                # иногда content может быть {'type':'text','parts':[...]} или подобным
                return "\n".join(content.get("parts", [])) if content.get("parts") else str(content)
            return content or ""
        # fallback
        return json.dumps(data, ensure_ascii=False)[:2000]
    except Exception as e:
        logger.exception("OpenRouter request failed")
        return f"Извините, произошла ошибка при обращении к AI: {e}"


# ----------------- Telegram handlers -----------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, getattr(user, "username", ""), user.first_name, getattr(user, "last_name", ""))
    save_message(user.id, "system", SYSTEM_PROMPT["content"])  # сохраняем system prompt (одноразово)

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📝 Задать вопрос"), KeyboardButton("📞 Связаться со специалистом")]],
        resize_keyboard=True,
    )

    await update.message.reply_html(
        f"Привет, <b>{html.escape(user.first_name or user.username or 'инвестор')}</b>! 👋\n"
        "Я — ваш помощник в мире инвестиций. Я помогу вам разобраться в мире инвестиций, с чего хотели бы начать?",
        reply_markup=kb,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Просто пиши — я расскажу, как начать инвестировать")
1

async def worker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Доступно только администратору.")
        return

    users = get_users()
    if not users:
        await update.message.reply_text("Пока нет пользователей.")
        return

    keyboard = []
    for u in users:
        title = u["username"] or f"{u['first_name']} {u['last_name']}".strip() or str(u["id"])
        keyboard.append([InlineKeyboardButton(title, callback_data=f"worker_user:{u['id']}")])

    await update.message.reply_text("Пользователи:", reply_markup=InlineKeyboardMarkup(keyboard))


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data and data.startswith("worker_user:"):
        target_id = int(data.split(":", 1)[1])
        # Кнопки: копировать тег (отправим его в чат админа), прочитать диалог
        user_row = None
        for u in get_users():
            if u["id"] == target_id:
                user_row = u
                break
        if not user_row:
            await query.edit_message_text("Пользователь не найден.")
            return

        title = user_row["username"] or f"{user_row['first_name']} {user_row['last_name']}".strip() or str(user_row["id"]) 
        keyboard = [
            [InlineKeyboardButton("📋 Отправить тег", callback_data=f"worker_copy:{target_id}"),
             InlineKeyboardButton("📖 Прочитать диалог", callback_data=f"worker_read:{target_id}")],
        ]
        await query.edit_message_text(f"Пользователь: {title}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data and data.startswith("worker_copy:"):
        target_id = int(data.split(":", 1)[1])
        # найти username
        u = next((x for x in get_users() if x["id"] == target_id), None)
        if not u:
            await query.edit_message_text("Пользователь не найден.")
            return
        tag = f"@{u['username']}" if u["username"] else f"tg://user?id={u['id']}"
        await query.edit_message_text(f"Тег пользователя: <code>{tag}</code>")

    elif data and data.startswith("worker_read:"):
        target_id = int(data.split(":", 1)[1])
        conv = get_conversation(target_id)
        if not conv:
            await query.edit_message_text("Диалог пуст.")
            return
        # Форматируем диалог в текстовый файл и отправляем
        lines = []
        for m in conv:
            ts = m.get("created_at", "")
            role = m.get("role")
            content = m.get("content")
            lines.append(f"[{ts}] {role.upper()}: {content}")
        txt = "\n\n".join(lines)
        # Если большой — отправим как файл
        if len(txt) > 3500:
            p = Path("tmp_dialog.txt")
            p.write_text(txt, encoding="utf-8")
            await query.edit_message_text("Отправляю диалог как файл...")
            await query.message.reply_document(document=InputFile(str(p)), filename=f"dialog_{target_id}.txt")
            p.unlink(missing_ok=True)
        else:
            # короткие диалоги — просто показать
            safe = html.escape(txt)
            await query.edit_message_text(f"<pre>{safe}</pre>", parse_mode="HTML")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    add_user(user.id, getattr(user, "username", ""), user.first_name, getattr(user, "last_name", ""))

    # Сохраняем сообщение пользователя
    save_message(user.id, "user", text)

    # Собираем контекст: system prompt + предыдущее сообщения этого пользователя
    conv = get_conversation(user.id)
    # OpenRouter expects messages list like [{role:..., content:...}, ...]
    messages = []
    # добавим system
    messages.append({"role": "system", "content": SYSTEM_PROMPT["content"]})
    # добавим последние N сообщений (user + assistant)
    for m in conv[-20:]:
        role = m["role"]
        messages.append({"role": role, "content": m["content"]})

    # добавим текущее (уже сохранено выше, но на всякий случай)
    # messages.append({"role": "user", "content": text})

    # Отправляем в OpenRouter
    await update.message.chat.action("typing")
    ai_response = call_openrouter(messages)

    # Сохраняем ответ AI
    save_message(user.id, "assistant", ai_response)

    # Ответ пользователю: красивое, короткое сообщение + кнопка перейти в @eToroTrade_Robot
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➡️ Перейти в @eToroTrade_Robot", url="https://t.me/eToroTrade_Robot")]]
    )

    # Гарантируем, что ответ не слишком длинный для одного сообщения. Если длинный — отправим файл.
    if len(ai_response) > 3000:
        p = Path(f"reply_{user.id}.txt")
        p.write_text(ai_response, encoding="utf-8")
        await update.message.reply_document(document=InputFile(str(p)), filename="response.txt", caption="Ответ от eToroTrade:")
        p.unlink(missing_ok=True)
    else:
        await update.message.reply_text(ai_response, reply_markup=kb)


# ----------------- Main -----------------

def main():
    init_db()
    if TELEGRAM_TOKEN.startswith("YOUR_"):
        logger.error("В файле не заполнен TELEGRAM_TOKEN. Пожалуйста, внесите токен и перезапустите.")
        return
    if OPENROUTER_API_KEY.startswith("YOUR_"):
        logger.error("В файле не заполнен OPENROUTER_API_KEY. Пожалуйста, внесите ключ и перезапустите.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("worker", worker_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Запуск бота...")
    app.run_polling()


if __name__ == "__main__":
    main()
