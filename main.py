import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from dotenv import load_dotenv
from keep_alive import keep_alive
from pydub import AudioSegment

# === CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,
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
recognizer = sr.Recognizer()
audio_buffers = {}  # user.id -> bytearray

# === TTS ===
async def speak(text: str):
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not in VC")
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
    except:
        pass

# === HANDLE PCM CHUNKS ===
async def handle_pcm(user: discord.Member, pcm_data: bytes):
    user_id = user.id
    audio_buffers.setdefault(user_id, bytearray())
    audio_buffers[user_id] += pcm_data

    # ~3s of stereo PCM at 48kHz
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
            print(f"[HEARD] {user.display_name}: {text}")  # <<<< THIS IS THE DEBUG PRINT

            # Example commands
            if "HI" in text:
                await speak(f"Hello {user.display_name}!")
            if "10 8" in text or "TEN EIGHT" in text:
                await speak(f"{user.display_name} is now 10-8")

        except sr.UnknownValueError:
            print(f"[DEBUG] Could not understand {user.display_name}")
        except sr.RequestError as e:
            print(f"[DEBUG] Speech Recognition failed: {e}")
        finally:
            os.remove(pcm_path)
            os.remove(wav_path)

# === VOICE LISTENER TASK ===
async def listen_voice():
    global voice_client_ref
    if not voice_client_ref:
        return

    async for user, pcm in voice_client_ref.listen():  # <- pseudo-code; replace with real PCM source
        await handle_pcm(user, pcm)

# === COMMANDS ===
@bot.command()
async def start(ctx):
    global voice_client_ref
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("Invalid VC ID")
        return

    voice_client_ref = await channel.connect()
    await asyncio.sleep(0.5)  # ensure VC ready
    await speak(f"{CONFIG['BOT_CALLSIGN']} 10-8 online!")

    # start listener task
    bot.loop.create_task(listen_voice())
    await ctx.send(f"Dispatcher online as {CONFIG['BOT_CALLSIGN']}!")

@bot.command()
async def stop(ctx):
    global voice_client_ref
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
    await ctx.send("Dispatcher stopped.")

# === RUN BOT ===
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)