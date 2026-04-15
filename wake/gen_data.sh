#!/bin/bash
# Generate training data for Trinity wake word - lightweight shell version
# Uses Piper TTS and ffmpeg, no Python memory issues

PIPER="/home/guy/.openclaw/workspace/.venvs/piper/bin/piper"
LESSAC="/home/guy/.openclaw/workspace/models/piper/en_US-lessac-medium.onnx"
RYAN="/home/guy/.openclaw/workspace/models/piper/en_US-ryan-medium.onnx"
OUTDIR="/home/guy/.openclaw/workspace/apps/voice-ui/wake/training_data"

mkdir -p "$OUTDIR/positive" "$OUTDIR/negative"

gen() {
    local text="$1"
    local model="$2"
    local tag="$3"
    local outdir="$4"
    local raw="/tmp/piper_${tag}_raw.wav"
    local out="$outdir/${tag}.wav"

    echo "$text" | "$PIPER" --model "$model" --output_file "$raw" 2>/dev/null
    ffmpeg -y -i "$raw" -ac 1 -ar 16000 -f wav "$out" -loglevel quiet 2>/dev/null
    rm -f "$raw"
    echo "  $tag"
}

echo "=== Generating positives ==="
POS_PHRASES=("Trinity" "Hey Trinity" "Trinity can you hear me" "Trinity what time is it" \
    "Trinity turn on the lights" "OK Trinity" "Yo Trinity" "Trinity help me" \
    "Trinity what is the weather" "Hey Trinity play some music" \
    "Trinity good morning" "Trinity set a timer")

i=0
for phrase in "${POS_PHRASES[@]}"; do
    gen "$phrase" "$LESSAC" "pos_${i}_lessac" "$OUTDIR/positive"
    i=$((i+1))
    gen "$phrase" "$RYAN" "pos_${i}_ryan" "$OUTDIR/positive"
    i=$((i+1))
done
echo "Generated $i positive clips"

echo "=== Generating negatives ==="
NEG_PHRASES=("What time is it" "Turn on the lights" "Play some music" \
    "What is the weather like" "Set a timer for five minutes" "Call mom" \
    "Send a message" "How are you doing today" "Tell me a joke" "Good morning" \
    "What is on my calendar" "Remind me to buy groceries" "Navigate to the store" \
    "Read my emails" "Turn off the TV" "Open the garage" "Lock the door" \
    "What is the news today" "How tall is Mount Everest" "Calculate fifteen percent" \
    "Translate hello to Spanish" "Define serendipity" "Who won the game" \
    "Is it going to rain tomorrow" "Set the thermostat to seventy two" \
    "Dim the bedroom lights" "Start the robot vacuum" "Order more coffee" \
    "Check my bank balance" "Find a recipe for pasta")

j=0
for phrase in "${NEG_PHRASES[@]}"; do
    model="$LESSAC"
    [ $((j % 2)) -eq 1 ] && model="$RYAN"
    gen "$phrase" "$model" "neg_${j}" "$OUTDIR/negative"
    j=$((j+1))
done
echo "Generated $j negative clips"
echo "Total: $i positive + $j negative"
