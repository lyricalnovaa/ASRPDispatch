import os 
import asyncio 
import discord
from discord.ext import commands 
import speech_recognition as sr 
import edge_tts
from dotenv import load_dotenv 
from keep_alive import keep_alive 
from pydub import AudioSegment
from discord.ext.voice_recv import VoiceRecvClient, SpeakingState
# === CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,
    "BOT_CALLSIGN": "2 David Double 0",
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

# === TTS ===
async def speak(text: str):
    """Generates and plays TTS audio in the voice channel."""
    global voice_client_ref
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not in VC")
        return

    try:
        tts = edge_tts.Communicate(text, CONFIG["VOICE"])
        await tts.save(CONFIG["OUTPUT_TTS_FILE"])

        if voice_client_ref.is_playing():
            voice_client_ref.stop()

        voice_client_ref.play(discord.FFmpegPCMAudio(CONFIG["OUTPUT_TTS_FILE"]))
        
        while voice_client_ref.is_playing():
            await asyncio.sleep(0.1)

    except Exception as e:
        print(f"[ERROR] TTS failed: {e}")
    finally:
        try:
            os.remove(CONFIG["OUTPUT_TTS_FILE"])
        except OSError as e:
            print(f"[DEBUG] Error deleting TTS file: {e}")

# === NEW: VOICE RECEIVE HANDLERS ===
def on_speaking(user, state):
    """Handles the speaking status of a user."""
    if user is None:
        return
    if state == SpeakingState.SPEAKING:
        print(f"[DEBUG] {user.display_name} started speaking.")
    else:
        print(f"[DEBUG] {user.display_name} stopped speaking.")

def on_voice_packet(user, packet):
    """Processes a raw voice packet from a user."""
    if user and packet.pcm:
        # We need to process the audio in a separate task to avoid blocking the event loop.
        # `pydub` is used to convert the raw PCM data to a format `SpeechRecognition` can use.
        asyncio.create_task(process_audio(user, packet.pcm))

async def process_audio(user, audio_data):
    """Converts audio data to text using Google Speech Recognition."""
    try:
        # Create an AudioSegment from the raw PCM data
        audio_segment = AudioSegment(
            audio_data, 
            sample_width=2, 
            frame_rate=48000, 
            channels=2
        )
        
        # Convert the AudioSegment to a WAV-like byte stream
        wav_stream = audio_segment.export(format="wav").read()
        
        # Use SpeechRecognition to recognize the audio
        audio = sr.AudioData(wav_stream, 48000, 2)
        text = recognizer.recognize_google(audio).upper()
        print(f"[HEARD] {user.display_name}: {text}")

        # === BOT RESPONSES ===
        if "HI" in text:
            await speak(f"Hello {user.display_name}!")
        if "10 8" in text or "TEN EIGHT" in text:
            await speak(f"{user.display_name} is now 10-8")
        
        if "10 11" in text or "ten eleven" in text:
            await speak(f"10 4, proceed with caution")
    except sr.UnknownValueError:
        print(f"[DEBUG] Could not understand {user.display_name}")
    except sr.RequestError as e:
        print(f"[DEBUG] Speech Recognition failed: {e}")
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred: {e}")

# === COMMANDS ===
@bot.command()
async def start(ctx):
    """Connects the bot to the RTO voice channel and starts listening."""
    global voice_client_ref
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])
    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("Invalid VC ID specified in config.")
        return

    if voice_client_ref and voice_client_ref.is_connected():
        await ctx.send("I'm already online!")
        return

    # Connect using the VoiceClient from the new library
    voice_client_ref = await VoiceClient.connect(channel)
    voice_client_ref.on_speaking = on_speaking
    voice_client_ref.on_voice_packet = on_voice_packet

    await asyncio.sleep(0.5)
    await speak(f"{CONFIG['BOT_CALLSIGN']} 10 8, Active dispatch!")
    await ctx.send(f"Dispatcher online as {CONFIG['BOT_CALLSIGN']}!")

@bot.command()
async def stop(ctx):
    """Stops the dispatcher and disconnects from the voice channel."""
    global voice_client_ref
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
        voice_client_ref = None
    await ctx.send("Dispatcher stopped.")

# === RUN BOT ===
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)

