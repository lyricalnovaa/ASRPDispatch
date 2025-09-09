import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from pydub import AudioSegment
from dotenv import load_dotenv
from keep_alive import keep_alive

# === ENV & CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,  # replace with your VC ID
    "VOICE": "en-US-GuyNeural",
    "OUTPUT_TTS_FILE": "dispatch_tts.mp3"
}

# === BOT SETUP ===
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# === GLOBAL STATE ===
voice_client_ref = None
recognizer = sr.Recognizer()
unit_status = {}  # callsign -> status (10-8, 10-7, etc.)
is_listening = False

# === TTS HANDLER ===
async def speak(text: str):
    global voice_client_ref
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not connected to VC.")
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
    except OSError:
        pass

# === SINK FOR VOICE RECEIVE ===
class DispatchSink(discord.sinks.RawDataSink):
    def __init__(self, *, loop):
        super().__init__(filters=None, encoding="pcm")
        self.loop = loop

    def on_packet(self, user, packet):
        if not user or not packet:
            return
        asyncio.run_coroutine_threadsafe(self.process_audio(user, packet), self.loop)

    async def process_audio(self, user, packet):
        pcm_path = f"temp_{user.id}.pcm"
        with open(pcm_path, "ab") as f:
            f.write(packet)

        # when enough audio is collected, convert + transcribe
        if os.path.getsize(pcm_path) > 48000 * 2 * 2 * 3:  # ~3s of audio
            wav_path = pcm_path.replace(".pcm", ".wav")
            os.system(f"ffmpeg -f s16le -ar 48000 -ac 2 -i {pcm_path} {wav_path} -y")
            try:
                with sr.AudioFile(wav_path) as source:
                    audio = recognizer.record(source)
                text = recognizer.recognize_google(audio).upper()
                callsign = user.display_name[:5]

                if "10-8" in text:
                    unit_status[callsign] = "10-8"
                    await speak(f"{callsign} is now 10-8, available.")
                elif "10-7" in text:
                    unit_status[callsign] = "10-7"
                    await speak(f"{callsign} is now 10-7, out of service.")
                elif "10-6" in text:
                    unit_status[callsign] = "10-6"
                    await speak(f"{callsign} is now 10-6, busy.")
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

            os.remove(pcm_path)
            os.remove(wav_path)

    def on_stop(self):
        print("[SINK] Stopped listening.")

# === BOT EVENTS ===
@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")

# === COMMANDS ===
@bot.command(name="start")
async def start_dispatcher(ctx):
    global voice_client_ref, is_listening
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("❌ Invalid RTO channel ID.")
        return
    if is_listening:
        await ctx.send("📻 Dispatcher already running.")
        return

    voice_client_ref = await channel.connect()
    sink = DispatchSink(loop=bot.loop)
    voice_client_ref.listen(sink)
    is_listening = True
    bot_callsign = "2 David double O"
    await speak(f"{bot_callsign} ASRPDispatch 10-8")
    await ctx.send(f"✅ Dispatcher is online as {bot_callsign}!")

@bot.command(name="stop")
async def stop_dispatcher(ctx):
    global voice_client_ref, is_listening
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
        voice_client_ref = None
        is_listening = False
        await ctx.send("✅ Dispatcher has been shut down.")
    else:
        await ctx.send("❌ Dispatcher is not running.")

# === START ===
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN not set. Exiting.")
    else:
        keep_alive()
        bot.run(TOKEN)