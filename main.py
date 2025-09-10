import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from dotenv import load_dotenv
from keep_alive import keep_alive

# === CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,  # your VC
    "BOT_CALLSIGN": "2D-00",
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
unit_status = {}  # callsign -> status
audio_buffers = {}  # user.id -> bytearray

# === PHONETIC ALPHABET ===
PHONETIC = {
    "ALPHA": "A", "BRAVO": "B", "CHARLIE": "C", "DAVID": "D",
    "ECHO": "E", "FOXTROT": "F", "GOLF": "G", "HOTEL": "H",
    "INDIA": "I", "JULIET": "J", "KILO": "K", "LIMA": "L",
    "MIKE": "M", "NOVEMBER": "N", "OSCAR": "O", "PAPA": "P",
    "QUEBEC": "Q", "ROMEO": "R", "SIERRA": "S", "TANGO": "T",
    "UNIFORM": "U", "VICTOR": "V", "WHISKEY": "W", "XRAY": "X",
    "YANKEE": "Y", "ZULU": "Z",
    "ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4",
    "FIVE": "5", "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9",
    "DOUBLE": "", "TRIPLE": ""
}

def parse_callsign(text):
    words = text.upper().split()
    result = ""
    i = 0
    while i < len(words):
        w = words[i]
        if w == "DOUBLE" and i + 1 < len(words):
            letter = PHONETIC.get(words[i + 1], words[i + 1])
            result += letter * 2
            i += 2
        elif w == "TRIPLE" and i + 1 < len(words):
            letter = PHONETIC.get(words[i + 1], words[i + 1])
            result += letter * 3
            i += 2
        else:
            result += PHONETIC.get(w, w)
            i += 1
    return result

# === TTS FUNCTION ===
async def speak(text: str):
    global voice_client_ref
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not in VC.")
        return
    tts = edge_tts.Communicate(text, CONFIG["VOICE"])
    await tts.save(CONFIG["OUTPUT_TTS_FILE"])
    if voice_client_ref.is_playing():
        voice_client_ref.stop()
    voice_client_ref.play(discord.FFmpegPCMAudio(CONFIG["OUTPUT_TTS_FILE"]))
    while voice_client_ref.is_playing():
        await asyncio.sleep(0.1)
    try:
        os.remove(CONFIG["OUTPUT_TTS_FILE"])
    except OSError:
        pass

# === AUDIO HANDLER ===
async def handle_pcm(user: discord.Member, pcm_data: bytes):
    user_id = user.id
    audio_buffers.setdefault(user_id, bytearray())
    audio_buffers[user_id] += pcm_data

    # ~3 seconds of stereo PCM at 48kHz * 2 bytes/sample * 2 channels
    if len(audio_buffers[user_id]) >= 48000 * 2 * 2 * 3:
        pcm_path = f"temp_{user_id}.pcm"
        wav_path = f"temp_{user_id}.wav"
        with open(pcm_path, "wb") as f:
            f.write(audio_buffers[user_id])
        audio_buffers[user_id].clear()

        os.system(f"ffmpeg -f s16le -ar 48000 -ac 2 -i {pcm_path} {wav_path} -y")
        try:
            with sr.AudioFile(wav_path) as source:
                audio = recognizer.record(source)
            text = recognizer.recognize_google(audio).upper()
            callsign = parse_callsign(text)

            # Check for test "HI"
            if "HI" in text:
                await speak(f"Hello {callsign}!")

            # Check for unit commands
            if "10-8" in text or "TEN EIGHT" in text:
                unit_status[callsign] = "10-8"
                await speak(f"{callsign} is now 10-8")
            elif "10-7" in text or "TEN SEVEN" in text:
                unit_status[callsign] = "10-7"
                await speak(f"{callsign} is now 10-7")
            elif "10-6" in text or "TEN SIX" in text:
                unit_status[callsign] = "10-6"
                await speak(f"{callsign} is now 10-6")
            elif "DISPATCH" in text and "NEED UNIT" in text:
                available = [c for c, s in unit_status.items() if s == "10-8"]
                if available:
                    assigned = available[0]
                    unit_status[assigned] = "10-6"
                    await speak(f"Dispatching {assigned} to the call.")
                else:
                    await speak("No units are currently available.")
            if "10-11" in text or "ten eleven" in text:
                await speak(f"10 4 proceed with caution")
            print(f"[HEARD] {callsign}: {text}")
        except Exception as e:
            print(f"[STT ERROR] {e}")
        finally:
            os.remove(pcm_path)
            os.remove(wav_path)

# === BOT COMMANDS ===
@bot.command()
async def start(ctx):
    global voice_client_ref, listening_task
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("Invalid VC ID.")
        return
    
    voice_client_ref = await channel.connect()
    await speak(f"2 David Double O show me 10-8 active dispatch")
    await ctx.send(f"Dispatcher online as {CONFIG['BOT_CALLSIGN']}!")

    # Start listening immediately
    listening_task = bot.loop.create_task(listen_voice())
@bot.command()
async def stop(ctx):
    global voice_client_ref
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
    await ctx.send("Dispatcher stopped.")

# === VOICE RECEIVE TASK ===
async def listen_voice():
    global voice_client_ref
    if not voice_client_ref:
        return
    import voice_recv  # the forked library
    async for user, pcm in voice_recv.recv_audio(voice_client_ref):
        await handle_pcm(user, pcm)

# Start listening when bot joins
@bot.event
async def on_ready():
    global listening_task
    print(f"[READY] Logged in as {bot.user}")
    if voice_client_ref and not listening_task:
        listening_task = bot.loop.create_task(listen_voice())

# === START BOT ===
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)