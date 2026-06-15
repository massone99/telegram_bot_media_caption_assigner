import os
import logging
import asyncio
import re
import sqlite3
from io import BytesIO
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# Configura il logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- TOPIC STORAGE ---
# Durable mapping: (chat_id, normalized topic name) -> message_thread_id.
TOPIC_DB_PATH = os.getenv("TOPIC_DB_PATH", "topics.sqlite3")
TOPIC_LOCKS = {}

CREATE_TOPICS = True  # Flag to manage topic creation
DELIMITER = "##"  # Delimiter to split topic name from file name


def normalize_topic_name(topic_name):
    display_name = (topic_name or "").replace("_", " ")
    display_name = re.sub(r"\s+", " ", display_name).strip()
    topic_key = display_name.casefold()
    return display_name, topic_key


def init_topic_db():
    with sqlite3.connect(TOPIC_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_mappings (
                chat_id INTEGER NOT NULL,
                topic_key TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                message_thread_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, topic_key)
            )
            """
        )


def get_topic_mapping(chat_id, topic_key):
    with sqlite3.connect(TOPIC_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT message_thread_id, topic_name
            FROM topic_mappings
            WHERE chat_id = ? AND topic_key = ?
            """,
            (chat_id, topic_key),
        ).fetchone()
    if not row:
        return None
    return {"message_thread_id": row[0], "topic_name": row[1]}


