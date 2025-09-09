import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from pydub import AudioSegment
from pydub.playback import play
from dotenv import load_dotenv

 # === ENV & CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,
    "CODE_FILE": "codes.txt",
    "VOICE": "en-US-GuyNeural",
    "OUTPUT_TTS_FILE": "dispatch_tts.mp3"
}

# === BOT SETUP ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# === GLOBAL STATE ===
voice_client_ref = None
listening_task = None
recognizer = sr.Recognizer()
audio_queue = asyncio.Queue()
is_listening = False

# === LOAD CODES ===
def load_codes(file_path):
    codes = {}
    try:
        with open(file_path, "r") as f:
            for line in f:
                if ":" in line:
                    code, meaning = line.strip().split(":", 1)
                    codes[code.strip()] = meaning.strip()
    except FileNotFoundError:
        print(f"[ERROR] {file_path} not found!")
    return codes
CODES = load_codes(CONFIG["CODE_FILE"])

# === TTS HANDLER ===
async def speak(text: str):
    global voice_client_ref
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not connected to a voice channel.")
        return

    tts = edge_tts.Communicate(text, CONFIG["VOICE"])
    temp_file = CONFIG["OUTPUT_TTS_FILE"]
    await tts.save(temp_file)

    if voice_client_ref.is_playing():
            voice_client_ref.stop()

    source = discord.FFmpegPCMAudio(temp_file)
    voice_client_ref.play(source)

    while voice_client_ref.is_playing():
        await asyncio.sleep(0.1)

    try:
        os.remove(temp_file)
    except OSError as e:
        print(f"Error removing file: {e}")

# === STT HANDLER ===
async def process_audio_queue():
    global audio_queue, is_listening
    while is_listening:
        try:
            user, pcm_data = await asyncio.wait_for(audio_queue.get(), timeout=10)
            if pcm_data is None:
                continue

            # Convert Discord PCM audio to WAV for SpeechRecognition
            audio_segment = AudioSegment(
                pcm_data,
                frame_rate=48000,
                sample_width=2,
                channels=2
            ).set_frame_rate(16000).set_channels(1)
            temp_wav_file = f"temp_audio_{user.id}.wav"
            audio_segment.export(temp_wav_file, format="wav")

            with sr.AudioFile(temp_wav_file) as source:
                audio_data = recognizer.record(source)

            try:
                text = recognizer.recognize_google(audio_data).upper()
                print(f"[HEARD] {user.display_name}: {text}")

                # Check for 10-codes
                for code, meaning in CODES.items():
                    if code in text:
                        reply = f"{code} received from {user.display_name}. {meaning}."
                        print(f"[DISPATCH] {reply}")
                        await speak(reply)
                        break

                # Handle Code 1-5
                if "CODE 1" in text:
                    await speak("Copy Code 1, no lights and sirens.")
                elif "CODE 2" in text:
                    await speak("Copy Code 2, lights only.")
                elif "CODE 3" in text:
                    await speak("Copy Code 3, lights and sirens.")
                elif "CODE 5" in text:
                    await speak("Copy Code 5, felony stop. Dispatching two additional units.")

            except sr.UnknownValueError:
                print(f"[RECOGNITION] Could not understand audio from {user.display_name}")
            except sr.RequestError as e:
                print(f"[RECOGNITION ERROR] Could not request results from Google Speech Recognition service; {e}")

            # Clean up temp file
            try:
                os.remove(temp_wav_file)
            except OSError as e:
                print(f"Error removing file: {e}")

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"[PROCESS ERROR] {e}")

def on_audio_receive(user, pcm_data):
    if is_listening:
        asyncio.run_coroutine_threadsafe(audio_queue.put((user, pcm_data)), bot.loop)

# === BOT EVENTS ===
@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")

@bot.event
async def on_voice_state_update(member, before, after):
    global voice_client_ref, listening_task, is_listening
    if member == bot.user and before.channel and not after.channel:
        # Bot has been disconnected, clean up
        print("[DISCONNECT] Bot was disconnected from voice channel. Cleaning up.")
        if listening_task:
            listening_task.cancel()
            await asyncio.gather(listening_task, return_exceptions=True)
            listening_task = None
        is_listening = False
        voice_client_ref = None

# === COMMANDS ===
@bot.command(name="start")
async def start_dispatcher(ctx):
    global voice_client_ref, listening_task, is_listening
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])

    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("❌ RTO channel ID is invalid or not a voice channel.")
        return

    if is_listening:
        await ctx.send("📻 Dispatcher is already live.")
        return

    try:
        voice_client_ref = await channel.connect()
        voice_client_ref.listen(on_audio_receive)
        is_listening = True
        listening_task = bot.loop.create_task(process_audio_queue())
        await ctx.send("✅ Dispatcher is now online and listening!")
    except Exception as e:
        await ctx.send(f"❌ Could not connect to the voice channel: {e}")
        print(f"[ERROR] Failed to start dispatcher: {e}")

@bot.command(name="stop")
async def stop_dispatcher(ctx):
    global voice_client_ref, listening_task, is_listening
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
        voice_client_ref = None
        if listening_task:
            listening_task.cancel()
            await asyncio.gather(listening_task, return_exceptions=True)
            listening_task = None
        is_listening = False
        await ctx.send("✅ Dispatcher has been shut down.")
    else:
        await ctx.send("❌ Dispatcher is not currently running.")

@bot.command(name="code")
async def code_lookup(ctx, *, query: str):
    query = query.strip().upper()
    meaning = CODES.get(query)
    if meaning:
        await ctx.send(f"📻 **{query}** → {meaning}")
    else:
        await ctx.send(f"⚠️ Code `{query}` not found.")

# === START ===
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN environment variable is not set. Exiting.")
    else:
        bot.run(TOKEN)