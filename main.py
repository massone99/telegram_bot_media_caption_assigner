import os
import logging
import tempfile
import cv2
from dotenv import load_dotenv
from telegram import Update
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

CREATE_TOPICS = False # Flag to manage topic creation

def extract_thumbnail(video_path, thumb_path):
    """Estrae un frame al 5° secondo."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False
    
    cap.set(cv2.CAP_PROP_POS_MSEC, 5000)
    success, frame = cap.read()
    
    if not success:
        cap.set(cv2.CAP_PROP_POS_MSEC, 0)
        success, frame = cap.read()

    if success:
        # Qualità JPEG 75 per stare leggeri
        cv2.imwrite(thumb_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    
    cap.release()
    return success

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

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    bot = context.bot
    chat_id = msg.chat_id

    # 1. Controllo Loop
    if msg.from_user and msg.from_user.id == bot.id:
        return

    # 2. Identifica Video o Documento
    video_obj = msg.video or msg.document
    
    if not video_obj:
        return

    if msg.document:
        mime = getattr(msg.document, "mime_type", "")
        if not mime or not mime.startswith("video/"):
            return 

    # 3. Prepara il nome file e Logica Routing
    full_file_name = getattr(video_obj, "file_name", None) or "Video_User"
    name_without_ext = os.path.splitext(full_file_name)[0]
    
    # --- LOGICA DI SEPARAZIONE (Split su "_") ---
    if "_" in name_without_ext and CREATE_TOPICS:
        # Divide solo alla prima occorrenza. Es: "Vacanze_Video_1" -> prefix="Vacanze", real_name="Video_1"
        prefix, real_name = name_without_ext.split("_", 1)
        clean_caption = real_name.strip()
        topic_name = prefix.strip()
    else:
        # Nessun prefisso, nessuna gestione topic speciale
        topic_name = None
        clean_caption = name_without_ext
    
    logging.info(f"File: {full_file_name} -> Topic Target: {topic_name} | Caption: {clean_caption}")

    # Determina dove mandare il file
    target_thread_id = msg.message_thread_id # Default: dove siamo ora
    
    # Se siamo in un gruppo Forum (Topic abilitati) e abbiamo trovato un prefisso
    if msg.chat.is_forum and topic_name:
        found_id = await get_target_thread_id(bot, chat_id, topic_name)
        if found_id:
            target_thread_id = found_id
        else:
            logging.warning("Fallback sul topic corrente (Creazione fallita o errore).")

    # 4. Processa il video
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "temp_video.mp4")
            thumb_path = os.path.join(temp_dir, "thumb.jpg")

            # Scarica
            new_file = await bot.get_file(video_obj.file_id)
            await new_file.download_to_drive(video_path)

            # Thumbnail
            has_thumb = extract_thumbnail(video_path, thumb_path)
            thumb_arg = open(thumb_path, 'rb') if has_thumb else None
            
            try:
                await bot.send_video(
                    chat_id=chat_id,
                    video=video_obj.file_id, 
                    caption=clean_caption, # Mettiamo solo il nome pulito
                    thumbnail=thumb_arg,
                    supports_streaming=True,
                    message_thread_id=target_thread_id, # <--- Qui avviene la magia del routing
                )
            finally:
                if thumb_arg:
                    thumb_arg.close()

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

    media_filter = (filters.VIDEO | filters.Document.ALL)
    app.add_handler(MessageHandler(media_filter, handle_video))

    print("Bot avviato con gestione Topic...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()