def delete_topic_mapping(chat_id, topic_key):
    with sqlite3.connect(TOPIC_DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM topic_mappings
            WHERE chat_id = ? AND topic_key = ?
            """,
            (chat_id, topic_key),
        )


def save_topic_mapping(chat_id, topic_name, message_thread_id):
    display_name, topic_key = normalize_topic_name(topic_name)
    with sqlite3.connect(TOPIC_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO topic_mappings (chat_id, topic_key, topic_name, message_thread_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, topic_key) DO UPDATE SET
                topic_name = excluded.topic_name,
                message_thread_id = excluded.message_thread_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, topic_key, display_name, message_thread_id),
        )
    return display_name, topic_key


def get_topic_lock(chat_id, topic_key):
    key = (chat_id, topic_key)
    if key not in TOPIC_LOCKS:
        TOPIC_LOCKS[key] = asyncio.Lock()
    return TOPIC_LOCKS[key]


def is_message_thread_not_found(error):
    return "Message thread not found" in str(error)


async def get_target_thread_id(bot, chat_id, topic_name):
    """
    Cerca se esiste già un topic nel DB locale, altrimenti lo crea.
    Restituisce il thread_id del topic.
    """
    display_name, topic_key = normalize_topic_name(topic_name)
    if not topic_key:
        logging.warning("Topic vuoto dopo normalizzazione, fallback sul thread corrente.")
        return None

    # Fast path: persistent DB lookup.
    mapping = get_topic_mapping(chat_id, topic_key)
    if mapping:
        return mapping["message_thread_id"]

    # Slow path: serialize creation per topic to avoid duplicate topics during batches.
    async with get_topic_lock(chat_id, topic_key):
        mapping = get_topic_mapping(chat_id, topic_key)
        if mapping:
            return mapping["message_thread_id"]

        try:
            logging.info(f"Tentativo di creazione Topic: '{display_name}'...")
            new_topic = await bot.create_forum_topic(chat_id=chat_id, name=display_name)
            save_topic_mapping(chat_id, display_name, new_topic.message_thread_id)
            logging.info(
                f"Topic '{display_name}' creato con successo "
                f"(ID: {new_topic.message_thread_id})"
            )
            return new_topic.message_thread_id
        except Exception as e:
            logging.error(f"Impossibile creare il topic '{display_name}'. Errore: {e}")
            # Se fallisce (es. permessi mancanti), usa il thread corrente come fallback.
            return None


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return False
    try:
        member = await context.bot.get_chat_member(msg.chat_id, user.id)
    except Exception as e:
        logging.error(f"Impossibile verificare admin: {e}")
        return False
    return member.status in ("administrator", "creator")


async def bind_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    if not await is_admin(update, context):
        await msg.reply_text("Solo admin.")
        return

    if not msg.message_thread_id:
        await msg.reply_text("Usa /bindtopic dentro al topic Telegram da collegare.")
        return

    topic_name = " ".join(context.args).strip()
    display_name, topic_key = normalize_topic_name(topic_name)
    if not topic_key:
        await msg.reply_text("Uso: /bindtopic Nome Topic")
        return

    save_topic_mapping(msg.chat_id, display_name, msg.message_thread_id)
    await msg.reply_text(
        f"Topic collegato: {display_name} -> thread {msg.message_thread_id}"
    )

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    bot = context.bot
    chat_id = msg.chat_id

    # 1. Controllo Loop
    if msg.from_user and msg.from_user.id == bot.id:
        return

    # 2. Identifica Video, Audio, Documento o Foto/Immagine
    media_obj = msg.video or msg.audio or msg.document or (msg.photo[-1] if msg.photo else None)
    
    if not media_obj:
        return

    media_kind = "document"
    if msg.video:
        media_kind = "video"
    elif msg.photo:
        media_kind = "photo"
    elif msg.audio:
        media_kind = "audio"
    elif msg.document:
        mime = getattr(msg.document, "mime_type", "")
        if mime and mime.startswith("video/"):
            media_kind = "video"
        elif mime and mime.startswith("image/"):
            media_kind = "image"

    # 3. Prepara il nome file e Logica Routing
    full_file_name = getattr(media_obj, "file_name", None) or (
        (msg.caption.strip() if msg.caption and msg.caption.strip() else None)
        if media_kind in ("photo", "image")
        else None
    ) or (
        "Video_User"
        if media_kind == "video"
        else ("Photo_User" if media_kind in ("photo", "image") else None)
    ) or (
        "Photo_User"
        if media_kind in ("photo", "image")
        else ("Audio_User" if media_kind == "audio" else "File_User")
    )
    name_without_ext = os.path.splitext(full_file_name)[0]
    
    # --- LOGICA DI SEPARAZIONE (Split su DELIMITER) ---
    if DELIMITER in name_without_ext and CREATE_TOPICS:
        # Divide solo alla prima occorrenza. Es: "Vacanze##Video_1" -> prefix="Vacanze", real_name="Video_1"
        prefix, real_name = name_without_ext.split(DELIMITER, 1)
        clean_caption = real_name.strip()
        topic_name = prefix.strip()
    else:
        # Nessun prefisso, nessuna gestione topic speciale
        topic_name = None
        clean_caption = name_without_ext
    
    logging.info(f"File: {full_file_name} -> Topic Target: {topic_name} | Caption: {clean_caption}")
    logging.info(f"Chat type: {msg.chat.type} | is_forum: {msg.chat.is_forum} | thread_id: {msg.message_thread_id}")

    # Determina dove mandare il file
    target_thread_id = msg.message_thread_id # Default: dove siamo ora
    
    # Se siamo in un gruppo (supergroup) e abbiamo trovato un prefisso per il topic
    is_forum = bool(msg.chat.is_forum)
    if is_forum and topic_name:
        found_id = await get_target_thread_id(bot, chat_id, topic_name)
        if found_id:
            target_thread_id = found_id
        else:
            logging.warning("Fallback sul topic corrente (Creazione fallita o errore).")
    logging.info(f"Target thread_id: {target_thread_id}")

    # 4. Processa il media
    async def send_media_to_thread(message_thread_id):
        if media_kind == "video":
            await bot.send_video(
                chat_id=chat_id,
                video=media_obj.file_id,
                caption=clean_caption, # Mettiamo solo il nome pulito
                supports_streaming=True,
                message_thread_id=message_thread_id, # <--- Qui avviene la magia del routing
            )
        elif media_kind == "photo":
            if not topic_name and not getattr(media_obj, "file_name", None):
                logging.warning(
                    "Foto senza nome file/caption con delimiter: impossibile dedurre il topic. "
                    "Inviala come file/documento o usa caption 'Topic##Nome'."
                )
            await bot.send_photo(
                chat_id=chat_id,
                photo=media_obj.file_id,
                caption=clean_caption,
                message_thread_id=message_thread_id,
            )
        elif media_kind in ("audio", "document", "image"):
            # Per audio/documento rifacciamo upload con nome file pulito,
            # altrimenti Telegram mantiene il nome originale legato al file_id.
            _, ext = os.path.splitext(full_file_name)
            clean_file_name = f"{clean_caption}{ext}" if ext else clean_caption

            telegram_file = await bot.get_file(media_obj.file_id)
            file_bytes = await telegram_file.download_as_bytearray()
            payload = InputFile(BytesIO(file_bytes), filename=clean_file_name)

            if media_kind == "audio":
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=payload,
                    caption=clean_caption,
                    message_thread_id=message_thread_id,
                )
            elif media_kind == "image":
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=payload,
                    caption=clean_caption,
                    message_thread_id=message_thread_id,
                )
            else:
                await bot.send_document(
                    chat_id=chat_id,
                    document=payload,
                    caption=clean_caption,
                    message_thread_id=message_thread_id,
                )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=media_obj.file_id,
                caption=clean_caption,
                message_thread_id=message_thread_id,
            )

    send_succeeded = False
    try:
        await send_media_to_thread(target_thread_id)
        send_succeeded = True
    except Exception as e:
        logging.error(f"Errore download/send: {e}")
        if is_forum and topic_name and is_message_thread_not_found(e):
            display_name, topic_key = normalize_topic_name(topic_name)
            logging.warning(
                f"Mapping topic non valido: {display_name} -> {target_thread_id}. "
                "Elimino mapping e ritento."
            )
            delete_topic_mapping(chat_id, topic_key)
            retry_thread_id = await get_target_thread_id(bot, chat_id, topic_name)
            if retry_thread_id:
                target_thread_id = retry_thread_id
                try:
                    logging.info(f"Retry su nuovo thread_id: {retry_thread_id}")
                    await send_media_to_thread(retry_thread_id)
                    send_succeeded = True
                except Exception as retry_error:
                    logging.error(f"Retry dopo refresh topic fallito: {retry_error}")
            else:
                target_thread_id = msg.message_thread_id

    if not send_succeeded:
        # Fallback Copy Message
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=msg.message_id,
                caption=clean_caption,
                message_thread_id=target_thread_id, # Usiamo comunque il topic calcolato se possibile
            )
        except Exception as e2:
            logging.error(f"Errore totale fallback: {e2}")
            return

    # 5. Cancella originale
    try:
        await msg.delete()
    except Exception as e:
        logging.error(f"Delete error: {e}")

def main():
    if not BOT_TOKEN:
        print("Errore: TELEGRAM_BOT_TOKEN non impostato.")
        return

    init_topic_db()

    app = Application.builder().token(BOT_TOKEN).build()

    media_filter = (filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.Document.ALL)
    app.add_handler(CommandHandler("bindtopic", bind_topic))
    app.add_handler(MessageHandler(media_filter, handle_media))

    if CREATE_TOPICS:
        print("Bot avviato con gestione Topic attiva (creazione automatica).")
    else:
        print("Bot avviato senza gestione Topic (tutti i video rimarranno nel topic corrente).")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
