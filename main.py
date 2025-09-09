import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from pydub import AudioSegment
from dotenv import load_dotenv
from transformers import pipeline
from flask import Flask
from threading import Thread

# === ENV & CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,
    "CODE_FILE": "codes.txt",
    "VOICE": "en-US-GuyNeural",
    "OUTPUT_TTS_FILE": "dispatch_tts.mp3",
    "BOT_CALLSIGN": "2D-01"
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

# === LOAD 10-CODES ===
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

# === OFFICER TRACKING BY CALLSIGN ===
officers = {}  # key: callsign, value: {"user": discord.Member, "status": str}

def set_status(callsign, user, status):
    officers[callsign] = {"user": user, "status": status}

def get_available_officers():
    return [callsign for callsign, info in officers.items() if info["status"] == "10-8"]

# === LOAD TEXT GENERATOR ===
generator = pipeline("text-generation", model="distilgpt2")

async def generate_dispatch_reply(call_text):
    available = get_available_officers()
    if not available:
        return "⚠️ No officers available to dispatch right now."
    officer = available[0]
    prompt = f"Dispatching {officer} for the following call: {call_text}"
    output = generator(prompt, max_length=50, do_sample=True, temperature=0.7)
    return output[0]["generated_text"]

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
                
                # --- 10-code handling ---
                for code, meaning in CODES.items():
                    if code in text:
                        reply = f"{code} received from {user.display_name}. {meaning}."
                        print(f"[DISPATCH] {reply}")
                        await speak(reply)
                        break

                # --- Status commands (10-8/7/6) ---
                if "TO DISPATCH SHOW ME" in text:
                    for status_code in ["10-8", "10-7", "10-6"]:
                        if status_code in text:
                            callsign = user.display_name[:5].upper()
                            set_status(callsign, user, status_code)
                            await speak(f"{callsign} set to {status_code}")
                            print(f"[STATUS] {callsign} -> {status_code}")
                            break

                # --- Dynamic dispatch for CODE 1-5 ---
                if any(code in text for code in ["CODE 1", "CODE 2", "CODE 3", "CODE 5"]):
                    reply = await generate_dispatch_reply(text)
                    print(f"[AI DISPATCH] {reply}")
                    await speak(reply)

            except sr.UnknownValueError:
                print(f"[RECOGNITION] Could not understand audio from {user.display_name}")
            except sr.RequestError as e:
                print(f"[RECOGNITION ERROR] Could not request results from Google Speech Recognition service; {e}")
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

# === KEEP ALIVE SERVER ===
app = Flask('')

@app.route('/')
def home():
    return "ASRPDispatcher Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# === BOT EVENTS ===
@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")

@bot.event
async def on_voice_state_update(member, before, after):
    global voice_client_ref, listening_task, is_listening
    if member == bot.user and before.channel and not after.channel:
        print("[DISCONNECT] Bot disconnected. Cleaning up.")
        if listening_task:
            listening_task.cancel()
            await asyncio.gather(listening_task, return_exceptions=True)
            listening_task = None
        is_listening = False
        voice_client_ref = None

# === BOT COMMANDS ===
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
        await speak(f"{CONFIG['BOT_CALLSIGN']} ASRPDispatch 10-8")
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
            asyncio.gather(listening_task, return_exceptions=True)
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

# === START BOT ===
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN environment variable is not set. Exiting.")
    else:
        keep_alive()
        bot.run(TOKEN)