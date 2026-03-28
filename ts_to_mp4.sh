#!/bin/bash
# Simple Bash script to convert all .ts files to MP4 with GUI folder selection

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ ffmpeg non trovato! Installalo prima di usare questo script."
    if command -v zenity &> /dev/null; then
        zenity --error --text="❌ ffmpeg non trovato!\n\nInstallalo con:\nsudo apt install ffmpeg"
    fi
    exit 1
fi

# Get input directory
INPUT_DIR="$1"

# If no argument provided, try to use GUI
if [ -z "$INPUT_DIR" ]; then
    if command -v zenity &> /dev/null; then
        # Use zenity for GUI folder selection
        INPUT_DIR=$(zenity --file-selection --directory --title="Seleziona cartella con file .ts")
        
        # Check if user canceled
        if [ $? -ne 0 ] || [ -z "$INPUT_DIR" ]; then
            echo "❌ Selezione annullata"
            exit 0
        fi
    else
        # No GUI available, use current directory
        echo "⚠️  Zenity non trovato. Uso la cartella corrente."
        echo "   Installa zenity per la GUI: sudo apt install zenity"
        INPUT_DIR="."
    fi
fi

# Check if directory exists
if [ ! -d "$INPUT_DIR" ]; then
    echo "❌ Directory non valida: $INPUT_DIR"
    exit 1
fi

# Count .ts files
TS_COUNT=$(find "$INPUT_DIR" -maxdepth 1 -iname "*.ts" | wc -l)

if [ "$TS_COUNT" -eq 0 ]; then
    MSG="⚠️  Nessun file .ts trovato in:\n$INPUT_DIR"
    echo "$MSG"
    if command -v zenity &> /dev/null; then
        zenity --warning --text="$MSG"
    fi
    exit 0
fi

# Show confirmation dialog
MSG_CONFIRM="📁 Trovati $TS_COUNT file .ts in:\n$INPUT_DIR\n\n🎬 Qualità: Medium (H.264, CRF 23)\n\n▶️ Vuoi procedere con la conversione?"
echo "📁 Trovati $TS_COUNT file .ts"

if command -v zenity &> /dev/null; then
    if ! zenity --question --text="$MSG_CONFIRM" --title="Conferma Conversione" --width=400; then
        echo "❌ Conversione annullata dall'utente"
        exit 0
    fi
fi

echo "🚀 Inizio conversione..."
echo ""

# Counter
SUCCESS=0
FAILED=0
CURRENT=0

# Function to convert a single file
convert_file() {
    local ts_file="$1"
    local filename=$(basename "$ts_file")
    local basename="${filename%.*}"
    local mp4_file="$INPUT_DIR/${basename}.mp4"
    
    echo "📹 Conversione: $filename"
    
    if ffmpeg -i "$ts_file" \
        -c:v libx264 \
        -preset medium \
        -crf 23 \
        -c:a aac \
        -b:a 192k \
        -movflags +faststart \
        -n \
        "$mp4_file" \
        -loglevel error -stats 2>&1; then
        echo "  ✅ Completato: ${basename}.mp4"
        return 0
    else
        echo "  ❌ Errore durante la conversione"
        return 1
    fi
}

# Check if zenity is available for progress bar
USE_GUI=false
if command -v zenity &> /dev/null; then
    USE_GUI=true
fi

# Convert with or without GUI progress
if [ "$USE_GUI" = true ]; then
    # Convert with progress bar
    (
        for ts_file in "$INPUT_DIR"/*.ts "$INPUT_DIR"/*.TS; do
            [ -e "$ts_file" ] || continue
            
            CURRENT=$((CURRENT + 1))
            PERCENT=$((CURRENT * 100 / TS_COUNT))
            
            filename=$(basename "$ts_file")
            echo "$PERCENT"
            echo "# [$CURRENT/$TS_COUNT] Conversione: $filename"
            
            if convert_file "$ts_file" > /dev/null 2>&1; then
                SUCCESS=$((SUCCESS + 1))
            else
                FAILED=$((FAILED + 1))
            fi
        done
        echo "100"
        echo "# Conversione completata!"
    ) | zenity --progress \
        --title="Conversione TS → MP4" \
        --text="Inizializzazione..." \
        --percentage=0 \
        --auto-close \
        --width=400
    
    # Check if user canceled
    if [ $? -ne 0 ]; then
        echo ""
        echo "❌ Conversione annullata dall'utente"
        exit 0
    fi
else
    # Convert without progress bar (console only)
    for ts_file in "$INPUT_DIR"/*.ts "$INPUT_DIR"/*.TS; do
        [ -e "$ts_file" ] || continue
        
        CURRENT=$((CURRENT + 1))
        echo ""
        echo "[$CURRENT/$TS_COUNT]"
        
        if convert_file "$ts_file"; then
            SUCCESS=$((SUCCESS + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done
fi

echo ""

echo "=================================================="
echo "📊 RIEPILOGO"
echo "=================================================="
echo "✅ Successo: $SUCCESS"
echo "❌ Falliti: $FAILED"

# Show GUI summary if available
if command -v zenity &> /dev/null; then
    if [ "$FAILED" -eq 0 ]; then
        zenity --info --title="Conversione Completata" --width=350 \
            --text="✅ <b>Conversione completata con successo!</b>\n\n📊 File convertiti: $SUCCESS\n📁 Cartella: $INPUT_DIR"
    else
        zenity --warning --title="Conversione Completata con Errori" --width=350 \
            --text="⚠️ <b>Conversione completata con errori</b>\n\n✅ Successo: $SUCCESS\n❌ Falliti: $FAILED\n📁 Cartella: $INPUT_DIR"
    fi
fi