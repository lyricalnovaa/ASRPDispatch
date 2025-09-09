import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from dotenv import load_dotenv
from keep_alive import keep_alive
from discord.ext import voice_recv  # the fork you installed

# === CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 123456789012345678,  # replace with your VC
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
unit_status = {}  # callsign -> status (10-8, 10-7, etc.)
audio_buffers = {}  # user.id -> bytearray

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
            callsign = user.display_name[:5]

            if "10-8" in text:
                unit_status[callsign] = "10-8"
                await speak(f"{callsign} is now 10-8")
            elif "10-7" in text:
                unit_status[callsign] = "10-7"
                await speak(f"{callsign} is now 10-7")
            elif "10-6" in text:
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

            print(f"[HEARD] {callsign}: {text}")
        except Exception as e:
            print(f"[STT ERROR] {e}")
        finally:
            os.remove(pcm_path)
            os.remove(wav_path)

# === VOICE RECEIVING TASK ===
async def listen_voice():
    global voice_client_ref
    if not voice_client_ref:
        return
    async for user, pcm in voice_recv.recv_audio(voice_client_ref):
        await handle_pcm(user, pcm)

# === BOT COMMANDS ===
@bot.command()
async def start(ctx):
    global voice_client_ref, listening_task
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("Invalid VC ID.")
        return
    voice_client_ref = await channel.connect()
    listening_task = bot.loop.create_task(listen_voice())
    bot_callsign = bot.user.display_name[:5]
    await speak(f"{bot_callsign} 10-8")
    await ctx.send(f"Dispatcher online as {bot_callsign}!")

@bot.command()
async def stop(ctx):
    global voice_client_ref, listening_task
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
    if listening_task:
        listening_task.cancel()
    await ctx.send("Dispatcher stopped.")

# === START BOT ===
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)