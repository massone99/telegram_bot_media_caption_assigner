import os
import logging
from io import BytesIO
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import (
    Application,
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

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- CACHE PER I TOPIC ---
# Dizionario per memorizzare l'associazione "Nome Topic" -> "Thread ID" per ogni Chat ID
# Struttura: { (chat_id, "NomeTopic"): thread_id }
TOPIC_CACHE = {}

CREATE_TOPICS = True  # Flag to manage topic creation
DELIMITER = "##"  # Delimiter to split topic name from file name

async def get_target_thread_id(bot, chat_id, topic_name):
    """
    Cerca se esiste già un topic in cache, altrimenti lo crea.
    Restituisce il thread_id del topic.
    """
    key = (chat_id, topic_name)
    
    # 1. Controlla nella cache se lo conosciamo già
    if key in TOPIC_CACHE:
        return TOPIC_CACHE[key]

    # 2. Se non esiste in cache, proviamo a crearlo su Telegram
    try:
        logging.info(f"Tentativo di creazione Topic: '{topic_name}'...")
        new_topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
        
        # Salviamo l'ID in cache per i prossimi file
        TOPIC_CACHE[key] = new_topic.message_thread_id
        logging.info(f"Topic '{topic_name}' creato con successo (ID: {new_topic.message_thread_id})")
        
        # Opzionale: Manda un messaggio di separazione/benvenuto nel nuovo topic
        # await bot.send_message(chat_id=chat_id, message_thread_id=new_topic.message_thread_id, text=f"📂 Inizio raccolta: {topic_name}")
        
        return new_topic.message_thread_id
    except Exception as e:
        logging.error(f"Impossibile creare il topic '{topic_name}'. Errore: {e}")
        # Se fallisce (es. permessi mancanti o topic già esistente ma non in cache), 
        # ritorna None per usare il thread corrente come fallback
        return None

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    bot = context.bot
    chat_id = msg.chat_id

    # 1. Controllo Loop
    if msg.from_user and msg.from_user.id == bot.id:
        return

    # 2. Identifica Video, Audio, Documento o Foto
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

    # 3. Prepara il nome file e Logica Routing
    full_file_name = getattr(media_obj, "file_name", None) or (
        (msg.caption.strip() if msg.caption and msg.caption.strip() else None)
        if media_kind == "photo"
        else None
    ) or (
        "Video_User"
        if media_kind == "video"
        else ("Photo_User" if media_kind == "photo" else None)
    ) or (
        "Photo_User"
        if media_kind == "photo"
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
    is_forum = msg.chat.is_forum or (msg.chat.type == "supergroup")
    if is_forum and topic_name:
        found_id = await get_target_thread_id(bot, chat_id, topic_name)
        if found_id:
            target_thread_id = found_id
        else:
            logging.warning("Fallback sul topic corrente (Creazione fallita o errore).")

    # 4. Processa il media
    try:
        if media_kind == "video":
            await bot.send_video(
                chat_id=chat_id,
                video=media_obj.file_id,
                caption=clean_caption, # Mettiamo solo il nome pulito
                supports_streaming=True,
                message_thread_id=target_thread_id, # <--- Qui avviene la magia del routing
            )
        elif media_kind == "photo":
            await bot.send_photo(
                chat_id=chat_id,
                photo=media_obj.file_id,
                caption=clean_caption,
                message_thread_id=target_thread_id,
            )
        elif media_kind in ("audio", "document"):
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
                    message_thread_id=target_thread_id,
                )
            else:
                await bot.send_document(
                    chat_id=chat_id,
                    document=payload,
                    caption=clean_caption,
                    message_thread_id=target_thread_id,
                )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=media_obj.file_id,
                caption=clean_caption,
                message_thread_id=target_thread_id,
            )

    except Exception as e:
        logging.error(f"Errore download/send: {e}")
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

    app = Application.builder().token(BOT_TOKEN).build()

    media_filter = (filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.Document.ALL)
    app.add_handler(MessageHandler(media_filter, handle_media))

    if CREATE_TOPICS:
        print("Bot avviato con gestione Topic attiva (creazione automatica).")
    else:
        print("Bot avviato senza gestione Topic (tutti i video rimarranno nel topic corrente).")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
